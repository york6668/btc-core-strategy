"""Render a flexible authored layout exclusively from one accepted assessment."""
import re
import decision_report
from .common import digest, exact, pointer, require, text

TOKEN = re.compile(r'\{\{(/[A-Za-z0-9_~/.-]+)\}\}')
INTERNAL = re.compile(r'(?i)(?:gpt[-‑–]?\d|\b(?:Sol|Astra|Terra)/(?:high|max)|sha256:|fingerprint|report_required|DecisionSnapshot|EvidenceBundle|AssessmentDraft|A/D/E|::inbox|策略状态ID|内部控制面|BTC-[A-Z]|position_fact|validation_projection|\bV\d+\b|RETEST_PENDING|RETEST_CONFIRMED|CURRENT_CYCLE_VALIDATED)')
MONEY = re.compile(r'\d[\d,.]*\s*(?:U\b|USDT|USD|美元)', re.I)
COVERAGE = {'outlook', 'causes', 'alternatives', 'uncertainty', 'risk', 'next_evidence', *decision_report.MODULES}


def render(assessment):
    if 'format' in assessment['report']:
        from .brief_report import render as render_brief
        return render_brief(assessment)
    if assessment.get('revision') == 2:
        return render_recommendations(assessment)
    return _render(assessment, COVERAGE)


def bind_template(assessment, template, numeric_literals=False):
    """Resolve reviewed text and numbers without forcing internal narratives into a report."""
    text(template, 'report text')
    bound_paths = set()
    literal = re.compile(r'\d') if numeric_literals else MONEY
    require(not literal.search(TOKEN.sub('', template)), 'literal numbers must bind assessment')
    def substitute(match, depth=0):
        require(depth < 8, 'cyclic report binding')
        path = match[1]
        bound_paths.add(path)
        require(path.startswith(('/conclusion/', '/recommendations/', '/outlook/', '/robustness/', '/portfolio_analysis/', '/domain/report_semantics/', '/domain/portfolio_decision/', '/domain/value_zones/', '/domain/trade_candidates/')), 'report references control data')
        value = pointer(assessment, path)
        require(isinstance(value, (str, int)) and not isinstance(value, bool), 'report reference must be scalar')
        value = str(value)
        # Numeric scalars are authoritative; digits inside authored prose still need bindings.
        is_number = re.fullmatch(r'-?\d+(?:\.\d+)?', value) is not None
        require(is_number or not literal.search(TOKEN.sub('', value)), 'authored numbers must bind numeric decision fields')
        return TOKEN.sub(lambda m: substitute(m, depth + 1), value)
    body = TOKEN.sub(substitute, template)
    require('{{' not in body and '}}' not in body and not INTERNAL.search(body), 'report leaks control data or unresolved binding')
    return body, bound_paths


def validate_changes(assessment, comparison):
    if assessment['report'].get('format'):
        from .brief_report import validate_changes as check
        check(assessment, comparison)


def _render(assessment, required):
    layout = assessment['report']
    exact(layout, 'blocks', 'report layout')
    require(isinstance(layout['blocks'], list) and layout['blocks'], 'report needs blocks')
    rendered, covered, bound_paths = [], set(), set()
    for block in layout['blocks']:
        exact(block, 'template coverage', 'report block')
        text(block['template'], 'report text')
        require(isinstance(block['coverage'], list) and set(block['coverage']) <= required, 'invalid report coverage')
        covered.update(block['coverage'])
        template = block['template']
        require(not MONEY.search(TOKEN.sub('', template)), 'literal monetary values must bind assessment')
        def substitute(match, depth=0):
            require(depth < 8, 'cyclic report binding')
            path = match[1]
            bound_paths.add(path)
            require(path.startswith(('/conclusion/', '/recommendations/', '/outlook/', '/robustness/', '/portfolio_analysis/', '/domain/report_semantics/', '/domain/portfolio_decision/', '/domain/value_zones/', '/domain/trade_candidates/')), 'report references control data')
            value = pointer(assessment, path)
            require(isinstance(value, (str, int)) and not isinstance(value, bool), 'report reference must be scalar')
            value = str(value)
            require(not MONEY.search(TOKEN.sub('', value)), 'authored monetary values must bind numeric decision fields')
            return TOKEN.sub(lambda m: substitute(m, depth + 1), value)
        body = TOKEN.sub(substitute, template)
        require('{{' not in body and '}}' not in body and not INTERNAL.search(body), 'report leaks control data or unresolved binding')
        rendered.append(body)
    require(covered == required, 'report omits required business meaning: ' + ','.join(sorted(required-covered)))
    for module in decision_report.MODULES:
        require('/portfolio_analysis/' + module + '/narrative' in bound_paths, 'module coverage lacks actual module explanation')
        from decimal import Decimal
        if Decimal(assessment['domain']['portfolio_decision'][module]['amount_u']) != 0:
            require('/domain/portfolio_decision/' + module + '/amount_u' in bound_paths, 'report omits nonzero formal amount')
        if assessment['portfolio_analysis'][module]['semantic'].get('scenario_budget'):
            require('/portfolio_analysis/' + module + '/semantic/scenario_budget/amount_u' in bound_paths, 'report omits material prospective budget')
    require('/outlook/narrative' in bound_paths and '/robustness/narrative' in bound_paths, 'report omits actual outlook or robustness')
    return '\n\n'.join(rendered)


def render_recommendations(assessment):
    from copy import deepcopy
    from .recommendations import SIDES, validate, sequences
    exact(assessment['report'], 'overview blocks', 'recommendation report layout')
    validate(assessment['recommendations'], assessment['temporal_scope']['knowledge_cutoff'], lambda ref: None)
    text(assessment['report']['overview'], 'current portfolio overview')
    blocks = [{'template': '综合方案：' + assessment['report']['overview'], 'coverage': []}]
    for side, title, unit in (('upper_short', '上沿空单', 'U名义金额'), ('lower_spot', '下沿现货篮子', 'U投入金额')):
        value = assessment['recommendations'][side]
        if side == 'lower_spot' and any(set(t.get('allocation_percent', {})) == {'BTC'} for t in value['tiers']):
            title = '现货篮子与独立BTC建议'
        prefix = '/recommendations/' + side
        token = lambda path: '{{' + prefix + path + '}}'
        if value['status'] == 'NOT_RECOMMENDED':
            summary = title + '：当前不建议。'
        else:
            lines = [title, '', '| 建议BTC参考位置（USDT/BTC） | 建议金额 | 计划资金占用 |', '|---|---:|---:|']
            for i, tier in enumerate(value['tiers']):
                p = '/tiers/' + str(i)
                price = token(p + '/price_low_usd')
                if tier['price_high_usd'] != tier['price_low_usd']:
                    price += '—' + token(p + '/price_high_usd')
                if side == 'lower_spot' and any('allocation_percent' in t for t in value['tiers']):
                    from .recommendations import spot_label
                    price = spot_label(tier) + ' ' + price
                capital = token(p + '/capital_u') + 'U' if tier['capital_u'] is not None else '保证金与缓冲尚未确定'
                lines.append('| ' + price + ' | ' + token(p + '/amount_u') + unit + ' | ' + capital + ' |')
            summary = '\n'.join(lines)
        blocks.append({'template': summary, 'coverage': [side]})
    for side in SIDES:
        p = '/recommendations/' + side
        blocks.append({'template': '{{' + p + '/rationale}}\n\n{{' + p + '/impact}}\n\n本建议复核期限：{{' + p + '/validity/until}}。建议及失效条件由Codex持续复核；到价记录本身不构成成交。', 'coverage': []})
    p = '/recommendations/portfolio/'
    blocks.append({'template': '{{' + p + 'narrative}}\n\n{{' + p + 'existing_exposure}} {{' + p + 'uncertainty}}', 'coverage': ['recommendation_funding']})
    copy = deepcopy(assessment)
    copy['report'] = {'blocks': blocks + assessment['report']['blocks']}
    body = _render(copy, COVERAGE | set(SIDES) | {'recommendation_funding'})
    # Four arithmetic paths are computed from the same tiers, not another amount owner.
    scenarios = sequences(assessment['recommendations'])
    labels = {'spot_only': '仅现货', 'short_only': '仅空单', 'spot_then_short': '现货后空单', 'short_then_spot': '空单后现货'}
    rows = ['两侧分别或先后全部采用的计划增量（不代表已执行）：', '', '| 路径 | 累计资金占用 | 现货投入 | 空单名义金额 |', '|---|---:|---:|---:|']
    for key, steps in scenarios.items():
        last = steps[-1]
        capital = last['cumulative_capital_u'] + 'U' if last['cumulative_capital_u'] is not None else last['known_capital_u'] + 'U已知计划占用＋未确定的空单占用'
        rows.append('| ' + labels[key] + ' | ' + capital + ' | ' + last['spot_capital_u'] + 'U | ' + last['short_notional_u'] + 'U |')
    if any(assessment['recommendations'][s]['tiers'] for s in SIDES):
        body += '\n\n' + '\n'.join(rows)
    require(not re.search(r'请(?:自行|你自己)(?:判断|分析)|由你判断.*条件', body), 'report returns market judgement to user')
    return '市场数据截止：' + assessment['temporal_scope']['market_cutoff'] + '；评估证据截止：' + assessment['temporal_scope']['knowledge_cutoff'] + '。小时观察存在采样和深评延迟。\n\n' + body


def freeze(assessment, decision_id):
    body = render(assessment)
    return freeze_body(body, decision_id)


def freeze_body(body, decision_id):
    """Caller verifies immutable version bytes; renderer changes cannot rewrite history."""
    payload = {'schema': 'BTC_REPORT_V4', 'decision_id': decision_id, 'body': body, 'body_hash': digest(body.encode())}
    payload['report_id'] = digest(payload)
    return payload
