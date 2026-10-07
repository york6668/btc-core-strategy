"""Model-reviewed market reporting, independent of strategy publication.

This module binds evidence and journals decisions. It never decides whether a
market movement matters, sends messages, or changes the strategy Owner.
"""
from copy import deepcopy
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
import sqlite3
import json
import market_observation as market
from .common import create, digest, exact, facts, instant, locked, now, pointer, read, require, text
from .authored_report import FORMAT as AUTHORED_FORMAT, DISPLAY_FORMATS

SOURCE_SCHEMA = 'BTC_REPORT_SOURCE_V4'
ARTIFACT_SCHEMA = 'BTC_REPORT_V6'
TOKEN = re.compile(r'\{\{(/[^{}]+)\}\}')
FIELDS = ('overview', 'market', 'explanation', 'risk', 'next_observation')


def raw_for_observation(root, observed):
    """Read the exact observed package, never a later replacement of its rows."""
    db = Path(root).parent / 'market_observation.sqlite3'
    with market._connect(db, readonly=True) as conn:
        row = conn.execute('SELECT payload FROM evidence WHERE evidence_id=?', (observed['market_evidence_id'],)).fetchone()
    require(row is not None, 'reported observation raw evidence missing')
    raw = json.loads(row[0])
    market.validate_evidence(raw)
    require(raw['evidence_id'] == observed['market_evidence_id'], 'observation/raw mismatch')
    return raw


def metrics(raw, start):
    """Never aggregate instruments/quote units; windows are independent views."""
    result = {}
    end = market.timestamp(raw['cutoff'])
    day = instant(raw['cutoff']).astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for key, dataset in raw['datasets'].items():
        if dataset['interval_seconds'] != market.HOUR:
            value = {k: deepcopy(dataset[k]) for k in ('market', 'quote_unit', 'group', 'kind', 'instrument', 'interval_seconds', 'coverage', 'issues')}
            value['latest'] = deepcopy(dataset['rows'][-1]) if dataset['rows'] else None
            value['closed_history'] = market.summarize_candles(dataset['rows'], market.timestamp(dataset['requested_start']), market.timestamp(dataset['requested_end']), dataset['interval_seconds'])
            value['retrieved_at'] = raw['retrievals'][key]['retrieved_at']
            result[key] = value
            continue
        value = {k: dataset[k] for k in ('market', 'quote_unit', 'group', 'kind') if k in dataset}
        value['instrument'] = dataset.get('instrument')
        rows = dataset['rows']
        value['latest'] = deepcopy(rows[-1]) if rows else None
        for label, begin in [('today_utc', day.timestamp()), ('since_report', market.timestamp(start) if start else day.timestamp())]:
            summary = (market.summarize_candles(rows, begin, end, market.HOUR) if dataset['kind'] in ('CANDLES','MARK','INDEX')
                       else market.summarize_point(rows, begin, end, dataset['kind']))
            measured = summary.get('measurements') or {}
            if measured.get('net_taker_quote') is not None:
                measured['net_taker_quote_wan'] = measured['net_taker_quote'] / 10000
            if measured.get('observed_return_ratio') is not None:
                measured['observed_return_percent'] = measured['observed_return_ratio'] * 100
            value[label] = summary
        result[key] = value
    return result


def consensus_prices(root, artifact):
    """Project displayed prices from their original formal decision, without advice state."""
    from . import delivery
    source = artifact.get('source', {})
    layout = source.get('layout', {})
    authored = layout.get('format') == AUTHORED_FORMAT
    if not authored and not layout.get('include_recommendations'):
        return {}
    decision = delivery._decision(root, artifact['decision_id'])
    prior = source.get('report_review', {}).get('context', {}).get('reported', {}).get('consensus_prices', {})
    result = {}
    for side in ('lower_spot', 'upper_short'):
        if authored:
            bound = {ref for b in layout['bindings'] for ref in b['refs']}
            if not any(ref.startswith(('/assessment/recommendations/'+side+'/tiers/', '/execution_plans/'+side+'/tiers/')) for ref in bound):
                continue
        if source.get('layout', {}).get('price_status', {}).get(side) == 'WITHDRAWN':
            continue
        origin = decision
        tiers = origin['assessment']['recommendations'][side]['tiers']
        previous = prior.get(side)
        if not tiers and previous:
            origin = delivery._decision(root, previous['decision_id'])
            tiers = origin['assessment']['recommendations'][side]['tiers']
        prices = []
        for i, tier in enumerate(tiers):
            fields = {'price_low_usd','price_high_usd','allocation_percent'}
            if authored:
                prefixes = ('/assessment/recommendations/'+side+'/tiers/'+str(i)+'/',
                            '/execution_plans/'+side+'/tiers/'+str(i)+'/')
                fields = {key for key in fields if any(prefix+key in bound for prefix in prefixes)}
                if (fields & {'price_low_usd','price_high_usd'}
                        and Decimal(tier['price_low_usd']) == Decimal(tier['price_high_usd'])):
                    fields |= {'price_low_usd','price_high_usd'}
                if not {'price_low_usd','price_high_usd'} <= fields:
                    continue
            prices.append({key:deepcopy(tier[key]) for key in fields if key in tier})
        if origin is not decision:
            require(previous['tiers'] == prices, 'historical consensus prices differ from their formal source')
        if prices:
            result[side] = {'decision_id': origin['decision_id'], 'tiers': prices}
    return result


def reported(root):
    from . import delivery
    root = Path(root)
    p = root / 'reported-current.json'
    if not p.exists():
        return {'status': 'UNKNOWN', 'identity': None, 'display': None, 'coverage': None, 'information': None}
    baseline = read(p)
    if not baseline.get('delivery_id') or not (root/'outbox'/baseline['delivery_id']/'artifact.json').exists():
        return {'status':'LEGACY_UNSTRUCTURED', 'identity':digest(baseline), 'display':None, 'coverage':None, 'information':None}
    artifact, status, receipt = delivery.validate_delivered(root, baseline['delivery_id'])
    decision = delivery._decision(root, artifact['decision_id'])
    edited = artifact['schema'] in (delivery.ARTIFACT_SCHEMA, ARTIFACT_SCHEMA)
    display = receipt['assistant']['body'] if edited else receipt['visible_report_text']
    display_pointer = root/'last-display-current.json'
    display_identity = read(display_pointer)['delivery_id'] if display_pointer.exists() else baseline['delivery_id']
    price_artifact = artifact
    actual_receipt = receipt
    if display_identity != baseline['delivery_id']:
        display_artifact, _, display_receipt = delivery.validate_delivered(root, display_identity)
        price_artifact = display_artifact
        actual_receipt = display_receipt
        display = (display_receipt['assistant']['body'] if display_artifact['schema'] in delivery.EDITED_SCHEMAS
                   else display_receipt['visible_report_text'])
    coverage = artifact.get('coverage') or {'start':None, 'end':decision['assessment']['temporal_scope']['market_cutoff']}
    from .mobile_report import displayed_evaluation_time
    return {'status':'VERIFIED_DISPLAY' if edited else 'VERIFIED_LEGACY_BODY_AND_ACK',
            'identity':digest(baseline), 'delivery_id':baseline['delivery_id'], 'display':display,
            'display_delivery_id':display_identity, 'display_matches_baseline':display_identity==baseline['delivery_id'],
            'display_format':price_artifact.get('source',{}).get('layout',{}).get('format'),
            'display_evaluation_time':displayed_evaluation_time(price_artifact.get('source',{}), actual_receipt),
            'coverage':coverage, 'confirmed_at':receipt.get('checked_at', status['updated_at']),
            # A review can contain maintenance facts omitted from its summary.
            # Only the actual readback proves what the user received.
            'information':({'display':display} if price_artifact.get('source',{}).get('layout',{}).get('format') in DISPLAY_FORMATS
                           else price_artifact.get('source',{}).get('report_review',{}).get('information')),
            'consensus_prices':consensus_prices(root, price_artifact),
            'missing_conclusion':'conclusion' not in (decision.get('assessment') or {})}


def context(root):
    from . import sensing, storage, delivery
    root = Path(root)
    decision = storage.current(root)
    require(decision and decision.get('assessment'), 'complete strategy required for report context')
    latest = read(root/'sensing-current.json')
    observed = sensing.observation(root, latest['observation_id'])
    require(latest['observed_hour'] == observed['observed_hour'], 'report sensing pointer mismatch')
    raw = raw_for_observation(root, observed)
    from . import market_state
    separated = market_state.enabled(root, decision['assessment'])
    supplementary_id = observed['context']['identity'].get('factor_evidence_id')
    supplementary = read(root / 'evidence' / supplementary_id[7:] / 'evidence.json') if supplementary_id else None
    from .collector import context as collector_context
    collector_observations = collector_context(supplementary, root / 'evidence' / supplementary_id[7:]) if supplementary and supplementary.get('collector') else None
    prior = reported(root)
    start = (prior['coverage'] or {}).get('end')
    if start and instant(start) > instant(raw['cutoff']):
        start = raw['cutoff']
    unreported = []
    for path in (root/'sensing').glob('*.json'):
        item = read(path)
        if start is None or instant(item['observed_hour']) >= instant(start):
            unreported.append({'observation_id':item['observation_id'], 'observed_hour':item['observed_hour'],
                               'summary':item['review']['market_review']['assessment_summary']})
    unreported.sort(key=lambda x:(x['observed_hour'],x['observation_id']))
    attempts = delivery.attempt_recovery(root)
    pending_deliveries = delivery.recovery_actions(root, attempts)
    current_facts = facts(root.parent.parent)
    identity = {'decision_id':decision['decision_id'], 'observation_id':observed['observation_id'],
                'market_evidence_id':raw['evidence_id'], 'reported_identity':prior['identity'], 'display_delivery_id':prior.get('display_delivery_id'), 'fact_version':current_facts}
    if separated:
        identity['factor_evidence_id'] = supplementary_id
    previous_path = root/'report-review-current.json'
    previous = review(root, read(previous_path)['review_id']) if previous_path.exists() else None
    stamp = now()
    from .equity_products import subjects, REPORT_QUOTE_POLICY
    identity['report_quote_policy'] = digest(REPORT_QUOTE_POLICY)
    from . import report_care, cycle
    care=report_care.status(root,stamp)
    cycle_context = {}
    if cycle.enabled(root):
        basis = cycle.baseline(root, decision['assessment'], current_facts, stamp)
        cycle_context = {'cycle_policy_revision': 1, 'cycle_baseline': basis}
        identity['cycle_policy_revision'] = 1
        identity['cycle_baseline_id'] = basis['result_id']
        identity['cycle_baseline_status'] = basis['status']
        if cycle.report_required(root):
            cycle_context['deep_report_policy_revision'] = 1
            identity['deep_report_policy_revision'] = 1
            identity['cycle_report_hash'] = basis['report_hash']
    if care['enabled']:identity['report_day']=care['day']
    return {'schema':'BTC_REPORT_CONTEXT_V1', 'context_id':digest(identity), 'identity':identity,
            **cycle_context, **({'market_revision':max(5, observed.get('revision', 5), decision['assessment'].get('revision', 5))} if separated else {}), 'evaluated_at':stamp, 'reported':prior, 'report_care':care, 'coverage':{'start':start or raw['observed_hour'],'end':raw['cutoff']},
            'coverage_display_revision':3,
            'knowledge_cutoff':max([x['retrieved_at'] for x in raw['retrievals'].values()] + ([supplementary['cutoff']] if supplementary else []), key=instant),
            'public_evidence': supplementary if separated else None,
            **({'collector_observations': collector_observations} if collector_observations else {}),
            'report_subjects': subjects(supplementary, observed['review'].get('dispute_zones', [])),
            'report_quote_policy': deepcopy(REPORT_QUOTE_POLICY),
            'silent_hours':max(0,(instant(stamp)-instant(prior['confirmed_at'])).total_seconds()/3600) if prior.get('confirmed_at') else None,
            'current_strategy':{'decision_id':decision['decision_id'],
                                **{k:decision['assessment'].get(k) for k in ('conclusion','outlook','robustness','portfolio_analysis','recommendations','factor_assessment','section_provenance')},
                                'market_cutoff':decision['assessment']['temporal_scope']['market_cutoff']},
            'observation':{'observation_id':observed['observation_id'],'review':observed['review'], 'main_eligible':observed.get('main_eligible'),
                           'baseline_decision_id':observed['context']['identity']['decision_id']},
            'metrics':metrics(raw,start), 'unreported':unreported[-240:], 'unreported_total':len(unreported),
            'unreported_truncated':len(unreported)>240,'pending_delivery':pending_deliveries,
            'attempt_recovery': attempts,
            'pending_report_reviews':pending(root),
            'delivery_obligations':obligations(root),
            'required_report_events':required_events(root, cycle_context.get('cycle_baseline')),
            'previous_silence':{k:previous[k] for k in ('action','reason','next_observation')} if previous else None}


def review(root, identity):
    require(isinstance(identity,str) and re.fullmatch(r'sha256:[a-f0-9]{64}',identity), 'invalid report review identity')
    value = read(Path(root)/'report-reviews'/(identity[7:]+'.json'))
    require(value['review_id'] == identity == digest({k:v for k,v in value.items() if k!='review_id'}), 'report review damaged')
    return value


def delivered_events(root):
    from . import delivery
    result = set()
    for path in (Path(root)/'outbox').glob('*/status.json'):
        if read(path)['state'] != 'DELIVERED':continue
        artifact, _, _ = delivery.validate_delivered(root, path.parent.name)
        value = artifact.get('source', {}).get('report_review', {})
        result.update(v['id'] for v in value.get('important_events', []))
        basis = value.get('report_basis', {})
        if basis.get('result_id') and basis.get('mode') == 'CYCLE_SUMMARY':
            result.add('deep-result:' + basis['result_id'])
    return result


def required_events(root, basis):
    # Actual adopted research has a completion identity independent of rating,
    # amount, daily feedback and the decision's semantic diff.
    if not basis or basis.get('status') != 'CURRENT' or not basis.get('result_id') or not basis.get('report_hash'):return []
    event_id = 'deep-result:' + basis['result_id']
    return [] if event_id in delivered_events(root) else [
        {'id':event_id, 'kind':'DEEP_REVIEW', 'phase':'COMPLETED', 'impact':None}]


def record(root, spec, *, prepared_context=None):
    from . import storage
    from .common import replace
    with locked(root):
        ctx = context(root)
        if prepared_context is not None:
            require(prepared_context['context_id'] == ctx['context_id'] and
                    prepared_context.get('identity') == ctx.get('identity'), 'stale prepared report context')
            require(instant(prepared_context['evaluated_at']) <= instant(ctx['evaluated_at']),
                    'frozen report clock cannot be in the future')
            if (spec.get('layout') or {}).get('format') == AUTHORED_FORMAT:
                # Keep the author's frozen clock line. Applicability, deadlines
                # and price validity still use the live evaluated_at below.
                ctx['authored_evaluated_at'] = prepared_context['evaluated_at']
        exact(spec,'schema context_id action purposes reason next_observation baseline_review applicability_review information layout provenance'+(' user_request' if 'user_request' in spec else '')+(' maintenance' if 'maintenance' in spec else '')+(' report_basis' if 'report_basis' in spec else '')+(' delivery_coverage' if 'delivery_coverage' in spec else '')+(' important_events' if 'important_events' in spec else '')+(' result_binding' if ctx.get('market_revision', 0) >= 6 else ''),'report review')
        if ctx.get('market_revision', 0) >= 6:
            from .closed_loop import validate_report_binding
            validate_report_binding(spec, ctx)
        if 'user_request' in spec:
            exact(spec['user_request'],'reference reason','explicit user rewrite request')
            for key in ('reference','reason'):text(spec['user_request'][key],'user request '+key)
        require(spec['schema']=='BTC_REPORT_REVIEW_V1' and spec['context_id']==ctx['context_id'],'stale report review context')
        require(spec['action'] in ('REPORT','SILENT','EVIDENCE_LIMITED'),'invalid report action')
        events = spec.get('important_events', [])
        require(isinstance(events, list), 'important events must be a list')
        event_ids = set()
        for event in events:
            exact(event, 'id kind phase impact', 'important report event')
            for key in ('id', 'kind', 'impact'):text(event[key], 'important event ' + key)
            require(event['phase'] in ('IMPACT_PENDING', 'COMPLETED', 'CORRECTION'), 'invalid report event phase')
            require(event['id'] not in event_ids, 'duplicate report event')
            event_ids.add(event['id'])
        required = {v['id'] for v in ctx.get('required_report_events', [])}
        basis = spec.get('report_basis', {})
        completed_feedback = (spec['action'] == 'REPORT' and basis.get('mode') == 'CYCLE_SUMMARY'
                              and required == {'deep-result:' + (basis.get('result_id') or '')})
        require(not events or spec['action'] == 'REPORT', 'important event impact or result must be reported')
        require(isinstance(spec['purposes'],list) and set(spec['purposes']) <= {'MARKET_SUMMARY','STRATEGY_UPDATE'},'invalid report purposes')
        for key in ('reason','next_observation','baseline_review','applicability_review'):
            text(spec[key],key); require(len(spec[key].strip())>=12,key+' requires concrete model judgement')
        p=spec['provenance']; exact(p,'role model effort agent_id run_ref','report provenance')
        require(p['role']=='MAIN' and p['model']=='gpt-6.1-sol' and p['effort']=='high','report judgement belongs to Sol/high MAIN')
        text(p['agent_id'],'actual main agent');text(p['run_ref'],'actual main run')
        require(isinstance(spec['information'],dict),'reviewed information must be a mapping')
        for key,val in spec['information'].items():text(key,'information topic');text(val,'reviewed business meaning')
        pending_cycle = ctx.get('cycle_policy_revision') == 1 and ctx['cycle_baseline']['status'] != 'CURRENT'
        facts_match = facts(Path(root).parent.parent) == storage.current(root)['assessment']['fact_version']
        if not facts_match and ctx.get('market_revision', 0) >= 5 and spec['action'] == 'REPORT':
            text(spec['information'].get('fact_limitations'), 'personal response must explain changed facts without limiting market eligibility')
        if not facts_match and ctx.get('market_revision', 0) < 5:
            require(pending_cycle, 'facts changed; portfolio impact awaits cycle review')
            if spec['action'] == 'REPORT':
                require(spec['layout'] and (spec['layout'].get('format') in DISPLAY_FORMATS or not spec['layout']['include_recommendations']),
                        'facts changed; report limitations while portfolio impact awaits cycle review')
                text(spec['information'].get('fact_limitations'), 'report must explain changed facts and unreviewed portfolio impact')
            else:
                # A file fingerprint also changes for definition-only edits.
                # Keep the pending review and its limitations recordable; this
                # does not publish advice, certify facts, or count as delivery.
                require(spec['action'] == 'EVIDENCE_LIMITED',
                        'facts changed; record limitations rather than confirmed market silence')
        if ctx['reported']['status'] in ('VERIFIED_DISPLAY','VERIFIED_LEGACY_BODY_AND_ACK'):
            from .main_flow import report_comparison
            current_decision=storage.current(root)
            comparison=report_comparison(root,current_decision['assessment'],current_decision,current_decision.get('review',{}).get('material_new_outlook',False))
            # V2 summarizes the adopted report's cycle, advice value and
            # consensus applicability. MAIN reviews material meaning changes;
            # internal revisions cannot mechanically force an identical push.
            minimal = ((spec.get('layout') or {}).get('format') in DISPLAY_FORMATS
                       or (spec['layout'] is None and ctx['reported'].get('display_format') in DISPLAY_FORMATS))
            if comparison['report_required'] and not minimal:
                require(spec['action']=='REPORT' and (pending_cycle or 'STRATEGY_UPDATE' in spec['purposes']),'material strategy change must be covered as STRATEGY_UPDATE')
                if not pending_cycle and set(comparison['changed']) & {'recommendations','decision'}:
                    require(spec['layout'] and spec['layout']['include_recommendations'],'changed advice needs the formal recommendation table')
        from . import report_care
        report_care.validate(root,spec,ctx)
        from .cycle import validate_report_basis
        validate_report_basis(spec,ctx)
        require(required <= event_ids or completed_feedback, 'unreported completed research needs result feedback even when rating is unchanged')
        if spec['action']=='REPORT':
            if ctx.get('market_revision', 0) >= 6:
                require(spec['layout'] and spec['layout'].get('format') == AUTHORED_FORMAT,
                        'revision 6 reports use the final authored result, not legacy automatic assembly')
            if ctx.get('cycle_policy_revision') == 1:
                require(spec['layout'] and spec['layout'].get('format') in ('MARKET_MOBILE_V1','MARKET_MOBILE_V2',AUTHORED_FORMAT), 'cycle reports require the reviewed report layout')
            require(spec['purposes'] and spec['information'],'report purpose and information required')
            require(spec['layout'] is not None,'report layout required')
            # Legacy layouts retain their semantic index. V2 compares the actual
            # display below; historical indexes can include undisclosed analysis.
            prior = ctx['reported'].get('information')
            require(spec['layout'].get('format') in DISPLAY_FORMATS or prior is None or digest(prior)!=digest(spec['information']) or ctx['reported'].get('display_matches_baseline') is False or user_request_due(root,spec) or report_care.daily_override(root,spec,ctx.get('evaluated_at')),'same information already displayed; choose SILENT')
        else:
            require(spec['layout'] is None,'silence/limited evidence cannot contain a report source')
        coverage = spec.get('delivery_coverage', [])
        require(isinstance(coverage, list), 'delivery coverage must be a list')
        require(not coverage or spec['action'] == 'REPORT', 'silence cannot replace an outstanding report')
        outstanding = {v['review_id'] for v in obligations(root)}
        covered = set()
        for item in coverage:
            exact(item, 'review_id explanation', 'outstanding report coverage')
            require(item['review_id'] in outstanding and item['review_id'] not in covered, 'coverage must identify an outstanding report once')
            text(item['explanation'], 'coverage of still-important implications')
            require(len(item['explanation'].strip()) >= 12, 'replacement must explain covered or corrected meaning')
            covered.add(item['review_id'])
        frozen = dict(spec, context=ctx, created_at=now(), supersedes=sorted(covered))
        timing = report_care.timing_evidence(ctx, frozen['created_at'])
        if timing is not None:
            frozen['timing_evidence'] = timing
        frozen['review_id']=digest(frozen)
        if spec['action']=='REPORT':
            body=render(frozen,storage.current(root))
            require(all(event['impact'] in body for event in events), 'important event impact must appear in the completed report')
            if spec['layout'].get('format') == AUTHORED_FORMAT or (spec['layout'].get('format') == 'MARKET_MOBILE_V2' and spec['layout']['blocks']):
                require(all(item in body for item in spec['information'].values()), 'display information must quote actual rendered content; keep internal analysis in maintenance')
            require(not duplicate_mobile_body(root,frozen,ctx,body), 'same mobile meaning already displayed; choose SILENT')
        path=Path(root)/'report-reviews'/(frozen['review_id'][7:]+'.json')
        if not path.exists():create(path,frozen)
        replace(Path(root)/'report-review-current.json',{'review_id':frozen['review_id']})
        return {'review_id':frozen['review_id'],'action':spec['action'],'reason':spec['reason'],'path':str(path)}


def render(value, decision):
    """Numbers resolve only from the frozen observed metrics or formal assessment."""
    require(value['action']=='REPORT' and decision['decision_id']==value['context']['identity']['decision_id'],'report requires matching complete decision')
    if value['layout'].get('format') == AUTHORED_FORMAT:
        from .authored_report import render as authored_render
        return authored_render(value, decision)[0]
    if value['layout'].get('format') in ('MARKET_MOBILE_V1','MARKET_MOBILE_V2'):
        from .mobile_report import render as mobile_render
        return mobile_render(value,decision)[0]
    layout=value['layout'];exact(layout,'format overview market explanation risk next_observation include_recommendations','market report layout')
    require(layout['format']=='MARKET_CONCISE_V1' and type(layout['include_recommendations']) is bool,'invalid market layout')
    values={'assessment':decision['assessment'],'metrics':value['context']['metrics']}
    paragraphs=[]
    templates=' '.join(str(layout.get(k,'')) for k in FIELDS)
    for module, item in decision['assessment']['domain']['portfolio_decision'].items():
        if isinstance(item,dict) and Decimal(str(item.get('amount_u','0'))) != 0:
            require('/assessment/domain/portfolio_decision/'+module+'/amount_u' in templates,'report omits a nonzero formal adjustment')
    for key in FIELDS:
        template=layout[key];text(template,key)
        require(not re.search(r'\d', TOKEN.sub('',template)),'report numbers must use frozen metric/assessment pointers')
        def bind(match):
            result=pointer(values,match.group(1))
            require(not isinstance(result,(dict,list,bool)) and result is not None,'report reference must be a known scalar')
            if isinstance(result,(int,float)) or (isinstance(result,str) and re.fullmatch(r'-?\d+(?:\.\d+)?',result)):
                return format(Decimal(str(result)).quantize(Decimal('.01')),',.2f').rstrip('0').rstrip('.')
            return str(result)
        body=TOKEN.sub(bind,template)
        require('\n' not in body and '\r' not in body and '|' not in body and len(body)<=240,'market paragraph too long or contains layout')
        paragraphs.append(body)
    ctx=value['context']; cover=ctx['coverage']
    stamp=lambda s:instant(s).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    purposes='／'.join({'MARKET_SUMMARY':'行情总结','STRATEGY_UPDATE':'策略更新'}[p] for p in value['purposes'])
    lines=[purposes+'｜覆盖：'+stamp(cover['start'])+'—'+stamp(cover['end'])+'；行情截至：'+stamp(cover['end'])+'；取证截至：'+stamp(ctx['knowledge_cutoff']),'',paragraphs[0],'',paragraphs[1],'',paragraphs[2],'',paragraphs[3],'',paragraphs[4]]
    if ctx.get('coverage_display_revision',1)>=2 and instant(cover['start'])==instant(cover['end']):
        lines[0]=purposes+'｜同一截止时点的补充；行情截至：'+stamp(cover['end'])+'；取证截至：'+stamp(ctx['knowledge_cutoff'])
    if layout['include_recommendations']:
        from .brief_report import _number
        lines+=['','| 建议 | BTC参考位置（USDT/BTC） | 金额 |','|---|---:|---:|']
        for side,label,unit in [('upper_short','上沿空单','U名义额'),('lower_spot','下沿现货篮子','U投入')]:
            rec=decision['assessment']['recommendations'][side]
            require(instant(rec['validity']['until'])>instant(ctx['knowledge_cutoff']),'expired recommendations require main reassessment')
            if rec['status']=='NOT_RECOMMENDED':lines+=['| '+label+' | 当前不建议 | — |'];continue
            for tier in rec['tiers']:
                from .recommendations import spot_label
                tier_label = spot_label(tier) if side == 'lower_spot' and 'allocation_percent' in tier else label
                price=_number(tier['price_low_usd'])
                if Decimal(tier['price_low_usd'])!=Decimal(tier['price_high_usd']):price+='–'+_number(tier['price_high_usd'])
                lines+=['| '+tier_label+' | '+price+' | '+_number(tier['amount_u'])+unit+' |']
    return '\n'.join(lines)


def source(root, identity):
    from . import delivery, semantics
    value=review(root,identity);decision=delivery._decision(root,value['context']['identity']['decision_id'])
    require(value['action']=='REPORT','report review did not request a report')
    body=render(value,decision)
    kinds=['deadline','direction','judgement','judgement','uncertainty','condition']
    paragraphs=[p for p in body.split('\n\n') if p]
    constraints=[]
    for i,p in enumerate(paragraphs):
        if p.startswith('|'):
            constraints.extend({'id':'row-'+str(j),'kind':'amount','source_text':line} for j,line in enumerate(p.splitlines()) if 'U名义额' in line or 'U投入' in line or '当前不建议' in line)
        else:constraints.append({'id':'paragraph-'+str(i),'kind':kinds[min(i,5)],'source_text':p})
    if value['layout'].get('format') in ('MARKET_MOBILE_V1','MARKET_MOBILE_V2'):
        from .mobile_report import render as mobile_render
        body,constraints=mobile_render(value,decision)
    elif value['layout'].get('format') == AUTHORED_FORMAT:
        from .authored_report import render as authored_render
        body,constraints=authored_render(value,decision)
    result={'schema':SOURCE_SCHEMA,'decision_id':decision['decision_id'],'body':body,'body_hash':digest(body.encode()),
            'semantic_hash':digest(semantics.stable(decision['assessment'])),'constraints':constraints,
            'author_review':{'role':'MAIN','agent_id':value['provenance']['agent_id'],'source_hash':digest(body.encode()),
                             'coverage_review':value['baseline_review'],'faithfulness_review':value['applicability_review']},
            'layout':value['layout'],'report_review':value}
    delivery.validate_source(result,decision)
    return result


def due(root, source_value):
    """Recheck ownership/baseline, without turning a price/time change into a report."""
    value=source_value['report_review']; ctx=value['context']; latest=context(root)
    from . import report_care
    if latest['context_id'] != ctx['context_id'] or not report_care.source_current(root,value):return False
    if ctx.get('market_revision', 0) >= 6:
        from .closed_loop import report_inputs
        if value['result_binding'] != report_inputs(latest)['result_binding']:
            return False
    if duplicate_mobile_body(root,value,latest,source_value['body']):return False
    prior=latest['reported'].get('information')
    return value['action']=='REPORT' and (value['layout'].get('format') in DISPLAY_FORMATS or prior is None or digest(prior)!=digest(value['information']) or latest['reported'].get('display_matches_baseline') is False or user_request_due(root,value) or report_care.daily_override(root,value))


def duplicate_mobile_body(root, value, ctx, body):
    """Exact repeat guard after semantic review; never a semantic acceptance test."""
    from . import report_care
    from .mobile_report import evaluation_time_line, without_evaluation_time
    from .authored_report import business_text
    normalize = business_text if AUTHORED_FORMAT in (value.get('layout',{}).get('format'), ctx['reported'].get('display_format')) else (lambda v:v)
    return (value.get('layout',{}).get('format') in DISPLAY_FORMATS
            and ctx['reported'].get('display_format') in DISPLAY_FORMATS
            and ctx['reported'].get('display_matches_baseline') is not False
            and normalize(without_evaluation_time(body, evaluation_time_line(value))) == normalize(without_evaluation_time(
                ctx['reported'].get('display'), ctx['reported'].get('display_evaluation_time')))
            and not user_request_due(root,value)
            and not report_care.daily_override(root,value,ctx.get('evaluated_at')))


def obligations(root):
    """REPORT persists until verified delivery or explicit transfer to another REPORT.

    Queue receipts and later SILENT/EVIDENCE_LIMITED reviews never discharge it.
    Historical silent supersedes are deliberately ignored.
    """
    from . import delivery
    root=Path(root); queued={}; delivered=set()
    for p in (root/'outbox').glob('*/artifact.json'):
        a=read(p);rid=a.get('source',{}).get('report_review',{}).get('review_id')
        if rid:
            state=read(p.parent/'status.json')['state']
            queued.setdefault(rid, []).append({'delivery_id':p.parent.name,'state':state})
            if state=='DELIVERED':
                delivery.validate_delivered(root,p.parent.name)
                delivered.add(rid)
    reviews=[review(root,'sha256:'+p.stem) for p in (root/'report-reviews').glob('*.json')]
    reports={v['review_id']:v for v in reviews if v['action']=='REPORT'}
    # Legacy automatic supersedes did not review retained meaning. Only an
    # explicit coverage review transfers the outstanding obligation.
    superseded={item['review_id'] for v in reports.values() for item in v.get('delivery_coverage', [])
                if item['review_id'] in reports and item['review_id'] in v.get('supersedes', [])}
    return sorted([{'review_id':rid,'decision_id':v['context']['identity']['decision_id'],
                    'created_at':v['created_at'],'reason':v['reason'],
                    'deliveries':queued.get(rid,[]),'requires_current_review':True}
                   for rid,v in reports.items() if rid not in delivered and rid not in superseded], key=lambda v:v['created_at'])


def pending(root):
    """Compatibility view: obligations not yet enqueued, not a delivery verdict."""
    return [v for v in obligations(root) if not v['deliveries']]


def review_required(root):
    """A missing reporting judgement is not a silent hour."""
    from .storage import current
    root=Path(root)
    if obligations(root):return True
    from . import report_care
    if report_care.status(root).get('review_required'):return True
    if not (root/'sensing-current.json').exists():return False
    if not (root/'report-review-current.json').exists():return True
    value=review(root,read(root/'report-review-current.json')['review_id'])
    identity=value['context']['identity']
    baseline=read(root/'reported-current.json') if (root/'reported-current.json').exists() else None
    displayed=read(root/'last-display-current.json')['delivery_id'] if (root/'last-display-current.json').exists() else (baseline or {}).get('delivery_id')
    if identity.get('display_delivery_id') not in (None, displayed):
        if not (baseline and displayed==baseline.get('delivery_id') and read(root/'outbox'/displayed/'artifact.json').get('source',{}).get('report_review',{}).get('review_id')==value['review_id']):return True
    return (identity['observation_id']!=read(root/'sensing-current.json')['observation_id'] or
            identity['decision_id']!=current(root)['decision_id'] or
            identity['fact_version']!=facts(root.parent.parent) or
            (identity['reported_identity']!=(digest(baseline) if baseline else None) and
             not (baseline and baseline.get('delivery_id') and
                  read(root/'outbox'/baseline['delivery_id']/'artifact.json').get('source',{}).get('report_review',{}).get('review_id')==value['review_id'])))


def user_request_due(root,value):
    request=value.get("user_request")
    if not request:return False
    for path in (Path(root)/"outbox").glob("*/artifact.json"):
        artifact=read(path)
        prior=artifact.get("source",{}).get("report_review",{}).get("user_request",{})
        if prior.get("reference")==request["reference"] and read(path.parent/"status.json")["state"]=="DELIVERED":return False
    return True
