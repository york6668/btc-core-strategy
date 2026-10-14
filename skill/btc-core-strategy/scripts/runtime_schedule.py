"""Read the existing local automation's timing; never create or change a task.

Only task identity/timestamps and native lifecycle events cross the Docker
boundary. No prompts, account identifiers or conversation contents are exported.
"""
from datetime import datetime, timezone, timedelta
import json
import math
import os
from pathlib import Path
import sqlite3

_bound = None
_run_ref = None
COMMANDS = ('run-prepare', 'run-material', 'run-advance', 'report-care', 'report-context', 'report-review')


def stamp(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('schedule timestamp requires a timezone')
    return result


def run_reference(argv):
    if '--run-ref' in argv:
        return argv[argv.index('--run-ref') + 1]
    for flag, key in (('--run', 'run_ref'), ('--spec', 'provenance')):
        if flag in argv:
            value = json.loads(Path(argv[argv.index(flag) + 1]).read_text())
            if not isinstance(value, dict):
                return None  # Leave malformed authored input to its own validator.
            ref = value.get(key)
            return ref.get('run_ref') if isinstance(ref, dict) else ref
    return os.environ.get('CODEX_THREAD_ID')


def lifecycle(path):
    """Use the first actual completed turn, not the UI archive/read timestamp."""
    started = None
    with Path(path).open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get('payload') or {}
            kind = payload.get('type') if isinstance(payload, dict) else None
            if kind == 'task_started':
                started = event.get('timestamp')
            elif kind in ('task_aborted', 'turn_aborted'):
                started = None
            elif kind == 'task_complete' and started:
                completed = event['timestamp']
                seconds = (instant(completed) - instant(started)).total_seconds()
                if seconds > 0:
                    return {'started_at': started, 'completed_at': completed,
                            'duration_seconds': seconds}
    return None


def snapshot(config, run_ref, at=None):
    at = at or datetime.now(timezone.utc)
    value = {'schema': 'BTC_EXECUTION_WINDOW_V1', 'status': 'UNAVAILABLE',
             'checked_at': at.isoformat(), 'run_ref': run_ref,
             'reason': 'Native automation timing is not available.'}
    node = config.get('node_scheduler') or {}
    if node.get('enabled') is True:
        try:
            original = json.loads(Path(node['window_file']).read_text())
            cadence = int(node.get('poll_ms', 60000))
            checked = instant(original['checked_at'])
            if (original['schema'] != 'BTC_EXECUTION_WINDOW_V1' or
                    original['source'] != 'BTC_NODE_SCHEDULER_V1' or
                    not 60000 <= cadence <= 3600000 or
                    abs((at - checked).total_seconds()) * 1000 > 2 * cadence):
                raise ValueError('node window is stale or invalid')
            return {**original, 'run_ref': run_ref}
        except (OSError, KeyError, TypeError, ValueError):
            return {**value, 'source': 'BTC_NODE_SCHEDULER_V1',
                    'reason': 'Local node scheduling window is unavailable or stale.'}
    state_path = config.get('codex_state_db')
    if not state_path or not run_ref:
        return value
    state_path = Path(state_path).expanduser().resolve()
    database = Path(config.get('codex_automations_db') or
                    state_path.parent / 'sqlite' / 'codex-dev.db').expanduser().resolve()
    value['source'] = {'database': str(database), 'state_database': str(state_path)}
    # Native scheduler rows use the thread ID. Keep the full domain run
    # reference in the frozen snapshot and use only its transport prefix-free
    # identity for native ownership and current-run exclusion.
    thread_id = run_ref[6:] if run_ref.startswith('codex:') else run_ref
    try:
        # URI read-only mode neither creates a database nor changes its schema.
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            owner = db.execute('SELECT automation_id FROM automation_runs WHERE thread_id=?',
                               (thread_id,)).fetchone()
            if owner is None:
                value.update(status='NOT_SCHEDULED', reason='No original automation owns this run.')
                return value
            value['automation_id'] = owner[0]
            columns = {r[1] for r in db.execute('PRAGMA table_info(automations)')}
            nominal = 'next_run_nominal_at' if 'next_run_nominal_at' in columns else 'NULL'
            row = db.execute('SELECT status,rrule,next_run_at,' + nominal + ' '
                             'FROM automations WHERE id=?', owner).fetchone()
            if not row or row[0] != 'ACTIVE' or row[2] is None:
                value['reason'] = 'The original automation has no active next execution time.'
                return value
            value.update(rrule=row[1], next_run_at=stamp(row[2]),
                         next_run_nominal_at=stamp(row[3]) if row[3] is not None else None)
            candidates = db.execute('SELECT thread_id FROM automation_runs WHERE automation_id=? '
                                    'AND thread_id<>? ORDER BY created_at DESC LIMIT 8',
                                    (owner[0], thread_id)).fetchall()
        samples = []
        with sqlite3.connect(state_path.as_uri() + '?mode=ro', uri=True) as db:
            for (identity,) in candidates:
                record = db.execute('SELECT rollout_path FROM threads WHERE id=?', (identity,)).fetchone()
                if not record:
                    continue
                try:
                    times = lifecycle(record[0])
                except (OSError, ValueError, TypeError, KeyError):
                    continue
                if times:
                    samples.append(dict(times, run_ref=identity))
                if len(samples) == 5:
                    break
        value['recent_completed_runs'] = samples
        if not samples:
            value['reason'] = 'No completed native run establishes an execution margin.'
            return value
        if instant(value['next_run_at']) <= at:
            value['reason'] = 'The next start is already due; refresh actual scheduler state before promising completion.'
            return value
        # A transparent feasibility allowance, not an investment horizon or SLA.
        margin = math.ceil(max(s['duration_seconds'] for s in samples)) + 60
        value.update(status='AVAILABLE', reason='Original next start plus the longest recent completed run and 60 seconds of completion allowance.',
                     execution_margin_seconds=margin,
                     review_not_before=(instant(value['next_run_at']) + timedelta(seconds=margin)).isoformat())
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        value['reason'] = 'Read-only native timing lookup failed; feasibility remains unverified.'
    return value


def bind(path, run_ref):
    global _bound, _run_ref
    value = json.loads(Path(path).read_text()) if path else None
    if value is not None and (value.get('schema') != 'BTC_EXECUTION_WINDOW_V1' or
                              (run_ref is not None and value.get('run_ref') != run_ref)):
        raise ValueError('execution window must belong to this actual run')
    _bound = value
    _run_ref = run_ref or (value or {}).get('run_ref')


def current(run_ref=None):
    if _bound is not None:
        return _bound
    import node_config
    return snapshot(node_config.load(), run_ref or _run_ref or os.environ.get('CODEX_THREAD_ID'))
