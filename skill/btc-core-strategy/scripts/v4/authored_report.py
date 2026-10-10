"""Validate the author's finished Markdown without rendering or rewriting it."""
import re
from decimal import Decimal
from datetime import timedelta, timezone
from .common import exact, instant, pointer, require, text
from .mobile_report import QUALITY, INDEX_NAME

FORMAT = 'AUTHORED_REPORT_V1'
DISPLAY_FORMATS = {'MARKET_MOBILE_V2', FORMAT}
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


def render(value, decision):
    layout = value['layout']; ctx = value['context']
    exact(layout, 'format body bindings price_status quality_review', 'authored report layout')
    require(layout['format'] == FORMAT, 'invalid authored report format')
    body = layout['body']; text(body, 'final authored Markdown')
    require('{{' not in body and '}}' not in body, 'final body cannot contain unresolved template references')
    from .cycle import REPORT_CHECKS, SUMMARY_CHECKS, validate_report_basis
    expected = QUALITY | (REPORT_CHECKS if ctx.get('cycle_policy_revision') == 1 else set())
    if ctx.get('deep_report_policy_revision') == 1:
        expected |= SUMMARY_CHECKS
        validate_report_basis(value, ctx)
    if ctx.get('report_contract_revision', 1) >= 3:
        expected.add('market_independence')
    require(isinstance(layout['quality_review'], dict) and set(layout['quality_review']) == expected,
            'complete authored expression review required')
    for key, explanation in layout['quality_review'].items():
        text(explanation, key); require(len(explanation.strip()) >= 12, 'expression review needs concrete reasoning')
    price_status(value, decision)
    values = {k: ctx.get(k) for k in ('metrics', 'observation', 'public_evidence', 'report_subjects', 'report_quote_policy')}
    values.update(assessment=decision['assessment'], evaluation_time=evaluation_time(ctx))
    if ctx.get('market_revision', 0) >= 6:
        from .closed_loop import validate_report_binding
        values.update(validate_report_binding(value, ctx))
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
