"""Build the first risk edition from adopted decisions without exposing personal inputs to its public author."""
from __future__ import annotations

from . import trading_decisions, trading_delivery, trading_policy, trading_portfolio, trading_public_report, trading_report
from .codec import Conflict, identity, millis
from .entities import Entities
from .trading_report_inputs import public_inputs


class SeedPending(Conflict):
    """A verified event has no complete source edition yet."""


def _source_market(db, base):
    if base and base.get('seeded'):
        original = base.get('public_market') or (base.get('seed_sources') or {}).get('market') or next(
            (ref for ref in base.get('decisions', []) if ref['kind'] == 'DECISION'), None)
        if not original:
            raise SeedPending('ORIGINAL_PUBLIC_MARKET_REQUIRED')
        market = Entities(db).get(original['kind'], original['object_id'], version=original['version'])
        if not market or trading_decisions.binding(market) != original or market['scope'] != 'PUBLIC':
            raise SeedPending('ORIGINAL_PUBLIC_MARKET_REQUIRED')
        return market
    from .trading import MARKET
    return trading_decisions.current_market(db, MARKET)


def public_source(db, base=None):
    market = _source_market(db, base)
    return public_inputs(market) if market else None


def _public_copy(db, run, target, market, base, supplied):
    from .trading import material
    if supplied is None:
        copy = trading_public_report.read(db, target, market, base)
        if copy:
            return copy
        if trading_public_report.legacy_inputs(db, target, base).get('legacy_review_required'):
            raise SeedPending('LEGACY_PUBLIC_BASELINE_REVIEW_REQUIRED')
        raise SeedPending('PUBLIC_REPORT_SECTIONS_REQUIRED')
    if (not isinstance(supplied, dict) or not {'market', 'sections'} <= supplied.keys()
            or supplied.keys() - {'market', 'sections', 'reviewed_unchanged'}):
        raise ValueError('public_seed needs the exact market and its public report sections')
    if supplied['market'] != trading_decisions.binding(market):
        raise SeedPending('PUBLIC_REPORT_SECTIONS_CHANGED')
    author_run = run
    if base and base.get('seeded'):
        author_run = {**run, 'known_at': base.get('reviewed_at') or base['assessed_at']}
    return trading_public_report.prepare(db, author_run, target, market, base, {
        'base_report_id': (base or {}).get('report_id'), 'changes': supplied['sections'],
        'reviewed_unchanged': supplied.get('reviewed_unchanged', [])},
        independent=not material(db, run).get('market_binding'))


def migrate_public(db, run, target, base, supplied):
    market = _source_market(db, base)
    copy = _public_copy(db, run, target, market, base, supplied)
    return {**base, **({'risk_public_copy': copy['copy_id']} if supplied is None else {}),
            **({'legacy_public_review': copy['legacy_review_id']} if copy.get('legacy_review_id') else {}),
            'modules': {**base['modules'], **copy['sections']}, 'public_market': copy['market'],
            'basis': {**base.get('basis', {}), **copy['basis']}, 'market_sources': copy['sources']}


def _label(subject, policy, scoped):
    if policy['kind'] == 'MONTHLY':
        weights = (scoped or {}).get('inputs', {}).get('weights', {})
        if weights and scoped['basis'] == policy['basis']:
            return '、'.join(weights) + ' 月度组'
    if subject == 'independent-opportunity':
        return '独立机会'
    return subject


def _choice_text(subject, record, scope):
    policy, choice = record['payload']['policy'], record['payload']['result']
    name = _label(subject, policy, scope.get(subject))
    decisions = {'CONTINUE': '继续', 'SKIP': '跳过', 'PARTICIPATE': '参与', 'WAIT': '等待', 'AVOID': '回避',
                 'UNRESOLVED': '待复评', 'KEEP': '维持', 'REDUCE': '减持', 'EXIT': '退出', 'HEDGE': '对冲',
                 'NO_POSITION': '未持有'}
    selection = decisions.get(choice['decision'], choice['decision'])
    period = choice.get('month', '')
    parts = ['**' + ' '.join(value for value in (name, period, selection) if value) + '。**']
    unit = policy.get('currency', '')
    if 'budget' in policy:
        parts.append('规划预算 ' + policy['budget'] + ' ' + unit + '。')
    if 'amount' in choice:
        amount = choice['amount']
        parts.append(('本次计划 ' + amount + ' ' + unit + '。') if amount is not None else '本次金额待核定。')
    for field in ('reason', 'amount_basis', 'gap'):
        if choice.get(field) and choice[field] not in parts:
            parts.append(choice[field])
    return ' '.join(parts)


def _portfolio_inputs(db, run, release, assessed_at):
    from .trading import confirmed_files, personal_state, policies
    portfolio = trading_portfolio.current(db)
    if not portfolio or portfolio['known_ms'] > millis(assessed_at):
        raise SeedPending('PORTFOLIO_ADOPTION_REQUIRED')
    scope, status = policies(db, release)
    plan = portfolio['payload']
    records, pending = {}, set()
    files = confirmed_files(db)
    objects = Entities(db)
    for subject, ref in {**plan.get('unavailable_members', {}), **plan['decisions']}.items():
        if subject not in scope and subject not in status['unavailable']:
            continue
        member = objects.get(ref['kind'], ref['object_id'], version=ref['version'])
        if not member or trading_decisions.binding(member) != ref or member['known_ms'] > millis(assessed_at):
            raise SeedPending('PORTFOLIO_MEMBER_SOURCE_REQUIRED')
        records[subject] = member
        current = objects.get(ref['kind'], ref['object_id'])
        if (not current or trading_decisions.binding(current) != ref or subject not in scope
                or personal_state(db, member, scope[subject], files, run)['status'] not in {'CURRENT', 'UNRESOLVED'}):
            pending.add('allocation.' + subject)
    missing = (scope.keys() | status['unavailable'].keys()) - records.keys()
    if not records and (missing or status['state'] != 'CURRENT'):
        raise SeedPending('PORTFOLIO_SCOPE_REASSESSMENT_REQUIRED')
    pending.update('allocation.' + s for s in missing)
    binding = {'portfolio': trading_decisions.binding(portfolio),
               'scope': identity({s: v['basis'] for s, v in scope.items()}, status, sorted(pending))}
    return scope, portfolio, records, pending, missing, binding


def build(db, run, release, assessed_at, public_seed, previous):
    from .trading import MARKET, destination, report_allocation, report_basis, report_judgement_times
    market = trading_decisions.current_market(db, MARKET)
    if not market or market['known_ms'] > millis(assessed_at):
        raise SeedPending('PUBLIC_MARKET_ADOPTION_REQUIRED')
    copy = _public_copy(db, run, destination(db), market, previous, public_seed)
    sections = copy['sections']
    scope, portfolio, records, pending, missing, sources = _portfolio_inputs(db, run, release, assessed_at)
    known_kinds = {s: {'kind': scope[s]['kind']} for s in missing if s in scope}
    order, terms = report_allocation(records, {'unavailable': missing}, {'allocation_terms': known_kinds}, {})
    confirmations = trading_portfolio.arrangement_confirmations(db, portfolio)
    times = report_judgement_times(market, records, portfolio, confirmations)
    market_end = market['payload']['result']['valid_until']
    expiries = {name + '.' + item: market_end for name, (_, items) in trading_report.SECTIONS.items() for item in items}
    for subject, record in records.items():
        expiries['allocation.' + subject] = record['payload']['result']['valid_until']
    monthly = [s for s in records if records[s]['payload']['policy']['kind'] == 'MONTHLY']
    expiries['allocation.cash'] = min((expiries['allocation.' + s] for s in monthly), default=market_end, key=millis)
    pending.update(path for path, until in expiries.items() if millis(until) <= millis(assessed_at))
    sources.update(market=trading_decisions.binding(market), market_status=trading_decisions.decision_state(
        db, market, at=assessed_at)['status'])
    if sources['market_status'] != 'CURRENT':
        pending.update(name + '.' + item for name, (_, items) in trading_report.SECTIONS.items() for item in items)
    allocation = {}
    line = None
    for subject, record in records.items():
        path = 'allocation.' + subject
        if subject == trading_policy.POSITION_SUBJECT:
            try:
                line = trading_delivery.position_line(record)
            except Conflict:
                line = '原 BTCUSDT 保护性空单：上一版没有可沿用的方向。'
                pending.add(path)
        else:
            allocation[subject] = _choice_text(subject, record, scope)
            if confirmations.get(subject):
                facts = ' '.join(event['fact_ref']['quote'] for event in confirmations[subject])
                allocation[subject] += ' 用户确认记录如下。' + facts
    cash_gap = False
    for subject in missing:
        policy = scope.get(subject)
        if subject == trading_policy.POSITION_SUBJECT:
            line = '原 BTCUSDT 保护性空单：当前配置尚无已采纳的方向。'
        elif policy:
            name = _label(subject, policy, policy)
            allowance = ('，规划预算 ' + policy['budget'] + ' ' + policy['currency']) if 'budget' in policy else ''
            allocation[subject] = '**' + name + '。** 当前配置已确认' + allowance + '，组合尚未采纳本期安排。'
        else:
            allocation[subject] = '**' + subject + '。** 配置依据待核，尚无可以引用的正式安排。'
        cash_gap |= not policy or policy['kind'] == 'MONTHLY'
    if cash_gap or any('allocation.' + s in pending for s in monthly):
        pending.add('allocation.cash')
    cash = [((amount + ' ' + unit) if amount is not None else unit + ' 金额待核定')
            for unit, amount in terms['cash'].items()]
    if cash:
        allocation['cash'] = '**月度规划留存。** ' + '、'.join(cash) + '。'
    elif cash_gap:
        allocation['cash'] = '已采纳的月度决定尚不足以确定留存金额。'
    else:
        allocation['cash'] = '已采纳的组合没有适用的月度投入，本期没有由月度预算产生的留存。'
    if cash_gap and cash:
        allocation['cash'] += ' 该金额只涵盖已采纳的月度组，全部月度留存仍待核定。'
    if cash_gap:
        units = {scope[s]['currency'] for s in missing if s in scope and scope[s]['kind'] == 'MONTHLY'}
        if any(s not in scope for s in missing):
            units.update(terms['cash'])
        terms['cash'].update({unit: None for unit in units})
    _, seed = trading_report.compose(None, {**sections, 'allocation': allocation}, assessed_at, line,
                                     members=order, source_times=times)
    # This is an adopted source edition. Only a native receipt advances the destination baseline.
    seed.update(report_id=(previous or {}).get('report_id'), reviewed_at=times['allocation.cash'],
                pending_review={p: assessed_at for p in pending}, allocation_terms=terms, valid_until=expiries,
                basis=report_basis(db, run, market, records, copy['sources'], confirmations,
                                   portfolio['payload'].get('member_reviews') if portfolio else None),
                public_market=copy['market'], market_sources=copy['sources'],
                seed_sources=sources)
    if public_seed is None:
        seed['risk_public_copy'] = copy['copy_id']
    if previous:
        seed['risk_updates'] = previous['risk_updates']
        seed['pending_review'].update({p: t for p, t in previous['pending_review'].items() if p in expiries})
    return seed


def check_sources(db, run, release, sources):
    from .trading import MARKET
    market = trading_decisions.current_market(db, MARKET)
    *_, current = _portfolio_inputs(db, run, release, run['known_at'])
    if market:
        current.update(market=trading_decisions.binding(market),
                       market_status=trading_decisions.decision_state(db, market, at=run['known_at'])['status'])
    if current != sources:
        raise Conflict('the adopted inputs of this first risk edition changed; recompose against current decisions')
