"""One BTC deep-research admission per Tokyo day, in the existing node state.

The service writer serializes callers from every task and node.  This module
does not launch a model; a successful claim is consumed even if the caller
loses its response or crashes before launch.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from zoneinfo import ZoneInfo

from .codec import Conflict, millis


STATE_NAME = 'model.deep.BTC'
TIMEZONE = 'Asia/Tokyo'
SCHEMA = 'BTC_DEEP_DAILY_GATE_V1'
FAULT_CLASSES = {'DETERMINISTIC', 'SEMANTIC', 'CAPACITY', 'NETWORK', 'PERMISSION', 'UNKNOWN'}


def tokyo_day(utc_ms):
    if type(utc_ms) is not int or utc_ms < 0:
        raise ValueError('UTC millisecond clock required')
    return datetime.fromtimestamp(utc_ms / 1000, timezone.utc).astimezone(ZoneInfo(TIMEZONE)).date().isoformat()


def _digest(value):
    if not isinstance(value, str) or len(value) != 71 or not value.startswith('sha256:'):
        raise ValueError('sha256 digest required')
    try: int(value[7:], 16)
    except ValueError as exc: raise ValueError('sha256 digest required') from exc
    return value


def _scope(value):
    if not isinstance(value, str) or not value or value.startswith('/') or '..' in value.split('/'):
        raise ValueError('affected scope required')
    if any(not part or not all(c.isalnum() or c in '_-' for c in part) for part in value.split('/')):
        raise ValueError('invalid affected scope')
    return value


def _affected(fault_scope, operation_scope):
    return (fault_scope == operation_scope or operation_scope.startswith(fault_scope + '/')
            or fault_scope.startswith(operation_scope + '/'))


def _state(db):
    state = db.metadata(STATE_NAME)
    if state is None:
        return {'schema': SCHEMA, 'timezone': TIMEZONE, 'days': {}, 'faults': {}}
    if state.get('schema') != SCHEMA or state.get('timezone') != TIMEZONE:
        raise Conflict('deep research gate state is incompatible')
    return deepcopy(state)


def _save(db, state):
    db.metadata(STATE_NAME, state, write=True)


def status(db):
    state = _state(db)
    return {'schema': SCHEMA, 'timezone': TIMEZONE, 'today': tokyo_day(millis()),
            'days': state['days'], 'faults': state['faults']}


def check_scope(db, *, scope):
    """Read the same open faults before ordinary affected model stages."""
    scope = _scope(scope)
    records = list(_state(db)['faults'].values())
    faults = [{'fault_id': item['fault_id'], 'kind': item['kind'], 'scope': item['scope']}
              for item in records
              if item['state'] == 'OPEN' and _affected(item['scope'], scope)]
    faults.sort(key=lambda item: item['fault_id'])
    corrections = [{key: item.get(key) for key in ('fault_id', 'scope', 'kind',
                   'fault_digest', 'evidence_ref', 'impact_ref', 'cause_ref',
                   'repair_digest', 'correction_ref', 'verification_ref', 'resolved_at_ms')}
                   for item in records if item['state'] == 'RESOLVED'
                   and _affected(item['scope'], scope)
                   and all(item.get(key) for key in ('impact_ref', 'cause_ref',
                                                     'correction_ref', 'verification_ref'))]
    corrections.sort(key=lambda item: (item['resolved_at_ms'], item['fault_id']))
    return {'scope': scope, 'allowed': not faults, 'faults': faults,
            'accepted_corrections': corrections}


def claim(db, *, claim_id, issue_id, scope, phase, input_digest, model):
    """Reserve the whole trading system's daily deep slot, not a task's slot."""
    if not isinstance(claim_id, str) or not 8 <= len(claim_id) <= 191:
        raise ValueError('stable claim_id required')
    if not isinstance(issue_id, str) or not issue_id:
        raise ValueError('original issue identity required')
    if not isinstance(phase, str) or not phase:
        raise ValueError('original research phase required')
    scope, input_digest = _scope(scope), _digest(input_digest)
    if not isinstance(model, str) or not model or model in {'gpt-6.1-sol', 'gpt-6-sol', 'gpt-5.6-sol'}:
        raise ValueError('this admission is for high-grade deep analysis only')
    now_ms = millis()
    day = tokyo_day(now_ms)
    state = _state(db)
    if any(item['state'] == 'OPEN' and _affected(item['scope'], scope)
           for item in state['faults'].values()):
        raise Conflict('known defect blocks affected deep-research scope')
    old = state['days'].get(day)
    if old:
        if old['state'] == 'COMPLETE' and old.get('result_ref'):
            if (old['issue_id'] == issue_id and old['scope'] == scope
                    and old['phase'] == phase and old['input_digest'] == input_digest
                    and old['model'] == model):
                return {'action':'REUSE', 'day':day, 'claim':old}
            raise Conflict('today deep-research slot belongs to another issue or input; it is not reusable')
        raise Conflict('today deep-research slot already consumed; inspect original claim')
    if any(item['state'] in ('CLAIMED','UNKNOWN') for item in state['days'].values()):
        raise Conflict('earlier in-flight or unknown deep call needs reconciliation')
    # Only a new high-grade launch needs the execution interlock. Exact
    # completed-result REUSE above is read-only; ordinary Sol work may proceed
    # alongside independent research in either admission order.
    from . import tasks
    if tasks.same_execution_lane_running(tasks.status(db)['tasks'], 'DEEP'):
        raise Conflict('another deep research task is running')
    record = {'day':day, 'timezone':TIMEZONE, 'claim_id':claim_id,
              'issue_id':issue_id, 'scope':scope, 'phase':phase, 'input_digest':input_digest,
              'model':model, 'state':'CLAIMED', 'claimed_at_ms':now_ms,
              'finished_at_ms':None, 'result_ref':None, 'failure_kind':None}
    state['days'][day] = record
    _save(db,state)
    return {'action':'CLAIMED_NEW', 'day':day, 'claim':record}


def _fault_id(scope, signature):
    return 'sha256:' + hashlib.sha256((_scope(scope) + '\0' + _digest(signature)).encode()).hexdigest()


def open_fault(db, *, scope, kind, signature, fault_digest, evidence_ref,
               impact_ref=None, cause_ref=None):
    if kind not in FAULT_CLASSES:
        raise ValueError('fault classification required')
    if not isinstance(evidence_ref, str) or not evidence_ref:
        raise ValueError('actual failure evidence reference required')
    if any(value is not None and (not isinstance(value, str) or not value)
           for value in (impact_ref, cause_ref)):
        raise ValueError('impact and cause references must be nonempty when supplied')
    scope, signature, fault_digest = _scope(scope), _digest(signature), _digest(fault_digest)
    state = _state(db)
    identity = _fault_id(scope,signature)
    current = state['faults'].get(identity)
    if current and current['state'] == 'OPEN':
        return current
    record = {'fault_id':identity, 'scope':scope, 'kind':kind,'signature':signature,
              'fault_digest':fault_digest,'evidence_ref':evidence_ref,
              'impact_ref':impact_ref,'cause_ref':cause_ref,
              'state':'OPEN','opened_at_ms':millis(),'repair_digest':None,
              'correction_ref':None,'verification_ref':None,'resolved_at_ms':None}
    if current:
        record['prior_resolutions'] = current.get('prior_resolutions', []) + [
            {key: current.get(key) for key in ('fault_id', 'fault_digest', 'evidence_ref',
             'impact_ref', 'cause_ref', 'repair_digest', 'correction_ref',
             'verification_ref', 'opened_at_ms', 'resolved_at_ms')}]
    state['faults'][identity] = record
    _save(db,state)
    return record


def finish(db, *, claim_id, outcome, result_ref=None, failure_kind=None, failure_ref=None):
    """Settle a claim; failure/unknown remains spent and opens a fault."""
    if outcome not in ('COMPLETE','FAILED','UNKNOWN','CANCELED'):
        raise ValueError('invalid deep claim outcome')
    state = _state(db)
    slot = next((v for v in state['days'].values() if v['claim_id'] == claim_id),None)
    if not slot:
        raise ValueError('unknown original deep claim')
    if slot['state'] != 'CLAIMED':
        if slot['state'] == outcome and slot.get('result_ref') == result_ref:
            return {'claim':slot,'action':'ALREADY_SETTLED'}
        raise Conflict('deep claim already settled; do not rewrite outcome')
    if outcome == 'COMPLETE':
        if not isinstance(result_ref, str) or not result_ref:
            raise ValueError('completed deep call needs actual result reference')
    elif outcome in ('FAILED','UNKNOWN'):
        if failure_kind not in FAULT_CLASSES or not isinstance(failure_ref,str) or not failure_ref:
            raise ValueError('failed or unknown call needs classified failure evidence')
    elif not isinstance(failure_ref,str) or not failure_ref:
        raise ValueError('cancellation needs actual termination evidence')
    slot['state'] = outcome
    slot['finished_at_ms'] = millis()
    slot['result_ref'] = result_ref
    slot['failure_kind'] = failure_kind
    if outcome in ('FAILED','UNKNOWN'):
        signature = 'sha256:' + hashlib.sha256((failure_kind + '\0' + slot['input_digest']).encode()).hexdigest()
        identity = _fault_id(slot['scope'],signature)
        state['faults'][identity] = {'fault_id':identity,'scope':slot['scope'],
            'kind':failure_kind,'signature':signature,'fault_digest':slot['input_digest'],
            'evidence_ref':failure_ref,'state':'OPEN','opened_at_ms':millis(),
            'repair_digest':None,'verification_ref':None,'resolved_at_ms':None}
    _save(db,state)
    return {'claim':slot,'action':'SETTLED'}


def resolve_fault(db, *, fault_id, repair_digest, verification_ref, verification_status,
                  correction_ref=None):
    """Mechanical recovery gate; actual evidence is still independently reviewed."""
    repair_digest = _digest(repair_digest)
    if not isinstance(verification_ref,str) or not verification_ref:
        raise ValueError('actual isolated verification reference required')
    if correction_ref is not None and (not isinstance(correction_ref, str) or not correction_ref):
        raise ValueError('correction reference must be nonempty when supplied')
    state = _state(db)
    item = state['faults'].get(fault_id)
    if not item or item['state'] != 'OPEN':
        raise Conflict('original open fault required')
    if item['kind'] in ('DETERMINISTIC','SEMANTIC','UNKNOWN'):
        if repair_digest == item['fault_digest'] or verification_status != 'PASS':
            raise Conflict('changed repair and passing isolated verification required')
    elif item['kind'] in ('CAPACITY','NETWORK'):
        if verification_status != 'RECOVERED':
            raise Conflict('actual service recovery evidence required')
    elif verification_status != 'AUTHORIZED':
        raise Conflict('permission denial needs actual matching authorization')
    item['state'] = 'RESOLVED'
    item['repair_digest'] = repair_digest
    item['correction_ref'] = correction_ref
    item['verification_ref'] = verification_ref
    item['resolved_at_ms'] = millis()
    _save(db,state)
    return item


def reconcile_unknown(db, *, claim_id, outcome, evidence_ref, result_ref=None):
    """Resolve an unknown older call; never reopens its already spent day."""
    if outcome not in ('COMPLETE','CANCELED','FAILED') or not evidence_ref:
        raise ValueError('verified original-call outcome and evidence required')
    state = _state(db)
    slot = next((v for v in state['days'].values() if v['claim_id'] == claim_id),None)
    if not slot or slot['state'] not in ('CLAIMED','UNKNOWN'):
        raise Conflict('only an in-flight or unknown original call can be reconciled')
    if outcome == 'COMPLETE' and not result_ref:
        raise ValueError('verified completed call needs actual result reference')
    slot['state'] = outcome
    slot['finished_at_ms'] = millis()
    slot['result_ref'] = result_ref
    slot['reconciliation_ref'] = evidence_ref
    _save(db,state)
    return {'claim':slot,'action':'RECONCILED_NO_NEW_CALL'}
