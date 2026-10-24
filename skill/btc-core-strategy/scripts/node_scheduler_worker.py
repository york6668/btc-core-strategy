"""Local-node task runner. Install its timer only after a verified cutover.

This is a thin adapter for the existing Docker writer and installed Codex CLI.
The durable task journal, release pin, deep budget and result validators live in
btc_node. An interrupted launch is never automatically replayed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import stat
import sys
import uuid

import node_config
import market_observation as market


MODEL = market.CURRENT_HOURLY_MODEL['name']
DEEP_MODEL = 'gpt-6-astra'
RESPONSE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'task_id': {'type': 'string'},
        'attempt_id': {'type': 'string'},
        'completion_ref': {'type': 'string'},
    },
    'required': ['task_id', 'attempt_id', 'completion_ref'],
}


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        _sync_directory(path.parent)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _replace_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    _write_private(temporary, content)
    os.replace(temporary, path)


def _docker_command(config):
    docker = config['docker_service']
    if not docker.get('enabled'):
        raise ValueError('existing Docker service is disabled')
    command = [docker['binary'], 'compose', '-p', docker['project'],
               '--env-file', docker['env_file'], '-f', docker['compose_file']]
    for extra in docker.get('override_files', []):
        command += ['-f', extra]
    return command + ['exec', '-T', 'core', 'python', '-m', 'btc_node', 'request']


class NodeClient:
    def __init__(self, config):
        self.command = _docker_command(config)

    def request(self, method, params=None, operation_id=None):
        request = {'method': method, 'params': params or {}}
        if operation_id:
            request['operation_id'] = operation_id
        from node_bridge import NodeTransportError, decode_node_response
        try:
            try:
                result = subprocess.run(self.command, input=json.dumps(request), text=True,
                                        capture_output=True, timeout=120)
            except subprocess.TimeoutExpired as exc:
                raise NodeTransportError('CLIENT_WAIT_TIMEOUT', operation_id=operation_id,
                                         error=exc, stage='CLIENT_WAIT') from None
            except OSError as exc:
                raise NodeTransportError('CLIENT_START_FAILED', operation_id=operation_id, error=exc) from None
            except UnicodeError as exc:
                raise NodeTransportError('CLIENT_OUTPUT_DECODE_FAILED', operation_id=operation_id,
                                         error=exc, stage='CLIENT_RESPONSE') from None
            response = decode_node_response(result, operation_id)
        except NodeTransportError as exc:
            failure = RuntimeError('Docker node response unavailable; inspect original operation; ' + str(exc))
            failure.diagnostic = exc.diagnostic
            raise failure from None
        if not response.get('ok'):
            raise RuntimeError(response.get('message', 'node request failed'))
        return response['result']


def _prompt(task, release):
    # Identifiers and node-verified references only; the model reads the
    # installed release and original artifacts. The prompt has no credentials.
    return ('You are executing one admitted BTC node task. Read AGENTS.md, '
            'skill/btc-core-strategy/SKILL.md and tasks/hourly.md in the pinned '
            'read-only release at ' + str(release) + '. Do not edit release files. '
            'Follow the existing stage-specific workflow. Do not use '
            'a Codex desktop automation, '
            'send a report, create a timer, or trade. Do not launch independent '
            'deep research unless kind is DEEP. Use the original node and '
            'domain validators. If blocked or uncertain, stop and explain in '
            'the final response; never invent a completion reference.\n'
            + json.dumps({key: task[key] for key in ('task_id', 'attempt_id', 'kind',
                'issue_id', 'phase', 'input_digest', 'evidence_ref', 'release_id',
                'reason', 'due_ms')}, ensure_ascii=False, sort_keys=True)
            + '\nFinal response must be JSON with task_id, attempt_id and '
            'completion_ref of the actual validated original artifact. No prose.')


def _valid_identity(value):
    return isinstance(value, str) and 0 < len(value) <= 191 and all(
        character.isalnum() or character in '/._:-' for character in value)


def _native_execution(raw):
    """Bind one admitted CLI execution to one ordered original thread/turn.

    The exec entry creates one turn. A second turn, an unfinished later turn,
    foreign identities or out-of-order terminal events cannot complete it.
    CLI events without server turn IDs retain an explicit None, never an
    invented ID; their one admitted ordered turn is the bounded evidence.
    """
    thread_id, turn_id = None, None
    started, completed, active = False, False, False
    try:
        for line in raw.splitlines():
            event = json.loads(line)
            if not isinstance(event, dict): return None
            kind = event.get('type')
            turn, item = event.get('turn') or {}, event.get('item') or {}
            if not isinstance(turn, dict) or not isinstance(item, dict): return None
            if (kind in {'turn.failed', 'error'} or event.get('error') is not None
                    or turn.get('error') is not None or item.get('type') == 'error'):
                return None
            if any(value in {'failed', 'interrupted', 'cancelled', 'canceled', 'error'}
                   for value in (event.get('status'), turn.get('status')) if isinstance(value, str)):
                return None
            if kind == 'thread.started':
                if thread_id is not None or not _valid_identity(event.get('thread_id')): return None
                thread_id = event['thread_id']
                continue
            if thread_id is None: return None
            if any(value not in (None, thread_id) for value in
                   (event.get('thread_id'), turn.get('thread_id'))): return None
            ids = [value for value in (event.get('turn_id'), turn.get('id')) if value is not None]
            if any(not _valid_identity(value) for value in ids) or len(set(ids)) > 1: return None
            identified = ids[0] if ids else None
            if kind == 'turn.started':
                if started or active or completed: return None
                turn_id, started, active = identified, True, True
            elif kind == 'turn.completed':
                if not active or completed or identified != turn_id: return None
                if any(status not in (None, 'completed', 'COMPLETE') for status in
                       (event.get('status'), turn.get('status'))): return None
                active, completed = False, True
            elif isinstance(kind, str) and kind.startswith(('turn.', 'thread.')):
                return None
            elif isinstance(kind, str) and kind.startswith('item.'):
                if not active or (identified is not None and identified != turn_id): return None
            elif identified is not None and (not active or identified != turn_id):
                return None
    except (ValueError, TypeError):
        return None
    if not (thread_id is not None and started and completed and not active): return None
    return {'thread_id': thread_id, 'turn_id': turn_id, 'ordered_single_turn': True}


def _native_terminal(raw):
    value = _native_execution(raw)
    return value['thread_id'] if value else None


def _native_session(events):
    """Original CLI event evidence only, never a session-store/status query."""
    try:
        return _native_terminal(events.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _runtime_preflight(config):
    """Reject a broken local executor before consuming a durable task attempt."""
    try:
        cli = Path(config['node_scheduler']['codex_cli']).expanduser().resolve(strict=True)
    except (KeyError, OSError):
        return 'CODEX_CLI_UNAVAILABLE'
    if not cli.is_file() or not os.access(cli, os.X_OK):
        return 'CODEX_CLI_UNAVAILABLE'
    try:
        project = Path(config['project_dir']).expanduser().resolve(strict=True)
    except (KeyError, OSError):
        return 'PROJECT_UNAVAILABLE'
    if not project.is_dir():
        return 'PROJECT_UNAVAILABLE'
    try:
        root = Path(config['release_root']).expanduser().resolve(strict=True)
        releases = (root / 'releases').resolve(strict=True)
        current = (root / 'current').resolve(strict=True)
    except (KeyError, OSError):
        return 'PINNED_RELEASE_UNAVAILABLE'
    if current.parent != releases or not (current / 'RELEASE.json').is_file():
        return 'PINNED_RELEASE_UNAVAILABLE'
    return None


def launch(config, task, directory):
    runtime_root = Path(config['release_root']).resolve(strict=True)
    release_root = runtime_root / 'releases'
    release = release_root / task['release_id']
    if (not release.is_dir() or release.resolve() != release or
            release.parent != release_root or not (release / 'RELEASE.json').is_file()
            or (runtime_root / 'current').resolve(strict=True) != release):
        raise RuntimeError('claimed physical release is unavailable')
    cli = Path(config['node_scheduler']['codex_cli']).resolve(strict=True)
    if not cli.is_file():
        raise RuntimeError('configured local Codex CLI unavailable')
    schema = directory / 'response-schema.json'
    output = directory / 'last-message.json'
    _write_private(schema, json.dumps(RESPONSE_SCHEMA, sort_keys=True).encode())
    model = DEEP_MODEL if task['kind'] == 'DEEP' else MODEL
    command = [str(cli), 'exec', '--json', '-m', model, '-s', 'workspace-write',
               '-c', 'model_reasoning_effort="max"' if task['kind'] == 'DEEP'
                     else 'model_reasoning_effort="high"',
               '--approve-for-me',
               '-C', str(Path(config['project_dir']).resolve(strict=True)),
               '--skip-git-repo-check', '--output-schema', str(schema),
               '-o', str(output), '-']
    environment = dict(os.environ)
    if config.get('_source_path'):
        environment['BTC_NODE_CONFIG'] = config['_source_path']
    if config.get('python'):
        environment['BTC_PYTHON'] = config['python']
    events = directory / 'events.jsonl'
    with os.fdopen(os.open(events, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                           0o600), 'wb') as stdout, os.fdopen(os.open(
                           directory / 'stderr.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                           0o600), 'wb') as stderr:
        process = subprocess.run(command, input=_prompt(task, release), text=True,
                                 stdout=stdout, stderr=stderr,
                                 timeout=3600, env=environment)
        stdout.flush(); stderr.flush()
        os.fsync(stdout.fileno()); os.fsync(stderr.fileno())
    if process.returncode:
        return {'outcome': 'UNKNOWN', 'kind': 'CLI_EXIT_UNKNOWN',
                'detail': 'Codex CLI exited after admission; inspect its original transcript'}
    native = _native_execution(_private_bytes(events).decode('utf-8'))
    if native is None:
        return {'outcome': 'UNKNOWN', 'kind': 'NATIVE_SESSION_UNVERIFIED',
                'detail': 'Codex native session has no unique completed thread reference'}
    try:
        result = json.loads(output.read_text())
    except (OSError, ValueError):
        return {'outcome': 'UNKNOWN', 'kind': 'RESULT_UNVERIFIED',
                'detail': 'CLI completed without a structured artifact reference'}
    if (not isinstance(result, dict) or set(result) != set(RESPONSE_SCHEMA['required'])
            or result['task_id'] != task['task_id']
            or result['attempt_id'] != task['attempt_id']
            or not _valid_identity(result['completion_ref'])):
        return {'outcome': 'UNKNOWN', 'kind': 'RESULT_UNVERIFIED',
                'detail': 'CLI result does not identify the admitted original attempt'}
    return {'outcome': 'COMPLETE', 'completion_ref': result['completion_ref'],
            'thread_id': native['thread_id'], 'turn_id': native['turn_id'],
            'native_evidence_scope': 'original_admitted_ordered_single_CLI_turn'}



ATTEMPT_RECEIPT_REVISION = 2
BINDING_FIELDS = ('task_id', 'attempt_id', 'kind', 'issue_id', 'phase',
                  'input_digest', 'evidence_ref', 'release_id', 'started_ms')


def _private_bytes(path, limit=8 * 1024 * 1024):
    """Only owned regular attempt artifacts; no native session-store access."""
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_mode & 0o077
            or before.st_uid != os.geteuid()):
        raise ValueError('attempt artifact is not an owned private regular file')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        current = os.fstat(descriptor)
        if (not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
                or current.st_size > limit):
            raise ValueError('attempt artifact changed or exceeds its bound')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            result = stream.read(limit + 1)
        if len(result) != current.st_size or len(result) > limit:
            raise ValueError('attempt artifact changed during read')
        return result
    finally:
        os.close(descriptor)


def _binding(task):
    return {key: task[key] for key in BINDING_FIELDS}


def recover_pending(client, attempt_root):
    """Revisit original attempts under the host lock, without launching models.

    The node's original domain verifier is still the completion authority.
    Missing or ambiguous external results remain pending; elapsed time is never
    evidence of NOT_EXECUTED. Resolve its saved node transaction through the
    original writer/ID; never resend a newly identified reconciliation.
    """
    pending = client.request('task.status')['tasks']
    outcomes = []
    for task in pending:
        if (task.get('namespace') != 'formal'
                or (task.get('state') != 'RUNNING' and task.get('blocker') not in
                    {'UNKNOWN_OUTCOME', 'DELIVERY_UNKNOWN'})):
            continue
        attempt = task.get('attempt_id')
        row = {'task_id': task['task_id'], 'attempt_id': attempt, 'action': 'UNKNOWN_NO_REPLAY'}
        outcomes.append(row)
        if (not _valid_identity(attempt) or Path(attempt).name != attempt
                or attempt in {'.', '..'}):
            row['reason'] = 'unsafe_original_attempt_identity'
            continue
        directory = attempt_root / attempt
        try:
            meta = directory.lstat()
            if (not stat.S_ISDIR(meta.st_mode) or meta.st_mode & 0o077
                    or meta.st_uid != os.geteuid()):
                raise ValueError('attempt directory is not owned and private')
            claim_bytes = _private_bytes(directory / 'claim.json')
            claim = json.loads(claim_bytes)
            if _binding(claim) != _binding(task):
                raise ValueError('original task and saved claim do not match')
            # Submission identity belongs to the original attempt, not its
            # mutable evidence bytes. Even legacy R1 per-proof intents fence it.
            fences = sorted(entry.name for entry in directory.iterdir()
                            if entry.name.startswith('reconcile-') and entry.name.endswith('.json'))
            if fences:
                operations = set()
                for name in fences:
                    if name in {'reconcile-intent.json', 'reconcile-intent.ack.json'}:
                        operations.add('schedule-reconcile-' + attempt)
                    legacy = re.fullmatch(r'reconcile-([0-9a-f]{64})(?:\.ack)?\.json', name)
                    if legacy:
                        operations.add('schedule-reconcile-' + attempt + '-' + legacy.group(1)[:16])
                row.update(reason='original_reconciliation_requires_operation_recovery',
                           original_operation_ids=sorted(operations),
                           original_reconciliation_artifacts=fences)
                if set(fences) <= {'reconcile-intent.json', 'reconcile-intent.ack.json'} and 'reconcile-intent.json' in fences:
                    saved = json.loads(_private_bytes(directory / 'reconcile-intent.json'))
                    fields = {'worker_receipt_revision', 'task', 'outcome', 'completion_ref',
                              'evidence', 'proof_sha256', 'operation_id', 'original_request'}
                    if not isinstance(saved, dict) or set(saved) != fields:
                        raise ValueError('original reconciliation receipt schema/integrity unavailable')
                    proof = {key: saved[key] for key in
                             ('worker_receipt_revision', 'task', 'outcome', 'completion_ref', 'evidence')}
                    expected = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
                    if (saved['proof_sha256'] != expected
                            or saved['worker_receipt_revision'] != ATTEMPT_RECEIPT_REVISION
                            or not isinstance(saved['evidence'], dict)
                            or saved['evidence'].get('claim_sha256') != hashlib.sha256(claim_bytes).hexdigest()):
                        raise ValueError('original reconciliation proof checksum/binding mismatch')
                    if saved['outcome'] == 'NOT_EXECUTED':
                        if (saved['completion_ref'] is not None
                                or saved['evidence'].get('launch_intent_absent_under_original_worker_lock') is not True
                                or any(os.path.lexists(directory / name) for name in
                                       ('launch-intent.json', 'events.jsonl', 'last-message.json', 'result.json'))):
                            raise ValueError('nonexecution proof conflicts with original execution artifacts')
                    elif (saved['outcome'] != 'COMPLETE'
                          or not _valid_identity(saved['completion_ref'])
                          or saved['evidence'].get('ordered_single_turn') is not True):
                        raise ValueError('original positive completion proof unavailable')
                    original = saved.get('original_request')
                    operation = 'schedule-reconcile-' + attempt
                    params = {'task_id': task['task_id'], 'attempt_id': attempt,
                              'outcome': saved['outcome'],
                              'evidence_ref': 'node-worker/' + attempt + '/reconcile-intent.json'}
                    if saved.get('completion_ref') is not None:
                        params['completion_ref'] = saved['completion_ref']
                    if (saved.get('task') != _binding(task) or saved.get('operation_id') != operation
                            or original != {'method': 'task.reconcile', 'params': params, 'operation_id': operation}):
                        row['reason'] = 'legacy_or_mismatched_request_requires_explicit_original_operation_review'
                        continue
                    recovered = client.request('core.recover', {'operation_id': operation,
                                                              'original_request': original})
                    if (recovered.get('recovery') != 'ORIGINAL_NODE_TRANSACTION_RECONCILED'
                            or recovered.get('operation_id') != operation):
                        raise ValueError('original node recovery capability/result not verified')
                    settled = recovered['result']
                    row.update(action=settled['action'], outcome=saved['outcome'], operation_id=operation)
                    ack = directory / 'reconcile-intent.ack.json'
                    if not os.path.lexists(ack):
                        _write_private(ack, json.dumps({'operation_id': operation, 'result': settled}, sort_keys=True).encode())
                continue
            intent_path = directory / 'launch-intent.json'
            intent_missing = not os.path.lexists(intent_path)
            if intent_missing and claim.get('worker_receipt_revision') == ATTEMPT_RECEIPT_REVISION:
                if any(os.path.lexists(directory / name) for name in
                       ('events.jsonl', 'last-message.json', 'result.json')):
                    raise ValueError('missing launch intent conflicts with saved execution artifacts')
                outcome, completion = 'NOT_EXECUTED', None
                evidence = {'claim_sha256': hashlib.sha256(claim_bytes).hexdigest(),
                            'launch_intent_absent_under_original_worker_lock': True}
            else:
                intent_bytes = None
                if not intent_missing:
                    intent_bytes = _private_bytes(intent_path)
                    intent = json.loads(intent_bytes)
                    if (intent.get('worker_receipt_revision') != ATTEMPT_RECEIPT_REVISION
                            or intent.get('task') != _binding(task)):
                        raise ValueError('launch intent does not bind the original task')
                # Legacy receipts can prove a positive completed result; lack of
                # a legacy launch-intent can never prove nonexecution.
                events = _private_bytes(directory / 'events.jsonl')
                message = _private_bytes(directory / 'last-message.json', 64 * 1024)
                native = _native_execution(events.decode('utf-8'))
                result = json.loads(message)
                if (native is None
                        or not isinstance(result, dict)
                        or set(result) != set(RESPONSE_SCHEMA['required'])
                        or result['task_id'] != task['task_id']
                        or result['attempt_id'] != attempt
                        or not _valid_identity(result['completion_ref'])):
                    raise ValueError('original native output is not a unique completed result')
                outcome, completion = 'COMPLETE', result['completion_ref']
                evidence = {'claim_sha256': hashlib.sha256(claim_bytes).hexdigest(),
                            'launch_intent_sha256': hashlib.sha256(intent_bytes).hexdigest() if intent_bytes is not None else None,
                            'events_sha256': hashlib.sha256(events).hexdigest(),
                            'last_message_sha256': hashlib.sha256(message).hexdigest(),
                            'native_thread_id': native['thread_id'],
                            'native_turn_id': native['turn_id'], 'ordered_single_turn': True}
            proof = {'worker_receipt_revision': ATTEMPT_RECEIPT_REVISION,
                     'task': _binding(task), 'outcome': outcome,
                     'completion_ref': completion, 'evidence': evidence}
            proof_bytes = json.dumps(proof, sort_keys=True).encode()
            proof_hash = hashlib.sha256(proof_bytes).hexdigest()
            receipt = directory / 'reconcile-intent.json'
            operation_id = 'schedule-reconcile-' + attempt
            params = {'task_id': task['task_id'], 'attempt_id': attempt,
                      'outcome': outcome, 'evidence_ref': 'node-worker/' + attempt + '/' + receipt.name}
            if completion is not None: params['completion_ref'] = completion
            original = {'method': 'task.reconcile', 'params': params, 'operation_id': operation_id}
            intent_record = dict(proof, proof_sha256=proof_hash, operation_id=operation_id,
                                 original_request=original)
            # Persist exact original request BEFORE the first transaction.
            _write_private(receipt, json.dumps(intent_record, sort_keys=True).encode())
            settled = client.request('task.reconcile', params, operation_id=operation_id)
            row.update(action=settled['action'], outcome=outcome, operation_id=operation_id)
            _write_private(receipt.with_suffix('.ack.json'),
                           json.dumps({'operation_id': operation_id, 'result': settled}, sort_keys=True).encode())
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            row['reason'] = type(exc).__name__ + ': ' + str(exc)[:160]
    return outcomes

def run_once(config, client=None, launcher=launch):
    settings = config.get('node_scheduler') or {}
    if settings.get('enabled') is not True:
        return {'action': 'DISABLED'}
    if not settings.get('attempt_dir') or not settings.get('codex_cli'):
        raise ValueError('node worker requires a private attempt directory and CLI path')
    client = client or NodeClient(config)
    attempt_root = Path(settings['attempt_dir']).expanduser().resolve()
    attempt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if attempt_root.stat().st_mode & 0o077:
        raise ValueError('node worker attempt directory must be private')
    # Host admission lock complements the Docker writer transaction. A second
    # host invocation cannot race to claim while this model process runs.
    with (attempt_root / '.worker.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'action': 'WORKER_ALREADY_RUNNING'}
        try:
            recovered = recover_pending(client, attempt_root)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            return {'action': 'ORIGINAL_ATTEMPT_STATUS_UNAVAILABLE_NO_LAUNCH',
                    'reason': type(exc).__name__}
        if any(row['action'] in {'COMPLETED', 'NEEDED'} for row in recovered):
            return {'action': 'RECOVERED_ORIGINAL_ATTEMPT_NO_LAUNCH', 'recovery': recovered}
        # The Docker node owns the clock and due registration. A host adapter
        # may execute only while that clock has a fresh, fault-free window.
        window = client.request('schedule.window')
        if window.get('status') != 'AVAILABLE':
            return {'action': 'NO_ACTIVE_NODE_SCHEDULE', 'window': window}
        missing = _runtime_preflight(config)
        if missing:
            return {'action': 'NODE_RUNTIME_UNAVAILABLE', 'reason': missing}
        if settings.get('window_file'):
            _replace_private(Path(settings['window_file']).expanduser(),
                             json.dumps(window, sort_keys=True).encode())
        attempt_id = 'node-' + uuid.uuid4().hex
        claim = client.request('schedule.claim', {'attempt_id': attempt_id},
                               operation_id='schedule-claim-' + attempt_id)
        if claim.get('action') != 'LAUNCH_ONCE':
            return dict(claim, **({'recovery': recovered} if recovered else {}))
        task = claim['task']
        if task['attempt_id'] != attempt_id or not _valid_identity(task['task_id']):
            raise RuntimeError('node claim identity mismatch; original attempt remains RUNNING')
        directory = attempt_root / attempt_id
        directory.mkdir(mode=0o700)
        claim_receipt = dict(task, worker_receipt_revision=ATTEMPT_RECEIPT_REVISION)
        _write_private(directory / 'claim.json', json.dumps(claim_receipt, sort_keys=True).encode())
        _write_private(directory / 'launch-intent.json', json.dumps({
            'worker_receipt_revision': ATTEMPT_RECEIPT_REVISION,
            'task': _binding(task)}, sort_keys=True).encode())
        try:
            result = launcher(config, task, directory)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result = {'outcome': 'UNKNOWN', 'kind': type(exc).__name__,
                      'detail': str(exc)[:500]}
        trace = {'schema': 'BTC_NODE_WORK_ATTEMPT_V1', 'task_id': task['task_id'],
                 'attempt_id': attempt_id, 'outcome': result['outcome'],
                 'native_thread_id': result.get('thread_id'),
                 'native_turn_id': result.get('turn_id'),
                 'completion_ref': result.get('completion_ref'),
                 'failure_kind': result.get('kind'), 'detail': result.get('detail')}
        _write_private(directory / 'result.json', json.dumps(trace, sort_keys=True).encode())
        if result['outcome'] == 'COMPLETE':
            params = {'task_id': task['task_id'], 'attempt_id': attempt_id,
                      'outcome': 'COMPLETE', 'completion_ref': result['completion_ref']}
        else:
            params = {'task_id': task['task_id'], 'attempt_id': attempt_id,
                      'outcome': 'UNKNOWN', 'completion_ref': 'unknown/' + attempt_id,
                      'failure_kind': 'UNKNOWN' if task['kind'] == 'DEEP' else result['kind'],
                      'failure_ref': 'node-worker/' + attempt_id + '/result.json'}
        # If validation or transport fails, RUNNING remains durable. A later
        # tick cannot relaunch; the next worker entry inspects this original
        # attempt and its persisted native/domain effects before any new claim.
        finished = client.request('task.finish', params,
                                  operation_id='schedule-finish-' + attempt_id)
        return {'action': finished['action'], 'task_id': task['task_id'],
                'attempt_id': attempt_id, 'recovery': recovered, 'model': DEEP_MODEL if task['kind'] == 'DEEP' else MODEL}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='BTC_NODE_CONFIG_V1 file')
    args = parser.parse_args()
    config = node_config.load(args.config)
    selected = args.config or os.environ.get('BTC_NODE_CONFIG')
    if selected:
        config['_source_path'] = str(Path(selected).expanduser().resolve())
    result = run_once(config)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
