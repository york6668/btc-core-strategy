"""Concise presentation of a complete assessment; no market inference or state writes.

Bindings pin each authored sentence to reviewed semantic topics, not to full
internal narratives. They detect stale/omitted coverage; the main reviewer still
checks what the sentence actually says, including why an exceptional risk matters.
"""
import re
from decimal import Decimal
from datetime import timezone

import decision_report
from . import recommendations, semantics
from .common import digest, exact, instant, numbered, require
from .report import bind_template

FORMAT = 'CONCISE_V1'


def sentence(template, assessment, topics):
    """Mechanical binding helper. The caller authors and reviews the sentence."""
    stable = semantics.stable(assessment)
    require(set(topics) <= set(stable), 'unknown concise report topic')
    return {'template': template, 'bindings': {k: digest(stable[k]) for k in topics}}


def _block(assessment, block, stable, limit):
    exact(block, 'template bindings', 'concise sentence')
    bindings = block['bindings']
    require(isinstance(bindings, dict) and bindings and set(bindings) <= set(stable), 'concise sentence needs reviewed semantic bindings')
    for topic, expected in bindings.items():
        require(expected == digest(stable[topic]), 'stale concise sentence binding: ' + topic)
    body, paths = bind_template(assessment, block['template'], numeric_literals=True)
    require('\n' not in body and '\r' not in body and '|' not in body and len(body) <= limit, 'concise sentence is too long or contains extra layout')
    # Decimal points and linked URLs are not sentence boundaries.
    prose = re.sub(r'\[[^\]]*\]\([^)]+\)', '', body)
    parts = [v for v in re.split(r'[。！？!?]+|(?<!\d)\.(?:\s|$)', prose) if v.strip()]
    require(len(parts) <= 1, 'concise block must contain one sentence')
    require(not re.search(r'请(?:自行|你自己)(?:判断|分析)|由你判断.*条件', body), 'report returns market judgement to user')
    return body, paths, set(bindings)


def _content(assessment):
    require(assessment.get('revision', 1) >= 2, 'concise report requires recommendations')
    layout = assessment['report']
    exact(layout, 'format overview notes risk_notes', 'concise report layout')
    require(layout['format'] == FORMAT, 'unsupported report format')
    recommendations.validate(assessment['recommendations'], assessment['temporal_scope']['knowledge_cutoff'], lambda ref: None)
    require(isinstance(layout['notes'], list) and len(layout['notes']) <= 2, 'ordinary concise report allows at most two notes')
    require(isinstance(layout['risk_notes'], list), 'risk notes must be a list')
    stable = semantics.stable(assessment)
    overview, paths, covered = _block(assessment, layout['overview'], stable, 120)
    require({'decision', 'portfolio'} <= covered, 'overview must bind current portfolio decisions')
    notes, risks = [], []
    for field, result in (('notes', notes), ('risk_notes', risks)):
        for block in layout[field]:
            body, bound, topics = _block(assessment, block, stable, 180)
            if field == 'risk_notes':
                require('robustness' in topics, 'extra note requires reviewed risk binding')
            result.append(body)
            paths.update(bound)
            covered.update(topics)
    for module in decision_report.MODULES:
        if Decimal(assessment['domain']['portfolio_decision'][module]['amount_u']) != 0:
            require('/domain/portfolio_decision/' + module + '/amount_u' in paths, 'report omits nonzero formal amount')
    return overview, notes, risks, covered


def validate_changes(assessment, comparison):
    _, _, _, covered = _content(assessment)
    changed = {'outlook' if key == 'new_material_outlook' else key for key in comparison['changed']}
    require(changed <= covered, 'concise report omits material change: ' + ','.join(sorted(changed - covered)))


def _number(value):
    return format(Decimal(numbered(value)), ',f')


def render(assessment):
    overview, notes, risks, _ = _content(assessment)
    scope = assessment['temporal_scope']
    stamp = lambda key: instant(scope[key]).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines = ['评估：' + stamp('knowledge_cutoff') + '；行情截至：' + stamp('market_cutoff'), '',
             '**当前安排：' + overview + '**', '',
             '| 建议 | BTC参考位置（USDT/BTC） | 金额 |', '|---|---:|---:|']
    for side, label, unit in (('upper_short', '上沿空单', 'U名义额'), ('lower_spot', '下沿现货篮子', 'U投入')):
        value = assessment['recommendations'][side]
        if value['status'] == 'NOT_RECOMMENDED':
            lines.append('| ' + label + ' | 当前不建议 | — |')
            continue
        for tier in value['tiers']:
            from .recommendations import spot_label
            tier_label = spot_label(tier) if side == 'lower_spot' and 'allocation_percent' in tier else label
            price = _number(tier['price_low_usd'])
            if Decimal(tier['price_low_usd']) != Decimal(tier['price_high_usd']):
                price += '–' + _number(tier['price_high_usd'])
            lines.append('| ' + tier_label + ' | ' + price + ' | ' + _number(tier['amount_u']) + unit + ' |')
    if notes:
        lines.extend(['', ''.join(notes)])
    for risk in risks:
        lines.extend(['', '重要风险：' + risk])
    return '\n'.join(lines)
