"""Model-authored public market results and continuous dispute-zone observations.

This module validates provenance and continuity, never computes a direction,
resonance, reversal, breakout or trading action from indicators.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from .common import digest, exact, instant, read, require, text
from .equity_products import INSTRUCTIONS as PRODUCT_INSTRUCTIONS
from .factors import OPPORTUNITY_COST_REVIEW

CORE_MARKETS = {'SPX', 'NDX', 'BTC'}
MARKET_FIELDS = ('market_review', 'extra_findings', 'research_findings',
                 'factor_checks', 'applicability_review', 'market_assessment', 'dispute_zones')
EVIDENCE_REASONING_REVIEW = (
    '在现有basis、factor_assessment、conflicts及forward中区分已核验事实、有证据支持但受限的判断、待验证假设；关联原始refs、口径、时间与反证，不要求新增标签或固定栏目。'
    '关键判断须有贯穿其适用周期的主证据链；分别核验当前方向、主因归属、延续把握和现价参与选择，主因未识别只限制依赖它的命题，不能抹去独立结构支持的方向。引用存在、数据齐全或正反因素并列不证明论证完成。'
    '涉及结构或关键位置时，接续原结构、位置形成依据与识别时间、历次实际测试、可比量价及后续反应；未测试、形成前价格和失效记录分别保留。用户示例不证明行情，也不固定为必用指标或周数。'
    '比较说明场所、现货或衍生品、单位、窗口与完整性；摘要保留各分句证据的实际范围，多所价格的主语不能延伸到单所成交或持仓。先核对已有冻结材料和重叠数据，缺少的比较具体标缺，不把日内切片当多周证据。主动成交差额不是外部净流，单所数据不代表全市场，重叠窗口不相加。'
    '共同变化仅支持相关或相容解释，因果须有传导及可区分竞争解释的证据；低量不自动证明枯竭或反转，历史购买或融资容量不自动证明未来净需求及现价有投资价值。宏观、需求和其他证据按实际因果关联取舍，不机械计票。'
    '每项依赖未来条件的判断保留可观察支持、失效依据和原检验窗口；证据缺失不默认为条件成立。未证实假设只指导取证或条件情景，不能作无条件规划前提，加上“可能”也不能代替补证。'
    '将缺口定位到采集、研究、判断、规划或表达，先复用可核验资料；核心依据缺失回原研究接续，不改写市场结论来满足案例，也不以延期或免责声明替代论证。'
)
CYCLE_THESIS_REVIEW = (
    '接续原主论点，核对机制若成立应有的响应、实际响应与留存，再裁决竞争解释及重要反证；仍相容的原因保留未识别，不强选主因。按投资问题和价值兑现选择完整推动、回撤及留存序列，不设固定粒度；具体方法见core-strategy的事实、判断与规划前提。'
    '证据权重取决于实际因果和定价影响，不按窗口长短、新鲜度或信号数量排优先。局部恢复说明恢复了哪段损伤、原结构哪些已经恢复或仍未留住，再裁决对局部风险、方向或延续的影响；窗口较长不自动胜过反证，局部事件也不替代主周期。'
    'adopted_research是带原时点的已采纳论证，previous_market_observation是本轮待核验的后继判断，二者均不自动证明当前正确。changes_since_deep和applicability_review.explanation承接原论点、本次决定性证据、竞争解释和保留或修正原因，不以最新小时摘要自由替换原研究。'
    '原方向推动力衰减与反向趋势形成分别判断；突破后的动能变化、成交与持仓、费率等只是可选证据，不设固定阈值或必查清单。识别条件新增、改变或退出须有当期来源及理由，不能事后改标准维持旧结论。'
    '从现价比较参与、防守与等待；主线偏弱时降价仍可能改善补偿，不能要求需求先反转才研究价值。分歧区、结构风险区与价值区分别论证，历史支撑不自动成为价值。核心遗漏或实质反证沿原研究补评，不只改摘要或风险声明。'
)
PUBLIC_RESULT_REVIEW = (
    '市场填写完成后，先用run-advance --stage market --check-only导出实际合成候选，用返回的review_read执行run-read，按next_cursor读至complete；完整理解required材料后才审阅冻结。不能用文件解析、路径或抽取少数字段代替完整回读。输出截断时在同一cursor减小max-characters重读，不跳过余段；输出完成仅证明内容已提供，不证明语义审阅。不能在同一次调用中写判断并直接冻结。结构与引用预检不证明语义已通过。'
    '审阅实际候选的全部公开字段，尤其market_review.assessment_summary、changes_since_hour、changes_since_deep、factor_checks、applicability_review及market_assessment，不能只读详细basis或最后报告。'
    '按真实消费边界核对摘要范围：同一完整对象内已明确且唯一对应的交易所、产品、单位和窗口可以共享，不机械要求每句话重复；市场对象含多个场所或渠道时，核对各句能否唯一对应实际依据。脱离所属对象独立使用的摘要、报告摘录和条件必须自行保留或明确承接其必要限定，不能靠消费者未获得的旁文或原始refs消歧。任何省略均不能将多所价格范围扩张到单所成交与持仓。'
    '核对主证据链的应有与实际响应、竞争解释及反证裁决；分别检查方向、归因、延续和参与价值的依据及限制，不能因撤回一个原因就无依据地撤回方向。历史摘要仍需核验，不因已冻结或已有引用视为充分；本轮澄清保留历史字节和原研究身份，不据含混摘要形成无条件规划。'
    '需要修正时修改本轮判断并重新预检、回读新候选；审阅完成后用--input <market_candidate>冻结同一候选，再读个人材料。程序只绑定内容与当前公开上下文，不以关键词、阅读收据或测试结果代替模型审阅。'
)
EVIDENCE_REPORT_REVIEW = (
    '成稿保留已形成结论的主证据链及会改变其理解或安排的限制：事实、受限判断和待验假设不得在压缩中互换。'
    '并列或相邻分句保留各自证据的实际场所、产品和比较窗口；主语切换或省略不能把多所价格的范围延伸到单所成交或持仓，可共享清楚的限定，不逐项列字段。同期变化不能写成确定因果、主动差额不能写成外部资金净流、历史需求不能写成现价值得参与、低量不能写成反转、条件方案不能写成立即安排。'
    'reasoning、cycle_basis及deep_report_summary对照实际源句与正文，核对分句主语、口径及方向与归因的不同确认程度，并指出总裁决、重要主题本轮选择、未决动作影响与总体收束的实际原句及上游依据。方向句或条件清单不能代替选择；论证缺失回原研究，已有选择而正文遗漏则修稿，不靠绑定、逐字交付或模板词自证通过。'
)
CYCLE_EXPRESSION_REVIEW = (
    '当前周期状态与时间窗口分别表达：先说明截至当前识别的方向和依据；提及日期时说明其检验、有效或兑现用途，不能把检验截止日写成该状态将持续到那天。'
    '有独立依据的前瞻延续判断仍可表达，保留其条件、期限与确认程度，不一律降为观察窗口；当前识别本身不构成延续预测。'
    '原稿以“至某日”含混连接状态时，对照structure/basis及forward的timing_basis、conditions和invalidations澄清真实含义；不为逐字摘要保留歧义，不能从措辞修正产生新判断。'
    '按内容自然分句或说明日期用途，不规定句式、栏目或禁词；不要求为表达当前状态展示所有内部期限，也不删除影响理解或执行的有效期限。'
)
CYCLE_FOCUS_REVIEW = (
    '周期是框架识别出的当前中性、向上或向下状态，不是若干天或周；证据不足明确哪项状态尚不能判断，未知不填成中性。'
    '先按驱动机制、价格反应及证据形成当前周期、切换确认、参与价值、双向风险与改变条件；观察窗口、判断有效期及兑现窗口另行选择，不用延长预测期限回答周期。'
    'market_assessment.summary、horizons中的结构与方向、BTC的outlook.semantic主情景和market_report承接同一当前周期判断；forward维护其证据与期限，小时核验及短线剩余有效期不能替代周期结论。'
    '日内数据用于验证支持、反证及重要即时风险，普通回收、反弹或破位不替代周期依据；周期证据不足先补证并保留具体限制，不能退回小时判断充当周期结论。'
    '观察窗口、判断期限和兑现窗口分别维护；修正原主周期须有实际研究依据并保留旧判断和期限，不能将小时结论直接改标为数周。'
) + CYCLE_EXPRESSION_REVIEW
MOMENTUM_REVIEW = (
    '本轮重点分别判断原方向推动力是否衰减，以及相反方向是否建立可持续趋势；上涨后的买方动量与下跌后的卖方动量均适用，不预设当前已经枯竭或反转。'
    '动量由实际驱动力及其价格效果解释，不限定日内技术指标、卖竭指数或固定因子组合；按问题选择需求、供给、融资、盈利等证据及竞争解释，不制造综合分数。'
    '沿原论点和可核验窗口比较驱动力、价格对新增支持或冲击的反应、反向运动的延续性、重要反证与确认程度。原动力减弱不证明反向动力建立；短暂反弹、回撤或去仓不能直接升级为反转趋势。'
    '用实际来源说明相较上次是推动力衰减、反向尝试、延续得到支持、反转失败还是仍不能判断；这些是开放分析问题，不是必经阶段或程序状态机。判断失败时保留旧论点及反证，不改写过去的确认。'
    '结论写入现有horizons的structure/change/basis/confirmation及refs/counter_refs，reversal.assessment解释原驱动与反向趋势各到哪一步，conflicts与next_check保留限制及下一可区分的真实证据。'
    'factor_assessment承载驱动机制，outlook.semantic和business_meaning保留实质含义；原始测量与模型推断分开，未知具体到哪项判断未成立，不以未知推成中性。'
    '原研究已经完成的有据判断可按实际版本复用；未分别论证这两个问题不算完成新目标。先用本轮证据实质核验，影响核心判断的遗漏或反证沿原BTC_CYCLE接续补评，不因方法改版重复建立研究。'
    '趋势识别不替代当前价格的潜在收益、下行风险与等待比较；市场冻结后仍独立评估个人风险、承受能力和机会，不能从枯竭或反转标签直接推出操作。'
)
MOMENTUM_REPORT_REVIEW = (
    '正文承接已形成的动量与趋势判断，清晰说明当前到哪一步、关键依据、相较上次的实质变化和下一项区分性验证；沿原字段与真实展示比较，不要求固定栏目。'
    '保留原推动力衰减与相反趋势形成的确认差别：枯竭不等于反转，短暂反弹或回撤不等于可持续趋势，反转失败及具体证据缺口如实表达；不从UNKNOWN写出中性。'
    '保留已评估的现价风险收益、等待价值和个人风险结论，不以趋势标签替代。成稿不得提高确认程度或新增研究结论；缺论证返回对应研判，不靠添加模板词语补齐。'
)
INSTRUCTIONS = CYCLE_FOCUS_REVIEW + MOMENTUM_REVIEW + EVIDENCE_REASONING_REVIEW + PUBLIC_RESULT_REVIEW + (
    '市场判断独立于个人账户、个人仓位与成本、目标比例和个人建议；公共持有人成本按群体与统计口径作为市场证据，不得默认原有多空方向延续。'
    '跨市场、同向交易是识别渠道的示例，模型自主选择并扩展方法；在内部完成支持与冲突证据的取舍，不机械计票。'
    '逐市场判断方向、适用周期、结构变化、周期共振和趋势反转及确认程度；未知不写中性。'
    '按 method_references.report_boundary 的职责边界，summary 给已完成取舍的综合方向、周期与主导理由，反证说明其实际影响，不用冲突并列或“尚未证伪”替代结论，也不提高事实确认程度。'
    '分歧区独立维护形成后的状态、突破迹象、双方损失风险、交易应对和后续结果；内部字段不自动成为正文清单，报告只呈现对当前判断和安排有实际影响的部分。'
    '维护近期分歧区时，从成交、买卖力量和价格反应的累积独立推断该区上下沿，再审查历史参考；此局部观察不决定主要投资周期，4小时只是可选观察窗口。'
    '每侧分别区分近期推断待验证、近期触价支持、历史参考和未知；未触价可提出有依据的候选，不能用历史高低点默认补齐当前区域。无订单簿证据不声称存在未成交挂单。'
    '主要分歧区、内部短线关口、历史结构锚与建议价位分别保留用途、周期和证据；穿越内部关口不等于突破整体区域。'
    '新建或修正区域填写role与boundary_review revision 2，分别记录每侧证据窗口、首次识别时间、适用周期、依据、验证状态及下一条件；一侧不足独立保留未知。新证据不倒写，不将案例数字固定为长期边界。'
    '重要突破迹象无需等待突破确认或中期反转才报告；放量、突破和反转不自动互相推出。'
    '原判断逐轮核验：支持、削弱、否定或未知；区域退出或被替代必须说明原因。'
    + OPPORTUNITY_COST_REVIEW
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
    from .closed_loop import allocation, risk
    if risk(assessment) is not None:
        return 7
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
    core = ctx.get('current_core') or {}
    outlook = core.get('outlook') or {}
    if outlook.get('market_assessment') is not None:
        # Keep the actual adopted argument separate from the latest hourly
        # overlay. This is provenance, not a program verdict on its validity.
        value['adopted_research'] = {
            'decision_id': core.get('decision_id'), 'market_cutoff': core.get('market_cutoff'),
            'result_id': (ctx.get('cycle_baseline') or {}).get('result_id'),
            'semantic': deepcopy(outlook.get('semantic')),
            'market_assessment': deepcopy(outlook['market_assessment'])}
        value['thesis_review_instructions'] = CYCLE_THESIS_REVIEW
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
        value['revision'] = ctx['revision']
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
