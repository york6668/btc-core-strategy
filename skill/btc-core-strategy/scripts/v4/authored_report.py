"""Validate the author's finished Markdown without rendering or rewriting it."""
import re
from decimal import Decimal
from datetime import timedelta, timezone
from .common import digest, exact, instant, pointer, require, text
from .mobile_report import QUALITY, INDEX_NAME

FORMAT = 'AUTHORED_REPORT_V1'
DISPLAY_FORMATS = {'MARKET_MOBILE_V2', FORMAT}
REPORT_CONTRACT_REVISION = 6
# These are model review responsibilities, not a prose classifier or score.
# Frozen revisions retain the review keys used when their source was authored.
READING_REVIEW = {
    'decision_delivery': '对照最终正文原句与冻结结果，分别核验当前状态、有界未来竞争路径及相对把握、公开现价价值和策略适用性、个人总体取舍；方向句、未反转或条件清单不能代替未来判断及选择。有限反馈说明完成范围与上游未决，缺研究返回原研究，缺表达修稿。',
    'evidence_priority': '指出支撑主线的决定性证据与关键反证及其正文原句，核验各分句的场所、产品、窗口和确认程度；完整数字与历史流水只在影响取舍时展示。',
    'maintenance_separation': '对照最终正文说明核验记录、哈希、内部到期续期、冻结采用发送状态和完整缺口详单如何留在技术依据或原维护；必要限制以对价值、风险或动作的具体影响保留。',
    'narrative_focus': '逐段对照正文说明各段推进的市场因果或个人取舍，实际删并重复结论与重复否定；开头独立市场、随后论证、再个人安排及改变条件，收束没有重述全部结论。',
}
BODY_AUTHORING_INSTRUCTIONS = (
    '面向个人直接交付完整主题和自然段：独立市场裁决分别承接当前方向、拐点进展及已形成的有界未来路径比较，'
    '随后主证据和重要反证、权益宏观及产品影响，再交付个人总体取舍与必要分项，最后说明改变判断的条件。'
    '公开策略适用性保留公开价格风险依据；个人资金优先级及保护配合不插入独立市场结论。仅承接冻结已形成结果，不重新研究、不提高确认程度或补造价量。'
    '数据时点在开头简明说明一次；其他日期仅在影响判断或行动时保留。'
    '研究及个人叙述是来源，不是逐段必抄的正文；选择决定性证据，集中说明关键未知的实际影响。'
    '核验、哈希、内部期限、冻结采用发送状态、历史事实核对流水及完整缺口详单留在已有来源索引、审阅和维护材料。'
    '正文不按内部维护组织，不附机构式风险声明；不强制交易，不以未知默认安全。'
    '长度按用户阅读需要及内容完整性取舍，不硬截断、不用字数、关键词或固定段数代替语义验收。'
)


def quality_keys(context):
    from .cycle import REPORT_CHECKS, SUMMARY_CHECKS
    expected = QUALITY | (REPORT_CHECKS if context.get('cycle_policy_revision') == 1 else set())
    if context.get('deep_report_policy_revision') == 1:
        expected |= SUMMARY_CHECKS
    if context.get('report_contract_revision', 1) >= 3:
        expected.add('market_independence')
    if context.get('market_revision', 0) >= 8:
        expected |= {'overall_choice', 'upstream_completeness'}
    if context.get('report_contract_revision', 1) >= 4:
        expected |= set(READING_REVIEW)
    return expected
PUBLIC = ('/assessment/outlook/', '/assessment/factor_assessment/',
          '/observation/review/market_assessment/', '/observation/review/dispute_zones/',
          '/observation/review/factor_checks/', '/metrics/', '/public_evidence/', '/report_subjects/')
NUMBER = re.compile(r'(?<![A-Za-z0-9_])[-+]?\d+(?:,\d{3})*(?:\.\d+)?')


def numbers(value):
    value = re.sub(r'^\s*\d+[.)]\s+', '', str(value), flags=re.M)
    value = re.sub(r'(\[[^\]]*\])\([^\n)]*\)', r'\1', value)
    return {Decimal(n.replace(',', '')) for n in NUMBER.findall(INDEX_NAME.sub('', value))}


def business_text(body):
    """Only formatting normalization; paraphrase equivalence remains model-owned."""
    if body is None:
        return None
    lines = []
    for line in body.splitlines():
        if re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*', line):
            continue
        line = re.sub(r'^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)', '', line)
        line = line.replace('**','').replace('__','')
        # Table cell boundaries are layout. Text and punctuation remain.
        lines.append(line.replace('|',' ') if line.strip().startswith('|') and line.strip().endswith('|') else line)
    return re.sub(r'\s+', ' ', ' '.join(lines)).strip()


def evaluation_time(context):
    if not context.get('evaluated_at'):
        return None
    dt = instant(context.get('authored_evaluated_at', context['evaluated_at'])).astimezone(timezone(timedelta(hours=9)))
    return '评估时间：'+str(dt.year)+'年'+str(dt.month)+'月'+str(dt.day)+'日 '+dt.strftime('%H:%M')+'（日本时间）'


def price_status(value, decision):
    statuses = value['layout']['price_status']
    exact(statuses, 'lower_spot upper_short', 'authored price status')
    require(set(statuses.values()) <= {'VALID', 'ADJUSTED', 'REVIEW_REQUIRED', 'WITHDRAWN'}, 'invalid price applicability')
    ctx = value['context']
    pending = (ctx.get('cycle_policy_revision') == 1 and ctx['cycle_baseline']['status'] != 'CURRENT')
    pending |= ctx.get('identity', {}).get('fact_version', decision['assessment'].get('fact_version')) != decision['assessment'].get('fact_version')
    checked = max(instant(ctx['knowledge_cutoff']), instant(ctx.get('evaluated_at', ctx['knowledge_cutoff'])))
    if decision['assessment'].get('revision',0)>=8:
        from .investment import decision as investment_decision
        whole=investment_decision(decision['assessment'])
        pending |= whole is None or instant(whole['valid_until'])<=checked
        if whole is not None:
            pending |= whole['fact_version']!=ctx.get('identity',{}).get('fact_version',decision['assessment'].get('fact_version'))
    for side, status in statuses.items():
        rec = decision['assessment']['recommendations'][side]
        if rec['status'] in ('EXECUTION_LIMITED', 'UNDETERMINED'):
            require(status == 'REVIEW_REQUIRED', 'execution-limited direction is not an executable tier or a withdrawn decision')
        elif rec['status'] == 'NOT_RECOMMENDED':
            require(status in {'REVIEW_REQUIRED', 'WITHDRAWN'}, 'inactive advice cannot be presented as valid')
        elif pending or instant(rec['validity']['until']) <= checked:
            require(status == 'REVIEW_REQUIRED', 'unreviewed or expired advice cannot be presented as valid')
        else:
            require(status != 'WITHDRAWN', 'withdrawn price needs an inactive formal recommendation')


def validate_price(binding, values):
    price = binding.get('price')
    if price is None:
        return
    exact(price, 'subject unit', 'bound price identity')
    subject, unit = price['subject'], price['unit']
    policy = values.get('report_quote_policy')
    if policy:
        require(unit in policy.get('accepted_quote_units', [policy['quote_unit']]), 'user report price unit is outside the selected dollar quote policy')
        require(policy['show_underlying_prices'] or subject not in ('SPX', 'NDX'),
                'underlying index levels stay in research, not the unified product report')
    label = {'index points':'点', 'INDEX_POINTS':'点', 'points':'点', 'USD/share':'USD/股'}.get(unit, unit)
    require(re.search(r'(?<![A-Za-z])'+re.escape(label)+r'(?![A-Za-z])', binding['text']) is not None,
            'displayed price unit differs from binding')
    refs = binding['refs']
    if subject in ('QQQ', 'VOO'):
        row = values['report_subjects'].get(subject) or {}
        require(row.get('identity_status') == 'VERIFIED' and row.get('quote_unit') == unit,
                'product price needs verified identity and matching unit')
        prefix = '/report_subjects/' + subject + '/'
        require(all(p.startswith((prefix + 'quote/', prefix + 'product_zones/')) for p in refs),
                'index or other instrument values cannot become product prices')
        require(row.get('quote') is not None or any(p.startswith(prefix+'product_zones/') for p in refs),
                'product quote is unavailable')
    else:
        # Non-product prices retain their source instrument and quote unit.
        for ref in refs:
            parts = ref.strip('/').split('/')
            owner = None
            for end in range(len(parts)-1, 0, -1):
                item = pointer(values, '/'+'/'.join(parts[:end]))
                if isinstance(item, dict) and 'quote_unit' in item:
                    owner = item
                    break
            require(owner is not None and owner['quote_unit'] == unit, 'price requires source quote unit')
            instrument = owner.get('market') if 'area' in owner else owner.get('instrument')
            require(instrument == subject or (subject == 'BTC' and instrument in ('BTCUSDT','BTCUSD','BTC/USDT','BTC/USD')),
                    'price belongs to a different instrument')


def candidate_hash(value):
    """Bind the actual review target, not an assertion that its meaning is sound."""
    layout = value['layout']
    return digest({'context_id': value['context']['context_id'],
                   'result_binding': value.get('result_binding'),
                   'layout': {key: layout[key] for key in ('format', 'body', 'bindings', 'price_status')}})


def preview(value, decision):
    """Preflight the whole candidate without accepting its semantic review."""
    body, _ = render(value, decision, require_review=False)
    return {'valid': True, 'scope': 'structure_and_bindings_only',
            'semantic_review': 'PENDING_ACTUAL_FULL_BODY_REVIEW',
            'reviewed_candidate_hash': candidate_hash(value), 'body_hash': digest(body.encode())}


def render(value, decision, *, require_review=True):
    layout = value['layout']; ctx = value['context']
    exact(layout, 'format body bindings price_status quality_review' +
          (' reviewed_candidate_hash' if ctx.get('report_contract_revision', 1) >= 5 else ''), 'authored report layout')
    require(layout['format'] == FORMAT, 'invalid authored report format')
    body = layout['body']; text(body, 'final authored Markdown')
    require('{{' not in body and '}}' not in body, 'final body cannot contain unresolved template references')
    from .cycle import validate_report_basis
    expected = quality_keys(ctx)
    if ctx.get('deep_report_policy_revision') == 1:
        validate_report_basis(value, ctx)
    require(isinstance(layout['quality_review'], dict) and set(layout['quality_review']) == expected,
            'complete authored expression review required')
    for key, explanation in layout['quality_review'].items():
        if not require_review and explanation is None:
            continue
        text(explanation, key); require(len(explanation.strip()) >= 12, 'expression review needs concrete reasoning')
    if require_review and ctx.get('report_contract_revision', 1) >= 5:
        require(layout['reviewed_candidate_hash'] == candidate_hash(value),
                'final report changed or has not been reviewed; preflight and reread the actual candidate')
    price_status(value, decision)
    values = {k: ctx.get(k) for k in ('metrics', 'observation', 'public_evidence', 'report_subjects', 'report_quote_policy')}
    values.update(assessment=decision['assessment'], evaluation_time=evaluation_time(ctx))
    if ctx.get('market_revision', 0) >= 6:
        from .closed_loop import validate_report_binding
        values.update(validate_report_binding(value, ctx))
    # An unfinished synthesis remains an upstream obligation, but must not
    # suppress a contract-validated limited risk/impact report. The projected
    # inputs already remove executable outputs and preserve their limitations.
    limited_personal = (ctx.get('deep_report_policy_revision') == 1
                        and value['report_basis']['mode'] == 'LIMITED_FEEDBACK'
                        and values.get('investment_decision') is None)
    require(isinstance(layout['bindings'], list), 'authored evidence bindings required')
    covered = set()
    for binding in layout['bindings']:
        exact(binding, 'text refs scope'+(' price' if 'price' in binding else ''), 'authored evidence binding')
        excerpt = binding['text']; text(excerpt, 'bound report excerpt')
        require(excerpt in body, 'evidence binding must occur verbatim in the final body')
        require(binding['scope'] in ('market', 'personal', 'clock'), 'invalid report binding scope')
        require(isinstance(binding['refs'], list) and binding['refs'], 'report excerpt needs frozen references')
        supported = set()
        for ref in binding['refs']:
            if binding['scope'] == 'market':
                allowed = ('/market_result/', '/metrics/', '/public_evidence/', '/report_subjects/') if ctx.get('market_revision', 0) >= 6 else PUBLIC
                require(ref.startswith(allowed), 'market conclusion cannot bind personal portfolio data or internal research prose')
            if binding['scope'] == 'personal' and ctx.get('market_revision', 0) >= 6:
                personal_result = '/portfolio_risk/' if ctx.get('market_revision', 0) >= 7 else '/allocation_decision/'
                allowed = (personal_result, '/applicability/portfolio/', '/execution_plans/')
                if ctx.get('market_revision', 0) >= 7:
                    allowed += ('/strategy_advice/',)
                if ctx.get('market_revision', 0) >= 8:
                    allowed += ('/investment_decision/', '/portfolio_narrative')
                    if limited_personal:
                        require(values['applicability']['portfolio']['outcome'] == 'REVISE',
                                'limited personal feedback requires an actual synthesis applicability gap')
                        require(all(s in {'REVIEW_REQUIRED', 'WITHDRAWN'} for s in layout['price_status'].values()),
                                'limited personal feedback cannot make advice executable')
                        allowed = ('/portfolio_risk/', '/applicability/portfolio/')
                    else:
                        require(values.get('investment_decision') is not None, 'personal synthesis is unfinished; return upstream before a complete personal report')
                require(ref.startswith(allowed), 'personal report consumes only assessed risk, applicable advice and limits')
            if binding['scope'] == 'clock':
                require(ref == '/evaluation_time' and excerpt == values['evaluation_time'], 'only frozen evaluation time is a clock binding')
            scalar = pointer(values, ref)
            require(scalar is not None and not isinstance(scalar, (dict, list, bool)), 'report reference must be a known scalar')
            supported |= numbers(scalar)
            if (binding['scope'] == 'market' and ref.rsplit('/', 1)[-1] in {'price','open','high','low','close','lower','upper'}
                    and isinstance(scalar, (int, float, str)) and re.fullmatch(r'[-+]?\d+(?:\.\d+)?', str(scalar))):
                require('price' in binding, 'market price binding must identify its instrument and unit')
            for side, status in layout['price_status'].items():
                if ref.startswith(('/assessment/recommendations/'+side+'/tiers/', '/execution_plans/'+side+'/tiers/')):
                    require(binding['scope'] == 'personal' and status in {'VALID','ADJUSTED'},
                            'withdrawn or expired recommendation prices cannot be restored')
        require(numbers(excerpt) <= supported, 'authored numbers must match their frozen references without conversion')
        validate_price(binding, values)
        covered |= numbers(excerpt)
    if ctx.get('market_revision',0)>=8 and any(b['scope']=='personal' for b in layout['bindings']):
        if limited_personal:
            require(any(ref.startswith('/applicability/portfolio/limitations/')
                        for b in layout['bindings'] if b['scope']=='personal' for ref in b['refs']),
                    'limited personal feedback must consume the actual applicability limitation')
            require(not values['portfolio_risk'] or not values['portfolio_risk'].get('actions'),
                    'unfinished synthesis cannot supply new risk actions')
    if (ctx.get('market_revision',0)>=8 and not limited_personal
            and any(b['scope']=='personal' for b in layout['bindings'])):
        require(any(ref.startswith(('/investment_decision/summary','/investment_decision/priorities/','/investment_decision/supported_judgements/'))
                    for b in layout['bindings'] if b['scope']=='personal' for ref in b['refs']),
                'complete personal report must consume the formed overall choice, not only parallel risk and opportunity results')
        limits=values['investment_decision'].get('validation_limits',[])
        if any(l['unsupported_capabilities'] or l['basis']=='METHOD_CHECK' for l in limits):
            require(any(re.fullmatch(r'/investment_decision/(?:validation_limits/\d+/(?:limitations|unsupported_capabilities)/\d+|gaps/\d+/(?:consequence|required_evidence))',ref)
                        for b in layout['bindings'] if b['scope']=='personal' for ref in b['refs']),
                    'unsupported implementation capabilities must reach the personal report consumer through concrete limitations or their decision consequences')
    require(numbers(body) <= covered, 'all report numbers need frozen evidence bindings')
    if ctx.get('report_contract_revision', 1) >= 3:
        conclusions = [b for b in layout['bindings'] if b['scope'] != 'clock']
        require(conclusions and min(conclusions, key=lambda b: body.index(b['text']))['scope'] == 'market',
                'report must present the independent public conclusion before personal application')
        remaining = body
        for binding in sorted(layout['bindings'], key=lambda item: len(item['text']), reverse=True):
            remaining = remaining.replace(binding['text'], '')
        require(not business_text(remaining),
                'all report prose needs explicit public, personal or clock ownership, not only numbers')
    # The whole source is the delivery assertion: all prose, whitespace, order,
    # tables and caveats are protected, without forcing display sections.
    constraints = [{'id':'authored-body', 'kind':'condition', 'source_text':body}]
    line = evaluation_time(ctx)
    if line and body.startswith(line+'\n\n') and any(b['scope'] == 'clock' and b['text'] == line for b in layout['bindings']):
        constraints.append({'id':'authored-clock', 'kind':'deadline', 'source_text':line})
    return body, constraints
