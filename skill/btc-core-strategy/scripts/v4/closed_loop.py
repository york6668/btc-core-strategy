"""Revision 6/7 contracts. Models assess risk; checks protect ownership and lineage.

No market scoring, Markdown parsing, account inference or automatic migration.
Older immutable assessments/observations continue through their original contracts.
"""
from copy import deepcopy

from .common import digest, exact, instant, require, text
from .judgements import LIFECYCLE_REVIEW, REPORT_FIDELITY_REVIEW

REVISION = 7
OUTCOMES = ('VALID', 'REVISE', 'EVIDENCE_LIMITED')
SCOPES = ('MARKET', 'PORTFOLIO')
MARKET_INSTRUCTIONS = (
    '仅完成公开市场研究：同一公开证据、尺度、方法与截止下，方向、周期、动量衰减与反向趋势、主证据链、公开现价价值和验证关系独立于个人持仓、成本、偏好和执行能力。'
    '先形成机制、应有与实际价格响应、竞争解释和反证裁决，再形成当前周期及现价参与价值；方向、归因、延续和价值各由自身依据支持。'
    '市场价值、具体产品身份与报价、个人数量及执行分别判断；公开产品净成本缺口可限制该产品价值，但个人敞口未知不能撤销底层市场方向或公开价值分析。'
    '历史观点和已送达报告只用于比较，不作公开事实；混合稿中的个人安排不能回到市场论证。保持同尺度拐点、双向风险及条件/失效关系，证据变化或实际分析错误可以修正结论并说明原因。'
    '公开阶段不完成个人组合风险或动作；形成完整独立市场命题及依据，后续报告可调整表达但不能由个人输入改变命题。'
)
INSTRUCTIONS = (
    '完整闭环以持续识别和验证周期主线为主干：先形成独立市场论证与现价取舍，再结合实际持仓形成参与、防守、等待及改变条件的个人建议；风险评估服务这些取舍。'
    '公开事实、历史假设、个人事实分开；先从原始公开证据研判，再检验历史观点，历史预测和候选价不自证。'
    'rationale、risk_assessment.basis与strategy_advice.decision承接有据判断及其限制；未证实假设不能作为无条件规划前提，不能因写了“可能”就据此生成动作。'
    '条件情景在forward.conditions/invalidations及review_conditions中说明所需可观察证据、失效依据与原检验窗口；建议validity沿原稳定概念，由rationale解释其实际条件。区分尚待观察、已支持、已失败或缺证；触发后由模型核验，不自动转成交易。'
    '同一事实可以支持有边界判断，但历史买入、容量、趋势或未知回报不能直接推出当前投资价值及个人动作；推断不足只暂缓依赖它的决定，不撤销其他有独立依据的风险评估。'
    '研究覆盖周期、价格结构、资金与融资、盈利、未来价值和当前定价；内部完成冲突取舍。'
    '观察窗口、判断期限、预期兑现窗口分别记录；期限届满检查兑现，不随报告时钟续期。'
    '冻结市场结果后才读取个人材料；组合和成稿不能改写它，需要修正返回市场研究。'
    'revision 7的recommendations.portfolio.risk_assessment独立交付资产风险结论；总资金、占用比只是可选依据，不是公式、评分或自动调仓条件。'
    'response_needed由模型综合判断为true、false或null，并说明response_reason；风险迹象、短线偏弱、配置比例偏离或账户缺数不自动等于需要操作。'
    'actions可为空；仅response_needed=true时可补充有依据的风险应对建议。即使需要应对，操作本身缺乏依据时仍可留空并说明限制，不强行生成批次减半或条件链。'
    '结合资产特点、持有目的、市场判断和已确认组合事实判断实际影响；必要敞口、保护和资金检查在内部完成，不以材料缺失推断安全。'
    '原有定投、折价买入和其他机会分析继续按各自策略形成建议，不受个人风险应对触发条件替代；不能借机会分析包装无风险依据的减仓。'
    'upper_short/lower_spot保留原价量接口；其他已形成的参与或等待建议放在portfolio.strategy_advice（action结构，可为空），不以response_needed为前提。空数组必须来自实际比较，不能由账户未知或到期替代分析。'
    '五模块是内部覆盖，不是正文栏目；现货加减、合约开平和四小时区域是问题示例，不限定研究或动作范围。'
    '数量未知保留有依据的方向并标明执行受限；影响动作本身的缺口明确未决，不能默认持有安全。'
    '减仓、退出、暂停同时维护恢复参与条件，现金、保证金、敞口及保护不重复计算。'
    '市场变化先改市场再复核配置；个人变化只复核组合；重大遗漏、累计反证、前瞻落空回对应研究。'
    '只有表达变化则修稿；送达、触价、建议均非成交，资产事实只由用户确认更新。'
    '先完成有公开依据的分歧区、下跌路径、回调参与价值和失效条件，再分别说明现货与空头的含义。账户缺口只约束依赖它的规模、覆盖或动作，不阻塞独立市场判断；结构风险区不能假装价值区，未形成价值区仍完成可支持的价格风险分析。'
    '内部到期触发当前实质复核，保留旧期限和历史；不能以到期或未知结案、自动续期或默认安全。解释原论点仍支持什么、哪些分项可决定、哪些缺口影响哪个决定，必要正式修订沿原研究审阅。'
    'allocation_review.material及maintenance触发结果的material表示对用户尚未交付的实质主线、现价参与价值或实际组合安全影响，不是资料、时钟或内部风险字段变化。先完成分析，再对照最后真实正文判断通知；内部REVISE不自动等于REPORT。'
)

# Report authors consume assessed results. Research instructions belong to the
# preceding stages and must not re-enter the report through result_instructions.
REPORT_INSTRUCTIONS = (
    '成稿承接market_result、portfolio_risk、strategy_advice和applicability中的已形成结果；旧版本仍按原allocation_decision恢复，不冒充新版风险评估。'
    '开头从market_result承接独立公开结论；重要市场主题继续交付方向、支持程度、同尺度拐点、主线及公开价值，个人应用在其后明确映射。总体个人取舍不作为市场总裁决或市场依据，不能用已有仓位、成本、保护目标或执行缺口支持或撤销公共判断。不固定条数或栏目。周期不是天数，不以局部事件或短线剩余期限替代主结论。'
    '普通日内变化没有改变周期判断、重要风险或安排时留在内部；重大即时风险仍及时反馈并说明影响范围。缺少周期分析返回原研究，不在成稿延长小时结论。'
    '市场呈现主论点、决定性证据及反证裁决，个人部分承接现价下参与、防守和等待的有据取舍及现货、空头各自含义；不以风险变化组织另一份报告，不要求风险需要应对才给有用建议。'
    '没有操作建议不等于缺少评估；不因配置比例、短线偏弱或未知信息生成操作清单。比例、数量和操作顺序只在依据充分且必要时呈现。'
    'market_result中已形成且影响当前取舍的现价吸引力与美债等待比较属于综合结果，正文保留补偿是否充分或仍无法确认、主要原因和改变条件；方向与笼统资金成本不能替代这一判断。缺少量化依据时承接已有定性结论和具体限制，不在成稿补算。'
    '按结果的实际含义取舍表达，不逐项转述内部字段、来源核验、未知清单或维护记录；applicability限制当前安排，不要求照抄其explanation中的全部规则。'
    '详细理由集中说明；最后收束若概括个人安排，明确其适用个人事实及市场前提，不将个人等待写成整体市场价值不足。逐句删并保留各自决定及未决影响；历史reported.display只用于已交付含义比较，不从旧个人动作恢复当前建议。'
    '数量受限与判断未决分别说明实际影响；缺少新版风险评估不证明旧安排有效或安全，也不取消已有公开依据的区域、价格风险和条件价值分析。到期作为内部复核依据，正文说明本轮重新分析的实际结论，只有旧可执行建议失效确会误导时才简明纠正。'
    '重要风险、建议有效性、恢复参与条件及事实纠错忠实保留，不提高确认程度或补造交易价。'
    '成稿不重新研究或改写结果；发现影响判断的分析缺口，返回对应研究，表达问题在冻结前修正。'
    '研究和维护材料继续完整保存；来源绑定与逐字交付不改变正文，也不代表实际成交。'
)

from .market_state import CYCLE_EXPRESSION_REVIEW, MOMENTUM_REPORT_REVIEW, EVIDENCE_REPORT_REVIEW, CYCLE_THESIS_REVIEW
MARKET_INSTRUCTIONS += LIFECYCLE_REVIEW
INSTRUCTIONS += CYCLE_THESIS_REVIEW
REPORT_INSTRUCTIONS += REPORT_FIDELITY_REVIEW + CYCLE_EXPRESSION_REVIEW + MOMENTUM_REPORT_REVIEW + EVIDENCE_REPORT_REVIEW
from .investment import POLICY_INSTRUCTIONS
INSTRUCTIONS += POLICY_INSTRUCTIONS
REPORT_INSTRUCTIONS += (
    'validation_limits与gaps限制对应产品和安排；METHOD_CHECK只证明模拟方法，不得在正文写成杠杆组合、真实收益或策略有效。未支持能力影响动作时不输出执行建议，影响数量时保留有据方向和实际执行限制。'
    'revision 8报告消费investment_decision的总体取舍、优先关系、有据分项判断和具体依赖，不把风险与分项机会并列未决当作完整组合决定。'
    '核对公开方向与拐点依据、现价吸引力、策略适用性、总体选择及报告传递是否完整；实际分析缺项返回上游，不能用新增自审字段或已审查字样代替。'
)
MARKET_INSTRUCTIONS += (
    '宏观研究沿机制解释相关资产前景和市场已定价部分，比较主导与竞争解释；方向、当前价格吸引力和策略适用性分别判断。'
    '延续、动量衰减、反转迹象与反转确认分别由证据支持，不固定4小时、不要求ETF或慢宏观变量一票前置，不以单次价格或费率确定反转。'
)


def strings(value, name, nonempty=False):
    require(isinstance(value, list) and (value or not nonempty), name + ' must be a list')
    for item in value:
        text(item, name)


def window(value, name):
    exact(value, 'start end', name)
    require(instant(value['start']) <= instant(value['end']), name + ' is reversed')


def validate_forward(value, cutoff, resolve, prior=None, revision=6, lifecycle_root=None):
    exact(value, 'observed_window judgement_window realization_window timing_basis conditions invalidations prior_check' + (' lifecycle' if revision >= 9 else ''), 'forward assessment')
    window(value['observed_window'], 'observed_window')
    if revision >= 9 and value['judgement_window']['end'] is None:
        exact(value['judgement_window'], 'start end', 'mechanism judgement window')
        instant(value['judgement_window']['start'])
    else:
        window(value['judgement_window'], 'judgement_window')
    require(instant(value['observed_window']['end']) <= instant(cutoff), 'forward evidence exceeds cutoff')
    # Judgement may already be expired during delayed/recovery work; it must be
    # checked, never silently moved into the future to pass a publication check.
    if value['realization_window'] is not None:
        window(value['realization_window'], 'realization_window')
    text(value['timing_basis'], 'realization timing basis or explicit timing gap')
    strings(value['conditions'], 'forward conditions', True)
    strings(value['invalidations'], 'forward invalidations', True)
    checked = value['prior_check']
    exact(checked, 'outcome explanation refs', 'prior forward check')
    require(checked['outcome'] in ('INITIAL', 'PENDING', 'REALIZED', 'FAILED', 'REVISE', 'EVIDENCE_LIMITED'), 'invalid forward outcome')
    text(checked['explanation'], 'prior thesis and cumulative counterevidence assessment')
    require(isinstance(checked['refs'], list), 'forward check refs required')
    for ref in checked['refs']:
        resolve(ref)
    old = (prior or {}).get('forward')
    due = [value['judgement_window']]
    if value['realization_window'] is not None:
        due.append(value['realization_window'])
    if old:
        due += [old['judgement_window']]
        if old['realization_window'] is not None:
            due.append(old['realization_window'])
        require(checked['outcome'] != 'INITIAL', 'existing forward thesis requires an actual outcome check')
    if any(w['end'] is not None and instant(w['end']) <= instant(cutoff) for w in due):
        require(checked['outcome'] not in ('INITIAL', 'PENDING'), 'expired forward window requires realization review, not automatic renewal')
    if revision >= 9:
        from . import forward_lifecycle
        life = value['lifecycle']
        result = forward_lifecycle.validate(life, cutoff, resolve, prior,
            getattr(resolve, 'fact', None), lifecycle_root)
        c = life['claim']
        require(value['judgement_window']['end'] == c['scale']['end'], 'forward window differs from its own mechanism scale')
        if value['realization_window'] is not None:
            require(c['scale']['end_ref'] is not None and value['realization_window']['end'] == c['scale']['end']
                    and instant(value['realization_window']['start']) >= instant(c['formed_at']),
                    'realization window needs the new claim own evidenced calendar, not an inherited forecast date')
        previous_claim = ((old or {}).get('lifecycle') or {}).get('claim')
        if previous_claim is None or previous_claim['id'] != c['id']:
            require(instant(value['judgement_window']['start']) >= instant(c['formed_at']),
                    'new expectation cannot inherit a historical prediction start')
        if result['falsified_prior']:
            require(checked['outcome'] in ('FAILED', 'REVISE'), 'falsified prior must be corrected or withdrawn, not remain pending')
        if checked['outcome'] == 'REALIZED':
            outcomes = result['verification']
            require(result['complete_prior_support'] and outcomes and all(v['verdict'] == 'SUPPORTED' for v in outcomes),
                    'process completion or partial review cannot declare forecast realized')
        return result
    return {'status': 'POSTHOC_ONLY', 'prospective_support': False,
            'reason': 'Legacy fields remain readable; no first registered conditions or later mechanism proof.'}



def allocation(assessment):
    return ((assessment or {}).get('recommendations') or {}).get('portfolio', {}).get('allocation_decision')


def risk(assessment):
    return ((assessment or {}).get('recommendations') or {}).get('portfolio', {}).get('risk_assessment')


def result_keys(revision):
    return ('portfolio_risk', 'portfolio_risk_hash') if revision >= 7 else ('allocation_decision', 'allocation_decision_hash')


def public_hash(assessment):
    from .market_state import public_content
    value = public_content(assessment)
    require(value is not None, 'allocation needs an independently frozen market conclusion')
    return digest(value)


def validate_allocation(value, cutoff, market_hash=None, fact_version=None):
    exact(value, 'status summary actions execution_limits market_result_hash fact_version decided_at valid_until review_conditions', 'allocation decision')
    require(value['status'] in ('DECIDED', 'PARTIAL', 'UNDETERMINED'), 'invalid allocation decision status')
    text(value['summary'], 'final whole-portfolio arrangement')
    require(max(instant(value['decided_at']), instant(cutoff)) < instant(value['valid_until']), 'allocation needs a current, explicitly reviewed validity window')
    require(isinstance(value['market_result_hash'], str) and value['market_result_hash'].startswith('sha256:'), 'market result binding required')
    require(isinstance(value['fact_version'], dict) and value['fact_version'], 'local fact version binding required')
    if market_hash is not None:
        require(value['market_result_hash'] == market_hash, 'allocation binds a different frozen market result')
    if fact_version is not None:
        require(value['fact_version'] == fact_version, 'allocation binds different local facts')
    strings(value['execution_limits'], 'portfolio execution limits')
    strings(value['review_conditions'], 'allocation review conditions', True)
    require(isinstance(value['actions'], list) and value['actions'], 'allocation needs final arrangements or explicit undecided scope')
    statuses = validate_actions(value['actions'])
    expected = 'PARTIAL' if len(statuses) > 1 else next(iter(statuses))
    require(value['status'] == expected, 'overall allocation status contradicts its actual decisions')


def validate_actions(actions):
    require(isinstance(actions, list), 'risk responses must be a list')
    ids, statuses = set(), set()
    for action in actions:
        exact(action, 'id scope status decision arrangement gap_impact execution reentry_conditions'+(' investment_candidates' if 'investment_candidates' in action else '')+(' implementation_products' if 'implementation_products' in action else ''), 'allocation action')
        for key in ('id', 'scope', 'arrangement'):
            text(action[key], 'allocation ' + key)
        require(action['id'] not in ids, 'duplicate allocation action')
        ids.add(action['id']); statuses.add(action['status'])
        require(action['status'] in ('DECIDED', 'UNDETERMINED'), 'invalid action status')
        require(action['decision'] in ('INCREASE', 'REDUCE', 'HOLD', 'EXIT', 'OPEN', 'CLOSE', 'PAUSE', 'RESUME', 'ADJUST', 'UNDETERMINED'), 'invalid allocation action')
        require(action['gap_impact'] in ('NONE', 'QUANTITY_ONLY', 'ACTION'), 'invalid gap impact')
        unresolved = action['status'] == 'UNDETERMINED'
        require(unresolved == (action['decision'] == 'UNDETERMINED') == (action['gap_impact'] == 'ACTION'), 'action-level gap cannot become assumed hold or decided action')
        execution = action['execution']
        exact(execution, 'status limits', 'allocation execution')
        require(execution['status'] in ('READY', 'LIMITED', 'UNDETERMINED', 'NOT_APPLICABLE'), 'invalid execution status')
        require(unresolved == (execution['status'] == 'UNDETERMINED'), 'unknown action cannot claim execution readiness')
        require(action['gap_impact'] != 'QUANTITY_ONLY' or execution['status'] == 'LIMITED', 'unknown quantity preserves direction but limits execution')
        strings(execution['limits'], 'action execution limits', execution['status'] in ('LIMITED', 'UNDETERMINED'))
        strings(action['reentry_conditions'], 'restore participation conditions', action['decision'] in ('REDUCE', 'EXIT', 'CLOSE', 'PAUSE'))
    return statuses


def validate_risk(value, cutoff, market_hash=None, fact_version=None):
    exact(value, 'summary basis response_needed response_reason actions limitations market_result_hash fact_version assessed_at valid_until review_conditions', 'portfolio risk assessment')
    for field in ('summary', 'basis', 'response_reason'):
        text(value[field], 'asset risk ' + field)
    require(value['response_needed'] is None or type(value['response_needed']) is bool, 'risk response need must be true, false or null')
    validate_actions(value['actions'])
    require(not value['actions'] or value['response_needed'] is True, 'risk advice requires an assessed need to respond')
    strings(value['limitations'], 'risk assessment limitations', value['response_needed'] is None)
    require(max(instant(value['assessed_at']), instant(cutoff)) < instant(value['valid_until']), 'risk assessment needs an explicitly reviewed validity window')
    require(isinstance(value['market_result_hash'], str) and value['market_result_hash'].startswith('sha256:'), 'risk market binding required')
    require(isinstance(value['fact_version'], dict) and value['fact_version'], 'risk local fact binding required')
    if any(v is None for v in value['fact_version'].values()):
        require(value['limitations'], 'unavailable personal files need explicit bounded risk limitations')
    if market_hash is not None:
        require(value['market_result_hash'] == market_hash, 'risk assessment binds another market result')
    if fact_version is not None:
        require(value['fact_version'] == fact_version, 'risk assessment binds different local facts')
    strings(value['review_conditions'], 'risk review conditions', True)


def review_template(revision=6):
    return {'market_result_hash': None, result_keys(revision)[1]: None, 'fact_version': None,
            'outcome': None, 'explanation': None, 'limitations': [], 'material': None,
            **({'strategy_advice_hash': None} if revision >= 7 else {}),
            **({'investment_decision_hash': None, 'issue_reviews': []} if revision >= 8 else {})}


def validate_allocation_review(value, ctx, market_hash, main_required=False):
    revision = ctx.get('revision', 6)
    binding = result_keys(revision)[1]
    exact(value, ' '.join(review_template(revision)), 'portfolio applicability review')
    assessment = {'recommendations': ctx.get('current_recommendations')}
    current = risk(assessment) if revision >= 7 else allocation(assessment)
    if revision >= 7:
        advice = (ctx.get('current_recommendations') or {}).get('portfolio', {}).get('strategy_advice') if current else None
        require(value['strategy_advice_hash'] == (digest(advice) if advice is not None else None), 'portfolio review binds different opportunity advice')
    if revision >= 8:
        from .investment import decision, validate_issue_review
        whole = decision(assessment)
        require(value['investment_decision_hash'] == (digest(whole) if whole is not None else None), 'portfolio review binds another investment decision')
        validate_issue_review(value['issue_reviews'], assessment, main_required)
    require(value['market_result_hash'] == market_hash, 'portfolio review changed its frozen market result')
    require(value[binding] == (digest(current) if current else None), 'portfolio review binds another assessed result')
    require(value['fact_version'] == ctx['identity']['fact_version'], 'portfolio review facts changed')
    require(value['outcome'] in OUTCOMES, 'invalid portfolio review outcome')
    require(type(value['material']) is bool, 'portfolio review needs materiality judgement')
    text(value['explanation'], 'portfolio applicability reasoning')
    strings(value['limitations'], 'portfolio applicability limits', value['outcome'] == 'EVIDENCE_LIMITED')
    if any(v is None for v in ctx['identity']['fact_version'].values()):
        require(value['outcome'] != 'VALID' and value['limitations'],
                'unavailable personal files require bounded portfolio limitations, not confirmed safety')
    if value['outcome'] == 'VALID':
        if revision >= 8:
            require(whole is not None and instant(whole['valid_until']) > instant(ctx['factor_context']['evidence']['cutoff']), 'missing or expired whole investment decision cannot become VALID')
        require(current is not None, 'legacy content cannot manufacture a current portfolio assessment')
        require(current['fact_version'] == value['fact_version'], 'changed facts require an actual allocation reassessment')
        require(instant(current['valid_until']) > instant(ctx['factor_context']['evidence']['cutoff']), 'expired allocation requires reassessment')


def report_inputs(ctx):
    """A finished-result interface, never the five-module analysis or mixed prose."""
    strategy = ctx['current_strategy']
    observation = ctx.get('observation') or {}
    # Publication can advance the decision before its next observation exists.
    decision_id = ctx['identity'].get('decision_id')
    matched = (isinstance(decision_id, str) and bool(decision_id)
               and observation.get('baseline_decision_id') == decision_id)
    review = (observation.get('review') or {}) if matched else {}
    market = {k: deepcopy(review[k]) for k in ('market_assessment', 'dispute_zones') if k in review}
    if not market:
        market = {k: deepcopy(strategy.get('outlook', {}).get(k)) for k in ('market_assessment', 'dispute_zones')}
    current_contract = ctx.get('report_contract_revision', 1) >= 2
    if current_contract:
        basis = ctx.get('cycle_baseline') or {}
        # Only the verified pure-market research report crosses this boundary.
        # Keep its original date; it is the argument being checked, not a new
        # current conclusion and not mixed personal research prose.
        market['research_basis'] = {
            'result_id': basis.get('result_id'), 'market_cutoff': basis.get('market_cutoff'),
            'market_report': basis.get('market_report'),
            'factor_assessment': deepcopy(strategy.get('factor_assessment'))}
    revision = ctx.get('market_revision', strategy.get('revision', 6))
    if ctx.get('report_contract_revision', 1) >= 6:
        # Carry the completed argument verbatim, under its real adopted owner.
        # An hourly overlay is a separately dated check, not a fresh authorship
        # of the adopted semantic outlook. Never reconstruct it from prose.
        provenance = (strategy.get('section_provenance') or {}).get('outlook') or {}
        market['research_basis'].update(
            decision_id=decision_id,
            evidence_id=provenance.get('evidence_id', strategy.get('evidence_id')),
            assessed_at=provenance.get('assessed_at', strategy.get('knowledge_cutoff')),
            section_provenance=deepcopy(provenance),
            outlook=deepcopy(strategy.get('outlook')))
        market['observation_basis'] = {
            'observation_id': observation.get('observation_id') if matched else None,
            'baseline_decision_id': observation.get('baseline_decision_id') if matched else None,
            'market_result_hash': review.get('market_result_hash') if matched else None,
            'applicability_review': deepcopy(review.get('applicability_review')) if matched else None}
    result_key, binding = result_keys(revision)
    decision = risk(strategy) if revision >= 7 else allocation(strategy)
    personal = deepcopy(review.get('allocation_review'))
    advice = strategy['recommendations']['portfolio'].get('strategy_advice') if revision >= 7 and decision else None
    if personal and (binding not in personal or personal[binding] != (digest(decision) if decision else None)):
        personal = None  # A just-published arrangement needs its own applicability check.
    if revision >= 7 and personal and personal.get('strategy_advice_hash') != (digest(advice) if advice is not None else None):
        personal = None
    if current_contract and personal and personal.get('fact_version') != ctx['identity']['fact_version']:
        personal = None  # A prior/other fact review is not a current reassessment.
    result = {'market_result': market, result_key: deepcopy(decision),
              'execution_plans': {s: {k: deepcopy(strategy['recommendations'][s][k]) for k in
                  ('status', 'unit', 'tiers', 'validity', 'allocation_percent')} for s in ('upper_short', 'lower_spot')},
              'applicability': {'market': deepcopy(review.get('applicability_review')) or {
                                    'outcome': 'EVIDENCE_LIMITED', 'limitations': ['Current decision has no matching observation review.']},
                                'portfolio': personal or {'outcome': 'EVIDENCE_LIMITED', 'limitations': ['Current allocation has not been reviewed in this observation.']}}}
    if revision >= 7 and decision is None:
        result['applicability']['portfolio'] = {'outcome': 'EVIDENCE_LIMITED', 'limitations': ['A revision 7 asset risk assessment has not been formed; legacy actions are not a new risk conclusion.']}
    if decision and decision['fact_version'] != ctx['identity']['fact_version']:
        changed = {'outcome': 'REVISE', 'limitations': ['Local asset facts changed; the previous arrangement is not a current decision.']}
        if current_contract and personal:
            # The old arrangement is invalid on new facts, but a matching
            # current-fact review can still carry useful bounded analysis.
            actual = deepcopy(result['applicability']['portfolio'])
            actual['outcome'] = 'REVISE'
            actual['limitations'] = actual.get('limitations', []) + changed['limitations']
            result['applicability']['portfolio'] = actual
        else:
            result['applicability']['portfolio'] = changed
    if decision and instant(decision['valid_until']) <= instant(ctx['evaluated_at']):
        expired = {'outcome': 'REVISE', 'limitations': ['Allocation expired; the report clock cannot extend it.']}
        if current_contract:
            # Preserve the actual current review and its bounded conclusions.
            # Expiry restricts the old advice; it must not erase new analysis.
            actual = deepcopy(result['applicability']['portfolio'])
            actual['outcome'] = 'REVISE'
            actual['limitations'] = actual.get('limitations', []) + expired['limitations']
            result['applicability']['portfolio'] = actual
        else:
            result['applicability']['portfolio'] = expired
    version = ctx['identity'].get('fact_version')
    unavailable = [name for name, hashed in version.items() if hashed is None] if isinstance(version, dict) else []
    if unavailable:
        if not decision or decision['fact_version'] != ctx['identity']['fact_version']:
            result[result_key] = None
            advice = None
        bounded = deepcopy(result['applicability']['portfolio']) if result['applicability']['portfolio'] else {}
        if bounded.get('outcome') not in ('REVISE', 'EVIDENCE_LIMITED'):
            bounded['outcome'] = 'EVIDENCE_LIMITED'
        bounded['limitations'] = bounded.get('limitations', []) + [
            'Personal source files unavailable: ' + ', '.join(unavailable) + '; do not infer missing holdings or reuse actions bound to different facts.']
        result['applicability']['portfolio'] = bounded
        if strategy.get('fact_version') != ctx['identity']['fact_version']:
            for plan in result['execution_plans'].values():
                plan.update(status='UNDETERMINED', tiers=[])
    result['result_binding'] = {'market_result_hash': digest(market),
                                binding: digest(result[result_key]) if result[result_key] else None,
                                'applicability_hash': digest(result['applicability'])}
    if revision >= 7:
        # Only an actual revision 7 assessment owns current opportunity advice.
        # Do not relabel historical allocation actions during a new observation.
        result['strategy_advice'] = deepcopy(advice)
        result['result_binding']['strategy_advice_hash'] = digest(advice) if advice is not None else None
    if revision >= 8:
        from .investment import decision as investment_decision, finished
        whole = investment_decision(strategy)
        if whole is not None and whole['fact_version'] != ctx['identity']['fact_version']:
            whole = None
        result['investment_decision'] = finished(whole)
        current_whole=whole is not None and instant(whole['valid_until'])>instant(ctx['evaluated_at'])
        result['portfolio_narrative']=strategy['recommendations']['portfolio']['narrative'] if current_whole else None
        result['result_binding']['portfolio_narrative_hash']=digest(result['portfolio_narrative']) if result['portfolio_narrative'] is not None else None
        result['result_binding']['investment_decision_hash'] = digest(whole) if whole is not None else None
        if whole is None:
            result['applicability']['portfolio']['outcome'] = 'REVISE'
            result['applicability']['portfolio'].setdefault('limitations', []).append('Whole-portfolio investment judgement has not been formed; return upstream for synthesis.')
        elif instant(whole['valid_until']) <= instant(ctx['evaluated_at']):
            result['applicability']['portfolio']['outcome'] = 'REVISE'
            result['applicability']['portfolio'].setdefault('limitations', []).append('Investment judgement expired; preserve open issues and current bounded review, never renew by reporting.')
        if whole is None or instant(whole['valid_until']) <= instant(ctx['evaluated_at']):
            # Preserve the original bounded judgement and open questions, but
            # remove its executable outputs from the current report interface.
            for plan in result['execution_plans'].values():
                plan.update(status='UNDETERMINED',tiers=[])
            result['strategy_advice']=None
            if result[result_key] is not None:
                result[result_key]['actions']=[]
            result['result_binding'][binding]=digest(result[result_key]) if result[result_key] is not None else None
            result['result_binding']['strategy_advice_hash']=None
        result['result_binding']['applicability_hash'] = digest(result['applicability'])
    return result


def validate_report_binding(spec, ctx):
    result = report_inputs(ctx)
    require(spec.get('result_binding') == result['result_binding'], 'report must bind the final market, allocation and applicability results')
    return result


MARKET_INSTRUCTIONS += (
    'revision 9按forward.lifecycle固定初判和原标准，逐项核验首次登记之后的真实事实；后继明确引用被支持的机制及scope。未知/部分/证伪不越界，旧记录无事前标准只作事后复核。'
)
REPORT_INSTRUCTIONS += (
    '按实际主题保留清晰普通小节标题，结论先行，以自然段说明关键因果及反证；个人应用去重。资产长期用途不能替代完整敞口/资金/全仓约束，不将未知写成维持。内部验证字段留在内部。'
)

REPORT_INSTRUCTIONS += (
    'report contract 6的market_result.research_basis.outlook逐字承接已采纳完整前景及实际Owner/证据/判断时点；'
    'market_assessment与observation_basis保留当前观察和对原论点的适用性裁决。两层不能互相冒充。'
    '正文主路径、相对把握及反证影响须可回溯到原已形成判断；当前观察已修订的状态与限制同样交付。'
    '若当前观察与原前景实质冲突而未完成同尺度重新裁决，回到同一原研究补评，不在成稿选择一个字段掩盖缺口。'
)
