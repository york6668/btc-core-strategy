"""One recommendation owner, exact hypothetical funding, derived monitoring refs."""
from decimal import Decimal
import re
from .common import atoms, exact, instant, numbered, require, text

SIDES = ('upper_short', 'lower_spot')


def allocation(side, tier):
    """A tier inherits the basket unless it explicitly identifies its assets."""
    return tier.get('allocation_percent', side['allocation_percent'])


def spot_label(tier):
    return '独立BTC现货' if set(tier.get('allocation_percent', {})) == {'BTC'} else '现货篮子'


def spot_capital_by_asset(side):
    result = {}
    for tier in side['tiers']:
        for asset, percent in allocation(side, tier).items():
            result[asset] = result.get(asset, Decimal(0)) + Decimal(numbered(tier['capital_u'])) * Decimal(numbered(percent)) / 100
    return {asset: numbered(amount) for asset, amount in sorted(result.items())}


def _validate_allocation(value):
    require(isinstance(value, dict) and value, 'nonempty asset allocation required')
    require(all(isinstance(asset, str) and re.fullmatch(r'[A-Z0-9]+(?:[.-][A-Z0-9]+)*', asset)
                for asset in value), 'invalid allocation asset identifier')
    require(all(Decimal(numbered(v)) > 0 for v in value.values()) and
            sum(Decimal(numbered(v)) for v in value.values()) == 100, 'spot allocation must sum to 100')


def total(side, field):
    if side.get('status') in ('EXECUTION_LIMITED', 'UNDETERMINED') or any(t[field] is None for t in side['tiers']):
        return None
    return sum((Decimal(numbered(t[field])) for t in side['tiers']), Decimal(0))


def sequences(value):
    """Full-tier scenarios are proposals, never fills or account balances."""
    orders = {'spot_only': ['lower_spot'], 'short_only': ['upper_short'],
              'spot_then_short': ['lower_spot', 'upper_short'],
              'short_then_spot': ['upper_short', 'lower_spot']}
    result = {}
    explicit_allocation = any('allocation_percent' in t for t in value['lower_spot']['tiers'])
    for name, order in orders.items():
        cash = spot = short = Decimal(0)
        unknown = []
        steps = []
        for side in order:
            capital, amount = total(value[side], 'capital_u'), total(value[side], 'amount_u')
            if capital is None:
                unknown.append(side)
            else:
                cash += capital
            if side == 'lower_spot':
                spot = None if amount is None else spot + amount
            else:
                short = None if amount is None else short + amount
            steps.append({'side': side, 'capital_u': numbered(capital) if capital is not None else None, 'cumulative_capital_u': numbered(cash) if not unknown else None,
                          'known_capital_u': numbered(cash), 'unknown_capital_sides': list(unknown),
                          'spot_capital_u': numbered(spot) if spot is not None else None,
                          'short_notional_u': numbered(short) if short is not None else None})
            if explicit_allocation:
                steps[-1]['spot_capital_by_asset_u'] = spot_capital_by_asset(value['lower_spot']) if spot else {}
        result[name] = steps
    return result


def validate(value, cutoff, resolve, revision=5, market_hash=None, fact_version=None):
    exact(value, 'upper_short lower_spot portfolio', 'recommendations')
    for key in SIDES:
        side = value[key]
        exact(side, 'status unit tiers validity purpose amount_basis allocation_percent rationale impact refs', key)
        require(side['status'] in (('RECOMMENDED', 'NOT_RECOMMENDED', 'EXECUTION_LIMITED', 'UNDETERMINED') if revision >= 6 else ('RECOMMENDED', 'NOT_RECOMMENDED')), 'recommendation needs explicit validity conclusion')
        require(side['unit'] == ('SHORT_NOTIONAL_U' if key == 'upper_short' else 'SPOT_CAPITAL_U'), 'recommendation amount unit mismatch')
        require(isinstance(side['tiers'], list), 'recommendation tiers required')
        require(bool(side['tiers']) == (side['status'] == 'RECOMMENDED'), 'valid recommendation requires prices and positive amounts; no-recommendation has no invented tiers')
        allocation = side['allocation_percent']
        require(isinstance(allocation, dict), 'asset allocation required')
        if key == 'lower_spot':
            _validate_allocation(allocation)
        else:
            require(allocation == {}, 'short amount is BTC not basket capital')
        ids = set()
        for tier in side['tiers']:
            exact(tier, 'id price_low_usd price_high_usd amount_u capital_u' +
                  (' allocation_percent' if 'allocation_percent' in tier else ''), 'recommendation tier')
            if 'allocation_percent' in tier:
                require(key == 'lower_spot', 'asset allocation override only belongs to spot tiers')
                _validate_allocation(tier['allocation_percent'])
                override = {asset: numbered(percent) for asset, percent in tier['allocation_percent'].items()}
                inherited = {asset: numbered(percent) for asset, percent in allocation.items()}
                require(override == inherited or override == {'BTC': '100'},
                        'spot tier must inherit the reviewed basket or explicitly identify independent BTC')
            text(tier['id'], 'stable tier id')
            require(tier['id'] not in ids, 'duplicate recommendation tier')
            ids.add(tier['id'])
            low, high, amount = [Decimal(numbered(tier[k])) for k in ('price_low_usd', 'price_high_usd', 'amount_u')]
            capital = Decimal(numbered(tier['capital_u'])) if tier['capital_u'] is not None else None
            require(0 < low <= high and amount > 0 and (capital is None or 0 < capital <= amount), 'explicit positive recommendation price and amount required')
            if key == 'lower_spot':
                require(amount == capital, 'spot amount must equal its cash use')
        exact(side['validity'], 'from until conditions invalidations', 'recommendation validity')
        validity = side['validity']
        start, end = instant(validity['from']), instant(validity['until'])
        require(start <= instant(cutoff) and start < end, 'invalid or future-start recommendation validity')
        if side['status'] in ('RECOMMENDED', 'EXECUTION_LIMITED'):
            require(instant(cutoff) < end, 'cannot publish already expired or future-start recommendation')
        atoms(validity['conditions'], 'recommendation support conditions')
        atoms(validity['invalidations'], 'recommendation invalidations')
        atoms(side['purpose'], 'recommendation protection purpose')
        atoms(side['amount_basis'], 'recommendation amount basis')
        text(side['rationale'], 'recommendation rationale or reason for no recommendation')
        text(side['impact'], 'recommendation portfolio impact')
        require(isinstance(side['refs'], list) and side['refs'], 'recommendation conclusion requires raw evidence references')
        for ref in side['refs']:
            resolve(ref)
    portfolio = value['portfolio']
    exact(portfolio, 'capital_envelope_u sequence_policy existing_exposure uncertainty narrative' + (' allocation_decision' if revision >= 6 else ''), 'recommendation portfolio')
    capitals = [total(value[s], 'capital_u') for s in SIDES]
    if any(c is None for c in capitals) or any(value[s]['status'] in ('EXECUTION_LIMITED', 'UNDETERMINED') for s in SIDES):
        require(portfolio['capital_envelope_u'] is None, 'unknown short capital cannot become a confirmed total funding envelope')
    else:
        require(Decimal(numbered(portfolio['capital_envelope_u'])) >= sum(capitals), 'shared capital envelope double counted')
    require(portfolio['sequence_policy'] == 'REEVALUATE_AFTER_FIRST', 'sequential proposals require Codex reassessment after first confirmed fact change')
    for field in ('existing_exposure', 'uncertainty', 'narrative'):
        text(portfolio[field], 'combined portfolio ' + field)
    if revision >= 6:
        from .closed_loop import validate_allocation
        validate_allocation(portfolio['allocation_decision'], cutoff, market_hash, fact_version)
    return sequences(value)


def watch_targets(decision):
    assessment = (decision or {}).get('assessment') or {}
    if assessment.get('revision', 1) < 2:
        return []
    return [{'decision_id': decision['decision_id'], 'side': side,
             'recommendation_ref': '/assessment/recommendations/' + side}
            for side in SIDES]


def stable(value):
    from copy import deepcopy
    result = deepcopy(value)
    for side in SIDES:
        for field in ('rationale', 'impact', 'refs'):
            result[side].pop(field)
        result[side]['validity'].pop('from')
        for tier in result[side]['tiers']:
            tier.pop('id')
            if 'allocation_percent' in tier:
                tier['allocation_percent'] = {asset: numbered(percent) for asset, percent in tier['allocation_percent'].items()}
                inherited = {asset: numbered(percent) for asset, percent in result[side]['allocation_percent'].items()}
                # Explicit BTC remains an independent opportunity even when the
                # reviewed basket itself currently consists only of BTC.
                if tier['allocation_percent'] == inherited and set(inherited) != {'BTC'}:
                    tier.pop('allocation_percent')
        result[side]['allocation_percent'] = {asset: numbered(percent) for asset, percent in result[side]['allocation_percent'].items()}
        # A true expiry extension is a decision change; timezone spelling is not.
        from datetime import timezone
        result[side]['validity']['until'] = instant(result[side]['validity']['until']).astimezone(timezone.utc).isoformat()
    for field in ('existing_exposure', 'uncertainty', 'narrative'):
        result['portfolio'].pop(field)
    decision = result['portfolio'].get('allocation_decision')
    if decision:
        # Source/time rebinding alone is not a different arrangement. Expiry,
        # actions, uncertainty and execution limitations still carry meaning.
        for field in ('market_result_hash', 'fact_version', 'decided_at'):
            decision.pop(field)
    return result
