"""Quantity-free personal analysis, projected from the existing policy owner.

This module validates lineage and scope. It never selects a financial action,
parses Markdown into positions, or certifies execution/account safety.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from .common import digest, exact, facts, instant, read, require, text

MODE = 'PRICE_BASELINE_DIRECTIONAL_V1'
POLICY = 'BTC_PRICE_BASELINE_POLICY_V1'
INPUT = 'BTC_PRICE_BASELINE_INPUT_V1'
ROLE = 'DIRECTIONAL_ANALYSIS'
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


def validate_economic(value):
    exact(value, 'baseline non_quantity_constraints source_level', 'quantity-free economic inputs')
    text(value['source_level'], 'actual source confirmation level, not platform approval')
    b = value['baseline']
    exact(b, 'instrument product direction entry_price currency observed_at price_type time_basis time_precision', 'opening price baseline')
    require(b['product'] in ('CASH_SPOT', 'LEVERAGED_DERIVATIVE', 'CROSS_MARGIN_DERIVATIVE'), 'actual baseline product required')
    require(b['direction'] in ('NET_SHORT', 'NET_LONG'), 'explicit last-known direction required')
    require(b['price_type'] == 'PLATFORM_OPENING_PRICE', 'mark/current quote cannot replace opening price')
    from .common import numbered
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
    require(isinstance(value['non_quantity_constraints'], list), 'explicit non-quantity constraints required')
    ids = set()
    for c in value['non_quantity_constraints']:
        exact(c, 'id kind status statement', 'non-quantity analysis constraint')
        text(c['id'], 'constraint identity'); text(c['statement'], 'non-quantity constraint meaning')
        require(c['id'] not in ids and c['kind'] in CONSTRAINTS, 'unknown or duplicate non-quantity constraint')
        ids.add(c['id'])
        require(c['status'] in ('KNOWN', 'UNKNOWN'), 'constraint source status required')
    return value


def validate_spec(value, project=None, version=None):
    exact(value, 'schema baseline non_quantity_constraints source_level', 'price baseline policy input')
    require(value['schema'] == MODE, 'unsupported personal analysis mode')
    b = value['baseline']
    exact(b, 'instrument product direction entry_price currency observed_at price_type time_basis time_precision source_refs entry_price_ref', 'opening price source')
    economic = {'source_level': value['source_level'], 'baseline': {k: v for k, v in b.items() if k not in ('source_refs', 'entry_price_ref')},
                'non_quantity_constraints': [{k: v for k, v in c.items() if k != 'source_refs'}
                                            for c in value['non_quantity_constraints']]}
    validate_economic(economic)
    require(isinstance(b['source_refs'], list) and b['source_refs'], 'direction and product sources required')
    price = b['entry_price_ref']
    exact(price, 'file line_start line_end quote', 'opening price scalar source')
    # Match the typed decimal to this dedicated price source, never to a
    # nearby mark field or another reference with an unrelated number.
    import re
    from .common import numbered
    numbers = re.findall(r'(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.,])', price['quote'])
    require(any(numbered(n.replace(',', '')) == numbered(b['entry_price']) for n in numbers), 'opening price scalar differs from its literal source')
    refs = list(b['source_refs']) + [price]
    for c in value['non_quantity_constraints']:
        exact(c, 'id kind status statement source_refs', 'non-quantity constraint source')
        require(isinstance(c['source_refs'], list), 'constraint sources required')
        require(c['status'] != 'KNOWN' or c['source_refs'], 'known constraint needs an actual source')
        refs.extend(c['source_refs'])
    if project is not None:
        from .investment import source_refs
        source_refs(refs, project, version)
    return value


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
    resolved = resolve_sources(owner['source_refs'] + spec['baseline']['source_refs']
                               + [spec['baseline']['entry_price_ref']]
                               + [r for c in spec['non_quantity_constraints'] for r in c['source_refs']], project, version)
    economic = {'source_level': spec['source_level'], 'baseline': {k: deepcopy(v) for k, v in spec['baseline'].items() if k not in ('source_refs', 'entry_price_ref')},
                'non_quantity_constraints': [{k: deepcopy(v) for k, v in c.items() if k != 'source_refs'}
                                            for c in spec['non_quantity_constraints']]}
    return {'schema': INPUT, 'analysis_scope': MODE, 'economic_inputs': economic,
            'economic_input_hash': digest(economic),
            'policy': {'schema': POLICY, 'version': owner['version'], 'source_refs': resolved, 'economic_inputs': economic,
                       'economic_input_hash': digest(economic)},
            'provenance': {'recorded_at': owner['recorded_at'], 'origin_fact_version': owner['fact_version'],
                           'fact_version': version, 'source_change_unclassified': owner['fact_version'] != version,
                           'source_refs': [{'file': r['file'], 'line_start': r['line_start'], 'line_end': r['line_end'],
                                            'quote_hash': digest(r['quote'])}
                                           for r in resolved],
                           'authority': 'SOURCE_BOUND_INPUT_NOT_ACCOUNT_RECONFIRMATION_OR_APPROVAL'},
            'instructions': INSTRUCTIONS}


def directional(value):
    return isinstance(value, dict) and value.get('analysis_scope') == MODE


def matches(value, mode):
    if not directional(value) or not isinstance(mode, dict):
        return False
    old, current = value.get('policy', {}), mode.get('policy', {})
    return all(old.get(k) == current.get(k) for k in ('schema', 'version', 'economic_inputs', 'economic_input_hash'))


def validate_decision(value, project=None):
    require(value['analysis_scope'] == MODE, 'unsupported decision analysis scope')
    p = value['policy']
    exact(p, 'schema version source_refs economic_inputs economic_input_hash', 'directional policy projection')
    import re
    require(isinstance(p['version'], str) and re.fullmatch(r'sha256:[a-f0-9]{64}', p['version']), 'actual policy owner identity required')
    require(p['schema'] == POLICY and p['economic_input_hash'] == digest(p['economic_inputs']), 'directional inputs changed')
    validate_economic(p['economic_inputs'])
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
        require(directional(value), 'active price-baseline policy requires a new directional personal decision')
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
    if 'current_core' in value:
        out['current_core'] = {k: deepcopy(value['current_core'].get(k)) for k in
                               ('decision_id', 'created_at', 'market_cutoff', 'fact_version', 'outlook')}
    basis = value.get('cycle_baseline')
    if basis:
        out['cycle_baseline'] = {k: deepcopy(basis.get(k)) for k in
                               ('result_id', 'request_id', 'status', 'market_cutoff', 'market_report', 'market_status')}
    recs = value.get('current_recommendations')
    if recs:
        portfolio = recs.get('portfolio', {})
        def binding(item):
            return digest(item) if item is not None else None
        out['prior_bindings'] = {'portfolio_risk_hash': binding(portfolio.get('risk_assessment')),
                                 'strategy_advice_hash': binding(portfolio.get('strategy_advice')),
                                 'investment_decision_hash': binding(portfolio.get('investment_decision'))}
    out['fact_version'] = facts(project)
    out['position_analysis'] = mode
    out['instructions'] = INSTRUCTIONS
    return out


def report_projection(result, mode, whole):
    if mode is None:
        return result
    result['position_analysis'] = deepcopy(mode)
    result['result_binding']['position_analysis_hash'] = digest(mode)
    if matches(whole, mode):
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
    result['applicability']['portfolio'] = {'outcome': 'REVISE', 'limitations': ['Price-baseline personal analysis has not been formed.']}
    primary = result.get('decision_results')
    if primary:
        primary.update(allocation_choice=None, prior_allocation_choice=None, personal_applicability='REASSESSMENT_REQUIRED',
                       validation_limits=[], limitations=['Return upstream for the active price-baseline analysis.'],
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
