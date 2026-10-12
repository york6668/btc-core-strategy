"""Freeze raw bytes before assessment; supplements always produce a new version."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
import ipaddress
import json
import re
import tempfile
from datetime import timedelta

from .common import create, digest, exact, fsync_dir, instant, now, numbered, pointer, read, require, text


def _fields(value, fields, name, optional=()):
    require(isinstance(value, dict), name + ' must be an object')
    missing = set(fields.split()) - value.keys()
    extra = value.keys() - set(fields.split()) - set(optional)
    require(not missing and not extra, name + ': invalid fields; missing=' +
            ','.join(sorted(missing)) + '; unexpected=' + ','.join(sorted(extra)))


def _source_fields(sources):
    require(isinstance(sources, list) and sources, 'raw sources required')
    for index, source in enumerate(sources):
        _fields(source, 'id origin url trust published_at retrieved_at start end raw_hash',
                'source[' + str(index) + ']')
        text(source['id'], 'source id')
        require(isinstance(source['raw_hash'], str) and
                re.fullmatch(r'sha256:[a-f0-9]{64}', source['raw_hash']), 'raw hash invalid')


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
    _fields(bundle, 'schema evidence_id parent_id cutoff sources measurements verified_facts candidates gaps handoff',
            'EvidenceBundle', ('report_products', 'collector'))
    require(bundle['schema'] == 'BTC_EVIDENCE_V4', 'wrong evidence schema')
    require(bundle['evidence_id'] == digest({k: v for k, v in bundle.items() if k != 'evidence_id'}), 'evidence identity mismatch')
    cutoff = instant(bundle['cutoff'])
    require(cutoff <= instant(now()) + timedelta(seconds=5), 'assessment cutoff is in the future')
    _source_fields(bundle['sources'])
    ids = set()
    for source in bundle['sources']:
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
    from . import public_observations
    if ref['pointer'].startswith(public_observations.PREFIX):
        require(digest((Path(directory) / 'raw' / (source['raw_hash'][7:] + '.json')).read_bytes()) == source['raw_hash'],
                'missing or changed original observation source')
        record_id, separator, suffix = ref['pointer'][len(public_observations.PREFIX):].partition('/')
        exact_data = json.loads((Path(directory) / 'raw' / (source['raw_hash'][7:] + '.json')).read_bytes(), parse_float=str)
        record = public_observations.projection(source, exact_data, record_id)
        value = pointer(record, '/' + suffix) if separator else record
    else:
        value = pointer(data, ref['pointer'])
    require(digest(value) == ref['value_hash'], 'raw value reference mismatch')
    return value



def observation_refs(bundle, directory, source_id):
    """List recomputable source projections; callers can inspect them with resolve.

    These are derived RawRefs, never additional public sources or conclusions.
    Exact field anchors may append /value and bind that field's own value_hash.
    """
    from . import public_observations
    source = next((s for s in bundle['sources'] if s['id'] == source_id), None)
    require(source is not None and source['trust'] == 'PUBLIC_RAW', 'classified observations need a frozen public source')
    path = Path(directory) / 'raw' / (source['raw_hash'][7:] + '.json')
    require(path.is_file() and digest(path.read_bytes()) == source['raw_hash'], 'missing or changed raw evidence')
    values = public_observations.records(source, json.loads(path.read_bytes(), parse_float=str))
    return [{'source_id': source_id, 'pointer': public_observations.PREFIX + v['id'], 'value_hash': digest(v)} for v in values]


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
    _fields(spec, 'parent_id cutoff sources measurements verified_facts candidates gaps handoff',
            'evidence specification', ('schema', 'evidence_id', 'report_products', 'collector'))
    _source_fields(spec['sources'])
    require(isinstance(raw_files, dict), 'raw files must be a source id to path object')
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
        require(source['id'] in raw_files, 'raw file missing for source: ' + source['id'])
        require(isinstance(raw_files[source['id']], (str, Path)),
                'raw file path invalid for source: ' + source['id'])
    bundle = dict(spec, schema='BTC_EVIDENCE_V4')
    bundle.pop('evidence_id', None)
    bundle['evidence_id'] = digest(bundle)
    directory.parent.mkdir(parents=True, exist_ok=True)
    # Invalid claims or interrupted writes must not publish a partial bundle
    # that prevents retrying this same evidence preparation.
    with tempfile.TemporaryDirectory(prefix='.' + directory.name + '.freeze-', dir=directory.parent) as temporary:
        stage = Path(temporary)
        for source in spec['sources']:
            payload = Path(raw_files[source['id']]).read_bytes()
            require(digest(payload) == source['raw_hash'], 'source bytes mismatch')
            target = stage / 'raw' / (source['raw_hash'][7:] + '.json')
            if not target.exists():
                create(target, payload)
        validate(bundle, stage)
        create(stage / 'evidence.json', bundle)
        require(not directory.exists(), 'evidence directory already exists')
        stage.rename(directory)
        fsync_dir(directory.parent)
    return bundle


def retained_owners(folders, cutoff, bindings=None):
    """An explicit choice may disambiguate legacy retrievals, never changed data."""
    import market_observation as market
    bindings = {} if bindings is None else bindings
    require(isinstance(bindings, dict), 'retained source bindings must be an object')
    variants = {}
    for folder in folders:
        package = validate(read(Path(folder) / 'evidence.json'), folder)
        require(instant(package['cutoff']) <= instant(cutoff), 'retained evidence exceeds cutoff')
        for source in package['sources']:
            variants.setdefault(source['id'], {}).setdefault(digest(source), []).append((source, package, folder))
    owners, aliases, used = {}, set(), set()
    for sid, records in variants.items():
        if len(records) == 1:
            owners[sid] = next(iter(records.values()))[0]
            continue
        candidates = [{'evidence_id': p['evidence_id'], 'source_hash': digest(s),
                       'raw_hash': s['raw_hash'], 'retrieved_at': s['retrieved_at']}
                      for entries in records.values() for s, p, _ in entries]
        require(sid in bindings, 'retained evidence source collision: ' + json.dumps(
            {'source_id': sid, 'candidates': candidates,
             'next_action': 'REVIEW_RETAINED_SOURCE_BINDINGS_FOR_NEW_PREPARATION'}, sort_keys=True))
        binding = bindings[sid]
        exact(binding, 'evidence_id source_hash', 'retained source binding')
        chosen = next((entry for entry in records.get(binding['source_hash'], [])
                       if entry[1]['evidence_id'] == binding['evidence_id']), None)
        require(chosen is not None,
                'retained source binding does not match original evidence')
        original, _, old_folder = chosen
        require(sid.startswith('market_'), 'retained source collision is not a market retrieval')
        old_raw = read(Path(old_folder) / 'raw' / (original['raw_hash'][7:] + '.json'))
        market.validate_evidence(old_raw)
        for entries in records.values():
            source, package, folder = entries[0]
            raw = read(Path(folder) / 'raw' / (source['raw_hash'][7:] + '.json'))
            market.validate_evidence(raw)
            require({k:v for k,v in old_raw.items() if k not in ('retrieval_id','retrievals')} ==
                    {k:v for k,v in raw.items() if k not in ('retrieval_id','retrievals')} and
                    {k:v for k,v in original.items() if k not in ('raw_hash','retrieved_at')} ==
                    {k:v for k,v in source.items() if k not in ('raw_hash','retrieved_at')},
                    'retained source binding cannot reconcile changed market data or origin')
            if source == original:
                continue
            suffix = (source['raw_hash'][7:23] if source['raw_hash'] != original['raw_hash']
                      else digest(source)[7:23])
            alias = sid + '_' + suffix
            require(alias not in variants and alias not in owners, 'retained source alias collision')
            owners[alias] = (dict(source, id=alias), package, folder)
            aliases.add(alias)
        owners[sid] = chosen
        used.add(sid)
    require(set(bindings) == used, 'retained source binding has no retained collision')
    return owners, aliases


def compact_root(base_directory, raw_market_file, refs, directory, cutoff, additional=None, retained_directories=(), collector_file=None, retained_source_bindings=None):
    """New independent study: copy only referenced historical sources and current raw.

    This deliberately has no parent. Same-study supplementation still uses freeze
    with --parent and cannot omit or rewrite already known sources. Optional
    additional frozen evidence supplies model-selected public sources at their
    own frequencies; it does not import another study's claims or conclusions.
    """
    import market_observation as market
    base = validate(read(Path(base_directory) / 'evidence.json'), base_directory)
    require(isinstance(refs, list), 'historical reference closure must be a list')
    owners, retained = retained_owners((base_directory, *retained_directories), cutoff, retained_source_bindings)
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
    if raw.get('normalization'):
        # A replay is an existing derived source, never a fabricated Binance
        # PUBLIC_RAW retrieval. Restore its exact immutable descriptor and every
        # original input from the retained/additional source owners.
        available = {sid: (source, package, folder) for sid, (source, package, folder) in owners.items()}
        if additional is not None:
            available.update({s['id']: (s, extra, additional) for s in extra['sources']})
        matched = []
        for sid, (source, _, folder) in available.items():
            path = Path(folder) / 'raw' / (source['raw_hash'][7:] + '.json')
            if source['trust'] == 'UNTRUSTED_LEAD' and read(path) == raw:
                matched.append(sid)
        require(matched, 'locked replay needs its exact retained UNTRUSTED_LEAD wrapper owner')
        needed = set(matched) | {value['source_id'] for value in raw['normalization']['inputs'].values()}
        by_id = {s['id']: s for s in sources}
        for sid in sorted(needed):
            require(sid in available, 'current original source is absent from retained evidence')
            source, _, folder = available[sid]
            require(sid not in by_id or by_id[sid] == source, 'current retained source identity collision')
            if sid not in by_id:
                sources.append(source); by_id[sid] = source
            files[sid] = str(Path(folder) / 'raw' / (source['raw_hash'][7:] + '.json'))
        from .main_flow import current_market_source_ownership
        current_market_source_ownership(by_id, {sid: read(files[sid]) for sid in by_id}, raw)
    else:
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


def forward_resolver(bundle, directory):
    """Public fact adapter; registration/lineage policy stays domain-neutral.

    Event time comes from the referenced observation itself or the start of
    its source period, never retrieval/publication time. Opaque material may
    inform independent research, but cannot automatically grant a later fact.
    """
    from datetime import datetime, timezone
    from urllib.parse import parse_qs, unquote

    def resolve_ref(ref): return resolve(bundle, directory, ref, verified=True)

    def observation(scope,start,end,source):
        # Economic keys omit raw/ref aliases and retrieval clocks. The original
        # publisher/provenance remains attached separately to the resolved fact.
        period={'start':instant(start).astimezone(timezone.utc).isoformat(),
                'end':instant(end).astimezone(timezone.utc).isoformat()}
        require(instant(period['start'])<=instant(period['end']),'invalid original observation period')
        key={'scope':{k:v for k,v in scope.items() if k!='publisher'},'economic_period':period}
        return dict(key,identity=digest(key),publisher=canonical_origin(source['url']))

    def view(ref, time_pointer):
        value = resolve_ref(ref)
        source = next(s for s in bundle['sources'] if s['id'] == ref['source_id'])
        from . import public_observations
        if ref['pointer'].startswith(public_observations.PREFIX):
            require(isinstance(value, dict) and 'economic_period' in value,
                    'later fact sample must reference its whole source observation')
            require(time_pointer in (None, '/observed_at'), 'derived fact cannot substitute another event clock')
            scopes = {'/value': value['scope']}
            numeric = [] if value['scope']['unit'] == 'SOURCE_STATEMENT' else ['/value']
            field_errors = {}
            if numeric:
                from decimal import Decimal, InvalidOperation
                try: number = Decimal(str(value['value']))
                except (InvalidOperation, TypeError, ValueError): number = None
                if number is None or not number.is_finite(): field_errors['/value'] = 'classified source value is unavailable'
            return {'kind': value['kind'], 'value': value, 'scope': value['scope'],
                    'field_scopes': scopes, 'field_times': {'/value': value['observed_at']},
                    'field_period_starts': {'/value': value['observed_at'] if value['scope'].get('period_kind') == 'DAILY_PRICE_BAR'
                                             else value['economic_period']['start']},
                    'numeric_fields': numeric, 'field_errors': field_errors,
                    # Exact dates remain source-bound. A process claim may leave
                    # its next date open; an explicitly named date still matches
                    # through the ordinary scope comparison in the validator.
                    'qualifiers': {'/value': [k for k in value['scope'] if k not in ('period', 'as_of')]},
                    'observed_at': value['observed_at'], 'known_at': value['known_at'],
                    'available_at': value['available_at'], 'provenance': value['provenance'],
                    'field_observations':{'/value':observation(value['scope'],value['economic_period']['start'],value['economic_period']['end'],source)}}
        raw = read(Path(directory) / 'raw' / (source['raw_hash'][7:] + '.json'))
        endpoint = urlsplit(source['url']); query = parse_qs(endpoint.query)
        base = {'publisher': source['origin']}; fields = {}; field_times = {}
        candidate = 'MATERIAL'
        symbol = query.get('symbol', [None])[0]
        if symbol and canonical_origin(source['url']) == 'BINANCE':
            subject = symbol
            for quote in ('USDT', 'USDC', 'BUSD', 'USD'):
                if subject.endswith(quote): subject = subject[:-len(quote)]; break
            base.update(subject=subject, venue='BINANCE', quote_unit=next((q for q in ('USDT','USDC','BUSD','USD') if symbol.endswith(q)), 'UNESTABLISHED'),
                        product=('FUTURES' if '_' in symbol else 'PERPETUAL') if (endpoint.hostname == 'fapi.binance.com' or endpoint.path.startswith(('/fapi/', '/futures/'))) else 'SPOT')
            if isinstance(value, list) and len(value) >= 11 and 'klines' in endpoint.path:
                # The actual request interval is part of the observation's
                # scope. An hourly close cannot verify an ex-ante daily test.
                if query.get('interval'):
                    base['interval'] = query['interval'][0]
                candidate = 'EXTERNAL_OBSERVATION'
                for i in (1, 2, 3, 4): fields['/' + str(i)] = dict(base, measure='PRICE')
                fields['/5'] = dict(base, measure='TRADED_VOLUME')
                fields['/7'] = dict(base, measure='TRADED_TURNOVER')
                fields['/9'] = dict(base, measure='TAKER_VOLUME')
                fields['/10'] = dict(base, measure='TAKER_TURNOVER')
            elif isinstance(value, dict):
                for k in ('sumOpenInterest', 'openInterest', 'sumOpenInterestValue', 'fundingRate'):
                    if k in value:
                        candidate = 'EXTERNAL_OBSERVATION'
                        fields['/' + k] = dict(base, measure='FUNDING_RATE' if k == 'fundingRate' else 'OPEN_INTEREST_NOTIONAL' if k == 'sumOpenInterestValue' else 'OPEN_INTEREST_QUANTITY')
        if isinstance(raw, dict) and isinstance(raw.get('datasets'), dict):
            segments = ref['pointer'].split('/')
            if len(segments) >= 3 and segments[1] == 'datasets' and segments[2] in raw['datasets']:
                ds = raw['datasets'][segments[2]]
                group = ds.get('group', '')
                if group in ('BINANCE', 'COINBASE', 'KRAKEN') and ds.get('instrument') and ds.get('quote_unit'):
                    instrument = ds['instrument']; quote = ds['quote_unit']
                    subject = instrument[:-len(quote)].rstrip('-') if instrument.endswith(quote) else 'UNESTABLISHED'
                    if subject == 'XBT': subject = 'BTC'  # Kraken's actual Bitcoin instrument alias.
                    base = {'subject':subject, 'venue':group, 'product':ds['market'], 'quote_unit':quote}
                    if isinstance(value, dict):
                        measures = {'open':'PRICE','high':'PRICE','low':'PRICE','close':'PRICE',
                                    'volume_btc':'TRADED_VOLUME','quote_volume':'TRADED_TURNOVER',
                                    'taker_buy_btc':'TAKER_VOLUME','taker_buy_quote':'TAKER_TURNOVER',
                                    'quantity_btc':'OPEN_INTEREST_QUANTITY','notional_usdt':'OPEN_INTEREST_NOTIONAL','rate':'FUNDING_RATE'}
                        for k, measure in measures.items():
                            if k in value and value[k] is not None:
                                fields['/'+k] = dict(base,measure=measure)
                        if fields: candidate = 'EXTERNAL_OBSERVATION'
        if isinstance(value, dict) and value.get('kind') in ('FORECAST', 'HYPOTHESIS', 'SIMULATION', 'ENGINEERING', 'BACKTEST', 'FROZEN_REPLAY'):
            candidate = value['kind']
        if time_pointer is None:
            stamp = instant(source['start'])
        else:
            require(isinstance(time_pointer, str), 'fact time must reference the same observation')
            allowed = {'/time', '/date', '/observed_at', '/timestamp', '/close_time', '/fundingTime'}
            if isinstance(value, list): allowed = {'/0', '/6'}
            require(time_pointer in allowed, 'retrieval/publication or unrelated clock cannot replace event time')
            t = pointer(value, time_pointer)
            if isinstance(t, (int, float)):
                stamp = datetime.fromtimestamp(t / 1000 if t > 100000000000 else t, timezone.utc)
            else:
                if isinstance(t, str) and len(t) == 10: t += 'T00:00:00Z'
                stamp = instant(t)
        require(instant(source['start']) <= stamp <= instant(source['end']), 'fact event time exceeds its frozen source period')
        def field_clock(t):
            if isinstance(t, (int, float)):
                return datetime.fromtimestamp(t / 1000 if t > 100000000000 else t, timezone.utc).isoformat()
            if isinstance(t, str) and len(t) == 10: t += 'T00:00:00Z'
            return instant(t).isoformat()
        if isinstance(value, list) and fields:
            field_times = {p: field_clock(value[0 if p == '/1' else 6]) for p in fields}
        elif isinstance(value, dict) and fields:
            # A candle's close/range/accumulated trades form at its end; an
            # open is already fixed at the start. Point facts use their own
            # observation clock, never an author's alternative sample clock.
            start_clock = value.get('time', value.get('timestamp', value.get('fundingTime', value.get('observed_at', value.get('date')))))
            end_clock = value.get('end', value.get('close_time', start_clock))
            if start_clock is not None:
                field_times = {p: field_clock(start_clock if p == '/open' else end_clock) for p in fields}
        for clock in field_times.values():
            require(instant(source['start']) <= instant(clock) <= instant(source['end']), 'field time exceeds its frozen source period')
        field_observations={}
        if fields:
            if isinstance(value,list):start_clock,end_clock=value[0],value[6]
            else:
                start_clock=value.get('time',value.get('timestamp',value.get('fundingTime',value.get('observed_at',value.get('date')))))
                end_clock=value.get('end',value.get('close_time',start_clock))
            if start_clock is not None and end_clock is not None:
                field_observations={field:observation(actual,field_clock(start_clock),field_clock(end_clock),source)
                                    for field,actual in fields.items()}
        # Close/open and point measures have their own event clock. Range
        # extrema and accumulated trades include the candle's whole period;
        # its later close cannot make formation-era events prospective.
        field_period_starts = {}
        aggregate_fields = ('/2', '/3', '/5', '/7', '/9', '/10', '/high', '/low',
                            '/volume_btc', '/quote_volume', '/taker_buy_btc', '/taker_buy_quote')
        for field in aggregate_fields:
            if field in field_observations:
                field_period_starts[field] = field_observations[field]['economic_period']['start']
        field_errors = {}
        from decimal import Decimal, InvalidOperation
        for field in fields:
            try: number = Decimal(str(pointer(value, field)))
            except (InvalidOperation, ValueError, TypeError):
                field_errors[field] = 'classified numeric observation is unavailable, not a changed price or quantity'
            else:
                if not number.is_finite(): field_errors[field] = 'nonfinite numeric observation cannot verify a mechanism'
        if fields:
            base = dict(base, measure=sorted({f['measure'] for f in fields.values()}))
        return {'kind': candidate, 'value': value, 'scope': base, 'field_scopes': fields, 'field_times': field_times, 'field_period_starts': field_period_starts, 'numeric_fields':list(fields), 'field_errors':field_errors,
                'qualifiers': {p: [k for k in s if k != 'publisher'] for p, s in fields.items()},
                'observed_at': stamp.isoformat(), 'known_at': source['retrieved_at'],
                'field_observations':field_observations}

    resolve_ref.fact = view
    return resolve_ref
