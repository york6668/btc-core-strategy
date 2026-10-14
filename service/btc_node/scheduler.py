"""Disabled-by-default BTC node scheduler over the existing writer and task log.

The reader bridge supplies actual projected increments. Polling only revisits
durable obligations and due conditions; wall time never invents market change.
All mutations run under Database.writer() and one transaction.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .codec import Conflict, encode, fingerprint, millis
from .entities import Entities
from . import tasks


NAME = 'scheduler.BTC'
SCHEMA = 'BTC_NODE_SCHEDULER_V1'
MODES = {'OFF', 'SHADOW', 'ACTIVE'}
PRIORITY = {'SIGNAL': 0, 'RECHECK': 1, 'MAIN': 2, 'DEEP': 3}
RECHECK_KINDS = {
    'PUBLIC': {'REGION', 'PRICE_EVENT', 'HYPOTHESIS', 'CYCLE', 'FACTOR', 'DECISION'},
    'PERSONAL': {'ALLOCATION', 'PLAN'},
}


def mode(db):
    value = db.settings.get('scheduler_mode', 'OFF')
    if value not in MODES:
        raise Conflict('invalid node scheduler mode')
    return value


def _state(db):
    value = db.metadata(NAME)
    if value is None:
        return {'schema': SCHEMA, 'pending_reader_batches': {}, 'last_tick_ms': None,
                'last_claim_ms': None, 'due_cursors': {'PUBLIC': '', 'PERSONAL': ''},
                'next_due_scope': 'PUBLIC'}
    if value.get('schema') != SCHEMA or not isinstance(value.get('pending_reader_batches'), dict):
        raise Conflict('node scheduler state is incompatible')
    state = deepcopy(value)
    state.setdefault('due_cursors', {'PUBLIC': '', 'PERSONAL': ''})
    state.setdefault('next_due_scope', 'PUBLIC')
    return state


def _save(db, value):
    db.metadata(NAME, value, write=True)


def pinned_release(db):
    root = Path(db.settings['release_root']).resolve(strict=True)
    current = root.parent / 'current'
    pinned = current.resolve(strict=True)
    if pinned.parent != root or not (pinned / 'RELEASE.json').is_file():
        raise Conflict('active BTC release is not an installed physical version')
    return pinned.name


def observe_reader(db, prices):
    """Retain a real numerical/content increment before any model decision."""
    if mode(db) == 'OFF':
        return {'status': 'OFF'}
    changed = [row for row in prices.get('changed_receipts', [])
               if row['outcome'] == 'READER_PRICES_V1' and row['inserted'] > 0]
    if not changed:
        return {'status': 'NO_VALUE_INCREMENT', 'new_batches': 0}
    for row in changed:
        actual = db.one('SELECT digest,outcome FROM projection_sources WHERE document_key=%s',
                        (row['document_key'],))
        if not actual or actual['digest'] != row['digest'] or actual['outcome'] != row['outcome']:
            raise Conflict('reader increment lacks its persisted original projection')
    record = {'schema': 'BTC_READER_INCREMENT_V1', 'receipts': sorted(changed,
              key=lambda row: row['document_key'])}
    digest = 'sha256:' + db.put_blob(encode(record))
    state = _state(db)
    if digest in state['pending_reader_batches']:
        return {'status': 'ALREADY_TRACKED', 'batch_digest': digest, 'new_batches': 0}
    state['pending_reader_batches'][digest] = {'first_seen_ms': millis(),
                                               'registered': False}
    _save(db, state)
    return {'status': 'QUEUED', 'batch_digest': digest, 'new_batches': 1}


def _register_reader(db, state, release_id):
    registered = 0
    for digest, row in sorted(state['pending_reader_batches'].items()):
        if row['registered']:
            continue
        task_id = 'btc/signal/' + digest[7:]
        tasks.submit(db, task_id=task_id, issue_id='BTC_MARKET_SNIFF', kind='SIGNAL',
            phase='PUBLIC', input_digest=digest, release_id=release_id,
            evidence_ref=digest, scope='btc/market', due_ms=row['first_seen_ms'],
            reason_kind='ACTUAL_READER_INCREMENT',
            reason='Check actual changed reader measurements against the prior market thesis.')
        row['registered'] = True
        registered += 1
    return registered


def _register_due(db, state, release_id, *, limit=500):
    registered, scanned = 0, 0
    now_ms = millis()
    first = state['next_due_scope']
    if first not in {'PUBLIC', 'PERSONAL'}:
        raise Conflict('invalid due-scan cursor scope')
    for scope in (first, 'PERSONAL' if first == 'PUBLIC' else 'PUBLIC'):
        after = state['due_cursors'].get(scope, '')
        while scanned < limit:
            page = Entities(db).due(scope=scope, at=datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat(),
                                    after=after, limit=min(100, limit - scanned))
            for row in page['objects']:
                scanned += 1
                # Research, delivery, confirmed facts and profile upkeep have
                # distinct owners. A generic model recheck must not claim them.
                if row['kind'] not in RECHECK_KINDS[scope]:
                    continue
                requirements = [r['requirement_key'] for r in db.rows(
                    'SELECT requirement_key FROM review_requirements '
                    'WHERE entity_key=%s AND resolved_ms IS NULL ORDER BY requirement_key',
                    (row['entity_key'],))]
                times = [v for v in (row['review_at_ms'], row['valid_until_ms'])
                         if v is not None and v <= now_ms]
                due_ms = min(times) if times else 0
                original = {'schema': 'BTC_DUE_SUBJECT_V1', 'kind': row['kind'],
                            'object_id': row['object_id'], 'version': row['current_version'],
                            'scope': scope, 'due_ms': due_ms,
                            'open_requirements': requirements}
                digest = 'sha256:' + db.put_blob(encode(original))
                task_id = 'btc/recheck/' + fingerprint(encode((row['entity_key'], row['current_version'])))
                prior = tasks.status(db, task_id=task_id)
                if prior and (prior['release_id'] != release_id or prior['state'] == 'RUNNING'
                              or prior.get('blocker') in {
                                  'UNKNOWN_OUTCOME', 'DELIVERY_UNKNOWN', 'ATTEMPTS_EXHAUSTED'}):
                    continue
                outcome = tasks.submit(db, task_id=task_id,
                    issue_id=row['entity_key'], kind='RECHECK', phase=scope,
                    input_digest=digest, release_id=release_id, evidence_ref=digest,
                    reason_kind='OPEN_REVIEW' if requirements else 'ORIGINAL_DUE',
                    reason='Review the original subject and its actual outcome without extending its deadline.',
                    scope='btc/market' if scope == 'PUBLIC' else 'btc/research',
                    due_ms=due_ms)
                if outcome['action'] == 'REGISTERED':
                    registered += 1
            if not page['truncated']:
                state['due_cursors'][scope] = ''
                state['next_due_scope'] = 'PERSONAL' if scope == 'PUBLIC' else 'PUBLIC'
                break
            after = page['next']
            state['due_cursors'][scope] = after
            state['next_due_scope'] = scope
        if scanned >= limit:
            break
    return registered


def _dependency(db, task):
    kind, reference = task['kind'], task['evidence_ref']
    if kind in {'SIGNAL', 'RECHECK'}:
        value = db.value(reference[7:]) if reference.startswith('sha256:') else None
        if kind == 'SIGNAL':
            valid = bool(value and value.get('schema') == 'BTC_READER_INCREMENT_V1'
                         and any(row['inserted'] > 0 for row in value['receipts']))
            return valid, reference if valid else 'reader-increment-unavailable'
        if not value or value.get('schema') != 'BTC_DUE_SUBJECT_V1':
            return False, 'original-due-subject-unavailable'
        current = Entities(db).get(value['kind'], value['object_id'])
        valid = bool(current and current['version'] == value['version'])
        if not valid:
            return False, 'original-due-subject-superseded'
        if task['phase'] == 'PERSONAL':
            binding = current.get('payload', {}).get('market_binding') or {}
            frozen = Entities(db).get('DECISION', binding.get('object_id', ''),
                                      version=binding.get('version')) if binding else None
            if not frozen or frozen['scope'] != 'PUBLIC' or frozen['digest'] != binding.get('digest'):
                return False, 'frozen-market-binding-unavailable'
        return True, reference
    if kind == 'DEEP':
        research = Entities(db).get('RESEARCH', task['issue_id'])
        payload = (research or {}).get('payload', {})
        latest = payload.get('latest_request') or {}
        valid = (payload.get('status') == 'REQUESTED'
                 and latest.get('request_id') == reference
                 and latest.get('progress', {}).get('stage') == 'REQUESTED')
        return valid, reference if valid else 'original-research-stage-unavailable'
    if kind == 'MAIN':
        allocation = Entities(db).get('ALLOCATION', 'portfolio')
        payload = (allocation or {}).get('payload', {})
        valid = (payload.get('investment_completion') != 'FORMALLY_ADOPTED'
                 and bool(payload.get('market_binding')))
        return valid, reference if valid else 'formal-market-binding-or-gap-unavailable'
    if kind == 'REPORT':
        return False, 'native-original-destination-unavailable'
    return False, 'no-registered-node-executor-for-stage'


def tick(db):
    selected = mode(db)
    if selected == 'OFF':
        return {'mode': selected, 'action': 'DISABLED'}
    try:
        release_id = pinned_release(db)
    except (OSError, Conflict):
        return {'mode': selected, 'action': 'BLOCKED_RELEASE_UNAVAILABLE'}
    state = _state(db)
    reader_registered = _register_reader(db, state, release_id)
    due_registered = _register_due(db, state, release_id)
    results = []
    for task in tasks.status(db)['tasks']:
        if task['namespace'] != 'formal' or task['release_id'] != release_id:
            continue
        ready, proof = _dependency(db, task)
        result = tasks.assess(db, task_id=task['task_id'],
                              dependency_ready=bool(ready), dependency_ref=proof)
        results.append({'task_id': task['task_id'], 'state': result['task']['state'],
                        'blocker': result['task']['blocker']})
    state['last_tick_ms'] = millis()
    _save(db, state)
    return {'mode': selected, 'action': 'TICKED', 'release_id': release_id,
            'reader_registered': reader_registered, 'due_registered': due_registered,
            'tasks': results}


def claim(db, *, attempt_id):
    if mode(db) != 'ACTIVE':
        return {'action': 'NO_LAUNCH_IN_SHADOW_OR_OFF'}
    if not db.settings.get('test_only') and not (db.settings.get('scheduler_cutover_receipt')
                                                  and db.settings.get('native_report_transport_ready')):
        raise Conflict('formal cutover and original-destination transport proof required')
    release_id = pinned_release(db)
    all_tasks = tasks.status(db)['tasks']
    if any(row['state'] == 'RUNNING' and row['namespace'] == 'formal' for row in all_tasks):
        return {'action': 'ORIGINAL_ATTEMPT_RUNNING_NO_LAUNCH'}
    eligible = [row for row in all_tasks if row['namespace'] == 'formal'
                and row['release_id'] == release_id and row['state'] == 'READY'
                and row['kind'] in PRIORITY
                and (row['kind'] != 'DEEP' or db.settings.get('scheduler_deep_enabled') is True)]
    if not eligible:
        return {'action': 'NO_READY_TASK'}
    selected = min(eligible, key=lambda row: (row['due_ms'], PRIORITY[row['kind']], row['task_id']))
    result = tasks.start(db, task_id=selected['task_id'], attempt_id=attempt_id,
        release_id=release_id,
        **({'claim_id': attempt_id + '-deep', 'model': 'gpt-6-astra'}
           if selected['kind'] == 'DEEP' else {}))
    state = _state(db)
    state['last_claim_ms'] = millis()
    _save(db, state)
    return result


def status(db):
    return {'mode': mode(db), **_state(db)}


def window(db):
    """Node clock evidence for the existing report-care timing contract."""
    state, selected = _state(db), mode(db)
    cadence_ms = db.settings.get('scheduler_poll_ms', 60_000)
    if type(cadence_ms) is not int or not 60_000 <= cadence_ms <= 3_600_000:
        raise ValueError('node scheduler poll must be between one minute and one hour')
    last = state['last_tick_ms']
    cutover = (db.settings.get('test_only') is True or
               bool(db.settings.get('scheduler_cutover_receipt') and
                    db.settings.get('native_report_transport_ready')))
    available = (selected == 'ACTIVE' and cutover and last is not None
                 and millis() - last <= 2 * cadence_ms)
    next_ms = max(millis(), last + cadence_ms) if last is not None else None
    stamp = lambda value: datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    return {'schema': 'BTC_EXECUTION_WINDOW_V1',
            'status': 'AVAILABLE' if available else 'UNAVAILABLE',
            'source': 'BTC_NODE_SCHEDULER_V1', 'checked_at': stamp(millis()),
            'next_run_at': stamp(next_ms) if next_ms is not None else None,
            'execution_margin_seconds': 60,
            'review_not_before': stamp(next_ms + 60_000) if available else None,
            'reason': ('Recent node tick and configured poll cadence.' if available else
                       'Node cutover or recent active tick is unavailable; feasibility is unverified.')}
