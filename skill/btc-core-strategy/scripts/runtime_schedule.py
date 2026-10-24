"""Optional execution timing from the existing project-owned window.

Report production never opens native Codex databases or session transcripts.
A missing supported window is diagnostic unavailability, not an observed lack
of scheduling and not proof of any business phase or market conclusion.
"""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path

_bound = None
_run_ref = None
COMMANDS = ('run-prepare', 'run-material', 'run-advance', 'report-care', 'report-context', 'report-review')


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


def snapshot(config, run_ref, at=None):
    at = at or datetime.now(timezone.utc)
    value = {'schema': 'BTC_EXECUTION_WINDOW_V1', 'status': 'UNAVAILABLE',
             'checked_at': at.isoformat(), 'run_ref': run_ref,
             'source': 'PLATFORM_TIMING_NOT_OBSERVED',
             'observation_state': 'NOT_OBSERVED',
             'reason': 'Supported execution timing is not bound. Native Codex databases and sessions are not queried; automatic review feasibility is unverified.'}
    node = config.get('node_scheduler') or {}
    if node.get('enabled') is not True:
        return value
    try:
        original = json.loads(Path(node['window_file']).read_text())
        cadence = int(node.get('poll_ms', 60000))
        checked = instant(original['checked_at'])
        if (original['schema'] != 'BTC_EXECUTION_WINDOW_V1' or
                original['source'] != 'BTC_NODE_SCHEDULER_V1' or
                original['status'] not in ('AVAILABLE', 'UNAVAILABLE') or
                not 60000 <= cadence <= 3600000 or
                abs((at - checked).total_seconds()) * 1000 > 2 * cadence):
            raise ValueError('node window is stale or invalid')
        if original['status'] == 'AVAILABLE':
            margin = original['execution_margin_seconds']
            if (type(margin) is not int or margin < 0 or
                    instant(original['next_run_at']) <= at or
                    instant(original['review_not_before']) < instant(original['next_run_at']) + timedelta(seconds=margin)):
                raise ValueError('node window does not establish a future execution floor')
        return {**original, 'run_ref': run_ref}
    except (OSError, KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return {**value, 'source': 'BTC_NODE_SCHEDULER_V1',
                'reason': 'The project-owned node window is unavailable or invalid; feasibility is unverified. No native platform fallback is used.'}


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
