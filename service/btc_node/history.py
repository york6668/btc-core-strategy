"""Incremental, chronological projection of the original immutable contracts."""
import json
from pathlib import Path

from .codec import Conflict, millis


def project_history(projection, root):
    from v4 import market_state, sensing
    from v4.common import digest
    db = projection.db
    rows = db.rows("SELECT d.* FROM documents d LEFT JOIN projection_sources p USING(document_key) "
        "WHERE d.namespace='strategy' AND d.kind='FILE' AND (p.digest IS NULL OR p.digest<>d.digest) AND "
        "(d.path LIKE 'v4/versions/%%/decision.json' OR d.path LIKE 'v4/sensing/%%.json' "
        "OR d.path LIKE 'v4/%%/market.json' OR d.path LIKE 'v4/%%/collector.json')")
    events, skipped = [], 0
    for row in rows:
        value = db.value(row['digest'])
        path = Path(root).parent / row['path']
        if row['path'].startswith('v4/versions/'):
            if value.get('decision_id') != path.parent.name or value['decision_id'] != digest({k:v for k,v in value.items() if k != 'decision_id'})[7:]:
                raise Conflict('historical decision hash mismatch')
            for name, expected in value.get('view_hashes', {}).items():
                if digest((path.parent/name).read_bytes()) != expected:
                    raise Conflict('historical decision view changed')
            known, kind = value['created_at'], 'DECISION'
        elif row['path'].startswith('v4/sensing/'):
            value = sensing.observation(root, value['observation_id'])
            known, kind = value['created_at'], 'SENSING'
        elif path.name == 'market.json' and isinstance(value.get('datasets'), dict) and value.get('retrievals'):
            known = min(r['retrieved_at'] for r in value['retrievals'].values())
            kind = 'PRICES'
        elif path.name == 'collector.json' and isinstance(value.get('assets'), dict):
            known, kind = value.get('captured_at'), 'COLLECTOR'
        else:
            known, kind = None, 'ARCHIVED_LEGACY'
        # Legacy mixed market/portfolio text remains available as its original
        # artifact; it is never relabelled as an independent market judgement.
        events.append((millis(known) if known else 0, kind, row, path, value))
    counts = {}
    for _, kind, row, path, value in sorted(events, key=lambda e:(e[0],e[1],e[2]['path'])):
        if kind == 'DECISION':
            assessment = value.get('assessment') or {}
            public = market_state.public_content(assessment)
            if public:
                content = {'market_assessment': public['outlook']['market_assessment'],
                           'dispute_zones': public['outlook'].get('dispute_zones', []), **public}
                market = projection.public(content, path, value['created_at'], reference=value['decision_id'])
                binding = {k:market[k] for k in ('object_id','version','digest')}
                projection.put('ALLOCATION', 'portfolio', {'market_binding':binding,
                    'fact_version':value['fact_version'], 'portfolio_analysis':assessment.get('portfolio_analysis'),
                    'portfolio':assessment.get('recommendations',{}).get('portfolio'),
                    'fact_authority':'bound source version'}, path, value['created_at'], scope='PERSONAL')
                for name, plan in assessment.get('recommendations',{}).items():
                    if name == 'portfolio': continue
                    projection.put('PLAN', 'recommendation:'+name, {'market_binding':binding,
                        'fact_version':value['fact_version'], 'recommendation':plan}, path, value['created_at'],
                        scope='PERSONAL', expires=(plan.get('validity') or {}).get('until'))
                for factor in public['factor_assessment'].get('items', []):
                    projection.put('FACTOR', factor['id'], factor, path, value['created_at'])
            else:
                skipped += 1
        elif kind == 'SENSING':
            review = value['review']
            if review.get('market_assessment'):
                projection.public(market_state.public_review(review), path, value['created_at'], reference=value['observation_id'])
            else:
                skipped += 1
        elif kind == 'PRICES':
            projection.prices(value, path)
        elif kind == 'COLLECTOR':
            from .collector_series import ingest
            ingest(db, value, path)
        else:
            skipped += 1
        db.execute('REPLACE INTO projection_sources(document_key,digest,projected_ms,outcome) VALUES(%s,%s,%s,%s)',
                   (row['document_key'],row['digest'],millis(),kind))
        counts[kind] = counts.get(kind,0)+1
    return {'sources':counts,'legacy_without_independent_public_contract':skipped}
