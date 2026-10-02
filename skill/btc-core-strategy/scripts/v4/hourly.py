"""Bridge validated v4 completion into the existing public-only observation store."""
from pathlib import Path
import sqlite3
import market_observation
import strategy_contract
from .common import digest, instant, read, replace, require


def review_gate(review, market, completed_at, prior_market):
    """Preserve candidate and 24-hour rules while using the actual v4 clock."""
    latest = max(instant(r['retrieved_at']) for r in market['retrievals'].values())
    novel = market_observation._has_new_rows(market, prior_market) if prior_market is not None else None
    elapsed = (latest - instant(completed_at)).total_seconds() if completed_at else None
    initialization = completed_at is None
    due = bool(elapsed is not None and elapsed >= 86400 and novel)
    return {'initialization_review_due': initialization, 'periodic_review_due': due,
            'has_new_valid_evidence': novel,
            'deep_eligible': bool(review['deep_review']['required'] or initialization or due),
            'baseline_comparison': 'AVAILABLE' if prior_market is not None else 'RAW_BASELINE_UNAVAILABLE'}


def gate(root, db, binding):
    root = Path(root)
    policy = read(root / 'write-policy.json') if (root / 'write-policy.json').exists() else {}
    require(policy.get('minimum_assessment_revision', 1) < 2, 'legacy gate is replay-only; use sensing-gate')
    loaded = market_observation.load_review_binding(binding, db_path=db)
    pointer = root / 'deep-current.json'
    if not pointer.exists():
        return {k: loaded[k] for k in ('binding', 'deep_eligible', 'periodic_review_due', 'initialization_review_due', 'has_new_valid_evidence')}
    completed = read(root / read(pointer)['completion'])
    require(completed['completion_hash'] == digest({k: v for k, v in completed.items() if k != 'completion_hash'}), 'deep gate baseline damaged')
    accepted = completed['assessment']
    directory = root / 'evidence' / completed['evidence_id'][7:]
    from .evidence import validate, resolve
    package = validate(read(directory / 'evidence.json'), directory)
    sources = {s['id']: s for s in package['sources']}
    prior_market = None
    for ref in accepted['temporal_scope']['current_market_refs']:
        resolve(package, directory, ref, verified=True)
        raw_path = directory / 'raw' / (sources[ref['source_id']]['raw_hash'][7:] + '.json')
        raw = read(raw_path)
        if raw.get('schema') == market_observation.EVIDENCE_SCHEMA:
            from .market_runtime import validate_file
            validate_file(raw_path)
            require(instant(raw['cutoff']) == instant(accepted['temporal_scope']['market_cutoff']), 'deep gate raw window mismatch')
            prior_market = raw
            break
    return {'binding': binding, 'deep_completion_hash': completed['completion_hash'],
            **review_gate(loaded['review'], loaded['evidence'], completed['completed_at'], prior_market)}


def historical_domain(accepted):
    scope = accepted['temporal_scope']
    return instant(scope['domain_market_cutoff']) < instant(scope['market_cutoff'])


def _already_completed(db, kind, binding):
    if not Path(db).exists():
        return False
    with market_observation._connect(db, readonly=True) as conn:
        return conn.execute("SELECT 1 FROM completion_attempt WHERE kind=? AND review_id=? AND outcome='SUCCEEDED' LIMIT 1", (kind, binding['review_id'])).fetchone() is not None


def complete_deep(root, completion_path, db):
    root = Path(root)
    path = Path(completion_path).resolve()
    require(path.parent == (root / 'deep').resolve(), 'completion must be a local committed deep artifact')
    payload = read(path)
    require(payload['assessment'].get('revision', 1) < 3, 'main publication cannot be repaired as a legacy deep completion')
    require(payload['completion_hash'] == digest({k: v for k, v in payload.items() if k != 'completion_hash'}), 'deep completion damaged')
    published = False
    active = root / 'deep-current.json'
    if active.exists():
        published = (root / read(active)['completion']).resolve() == path
    from .storage import current
    node = current(root)
    seen = set()
    while node and not published:
        published = (node.get('assessment') or {}).get('draft_id') == payload['draft_id']
        parent = node.get('parent_id')
        if published or not parent:
            break
        require(parent not in seen, 'cycle in assessment ancestry')
        seen.add(parent)
        node = read(root / 'versions' / parent / 'decision.json')
        require(node['decision_id'] == digest({k: v for k, v in node.items() if k != 'decision_id'})[7:], 'damaged assessment ancestor')
    require(published, 'unpublished completion cannot advance successful deep baseline')
    # A crash after the formal pointer, before deep-current, leaves a published
    # assessment with an older completion pointer. Repair only from this proof.
    prior_completion = read(root / read(active)['completion']) if active.exists() else None
    if prior_completion:
        require(prior_completion['completion_hash'] == digest({k: v for k, v in prior_completion.items() if k != 'completion_hash'}), 'completion pointer damaged')
    newer = prior_completion is None or (
        instant(payload['completed_at']) >= instant(prior_completion['completed_at']) and
        all(instant(payload['assessment']['temporal_scope'][k]) >= instant(prior_completion['assessment']['temporal_scope'][k]) for k in ('market_cutoff', 'knowledge_cutoff')))
    if newer and (not active.exists() or (root / read(active)['completion']).resolve() != path):
        replace(active, {'completion': str(path.relative_to(root.resolve()))})
    accepted = payload['assessment']
    if accepted.get('revision') == 2:
        return {'status': 'V4_SENSING_BASELINE_AUTHORITATIVE'}
    binding = accepted['domain']['observation'].get('hourly_review_binding')
    if binding is None or accepted['domain']['runtime']['trigger_type'] in ('PLAN_GENERATION', 'MANUAL_REEVALUATION'):
        return {'status': 'MANUAL_NO_HOURLY_BINDING'}
    if historical_domain(accepted):
        return {'status': 'V4_AUTHORITATIVE_HISTORICAL_DOMAIN_NOT_RELABELED'}
    if _already_completed(db, 'DEEP', binding):
        return {'status': 'V4_AUTHORITATIVE_COMPATIBILITY_HISTORY_RETAINED'}
    return market_observation.record_completion(db, 'DEEP', binding, payload['completion_hash'], payload['completed_at'],
              accepted_market_assessment=strategy_contract.accepted_market_assessment(accepted['domain']))


def complete_report(root, identity, db):
    root = Path(root)
    from .delivery import validate_delivered, EDITED_SCHEMAS
    artifact, status, receipt = validate_delivered(root, identity)
    decision = read(root / 'versions' / artifact['decision_id'] / 'decision.json')
    accepted = decision['assessment']
    if artifact['schema'] in EDITED_SCHEMAS:
        require(accepted.get('revision', 1) >= 3, 'edited report has no main conclusion source')
        # Main conclusions do not imply a specialist review occurred.
        return {'status': 'V3_REPORTED_CONCLUSION_AUTHORITATIVE', 'decision_id': artifact['decision_id']}
    completion = root / 'deep' / (accepted['draft_id'][7:] + '.json')
    complete_deep(root, completion, db)
    if accepted.get('revision') == 2:
        return {'status': 'V4_REPORTED_BASELINE_AUTHORITATIVE'}
    binding = accepted['domain']['observation'].get('hourly_review_binding')
    if binding is None or accepted['domain']['runtime']['trigger_type'] in ('PLAN_GENERATION', 'MANUAL_REEVALUATION'):
        return {'status': 'MANUAL_NO_HOURLY_BINDING'}
    if historical_domain(accepted):
        return {'status': 'V4_AUTHORITATIVE_HISTORICAL_DOMAIN_NOT_RELABELED'}
    if _already_completed(db, 'REPORT', binding):
        return {'status': 'V4_AUTHORITATIVE_COMPATIBILITY_HISTORY_RETAINED'}
    return market_observation.record_completion(db, 'REPORT', binding, digest({'artifact': artifact, 'receipt': receipt}), status['updated_at'],
              accepted_market_assessment=strategy_contract.accepted_market_assessment(accepted['domain']), report_fingerprint=decision['comparison']['semantic_hash'])


def context(root, db, limit=240):
    """Include successful manual deep assessments without inventing an hourly review."""
    root = Path(root)
    policy = read(root / 'write-policy.json') if (root / 'write-policy.json').exists() else {}
    require(policy.get('minimum_assessment_revision', 1) < 2, 'recommendation sensing context requires --market; legacy context is replay-only')
    value = market_observation.read_context(db, limit)
    deep_pointer = root / 'deep-current.json'
    value['v4_successful_deep'] = None
    if deep_pointer.exists():
        completed = read(root / read(deep_pointer)['completion'])
        require(completed['completion_hash'] == digest({k: v for k, v in completed.items() if k != 'completion_hash'}), 'deep baseline damaged')
        accepted = completed['assessment']
        value['v4_successful_deep'] = {'completed_at': completed['completed_at'], 'evidence_id': completed['evidence_id'],
              'temporal_scope': accepted['temporal_scope'],
              'current_outlook': accepted['outlook'], 'current_robustness': accepted['robustness'],
              'current_portfolio_analysis': accepted['portfolio_analysis'],
              'domain_baseline': {'market_cutoff': accepted['temporal_scope']['domain_market_cutoff'],
                    'role': 'HISTORICAL_INVARIANT_ANCHOR' if historical_domain(accepted) else 'CURRENT_DOMAIN',
                    'accepted_market_assessment': strategy_contract.accepted_market_assessment(accepted['domain'])}}
    reported = root / 'reported-current.json'
    value['v4_reported'] = read(reported) if reported.exists() else None
    return value
