"""Keep collector observations auxiliary, with explicit identity and local knowledge time."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from .codec import encode, fingerprint, identity, millis, stamp
from .series import Series


SELLER = 'seller_exhaustion_daily'


def seller_rows(dataset, asset, known, reader_directory=None):
    """Validate with the deployed producer contract, not a second formula."""
    location = os.environ.get('BTC_COLLECTOR_CONTRACT_PATH')
    if reader_directory is not None:
        directory = Path(reader_directory)
    elif location:
        directory = Path(location).parent
    else:
        import node_config
        directory = node_config.path('reader_code_dir', node_config.project() / 'telegram_reader')
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location('btc_series_seller', directory / 'seller_exhaustion.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reasons = module.problems(module.from_projection(dataset), known)
    allowed = {'collecting', 'source_partial', 'source_collecting', 'source_stale', 'source_unavailable'}
    if asset != 'BTC' or set(reasons) - allowed:
        raise ValueError('invalid seller contract: ' + ','.join(reasons or ['asset']))
    sources = {kind: {'endpoint_url': module.BASE + path, 'unit': unit, 'access_scope': 'public'}
               for kind, (path, _, unit) in module.INPUTS.items()}
    ident = {'market': module.BASE.split('/')[2], 'symbol': 'BTC',
             'product_type': 'COLLECTOR_SERIES:' + SELLER, 'quote_unit': dataset['unit'],
             'source_identity': 'derived:'+identity(dataset['method'], sources),
             'derivation': {'method': dataset['method'], 'inputs': sources}}
    rows = []
    first = min((row['period_start_ms'] for row in dataset.get('rows', [])), default=0)
    for row in dataset.get('rows', []):
        # The attachment carries 30 leading days as calculation inputs. Their
        # missing preceding prices are an export boundary, not a source null
        # revision. Retain them in the original blob, never retract a value.
        if row['period_start_ms'] < first + 30 * module.DAY:
            rows.append(None)
            continue
        payload = deepcopy(row)
        for raw in payload['inputs'].values():
            raw.pop('retrieved_at_ms', None)
        rows.append((ident, module.DAY, payload))
    return rows


def ingest(db, package, source, *, only=None):
    series = Series(db)
    original = db.put_blob(Path(source).read_bytes())
    counts, gaps = [], []
    for asset, value in package.get('assets', {}).items():
        received = (value.get('transport') or {}).get('received_at')
        if received is None:
            gaps.append({'asset':asset,'reason':'first local receipt time missing'})
            continue
        known = int(received * 1000) if isinstance(received,(float,int)) else millis(received)
        for name, dataset in value.get('datasets', {}).items():
            if only is not None and name not in only:
                continue
            rows = dataset.get('rows') or []
            if not rows: continue
            if not dataset.get('instrument') or not dataset.get('unit'):
                gaps.append({'asset':asset,'series':name,'reason':'original product/unit contract missing'})
                continue
            groups = defaultdict(list)
            prepared = None
            if name == SELLER:
                try:
                    # Projection runs in the service parent, outside the pinned
                    # CLI subprocess environment. Use its owned node settings.
                    node_path = db.settings.get('container_node_config')
                    reader_directory = json.loads(Path(node_path).read_text())['reader_code_dir'] if node_path else None
                    prepared = seller_rows(dataset, asset, known, reader_directory)
                except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
                    gaps.append({'asset':asset,'series':name,'reason':str(exc)})
                    continue
            for index, row in enumerate(rows):
                observed = row.get('observed_ms')
                endpoint = (row.get('provenance') or {}).get('endpoint_url')
                if not isinstance(observed,int) or observed > known or not endpoint and prepared is None:
                    gaps.append({'asset':asset,'series':name,'reason':'row event time/source missing or later than receipt'})
                    continue
                # Product kind is deliberately source-series specific when the
                # transport provides no exchange product-class assertion.
                ident = {'market':urlsplit(endpoint or '').netloc,'symbol':dataset['instrument'],
                         'product_type':'COLLECTOR_SERIES:'+name,'quote_unit':dataset['unit'],
                         'source_identity':endpoint}
                interval = 0
                if isinstance(row.get('open_time'),int) and isinstance(row.get('close_time'),int):
                    interval = row['close_time']-row['open_time']+1
                elif isinstance(row.get('period_start_ms'),int) and isinstance(row.get('period_end_ms'),int):
                    interval = row['period_end_ms']-row['period_start_ms']
                # Retrieval clocks are preserved by the original package and
                # one retrieval record, not copied into every unchanged bar.
                payload = {k:v for k,v in row.items() if k not in {'retrieved_at_ms','last_event_retrieved_ms'}}
                if prepared is not None:
                    if prepared[index] is None:
                        continue
                    ident, interval, payload = prepared[index]
                product = 'collector:'+identity(ident)
                groups[(product,interval)].append((ident, {'item_key':str(observed),
                    'observed_at':stamp(datetime.fromtimestamp(observed/1000,timezone.utc)),
                    'known_at':stamp(datetime.fromtimestamp(known/1000,timezone.utc)),'payload':payload}))
            for (product,interval), items in groups.items():
                series.instrument(product,items[0][0])
                provenance = {'original_digest':original,'package_id':value.get('bundle_id'),
                    'retrieval':{'received_at_ms':known,'generated_at':value.get('generated_at')},
                    'trust':'UNTRUSTED_LEAD','independent_verification':False,'asset':asset,
                    'relation':dataset.get('relation','UNKNOWN'),'quality':dataset.get('quality'),
                    'analysis_eligible':False,'field_units':dataset.get('field_units',{})}
                if name == SELLER:
                    # Full original clocks, quality and the exact frozen raw_ref
                    # belong to this retrieval, not every numerical revision.
                    provenance['retrieval'].update(quality=provenance.pop('quality'),
                        status=dataset.get('status'), calculated_at_ms=dataset.get('calculated_at_ms'),
                        raw_ref={'source_id':'collector_'+original[:20],
                                 'pointer':'/assets/'+asset+'/datasets/'+name,
                                 'value_hash':'sha256:'+fingerprint(encode(dataset))})
                    provenance.update(method=dataset['method'], official_equivalence='unverified')
                for start in range(0,len(items),10000):
                    counts.append(series.ingest(product,name,[r for _,r in items[start:start+10000]],provenance,
                                                interval_ms=max(0,interval)))
    # Missing historical metadata is visible maintenance work, not guessed
    # product identity or permission to use an earlier snapshot for this hour.
    compact = {identity(gap):gap for gap in gaps}
    db.metadata('collector.projection', {'source':str(source),'original_digest':original,
        'checked_at':stamp(),'gaps':list(compact.values()),'counts':counts}, write=True)
    return {'counts':counts,'gaps':list(compact.values())}
