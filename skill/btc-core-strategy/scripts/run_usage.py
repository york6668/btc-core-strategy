#!/usr/bin/env python3
"""Read runtime usage and optionally update its existing defect; never estimate quota charges."""
import argparse
from datetime import datetime, timezone, timedelta
import json
import re
from pathlib import Path
import sqlite3
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


def rollout(path, since=None, until=None, seen_responses=None, thread_id=None):
    usage = {}; calls = {}; started = set(); completed = set(); completed_at = None
    errors = Counter(); aborted = set(); waits = 0; compactions = 0
    error_occurrences = []; seen_errors = set()
    active_call_id = None
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
    with open(path) as stream:
        for line_number, line in enumerate(stream, 1):
            try: event = json.loads(line)
            except ValueError: continue  # A running file can end in a partial write.
            p = event.get('payload', {}); kind = event.get('type')
            stamp = timestamp(event['timestamp']) if event.get('timestamp') else None
            if stamp is not None and ((since is not None and stamp < since) or (until is not None and stamp >= until)):
                continue
            if kind == 'token_usage_record':
                identity = p.get('response_id')
                if thread_id and p.get('thread_id') and p['thread_id'] != thread_id: continue
                if identity and identity not in seen_responses:
                    usage[identity] = p.get('usage', {}); seen_responses.add(identity)
            elif kind == 'event_msg':
                if p.get('type') == 'task_started': started.add(p.get('turn_id'))
                if p.get('type') == 'task_complete':
                    completed.add(p.get('turn_id')); completed_at = p.get('completed_at') or event.get('timestamp')
                if p.get('type') in ('turn_aborted','task_failed'): aborted.add(p.get('turn_id'))
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
            elif kind == 'response_item' and p.get('type') in ('function_call_output','custom_tool_call_output'):
                call_id = p.get('call_id') or p.get('id') or 'line:' + str(line_number)
                record_errors(p.get('output', ''), call_id, line_number)
    complete = bool(completed) and not (started - completed) and not (aborted - completed)
    totals = {key: sum(v[key] for v in usage.values()) if usage and all(key in v for v in usage.values()) else None for key in FIELDS}
    return dict(totals, model_roundtrips=len(usage) if usage else None, tool_calls=len(calls),
                completed=complete, completed_at=completed_at, failed_or_interrupted=bool(aborted - completed),
                error_counts=dict(errors), error_occurrences=error_occurrences,
                repeated_errors=sum(max(0,n-1) for n in errors.values()),
                wait_calls=waits, compactions=compactions, **flags)


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
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
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


def audit(db, since, until):
    start, end = timestamp(since), timestamp(until)
    if end <= start: raise ValueError('until must follow since')
    db = usage_database(explicit=db)
    uri = db.as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        threads = {v['id']:dict(v) for v in conn.execute('SELECT * FROM threads')}
        tables = {v[0] for v in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        parents = dict((v[1],v[0]) for v in conn.execute('SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges')) if 'thread_spawn_edges' in tables else {}
        for identity, v in threads.items():
            try: parents[identity] = json.loads(v.get('source',''))['subagent']['thread_spawn']['parent_thread_id']
            except (TypeError,ValueError,KeyError): pass
    roots = {k for k,v in threads.items() if v.get('thread_source')=='automation' and v.get('name')=='BTC核心策略自动重评'
             and v['created_at'] < end and v.get('updated_at',v['created_at']) >= start}
    def owner(identity):
        seen = set()
        while identity in parents and identity not in seen:
            seen.add(identity); identity = parents[identity]
        return identity
    selected = sorted((v for k,v in threads.items() if owner(k) in roots and v['created_at'] < end),key=lambda v:v['created_at'])
    rows = []; excluded = []; costs = []; seen_responses = set()
    for item in selected:
        thread_id,path,created,model,effort = (item.get(k) for k in ('id','rollout_path','created_at','model','reasoning_effort'))
        try: value = rollout(path,start,end,seen_responses,thread_id)
        except (OSError, ValueError) as exc:
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
    groups = {}
    for row in rows: groups.setdefault('/'.join((row['model'] or 'UNKNOWN',row['effort'] or 'UNKNOWN',row['stratum'])),[]).append(row)
    errors = Counter()
    for row in costs: errors.update(row['error_counts'])
    return {'schema':'BTC_RUN_USAGE_AUDIT_V1','since':since,'until':until,
        'source':{'status':'AVAILABLE','adapter':'codex_internal_sqlite','path':str(db)},
        'measurement':'Distinct model response usage records and outer model tool calls. Cached input is included in input; no quota conversion.',
        'stratification':'Completed run totals include descendants; any descendant classifies the run as research. Conservative tool-trace flags, not a quality score.',
        'summary':summarize(rows),'groups':{key:summarize(value) for key,value in groups.items()},'runs':rows,'excluded':excluded,
        'cost_scope':'Window-filtered main and descendant usage; failed/incomplete work retained, never called successful.',
        'thread_costs':costs,'cost_totals':{scope:{k:v for k,v in summarize(items).items() if k!='completed_runs'}
            for scope,items in {'all':costs,'main':[r for r in costs if r['role']=='main'],
                'subagent':[r for r in costs if r['role']=='subagent'],
                'incomplete_or_failed':[r for r in costs if not r['fully_completed_in_window']]}.items()},
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
    p.add_argument('--status',action='store_true',help='Read only: preceding JST day and pending acceptance, requires --defect')
    p.add_argument('--defect',help='Update the existing local defect with usage evidence; never closes it.')
    a=p.parse_args()
    if a.status:
        if not a.defect:p.error('--status requires --defect')
        print(json.dumps(daily_status(a.defect),ensure_ascii=False));return
    if not all((a.since,a.until,a.output)):p.error('audit requires --since, --until and --output')
    try:
        configured = __import__('node_config').load().get('codex_state_db')
        database = usage_database(a.db, configured)
        value=audit(database,a.since,a.until)
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
