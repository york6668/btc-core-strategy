"""Model-authored public market results and continuous dispute-zone observations.

This module validates provenance and continuity, never computes a direction,
resonance, reversal, breakout or trading action from indicators.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from .common import digest, exact, instant, read, require, text
from .equity_products import INSTRUCTIONS as PRODUCT_INSTRUCTIONS

CORE_MARKETS = {'SPX', 'NDX', 'BTC'}
MARKET_FIELDS = ('market_review', 'extra_findings', 'research_findings',
                 'factor_checks', 'applicability_review', 'market_assessment', 'dispute_zones')
INSTRUCTIONS = (
    '市场判断独立于账户、仓位、成本、目标比例和个人建议；不得默认原有多空方向延续。'
    '跨市场、同向交易是识别渠道的示例，模型自主选择并扩展方法；在内部完成支持与冲突证据的取舍，不机械计票。'
    '逐市场判断方向、适用周期、结构变化、周期共振和趋势反转及确认程度；未知不写中性。'
    '按 method_references.report_boundary 的职责边界，summary 给已完成取舍的综合方向、周期与主导理由，反证说明其实际影响，不用冲突并列或“尚未证伪”替代结论，也不提高事实确认程度。'
    '分歧区独立维护形成后的状态、突破迹象、双方损失风险、交易应对和后续结果；内部字段不自动成为正文清单，报告只呈现对当前判断和安排有实际影响的部分。'
    '优先从近期成交、买卖力量和价格反应的累积独立推断主要上下沿，再审查历史参考；4小时只是可选观察窗口，不是固定周期。'
    '每侧分别区分近期推断待验证、近期触价支持、历史参考和未知；未触价可提出有依据的候选，不能用历史高低点默认补齐当前区域。无订单簿证据不声称存在未成交挂单。'
    '主要分歧区、内部短线关口、历史结构锚与建议价位分别保留用途、周期和证据；穿越内部关口不等于突破整体区域。'
    '新建或修正区域填写role与boundary_review revision 2，分别记录每侧证据窗口、首次识别时间、适用周期、依据、验证状态及下一条件；一侧不足独立保留未知。新证据不倒写，不将案例数字固定为长期边界。'
    '重要突破迹象无需等待突破确认或中期反转才报告；放量、突破和反转不自动互相推出。'
    '原判断逐轮核验：支持、削弱、否定或未知；区域退出或被替代必须说明原因。'
    + PRODUCT_INSTRUCTIONS
)


def enabled(root, assessment=None):
    if assessment is None:
        from . import storage
        assessment = (storage.current(root) or {}).get('assessment', {})
    path = Path(root) / 'write-policy.json'
    policy = read(path) if path.exists() else {}
    return max((assessment or {}).get('revision', 1), policy.get('minimum_assessment_revision', 1)) >= 5


def revision(assessment):
    from .closed_loop import allocation
    if allocation(assessment) is not None:
        return 6
    return max(5, assessment.get('revision', 5)) if 'market_assessment' in (assessment.get('outlook') or {}) else assessment.get('revision', 4)


def refs(value, resolve):
    for key in ('refs', 'counter_refs'):
        require(isinstance(value[key], list), key + ' must be a list')
        for ref in value[key]:
            resolve(ref)


def validate_assessment(value, resolve, cutoff=None, revision=5, prior=None):
    exact(value, 'markets summary', 'public market assessment')
    text(value['summary'], 'independent market synthesis')
    require(isinstance(value['markets'], list), 'markets must be a list')
    ids = set()
    for item in value['markets']:
        exact(item, 'id as_of horizons resonance reversal conflicts next_check', 'market result')
        text(item['id'], 'market id')
        require(item['id'] not in ids, 'duplicate market result')
        ids.add(item['id'])
        stamp = instant(item['as_of'])
        require(cutoff is None or stamp <= instant(cutoff), 'market result exceeds evidence time')
        require(isinstance(item['horizons'], list) and item['horizons'], 'market needs assessed horizons')
        periods = set()
        for h in item['horizons']:
            exact(h, 'id period direction structure change basis confirmation refs counter_refs' + (' forward' if revision >= 6 else ''), 'market horizon')
            for key in ('id', 'period', 'structure', 'change', 'basis'):
                text(h[key], 'horizon ' + key)
            require(h['id'] not in periods, 'duplicate horizon')
            periods.add(h['id'])
            require(h['direction'] in ('BULLISH', 'BEARISH', 'NEUTRAL', 'UNKNOWN'), 'invalid market direction')
            require(h['confirmation'] in ('CONFIRMED', 'PARTIAL', 'PENDING', 'UNKNOWN'), 'invalid confirmation')
            refs(h, resolve)
            if revision >= 6:
                from .closed_loop import validate_forward
                old_market = next((m for m in (prior or {}).get('markets', []) if m['id'] == item['id']), {})
                old = next((p for p in old_market.get('horizons', []) if p['id'] == h['id']), None)
                validate_forward(h['forward'], cutoff or item['as_of'], resolve, old)
        for name, statuses in (
            ('resonance', ('ALIGNED', 'CONFLICTED', 'NOT_ESTABLISHED', 'UNKNOWN')),
            ('reversal', ('CONFIRMED', 'FORMING', 'NOT_CONFIRMED', 'UNKNOWN')),
        ):
            relation = item[name]
            exact(relation, 'status periods assessment', name)
            require(relation['status'] in statuses, 'invalid ' + name + ' status')
            require(isinstance(relation['periods'], list) and set(relation['periods']) <= periods,
                    name + ' must identify actual assessed horizons')
            text(relation['assessment'], name + ' reasoning')
        for key in ('conflicts', 'next_check'):
            text(item[key], 'market ' + key)
    require(CORE_MARKETS <= ids, 'independent assessment must cover SPX, NDX and BTC')


def validate_zones(values, resolve, prior=None, cutoff=None, revision=5):
    require(isinstance(values, list), 'dispute zones must be a list')
    ids = set()
    for z in values:
        extra = ''.join(' ' + k for k in ('role', 'boundary_review') if k in z)
        if revision >= 6:
            extra += ' evidence_kind limitations'
            require(z.get('evidence_kind') in ('OHLC_RANGE', 'TRADED_VOLUME', 'ORDER_BOOK', 'PRICE_RESPONSE', 'UNKNOWN'), 'price area evidence kind required')
            text(z.get('limitations'), 'price area evidence limitations')
            require('role' in z and (z.get('boundary_review') or {}).get('revision') == 2, 'revision 6 price area needs its actual formation and validation history')
        exact(z, 'id market quote_unit area horizon as_of status state breakout long_risk short_risk response conflicts change_reason next_check retirement_reason supersedes refs counter_refs' + extra, 'dispute zone')
        if 'role' in z:
            require(z['role'] in ('MAIN_DISPUTE', 'INTERNAL_LEVEL', 'HISTORICAL_ANCHOR'), 'invalid price structure role')
        if 'boundary_review' in z:
            b = z['boundary_review']
            expanded = b.get('revision') == 2
            exact(b, 'lower upper prior_zone_assessment' + (' revision' if expanded else ''), 'boundary review')
            text(b['prior_zone_assessment'], 'independent review of previous boundaries')
            for side in ('lower', 'upper'):
                if expanded:
                    _validate_boundary(b[side], z, side, resolve)
                    continue
                exact(b[side], 'observed_at basis refs', 'boundary evidence')
                text(b[side]['basis'], 'boundary formation and response')
                require(instant(b[side]['observed_at']) <= instant(z['as_of']), 'boundary uses future evidence')
                require(isinstance(b[side]['refs'], list) and b[side]['refs'], 'boundary needs public evidence')
                for ref in b[side]['refs']:
                    resolve(ref)
        for key in ('id', 'market', 'quote_unit', 'horizon', 'state', 'long_risk', 'short_risk',
                    'response', 'conflicts', 'change_reason', 'next_check'):
            text(z[key], 'zone ' + key)
        require(z['id'] not in ids, 'duplicate dispute zone')
        ids.add(z['id'])
        require(cutoff is None or instant(z['as_of']) <= instant(cutoff), 'zone exceeds evidence time')
        instant(z['as_of'])
        exact(z['area'], 'lower upper label', 'dispute area')
        text(z['area']['label'], 'area display label')
        low, high = z['area']['lower'], z['area']['upper']
        for bound in (low, high):
            if bound is None:continue
            try:
                number = Decimal(str(bound))
                valid = number.is_finite() and number > 0
            except Exception:
                valid = False
            require(valid, 'invalid dispute area bounds')
        if low is not None and high is not None:
            require(Decimal(str(low)) <= Decimal(str(high)), 'invalid dispute area bounds')
        require(z['status'] in ('ACTIVE', 'RETIRED'), 'invalid zone tracking state')
        if z['status'] == 'RETIRED':
            text(z['retirement_reason'], 'zone retirement reason')
        else:
            require(z['retirement_reason'] is None, 'active zone has no retirement reason')
        require(isinstance(z['supersedes'], list) and all(isinstance(v, str) for v in z['supersedes']),
                'zone replacements must identify prior zones')
        exact(z['breakout'], 'direction stage assessment', 'breakout assessment')
        require(z['breakout']['direction'] in ('UP', 'DOWN', 'NONE', 'UNKNOWN'), 'invalid breakout direction')
        require(z['breakout']['stage'] in ('CONTESTED', 'SIGNS', 'INITIAL_BREAK', 'CONFIRMED', 'FAILED', 'UNKNOWN'),
                'invalid breakout stage')
        text(z['breakout']['assessment'], 'breakout reasoning')
        refs(z, resolve)
    current = {z['id']: z for z in values}
    for old in prior or ():
        if old['id'] in current:
            require(current[old['id']]['market'] == old['market'] and current[old['id']]['quote_unit'] == old['quote_unit'],
                    'historical zone identity and unit cannot be renamed')
        if old['status'] == 'ACTIVE':
            require(old['id'] in current, 'active zone disappeared without retirement: ' + old['id'])
    previous_ids = {z['id'] for z in prior or ()}
    for z in values:
        for previous in z['supersedes']:
            # Standalone parsing has no historical baseline. Validate its local
            # retirement link now; the write path separately checks real history.
            require((prior is None or previous in previous_ids) and previous in current and current[previous]['status'] == 'RETIRED',
                    'replacement must explicitly retire its previous zone')
            require(current[previous]['market'] == z['market'] and current[previous]['quote_unit'] == z['quote_unit'],
                    'product zone cannot replace an underlying index zone')


def _validate_boundary(value, zone, side, resolve):
    exact(value, 'formation validation evidence_window identified_at tested_at horizon basis refs next_check', 'recent boundary evidence')
    require(value['formation'] in ('RECENT_INFERENCE', 'RECENT_TESTED', 'HISTORICAL_REFERENCE', 'UNKNOWN'), 'invalid boundary formation')
    require(value['validation'] in ('UNTESTED', 'SUPPORTED', 'INVALIDATED', 'UNKNOWN'), 'invalid boundary validation')
    for key in ('horizon', 'basis', 'next_check'):text(value[key], 'boundary ' + key)
    window = value['evidence_window']
    exact(window, 'start end', 'boundary evidence window')
    require(instant(window['start']) <= instant(window['end']) <= instant(zone['as_of']), 'boundary uses future evidence')
    # Interpretation can be authored after the frozen evidence cutoff. Its real
    # creation clock must not be backdated to pretend the candidate existed then.
    instant(value['identified_at'])
    if value['tested_at'] is not None:
        require(instant(value['tested_at']) <= instant(zone['as_of']), 'boundary test uses future evidence')
    if value['formation'] == 'RECENT_INFERENCE':
        require(value['tested_at'] is None, 'unverified inference cannot claim a completed price test')
        require(value['validation'] != 'SUPPORTED', 'untested inference cannot claim price validation')
    if value['formation'] == 'RECENT_TESTED':
        require(value['tested_at'] is not None, 'tested boundary needs an actual reaction time')
    require((zone['area'][side] is None) == (value['formation'] == 'UNKNOWN'), 'unknown side must retain an unknown price')
    if value['formation'] == 'UNKNOWN':require(value['validation'] == 'UNKNOWN', 'unknown boundary cannot claim validation')
    require(isinstance(value['refs'], list), 'boundary refs must be a list')
    require(value['formation'] == 'UNKNOWN' or value['refs'], 'known boundary needs public evidence')
    for ref in value['refs']:resolve(ref)


def public_content(assessment):
    """No legacy mixed prose is guessed to be a pure market conclusion."""
    outlook = (assessment or {}).get('outlook') or {}
    if 'market_assessment' not in outlook:
        return None
    return deepcopy({'outlook': outlook, 'factor_assessment': assessment['factor_assessment']})


def public_review(review):
    value = {k: deepcopy(review[k]) for k in MARKET_FIELDS if k in review}
    for key in ('schema', 'context_id', 'model', 'evidence_id', 'observed_hour'):
        (value.get('market_review') or {}).pop(key, None)
    return value


def effective(root, assessment=None, decision_id=None):
    from . import storage
    root = Path(root)
    current = storage.current(root) if assessment is None or decision_id is None else None
    assessment = assessment if assessment is not None else (current or {}).get('assessment', {})
    decision_id = decision_id or (current or {}).get('decision_id')
    outlook = assessment.get('outlook') or {}
    result = {'decision_id': decision_id, 'observation_id': None,
              'market_assessment': deepcopy(outlook.get('market_assessment')),
              'dispute_zones': deepcopy(outlook.get('dispute_zones', []))}
    path = root / 'sensing-current.json'
    if path.exists():
        from . import sensing
        observed = sensing.observation(root, read(path)['observation_id'])
        review = observed['review']
        if (observed['context']['identity']['decision_id'] == decision_id
                and review.get('market_assessment') is not None):
            # Quiet observations may omit already retired zones. Their earlier
            # lifecycle record is still needed when a later study replaces or
            # reclassifies that actual zone. Overlay by identity; omission must
            # neither erase history nor revive a formally active old version.
            zones = {z['id']: deepcopy(z) for z in result['dispute_zones']}
            for values in (observed['context'].get('market_baseline', {}).get('dispute_zones', []),
                           review.get('dispute_zones', [])):
                zones.update({z['id']: deepcopy(z) for z in values})
            result.update(observation_id=observed['observation_id'],
                          market_assessment=deepcopy(review['market_assessment']),
                          dispute_zones=list(zones.values()),
                          public_review=public_review(review))
    return result


def market_projection(ctx):
    """Whitelist public inputs; never excerpt mixed historic report prose."""
    factor = deepcopy(ctx.get('factor_context') or {})
    current = factor.get('current', {})
    for key in ('decision_id', 'section_provenance', 'temporal_scope'):
        current.pop(key, None)
    if current.get('assessment_revision', 0) < 5:
        current['factor_assessment'] = None
        current['status'] = 'HISTORY_UNASSESSED'
    factor['discovery_instructions'] = INSTRUCTIONS
    indexed = factor.get('evidence') or {}
    indexed.pop('directory', None)
    for source in indexed.get('sources', []):
        source.pop('raw_path', None)
    previous = ctx.get('previous_hour') or {}
    review = previous.get('review') or {}
    if review.get('market_assessment') is None:
        review = (ctx.get('market_baseline') or {}).get('public_review') or {}
    previous_public = ({'observed_hour': previous.get('observed_hour'),
                        **public_review(review)}
                       if review.get('market_assessment') is not None else None)
    value = {'revision': 5, 'public_evidence': {
        k: ctx['identity'][k] for k in ('market_evidence_id', 'factor_evidence_id') if k in ctx['identity']},
        'factor_context': factor, 'market_baseline': deepcopy(ctx.get('market_baseline')),
        'market_report': (ctx.get('cycle_baseline') or {}).get('market_report'),
        'previous_market_observation': previous_public,
        'accumulation': deepcopy(ctx.get('accumulation')), 'instructions': INSTRUCTIONS}
    basis = ctx.get('cycle_baseline') or {}
    value['comparison_baselines'] = {
        'research': {key: basis.get(key) for key in ('result_id', 'market_cutoff', 'knowledge_cutoff')},
        'last_verified_delivery': deepcopy(ctx.get('reported', {}).get('coverage')) if ctx.get('reported') else None,
        'instructions': '累积变化对照原研究截止，报告新增含义对照最后真实送达；新小时或发送不重置研究起点。分别判断增量影响、原论点有效性及当前价格出发的风险收益。'}
    from .equity_products import subjects, REPORT_QUOTE_POLICY
    value['report_subjects'] = subjects(indexed, (ctx.get('market_baseline') or {}).get('dispute_zones', []))
    value['report_quote_policy'] = deepcopy(REPORT_QUOTE_POLICY)
    if indexed.get('collector_observations'):
        value['collector_observations'] = deepcopy(indexed['collector_observations'])
    if value['market_baseline']:
        for key in ('decision_id', 'observation_id'):
            value['market_baseline'].pop(key, None)
    if ctx.get('revision', 5) >= 6:
        from .closed_loop import INSTRUCTIONS as LOOP_INSTRUCTIONS
        value['revision'] = 6
        value['historical_hypotheses'] = {k: value.pop(k) for k in
            ('market_baseline', 'market_report', 'previous_market_observation')}
        value['historical_hypotheses']['factor_assessment'] = value['factor_context'].pop('current', None)
        # Candidate zones are historical interpretations, not newly observed
        # prices in the factual input. Product identity/quotes remain public.
        for row in value['report_subjects'].values():
            if isinstance(row, dict):
                row.pop('product_zones', None)
                row.pop('underlying_zones', None)
        value['instructions'] += LOOP_INSTRUCTIONS
    value['market_context_id'] = digest(value)
    return value


def parse_result(analysis, revision=5):
    from .cycle import parse_review
    value = parse_review(analysis)
    exact(value, 'outlook factor_assessment market_report', 'independent public market result')
    if any('forward' in h for m in value['outlook']['market_assessment']['markets'] for h in m['horizons']):
        revision = max(revision, 6)
    validate_assessment(value['outlook']['market_assessment'], lambda ref: None, revision=revision)
    validate_zones(value['outlook']['dispute_zones'], lambda ref: None, revision=revision)
    text(value['market_report'], 'independent market and price-risk report')
    return value


def ensure_research_mapping(independent, candidate):
    result = independent.get('market_result')
    if result is None:
        require(revision(candidate['assessment']) < 5, 'revision 5 needs an independently frozen public market result')
        return
    mapped = candidate['assessment']
    require(mapped['outlook'] == result['outlook'] and mapped['factor_assessment'] == result['factor_assessment'],
            'portfolio mapping changed the independent public market result')
    require(mapped['conclusion']['narrative'].startswith(result['market_report'].rstrip()),
            'full report changed the independent market report')
