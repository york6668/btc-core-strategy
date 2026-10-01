"""Freeze raw bytes before assessment; supplements always produce a new version."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
import ipaddress
import json
import re
from datetime import timedelta

from .common import create, digest, exact, instant, now, numbered, pointer, read, require, text


def canonical_origin(url):
    host = urlsplit(url).hostname or ''
    registry = {'binance.com': 'BINANCE', 'binance.vision': 'BINANCE', 'coinbase.com': 'COINBASE',
                'kraken.com': 'KRAKEN', 'bls.gov': 'BLS', 'federalreserve.gov': 'FED', 'treasury.gov': 'TREASURY',
                'eia.gov': 'EIA', 'ecb.europa.eu': 'ECB', 'boj.or.jp': 'BOJ', 'pbc.gov.cn': 'PBOC',
                'bis.org': 'BIS', 'bea.gov': 'BEA', 'cftc.gov': 'CFTC', 'imf.org': 'IMF',
                'bankofengland.co.uk': 'BOE', 'stats.gov.cn': 'NBS_CN'}
    for suffix, identity in registry.items():
        if host == suffix or host.endswith('.' + suffix):
            return identity
    # Unknown providers retain the full host; independence is explicitly unestablished.
    return 'UNESTABLISHED:' + host


def validate(bundle, directory):
    exact(bundle, 'schema evidence_id parent_id cutoff sources measurements verified_facts candidates gaps handoff'+(' report_products' if 'report_products' in bundle else '')+(' collector' if 'collector' in bundle else ''), 'EvidenceBundle')
    require(bundle['schema'] == 'BTC_EVIDENCE_V4', 'wrong evidence schema')
    require(bundle['evidence_id'] == digest({k: v for k, v in bundle.items() if k != 'evidence_id'}), 'evidence identity mismatch')
    cutoff = instant(bundle['cutoff'])
    require(cutoff <= instant(now()) + timedelta(seconds=5), 'assessment cutoff is in the future')
    require(isinstance(bundle['sources'], list) and bundle['sources'], 'raw sources required')
    ids = set()
    for source in bundle['sources']:
        exact(source, 'id origin url trust published_at retrieved_at start end raw_hash', 'source')
        text(source['id'], 'source id')
        require(source['id'] not in ids, 'duplicate source id')
        ids.add(source['id'])
        text(source['origin'], 'canonical origin')
        url = urlsplit(source['url'])
        require(url.scheme == 'https' and url.hostname and not url.username and not url.password, 'public HTTPS provenance required')
        # Registering a new official publisher cannot invalidate old frozen
        # sources or retrospectively upgrade their independence. FRED and other
        # redistributors remain UNESTABLISHED unless original provenance is known.
        historical_origin = 'UNESTABLISHED:' + (url.hostname or '')
        require(source['origin'] in (canonical_origin(source['url']), historical_origin), 'source cannot self-declare independence')
        require(not url.query or not any(x in url.query.lower() for x in ('token', 'key', 'auth', 'secret')), 'credential-like URL')
        try:
            ipaddress.ip_address(url.hostname)
        except ValueError:
            require('.' in url.hostname and not url.hostname.endswith(('.local', '.localhost')), 'nonpublic source')
        else:
            require(False, 'IP literal source forbidden')
        require(source['trust'] in ('PUBLIC_RAW', 'UNTRUSTED_LEAD'), 'invalid source trust')
        start, end, retrieved = [instant(source[k]) for k in ('start', 'end', 'retrieved_at')]
        published = instant(source['published_at']) if source['published_at'] is not None else None
        require(start <= end <= retrieved <= cutoff and (published is None or published <= retrieved),
                'source exceeds assessment time boundary')
        raw_hash = source['raw_hash']
        require(isinstance(raw_hash, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', raw_hash), 'raw hash invalid')
        payload = Path(directory) / 'raw' / (raw_hash[7:] + '.json')
        require(payload.is_file() and digest(payload.read_bytes()) == raw_hash, 'missing or changed raw evidence')
    for group in ('measurements', 'verified_facts'):
        require(isinstance(bundle[group], list), group + ' must be a list')
        for claim in bundle[group]:
            exact(claim, 'id statement refs', group)
            text(claim['statement'], 'measurement/fact statement')
            require(isinstance(claim['refs'], list) and claim['refs'], 'fact needs raw references')
            for ref in claim['refs']:
                resolve(bundle, directory, ref, verified=True)
    require(isinstance(bundle['candidates'], list) and isinstance(bundle['gaps'], list), 'candidates and gaps must remain separate')
    exact(bundle['handoff'], 'previous_hour successful_deep changes_since_hour changes_since_deep active_hypotheses open_questions', 'handoff')
    for field in ('changes_since_hour', 'changes_since_deep', 'active_hypotheses', 'open_questions'):
        require(isinstance(bundle['handoff'][field], list), 'handoff list required')
    if 'report_products' in bundle:
        from .equity_products import validate as validate_products
        validate_products(bundle['report_products'], bundle, directory)
    if 'collector' in bundle:
        exact(bundle['collector'], 'source_id snapshot_id', 'collector binding')
        require(bundle['collector']['source_id'] in ids, 'collector source is missing')
        from .collector import context as collector_context
        collector_context(bundle, directory)
    return bundle


def resolve(bundle, directory, ref, verified=False):
    exact(ref, 'source_id pointer value_hash', 'raw reference')
    sources = {s['id']: s for s in bundle['sources']}
    require(ref['source_id'] in sources, 'reference source absent')
    source = sources[ref['source_id']]
    if verified:
        require(source['trust'] == 'PUBLIC_RAW', 'untrusted lead cannot support verified fact')
    data = read(Path(directory) / 'raw' / (source['raw_hash'][7:] + '.json'))
    value = pointer(data, ref['pointer'])
    require(digest(value) == ref['value_hash'], 'raw value reference mismatch')
    return value


def resolve_number(bundle, directory, ref, verified=False):
    """Compare public JSON numbers using their frozen decimal token, not float rounding."""
    value = resolve(bundle, directory, ref, verified=verified)
    if not isinstance(value, float):
        return numbered(value)
    source = next(s for s in bundle['sources'] if s['id'] == ref['source_id'])
    payload = (Path(directory)/'raw'/(source['raw_hash'][7:]+'.json')).read_bytes()
    require(digest(payload) == source['raw_hash'], 'missing or changed raw evidence')
    exact_data = json.loads(payload, parse_float=str)
    return numbered(pointer(exact_data, ref['pointer']))


def origins(bundle, refs):
    sources = {s['id']: s for s in bundle['sources']}
    return {canonical_origin(sources[r['source_id']]['url']) for r in refs
            if not sources[r['source_id']]['origin'].startswith('UNESTABLISHED:')
            and not canonical_origin(sources[r['source_id']]['url']).startswith('UNESTABLISHED:')}


def freeze(spec, raw_files, directory, parent=None):
    directory = Path(directory)
    require(not directory.exists(), 'evidence directory already exists')
    if parent:
        old = validate(read(Path(parent) / 'evidence.json'), parent)
        require(spec['parent_id'] == old['evidence_id'], 'supplement parent mismatch')
        require(instant(spec['cutoff']) >= instant(old['cutoff']), 'supplement cannot backdate cutoff')
        by_id = {s['id']: s for s in spec['sources']}
        require(all(by_id.get(s['id']) == s for s in old['sources']), 'supplement must retain original sources unchanged')
        if 'collector' in old and 'collector' not in spec:
            spec = dict(spec, collector=old['collector'])
    else:
        require(spec['parent_id'] is None, 'missing parent evidence')
    for source in spec['sources']:
        payload = Path(raw_files[source['id']]).read_bytes()
        require(digest(payload) == source['raw_hash'], 'source bytes mismatch')
        target = directory / 'raw' / (source['raw_hash'][7:] + '.json')
        if not target.exists():
            create(target, payload)
    bundle = dict(spec, schema='BTC_EVIDENCE_V4')
    bundle.pop('evidence_id', None)
    bundle['evidence_id'] = digest(bundle)
    validate(bundle, directory)
    create(directory / 'evidence.json', bundle)
    return bundle


def compact_root(base_directory, raw_market_file, refs, directory, cutoff, additional=None, retained_directories=(), collector_file=None):
    """New independent study: copy only referenced historical sources and current raw.

    This deliberately has no parent. Same-study supplementation still uses freeze
    with --parent and cannot omit or rewrite already known sources. Optional
    additional frozen evidence supplies model-selected public sources at their
    own frequencies; it does not import another study's claims or conclusions.
    """
    import market_observation as market
    base = validate(read(Path(base_directory) / 'evidence.json'), base_directory)
    require(isinstance(refs, list), 'historical reference closure must be a list')
    owners = {}
    for folder in (base_directory, *retained_directories):
        package = validate(read(Path(folder) / 'evidence.json'), folder)
        require(instant(package['cutoff']) <= instant(cutoff), 'retained evidence exceeds cutoff')
        for source in package['sources']:
            old = owners.get(source['id'])
            require(old is None or old[0] == source, 'retained evidence source collision')
            owners[source['id']] = (source, package, folder)
    retained = set()
    if collector_file is None and base.get('collector'):
        retained.add(base['collector']['source_id'])
    for ref in refs:
        require(ref['source_id'] in owners, 'historical reference source missing')
        source, package, folder = owners[ref['source_id']]
        resolve(package, folder, ref)
        retained.add(ref['source_id'])
    sources = [owners[sid][0] for sid in sorted(retained)]
    files = {sid: str(Path(owners[sid][2]) / 'raw' / (owners[sid][0]['raw_hash'][7:]+'.json')) for sid in retained}
    if additional is not None:
        extra = validate(read(Path(additional) / 'evidence.json'), additional)
        require(instant(extra['cutoff']) <= instant(cutoff), 'additional evidence exceeds new cutoff')
        original = {sid: record[0] for sid, record in owners.items()}
        by_id = {s['id']: s for s in sources}
        for source in extra['sources']:
            sid = source['id']
            if sid in original and original[sid] != source and sid.startswith('market_'):
                old_source, _, old_folder = owners[sid]
                old_raw = read(Path(old_folder) / 'raw' / (old_source['raw_hash'][7:] + '.json'))
                new_raw = read(Path(additional) / 'raw' / (source['raw_hash'][7:] + '.json'))
                market.validate_evidence(old_raw)
                market.validate_evidence(new_raw)
                # Same closed market data can have two genuine retrievals in
                # concurrent runs. Preserve both bytes and clocks under distinct
                # identities; never reinterpret changed data as a mere refresh.
                ignored = {'retrieval_id', 'retrievals'}
                require({k:v for k,v in old_raw.items() if k not in ignored} ==
                        {k:v for k,v in new_raw.items() if k not in ignored}, 'additional evidence source collision')
                require({k:v for k,v in old_source.items() if k not in ('raw_hash','retrieved_at')} ==
                        {k:v for k,v in source.items() if k not in ('raw_hash','retrieved_at')}, 'additional evidence source collision')
                source = dict(source, id=sid + '_' + source['raw_hash'][7:23])
                sid = source['id']
            # Even an unretained historical id cannot be reused for different
            # bytes/metadata: its raw references would become ambiguous.
            require(sid not in original or original[sid] == source, 'additional evidence source collision')
            require(sid not in by_id or by_id[sid] == source, 'additional evidence source collision')
            if sid not in by_id:
                sources.append(source)
                by_id[sid] = source
                files[sid] = str(Path(additional) / 'raw' / (source['raw_hash'][7:] + '.json'))
    raw = read(raw_market_file)
    market.validate_evidence(raw)
    retrievals = [r['retrieved_at'] for r in raw['retrievals'].values()]
    start = min((d['requested_start'] for d in raw['datasets'].values()), key=instant)
    sid = 'market_' + raw['evidence_id'].replace('sha256:', '')[:20]
    existing = next((s for s in sources if s['id'] == sid), None)
    source = {'id':sid, 'origin':'BINANCE','url':'https://data-api.binance.vision/api/v3/klines',
              'trust':'PUBLIC_RAW','published_at':raw['cutoff'],'retrieved_at':max(retrievals,key=instant),
              'start':start,'end':raw['cutoff'],'raw_hash':digest(Path(raw_market_file).read_bytes())}
    if existing is not None and existing != source:
        # Market identity excludes retrieval metadata. Keep the previously cited
        # source immutable and give a fresh retrieval its own byte-bound identity.
        sid += '_' + source['raw_hash'][7:23]
        source['id'] = sid
        existing = next((s for s in sources if s['id'] == sid), None)
    if existing:
        require(existing == source, 'current evidence source collision')
    else:
        sources.append(source)
    files[sid] = str(raw_market_file)
    spec = {'parent_id':None,'cutoff':cutoff,'sources':sources,'measurements':[],'verified_facts':[],
            'candidates':[],'gaps':[], 'handoff':{'previous_hour':None,'successful_deep':None,
            'changes_since_hour':[],'changes_since_deep':[],'active_hypotheses':[],'open_questions':[]}}
    from .equity_products import retained
    products = retained([base, *[owner[1] for owner in owners.values()], *([extra] if additional else [])], {s['id'] for s in sources})
    if products:
        spec['report_products'] = products
    if collector_file is not None:
        from .collector import add_source
        add_source(spec, files, collector_file)
    elif base.get('collector') and base['collector']['source_id'] in files:
        spec['collector'] = base['collector']
    return freeze(spec, files, directory)
