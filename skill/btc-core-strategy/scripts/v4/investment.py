"""Source-bound investment decisions within the existing DecisionSnapshot owner.

Checks protect input/decision lineage and unresolved dependencies. They do not
score prose, select assets or turn a model judgement into a trade.
"""
from copy import deepcopy
from pathlib import Path
from .common import digest, exact, facts, instant, require, text

PRODUCTS={'CASH_SPOT','LEVERAGED_DERIVATIVE','CROSS_MARGIN_DERIVATIVE','EQUITY_PRODUCT','RESEARCH_OR_POLICY','UNKNOWN'}

POLICY_INSTRUCTIONS = (
    '投资者目标政策与独立市场研究是两套输入，在组合选择处汇合；目标、负债、税务、现金流按实际相关性读取获准资料，不要求机构清单。'
    '区分战略配置、战术偏离、再平衡和保护目的；目标比例不是现金，保证金预留不是可承受损失。'
    '公开方向、现价吸引力、策略适用性分别形成；宏观冲击沿机制传导到相关资产和已定价部分，动态比较竞争解释，不固化指标或慢变量前置。'
    '组合研究比较当前方案与相关候选，统筹收益来源、风险重叠、资本、流动性、净成本及等待机会成本；交付总体取舍、优先关系及有依据的分项判断。'
    '候选按问题选择，不要求每次五项操作齐全或精确权重。关键未知只限制依赖它的决定；无依据时不强造操作，也不把全体未决当作完成的取舍。'
    '每个旧问题明确解决、替代、仍待事实、需补研究或不再适用；复核结束和required=false不关闭旧问题、不恢复过期建议。'
    '每个候选的validation明确产品、判断或验证依据、实际验证记录、所需能力和限制；现货模拟只核验已实现方法，不能证明杠杆保护有效。保证金生存、跨保证金与资金费未验证时通过对应缺口限制动作或数量，不能由方法通过提升为可执行或有效策略。'
    '事前保留实际政策版本、市场冻结、候选比较、决定依据、采用状态、最小关键缺口及评价窗口；事后结果与原判断分别记录，不倒填。'
)


def source_refs(refs, project, version):
    require(isinstance(refs, list) and refs, 'confirmed source references required')
    for ref in refs:
        exact(ref, 'file line_start line_end quote', 'confirmed source reference')
        require(ref['file'] in version and version[ref['file']] is not None, 'source must be an available confirmed fact file')
        raw = (Path(project) / ref['file']).read_bytes()
        require(digest(raw) == version[ref['file']], 'confirmed source version changed')
        lines = raw.decode('utf-8').splitlines()
        a, b = ref['line_start'], ref['line_end']
        require(type(a) is int and type(b) is int and 1 <= a <= b <= len(lines), 'source line range invalid')
        text(ref['quote'], 'literal confirmed quote')
        require(ref['quote'] in '\n'.join(lines[a-1:b]), 'source quote differs from confirmed file')


def policy(value, version, at, project=None):
    exact(value, 'version recorded_at fact_version objectives constraints baseline unknowns source_refs', 'investor policy')
    require(value['version'] == digest({k:v for k,v in value.items() if k != 'version'}), 'policy version damaged')
    require(value['fact_version'] == version, 'policy binds different confirmed facts')
    require(instant(value['recorded_at']) <= instant(at), 'policy cannot be recorded after its decision')
    for key in ('objectives', 'constraints', 'unknowns'):
        require(isinstance(value[key], list), 'policy ' + key + ' must be a list')
        for item in value[key]:
            text(item, 'policy ' + key)
    require(value['objectives'] and value['constraints'], 'actual objectives and constraints required')
    text(value['baseline'], 'strategic policy baseline, not inferred holdings')
    if project is not None:
        source_refs(value['source_refs'], project, version)
        from .common import read
        recorded=Path(project)/'.strategy/v4/investment/policies'/(value['version'][7:]+'.json')
        require(recorded.is_file() and read(recorded)==value, 'policy must be recorded before candidate evaluation, not backfilled during publication')
    else:
        require(isinstance(value['source_refs'], list) and value['source_refs'], 'policy sources required')


def decision(assessment):
    return ((assessment or {}).get('recommendations') or {}).get('portfolio', {}).get('investment_decision')


def validate(value, cutoff, market_hash, version, project=None, revision=8):
    exact(value, 'policy market_result_hash fact_version decided_at valid_until status summary candidates selected_ids priorities supported_judgements gaps issues evaluation_windows review_conditions' + (' account_constraints' if revision >= 9 else ''), 'investment decision')
    require(value['market_result_hash'] == market_hash and value['fact_version'] == version, 'investment inputs changed')
    require(max(instant(cutoff), instant(value['decided_at'])) < instant(value['valid_until']), 'investment validity requires actual review')
    policy(value['policy'], version, value['decided_at'], project)
    require(value['status'] in ('DECIDED', 'PARTIAL', 'UNDETERMINED'), 'invalid investment decision status')
    text(value['summary'], 'whole portfolio trade-off')
    require(isinstance(value['candidates'], list) and len(value['candidates']) >= 2, 'compare current arrangement with a relevant alternative')
    ids, current = set(), 0
    for item in value['candidates']:
        exact(item, 'id scope purpose is_current arrangement verdict comparison dependencies validation' + (' execution_role' if revision >= 9 else ''), 'investment candidate')
        for key in ('id', 'scope', 'purpose', 'arrangement'):
            text(item[key], 'candidate ' + key)
        require(item['id'] not in ids, 'duplicate investment candidate')
        ids.add(item['id'])
        require(type(item['is_current']) is bool, 'current arrangement marker required')
        current += int(item['is_current'])
        require(item['verdict'] in ('SUPPORTED', 'CONDITIONAL', 'REJECTED', 'UNDETERMINED'), 'invalid candidate judgement')
        exact(item['comparison'], 'return_sources risk_overlap capital liquidity net_cost opportunity_cost rationale', 'candidate comparison')
        for finding in item['comparison'].values():
            text(finding, 'actual candidate comparison; explain irrelevance when appropriate')
        require(isinstance(item['dependencies'], list), 'candidate dependency list required')
    require(current == 1, 'one actual current arrangement required')
    require(isinstance(value['selected_ids'], list) and len(set(value['selected_ids'])) == len(value['selected_ids']), 'selected candidate ids required')
    selected = set(value['selected_ids'])
    require(selected <= ids, 'selection refers to unknown candidate')
    require((value['status'] == 'UNDETERMINED') == (not selected), 'undetermined net choice must not select an executable candidate')
    for item in value['candidates']:
        if item['id'] in selected:
            require(item['verdict'] in ('SUPPORTED', 'CONDITIONAL'), 'unsupported candidate cannot be selected')
    require(isinstance(value['gaps'], list), 'bounded investment gaps required')
    gap_ids = set()
    for gap in value['gaps']:
        exact(gap, 'id fact affected_candidates impact consequence required_evidence'+(' capabilities' if 'capabilities' in gap else ''), 'decision dependency gap')
        if 'capabilities' in gap:
            require(isinstance(gap['capabilities'],list) and gap['capabilities'] and all(isinstance(c,str) and c for c in gap['capabilities']), 'implementation gap capabilities required')
        for key in ('id', 'fact', 'consequence', 'required_evidence'):
            text(gap[key], 'decision gap ' + key)
        require(gap['id'] not in gap_ids, 'duplicate decision gap')
        gap_ids.add(gap['id'])
        require(gap['impact'] in ('QUANTITY_ONLY', 'ACTION', 'VALUE', 'FUNDING'), 'invalid dependency impact')
        require(isinstance(gap['affected_candidates'], list) and gap['affected_candidates'] and set(gap['affected_candidates']) <= ids, 'gap must limit specific candidate decisions')
        if gap['impact'] == 'ACTION':
            require(not (set(gap['affected_candidates']) & selected), 'action-level unknown cannot grant a selected action')
    for item in value['candidates']:
        require(set(item['dependencies']) <= gap_ids, 'candidate cites an unknown gap')
        for gap in value['gaps']:
            require((gap['id'] in item['dependencies']) == (item['id'] in gap['affected_candidates']), 'gap-to-decision dependencies disagree')
        require(item['verdict'] != 'UNDETERMINED' or item['dependencies'], 'undetermined candidate needs a concrete dependency')
    if revision >= 9:
        check_constraints(value, project)
    validate_implementation(value,project)
    for field in ('priorities', 'supported_judgements', 'review_conditions'):
        require(isinstance(value[field], list) and value[field], 'investment ' + field + ' must retain bounded choices')
        for item in value[field]:
            text(item, 'investment ' + field)
    validate_issues(value['issues'])
    require(isinstance(value['evaluation_windows'], list) and value['evaluation_windows'], 'prospective evaluation windows required')
    window_ids=set()
    for item in value['evaluation_windows']:
        exact(item, 'id question start end success_evidence invalidation_evidence' + (' process' if revision >= 9 else '') + (' boundary_refs' if 'boundary_refs' in item else ''), 'investment evaluation window')
        for key in ('id', 'question', 'success_evidence', 'invalidation_evidence'):
            text(item[key], 'evaluation ' + key)
        require(item['id'] not in window_ids, 'duplicate investment evaluation window')
        window_ids.add(item['id'])
        require(instant(value['decided_at']) <= instant(item['start']), 'evaluation cannot start before its decision')
        if revision >= 9:
            text(item['process'], 'mechanism-based evaluation progress')
            if item['end'] is not None:
                require(project is not None and item.get('boundary_refs'), 'dated evaluation needs its own confirmed boundary')
                source_refs(item['boundary_refs'], project, version)
                require(all(item['end'] in ref['quote'] for ref in item['boundary_refs']), 'old prediction date is not a new confirmed deadline')
                require(instant(item['start']) < instant(item['end']), 'evaluation deadline must follow formation')
        else:
            require(instant(item['start']) < instant(item['end']), 'evaluation window must be prospective at decision time')



def validate_implementation(value, project=None):
    for candidate in value['candidates']:
        info=candidate['validation']
        exact(info,'basis products record_ids required_capabilities limitations','candidate implementation evidence')
        require(info['basis'] in ('JUDGEMENT','METHOD_CHECK','RULE_EFFECTIVENESS','PORTFOLIO_EFFECTIVENESS'), 'invalid implementation evidence basis')
        require(isinstance(info['products'],list) and info['products'] and set(info['products'])<=PRODUCTS,'candidate product scope required')
        require(isinstance(info['record_ids'],list) and isinstance(info['required_capabilities'],list) and all(isinstance(c,str) and c for c in info['required_capabilities']),'candidate validation references required')
        require(isinstance(info['limitations'],list),'implementation limitations required')
        for limitation in info['limitations']:text(limitation,'implementation limitation')
        required=set(info['required_capabilities'])
        if set(info['products']) & {'LEVERAGED_DERIVATIVE','CROSS_MARGIN_DERIVATIVE'}:
            require({'MARGIN_SURVIVAL','FUNDING_COSTS'}<=required,'leveraged implementation must review margin survival and financing')
        if 'CROSS_MARGIN_DERIVATIVE' in info['products']:
            require('CROSS_MARGIN' in required,'cross-margin exposure cannot use isolated cash constraints')
        if info['basis']=='METHOD_CHECK' and set(info['products'])<={'CASH_SPOT'}:
            require({'CASH_FUNDING','DECLARED_EXECUTION_COSTS'}<=required, 'cash method check must retain implemented funding and cost capabilities')
        supported=set()
        if info['basis']=='JUDGEMENT':
            require(not info['record_ids'],'a judgement does not inherit simulated effectiveness')
        else:
            require(info['record_ids'],'validation basis requires actual stored evaluator records')
            if project is None:
                import re
                require(all(isinstance(i,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',i) for i in info['record_ids']),'invalid validation reference')
                # Pure disclosure preflight has no local owner access; final
                # publication resolves every record before any adoption.
                require(info['basis']=='METHOD_CHECK' and set(info['products'])<= {'CASH_SPOT'},'method-only cash validation cannot authorize effectiveness or leveraged products')
                supported={'CASH_FUNDING','DECLARED_EXECUTION_COSTS'}
            else:
                from .strategy_validation import validation_record
                for identity in info['record_ids']:
                    record,capabilities=validation_record(Path(project)/'.strategy/v4',identity)
                    require(instant(record['recorded_at'])<=instant(value['decided_at']),'validation evidence was not available at candidate evaluation')
                    require(set(info['products'])<=set(record['result']['product_scope']), 'cash spot validation cannot support a leveraged or different product candidate')
                    require(record['result']['status']=='SIMULATED_NOT_EFFECTIVENESS_VALIDATED', 'data-limited evaluator record is unfinished, not a completed method check')
                    supported|=capabilities
        if info['basis'] in ('RULE_EFFECTIVENESS','PORTFOLIO_EFFECTIVENESS'):
            require(info['basis'] in supported,'method simulation does not authorize adoption as an effective strategy')
        missing=required-supported
        if missing:
            limits=[g for g in value['gaps'] if candidate['id'] in g['affected_candidates']]
            bounded=set(c for g in limits for c in g.get('capabilities',[]))
            require(missing<=bounded and info['limitations'], 'unsupported implementation capability must limit the actual candidate through a concrete gap')
            if candidate['id'] in value['selected_ids']:
                require(candidate['verdict']=='CONDITIONAL','unverified implementation cannot be an unconditional selected arrangement')


def validate_outputs(value, recommendations):
    """Finished personal advice is constrained by the actual joint choice."""
    selected={c['id']:c for c in value['candidates'] if c['id'] in value['selected_ids']}
    def bounded(ids, ready):
        require(isinstance(ids,list) and ids and set(ids)<=selected.keys(),'decided advice must reference selected whole-portfolio candidates')
        if ready:
            require(not any(set(g['affected_candidates']) & set(ids) and (g['impact'] in ('ACTION','QUANTITY_ONLY','FUNDING') or g.get('capabilities')) for g in value['gaps']), 'implementation gap cannot produce execution-ready advice or tiers')
    def products(ids, actual):
        require(isinstance(actual,list) and actual and set(actual)<=PRODUCTS-{'UNKNOWN'}, 'decided advice requires its actual implementation products')
        covered=set(p for identity in ids for p in selected[identity]['validation']['products'])
        require(set(actual)<=covered, 'actual advice products must be covered by the selected candidate validation scope')
    if 'account_constraints' in value:
        roles = {c['id']: c['execution_role'] for c in value['candidates']}
        portfolio = recommendations['portfolio']
        advice = portfolio.get('strategy_advice', []) + (portfolio.get('risk_assessment') or {}).get('actions', [])
        for action in advice:
            if action['status'] != 'DECIDED': continue
            cited = action.get('investment_candidates', [])
            policy_only = (action['decision'] == 'ADJUST' and action.get('execution', {}).get('status') == 'NOT_APPLICABLE'
                           and action.get('implementation_products') == ['RESEARCH_OR_POLICY'])
            if action['decision'] in ('OPEN', 'INCREASE', 'REDUCE', 'EXIT', 'ADJUST') and not policy_only:
                require(cited and all(roles.get(i) != 'POLICY' for i in cited), 'policy cannot authorize an executable inventory action')
            if action['decision'] == 'HOLD':
                require(cited and all(roles.get(i) == 'INVENTORY_MAINTENANCE' for i in cited),
                        'policy use or unknown coverage cannot disguise actual HOLD')
    for side in ('upper_short','lower_spot'):
        rec=recommendations[side]
        ids=rec.get('investment_candidates',[])
        if rec['status'] in ('RECOMMENDED','EXECUTION_LIMITED'):
            bounded(ids,rec['status']=='RECOMMENDED')
            if side=='lower_spot':
                products(ids,['CASH_SPOT'])
            else:
                require(any(set(selected[identity]['validation']['products']) & {'LEVERAGED_DERIVATIVE','CROSS_MARGIN_DERIVATIVE'} for identity in ids), 'short advice requires a selected derivative candidate; cash spot or research cannot authorize short implementation')
    portfolio=recommendations['portfolio']
    for action in portfolio['strategy_advice']+portfolio['risk_assessment']['actions']:
        if action['status']=='DECIDED':
            ids=action.get('investment_candidates',[])
            bounded(ids,action['execution']['status']=='READY')
            products(ids,action.get('implementation_products'))
            if 'RESEARCH_OR_POLICY' in action['implementation_products']:
                require(set(action['implementation_products'])=={'RESEARCH_OR_POLICY'} and action['decision'] in ('PAUSE','RESUME','ADJUST') and action['execution']['status']=='NOT_APPLICABLE', 'research or policy candidate cannot authorize a trading action or claim execution readiness')


def validation_limits(value):
    result=[]
    for candidate in value['candidates']:
        info=candidate['validation']; limitations=deepcopy(info['limitations'])
        if info['basis']=='METHOD_CHECK':
            limitations.append('Completed cash spot method check only; it does not validate derivative protection, strategy effectiveness or real portfolio returns.')
        if limitations or info['required_capabilities']:
            result.append({'candidate_id':candidate['id'],'products':deepcopy(info['products']),
                'basis':info['basis'],'limitations':limitations,
                'unsupported_capabilities':sorted(set(info['required_capabilities'])-{'CASH_FUNDING','DECLARED_EXECUTION_COSTS'}
                    if info['basis']=='METHOD_CHECK' else set(info['required_capabilities']))})
    return result


def validate_issues(items):
    require(isinstance(items, list), 'investment issues required')
    ids = set()
    for item in items:
        exact(item, 'id scope question disposition explanation next_evidence successor', 'investment issue')
        for key in ('id', 'scope', 'question', 'explanation'):
            text(item[key], 'investment issue ' + key)
        require(item['id'] not in ids, 'duplicate investment issue')
        ids.add(item['id'])
        require(item['disposition'] in ('RESOLVED', 'REPLACED', 'PENDING_FACTS', 'NEEDS_RESEARCH', 'NOT_APPLICABLE'), 'invalid issue disposition')
        if item['disposition'] in ('PENDING_FACTS', 'NEEDS_RESEARCH'):
            text(item['next_evidence'], 'open issue discriminating evidence')
        else:
            require(item['next_evidence'] is None, 'closed issue cannot pretend a pending outcome')
        if item['disposition'] == 'REPLACED':
            text(item['successor'], 'replacement question identity')
            require(item['successor'] != item['id'], 'issue cannot replace itself')
        else:
            require(item['successor'] is None, 'only replaced issues have successors')
    by_id={item['id']:item for item in items}
    for item in items:
        seen=set()
        while item['disposition']=='REPLACED':
            require(item['successor'] in by_id, 'replaced issue must point to an actual successor issue')
            require(item['id'] not in seen, 'replacement issue chain cannot cycle')
            seen.add(item['id']);item=by_id[item['successor']]


def open_issues(assessment):
    formed = decision(assessment)
    if formed:
        return [deepcopy(item) for item in formed['issues'] if item['disposition'] in ('PENDING_FACTS', 'NEEDS_RESEARCH')]
    portfolio = ((assessment or {}).get('recommendations') or {}).get('portfolio', {})
    result = [{'id':'legacy:portfolio-synthesis', 'scope':'whole portfolio',
               'question':'Complete the joint investment choice from policy, market, risk and opportunities.',
               'disposition':'NEEDS_RESEARCH', 'explanation':'Legacy inputs are not a completed revision 8 investment decision.',
               'next_evidence':'Actual candidate comparison and bounded whole-portfolio judgement.', 'successor':None}]
    risk = portfolio.get('risk_assessment')
    if risk and risk.get('response_needed') is None:
        result.append({'id':'legacy:risk:' + digest({'scope':'portfolio risk','question':risk['summary']})[7:], 'scope':'portfolio risk', 'question':risk['summary'],
                       'disposition':'PENDING_FACTS', 'explanation':risk['response_reason'],
                       'next_evidence':'; '.join(risk['limitations']), 'successor':None})
    for action in (portfolio.get('allocation_decision') or {}).get('actions',[]):
        if action.get('status')=='UNDETERMINED' or action.get('execution',{}).get('status')=='LIMITED':
            result.append({'id':'legacy:allocation:'+action['id'],'scope':action['scope'],'question':action['arrangement'],
                           'disposition':'PENDING_FACTS','explanation':'The earlier allocation remains unresolved or implementation-limited.',
                           'next_evidence':'; '.join(action['execution']['limits']),'successor':None})
    for side in ('upper_short','lower_spot'):
        proposal=((assessment or {}).get('recommendations') or {}).get(side)
        if proposal and proposal.get('status') in ('UNDETERMINED','EXECUTION_LIMITED'):
            result.append({'id':'legacy:side:'+side,'scope':side,'question':proposal['rationale'],
                           'disposition':'PENDING_FACTS','explanation':proposal['impact'],
                           'next_evidence':'; '.join(proposal['validity']['conditions']),'successor':None})
    for action in (risk or {}).get('actions',[]):
        if action.get('status')=='UNDETERMINED' or action.get('execution',{}).get('status')=='LIMITED':
            result.append({'id':'legacy:risk-action:'+action['id'],'scope':action['scope'],'question':action['arrangement'],
                           'disposition':'PENDING_FACTS','explanation':'Earlier risk response remains unresolved or implementation-limited.',
                           'next_evidence':'; '.join(action['execution']['limits']),'successor':None})
    for advice in portfolio.get('strategy_advice', []):
        if advice.get('status') == 'UNDETERMINED' or advice.get('execution',{}).get('status')=='LIMITED':
            result.append({'id':'legacy:advice:' + advice['id'], 'scope':advice['scope'], 'question':advice['arrangement'],
                           'disposition':'PENDING_FACTS', 'explanation':advice['arrangement'],
                           'next_evidence':'; '.join(advice['execution']['limits']), 'successor':None})
    return result


def validate_issue_review(items, assessment, main_required):
    validate_issues(items)
    expected = {item['id']:item for item in open_issues(assessment)}
    actual = {item['id']:item for item in items}
    require(expected.keys() <= actual.keys(), 'every prior open issue requires an actual disposition')
    for key, old in expected.items():
        item = actual[key]
        require(item['question'] == old['question'] and item['scope'] == old['scope'], 'issue identity or question changed')
        if old['disposition'] == 'NEEDS_RESEARCH' and item['disposition'] != 'NOT_APPLICABLE':
            require(main_required, 'a missing investment judgement needs upstream completion, not a closed observation')
        if item['disposition'] in ('RESOLVED', 'REPLACED'):
            require(main_required, 'resolved or replaced personal decisions require formal adoption review')
    require(main_required or not any(i['disposition'] == 'NEEDS_RESEARCH' for i in items), 'unfinished analysis must return upstream')


def finished(value):
    if value is None:
        return None
    result = {k:deepcopy(value[k]) for k in ('status', 'summary', 'priorities', 'supported_judgements', 'gaps', 'issues', 'evaluation_windows', 'review_conditions', 'decided_at', 'valid_until')}
    result['validation_limits']=validation_limits(value)
    return result


def policy_inputs(root, version):
    from .common import read
    values=[]
    for path in (Path(root)/'investment/policies').glob('*.json'):
        item=read(path)
        require(item['version']=='sha256:'+path.stem and item['version']==digest({k:v for k,v in item.items() if k!='version'}),'recorded policy damaged')
        if item['fact_version']==version:values.append(item)
    return sorted(values,key=lambda v:instant(v['recorded_at']))


def record_policy(root, project, spec):
    """Record policy before candidate comparison; timestamp comes from execution."""
    from .common import create, locked, now
    exact(spec, 'objectives constraints baseline unknowns source_refs', 'confirmed policy input')
    value={'recorded_at':now(),'fact_version':facts(project),**deepcopy(spec)}
    value['version']=digest(value)
    policy(value,value['fact_version'],value['recorded_at'])
    source_refs(value['source_refs'],project,value['fact_version'])
    with locked(root):
        require(facts(project)==value['fact_version'],'confirmed policy facts changed before recording')
        path=Path(root)/'investment/policies'/(value['version'][7:]+'.json')
        if not path.exists():create(path,value)
    return {'policy_version':value['version'],'record':str(path),'policy':value,'adoption_status':'INPUT_ONLY_NOT_A_TRADE_OR_DECISION'}


def check_constraints(value, project):
    c = value['account_constraints']
    exact(c, 'fact_version record_id exposure_complete cross_margin_complete funding_complete', 'actual account constraints')
    require(c['fact_version'] == value['fact_version'], 'account constraints bind different confirmed facts')
    for k in ('exposure_complete','cross_margin_complete','funding_complete'):
        require(c[k] is None or type(c[k]) is bool, 'account constraint is unknown or confirmed')
    if project is not None:
        from .portfolio_ledger import constraints
        expected = constraints(Path(project)/'.strategy/v4', project, value['decided_at'])
        require(c == expected, 'account completeness cannot be self-certified from targets or partial old snapshots')
    else:
        require(not any(c[k] is True for k in ('exposure_complete','cross_margin_complete','funding_complete')),
                'missing confirmed owner cannot prove current account coverage')
    selected = set(value['selected_ids'])
    for item in value['candidates']:
        role = item['execution_role']
        require(role in ('INVENTORY_MAINTENANCE','INVENTORY_ADJUSTMENT','INCREMENTAL','POLICY','INDEPENDENT_RISK_RESPONSE'), 'candidate must distinguish use from actual inventory action')
        if role == 'POLICY':
            require(item['validation']['products'] == ['RESEARCH_OR_POLICY'], 'policy candidates cannot disguise inventory or funded execution')
        if item['id'] not in selected: continue
        if role in ('INVENTORY_MAINTENANCE','INVENTORY_ADJUSTMENT'):
            require(c['exposure_complete'] is True, 'unknown full exposure may change which side must be reduced; no default maintenance')
            if 'CROSS_MARGIN_DERIVATIVE' in item['validation']['products']:
                require(c['cross_margin_complete'] is True, 'unknown cross-margin state changes inventory action, not only amount')
        if role == 'INCREMENTAL':
            require(c['funding_complete'] is True, 'planned capital is not confirmed deployable funding')
        if role == 'INDEPENDENT_RISK_RESPONSE':
            require(item['validation']['limitations'], 'independent risk response must retain its concrete evidence and scope limits')
