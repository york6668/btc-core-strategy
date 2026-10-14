"""Node-owned execution obligations, separate from business conclusions.

The task journal points to existing evidence, research, strategy and delivery
artifacts.  It never manufactures a market judgement or confirms a send.
Callers hold Database.writer() and a transaction for every mutation.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .codec import Conflict, millis
from . import deep_gate


NAME = 'tasks.BTC'
SCHEMA = 'BTC_NODE_TASKS_V1'
KINDS = {'COLLECT', 'SIGNAL', 'RECHECK', 'DEEP', 'MAIN', 'REPORT', 'MAINTENANCE', 'RETEST'}
STATES = {'NEEDED', 'BLOCKED', 'READY', 'RUNNING', 'COMPLETED'}
DEEP_REASONS = {'INITIAL_QUESTION', 'NEW_KEY_EVIDENCE', 'MATERIAL_COUNTEREVIDENCE',
                'USER_REQUESTED_REVIEW'}
SIDE_EFFECTS = {'DEEP', 'REPORT'}


def _text(value, name):
    if not isinstance(value, str) or not value or len(value) > 191:
        raise ValueError(name + ' must be a stable non-empty identifier')
    return value


def _state(db):
    value = db.metadata(NAME)
    if value is None:
        return {'schema': SCHEMA, 'tasks': {}}
    if value.get('schema') != SCHEMA or not isinstance(value.get('tasks'), dict):
        raise Conflict('node task journal schema is incompatible')
    return deepcopy(value)


def _save(db, value):
    db.metadata(NAME, value, write=True)


def _verified_completion(db, task, completion_ref):
    if task['kind'] == 'REPORT' and task['phase'] == 'DELIVERY':
        from .entities import Entities
        delivered = Entities(db).get('DELIVERY', completion_ref)
        status = (delivered or {}).get('payload', {}).get('status', {})
        if status.get('state') != 'DELIVERED':
            raise Conflict('delivery task requires the original verified native receipt')


def status(db, *, task_id=None):
    value = _state(db)
    if task_id is not None:
        return value['tasks'].get(_text(task_id, 'task_id'))
    return {'schema': SCHEMA, 'tasks': sorted(value['tasks'].values(),
            key=lambda item: (item['due_ms'], item['task_id']))}


def submit(db, *, task_id, issue_id, kind, phase, input_digest, release_id,
           evidence_ref, reason, reason_kind, scope, due_ms, namespace='formal'):
    """Register one business question; an hourly clock alone is not evidence."""
    for name, item in (('task_id', task_id), ('issue_id', issue_id),
                       ('phase', phase), ('release_id', release_id),
                       ('evidence_ref', evidence_ref),
                       ('reason_kind', reason_kind), ('namespace', namespace)):
        _text(item, name)
    if not isinstance(reason, str) or len(reason.strip()) < 12:
        raise ValueError('business reason must explain the question')
    deep_gate._digest(input_digest)
    deep_gate._scope(scope)
    if kind not in KINDS or type(due_ms) is not int or due_ms < 0:
        raise ValueError('kind and UTC due_ms required')
    if kind == 'DEEP' and reason_kind not in DEEP_REASONS:
        raise ValueError('deep research needs a substantive question, not a timer or touch')
    if kind == 'RETEST' and namespace == 'formal':
        raise Conflict('retest must use its isolated namespace')
    if namespace != 'formal' and kind in {'DEEP', 'REPORT'}:
        raise Conflict('retest cannot consume the formal deep slot or deliver a formal report')
    state = _state(db)
    prior = state['tasks'].get(task_id)
    identity = (issue_id, kind, phase, release_id, scope, namespace)
    if prior:
        old = tuple(prior[key] for key in ('issue_id','kind','phase','release_id','scope','namespace'))
        if old != identity:
            raise Conflict('task_id already belongs to another problem, release or namespace')
        if prior['input_digest'] == input_digest:
            return {'action': 'ALREADY_TRACKED', 'task': prior}
        if prior['state'] == 'RUNNING':
            raise Conflict('in-flight task must be reconciled before changing its input')
        if prior['state'] == 'BLOCKED' and prior['blocker'] in {
                'UNKNOWN_OUTCOME', 'DELIVERY_UNKNOWN', 'ATTEMPTS_EXHAUSTED'}:
            raise Conflict('blocked original attempt requires reconciliation or verified repair')
        history = prior['history'] + [{key: prior[key] for key in
            ('input_digest','state','blocker','completion_ref','updated_ms','attempts')}]
        revision = prior['revision'] + 1
    else:
        history, revision = [], 1
    record = {'task_id': task_id, 'issue_id': issue_id, 'kind': kind, 'phase': phase,
              'input_digest': input_digest, 'release_id': release_id, 'scope': scope,
              'namespace': namespace, 'evidence_ref': evidence_ref, 'reason': reason,
              'reason_kind': reason_kind, 'due_ms': due_ms, 'revision': revision,
              'state': 'NEEDED', 'blocker': None, 'attempts': 0,
              'attempt_id': None, 'claim_id': None, 'completion_ref': None,
              'updated_ms': millis(), 'history': history}
    state['tasks'][task_id] = record
    _save(db, state)
    return {'action': 'REGISTERED', 'task': record}


def assess(db, *, task_id, dependency_ready, dependency_ref):
    """Separate a genuine obligation from authority to run it now."""
    _text(dependency_ref, 'dependency_ref')
    if type(dependency_ready) is not bool:
        raise ValueError('actual dependency readiness required')
    state = _state(db)
    task = state['tasks'].get(_text(task_id, 'task_id'))
    if not task:
        raise ValueError('unknown task')
    if task['state'] in {'RUNNING', 'COMPLETED'}:
        return {'action': 'UNCHANGED', 'task': task}
    if task['blocker'] in {'UNKNOWN_OUTCOME', 'DELIVERY_UNKNOWN', 'ATTEMPTS_EXHAUSTED'}:
        return {'action': 'RECONCILIATION_REQUIRED', 'task': task}
    blocker = None
    if task['due_ms'] > millis():
        blocker = 'NOT_DUE'
    elif not dependency_ready:
        blocker = 'DEPENDENCY_UNREADY'
    elif not deep_gate.check_scope(db, scope=task['scope'])['allowed']:
        blocker = 'OPEN_DEFECT'
    elif task['kind'] == 'DEEP':
        budget = deep_gate.status(db)
        if budget['days'].get(budget['today']) or any(
                row['state'] in {'CLAIMED','UNKNOWN'} for row in budget['days'].values()):
            blocker = 'DEEP_BUDGET_UNAVAILABLE'
    task['state'] = 'BLOCKED' if blocker else 'READY'
    task['blocker'] = blocker
    task['dependency_ref'] = dependency_ref
    task['updated_ms'] = millis()
    _save(db, state)
    return {'action': task['state'], 'task': task}


def start(db, *, task_id, attempt_id, release_id, claim_id=None, model=None):
    """Exactly one admission. A lost response never authorizes a second launch."""
    state = _state(db)
    task = state['tasks'].get(_text(task_id, 'task_id'))
    _text(attempt_id, 'attempt_id')
    if not task or task['state'] != 'READY':
        raise Conflict('task is not ready; inspect its blocker or original attempt')
    if task['release_id'] != release_id:
        raise Conflict('task belongs to another pinned release')
    settings = getattr(db, 'settings', None)
    if isinstance(settings, dict) and settings.get('release_root'):
        installed = Path(settings['release_root']) / release_id
        if not installed.is_dir() or not (installed / 'RELEASE.json').is_file():
            raise Conflict('task release is not an installed physical release')
    if task['attempts'] >= 2:
        raise Conflict('attempt limit reached; repair and register a changed input')
    if task['kind'] == 'DEEP':
        if model != 'gpt-6-astra':
            raise Conflict('BTC cycle deep research requires the approved Astra model')
        _text(claim_id, 'claim_id')
        claim = deep_gate.claim(db, claim_id=claim_id, issue_id=task['issue_id'],
            scope=task['scope'], phase=task['phase'], input_digest=task['input_digest'],
            model=model)
        if claim['action'] != 'CLAIMED_NEW':
            raise Conflict('existing deep result needs Sol continuation, not another model launch')
        task['claim_id'] = claim_id
    elif claim_id is not None or model is not None:
        raise ValueError('only deep research accepts a model claim')
    task['state'] = 'RUNNING'
    task['attempts'] += 1
    task['attempt_id'] = attempt_id
    task['updated_ms'] = millis()
    _save(db, state)
    return {'action': 'LAUNCH_ONCE', 'task': task}


def finish(db, *, task_id, attempt_id, outcome, completion_ref,
           failure_kind=None, failure_ref=None):
    """A failed or ambiguous external effect remains blocked until review."""
    if outcome not in {'COMPLETE', 'FAILED', 'UNKNOWN'}:
        raise ValueError('invalid task outcome')
    _text(completion_ref, 'completion_ref')
    if outcome != 'COMPLETE' and (not failure_kind or not failure_ref):
        raise ValueError('failure or unknown needs actual classified evidence')
    state = _state(db)
    task = state['tasks'].get(_text(task_id, 'task_id'))
    if not task or task['state'] != 'RUNNING' or task['attempt_id'] != attempt_id:
        raise Conflict('original running attempt required')
    if outcome == 'COMPLETE':
        _verified_completion(db, task, completion_ref)
    if task['kind'] == 'DEEP':
        deep_gate.finish(db, claim_id=task['claim_id'], outcome=outcome,
                         result_ref=completion_ref if outcome == 'COMPLETE' else None,
                         failure_kind=failure_kind, failure_ref=failure_ref)
    task['state'] = 'COMPLETED' if outcome == 'COMPLETE' else 'BLOCKED'
    task['blocker'] = (None if outcome == 'COMPLETE' else
                       'DELIVERY_UNKNOWN' if task['kind'] == 'REPORT' and outcome == 'UNKNOWN' else
                       'UNKNOWN_OUTCOME' if outcome == 'UNKNOWN' else 'ATTEMPTS_EXHAUSTED')
    task['completion_ref'] = completion_ref if outcome == 'COMPLETE' else None
    task['failure_ref'] = failure_ref if outcome != 'COMPLETE' else None
    task['updated_ms'] = millis()
    _save(db, state)
    return {'action': task['state'], 'task': task}


def reconcile(db, *, task_id, attempt_id, outcome, evidence_ref, completion_ref=None):
    """Recover an original ambiguous attempt without launching it again."""
    _text(evidence_ref, 'evidence_ref')
    state = _state(db)
    task = state['tasks'].get(_text(task_id, 'task_id'))
    if not task or task['attempt_id'] != attempt_id or task['state'] not in {'RUNNING','BLOCKED'}:
        raise Conflict('original in-flight or blocked attempt required')
    if outcome not in {'COMPLETE','NOT_EXECUTED','UNKNOWN'}:
        raise ValueError('verified original attempt outcome required')
    if outcome == 'COMPLETE':
        _text(completion_ref, 'completion_ref')
        _verified_completion(db, task, completion_ref)
        if task['kind'] == 'DEEP':
            deep_gate.reconcile_unknown(db, claim_id=task['claim_id'], outcome='COMPLETE',
                                        evidence_ref=evidence_ref, result_ref=completion_ref)
        task['state'], task['blocker'], task['completion_ref'] = 'COMPLETED', None, completion_ref
    elif outcome == 'NOT_EXECUTED' and task['kind'] not in SIDE_EFFECTS and task['attempts'] < 2:
        task['state'], task['blocker'] = 'NEEDED', None
    else:
        task['state'], task['blocker'] = 'BLOCKED', 'UNKNOWN_OUTCOME'
    task['reconciliation_ref'] = evidence_ref
    task['updated_ms'] = millis()
    _save(db, state)
    return {'action': task['state'], 'task': task}


def observe_formal_completion(db, *, task_id, decision_id):
    """Close an obligation only after the existing formal publisher succeeded.

    Projection.refresh calls this after storage.current has validated the
    published DecisionSnapshot and checked its whole-portfolio field.  The
    task journal is a follower, never an alternative conclusion owner.
    """
    state = _state(db)
    task = state['tasks'].get(task_id)
    if task is None or task['state'] == 'COMPLETED':
        return task
    if task['kind'] != 'MAIN' or task['phase'] != 'PERSONAL':
        raise Conflict('formal completion belongs to a personal main obligation')
    if task['state'] == 'RUNNING' or task['blocker'] in {'UNKNOWN_OUTCOME', 'DELIVERY_UNKNOWN'}:
        raise Conflict('in-flight original attempt needs reconciliation before formal closure')
    _text(decision_id, 'decision_id')
    task['state'], task['blocker'] = 'COMPLETED', None
    task['completion_ref'] = decision_id
    task['updated_ms'] = millis()
    _save(db, state)
    return task


def observe_research_completion(db, *, task_id, input_digest, independent_id):
    """Follow a domain-validated independent artifact without a new model call."""
    state = _state(db)
    task = state['tasks'].get(task_id)
    if task is None or task['state'] == 'COMPLETED':
        return task
    if task['kind'] != 'DEEP' or task['phase'] != 'PUBLIC' or task['input_digest'] != input_digest:
        raise Conflict('research artifact does not belong to this deep task')
    if task['state'] == 'RUNNING' or task['blocker'] in {'UNKNOWN_OUTCOME', 'DELIVERY_UNKNOWN'}:
        return task  # Only the original attempt may settle an admitted launch.
    _text(independent_id, 'independent_id')
    task['state'], task['blocker'] = 'COMPLETED', None
    task['completion_ref'] = independent_id
    task['updated_ms'] = millis()
    _save(db, state)
    return task


def repair(db, *, task_id, input_digest, release_id, evidence_ref,
           verification_ref, reason):
    """Explicitly rebase a stopped task after a verified repair or release change.

    This does not erase the failed attempt or reclaim a spent daily deep slot.
    Unknown external effects must first be reconciled against their originals.
    """
    for name, value in (('task_id', task_id), ('release_id', release_id),
                        ('evidence_ref', evidence_ref), ('verification_ref', verification_ref)):
        _text(value, name)
    deep_gate._digest(input_digest)
    if not isinstance(reason, str) or len(reason.strip()) < 12:
        raise ValueError('verified repair reason required')
    state = _state(db)
    task = state['tasks'].get(task_id)
    if not task or task['state'] not in {'NEEDED', 'BLOCKED', 'READY'}:
        raise Conflict('only a stopped original task may be repaired or migrated')
    if task['blocker'] in {'UNKNOWN_OUTCOME', 'DELIVERY_UNKNOWN'}:
        raise Conflict('uncertain external effect must be reconciled first')
    if input_digest == task['input_digest'] and release_id == task['release_id']:
        raise Conflict('repair requires a changed input or release')
    task['history'].append({key: task.get(key) for key in
        ('input_digest','release_id','state','blocker','completion_ref','updated_ms','attempts',
         'attempt_id','claim_id','failure_ref')})
    task.update(input_digest=input_digest, release_id=release_id,
                evidence_ref=evidence_ref, repair_ref=verification_ref,
                repair_reason=reason, revision=task['revision'] + 1,
                state='NEEDED', blocker=None, attempts=0, attempt_id=None,
                claim_id=None, completion_ref=None, failure_ref=None, updated_ms=millis())
    _save(db, state)
    return {'action': 'REPAIRED_NEEDS_ASSESSMENT', 'task': task}
