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
from pathlib import Path
import subprocess
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


def _write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
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
        result = subprocess.run(self.command, input=json.dumps(request), text=True,
                                capture_output=True, timeout=120)
        try:
            response = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError('Docker node response unavailable; inspect original operation') from exc
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


def _native_session(events):
    """Link the node attempt to Codex's saved native execution history."""
    threads, completed = set(), False
    try:
        with events.open(encoding='utf-8') as stream:
            for line in stream:
                event = json.loads(line)
                if event.get('type') == 'thread.started':
                    threads.add(event.get('thread_id'))
                elif event.get('type') == 'turn.completed':
                    completed = True
                elif event.get('type') == 'turn.failed':
                    return None
    except (OSError, ValueError, TypeError):
        return None
    identity = next(iter(threads)) if len(threads) == 1 else None
    return identity if completed and _valid_identity(identity) else None


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
    thread_id = _native_session(events)
    if thread_id is None:
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
            'thread_id': thread_id}


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
            return claim
        task = claim['task']
        if task['attempt_id'] != attempt_id or not _valid_identity(task['task_id']):
            raise RuntimeError('node claim identity mismatch; original attempt remains RUNNING')
        directory = attempt_root / attempt_id
        directory.mkdir(mode=0o700)
        _write_private(directory / 'claim.json', json.dumps(task, sort_keys=True).encode())
        try:
            result = launcher(config, task, directory)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result = {'outcome': 'UNKNOWN', 'kind': type(exc).__name__,
                      'detail': str(exc)[:500]}
        trace = {'schema': 'BTC_NODE_WORK_ATTEMPT_V1', 'task_id': task['task_id'],
                 'attempt_id': attempt_id, 'outcome': result['outcome'],
                 'native_thread_id': result.get('thread_id'),
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
        # tick cannot relaunch; an operator reconciles the original artifact.
        finished = client.request('task.finish', params,
                                  operation_id='schedule-finish-' + attempt_id)
        return {'action': finished['action'], 'task_id': task['task_id'],
                'attempt_id': attempt_id, 'model': DEEP_MODEL if task['kind'] == 'DEEP' else MODEL}


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
