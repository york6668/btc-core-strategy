"""Quantity-free personal analysis, projected from the existing policy owner.

This module validates lineage and scope. It never selects a financial action,
parses Markdown into positions, or certifies execution/account safety.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from .common import digest, exact, facts, instant, numbered, read, require, text
from .report_intent import PORTFOLIO_CHOICE_REVIEW

MODE = 'PRICE_BASELINE_DIRECTIONAL_V1'
POLICY = 'BTC_PRICE_BASELINE_POLICY_V1'
INPUT = 'BTC_PRICE_BASELINE_INPUT_V1'
PORTFOLIO_MODE = 'MONTHLY_PORTFOLIO_PLANNING_V1'
PORTFOLIO_POLICY = 'BTC_MONTHLY_PORTFOLIO_POLICY_V1'
PORTFOLIO_INPUT = 'BTC_MONTHLY_PORTFOLIO_INPUT_V1'
ROLE = 'DIRECTIONAL_ANALYSIS'
SHORT_PRODUCTS = ('LEVERAGED_DERIVATIVE', 'CROSS_MARGIN_DERIVATIVE')
CONSTRAINTS = {'PRODUCT', 'PROTECTION', 'LIQUIDITY_USE', 'FINANCING', 'FORWARD_COST', 'PURPOSE'}
INSTRUCTIONS = (
    '本次只按已知方向、真实开仓价与独立冻结市场形成方向性持仓处理；'
    '不输入或使用持仓数量、由量派生的集中度、总体规模或绝对风险，也不以其未知阻塞判断。'
    '开仓价不是现价，已有浮盈和成本价不是未来方向证据；不机械保本、止盈或止损。'
    '真实非数量用途、匹配保护、融资、未来可避免成本仍影响对应判断。'
    '减、留、退须有实际路径比较与准则；程序不生成排序。'
    '方向分析不证明订单、数量、金额、账户总PnL、净/保证金收益率或杠杆安全。'
    '只交非执行分析；任何数量执行或账户安全结论仍须原执行合同。'
    '完整事实Owner保留，当前文件hash变化不是账户重新确认；未分类来源变化不自动推翻价格源。'
    '个人引用只用本投影已披露源句；不读全量事实/旧投资叙述恢复数量。'
    '实际金融审阅须从输入反查准则、条件和隐含依赖；不能省去gap却继续以实质集中为减仓前提。'
)

AUTHORING_INSTRUCTIONS = PORTFOLIO_CHOICE_REVIEW + (
    '实际持仓候选限于投影绑定的标的；其他资产仍可根据冻结公开结果完成有依据的机会比较及条件纪律，'
    '经原决定的priorities与supported_judgements承接，保留其公开或条件范围。'
    '缺少获准的其他持仓事实时，整体净敞口和存量动作未决；不从未披露推定空仓，不以其他标的补造个人候选。'
    '对投影内的实际持仓候选，应用以下方向分析约束。'
) + INSTRUCTIONS

PORTFOLIO_INSTRUCTIONS = (
    '按独立冻结市场和已确认规划完成组合决策。short_position.status来自用户明确的适用仓位事实。'
    'PRESENT评估留减退及月度净补偿；ABSENT按无空头评估；UNKNOWN比较有空单和无空单的影响，'
    '有空单分支只给条件判断。entry_reference是用户告知的带时点价格缓存，'
    '只在适用范围内作为参考；单独告知开仓价不更新仓位，也不是成交或现价。'
    '没有价格缓存仍可按现价和前瞻成本评估熊市补偿与反抽拖累。'
    '其他未确认存量保持条件判断；计划规模可以计算，账户总敞口、数量、PnL及杠杆安全不推定。'
    '只使用本投影已披露的源句；规划不依赖账户资金、当月实际投入或执行确认。'
)

REFERENCE_FIELDS = 'price currency observed_at'
SHORT_FIELDS = 'status instrument product observed_at entry_reference'


def validate_short_position(value):
    exact(value, SHORT_FIELDS, 'short condition')
    require(value['status'] in ('PRESENT', 'ABSENT', 'UNKNOWN'), 'invalid short position status')
    text(value['instrument'], 'short condition instrument')
    require(value['product'] is None or value['product'] in SHORT_PRODUCTS, 'invalid short condition product')
    if value['observed_at'] is not None:
        instant(value['observed_at'])
    else:
        require(value['status'] == 'UNKNOWN', 'confirmed short status needs an observation time')
    entry = value['entry_reference']
    if entry is not None:
        exact(entry, REFERENCE_FIELDS, 'user entry price cache')
        require(Decimal(numbered(entry['price'])) > 0, 'positive cached entry price required')
        text(entry['currency'], 'cached entry price currency')
        instant(entry['observed_at'])


def regular_targets(plan):
    """Calculate per-symbol targets from the confirmed budget of each basket."""
    baskets = plan['regular_baskets']
    require(isinstance(baskets, list) and baskets, 'regular allocation needs budgeted baskets')
    targets = {}
    total = Decimal(0)
    for basket in baskets:
        exact(basket, 'amount weights', 'regular allocation basket')
        amount = Decimal(numbered(basket['amount']))
        weights = basket['weights']
        require(amount > 0 and isinstance(weights, dict) and weights, 'basket needs a positive budget and symbol weights')
        total += amount
        percentages = {}
        for symbol, weight in weights.items():
            text(symbol, 'regular allocation symbol')
            require(symbol == symbol.upper() and symbol == symbol.strip() and symbol not in targets,
                    'regular allocation symbols must be canonical and unique')
            percentages[symbol] = Decimal(numbered(weight))
            require(percentages[symbol] > 0, 'basket weights must be positive')
        require(sum(percentages.values()) == 100, 'weights must total 100 percent within each basket')
        targets.update({symbol: amount * weight / 100 for symbol, weight in percentages.items()})
    require(total == Decimal(numbered(plan['regular_amount'])), 'basket budgets must match the regular monthly budget')
    return targets


def validate_economic(value, mode=MODE):
    portfolio = mode == PORTFOLIO_MODE
    exact(value, ('short_position' if portfolio else 'baseline') + ' non_quantity_constraints source_level'
          + (' monthly_plan' if 'monthly_plan' in value else ''), 'quantity-free economic inputs')
    if portfolio:
        require('monthly_plan' in value, 'portfolio analysis requires a monthly policy')
        validate_short_position(value['short_position'])
    if 'monthly_plan' in value:
        plan = value['monthly_plan']
        require(isinstance(plan, dict), 'monthly plan policy must be an object')
        exact(plan, 'currency month_timezone regular_amount extra_min extra_max extra_limit short_objective'
              + (' regular_baskets' if 'regular_baskets' in plan else '')
              + (' extra_currency' if 'extra_currency' in plan else ''), 'monthly plan policy')
        text(plan['currency'], 'planning currency')
        if 'extra_currency' in plan:
            text(plan['extra_currency'], 'discretionary BTC planning currency')
        text(plan['short_objective'], 'monthly short compensation purpose')
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        text(plan['month_timezone'], 'planning calendar timezone')
        try:
            ZoneInfo(plan['month_timezone'])
        except ZoneInfoNotFoundError:
            require(False, 'unknown planning calendar timezone')
        require(Decimal(numbered(plan['regular_amount'])) > 0, 'monthly regular planning budget must be positive')
        require(not portfolio or 'regular_baskets' in plan, 'portfolio analysis requires confirmed monthly baskets')
        if 'regular_baskets' in plan:
            regular_targets(plan)
        require(0 < Decimal(numbered(plan['extra_min'])) <= Decimal(numbered(plan['extra_max'])),
                'invalid BTC additional planning range')
        require(type(plan['extra_limit']) is int and plan['extra_limit'] == 1, 'monthly BTC plan allows one discretionary purchase')
    text(value['source_level'], 'actual source confirmation level, not platform approval')
    if not portfolio:
        validate_baseline(value['baseline'])
    require(isinstance(value['non_quantity_constraints'], list), 'explicit non-quantity constraints required')
    ids = set()
    for c in value['non_quantity_constraints']:
        exact(c, 'id kind status statement', 'non-quantity analysis constraint')
        text(c['id'], 'constraint identity'); text(c['statement'], 'non-quantity constraint meaning')
        require(c['id'] not in ids and c['kind'] in CONSTRAINTS, 'unknown or duplicate non-quantity constraint')
        ids.add(c['id'])
        require(c['status'] in ('KNOWN', 'UNKNOWN'), 'constraint source status required')
    return value


def validate_baseline(b):
    exact(b, 'instrument product direction entry_price currency observed_at price_type time_basis time_precision', 'opening price baseline')
    require(b['product'] in ('CASH_SPOT', 'LEVERAGED_DERIVATIVE', 'CROSS_MARGIN_DERIVATIVE'), 'actual baseline product required')
    require(b['direction'] in ('NET_SHORT', 'NET_LONG'), 'explicit last-known direction required')
    require(b['price_type'] == 'PLATFORM_OPENING_PRICE', 'mark/current quote cannot replace opening price')
    require(Decimal(numbered(b['entry_price'])) > 0, 'positive opening price required')
    for key in ('instrument', 'currency'):
        text(b[key], 'opening price ' + key)
    require(b['time_basis'] in ('SOURCE_SNAPSHOT_TIME', 'SOURCE_UPLOAD_TIME', 'UNKNOWN'), 'baseline clock is a source clock, not an inferred fill time')
    require(b['time_precision'] in ('MINUTE', 'SECOND', 'DAY', 'UNKNOWN'), 'source time precision required')
    if b['observed_at'] is None:
        require(b['time_basis'] == b['time_precision'] == 'UNKNOWN', 'unknown source time cannot imply precision')
    else:
        instant(b['observed_at'])
        require(b['time_basis'] != 'UNKNOWN' and b['time_precision'] != 'UNKNOWN', 'known source time needs its real meaning and precision')


def validate_spec(value, project=None, version=None):
    require(isinstance(value, dict) and value.get('schema') in (MODE, PORTFOLIO_MODE), 'unsupported personal analysis mode')
    portfolio = value['schema'] == PORTFOLIO_MODE
    exact(value, 'schema non_quantity_constraints source_level ' + ('short_position' if portfolio else 'baseline')
          + (' monthly_plan' if 'monthly_plan' in value else ''), 'personal analysis policy input')
    refs = analysis_refs(value)
    validate_economic(economic_projection(value), value['schema'])
    for c in value['non_quantity_constraints']:
        exact(c, 'id kind status statement source_refs', 'non-quantity constraint source')
        require(isinstance(c['source_refs'], list), 'constraint sources required')
        require(c['status'] != 'KNOWN' or c['source_refs'], 'known constraint needs an actual source')
        refs.extend(c['source_refs'])
    if project is not None:
        from .investment import source_refs
        source_refs(refs, project, version)
    return value


def price_source(price, ref):
    exact(ref, 'file line_start line_end quote', 'opening price scalar source')
    # Match the typed decimal to its dedicated source, never to a mark price.
    import re
    numbers = re.findall(r'(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.,])', ref['quote'])
    require(any(numbered(n.replace(',', '')) == numbered(price) for n in numbers), 'opening price scalar differs from its literal source')
    return ref


def analysis_refs(spec):
    if spec['schema'] == MODE:
        b = spec['baseline']
        exact(b, 'instrument product direction entry_price currency observed_at price_type time_basis time_precision source_refs entry_price_ref', 'opening price source')
        require(isinstance(b['source_refs'], list) and b['source_refs'], 'direction and product sources required')
        return list(b['source_refs']) + [price_source(b['entry_price'], b['entry_price_ref'])]
    short = spec['short_position']
    exact(short, SHORT_FIELDS + ' source_refs', 'short condition sources')
    require(isinstance(short['source_refs'], list), 'short condition source list required')
    refs = list(short['source_refs'])
    require(short['status'] == 'UNKNOWN' or refs, 'confirmed short status needs user evidence')
    entry = short['entry_reference']
    if entry is not None:
        exact(entry, REFERENCE_FIELDS + ' source_ref', 'user entry price source')
        refs.append(price_source(entry['price'], entry['source_ref']))
    return refs


def economic_projection(spec):
    result = {'source_level': spec['source_level'],
              'non_quantity_constraints': [{k: deepcopy(v) for k, v in item.items() if k != 'source_refs'}
                                          for item in spec['non_quantity_constraints']]}
    if spec['schema'] == PORTFOLIO_MODE:
        short = {k: deepcopy(v) for k, v in spec['short_position'].items() if k != 'source_refs'}
        if short['entry_reference'] is not None:
            short['entry_reference'].pop('source_ref')
        result['short_position'] = short
    else:
        result['baseline'] = {k: deepcopy(v) for k, v in spec['baseline'].items() if k not in ('source_refs', 'entry_price_ref')}
    if 'monthly_plan' in spec:
        result['monthly_plan'] = deepcopy(spec['monthly_plan'])
    return result


def resolve_sources(refs, project, version):
    """Rebind only an unchanged literal quote, never a coincident price number."""
    from .investment import source_refs
    from .common import Invalid
    result = []
    for original in refs:
        current = deepcopy(original)
        try:
            source_refs([current], project, version)
        except Invalid:
            require(original['file'] in version and version[original['file']] is not None, 'analysis source unavailable')
            raw = (Path(project) / original['file']).read_text()
            quote = original['quote']
            require(raw.count(quote) == 1, 'analysis source quote changed or ambiguous; reassess only its affected meaning')
            start = raw.index(quote)
            current['line_start'] = raw[:start].count('\n') + 1
            current['line_end'] = current['line_start'] + quote.count('\n')
            source_refs([current], project, version)
        result.append(current)
    return result


def latest(root, at=None):
    """A later typed policy supersedes; unrelated quantity/fact versions do not."""
    items = []
    for p in (Path(root) / 'investment/policies').glob('*.json'):
        v = read(p)
        require(v.get('version') == 'sha256:' + p.stem == digest({k: x for k, x in v.items() if k != 'version'}), 'policy owner damaged')
        if 'position_analysis' in v and (at is None or instant(v['recorded_at']) <= instant(at)):
            items.append(v)
    return max(items, key=lambda v: instant(v['recorded_at'])) if items else None


def inputs(root, project, at=None):
    owner = latest(root, at)
    if owner is None:
        return None
    version = facts(project)
    spec = owner['position_analysis']
    validate_spec(spec)
    # Only selected source meaning is rechecked. A new quantity, historical
    # summary or concentration elsewhere must not become a mismatch gate.
    resolved = resolve_sources(owner['source_refs'] + analysis_refs(spec)
                               + [r for c in spec['non_quantity_constraints'] for r in c['source_refs']], project, version)
    economic = economic_projection(spec)
    portfolio = spec['schema'] == PORTFOLIO_MODE
    return {'schema': PORTFOLIO_INPUT if portfolio else INPUT, 'analysis_scope': spec['schema'], 'economic_inputs': economic,
            'economic_input_hash': digest(economic),
            'policy': {'schema': PORTFOLIO_POLICY if portfolio else POLICY, 'version': owner['version'], 'source_refs': resolved, 'economic_inputs': economic,
                       'economic_input_hash': digest(economic)},
            'provenance': {'recorded_at': owner['recorded_at'], 'origin_fact_version': owner['fact_version'],
                           'fact_version': version, 'source_change_unclassified': owner['fact_version'] != version,
                           'source_refs': [{'file': r['file'], 'line_start': r['line_start'], 'line_end': r['line_end'],
                                            'quote_hash': digest(r['quote'])}
                                           for r in resolved],
                           'authority': 'SOURCE_BOUND_INPUT_NOT_ACCOUNT_RECONFIRMATION_OR_APPROVAL'},
            'instructions': PORTFOLIO_INSTRUCTIONS if portfolio else INSTRUCTIONS}


def directional(value):
    return isinstance(value, dict) and value.get('analysis_scope') in (MODE, PORTFOLIO_MODE)


def matches(value, mode):
    if not directional(value) or not directional(mode) or value['analysis_scope'] != mode['analysis_scope']:
        return False
    old, current = value.get('policy', {}), mode.get('policy', {})
    return all(old.get(k) == current.get(k) for k in ('schema', 'version', 'economic_inputs', 'economic_input_hash'))


def basket_decision_missing(value, mode):
    from .decision_design import BASKET_ALLOCATION_SCHEMA
    return ((mode or {}).get('analysis_scope') == PORTFOLIO_MODE and
            (not matches(value, mode) or
             (value.get('allocation_choice') or {}).get('schema') != BASKET_ALLOCATION_SCHEMA))


def validate_decision(value, project=None):
    require(directional(value), 'unsupported decision analysis scope')
    portfolio = value['analysis_scope'] == PORTFOLIO_MODE
    p = value['policy']
    exact(p, 'schema version source_refs economic_inputs economic_input_hash', 'directional policy projection')
    import re
    require(isinstance(p['version'], str) and re.fullmatch(r'sha256:[a-f0-9]{64}', p['version']), 'actual policy owner identity required')
    require(p['schema'] == (PORTFOLIO_POLICY if portfolio else POLICY)
            and p['economic_input_hash'] == digest(p['economic_inputs']), 'personal inputs changed')
    validate_economic(p['economic_inputs'], value['analysis_scope'])
    require(isinstance(p['source_refs'], list) and p['source_refs'], 'actual disclosed personal source references required')
    for ref in p['source_refs']:
        exact(ref, 'file line_start line_end quote', 'disclosed directional personal source')
    if project is not None:
        actual = inputs(Path(project) / '.strategy/v4', project, value['decided_at'])
        require(actual is not None and p == actual['policy'], 'directional decision must consume its actual policy projection')
    for item in value['candidates']:
        require(item['execution_role'] == ROLE, 'directional scope cannot authorize an inventory or policy execution role')
        v = item['validation']
        require(v['basis'] == 'JUDGEMENT' and not v['record_ids'] and not v['required_capabilities']
                and v['limitations'], 'directional analysis is not implementation effectiveness or safety')
        if portfolio:
            short = p['economic_inputs']['short_position']
            derivative = short['product'] is not None and v['products'] == [short['product']]
            require(v['products'] == ['RESEARCH_OR_POLICY'] or (derivative and short['status'] != 'ABSENT'),
                    'candidate product must match the applicable portfolio or short scope')
            if derivative and short['status'] == 'UNKNOWN':
                require(item['id'] not in value['selected_ids'], 'unconfirmed short cannot supply a selected candidate')
        else:
            require(v['products'] == [p['economic_inputs']['baseline']['product']], 'retain actual baseline product identity in directional analysis')
    for gap in value['gaps']:
        require(gap.get('dimension') in CONSTRAINTS | {'MARKET', 'DIRECTION'},
                'directional dependencies must be non-quantity facts, not size/concentration/absolute risk')
        require(not gap.get('capabilities'), 'execution capabilities are outside price-baseline judgement, not an ACTION gate')


def validate_personal_refs(value, policy):
    """Do not import an undisclosed quantity source through another field."""
    allowed = {digest(r) for r in policy['source_refs']}
    def walk(v):
        if isinstance(v, dict):
            if 'file' in v and 'quote' in v:
                require(digest(v) in allowed, 'personal analysis reference was not disclosed by its quantity-free owner projection')
            for child in v.values():
                walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)
    walk(value)


def require_current_scope(root, project, assessment):
    """New writes cannot omit an active mode; historical readback is unchanged."""
    if latest(root) is not None:
        from .investment import decision
        value = decision(assessment)
        require(directional(value), 'active personal policy requires a scoped decision')
        require(value['policy'] == inputs(root, project)['policy'], 'new candidate uses a superseded personal analysis input')


def validate_outputs(value, recommendations):
    for side in ('upper_short', 'lower_spot'):
        plan = recommendations[side]
        require(not plan['tiers'] and plan['status'] != 'RECOMMENDED', 'directional analysis cannot create execution tiers')
    portfolio = recommendations['portfolio']
    for a in portfolio['strategy_advice'] + portfolio['risk_assessment']['actions']:
        require(a['execution']['status'] == ('UNDETERMINED' if a['status'] == 'UNDETERMINED' else 'NOT_APPLICABLE'),
                'directional judgement cannot claim execution readiness or quantity-limited execution')
        require(a['gap_impact'] != 'QUANTITY_ONLY', 'quantity is excluded from the analysis, not its decision gap')


def author_view(value, root, project):
    """One allowlist for research, portfolio and MAIN model-facing material.

    Full internal contexts/owners stay available to program validators. This
    is a data projection, not a claim of OS isolation from arbitrary tools.
    """
    mode = inputs(root, project)
    if mode is None:
        return value
    public = ('schema', 'input_id', 'phase', 'request_id', 'market_result', 'market_result_hash',
              'market_content_hash', 'portfolio_result_binding', 'decision_design_revision', 'decision_contract',
              'context_id', 'identity', 'revision', 'market_context_id', 'accumulation',
              'factor_context', 'prospective_feedback', 'since_deep', 'since_deep_total', 'since_deep_truncated')
    out = {k: deepcopy(value[k]) for k in public if k in value}
    from .decision_design import (BASKET_PLAN_INSTRUCTIONS, MONTHLY_PLAN_INSTRUCTIONS, PLAN_SCHEMAS,
                                  PORTFOLIO_PLAN_INSTRUCTIONS, monthly_policy, planned_allocation_schema)
    if 'current_core' in value:
        out['current_core'] = {k: deepcopy(value['current_core'].get(k)) for k in
                               ('decision_id', 'created_at', 'market_cutoff', 'fact_version', 'outlook')}
    basis = value.get('cycle_baseline')
    if basis:
        out['cycle_baseline'] = {k: deepcopy(basis.get(k)) for k in
                               ('result_id', 'request_id', 'status', 'market_cutoff', 'market_report', 'market_status')}
    recs = value.get('current_recommendations')
    prior = value.get('investment_decision')
    if recs:
        portfolio = recs.get('portfolio', {})
        prior = portfolio.get('investment_decision')
        def binding(item):
            return digest(item) if item is not None else None
        out['prior_bindings'] = {'portfolio_risk_hash': binding(portfolio.get('risk_assessment')),
                                 'strategy_advice_hash': binding(portfolio.get('strategy_advice')),
                                 'investment_decision_hash': binding(portfolio.get('investment_decision'))}
    if matches(prior, mode):
        choice = prior.get('allocation_choice') or {}
        if choice.get('schema') in PLAN_SCHEMAS:
            out['portfolio_plan_to_review'] = {
                'schema': choice['schema'],
                'valid_until': prior['valid_until'],
                'public_case_hash': choice['public_case_hash'],
                'plan': deepcopy(choice['portfolio_plan']),
            }
        if mode['analysis_scope'] == PORTFOLIO_MODE and value.get('investment_feedback') is not None:
            feedback = value['investment_feedback']
            out['investment_feedback'] = {k: deepcopy(feedback[k]) for k in (
                'judgement_evaluations', 'judgement_evaluation_origins', 'required_evaluation_windows')}
    out['fact_version'] = facts(project)
    out['position_analysis'] = mode
    portfolio = mode['analysis_scope'] == PORTFOLIO_MODE
    out['instructions'] = PORTFOLIO_CHOICE_REVIEW + PORTFOLIO_INSTRUCTIONS if portfolio else AUTHORING_INSTRUCTIONS
    if value.get('decision_design_revision', 2) >= 2:
        out['instructions'] += PORTFOLIO_PLAN_INSTRUCTIONS
        if monthly_policy(mode['policy']) is not None:
            out['decision_contract'] = ('investment_decision.allocation_choice: ' + planned_allocation_schema(mode['policy'])
                                        + '; source-bound monthly planning and downside compensation.')
            out['instructions'] += BASKET_PLAN_INSTRUCTIONS if portfolio else MONTHLY_PLAN_INSTRUCTIONS
            if basket_decision_missing(prior, mode):
                out['instructions'] += (
                    '当前月度方案缺少逐组选择。观察应将个人适用性标为需复评，由原MAIN补足，复用已冻结公开判断。'
                    '旧组金额只供对照，不能倒推继续或跳过。')
    return out


def report_projection(result, mode, whole, *, require_basket_decisions=False):
    if mode is None:
        return result
    result['position_analysis'] = deepcopy(mode)
    result['result_binding']['position_analysis_hash'] = digest(mode)
    basket_gap = require_basket_decisions and basket_decision_missing(whole, mode)
    if matches(whole, mode) and not basket_gap:
        if result.get('decision_results') is not None:
            result['decision_results']['position_analysis'] = deepcopy(mode)
            result['result_binding']['decision_results_hash'] = digest(result['decision_results'])
        return result
    # A newly enabled mode does not migrate or revive an old personal result.
    result['investment_decision'] = None
    result['portfolio_narrative'] = None
    result['strategy_advice'] = None
    if result.get('portfolio_risk') is not None:
        result['portfolio_risk'] = None
    for plan in result['execution_plans'].values():
        plan.update(status='UNDETERMINED', tiers=[])
    limitation = ('本月各组继续或跳过的判断仍待复评。' if basket_gap
                  else 'Price-baseline personal analysis has not been formed.')
    result['applicability']['portfolio'] = {'outcome': 'REVISE', 'limitations': [limitation]}
    primary = result.get('decision_results')
    if primary:
        upstream = limitation if basket_gap else 'Return upstream for the active price-baseline analysis.'
        primary.update(allocation_choice=None, prior_allocation_choice=None, personal_applicability='REASSESSMENT_REQUIRED',
                       validation_limits=[], limitations=[upstream],
                       position_analysis=deepcopy(mode))
    # Binding reflects the actual author projection; raw strategy identity is
    # still independently held by the normal report context/consumer.
    result['result_binding'] = {k: v for k, v in result['result_binding'].items() if k not in
                               ('investment_decision_hash', 'portfolio_narrative_hash', 'strategy_advice_hash', 'portfolio_risk_hash')}
    result['result_binding'].update(investment_decision_hash=None, portfolio_narrative_hash=None,
                                   strategy_advice_hash=None, portfolio_risk_hash=None,
                                   applicability_hash=digest(result['applicability']))
    if primary:
        result['result_binding']['decision_results_hash'] = digest(primary)
    return result
