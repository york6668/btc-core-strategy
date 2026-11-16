#!/usr/bin/env python3
"""Read runtime usage and optionally update its existing defect; never estimate quota charges."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import re
from pathlib import Path
import sqlite3
import subprocess
import time
from collections import Counter
from statistics import median

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens')
ORDINARY_MEDIAN_TARGET = 14


def execution_errors(output):
    """Inspect execution envelopes, not arbitrary successful stdout or examples."""
    found = {}

    def remember(signature, location, exit_code=None):
        signature = str(signature)[:220]
        found.setdefault(signature, {'signature': signature, 'output_path': location, 'exit_code': exit_code})
        return True

    def walk(value, location='$', failed=False):
        observed = False
        if isinstance(value, list):
            for i, child in enumerate(value):
                observed |= bool(walk(child, location + '/' + str(i), failed))
        elif isinstance(value, dict):
            code = value.get('exit_code')
            # A successful cat/read/test can print error examples. It did not fail.
            if code == 0 and not value.get('isError'):
                return False
            bad_exit = isinstance(code, int) and code != 0
            failed = failed or bad_exit or value.get('isError') is True
            if value.get('error'):
                observed |= remember(str(value['error']) + ': ' + str(value.get('message', ''))[:180], location, code)
            for key in ('output', 'content', 'text', 'result', 'results', 'value', 'structuredContent'):
                if key in value:
                    observed |= bool(walk(value[key], location + '/' + key, failed))
            if failed and not observed:
                observed |= remember('ProcessExit: ' + str(code) if bad_exit else 'ToolExecutionError', location, code)
            if bad_exit:
                for error in found.values():
                    if error['exit_code'] is None and error['output_path'].startswith(location):
                        error['exit_code'] = code
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                decoded = None
            if isinstance(decoded, (dict, list)):
                return walk(decoded, location, failed)
            terminal = re.search(r'(?:Process exited with code|Exit code:)\s*(-?\d+)', value)
            if terminal and int(terminal.group(1)) == 0:
                return False
            failed = failed or bool(terminal) or value.startswith(('Script error:', 'Traceback (most recent call last):'))
            fenced = False
            for i, line in enumerate(value.splitlines()):
                if line.strip().startswith('```'):
                    fenced = not fenced
                    continue
                if fenced:
                    continue
                if failed and re.match(r'^[\w.]*(?:Error|Exception):', line):
                    observed |= remember(line, location + '/line/' + str(i + 1), int(terminal.group(1)) if terminal else None)
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, (dict, list)):
                    observed |= bool(walk(item, location + '/line/' + str(i + 1), failed))
            if terminal and not observed:
                observed |= remember('ProcessExit: ' + terminal.group(1), location, int(terminal.group(1)))
            elif value.startswith('Script error:') and not observed:
                observed |= remember(value.splitlines()[0], location)
        return observed
    walk(output)
    return list(found.values())


def timestamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if abs(value) > 100000000000 else value
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None: raise ValueError('timestamps must include timezone')
    return dt.timestamp()


def rollout_source(value):
    """Follow Codex's plain-first representation of the same logical JSONL."""
    plain = Path(value)
    if plain.name.endswith('.jsonl.zst'):
        plain = plain.with_suffix('')
    try:
        return plain.resolve(strict=True), False
    except FileNotFoundError:
        if not plain.name.endswith('.jsonl'):
            raise
        return Path(str(plain) + '.zst').resolve(strict=True), True


@contextmanager
def rollout_stream(value):
    # Match the shared native meter's supported zstd stream. A representation
    # switch can race resolution/open; only retry that same logical source.
    for attempt in range(4):
        try:
            path, compressed = rollout_source(value)
            source = path.open('rb')
            break
        except FileNotFoundError:
            if attempt == 3:
                raise
            time.sleep(.05)
    with source:
        if not compressed:
            yield source, False
            return
        # Keep the original file descriptor across archival/materialization.
        # Never unpack, repair or modify native session files on disk.
        process = subprocess.Popen(['zstd', '-q', '-d', '-c'], stdin=source,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            yield process.stdout, True
            if process.wait() != 0:
                raise ValueError('native compressed rollout decoding failed')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def rollout(path, since=None, until=None, seen_responses=None, thread_id=None):
    usage = {}; calls = {}; started = set(); completed = set(); completed_at = None
    errors = Counter(); aborted = set(); waits = 0; compactions = 0
    error_occurrences = []; seen_errors = set()
    active_call_id = None
    turns = {}; active_turn = None; inherited_turns = set()
    unattributed = dict(model_roundtrips=0, **{key: 0 for key in FIELDS})
    def turn(identity):
        return turns.setdefault(identity, {'turn_id': identity, 'root_turn_id': identity,
            'started_at': None, 'completed_at': None, 'model': None, 'effort': None,
            'model_roundtrips': 0, 'tool_calls': 0, 'input_tokens': 0,
            'cached_input_tokens': 0, 'output_tokens': 0, 'failed_or_interrupted': False,
            'research_tool_observed': False, 'delivery_tool_observed': False})
    def record_errors(output, call_id, line_number, execution_id=None):
        for error in execution_errors(output):
            key = (call_id, error['signature'])
            native_key = (execution_id, error['signature']) if execution_id else None
            if key in seen_errors or (native_key and native_key in seen_errors):
                continue
            seen_errors.add(key)
            if native_key:seen_errors.add(native_key)
            errors[error['signature']] += 1
            error_occurrences.append(dict(error, call_id=call_id, execution_id=execution_id, line=line_number))
    seen_responses = seen_responses if seen_responses is not None else set()
    flags = {'research_tool_observed': False, 'delivery_tool_observed': False}
    with rollout_stream(path) as (stream, compressed):
        for line_number, raw in enumerate(iter(lambda: stream.readline(16 * 1024 * 1024 + 1), b''), 1):
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('native record exceeds bounded reader')
            if not raw.endswith(b'\n'):
                if compressed:
                    raise ValueError('incomplete compressed native record')
                break  # Do not consume an in-flight final native write.
            line = raw.decode('utf-8')
            try: event = json.loads(line)
            except ValueError:
                raise ValueError('complete native record is invalid JSON')
            p = event.get('payload', {}); kind = event.get('type')
            stamp = timestamp(event['timestamp']) if event.get('timestamp') else None
            if (kind == 'token_usage_record' and thread_id and p.get('thread_id')
                    and p['thread_id'] != thread_id and p.get('turn_id')):
                inherited_turns.add(p['turn_id'])
            if kind == 'turn_context' and p.get('turn_id'):
                active_turn = p['turn_id']
                turn(active_turn).update(root_turn_id=p.get('root_turn_id') or active_turn,
                                        model=p.get('model'), effort=p.get('effort'))
            if kind == 'event_msg' and p.get('type') == 'task_started' and p.get('turn_id'):
                active_turn = p['turn_id']
                turn(active_turn)['started_at'] = event.get('timestamp')
            if kind == 'event_msg' and p.get('turn_id'):
                if p.get('type') == 'task_complete':
                    turn(p['turn_id'])['completed_at'] = p.get('completed_at') or event.get('timestamp')
                if p.get('type') in ('turn_aborted', 'task_failed'):
                    turn(p['turn_id'])['failed_or_interrupted'] = True
            if stamp is not None and ((since is not None and stamp < since) or (until is not None and stamp >= until)):
                continue
            if kind == 'token_usage_record':
                identity = p.get('response_id')
                if thread_id and p.get('thread_id') and p['thread_id'] != thread_id: continue
                if identity and identity not in seen_responses:
                    measured = p.get('usage', {})
                    if any(key in measured and (type(measured[key]) is not int or measured[key] < 0) for key in FIELDS):
                        raise ValueError('invalid native token category')
                    if all(k in measured for k in FIELDS[:2]) and measured['cached_input_tokens'] > measured['input_tokens']:
                        raise ValueError('cached tokens exceed input')
                    usage[identity] = measured; seen_responses.add(identity)
                    if p.get('turn_id'):
                        native = turn(p['turn_id'])
                        native['root_turn_id'] = p.get('root_turn_id') or native['root_turn_id']
                        native['model_roundtrips'] += 1
                        for field in FIELDS:
                            value = p.get('usage', {}).get(field)
                            native[field] = native[field] + value if native[field] is not None and isinstance(value, int) else None
                    else:
                        unattributed['model_roundtrips'] += 1
                        for field in FIELDS:
                            value = measured.get(field)
                            unattributed[field] = unattributed[field] + value if unattributed[field] is not None and isinstance(value, int) else None
            elif kind == 'event_msg':
                if p.get('type') == 'task_started': started.add(p.get('turn_id'))
                if p.get('type') == 'task_complete':
                    completed.add(p.get('turn_id')); completed_at = p.get('completed_at') or event.get('timestamp')
                    if p.get('turn_id'): turn(p['turn_id'])['completed_at'] = completed_at
                if p.get('type') in ('turn_aborted','task_failed'): aborted.add(p.get('turn_id'))
                if p.get('type') in ('turn_aborted','task_failed') and p.get('turn_id'):
                    turn(p['turn_id'])['failed_or_interrupted'] = True
                item = p.get('item') or {}
                if p.get('type') == 'item_completed' and item.get('type') == 'CommandExecution':
                    # Native completion carries the actual exit code even when an
                    # orchestration tool forwards only bare stdout/stderr text.
                    record_errors({'exit_code': item.get('exit_code'),
                                   'isError': item.get('status') == 'failed',
                                   'output': item.get('aggregated_output') or item.get('stderr') or item.get('stdout', '')},
                                  active_call_id or item.get('id') or 'line:' + str(line_number),
                                  line_number, item.get('id'))
            elif kind == 'compacted': compactions += 1
            elif kind == 'response_item' and p.get('type') in ('function_call', 'custom_tool_call'):
                calls[p.get('call_id', p.get('id', str(len(calls))))] = p
                active_call_id = p.get('call_id') or p.get('id')
                name = p.get('name', ''); args = str(p.get('input', p.get('arguments', '')))
                flags['research_tool_observed'] |= 'spawn_agent' in name or 'research-independent' in args or 'research-orchestrated-independent' in args
                flags['delivery_tool_observed'] |= 'send_message_to_thread' in name or 'send_message_to_thread(' in args
                waits += name.split('.')[-1] in ('wait_agent','wait_threads','list_agents')
                if active_turn:
                    native = turn(active_turn); native['tool_calls'] += 1
                    native['research_tool_observed'] |= 'spawn_agent' in name or 'research-independent' in args
                    native['delivery_tool_observed'] |= 'send_message_to_thread' in name or 'send_message_to_thread(' in args
            elif kind == 'response_item' and p.get('type') in ('function_call_output','custom_tool_call_output'):
                call_id = p.get('call_id') or p.get('id') or 'line:' + str(line_number)
                record_errors(p.get('output', ''), call_id, line_number)
    complete = bool(completed) and not (started - completed) and not (aborted - completed)
    totals = {key: sum(v[key] for v in usage.values()) if usage and all(key in v for v in usage.values()) else None for key in FIELDS}
    return dict(totals, model_roundtrips=len(usage) if usage else None, tool_calls=len(calls),
                completed=complete, completed_at=completed_at, failed_or_interrupted=bool(aborted - completed),
                error_counts=dict(errors), error_occurrences=error_occurrences,
                repeated_errors=sum(max(0,n-1) for n in errors.values()),
                wait_calls=waits, compactions=compactions,
                unattributed_usage=unattributed if unattributed['model_roundtrips'] else None,
                native_turns=[v for v in turns.values() if v['turn_id'] not in inherited_turns and (v['model_roundtrips'] or
                    (v['started_at'] and (until is None or timestamp(v['started_at']) < until)
                     and (not v['completed_at'] or since is None or timestamp(v['completed_at']) >= since)))], **flags)


def summarize(rows):
    result = {'completed_runs': len(rows)}
    for key in ('model_roundtrips', 'tool_calls', 'elapsed_seconds', 'repeated_errors') + FIELDS:
        known = sorted(r[key] for r in rows if r.get(key) is not None)
        from math import ceil
        result[key] = {'known_runs': len(known), 'median': median(known) if known else None,
                       'p95': known[ceil(len(known) * .95) - 1] if known else None,
                       'sum': sum(known) if known else None}
    return result


class UsageSourceUnavailable(ValueError):
    """This adapter cannot measure usage; market analysis remains independent."""


def source_connection(path):
    path = Path(path).resolve(strict=True)
    uri = path.as_uri() + '?mode=ro'
    if not Path(str(path) + '-wal').exists():
        uri += '&immutable=1'
    return sqlite3.connect(uri, uri=True)


def usage_database(explicit=None, configured=None):
    """Respect an explicit source; discover only one schema-compatible candidate.

    Codex's internal filename is not an API. Never choose by suffix or mtime.
    All inspection is read-only, including a missing or incompatible source.
    """
    required = {'id', 'rollout_path', 'created_at', 'updated_at', 'model',
                'reasoning_effort', 'thread_source', 'name', 'source'}
    def inspect(path):
        if not path.is_file():
            raise UsageSourceUnavailable('usage database is unavailable: ' + str(path))
        try:
            with source_connection(path) as conn:
                columns = {row[1] for row in conn.execute('PRAGMA table_info(threads)')}
                missing = required - columns
                if missing:
                    raise UsageSourceUnavailable('usage database schema lacks: ' + ', '.join(sorted(missing)))
                edges = {row[1] for row in conn.execute('PRAGMA table_info(thread_spawn_edges)')}
                if edges and not {'parent_thread_id', 'child_thread_id'} <= edges:
                    raise UsageSourceUnavailable('usage database has incompatible thread_spawn_edges')
        except sqlite3.Error as exc:
            raise UsageSourceUnavailable('usage database cannot be read: ' + type(exc).__name__) from exc
        return path.resolve()
    if explicit:
        return inspect(Path(explicit).expanduser())
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return inspect(path)
        root = path.parent
    else:
        import os
        root = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    candidates = []
    for path in root.glob('state_*.sqlite'):
        try:
            candidates.append(inspect(path))
        except UsageSourceUnavailable:
            continue
    if len(candidates) != 1:
        raise UsageSourceUnavailable('usage source needs an explicit compatible database; found ' + str(len(candidates)))
    return candidates[0]


def native_threads(db):
    """Read identity/usage metadata only, never native message or report text."""
    with source_connection(db) as conn:
        conn.row_factory = sqlite3.Row
        threads = {v['id']:dict(v) for v in conn.execute(
            'SELECT id,rollout_path,created_at,updated_at,model,reasoning_effort,thread_source,name,source FROM threads')}
        tables = {v[0] for v in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        parents = dict((v[1],v[0]) for v in conn.execute('SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges')) if 'thread_spawn_edges' in tables else {}
        for identity, v in threads.items():
            try: parents[identity] = json.loads(v.get('source',''))['subagent']['thread_spawn']['parent_thread_id']
            except (TypeError,ValueError,KeyError): pass
    return threads, parents


def automation_roots(db, automation_id, before=None):
    """Use the original native registration, including failed and active runs.

    This is a read-only usage adapter, not a scheduler. Names, project IDs and
    thread creation time alone never establish membership in an automation.
    """
    if not db or not isinstance(automation_id, str) or not automation_id.strip():
        raise UsageSourceUnavailable('explicit automation database and automation_id are required')
    try:
        with source_connection(Path(db).expanduser()) as conn:
            if conn.execute('SELECT id FROM automations WHERE id=?', (automation_id,)).fetchone() is None:
                raise UsageSourceUnavailable('configured native automation registration is missing')
            rows = conn.execute('SELECT thread_id,created_at FROM automation_runs WHERE automation_id=?',
                                (automation_id,)).fetchall()
        roots = set()
        for identity, created in rows:
            if before is not None and timestamp(created) >= before:
                continue
            if not isinstance(identity, str) or not identity.strip():
                raise UsageSourceUnavailable('registered automation run has no native root identity')
            roots.add(identity)
        return roots
    except (OSError, sqlite3.Error, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, UsageSourceUnavailable):
            raise
        raise UsageSourceUnavailable('native automation usage registration unavailable: ' + type(exc).__name__) from exc


def selected_roots(threads, parents, root_threads, automation_db=None, automation_id=None, before=None):
    if root_threads is not None and not isinstance(root_threads, (list, tuple, set)):
        raise UsageSourceUnavailable('native roots must be a collection of explicit identities')
    roots = set(root_threads or [])
    if any(not isinstance(identity, str) or not identity.strip() for identity in roots):
        raise UsageSourceUnavailable('configured native root identity is empty')
    registered = automation_roots(automation_db, automation_id, before) if automation_db is not None else set()
    if any(identity in parents for identity in registered):
        raise UsageSourceUnavailable('registered automation root has a native parent; scope requires review')
    roots |= registered
    if not roots or not roots <= threads.keys():
        raise UsageSourceUnavailable('explicit current native roots are missing; no zero-usage assumption')
    return roots, registered


def current_native_scope(config, current_thread):
    """Prove this execution belongs to the configured metered native scope."""
    if not isinstance(current_thread, str) or not current_thread.strip():
        raise UsageSourceUnavailable('current native thread identity is empty; usage coverage unknown')
    db = usage_database(configured=config.get('codex_state_db'))
    threads, parents = native_threads(db)
    roots, registered = selected_roots(threads, parents, config.get('usage_root_threads'),
        config.get('codex_automations_db'), config.get('automation_id'))
    identity, seen = current_thread, set()
    if identity not in threads:
        raise UsageSourceUnavailable('current native thread metadata missing; usage coverage unknown')
    while identity not in roots and identity in parents and identity not in seen:
        seen.add(identity)
        identity = parents[identity]
        if identity not in threads:
            raise UsageSourceUnavailable('current native ancestor metadata missing; usage coverage unknown')
    if identity not in roots:
        raise UsageSourceUnavailable('current native thread is outside the registered usage scope')
    if config.get('codex_automations_db') is not None and identity not in registered:
        raise UsageSourceUnavailable('current native root is not registered to this automation; historical roots are audit-only')
    return {'thread_id': current_thread, 'root_thread_id': identity,
            'root_thread_ids': sorted(roots), 'automation_root_thread_ids': sorted(registered),
            'source_db': str(db), 'automation_id': config.get('automation_id')}


def audit(db, since, until, root_threads=None, *, automation_db=None, automation_id=None,
          historical_automation_ids=None):
    start, end = timestamp(since), timestamp(until)
    if end <= start: raise ValueError('until must follow since')
    db = usage_database(explicit=db)
    threads, parents = native_threads(db)
    history_ids = historical_automation_ids if historical_automation_ids is not None else []
    if (not isinstance(history_ids, list) or any(not isinstance(identity, str) or not identity.strip() for identity in history_ids)
            or len(set(history_ids)) != len(history_ids) or automation_id in history_ids):
        raise UsageSourceUnavailable('historical automation IDs must be distinct non-empty IDs, separate from current admission')
    history_scopes = []
    historical_roots = set()
    if root_threads is not None and not isinstance(root_threads, (list, tuple, set)):
        raise UsageSourceUnavailable('native roots must be a collection of explicit identities')
    for identity in history_ids:
        observed = automation_roots(automation_db, identity, end)
        historical_roots |= observed
        history_scopes.append({'automation_id':identity, 'root_thread_ids':sorted(observed),
                               'registration':'REGISTERED_NATIVE_RUNS' if observed else 'AWAITING_NATIVE_RUN'})
    if historical_roots:
        # Historical automation membership affects metering only. Admission
        # continues to resolve solely config.automation_id in current_native_scope.
        if any(identity in parents for identity in historical_roots):
            raise UsageSourceUnavailable('historical automation root has a native parent; scope requires review')
        root_threads = list(set(root_threads or []) | historical_roots)
    explicit_scope = root_threads is not None or automation_db is not None
    if explicit_scope:
        roots, registered = selected_roots(threads, parents, root_threads, automation_db, automation_id, end)
    else:
        registered = set()
        roots = {k for k,v in threads.items() if v.get('thread_source')=='automation' and v.get('name')=='BTC核心策略自动重评'
             and v['created_at'] < end and v.get('updated_at',v['created_at']) >= start}
    if not roots or not roots <= threads.keys():
        raise UsageSourceUnavailable('explicit current native roots are missing; no zero-usage assumption')
    def owner(identity):
        seen = set()
        while identity not in roots and identity in parents and identity not in seen:
            seen.add(identity); identity = parents[identity]
        return identity
    # An edge is evidence of scoped work even if its child's metadata has gone
    # missing. Walk the graph independently of existing rows, including through
    # a missing intermediate parent; absence cannot certify a zero-cost child.
    missing_metadata = sorted(k for k in parents if k not in threads and owner(k) in roots)
    selected = sorted((v for k,v in threads.items() if owner(k) in roots and v['created_at'] < end),key=lambda v:v['created_at'])
    rows = []; costs = []; seen_responses = set(); unread = list(missing_metadata)
    excluded = [{'thread_id':k, 'reason':'scoped descendant metadata missing; usage unknown'} for k in missing_metadata]
    for item in selected:
        thread_id,path,created,model,effort = (item.get(k) for k in ('id','rollout_path','created_at','model','reasoning_effort'))
        try: value = rollout(path,start,end,seen_responses,thread_id)
        except (OSError, ValueError) as exc:
            unread.append(thread_id)
            excluded.append({'thread_id':thread_id,'reason':type(exc).__name__}); continue
        value.update(thread_id=thread_id,rollout_path=path,started_at=datetime.fromtimestamp(created,timezone.utc).isoformat(),model=model,effort=effort)
        value.update(root_thread_id=owner(thread_id),role='main' if thread_id in roots else 'subagent')
        value['fully_completed_in_window'] = bool(value['completed'] and value['completed_at'] and start <= created < end and timestamp(value['completed_at']) < end)
        value['elapsed_seconds'] = max(0, timestamp(value['completed_at']) - created) if value['completed'] and value['completed_at'] else None
        costs.append(value)
    for value in costs:
        if value['role']=='subagent': continue
        family = [v for v in costs if v['root_thread_id']==value['thread_id']]
        if not all(v['fully_completed_in_window'] for v in family):
            thread_id = value['thread_id']
            excluded.append({'thread_id':thread_id,'reason':'not fully completed within window; usage retained in thread_costs'}); continue
        run = dict(value, counted_thread_ids=[v['thread_id'] for v in family])
        # Wall time is the family span, never the sum of overlapping agents.
        run['elapsed_seconds'] = max(timestamp(v['completed_at']) for v in family) - min(timestamp(v['started_at']) for v in family)
        run['repeated_errors'] = sum(v['repeated_errors'] for v in family)
        for key in ('model_roundtrips','tool_calls') + FIELDS:
            run[key] = sum(v[key] for v in family) if all(v[key] is not None for v in family) else None
        run['stratum'] = ('research' if len(family)>1 or any(v['research_tool_observed'] for v in family)
                          else 'delivery' if value['delivery_tool_observed'] else 'ordinary')
        rows.append(run)
    completed_turns = set(); observed_triggers = set(); attribution_gaps = []
    if explicit_scope:
        # Neither a long-lived heartbeat thread nor a fresh cron root is a
        # trigger count. Native root_turn_id supplies the exact family binding.
        families = {}
        for cost in costs:
            if cost['unattributed_usage']:
                attribution_gaps.append(cost['thread_id'])
            for value in cost['native_turns']:
                value = dict(value, thread_id=cost['thread_id'])
                families.setdefault((cost['root_thread_id'], value['root_turn_id']), []).append(value)
                if cost['role'] == 'main' and value['turn_id'] == value['root_turn_id']:
                    observed_triggers.add((cost['thread_id'], value['root_turn_id']))
        rows = []
        for (root_thread, root_turn), family in families.items():
            main = next((v for v in family if v['thread_id'] == root_thread and v['turn_id'] == root_turn), None)
            full = main and all(v['started_at'] and v['completed_at'] and not v['failed_or_interrupted']
                                and v['model_roundtrips'] and all(v[key] is not None for key in FIELDS)
                                and start <= timestamp(v['started_at']) < timestamp(v['completed_at']) < end for v in family)
            if not full:
                excluded.append({'root_turn_id': root_turn, 'reason': 'native turn family incomplete or partially outside window; costs retained'})
                continue
            row = dict(main, root_thread_id=root_thread, root_turn_id=root_turn, counted_thread_ids=sorted({v['thread_id'] for v in family}),
                       elapsed_seconds=max(timestamp(v['completed_at']) for v in family)-min(timestamp(v['started_at']) for v in family),
                       repeated_errors=None)
            for field in ('model_roundtrips', 'tool_calls') + FIELDS:
                row[field] = sum(v[field] for v in family) if all(v[field] is not None for v in family) else None
            row['stratum'] = ('research' if len(family)>1 or any(v['research_tool_observed'] for v in family)
                              else 'delivery' if main['delivery_tool_observed'] else 'ordinary')
            rows.append(row)
            completed_turns.update((v['thread_id'], v['turn_id']) for v in family)
    if unread or attribution_gaps:
        excluded.append({'reason': 'unread or unattributed scoped usage prevents certifying complete run families'})
        rows = []
        completed_turns.clear()
    incomplete_costs = ([v for cost in costs for v in cost['native_turns']
                         if (cost['thread_id'], v['turn_id']) not in completed_turns]
                        if explicit_scope else [r for r in costs if unread or not r['fully_completed_in_window']])
    if explicit_scope:
        incomplete_costs.extend(cost['unattributed_usage'] for cost in costs if cost['unattributed_usage'])
    groups = {}
    for row in rows: groups.setdefault('/'.join((row['model'] or 'UNKNOWN',row['effort'] or 'UNKNOWN',row['stratum'])),[]).append(row)
    errors = Counter()
    for row in costs: errors.update(row['error_counts'])
    return {'schema':'BTC_RUN_USAGE_AUDIT_V1','since':since,'until':until,
        'source':{'status':'AVAILABLE','adapter':'codex_internal_sqlite','path':str(db)},
        'scope':{'root_thread_ids':sorted(roots), 'automation_root_thread_ids':sorted(registered),
                 'automation_id':automation_id if automation_db is not None else None,
                 'historical_automation_scopes':history_scopes,
                 'automation_database':str(automation_db) if automation_db is not None else None,
                 'automation_registration':('REGISTERED_NATIVE_RUNS' if registered else 'AWAITING_NATIVE_RUN')
                                            if automation_db is not None else 'NOT_CONFIGURED'},
        'coverage':{'status':'INCOMPLETE' if unread or attribution_gaps else 'COMPLETE_LOCAL_SCOPE','unread_thread_ids':unread,
                    'missing_metadata_thread_ids':missing_metadata,
                    'unattributed_usage_thread_ids':attribution_gaps},
        'trigger_counts':({'observed_native_root_turns':len(observed_triggers), 'completed':len(rows),
                           'incomplete_or_failed':len(observed_triggers)-len(rows),
                           'identity_coverage':'INCOMPLETE' if unread or attribution_gaps else 'OBSERVED_NATIVE_TURNS'}
                          if explicit_scope else None),
        'measurement':'Distinct model response usage records and outer model tool calls. Cached input is included in input; no quota conversion.',
        'stratification':'Native root-turn families for explicit or registered roots; legacy thread families otherwise. Descendants included. Conservative tool-trace flags, not a quality score.',
        'summary':summarize(rows),'groups':{key:summarize(value) for key,value in groups.items()},'runs':rows,'excluded':excluded,
        'cost_scope':'Window-filtered main and descendant usage; failed/incomplete work retained, never called successful.',
        'thread_costs':costs,'cost_totals':{scope:{k:v for k,v in summarize(items).items() if k!='completed_runs'}
            for scope,items in {'all':costs,'main':[r for r in costs if r['role']=='main'],
                'subagent':[r for r in costs if r['role']=='subagent'],
                'incomplete_or_failed':incomplete_costs}.items()},
        'recurrence_signals':{'errors':dict(errors),'repeated_errors':sum(max(0,n-1) for n in errors.values()),
                              'wait_calls':sum(r['wait_calls'] for r in costs),'compactions':sum(r['compactions'] for r in costs)}}


def _all_known(values):
    if any(v is False for v in values):
        return False
    return True if values and all(v is True for v in values) else None


def absolute_target(current):
    groups = {}
    for key, value in current.get('groups', {}).items():
        if not key.endswith('/ordinary'):
            continue
        metric = value['model_roundtrips']
        median_value = metric['median']
        complete = metric.get('known_runs', value.get('completed_runs')) == value.get('completed_runs')
        groups[key] = {'median': median_value, 'target_median_at_most': ORDINARY_MEDIAN_TARGET,
                       'met': median_value <= ORDINARY_MEDIAN_TARGET if median_value is not None and complete else None}
    return {'groups': groups, 'met': _all_known([g['met'] for g in groups.values()])}


def compare(current, baseline):
    result = {}
    for key, value in current['groups'].items():
        if not key.endswith('/ordinary'): continue
        old=baseline.get('groups',{}).get(key,{}).get('model_roundtrips',{}).get('median')
        new=value['model_roundtrips']['median']
        relative_met = new <= old * 0.5 if old and new is not None else None
        absolute_met = absolute_target({'groups': {key: value}})['met']
        result[key]={'before_median':old,'after_median':new,'reduction_fraction':(1-new/old) if old and new is not None else None,
                     'comparison_status':'COMPARABLE' if old and new is not None else 'NO_COMPARABLE_BASELINE_OR_MEASUREMENT',
                     'target_at_least':0.5,'target_median_at_most':min(ORDINARY_MEDIAN_TARGET,old*0.5) if old else ORDINARY_MEDIAN_TARGET,
                     'relative_target_met':relative_met, 'absolute_target_met':absolute_met,
                     'usage_target_met':_all_known([relative_met, absolute_met]),
                     'natural_quality_and_daily_delivery_acceptance':'SEPARATE_REQUIRED'}
    return result


def record_defect_check(path, value, output):
    """Record measured evidence only; no usage number can close a quality defect."""
    path = Path(path); defect = json.loads(path.read_text())
    checks = defect.setdefault('natural_acceptance', {}).setdefault('usage_checks', {})
    comparison = value.get('comparison') or {}
    results = [v['usage_target_met'] for v in comparison.values()]
    target = value.get('target_check') or absolute_target(value)
    signals = value['recurrence_signals']
    checks[value['until']] = {'since':value['since'],'until':value['until'],'usage_file':str(Path(output).resolve()),
        'comparison':comparison,'target_check':target,
        'usage_target_met':_all_known([target['met'], _all_known(results)]),
        'repeated_errors':signals['repeated_errors'],'wait_calls':signals['wait_calls'],
        'incomplete_or_failed':value['cost_totals']['incomplete_or_failed'],
        'quality_and_delivery':'SEPARATE_EVIDENCE_REQUIRED'}
    current = checks[value['until']]
    if current['usage_target_met'] is False or signals['repeated_errors']:
        defect['status'] = 'OPEN_RECURRENCE_OR_USAGE_TARGET_FAILED'
    elif current['usage_target_met'] is True and defect.get('status','').startswith('OPEN') and defect['status'] != 'OPEN_RECURRENCE_OR_USAGE_TARGET_FAILED':
        defect['status'] = 'OPEN_PENDING_NATURAL_QUALITY_AND_DELIVERY'
    # Retain all historical failures and require actual separate acceptance.
    defect['natural_acceptance']['last_usage_checked_until'] = value['until']
    path.write_text(json.dumps(defect,ensure_ascii=False,indent=2)+'\n')
    return {'defect_status':defect['status'],'usage_target_met':current['usage_target_met'],
            'repeated_errors':signals['repeated_errors']}


def daily_status(path, at=None):
    """Read the preceding JST day's obligations without loading history into the model."""
    defect = json.loads(Path(path).read_text())
    acceptance = defect.get('natural_acceptance', {})
    jst = timezone(timedelta(hours=9))
    current = datetime.fromtimestamp(timestamp(at), jst) if at else datetime.now(jst)
    until = current.replace(hour=0, minute=0, second=0, microsecond=0)
    since = until - timedelta(days=1)
    day = since.date().isoformat()
    reviewed = any((v.get('day_jst') if isinstance(v, dict) else v) == day
                   for v in acceptance.get('reviewed_days', []))
    measured = any(timestamp(k) == until.timestamp() for k in acceptance.get('usage_checks', {}))
    return {'day_jst': day, 'since': since.isoformat(), 'until': until.isoformat(),
            'usage_due': not measured, 'quality_and_delivery_review_due': not reviewed,
            'defect_status': defect.get('status'), 'target_median_at_most': ORDINARY_MEDIAN_TARGET,
            'current_change_acceptance': acceptance.get('requirements_first_change'),
            'usage_output': str(Path(path).parent / ('usage-' + day + '.json'))}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',help='Explicit read-only Codex usage database; no filename/version assumption');p.add_argument('--since');p.add_argument('--until')
    p.add_argument('--output');p.add_argument('--baseline')
    p.add_argument('--root-thread', action='append', help='Explicit audit scope override; otherwise use configured historical and registered automation roots')
    p.add_argument('--status',action='store_true',help='Read only: preceding JST day and pending acceptance, uses explicit or configured defect')
    p.add_argument('--defect',help='Update the existing local defect with usage evidence; never closes it.')
    a=p.parse_args()
    if a.status:
        if not a.defect:
            configured = __import__('node_config').load()
            a.defect = configured.get('usage_defect')
        if not a.defect or not Path(a.defect).is_file():
            print(json.dumps({'status':'DEFECT_REFERENCE_UNAVAILABLE','usage_audit_available':True,
                  'next':'Use --since/--until/--output and configured usage_root_threads; legacy defect absence does not block metering.'}));return
        print(json.dumps(daily_status(a.defect),ensure_ascii=False));return
    if not all((a.since,a.until,a.output)):p.error('audit requires --since, --until and --output')
    try:
        configured = __import__('node_config').load()
        database = usage_database(a.db, configured.get('codex_state_db'))
        value=audit(database,a.since,a.until,a.root_thread if a.root_thread is not None else configured.get('usage_root_threads'),
                    automation_db=None if a.root_thread is not None else configured.get('codex_automations_db'),
                    automation_id=configured.get('automation_id'),
                    historical_automation_ids=[] if a.root_thread is not None else configured.get('usage_history_automation_ids'))
    except (UsageSourceUnavailable, sqlite3.Error, OSError) as exc:
        value={'schema':'BTC_RUN_USAGE_AUDIT_V1','since':a.since,'until':a.until,
               'source':{'status':'UNAVAILABLE','adapter':'codex_internal_sqlite','reason':str(exc)},
               'summary':None,'target_check':{'met':None},'market_analysis_blocked':False}
        dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(value,ensure_ascii=False))
        return 2
    value['target_check']=absolute_target(value)
    if a.baseline:value['comparison']=compare(value,json.loads(Path(a.baseline).read_text()))
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    defect = record_defect_check(a.defect,value,dest) if a.defect else None
    print(json.dumps({'output':str(dest.resolve()),'summary':value['summary'],'groups':value['groups'],'target_check':value['target_check'],'comparison':value.get('comparison'),'excluded':len(value['excluded']),'defect':defect},ensure_ascii=False))

if __name__=='__main__':raise SystemExit(main())
