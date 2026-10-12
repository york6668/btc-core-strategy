"""Rule simulation and prospective judgement feedback; neither proves live profit."""
from decimal import Decimal
from pathlib import Path
from .common import create, digest, exact, instant, locked, now, numbered, read, require, text
from . import evidence


def number(value):
    return Decimal(numbered(value))


def backtest(spec, bundle, directory):
    exact(spec, 'rule costs bars windows', 'rule validation input')
    rule=spec['rule']; exact(rule,'id kind recorded_at lookback capital_fraction instrument', 'predeclared rule')
    text(rule['id'],'strategy hypothesis identity'); text(rule['instrument'],'simulated instrument')
    require(rule['kind']=='CASH_SPOT_LONG_FLAT_MEAN', 'unsupported rule/product; no fabricated leverage or financing simulation')
    require(type(rule['lookback']) is int and rule['lookback']>=2, 'lookback requires a meaningful preceding sample')
    fraction=number(rule['capital_fraction']); require(0<fraction<=1,'cash spot capital constraint must be within one funded portfolio')
    costs=spec['costs']; exact(costs,'commission spread slippage source_refs', 'available implementation costs')
    base={'schema':'BTC_STRATEGY_VALIDATION_V1','result_kind':'HISTORICAL_SIMULATION', 'strategy_id':rule['id'],
          'rule_hash':digest(rule),'input_hash':digest(spec),'evidence_id':bundle['evidence_id'],
          'product_scope':['CASH_SPOT'],'funding_scope':'FULLY_FUNDED',
          'adoption_eligibility':'METHOD_CHECK_ONLY',
          'unsupported_capabilities':['MARGIN_SURVIVAL','CROSS_MARGIN','FUNDING_COSTS','PRODUCT_STRUCTURE','RULE_EFFECTIVENESS','PORTFOLIO_EFFECTIVENESS'],
          'live_performance':'NOT_VALIDATED','parameter_predeclaration':'DECLARED_INPUT_NOT_HISTORICALLY_ARCHIVED','limitations':[], 'windows':[],'sensitivity':[]}
    if any(costs[k] is None for k in ('commission','spread','slippage')) or not costs['source_refs']:
        base.update(status='DATA_LIMITED',limitations=['Available execution costs are incomplete; missing costs are not zero.']);return base
    for key in ('commission','spread','slippage'):
        require(0<=number(costs[key])<1,'invalid cost rate')
    require(isinstance(costs['source_refs'],dict) and set(costs['source_refs'])=={'commission','spread','slippage'}, 'each cost needs its exact archived numeric reference')
    for key,ref in costs['source_refs'].items():
        require(evidence.resolve_number(bundle,directory,ref,verified=True)==numbered(costs[key]), 'cost rate differs from its archived source')
    require(isinstance(spec['bars'],list) and spec['bars'],'archived bars required')
    bars=[]
    for source in spec['bars']:
        exact(source,'row ref','archived bar binding')
        row=source['row']; exact(row,'open_at close_at known_at open close','as-known price bar')
        require(evidence.resolve(bundle,directory,source['ref'],verified=True)==row,'bar differs from its archived raw value')
        require(instant(row['open_at'])<instant(row['close_at'])<=instant(row['known_at']),'bar availability clock is invalid')
        require(number(row['open'])>0 and number(row['close'])>0,'bar prices must be positive')
        bars.append(row)
    require(bars==sorted(bars,key=lambda r:instant(r['open_at'])),'bars must be ordered')
    require(all(instant(a['close_at'])<instant(b['open_at']) for a,b in zip(bars,bars[1:])),'overlapping price bars')
    require(isinstance(spec['windows'],list) and spec['windows'],'holdout or rolling validation windows required')
    windows=[]
    for window in spec['windows']:
        exact(window,'id train_start train_end test_start test_end','validation window')
        text(window['id'],'validation window identity')
        require(instant(rule['recorded_at'])<=instant(window['train_end'])<instant(window['test_start'])<=instant(window['test_end']),'rule/holdout clocks overlap or use future parameters')
        require(instant(window['train_start'])<=instant(window['train_end']),'training period reversed')
        test=[i for i,r in enumerate(bars) if instant(window['test_start'])<=instant(r['open_at']) and instant(r['close_at'])<=instant(window['test_end'])]
        require(test,'test window has no archived bars')
        available=[r for r in bars[:test[0]] if instant(r['open_at'])>=instant(window['train_start']) and instant(r['known_at'])<=instant(bars[test[0]]['open_at'])]
        if len(available)<rule['lookback']:
            base.update(status='DATA_LIMITED',limitations=['Insufficient as-known training history at the first holdout execution.']);return base
        source_by_id={s['id']:s for s in bundle['sources']}
        for ref in costs['source_refs'].values():
            source=source_by_id[ref['source_id']]
            require(instant(source['published_at'])<=instant(window['test_start']) and instant(source['start'])<=instant(window['test_start']) and instant(source['end'])>=instant(window['test_end']), 'cost source does not establish availability and applicability for the validation window')
        windows.append((window,test))
    def simulate(indices,multiplier):
        cash=Decimal(1); units=Decimal(0); previous=Decimal(1); peak=Decimal(1); drawdown=Decimal(0); turns=0; checkpoints=[]
        rate=sum((number(costs[k]) for k in ('commission','spread','slippage')),Decimal(0))*multiplier
        require(rate<1,'sensitivity costs exceed available capital')
        for i in indices:
            row=bars[i]; opening=number(row['open']); close=number(row['close'])
            history=[r for r in bars[:i] if instant(r['known_at'])<=instant(row['open_at'])]
            require(len(history)>=rule['lookback'],'no as-known history at execution')
            sample=history[-rule['lookback']:]
            mean=sum((number(r['close']) for r in sample),Decimal(0))/Decimal(rule['lookback'])
            target=fraction if number(sample[-1]['close'])>mean else Decimal(0)
            nav=cash+units*opening
            # A cash-funded target includes its actual turnover cost.
            old_value=units*opening
            desired=(target*(nav+rate*old_value)/(1+target*rate) if target*nav>=old_value
                     else target*(nav-rate*old_value)/(1-target*rate))
            desired=max(Decimal(0),desired)
            turnover=abs(desired-old_value);cash=nav-desired-turnover*rate;units=desired/opening
            require(cash>=Decimal('-1E-24'),'cash constraint violated')
            turns+=int(turnover!=0)
            nav=cash+units*close;peak=max(peak,nav);drawdown=max(drawdown,1-nav/peak)
            checkpoints.append({'open_at':row['open_at'],'signal_known_at':sample[-1]['known_at'],
                                'exposure':numbered(target),'net_return':numbered(nav/previous-1),'nav':numbered(nav)})
            previous=nav
        # Mark remaining units net of the same documented exit costs.
        ending=cash+units*number(bars[indices[-1]]['close'])*(1-rate)
        drawdown=max(drawdown,1-ending/peak)
        return {'net_return':numbered(ending-1),'max_close_to_close_drawdown':numbered(drawdown),
                'turnover_events':turns,'checkpoints':checkpoints,'exit_cost_included':True}
    for window,test in windows:
        base['windows'].append({'window':window,'result':simulate(test,Decimal(1))})
    base['sensitivity']=[{'cost_multiplier':'2','windows':[{'id':w['id'],'net_return':simulate(t,Decimal(2))['net_return']} for w,t in windows]}]
    base.update(status='SIMULATED_NOT_EFFECTIVENESS_VALIDATED')
    base['limitations']=['Cash spot rule only; no claim of derivative margin survival, calibrated forecast skill or real portfolio returns.',
                         'Held-out means outside the declared training window; dataset independence and selection bias require separate review.',
                         'Drawdown measures checkpoints, not unseen intrabar worst losses.']
    return base


def record_rule(root, spec):
    exact(spec,'id kind lookback capital_fraction instrument','fixed rule input')
    require(spec['kind']=='CASH_SPOT_LONG_FLAT_MEAN','unsupported rule product')
    require(type(spec['lookback']) is int and spec['lookback']>=2 and 0<number(spec['capital_fraction'])<=1,'invalid fixed rule constraints')
    for field in ('id','instrument'):text(spec[field],'rule '+field)
    value={**spec,'recorded_at':now()}
    identity=digest(value)
    with locked(root):
        path=Path(root)/'investment/rules'/(identity[7:]+'.json')
        if not path.exists():create(path,value)
    return {'rule_id':identity,'record':str(path),'rule':value,'adoption_status':'INPUT_ONLY'}


def record_backtest(root, spec, bundle, directory):
    result=backtest(spec,bundle,directory)
    rule_path=Path(root)/'investment/rules'/(digest(spec['rule'])[7:]+'.json')
    if rule_path.is_file():
        require(read(rule_path)==spec['rule'],'recorded rule differs from simulation')
        result['parameter_predeclaration']=('ACTUALLY_RECORDED_BEFORE_DECLARED_TEST_WINDOWS' if result['status']=='SIMULATED_NOT_EFFECTIVENESS_VALIDATED' else 'ACTUALLY_RECORDED_NO_COMPLETED_TEST')
    value={'schema':'BTC_VALIDATION_RECORD_V1','recorded_at':now(),'result':result}
    identity=digest(value)
    with locked(root):
        path=Path(root)/'investment/validations'/(identity[7:]+'.json')
        if not path.exists():create(path,value)
    return {'validation_id':identity,'record':str(path),'result':result}


def validation_record(root, identity):
    import re
    require(isinstance(identity,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',identity), 'invalid validation identity')
    value=read(Path(root)/'investment/validations'/(identity[7:]+'.json'))
    require(digest(value)==identity and value['schema']=='BTC_VALIDATION_RECORD_V1','validation record damaged')
    result=value['result']
    require(result['schema']=='BTC_STRATEGY_VALIDATION_V1' and result['result_kind']=='HISTORICAL_SIMULATION', 'unsupported validation owner')
    require(result['product_scope']==['CASH_SPOT'] and result['funding_scope']=='FULLY_FUNDED'
            and result['adoption_eligibility']=='METHOD_CHECK_ONLY' and result['live_performance']=='NOT_VALIDATED', 'simulation scope or adoption eligibility changed')
    # Capabilities come from the implemented evaluator, never an authored claim.
    supported={'CASH_FUNDING','DECLARED_EXECUTION_COSTS'} if result['status']=='SIMULATED_NOT_EFFECTIVENESS_VALIDATED' else set()
    return value,supported


def _evaluation_baseline(root, decision_id, window_id):
    from .investment import decision
    import re
    require(isinstance(decision_id,str) and re.fullmatch(r'[0-9a-f]{64}',decision_id), 'invalid original decision identity')
    original=read(Path(root)/'versions'/decision_id/'decision.json')
    require(original.get('decision_id')==decision_id and
            digest({k:v for k,v in original.items() if k!='decision_id'})[7:]==decision_id,
            'original evaluation decision changed')
    formed=decision(original['assessment']); require(formed is not None,'no prospective investment evaluation baseline')
    windows={w['id']:w for w in formed['evaluation_windows']}
    require(window_id in windows,'evaluation changed the original question identity')
    return formed,windows[window_id]


def _process_progress(spec, window):
    progress=spec.get('process_progress')
    if window['end'] is None:
        text(window.get('process'),'original mechanism-based evaluation progress')
        require(progress is not None,'process evaluation needs actual process progress')
    if progress is None:
        return
    text(window.get('process'),'original mechanism-based evaluation progress')
    exact(progress,'status explanation','actual evaluation process progress')
    require(progress['status'] in ('NOT_OCCURRED','OBSERVED','UNKNOWN'),'invalid evaluation process progress')
    text(progress['explanation'],'actual process occurrence or evidence limitation')
    if spec['outcome']=='NOT_DUE':
        require(progress['status']=='NOT_OCCURRED','observed or unknown process cannot remain NOT_DUE')
    elif spec['outcome'] in ('REALIZED','FAILED','PARTIAL'):
        require(progress['status']=='OBSERVED','a process outcome requires actual observed progress')
    else:
        require(spec['outcome']=='EVIDENCE_LIMITED','invalid process judgement outcome')


def evaluate_judgement(root, spec, bundle, directory):
    exact(spec,'decision_id window_id outcome explanation observed_at evidence_refs' +
          (' process_progress' if 'process_progress' in spec else ''),'judgement evaluation')
    formed,window=_evaluation_baseline(root,spec['decision_id'],spec['window_id'])
    require(instant(window['start'])<=instant(spec['observed_at'])<=instant(bundle['cutoff']),'evaluation evidence time exceeds its source cutoff')
    require(spec['outcome'] in ('REALIZED','FAILED','PARTIAL','EVIDENCE_LIMITED','NOT_DUE'),'invalid judgement outcome')
    if window['end'] is not None:
        require(spec['outcome']!='NOT_DUE' or instant(spec['observed_at'])<instant(window['end']),'expired evaluation window cannot remain NOT_DUE')
    _process_progress(spec,window)
    text(spec['explanation'],'actual mechanism and counterevidence evaluation')
    require(isinstance(spec['evidence_refs'],list),'evaluation source refs required')
    require(spec['evidence_refs'] or spec['outcome'] in ('EVIDENCE_LIMITED','NOT_DUE'),'claimed realization or failure needs evidence')
    for ref in spec['evidence_refs']: evidence.resolve(bundle,directory,ref,verified=True)
    value={'schema':'BTC_JUDGEMENT_EVALUATION_V1','result_kind':'JUDGEMENT_FEEDBACK','recorded_at':now(),
           'baseline_window':window,'original_policy_version':formed['policy']['version'],
           'original_investment_decision_hash':digest(formed),
           'evidence_id':bundle['evidence_id'],**spec,'performance':'NOT_A_RETURN_MEASUREMENT'}
    identity=digest(value)
    with locked(root):
        path=Path(root)/'investment/evaluations'/(identity[7:]+'.json')
        if not path.exists():create(path,value)
    return {'evaluation_id':identity,'record':str(path),'original_window_unchanged':True}


def feedback(root, assessment):
    from .investment import decision
    from .portfolio_ledger import context as ledger_context
    original=decision(assessment)
    evaluations=[]
    origins=[]
    for path in sorted((Path(root)/'investment/evaluations').glob('*.json')):
        value=read(path);require(digest(value)=='sha256:'+path.stem,'evaluation record damaged')
        formed,window=_evaluation_baseline(root,value['decision_id'],value['window_id'])
        require(value['baseline_window']==window and value['original_policy_version']==formed['policy']['version'],
                'evaluation baseline window or policy changed')
        baseline_hash=digest(formed)
        if 'original_investment_decision_hash' in value:
            require(value['original_investment_decision_hash']==baseline_hash,'evaluation baseline decision changed')
        if original and value['original_policy_version']==original['policy']['version']:
            evaluations.append(value)
            origins.append({'evaluation_id':'sha256:'+path.stem,'decision_id':value['decision_id'],
                'investment_decision_hash':baseline_hash,
                'applies_to_current_investment_decision':baseline_hash==digest(original)})
    validations=[]
    for path in (Path(root)/'investment/validations').glob('*.json'):
        identity='sha256:'+path.stem
        record,supported=validation_record(root,identity)
        validations.append({'validation_id':identity,'recorded_at':record['recorded_at'],
            'result':record['result'],'supported_method_capabilities':sorted(supported)})
    rules=[]
    for path in (Path(root)/'investment/rules').glob('*.json'):
        value=read(path);require(digest(value)=='sha256:'+path.stem,'rule record damaged')
        rules.append({'rule_id':'sha256:'+path.stem,'rule':value,'adoption_status':'INPUT_ONLY'})
    return {'ledger':ledger_context(root),'judgement_evaluations':evaluations,
            'judgement_evaluation_origins':origins,
            'available_method_validations':validations,'recorded_rule_inputs':rules,
            'required_evaluation_windows':original['evaluation_windows'] if original else [],
            'rule_performance':'NOT_VALIDATED_UNLESS_SEPARATELY_EVIDENCED','actual_performance':'REQUIRES_COMPLETE_CONFIRMED_LEDGER'}
