"""Evidence-bound acceptance windows; elapsed time never implies acceptance."""
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .codec import Conflict, encode, millis, stamp, utc
from .entities import Entities


JST = ZoneInfo('Asia/Tokyo')
CRITERIA = ('prices_history', 'regions_retests', 'forward_outcomes', 'personal_allocation',
            'research_corrections', 'cli_mcp_runtime', 'migration_recovery',
            'retention_capacity', 'report_delivery')


def start(db, *, release):
    if (db.metadata('writer') or {}).get('owner') != 'DOCKER':
        raise Conflict('natural acceptance begins only after actual writer cutover')
    prior = db.metadata('acceptance')
    if prior:
        return prior
    first = utc().astimezone(JST).date() + timedelta(days=1)
    value = {'cutover_ms': (db.metadata('writer') or {})['changed_ms'], 'release': release,
             'first_full_day': first.isoformat(), 'required_days': 7,
             'days': {}, 'defects': [], 'criteria': list(CRITERIA)}
    db.metadata('acceptance', value, write=True)
    return value


def window(db, *, day):
    date = datetime.strptime(day, '%Y-%m-%d').date()
    begin = datetime.combine(date, time(), JST)
    end = begin + timedelta(days=1)
    if end > utc():
        raise ValueError('acceptance reads a complete Japanese calendar day')
    lo, hi = millis(begin), millis(end)
    operations = []
    for row in db.rows('SELECT * FROM operations WHERE started_ms>=%s AND started_ms<%s ORDER BY started_ms', (lo, hi)):
        try: request = db.value(row['request_digest'])
        except FileNotFoundError: request = {'historical_request': 'NOT_ARCHIVED'}
        result = db.value(row['result_digest']) if row['result_digest'] else None
        operations.append({**row, 'request': request, 'result': result})
    changes = db.rows('SELECT e.object_id,e.kind,e.scope,v.version,v.known_ms,v.observed_ms,v.recorded_ms,v.change_reason '
                     'FROM entity_versions v JOIN entities e USING(entity_key) WHERE v.recorded_ms>=%s AND v.recorded_ms<%s '
                     'ORDER BY v.recorded_ms', (lo, hi))
    unresolved = db.rows('SELECT r.*,e.kind,e.object_id,e.scope FROM review_requirements r JOIN entities e USING(entity_key) '
                        'WHERE r.opened_ms<%s AND (r.resolved_ms IS NULL OR r.resolved_ms>=%s)', (hi, hi))
    historical_reviews = [row for row in unresolved if row['kind'] == 'REVIEW']
    unresolved = [row for row in unresolved if row['kind'] != 'REVIEW']
    return {'day': day, 'timezone': 'Asia/Tokyo', 'start': stamp(begin), 'end_exclusive': stamp(end),
            'operations': operations, 'changes': changes, 'unresolved_at_end': unresolved,
            'historical_review_requirements': historical_reviews,
            'capacity': next((r for r in (db.metadata('capacity.daily') or []) if r['day'] == day), None),
            'pending_now': db.rows("SELECT operation_id,status FROM operations WHERE status<>'COMMITTED'"),
            'meaning': 'Read inputs, state changes and actual consumed results. Counts alone are not acceptance.'}


def evidence(db, refs):
    if not isinstance(refs, list) or not refs:
        raise ValueError('acceptance needs actual stored evidence')
    result = []
    for ref in refs:
        kind = ref['type']
        if kind == 'ENTITY':
            item = Entities(db).get(ref['kind'], ref['object_id'], version=ref['version'])
            if not item: raise ValueError('acceptance references a missing object version')
            result.append({**ref, 'digest': item['digest']})
        elif kind == 'OPERATION':
            item = db.one('SELECT status,result_digest FROM operations WHERE operation_id=%s', (ref['operation_id'],))
            if not item or item['status'] != 'COMMITTED': raise ValueError('operation result has not been reconciled')
            result.append({**ref, 'digest': item['result_digest']})
        elif kind == 'BLOB':
            db.blobs.get(ref['digest']); result.append(ref)
        elif kind == 'FILE':
            path = Path(ref['path']).resolve(strict=True)
            roots = [Path(db.settings['project']).resolve(), *[Path(p).resolve() for p in db.settings.get('capacity_paths', [])]]
            if not any(path.is_relative_to(root) for root in roots): raise ValueError('acceptance file is outside this node')
            result.append({**ref, 'digest': db.put_blob(path.read_bytes())})
        else:
            raise ValueError('unknown acceptance evidence type')
    return result


def record(db, *, day, checks):
    state = db.metadata('acceptance')
    if not state: raise Conflict('acceptance has not started')
    snapshot = window(db, day=day)
    if day < state['first_full_day']:
        raise ValueError('partial cutover day is not a full observation day')
    if set(checks) != set(CRITERIA):
        raise ValueError('every planned acceptance criterion needs an explicit result')
    reviewed = {}
    for name, check in checks.items():
        if check.get('status') not in {'PASS', 'FAIL', 'REPLAY_ONLY'}:
            raise ValueError('explicit pass, failure, or replay-only coverage required')
        for field in ('actual_input', 'state_change', 'consumer_result'):
            if not isinstance(check.get(field), str) or not check[field].strip():
                raise ValueError('acceptance must explain actual input, state change and consumed result')
        reviewed[name] = {**check, 'evidence': evidence(db, check['evidence'])}
        if check['status'] == 'REPLAY_ONLY' and not check.get('natural_gap'):
            raise ValueError('replay-only acceptance must name missing natural coverage')
    observed = [r for r in snapshot['operations'] if 'run-advance' in r['request'].get('argv', [])
                and 'observe' in r['request'].get('argv', []) and (r['result'] or {}).get('returncode') == 0]
    if reviewed['cli_mcp_runtime']['status'] != 'FAIL' and not observed:
        raise Conflict('no completed natural analysis observation occurred in this window; replay cannot replace normal operation')
    state['days'][day] = {'reviewed_at': stamp(), 'checks': reviewed,
                          'snapshot_digest': db.put_blob(encode(snapshot)), 'valid': True}
    db.metadata('acceptance', state, write=True)
    return status(db)


def defect(db, *, reason, affected_days, criteria):
    state = db.metadata('acceptance')
    if not state or not reason.strip() or not set(criteria) <= set(CRITERIA):
        raise ValueError('describe the defect and its affected criteria/windows')
    for day in affected_days:
        if day in state['days']: state['days'][day]['valid'] = False
    state['defects'].append({'reason': reason, 'affected_days': affected_days,
                             'criteria': criteria, 'recorded_at': stamp()})
    db.metadata('acceptance', state, write=True)
    return status(db)


def repair(db, *, defect_index, release, reason, evidence_refs):
    """A verified critical repair starts a new full window, retaining history."""
    state=db.metadata('acceptance')
    if not state or not isinstance(defect_index,int) or not 0<=defect_index<len(state['defects']):
        raise ValueError('repair must reference a recorded defect')
    item=state['defects'][defect_index]
    if item.get('repair'):
        raise Conflict('defect already repaired; do not move the observation window again')
    if not reason.strip() or not release.strip():raise ValueError('describe the actual verified repair and release')
    proof=evidence(db,evidence_refs)
    state.setdefault('prior_windows',[]).append({'first_full_day':state['first_full_day'],
        'required_days':state['required_days'],'release':state['release'],'replaced_at':stamp(),
        'defect_index':defect_index})
    item['repair']={'release':release,'reason':reason,'evidence':proof,'verified_at':stamp()}
    state['first_full_day']=(utc().astimezone(JST).date()+timedelta(days=1)).isoformat()
    state['release']=release
    db.metadata('acceptance',state,write=True)
    return status(db)


def status(db):
    state = db.metadata('acceptance')
    if not state: return {'status': 'NOT_STARTED', 'scope': 'LOCAL_NATURAL_ACCEPTANCE'}
    first = datetime.strptime(state['first_full_day'], '%Y-%m-%d').date()
    required = [(first+timedelta(days=i)).isoformat() for i in range(state['required_days'])]
    missing, failed, replay = [], [], {}
    for day in required:
        review = state['days'].get(day)
        if not review or not review['valid']:
            missing.append(day); continue
        for name, check in review['checks'].items():
            if check['status'] == 'FAIL': failed.append({'day':day,'criterion':name})
            elif check['status'] == 'REPLAY_ONLY': replay.setdefault(day,[]).append(name)
    open_defects=[i for i,item in enumerate(state.get('defects',[])) if not item.get('repair')]
    return {'status': 'NEEDS_REPAIR' if failed or open_defects else 'FOLLOWING' if missing else 'NATURAL_WINDOW_REVIEWED',
            'scope': 'LOCAL_NATURAL_ACCEPTANCE',
            'required_days': required, 'missing_or_invalid': missing, 'failed': failed,
            'replay_only': replay, 'release': state['release'],
            'open_defects':open_defects,'prior_windows':state.get('prior_windows',[]),
            'overall_completion': 'This result covers local natural acceptance only. Source publication, node migration and cloud deployment have separate evidence and status.'}
