"""Revision 6 contracts. Models decide; these checks protect ownership and lineage.

No market scoring, Markdown parsing, account inference or automatic migration.
Older immutable assessments/observations continue through their original contracts.
"""
from copy import deepcopy

from .common import digest, exact, instant, require, text

REVISION = 6
OUTCOMES = ('VALID', 'REVISE', 'EVIDENCE_LIMITED')
SCOPES = ('MARKET', 'PORTFOLIO')
INSTRUCTIONS = (
    '完整闭环交付两个结果：独立市场结论、综合风险与机遇后的整体配置决定。'
    '公开事实、历史假设、个人事实分开；先从原始公开证据研判，再检验历史观点，历史预测和候选价不自证。'
    '研究覆盖周期、价格结构、资金与融资、盈利、未来价值和当前定价；内部完成冲突取舍。'
    '观察窗口、判断期限、预期兑现窗口分别记录；期限届满检查兑现，不随报告时钟续期。'
    '冻结市场结果后才读取个人材料；组合和成稿不能改写它，需要修正返回市场研究。'
    '组合内部比较参与、减风险、持有与退出的得失，allocation_decision只交付最终安排及必要范围、执行限制。'
    '五模块是内部覆盖，不是正文栏目；现货加减、合约开平和四小时区域是问题示例，不限定研究或动作范围。'
    '数量未知保留有依据的方向并标明执行受限；影响动作本身的缺口明确未决，不能默认持有安全。'
    '减仓、退出、暂停同时维护恢复参与条件，现金、保证金、敞口及保护不重复计算。'
    '市场变化先改市场再复核配置；个人变化只复核组合；重大遗漏、累计反证、前瞻落空回对应研究。'
    '只有表达变化则修稿；送达、触价、建议均非成交，资产事实只由用户确认更新。'
)

# Report authors consume assessed results. Research instructions belong to the
# preceding stages and must not re-enter the report through result_instructions.
REPORT_INSTRUCTIONS = (
    '成稿承接 market_result、allocation_decision 和 applicability 中已经形成的结果。'
    '市场写明确方向、适用周期及主导依据；配置写最终安排、范围与必要执行限制。'
    '按结果的实际含义取舍表达，不逐项转述内部字段、来源核验、未知清单或维护记录；applicability限制当前安排，不要求照抄其explanation中的全部规则。'
    '同一动作或限制集中说明一次；逐句检查当前理解是否需要它，段落主题重要不代表段内所有句子都必要。'
    '数量受限与动作未决分别说明实际影响；缺少正式配置决定时不得把旧安排写成重新验证有效。'
    '重要风险、建议有效性、恢复参与条件及事实纠错忠实保留，不提高确认程度或补造交易价。'
    '成稿不重新研究或改写结果；发现影响判断的分析缺口，返回对应研究，表达问题在冻结前修正。'
    '研究和维护材料继续完整保存；来源绑定与逐字交付不改变正文，也不代表实际成交。'
)


def strings(value, name, nonempty=False):
    require(isinstance(value, list) and (value or not nonempty), name + ' must be a list')
    for item in value:
        text(item, name)


def window(value, name):
    exact(value, 'start end', name)
    require(instant(value['start']) <= instant(value['end']), name + ' is reversed')


def validate_forward(value, cutoff, resolve, prior=None):
    exact(value, 'observed_window judgement_window realization_window timing_basis conditions invalidations prior_check', 'forward assessment')
    for key in ('observed_window', 'judgement_window'):
        window(value[key], key)
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
    if any(instant(w['end']) <= instant(cutoff) for w in due):
        require(checked['outcome'] not in ('INITIAL', 'PENDING'), 'expired forward window requires realization review, not automatic renewal')


def allocation(assessment):
    return ((assessment or {}).get('recommendations') or {}).get('portfolio', {}).get('allocation_decision')


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
    ids, statuses = set(), set()
    for action in value['actions']:
        exact(action, 'id scope status decision arrangement gap_impact execution reentry_conditions', 'allocation action')
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
    expected = 'PARTIAL' if len(statuses) > 1 else next(iter(statuses))
    require(value['status'] == expected, 'overall allocation status contradicts its actual decisions')


def review_template():
    return {'market_result_hash': None, 'allocation_decision_hash': None, 'fact_version': None,
            'outcome': None, 'explanation': None, 'limitations': [], 'material': None}


def validate_allocation_review(value, ctx, market_hash):
    exact(value, ' '.join(review_template()), 'allocation applicability review')
    current = ((ctx.get('current_recommendations') or {}).get('portfolio') or {}).get('allocation_decision')
    require(value['market_result_hash'] == market_hash, 'portfolio review changed its frozen market result')
    require(value['allocation_decision_hash'] == (digest(current) if current else None), 'portfolio review binds another allocation decision')
    require(value['fact_version'] == ctx['identity']['fact_version'], 'portfolio review facts changed')
    require(value['outcome'] in OUTCOMES, 'invalid portfolio review outcome')
    require(type(value['material']) is bool, 'portfolio review needs materiality judgement')
    text(value['explanation'], 'portfolio applicability reasoning')
    strings(value['limitations'], 'portfolio applicability limits', value['outcome'] == 'EVIDENCE_LIMITED')
    if value['outcome'] == 'VALID':
        require(current is not None, 'legacy narrative cannot manufacture a revision 6 allocation decision')
        require(current['fact_version'] == value['fact_version'], 'changed facts require an actual allocation reassessment')
        require(instant(current['valid_until']) > instant(ctx['factor_context']['evidence']['cutoff']), 'expired allocation requires reassessment')


def report_inputs(ctx):
    """A finished-result interface, never the five-module analysis or mixed prose."""
    strategy = ctx['current_strategy']
    review = (ctx.get('observation') or {}).get('review') or {}
    market = {k: deepcopy(review[k]) for k in ('market_assessment', 'dispute_zones') if k in review}
    if not market:
        market = {k: deepcopy(strategy.get('outlook', {}).get(k)) for k in ('market_assessment', 'dispute_zones')}
    decision = allocation(strategy)
    personal = deepcopy(review.get('allocation_review'))
    if personal and personal.get('allocation_decision_hash') != (digest(decision) if decision else None):
        personal = None  # A just-published arrangement needs its own applicability check.
    result = {'market_result': market, 'allocation_decision': deepcopy(decision),
              'execution_plans': {s: {k: deepcopy(strategy['recommendations'][s][k]) for k in
                  ('status', 'unit', 'tiers', 'validity', 'allocation_percent')} for s in ('upper_short', 'lower_spot')},
              'applicability': {'market': deepcopy(review.get('applicability_review')),
                                'portfolio': personal or {'outcome': 'EVIDENCE_LIMITED', 'limitations': ['Current allocation has not been reviewed in this observation.']}}}
    if decision and decision['fact_version'] != ctx['identity']['fact_version']:
        result['applicability']['portfolio'] = {'outcome': 'REVISE', 'limitations': ['Local asset facts changed; the previous arrangement is not a current decision.']}
    if decision and instant(decision['valid_until']) <= instant(ctx['evaluated_at']):
        result['applicability']['portfolio'] = {'outcome': 'REVISE', 'limitations': ['Allocation expired; the report clock cannot extend it.']}
    result['result_binding'] = {'market_result_hash': digest(market),
                                'allocation_decision_hash': digest(decision) if decision else None,
                                'applicability_hash': digest(result['applicability'])}
    return result


def validate_report_binding(spec, ctx):
    result = report_inputs(ctx)
    require(spec.get('result_binding') == result['result_binding'], 'report must bind the final market, allocation and applicability results')
    return result
