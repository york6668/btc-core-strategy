"""Report obligations and JST daily feedback, derived from immutable evidence."""
from pathlib import Path
from datetime import timedelta,timezone
from copy import deepcopy
from .common import read,require,text,exact,instant,now,pointer
JST=timezone(timedelta(hours=9))
AUTHORING = {
    'significance': 'Maintain the original cycle argument and compare decisive new evidence, mechanism, counterevidence and current participation value with the actual last display. material means an unreported substantive change in this meaning, an important factual correction or actual portfolio danger. Internal REVISE, expiry, data freshness or a short price/risk change does not by itself require notification. Explain why a move is noise or why it weakens/replaces the thesis; do not lock the old thesis or its recognition conditions.',
    'reason': 'maintenance.triggers[i].reason must explain which existing judgment the condition would change and why. At least 12 stripped characters is only an input floor, not proof of meaningful analysis; do not pad or repeat text.',
    'continuity': 'Retain unchanged trigger explanations; checked still requires fresh actual evidence for every prior condition. UNKNOWN is not NOT_MET.',
    'timing': 'Read report_care.execution_window. Select a meaningful review deadline no earlier than the verified next execution plus observed execution allowance. This feasibility floor is not an automatic new deadline or an extension of research/advice validity. UNAVAILABLE/NOT_OBSERVED is diagnostic only: do not claim an automatic execution or completion guarantee. A justified business review deadline is still required, and missing market observations, approvals or provenance are not supplied by timing metadata. Preserve missed deadlines and assess their actual impact.'}


def timing_evidence(ctx, reviewed_at):
    care = ctx.get('report_care') or {}
    if not care.get('enabled'):
        return None
    prior = (care.get('active_maintenance') or {}).get('recheck_at')
    return {'prior_review_id': care.get('prior_review_id'), 'prior_recheck_at': prior,
            'reviewed_at': reviewed_at,
            'late_seconds': max(0, (instant(reviewed_at) - instant(prior)).total_seconds()) if prior else None,
            'execution_window': deepcopy(care.get('execution_window'))}

def day(stamp):return instant(stamp).astimezone(JST).date().isoformat()
def local(stamp):
    t=instant(stamp).astimezone(JST)
    return str(t.month)+'月'+str(t.day)+'日 '+t.strftime('%H:%M')
def enabled(root):
    p=Path(root)/'write-policy.json'
    return p.exists() and read(p).get('report_care_revision')==1

def latest(root):
    from . import reporting
    p=Path(root)/'report-review-current.json'
    if not p.exists():return None
    value=reporting.review(root,read(p)['review_id'])
    if 'maintenance' not in value:return None
    displayed=Path(root)/'last-display-current.json'
    if displayed.exists():
        identity=read(displayed)['delivery_id']
        artifact=read(Path(root)/'outbox'/identity/'artifact.json')
        if (value['context']['reported'].get('display_delivery_id')!=identity and
                artifact.get('source',{}).get('report_review',{}).get('review_id')!=value['review_id']):
            delivery_status=read(Path(root)/'outbox'/identity/'status.json')
            receipt=read(Path(root)/'outbox'/identity/delivery_status['receipt'])
            completed=receipt.get('assistant',{}).get('completed_at')
            # Late confirmation of an old display must not erase newer checks,
            # unresolved issues, or the current day's unfulfilled deadline.
            if completed and instant(value['created_at'])>=instant(completed):
                return value
            value=artifact.get('source',{}).get('report_review',{})
            return value if 'maintenance' in value else None
    return value

def deliveries(root):
    """Only an accepted actual assistant reply can satisfy its own assessment day."""
    from . import delivery
    result={}
    for path in (Path(root)/'outbox').glob('*/artifact.json'):
        a=read(path);v=a.get('source',{}).get('report_review',{});m=v.get('maintenance')
        if not m or m['kind']=='EXPRESSION_ONLY':continue
        s=read(path.parent/'status.json')
        if s['state']!='DELIVERED':continue
        _,_,receipt=delivery.validate_delivered(root,path.parent.name)
        stamp=receipt['assistant'].get('completed_at')
        if not stamp:continue
        assessed=day(v['created_at'])
        if assessed!=day(stamp) or instant(stamp)>=instant(m['recheck_at']):continue
        result.setdefault(assessed,[]).append({'delivery_id':path.parent.name,'assistant_ref':receipt['assistant']['record_ref'],'completed_at':stamp,'kind':m['kind']})
    return result

def status(root,stamp=None,run_ref=None):
    stamp=stamp or now();today=day(stamp)
    if not enabled(root):return {'enabled':False,'review_required':False,'daily_due':False,'day':today}
    verified=deliveries(root);previous=latest(root);m=previous['maintenance'] if previous else None
    plan=m['daily'] if m and m['daily']['day']==today else None
    fulfilled=bool(verified.get(today));deadline=plan['report_by'] if plan else None
    due=not fulfilled and deadline is not None and instant(stamp)>=instant(deadline)
    policy=read(Path(root)/'write-policy.json');start=instant(policy['report_care_activated_at']).astimezone(JST).date();end=instant(stamp).astimezone(JST).date()
    gaps=[];d=start
    while d<end:
        if d.isoformat() not in verified:gaps.append(d.isoformat())
        d+=timedelta(days=1)
    recheck=bool(m and instant(stamp)>=instant(m['recheck_at']))
    events=bool(m and m['triggers'] and (run_ref is None or previous['provenance']['run_ref']!=run_ref))
    # A new day after an unfulfilled day requires catch-up even before a new plan.
    catchup=bool(gaps and gaps[-1]==(end-timedelta(days=1)).isoformat() and not fulfilled)
    import runtime_schedule
    return {'enabled':True,'day':today,'evaluated_at':stamp,'fulfilled':fulfilled,'deliveries':verified.get(today,[]),
            'execution_window':runtime_schedule.current(run_ref),
            'late_seconds':max(0,(instant(stamp)-instant(m['recheck_at'])).total_seconds()) if m else None,
            'daily_due':due or catchup,'daily_plan_required':not fulfilled and plan is None,'report_by':deadline,
            'missed_days':gaps[-366:],'missed_day_count':len(gaps),'prior_review_id':previous['review_id'] if previous else None,
            'active_maintenance':m,'time_due':recheck,'event_check_required':events,
            'review_required':m is None or recheck or due or catchup or (not fulfilled and plan is None) or (events and run_ref is not None)}

def validate(root,spec,ctx):
    required=enabled(root)
    if not required and 'maintenance' not in spec:return
    require('maintenance' in spec,'report analysis and follow-up obligations required')
    scoped = ctx.get('market_revision', 0) >= 6
    independent_notification = scoped and ctx.get('report_contract_revision', 1) >= 2
    m=spec['maintenance'];exact(m,'kind thesis support unresolved triggers recheck_at daily checked'+(' retired' if ctx.get('market_revision', 0) >= 5 else ''),'report maintenance')
    require(m['kind'] in ('MARKET_REVIEW','PORTFOLIO_REVIEW','DEEP_REVIEW','EXPRESSION_ONLY'),'invalid assessment kind')
    for k in ('thesis','support','unresolved'):text(m[k],k);require(len(m[k].strip())>=12,k+' needs actual analysis')
    require(isinstance(m['triggers'],list) and m['triggers'],'at least one model-selected revision trigger required')
    ids=set();values={'metrics':ctx['metrics'],'observation':ctx.get('observation')}
    from .storage import current
    values['assessment']=current(root)['assessment']
    prefixes = ('/assessment/', '/metrics/', '/observation/')
    if scoped:
        from .closed_loop import report_inputs
        values.update(report_inputs(dict(ctx, current_strategy=ctx.get('current_strategy') or values['assessment'])))
        values.update({k: ctx.get(k) for k in ('public_evidence', 'report_subjects')})
        personal_result = '/portfolio_risk/' if ctx.get('market_revision', 0) >= 7 else '/allocation_decision/'
        prefixes += ('/market_result/', personal_result, '/applicability/', '/execution_plans/', '/public_evidence/', '/report_subjects/')
        if ctx.get('market_revision', 0) >= 7:
            prefixes += ('/strategy_advice/',)
    for index,t in enumerate(m['triggers']):
        exact(t,'id condition reason refs'+(' scope' if scoped else ''),'revision trigger');require(t['id'] not in ids,'duplicate revision trigger');ids.add(t['id'])
        if scoped:
            require(t['scope'] in ('MARKET', 'PORTFOLIO'), 'trigger owner required')
        for k in ('id','condition'):text(t[k],k)
        label='maintenance.triggers['+str(index)+'].reason (id='+t['id']+')'
        text(t['reason'],label)
        reason=t['reason'].strip()
        require(len(reason)>=12,label+': trigger must explain why the analysis needs review; minimum 12 characters, got '+str(len(reason)))
        require(len(set(c for c in reason if c.isalnum()))>=2,label+': meaningful explanation required; punctuation or repeated characters are not analysis')
        require(isinstance(t['refs'],list),'trigger references required')
        for ref in t['refs']:require(ref.startswith(prefixes),'invalid trigger reference');pointer(values,ref)
        if scoped and t['scope'] == 'MARKET':
            from .authored_report import PUBLIC
            require(all(ref.startswith(PUBLIC + ('/market_result/', '/applicability/market/')) for ref in t['refs']), 'private account condition cannot be a market trigger')
    current_time=ctx['evaluated_at'];require(instant(m['recheck_at'])>instant(current_time),'next review must be in the future')
    window=(ctx.get('report_care') or {}).get('execution_window') or {}
    if window.get('automation_id') or window.get('status') == 'AVAILABLE':
        require(window.get('run_ref')==spec['provenance']['run_ref'],'execution window belongs to another run; refresh native timing')
        require(window.get('status')=='AVAILABLE','next execution is unverified: '+window.get('reason','refresh native timing'))
        age=(instant(current_time)-instant(window['checked_at'])).total_seconds()
        require(-5<=age<=300 and instant(window['next_run_at'])>instant(current_time),'execution window is stale; refresh native timing without changing prior deadlines')
        require(instant(m['recheck_at'])>=instant(window['review_not_before']),
                'maintenance.recheck_at precedes the next executable review: next_run_at='+window['next_run_at']+
                ', execution_margin_seconds='+str(window['execution_margin_seconds'])+', review_not_before='+window['review_not_before']+
                '; choose a justified feasible deadline and preserve the actual prior check and any lateness')
    daily=m['daily'];exact(daily,'day report_by reason','daily feedback plan')
    require(daily['day']==day(current_time)==day(daily['report_by']),'daily plan must refer to current Japanese day')
    text(daily['reason'],'daily timing rationale');require(len(daily['reason'].strip())>=12,'daily timing needs a reason')
    care=ctx.get('report_care') or status(root,current_time)
    previous=latest(root);check=m['checked'];exact(check,'prior_review_id basis_review trigger_results outcome'+(' outcomes' if scoped else ''),'actual report recheck')
    require(check['prior_review_id']==(previous['review_id'] if previous else None),'stale report maintenance review')
    text(check['basis_review'],'silence/report basis review');require(len(check['basis_review'].strip())>=12,'check needs actual evidence assessment')
    require(check['outcome'] in ('VALID','REVISE','EVIDENCE_LIMITED'),'invalid review outcome')
    prior_ids={t['id'] for t in previous['maintenance']['triggers']} if previous else set()
    require(isinstance(check['trigger_results'],list),'trigger check results required');seen=set()
    for result in check['trigger_results']:
        exact(result,'id state evidence'+(' scope material' if scoped else ''),'trigger check result');require(result['id'] not in seen,'duplicate trigger check');seen.add(result['id'])
        require(result['state'] in ('MET','NOT_MET','UNKNOWN'),'invalid trigger outcome');text(result['evidence'],'actual trigger evidence')
        if scoped:
            require(result['scope'] in ('MARKET', 'PORTFOLIO') and type(result['material']) is bool, 'trigger needs ownership and significance')
            prior = next((t for t in (previous or {}).get('maintenance', {}).get('triggers', []) if t['id'] == result['id']), {})
            require(prior.get('scope', result['scope']) == result['scope'], 'existing trigger ownership cannot change during checking')
            current = next((t for t in m['triggers'] if t['id'] == result['id']), {})
            require(current.get('scope', result['scope']) == result['scope'], 'retained trigger cannot change ownership')
    require(seen==prior_ids,'all prior report triggers must actually be checked')
    if ctx.get('market_revision', 0) >= 5:
        require(isinstance(m['retired'], list), 'retired conditions must be a list')
        retired=set()
        for item in m['retired']:
            exact(item, 'id reason', 'retired condition')
            require(item['id'] not in retired, 'duplicate retired condition')
            retired.add(item['id']);text(item['reason'], 'condition retirement reason')
        require(retired == prior_ids - ids, 'every removed prior condition needs its retirement reason')
    if not care.get('fulfilled') and care.get('report_by'):
        require(instant(daily['report_by'])<=instant(care['report_by']),'unfulfilled daily deadline cannot be postponed')
    if scoped:
        exact(check['outcomes'], 'MARKET PORTFOLIO', 'separate market and portfolio outcomes')
        require(all(v in ('VALID', 'REVISE', 'EVIDENCE_LIMITED') for v in check['outcomes'].values()), 'invalid scoped review outcome')
        worst = 'REVISE' if 'REVISE' in check['outcomes'].values() else 'EVIDENCE_LIMITED' if 'EVIDENCE_LIMITED' in check['outcomes'].values() else 'VALID'
        require(check['outcome'] == worst, 'overall recheck must preserve unresolved scope')
        for r in check['trigger_results']:
            if r['state'] == 'UNKNOWN':
                require(check['outcomes'][r['scope']] != 'VALID', 'unknown condition cannot be recorded as valid')
            if r['material'] and r['state'] in ('MET', 'UNKNOWN'):
                require(spec['action'] == 'REPORT', 'material change or unresolved risk requires feedback')
        personal = (ctx.get('observation') or {}).get('review', {}).get('allocation_review') or {}
        if personal.get('material'):
            require(spec['action'] == 'REPORT', 'important portfolio review cannot be hidden as no change')
    if spec['action']=='SILENT':
        if scoped and not independent_notification:
            require(check['outcomes']['MARKET'] == 'VALID' and check['outcomes']['PORTFOLIO'] != 'REVISE', 'silence needs valid market and no unreported allocation revision')
        elif not independent_notification:
            require(check['outcome']=='VALID','silence must establish why the previous analysis still holds')
            require(all(r['state']!='UNKNOWN' for r in check['trigger_results']),'unknown trigger evidence is not market silence')
        require(not care.get('daily_due') and (care.get('fulfilled') or instant(daily['report_by'])>instant(current_time)),'daily feedback is due; unchanged conclusions do not authorize silence')
        # This judges new market meaning only. Delivery obligations have their
        # own recovery state and survive an otherwise valid quiet observation.
    # REVISE is an internal review result, not a delivery verdict. Actual
    # unreported significance still uses material above; daily feedback and
    # immutable delivery obligations retain their separate protections.
    if not independent_notification:
        if check['outcome']=='REVISE':require(spec['action']=='REPORT','changed report analysis must be reported')
        if spec['action']=='EVIDENCE_LIMITED':require(check['outcome']=='EVIDENCE_LIMITED','limited evidence must be recorded separately')
    if care.get('daily_due') and spec['action']=='REPORT':require(m['kind']!='EXPRESSION_ONLY','format changes do not satisfy daily feedback')

def daily_override(root,value,stamp=None):
    m=value.get('maintenance')
    if not m or m['kind']=='EXPRESSION_ONLY':return False
    live=status(root,stamp)
    return live.get('daily_due',False) and m['daily']['day']==live['day']

def source_current(root,value):
    if 'maintenance' not in value:return True
    p=Path(root)/'report-review-current.json'
    return (value['maintenance']['daily']['day']==day(now())
            and instant(now())<instant(value['maintenance']['recheck_at'])
            and (not p.exists() or read(p)['review_id']==value['review_id']))
