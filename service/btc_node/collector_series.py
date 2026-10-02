"""Keep collector observations auxiliary, with explicit identity and local knowledge time."""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .codec import identity, millis, stamp
from .series import Series


def ingest(db, package, source):
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
            rows = dataset.get('rows') or []
            if not rows: continue
            if not dataset.get('instrument') or not dataset.get('unit'):
                gaps.append({'asset':asset,'series':name,'reason':'original product/unit contract missing'})
                continue
            groups = defaultdict(list)
            for row in rows:
                observed = row.get('observed_ms')
                endpoint = (row.get('provenance') or {}).get('endpoint_url')
                if not isinstance(observed,int) or observed > known or not endpoint:
                    gaps.append({'asset':asset,'series':name,'reason':'row event time/source missing or later than receipt'})
                    continue
                # Product kind is deliberately source-series specific when the
                # transport provides no exchange product-class assertion.
                ident = {'market':urlsplit(endpoint).netloc,'symbol':dataset['instrument'],
                         'product_type':'COLLECTOR_SERIES:'+name,'quote_unit':dataset['unit'],
                         'source_identity':endpoint}
                product = 'collector:'+identity(ident)
                interval = 0
                if isinstance(row.get('open_time'),int) and isinstance(row.get('close_time'),int):
                    interval = row['close_time']-row['open_time']+1
                elif isinstance(row.get('period_start_ms'),int) and isinstance(row.get('period_end_ms'),int):
                    interval = row['period_end_ms']-row['period_start_ms']
                # Retrieval clocks are preserved by the original package and
                # one retrieval record, not copied into every unchanged bar.
                payload = {k:v for k,v in row.items() if k not in {'retrieved_at_ms','last_event_retrieved_ms'}}
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
                for start in range(0,len(items),10000):
                    counts.append(series.ingest(product,name,[r for _,r in items[start:start+10000]],provenance,
                                                interval_ms=max(0,interval)))
    # Missing historical metadata is visible maintenance work, not guessed
    # product identity or permission to use an earlier snapshot for this hour.
    compact = {identity(gap):gap for gap in gaps}
    db.metadata('collector.projection', {'source':str(source),'original_digest':original,
        'checked_at':stamp(),'gaps':list(compact.values()),'counts':counts}, write=True)
    return {'counts':counts,'gaps':list(compact.values())}
