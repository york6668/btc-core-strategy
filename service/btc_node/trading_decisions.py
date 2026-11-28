"""Decisions and their subsequent reviews, stored by their business subject.

The service owns the transaction, the frozen run context, and the selection of
the applicable policy and evidence. This module owns decision applicability.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .codec import Conflict, encode, identity, millis, utc
from .entities import Entities
from .trading_policy import POSITION_SUBJECT


MARKET_PREFIX = 'trading/market/'
PERSONAL_PREFIX = 'trading/personal/'
REVIEW_PREFIX = 'trading/review/'
HISTORICAL_PREFIX = MARKET_PREFIX + 'archived/'
PRICE_BASELINE_DIRECTIONAL = 'PRICE_BASELINE_DIRECTIONAL'
SHORT_DIRECTIONS = ('REDUCE', 'KEEP', 'INCREASE')
POSITION_ACTIONS = {'KEEP', 'REDUCE', 'EXIT'}
CHOICES = {
    'MONTHLY': {'CONTINUE', 'DEFER', 'SKIP', 'UNRESOLVED'},
    'OPPORTUNITY': {'PARTICIPATE', 'WAIT', 'AVOID', 'UNRESOLVED'},
    'POSITION': POSITION_ACTIONS | {'INCREASE', 'HEDGE', 'NO_POSITION', 'UNRESOLVED'},
}
OUTCOMES = {'SUPPORTED', 'PARTIAL', 'REFUTED', 'NOT_TRIGGERED', 'UNKNOWN'}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' is required')
    return value.strip()


def _object_id(prefix, subject):
    name = _text(subject, 'decision subject')
    if len(name) > 240 or name != subject:
        raise ValueError('invalid decision subject')
    return prefix + name


def _time(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(label + ' requires a timezone-qualified time')
    return millis(value)


def _decimal(value, label):
    if type(value) not in {str, int, float}:
        raise ValueError(label + ' requires an amount')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(label + ' requires an amount') from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(label + ' requires a finite nonnegative amount')
    return amount


def _amount_text(amount):
    if amount == 0:
        return '0'
    # Expanding very large exponents would allocate memory proportional to the
    # exponent, not the input. Scientific form is lossless in those cases.
    if abs(amount.adjusted()) > 30:
        return str(amount)
    value = format(amount, 'f')
    return value.rstrip('0').rstrip('.') if '.' in value else value


def _evidence(value, *, required=True):
    if not isinstance(value, list) or (required and not value):
        raise ValueError('decision or factual review requires frozen evidence')
    fields = {
        'OBSERVATION': {'product', 'series', 'item_key', 'observation_key'},
        'ENTITY': {'kind', 'object_id', 'version', 'digest'},
        'BLOB': {'digest'},
    }
    for reference in value:
        if not isinstance(reference, dict) or reference.get('type') not in fields:
            raise ValueError('invalid evidence reference')
        if not fields[reference['type']] <= reference.keys():
            raise ValueError('use the exact frozen evidence identity')
    return value


def _condition_reference(value):
    if not isinstance(value, dict) or set(value) != {'target', 'condition_id', 'reason'}:
        raise ValueError('a condition transition needs its original target, condition_id and reason')
    if not isinstance(value['target'], dict) or set(value['target']) != {'kind', 'object_id', 'version', 'digest'}:
        raise ValueError('a condition transition needs an exact decision reference')
    return {'target': value['target'], 'condition_id': _text(value['condition_id'], 'prior condition identity'),
            'reason': _text(value['reason'], 'condition transition reason')}


def _conditions(values, *, valid_until):
    if not isinstance(values, list) or not values:
        raise ValueError('a decision needs a testable review condition')
    ids = set()
    conditions = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('a review condition needs an identity, criterion and time')
        condition_id = _text(value.get('id'), 'condition identity')
        review_at = _time(value.get('review_at'), 'condition review time')
        if condition_id in ids or review_at > valid_until:
            raise ValueError('review conditions need unique identities and times within the decision horizon')
        ids.add(condition_id)
        condition = {'id': condition_id, 'criterion': _text(value.get('criterion'), 'review criterion'),
                     'review_at': value['review_at']}
        if value.get('observation_end') is not None:
            _time(value['observation_end'], 'original observation cutoff')
            condition['observation_end'] = value['observation_end']
        if 'predecessor' in value:
            condition['predecessor'] = _condition_reference(value['predecessor'])
        conditions.append(condition)
    return conditions


def _horizon(result, run):
    cutoff = _time(run.get('known_at'), 'frozen knowledge cutoff')
    expiry = _time(result.get('valid_until'), 'decision expiry')
    if expiry <= cutoff or expiry <= millis():
        raise ValueError('a new decision must have a future expiry')
    horizon = {'valid_until': result['valid_until'],
               'conditions': _conditions(result.get('conditions'), valid_until=expiry)}
    if 'retired_conditions' in result:
        if not isinstance(result['retired_conditions'], list):
            raise ValueError('retired conditions must be a list of original conditions and reasons')
        horizon['retired_conditions'] = [_condition_reference(item) for item in result['retired_conditions']]
    return horizon


def current_market(database, subject, *, as_of=None):
    return Entities(database).get('DECISION', _object_id(MARKET_PREFIX, subject), as_of=as_of)


def current_decision(database, subject):
    return Entities(database).get('PLAN', _object_id(PERSONAL_PREFIX, subject))


def binding(record):
    return {key: record[key] for key in ('kind', 'object_id', 'version', 'digest')}


def _bound_record(objects, reference, *, current=False):
    if not isinstance(reference, dict):
        raise ValueError('an exact trading decision reference is required')
    kind, object_id = reference.get('kind'), reference.get('object_id')
    prefixes = {'DECISION': MARKET_PREFIX, 'PLAN': PERSONAL_PREFIX}
    if (kind not in prefixes or not isinstance(object_id, str) or not object_id.startswith(prefixes[kind])
            or type(reference.get('version')) is not int or not isinstance(reference.get('digest'), str)):
        raise ValueError('invalid trading decision reference')
    record = objects.get(kind, object_id, version=None if current else reference['version'])
    if (record is None or any(record[key] != reference[key] for key in ('version', 'digest'))
            or (current and not record['active'])):
        raise Conflict('bound trading decision does not match its saved record')
    return record


def _save(objects, *, kind, object_id, scope, payload, evidence, run, expected_version, reason):
    previous = objects.get(kind, object_id)
    version = previous['version'] if previous else 0
    if type(expected_version) is not int or expected_version != version:
        raise Conflict('the affected trading decision changed before submission')
    if kind != 'REVIEW':
        prior = previous['payload'] if previous else {}
        prior_conditions = {c['id']: c for c in prior.get('result', {}).get('conditions', [])}
        # Provenance already established for the same test is part of its normal form.
        for condition in payload['result']['conditions']:
            original = prior_conditions.get(condition['id'], {})
            if ('predecessor' in original and 'predecessor' not in condition
                    and _condition_basis(condition) == _condition_basis(original)):
                condition['predecessor'] = original['predecessor']
        derived = {'condition_sources', 'open_prior_conditions', 'predecessor_reviews'}
        if {key: value for key, value in prior.items() if key not in derived} == payload:
            if decision_state(objects.db, previous, at=run['known_at'])['status'] != 'NEEDS_REVIEW':
                return previous
        transitions = _condition_transitions(objects, previous, payload['result'], prior_conditions, at=run['known_at'])
        payload = {**payload, **transitions}
    # The business horizon belongs to the decision itself. Generic expiry
    # handling must not require closing an unrelated or unverified forecast.
    known = run['known_at']
    spec = {'kind': kind, 'object_id': object_id, 'scope': scope,
            'payload': payload, 'observed_at': run.get('observed_at') or known,
            'known_at': known, 'source_ref': _text(run.get('ref'), 'run identity'),
            'method_version': _text(run.get('method_version'), 'release provenance'),
            'change_reason': reason, 'dependencies': evidence}
    objects.put(spec, expected_version=expected_version)
    return objects.get(kind, object_id)


def adopt_market(database, *, subject, expected_version, result, evidence, run, theses=None, event_comparisons=None):
    primary = _text(result.get('primary'), 'primary market scenario')
    alternatives = result.get('alternatives')
    if not isinstance(alternatives, list) or not alternatives:
        raise ValueError('a market judgement needs a competing scenario')
    alternatives = [_text(value, 'competing market scenario') for value in alternatives]
    if primary in alternatives or len(set(alternatives)) != len(alternatives):
        raise ValueError('market scenarios must be distinct')
    assessments = result.get('assessments', {})
    if not isinstance(assessments, dict):
        raise ValueError('scoped public assessments must use named investment questions')
    assessments = {_text(key, 'actual market subject'): _text(value, 'scoped market assessment')
                   for key, value in assessments.items()}
    judgement = {'conclusion': _text(result.get('conclusion'), 'market conclusion'),
                 'primary': primary, 'alternatives': alternatives,
                 'value': _text(result.get('value'), 'risk and reward at current prices'),
                 'assessments': assessments, **_horizon(result, run)}
    if event_comparisons:
        judgement['event_comparisons'] = event_comparisons
    if theses:
        judgement['theses'] = theses
        evidence = [*evidence, *({'type': 'ENTITY', **ref} for ref in theses.values())]
    valuations = result.get('valuations', {})
    if not isinstance(valuations, dict) or valuations.keys() & assessments.keys():
        raise ValueError('write each product valuation once, separately from other market assessments')
    if valuations:
        condition_ids = {condition['id'] for condition in judgement['conditions']}
        judgement['valuations'] = {_text(name, 'valuation subject'): _valuation(database, name, terms, condition_ids, evidence)
                                   for name, terms in valuations.items()}
    return _save(Entities(database), kind='DECISION', object_id=_object_id(MARKET_PREFIX, subject), scope='PUBLIC',
                 payload={'subject': subject, 'result': judgement}, evidence=_evidence(evidence), run=run,
                 expected_version=expected_version, reason=judgement['conclusion'])


def _monthly(result, judgement, budget):
    compared = result.get('comparisons')
    if not isinstance(compared, list):
        raise ValueError('a basket needs a comparison of its available choices')
    alternatives = {}
    for candidate in compared:
        if not isinstance(candidate, dict):
            raise ValueError('each comparison alternative needs its amount and reason')
        amount = _decimal(candidate.get('amount'), 'alternative amount')
        if amount in alternatives or amount > budget:
            raise ValueError('basket alternatives must be unique and fit its own budget')
        alternatives[amount] = _text(candidate.get('reason'), 'alternative risk and return basis')
    if not {Decimal(0), budget} <= alternatives.keys():
        raise ValueError('a basket must compare its full contribution and skipping')
    judgement['comparisons'] = [{'amount': _amount_text(amount), 'reason': alternatives[amount]}
                               for amount in sorted(alternatives)]
    choice = judgement['decision']
    if choice == 'UNRESOLVED':
        if result.get('amount') is not None:
            raise ValueError('an unresolved basket has no selected amount')
        judgement['amount'] = None
        return
    # The selected alternative is the amount source. Accept `amount` as its
    # spelling for callers that already express a choice by contribution size.
    amount = _decimal(result.get('amount'), 'selected basket contribution')
    if amount not in alternatives or ((amount == 0) != (choice in {'SKIP', 'DEFER'})):
        raise ValueError('the selected contribution must match the chosen basket alternative')
    judgement['amount'] = _amount_text(amount)
    other = alternatives.keys() - {amount}
    if 'runner_up_amount' in result:
        runner_up = _decimal(result['runner_up_amount'], 'next-best contribution')
    elif len(other) == 1:
        runner_up = next(iter(other))
    else:
        raise ValueError('identify the next-best compared contribution in runner_up_amount')
    if runner_up not in other:
        raise ValueError('the next-best contribution must be a different compared alternative')
    judgement['runner_up_amount'] = _amount_text(runner_up)
    if 0 < amount < budget:
        judgement['amount_basis'] = _text(result.get('amount_basis'), 'why a partial amount beats the full plan')


def needs_nav_review(name, value):
    # GLD's official fund NAV and its US closing quote normally have different fixings.
    return ('GLD' in value.get('assets', (name,)) and value.get('reference') is not None
            and 'nav_comparison' not in value)


def monthly_price_bases(selected, choice):
    """Bind the current-price judgements used to rank the basket. Raw observations stay in basis."""
    valuations = {key: v for key, v in selected.items() if key.startswith('valuation/')}
    sources = choice.get('price_basis')
    if sources is None:
        sources = [key for key, value in valuations.items() if value.get('price_implication') and not needs_nav_review(key, value)]
    elif not isinstance(sources, list):
        sources = [sources]
    supporting = choice.get('supporting_price_bases', [])
    if not isinstance(supporting, list):
        raise ValueError('supporting_price_bases must list the other bound price judgements')
    sources = [*sources, *supporting]
    if not sources:
        raise ValueError('a ranked basket needs a frozen judgement about the value at the compared price')
    anchors = {}
    for source in sources:
        name = source.get('valuation') if isinstance(source, dict) else source
        if not isinstance(name, str) or name not in valuations or name in anchors:
            raise ValueError('cite each decisive bound valuation once in price_basis')
        implication = valuations[name].get('price_implication')
        if not implication:
            raise ValueError('complete the current-price judgement for ' + name + ' before ranking the basket')
        if needs_nav_review(name, valuations[name]):
            raise ValueError('the fund price and NAV fixing have not been compared; rank from another decisive valuation '
                             'or reassess the fund without treating their difference as a cost')
        anchor = {'valuation': name, 'quote': implication}
        if isinstance(source, dict) and source != anchor:
            raise ValueError('price_basis must cite the current frozen price implication for ' + name)
        anchors[name] = anchor
    return list(anchors.values())


def _return_percent(value):
    try:
        rate = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('give a scenario total return in percent') from error
    if not rate.is_finite() or rate < -100:
        raise ValueError('a cash or unlevered spot scenario return must be finite and at least -100 percent')
    return rate


def _basket_comparison(raw, policy, judgement):
    if not isinstance(raw, dict):
        raise ValueError('compare the two amounts through the same next opportunity in comparison_at_price: '
                         'give horizon, switch_when and, where measurable, cases with assumption, returns_pct and cash_pct')
    comparison = {'horizon': _text(raw.get('horizon'), 'common comparison horizon'),
                  'switch_when': _text(raw.get('switch_when'), 'reader-facing condition under which the other amount wins')}
    cases = raw.get('cases', [])
    if not isinstance(cases, list):
        raise ValueError('cases must be a list of comparable scenarios')
    weights = policy['inputs']['weights']
    extra = abs(Decimal(judgement['amount']) - Decimal(judgement['runner_up_amount']))
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get('returns_pct'), dict) or case['returns_pct'].keys() != weights.keys():
            raise ValueError('each measured scenario needs total returns for the confirmed constituents, including assumed flat legs')
        assumption = _text(case.get('assumption'), 'basis and assumptions of the scenario')
        rates = {k: _return_percent(v) for k, v in case['returns_pct'].items()}
        cash = _return_percent(case.get('cash_pct'))
        basket = sum(Decimal(str(w)) * rates[k] for k, w in weights.items()) / 100
        comparison.setdefault('cases', []).append({
            'assumption': assumption, 'returns_pct': {k: _amount_text(v) for k, v in rates.items()},
            'cash_pct': _amount_text(cash), 'more_invested_advantage': _amount_text(extra * (basket - cash) / 100)})
    return comparison


def monthly_decision_text(choice):
    comparison = choice.get('comparison_at_price')
    if not comparison:
        return None
    parts = [choice['reason']]
    for sentence in (choice['opportunity_cost'], comparison['switch_when'], choice.get('month_close_reason')):
        if sentence and not any(sentence in part for part in parts):
            parts.append(sentence)
    return '\n\n'.join(parts)


def _monthly_window(policy, judgement, run):
    try:
        tz = ZoneInfo(_text(policy.get('calendar'), 'applicable planning calendar'))
    except ZoneInfoNotFoundError as exc:
        raise ValueError('unknown planning calendar') from exc
    decided = utc(run['known_at']).astimezone(tz)
    year, month = (decided.year + 1, 1) if decided.month == 12 else (decided.year, decided.month + 1)
    month_end = millis(datetime(year, month, 1, tzinfo=tz))
    if millis(judgement['valid_until']) > month_end:
        raise ValueError('a monthly choice cannot outlive the confirmed planning month')
    if judgement['decision'] == 'DEFER' and not any(
            millis(run['known_at']) < millis(c['review_at']) < month_end for c in judgement['conditions']):
        raise ValueError('deferring this contribution needs an outstanding review within the planning month')
    return decided.strftime('%Y-%m')


def _opportunity(judgement, policy, result, budget):
    choice = judgement['decision']
    if choice == 'UNRESOLVED':
        if result.get('amount') is not None:
            raise ValueError('an unresolved opportunity has no selected amount')
        judgement['amount'] = None
        return
    amount = _decimal(result.get('amount'), 'opportunity amount')
    minimum = _decimal(policy.get('minimum', 0), 'minimum opportunity amount')
    if amount > budget or ((amount > 0) != (choice == 'PARTICIPATE')) or (amount and amount < minimum):
        raise ValueError('the opportunity amount must fit the chosen action and its confirmed range')
    judgement['amount'] = _amount_text(amount)
    zone = result.get('value_zone')
    if zone is None and choice != 'PARTICIPATE':
        return
    judgement['value_zone'] = _value_zone(zone)


def _value_zone(zone):
    if not isinstance(zone, dict):
        raise ValueError('an established value zone needs its bounds, quote unit and basis')
    low = _decimal(zone.get('lower'), 'value zone lower bound')
    high = _decimal(zone.get('upper'), 'value zone upper bound')
    if not 0 < low < high:
        raise ValueError('established value zone needs ordered positive bounds')
    return {'lower': _amount_text(low), 'upper': _amount_text(high),
            'quote_unit': _text(zone.get('quote_unit'), 'value zone quote unit'),
            'basis': _text(zone.get('basis'), 'value zone evidence basis')}


def _valuation_observation(database, source, quote, basis):
    """Bind the economic datum used by the author to the original frozen observation."""
    if source['type'] == 'ENTITY':
        if source['object_id'].startswith('trading/forecast/'):
            raise ValueError('use the asset economic observation as the primary valuation reference')
        record = Entities(database).get(source['kind'], source['object_id'], version=source['version'])
        if not record or record['scope'] != 'PUBLIC' or record['digest'] != source['digest']:
            raise ValueError('use the exact public research or observation for the valuation')
        value = record['payload']
    elif source['type'] == 'OBSERVATION':
        row = database.one('SELECT digest FROM observations WHERE product=%s AND series=%s '
                           'AND item_key=%s AND observation_key=%s',
                           tuple(source[k] for k in ('product', 'series', 'item_key', 'observation_key')))
        if not row:
            raise ValueError('the cited valuation observation is unavailable')
        value = database.value(row['digest'])
    elif 'sample' in source:
        from .trading_theses import packet_observation
        meta = database.metadata('trading.public.' + source['digest'].removeprefix('sha256:'))
        _, value, _ = packet_observation(database, source, meta)
    else:
        value = database.value(source['digest'].removeprefix('sha256:'))
    if isinstance(value, dict) and 'content' in value:
        from urllib.parse import urlsplit
        host = urlsplit(value.get('url', '')).hostname or ''
        if host == 'polymarket.com' or host.endswith('.polymarket.com'):
            raise ValueError('use the asset economic observation as the primary valuation reference')
        value = value['content']
    original = value if isinstance(value, str) else encode(value).decode('utf-8')
    if quote not in original or quote not in basis:
        raise ValueError('use the same observed number or short source phrase in the economic basis and its original evidence')


def _valuation(database, name, terms, condition_ids, evidence):
    allowed = {'assessment', 'basis', 'reference', 'evidence', 'assets', 'value_zone', 'condition_ids', 'price_implication', 'nav_comparison'}
    if not isinstance(terms, dict) or terms.keys() - allowed or 'reference' not in terms:
        raise ValueError('identify the factual value comparison for this product, or explicitly record reference=null')
    basis = _text(terms.get('basis'), 'the independent value comparison that supports this price judgement')
    sources = list(_evidence(terms.get('evidence', []), required=False))
    reference = terms['reference']
    if reference is not None:
        if not isinstance(reference, dict) or set(reference) != {'source', 'quote'}:
            raise ValueError('a valuation reference identifies its decisive observation with source and quote')
        source = _evidence([reference['source']])[0]
        if source not in evidence:
            raise ValueError('cite valuation evidence from this frozen market judgement')
        quote = _text(reference['quote'], 'the observed value used in the price comparison')
        _valuation_observation(database, source, quote, basis)
        reference = {'source': source, 'quote': quote}
        if source not in sources:
            sources.append(source)
    if not sources or any(source not in evidence for source in sources):
        raise ValueError('cite valuation evidence from this frozen market judgement')
    assets = terms.get('assets', [name])
    if not isinstance(assets, list) or not assets or any(not isinstance(a, str) or not a.strip() for a in assets):
        raise ValueError('identify the products covered by this valuation')
    refs = terms.get('condition_ids', [])
    if (not isinstance(refs, list) or any(not isinstance(key, str) or key not in condition_ids for key in refs)
            or len(refs) != len(set(refs))):
        raise ValueError('a valuation must cite distinct conditions of this market judgement')
    item = {'assessment': _text(terms.get('assessment'), 'price attractiveness over the investment horizon'),
            'basis': basis, 'reference': reference,
            'evidence': sources, 'assets': sorted(set(a.strip() for a in assets))}
    if 'price_implication' in terms:
        item['price_implication'] = _text(terms['price_implication'], 'what this price requires and what remains at risk')
    if 'nav_comparison' in terms:
        nav = terms['nav_comparison']
        if nav is not None:
            if not isinstance(nav, dict) or not {'market_at', 'nav_at'} <= nav.keys():
                raise ValueError('supply the actual price and NAV fixing instants, or nav_comparison=null')
            same = _time(nav['market_at'], 'market price fixing') == _time(nav['nav_at'], 'fund NAV fixing')
            nav = {'market_at': nav['market_at'], 'nav_at': nav['nav_at'], 'same_time': same}
        item['nav_comparison'] = nav
    if refs:
        item['condition_ids'] = sorted(refs)
    if 'value_zone' in terms:
        item['value_zone'] = _value_zone(terms['value_zone'])
    return item


def product_valuations(result):
    """Expose a single judgement per product, including historical split-format assessments."""
    return {name: terms if 'assessment' in terms else {**terms, 'assessment': result['assessments'][name]}
            for name, terms in result.get('valuations', {}).items()}


def legacy_valuation_names(record):
    return [name for name, value in record['payload']['result'].get('valuations', {}).items() if 'reference' not in value]


def covers_spot_policy(subject, policy, selected, *, choice):
    if policy['kind'] == 'MONTHLY' and choice == 'UNRESOLVED':
        # The gap and available inputs are saved on this member. No amount is ranked.
        return True
    scoped = [value for key, value in selected.items() if key.startswith('valuation/') and value
              and value.get('evidence') and 'reference' in value]
    covered = {asset for value in scoped for asset in value['assets']}
    fields = policy.get('inputs', {})
    required = set(fields.get('weights', {}))
    asset = fields.get('asset') or fields.get('product')
    if asset:
        required.add(asset)
    elif policy['kind'] == 'OPPORTUNITY' and subject == 'independent-opportunity':
        # The original separately budgeted BTC policy predates an explicit asset field.
        required.add('BTC')
    if choice == 'PARTICIPATE' or policy['kind'] == 'MONTHLY':
        measured = {asset for value in scoped if value['reference'] for asset in value['assets']}
        if not measured.intersection(required or covered):
            return False
    return bool(covered) and required <= covered


def market_inputs(database, record, *, at, state=None):
    result = record['payload']['result']
    inputs = {**{key: result[key] for key in ('conclusion', 'primary', 'alternatives', 'value')},
              **{'assessment/' + key: val for key, val in result['assessments'].items()},
              **{'thesis/' + key: ref for key, ref in result.get('theses', {}).items()}}
    for name, comparison in result.get('event_comparisons', {}).items():
        inputs['forecast/' + name] = {key: comparison[key] for key in ('conclusion', 'reason', 'probability')}
    valuations = product_valuations(result)
    checks = {}
    if any(terms.get('condition_ids') for terms in valuations.values()):
        if state is None:
            state = decision_state(database, record, at=at)
        for condition in state['conditions']:
            if not condition.get('from_prior_version'):
                check = {key: condition[key] for key in ('criterion', 'observation_end') if key in condition}
                if condition.get('observation_closed'):
                    check['observation_closed'] = True
                check['outcome'] = (condition['review'] or {}).get('outcome')
                checks[condition['id']] = (check, condition.get('review_ref'))
    for name, terms in valuations.items():
        value = {key: terms[key] for key in ('assessment', 'basis', 'reference', 'assets', 'evidence',
                                             'value_zone', 'price_implication', 'nav_comparison') if key in terms}
        if 'condition_ids' in terms:
            value['conditions'] = {key: {**checks[key][0], 'review_ref': checks[key][1]}
                                   if 'evidence' in terms else checks[key][0] for key in terms['condition_ids']}
        inputs['valuation/' + name] = value
        # Previously adopted report items can still cite the old assessment path.
        inputs.setdefault('assessment/' + name, value['assessment'])
    return inputs


def _directional_entry(policy):
    if policy.get('position_status') == 'ABSENT':
        return None
    return policy.get('entry_reference')


def _position(judgement, applied_policy, policy, result, *, subject):
    held = policy.get('position_status')
    choice = judgement['decision']
    if choice == 'NO_POSITION' and held != 'ABSENT':
        raise ValueError('absence of a position needs confirmed facts')
    if held == 'ABSENT' and choice != 'NO_POSITION':
        raise ValueError('an absent position cannot supply current holding or protection')
    if held is not None:
        applied_policy['position_status'] = held

    allowed = POSITION_ACTIONS | {'UNRESOLVED'}
    if subject == POSITION_SUBJECT and held != 'ABSENT':
        allowed.add('INCREASE')
        if choice not in allowed:
            raise ValueError('choose how to adjust the original protective short')
        comparison = result.get('direction_comparison')
        if not isinstance(comparison, dict) or comparison.keys() != set(SHORT_DIRECTIONS):
            raise ValueError('compare reducing, retaining and increasing the original protective short')
        judgement['direction_comparison'] = {
            action: _text(comparison[action], 'basis for ' + action) for action in SHORT_DIRECTIONS}
    elif choice == 'INCREASE' or 'direction_comparison' in result:
        raise ValueError('this comparison applies to the original protective short while it may still exist')

    actions = result.get('conditional_actions')
    entry = _directional_entry(policy)
    if entry is not None:
        if choice not in allowed or actions is not None:
            raise ValueError('an entry price basis needs one scoped directional decision')
        applied_policy.update(analysis_mode=PRICE_BASELINE_DIRECTIONAL, entry_reference=entry)
        return

    if held == 'UNKNOWN' and choice != 'UNRESOLVED':
        raise ValueError('an unknown position has no unconditional holding decision')
    if actions is None:
        return
    if held != 'UNKNOWN' or not isinstance(actions, dict) or set(actions) != {'PRESENT', 'ABSENT'}:
        raise ValueError('an unknown position needs distinct in-position and absent conclusions')
    branches = {}
    for state, choices in (('PRESENT', allowed), ('ABSENT', {'NO_POSITION'})):
        action = actions[state]
        if (not isinstance(action, dict) or not isinstance(action.get('decision'), str)
                or action['decision'] not in choices):
            raise ValueError('conditional action does not fit the stated position premise')
        decision = action['decision']
        branch = {'decision': decision, 'reason': _text(action.get('reason'), 'conditional action basis')}
        if decision == 'UNRESOLVED':
            branch['gap'] = _text(action.get('gap'), 'missing evidence that can reverse the in-position action')
        if set(action) != set(branch):
            raise ValueError('conditional action must use decision, reason and an applicable gap')
        branches[state] = branch
    judgement['conditional_actions'] = branches


def adopt_personal(database, *, subject, expected_version, market, policy, result, evidence, run,
                   fact_refs=(), fact_history=None, market_refs=('conclusion',)):
    objects = Entities(database)
    public = _bound_record(objects, market, current=True)
    if public['kind'] != 'DECISION' or public['scope'] != 'PUBLIC':
        raise ValueError('personal decisions require an independently frozen public judgement')
    as_of = _time(run.get('known_at'), 'frozen knowledge cutoff')
    if public['known_ms'] > as_of or _time(public['payload']['result']['valid_until'], 'market expiry') <= as_of:
        raise Conflict('the public judgement is outside the applicable decision window')
    state = decision_state(database, public, at=run['known_at'])
    if state['status'] != 'CURRENT':
        raise Conflict('the public judgement requires reassessment before personal adoption')
    inputs = market_inputs(database, public, at=run['known_at'], state=state)
    if (not isinstance(market_refs, (list, tuple)) or not market_refs or len(set(market_refs)) != len(market_refs)
            or any(field not in inputs for field in market_refs)):
        raise ValueError('select the exact parts of the market judgement this decision depends on')
    kind = policy.get('kind')
    selected = {field: inputs[field] for field in market_refs}
    choice = result.get('decision')
    if kind in {'MONTHLY', 'OPPORTUNITY'} and not covers_spot_policy(
            subject, policy, selected, choice=choice):
        raise ValueError('cover the products and cite a supported price comparison when ranking monthly capital or adding exposure')
    if kind not in CHOICES or choice not in CHOICES[kind]:
        raise ValueError('the choice must match its investment purpose')
    applied_policy = {'kind': kind, 'basis': _text(policy.get('basis'), 'applicable policy and facts')}
    judgement = {'decision': choice, 'reason': _text(result.get('reason'), 'decisive reason'),
                 'risk': _text(result.get('risk'), 'principal risk'),
                 'opportunity_cost': _text(result.get('opportunity_cost'), 'forgone opportunity'),
                 **_horizon(result, run)}
    steps = result.get('plan_steps', {})
    if not isinstance(steps, dict) or steps.keys() - {c['id'] for c in judgement['conditions']}:
        raise ValueError('a prearranged action must refer to a condition in this plan')
    judgement['plan_steps'] = {}
    for condition_id, step in steps.items():
        if (not isinstance(step, dict) or set(step) != {'outcome', 'action'}
                or step['outcome'] not in {'SUPPORTED', 'REFUTED'}):
            raise ValueError('identify the factual outcome and the action already agreed in the plan')
        judgement['plan_steps'][condition_id] = {'outcome': step['outcome'], 'action': _text(step['action'], 'planned action')}
    if millis(judgement['valid_until']) > millis(public['payload']['result']['valid_until']):
        raise ValueError('the personal decision cannot outlive its frozen market judgement')
    if choice == 'UNRESOLVED':
        judgement['gap'] = _text(result.get('gap'), 'missing evidence that could change the choice')
    if kind == 'MONTHLY' or (kind == 'OPPORTUNITY' and 'budget' in policy):
        budget = _decimal(policy.get('budget'), 'applicable budget')
        if budget <= 0:
            raise ValueError('an applicable contribution policy needs a positive budget')
        applied_policy.update(budget=_amount_text(budget), currency=_text(policy.get('currency'), 'policy currency'))
        if kind == 'MONTHLY' or 'calendar' in policy:
            judgement['month'] = _monthly_window(policy, judgement, run)
        if kind == 'MONTHLY':
            _monthly(result, judgement, budget)
            if choice != 'UNRESOLVED':
                if choice == 'SKIP':
                    judgement['month_close_reason'] = _text(result.get('month_close_reason'),
                        'why forgo the remaining investable opportunities this month even if the price improves; '
                        'use DEFER if the next in-month session could change the amount')
                primary, *supporting = monthly_price_bases(selected, result)
                # Existing readers consume this single binding, including during recovery.
                judgement['price_basis'] = primary
                if supporting:
                    judgement['supporting_price_bases'] = supporting
                judgement['comparison_at_price'] = _basket_comparison(result.get('comparison_at_price'), policy, judgement)
                from .trading_report import check_allocation_amounts
                check_allocation_amounts(subject, monthly_decision_text(judgement), {**applied_policy, **judgement})
        else:
            _opportunity(judgement, policy, result, budget)
    if kind == 'POSITION':
        _position(judgement, applied_policy, policy, result, subject=subject)
        if result.get('value_zone') is not None:
            held_action = judgement.get('conditional_actions', {}).get('PRESENT', judgement)
            if held_action['decision'] != 'REDUCE':
                raise ValueError('a short cover zone applies to a partial reduction of an existing position')
            judgement['value_zone'] = _value_zone(result['value_zone'])
    in_position = judgement.get('conditional_actions', {}).get('PRESENT')
    evidence_required = choice != 'UNRESOLVED' or (in_position is not None and in_position['decision'] != 'UNRESOLVED')
    return _save(objects, kind='PLAN', object_id=_object_id(PERSONAL_PREFIX, subject), scope='PERSONAL',
                 payload={'subject': subject, 'market': binding(public), 'policy': applied_policy,
                          'market_inputs': selected,
                          'fact_refs': list(fact_refs), 'fact_history': fact_history or {}, 'result': judgement},
                 evidence=[{'type': 'BLOB', 'digest': public['digest'], 'relation': 'FROZEN_MARKET_BASIS'},
                           *_evidence(evidence, required=evidence_required)], run=run,
                 expected_version=expected_version, reason=judgement['reason'])


def _review_id(record, condition_id):
    return REVIEW_PREFIX + identity(record['kind'], record['object_id'], record['version'], condition_id)


def save_review_target(database, *, subject, source_key, provenance, conditions, valid_until, known_at):
    """Project verified original conditions without adopting another current market view."""
    data_time = millis(provenance['data_cutoff'])
    formed = millis(provenance['formed_at'])
    registered = millis(provenance['registered_at'])
    if not data_time <= formed <= registered <= millis(known_at):
        raise ValueError('original registration has inconsistent market and knowledge clocks')
    if valid_until is not None and millis(valid_until) <= formed:
        raise ValueError('original condition window ends before formation')
    objects = Entities(database)
    object_id = HISTORICAL_PREFIX + identity(source_key)
    previous = objects.get('DECISION', object_id)
    payload = {'subject': _text(subject, 'original market subject'), 'original': provenance,
               'result': {'conditions': conditions, 'valid_until': valid_until}}
    if previous and previous['payload'] == payload:
        return binding(previous)
    if previous:
        past = previous['payload']
        if (past['original'] != provenance or past['result']['valid_until'] != valid_until
                or [_condition_basis(c) for c in past['result']['conditions']] != [_condition_basis(c) for c in conditions]):
            raise Conflict('original condition or registration differs from the saved evidence')
    objects.put({'kind': 'DECISION', 'object_id': object_id, 'scope': 'PUBLIC',
                 'active': False, 'payload': payload, 'observed_at': provenance['data_cutoff'],
                 'known_at': known_at, 'source_ref': source_key, 'method_version': provenance['source_release'],
                 'change_reason': 'Carry verified original market conditions'},
                expected_version=previous['version'] if previous else 0)
    return binding(objects.get('DECISION', object_id))


def _condition_basis(condition):
    return {key: value for key, value in condition.items() if key not in {'original_review', 'predecessor'}}


def _condition_lineage(objects, record, condition, versions):
    lineage = [record]
    basis = _condition_basis(condition)
    for previous_version in range(record['version'] - 1, 0, -1):
        if previous_version not in versions:
            versions[previous_version] = objects.get(record['kind'], record['object_id'], version=previous_version)
        previous = versions[previous_version]
        if not previous or not any(_condition_basis(item) == basis for item in previous['payload']['result']['conditions']):
            break
        lineage.append(previous)
    return lineage


def _condition_history(objects, record, condition, *, at, versions):
    condition_id = condition['id']
    source = record['payload'].get('condition_sources', {}).get(condition_id)
    if source:
        origin = _bound_record(objects, source['target'])
        if (origin['kind'] != record['kind'] or origin['object_id'] != record['object_id']
                or origin['version'] >= record['version']):
            raise Conflict('a carried condition must refer to the original scope')
        reviews = [objects.get('REVIEW', _review_id(origin, condition_id), as_of=at)]
        archived = source.get('prior_review')
        if archived:
            prior = objects.get('REVIEW', archived['object_id'], version=archived['version'])
            if not prior or binding(prior) != archived:
                raise Conflict('the recovered review does not match its exact recorded result')
            if prior['known_ms'] <= millis(at):
                reviews.append(prior)
    else:
        lineage = _condition_lineage(objects, record, condition, versions)
        origin = lineage[-1]
        reviews = [objects.get('REVIEW', _review_id(item, condition_id), as_of=at) for item in lineage]
    latest = max((review for review in reviews if review),
                 key=lambda r: (r['known_ms'], r['recorded_ms']), default=None)
    return origin, latest


def _linked_condition(objects, ref, *, owner, max_version):
    record = _bound_record(objects, ref['target'])
    if (owner is None or record['kind'] != owner['kind'] or record['object_id'] != owner['object_id']
            or record['version'] > max_version):
        raise ValueError('condition transitions must refer to an earlier version of the same decision')
    condition = next((c for c in record['payload']['result']['conditions'] if c['id'] == ref['condition_id']), None)
    if condition is None:
        raise ValueError('the prior condition does not exist in its referenced decision')
    return record, condition


def _condition_transitions(objects, previous, result, prior, *, at):
    """Keep still-open, displaced tests visible at their original version and clock."""
    version = previous['version'] if previous else 0
    carried, versions, handled, retired = {}, {}, set(), set()
    predecessor_reviews = {}

    def prior_result(ref):
        record, criterion = _linked_condition(objects, ref, owner=previous, max_version=version)
        origin, review = _condition_history(objects, record, criterion, at=at, versions=versions)
        return (origin['version'], criterion['id']), binding(review) if review else None

    for ref in result.get('retired_conditions', []):
        key, _ = prior_result(ref)
        if key in retired:
            raise ValueError('a condition can be retired only once in a decision')
        retired.add(key)
    for condition in result['conditions']:
        cid = condition['id']
        original = prior.get(cid)
        same_test = original is not None and _condition_basis(condition) == _condition_basis(original)
        parent_key = None
        if (same_test and 'predecessor' in original and original['predecessor'] != condition['predecessor']
                and prior_result(original['predecessor'])[0] != prior_result(condition['predecessor'])[0]):
            raise ValueError('the predecessor of an existing test cannot silently change')
        if 'predecessor' in condition:
            parent_key, predecessor_reviews[cid] = prior_result(condition['predecessor'])
            handled.add(parent_key)
        if same_test:
            origin, review = _condition_history(objects, previous, original, at=at, versions=versions)
            if parent_key is not None and parent_key[0] >= origin['version']:
                raise ValueError('a predecessor must precede formation of the actual test')
            source = {'target': binding(origin)}
            if review and review['object_id'] != _review_id(origin, cid):
                source['prior_review'] = binding(review)
            carried[cid] = source
            handled.add((origin['version'], cid))
        elif (millis(condition['review_at']) <= millis(at) or
              (condition.get('observation_end') and millis(condition['observation_end']) <= millis(at))):
            raise ValueError('a newly formed condition must use a prospective review time and observation cutoff')
    if handled.intersection(retired):
        raise ValueError('a continued condition cannot also be retired')
    derived = {'condition_sources': carried} if carried else {}
    if predecessor_reviews:
        derived['predecessor_reviews'] = predecessor_reviews
    if previous:
        open_refs = []
        for check in decision_state(objects.db, previous, at=at)['conditions']:
            key = check['origin']['version'], check['id']
            if check['pending'] and key not in handled and key not in retired:
                open_refs.append({'target': check['target'], 'condition_id': check['id'],
                                  'review_version': (check['review_ref'] or {}).get('version', 0)})
        if open_refs:
            derived['open_prior_conditions'] = open_refs
    return derived


def _later_evidence(database, objects, evidence, *, original, cutoff, observation_end=None):
    for reference in evidence:
        if reference['type'] == 'OBSERVATION':
            sample = database.one('SELECT observed_ms,known_ms FROM observations WHERE observation_key=%s '
                                  'AND product=%s AND series=%s AND item_key=%s',
                                  tuple(reference[k] for k in ('observation_key', 'product', 'series', 'item_key')))
        elif reference['type'] == 'ENTITY' and reference['kind'] == 'PRICE_EVENT':
            sample = objects.get(reference['kind'], reference['object_id'], version=reference['version'])
        elif reference['type'] == 'BLOB':
            tagged = database.metadata('trading.public.' + reference['digest'].removeprefix('sha256:'))
            if tagged and 'sample' in reference:
                from .trading_theses import packet_sample
                _, _, sample = packet_sample(database, reference, tagged)
            elif tagged and tagged['provider'] == 'PUBLIC_SOURCE_RETRIEVAL':
                sample = {'observed_ms': millis(tagged['observed_at']), 'known_ms': millis(tagged['known_at'])}
            else:
                continue  # The newest packet clock cannot date its constituent observations.
        else:
            continue  # An undated blob or a prior judgement is supporting material only.
        if (sample and original < sample['observed_ms'] <= sample['known_ms'] <= cutoff
                and (observation_end is None or sample['observed_ms'] <= millis(observation_end))):
            return True
    return False


def record_review(database, *, target, condition_id, outcome, explanation, evidence, run,
                  impact='KEEP', next_review_at=None):
    objects = Entities(database)
    selected = _bound_record(objects, target)
    condition = next((item for item in selected['payload']['result']['conditions'] if item['id'] == condition_id), None)
    if condition is None or outcome not in OUTCOMES or impact not in {'KEEP', 'REVISE'}:
        raise ValueError('review must address an original condition and its effect')
    if outcome == 'UNKNOWN' and impact != 'KEEP':
        raise ValueError('missing evidence cannot revise the original judgement by itself')
    known = _time(run.get('known_at'), 'review knowledge cutoff')
    original, last = _condition_history(objects, selected, condition, at=run['known_at'], versions={})
    current = objects.get(selected['kind'], selected['object_id'], as_of=run['known_at'])
    if current and current['version'] > selected['version']:
        for item in current['payload']['result']['conditions']:
            if _condition_basis(item) == _condition_basis(condition):
                active_origin, active_review = _condition_history(objects, current, item, at=run['known_at'], versions={})
                if binding(active_origin) == binding(original) and active_review:
                    last = active_review
                break
    archived = original['payload'].get('original')
    formed = (max(millis(archived['formed_at']), millis(archived['registered_at'])) if archived
              else max(original['known_ms'], original['recorded_ms']))
    if selected['known_ms'] > known or known <= formed:
        raise ValueError('a review must occur after the original judgement was formed')
    references = _evidence(evidence, required=outcome != 'UNKNOWN')
    if outcome != 'UNKNOWN' and not _later_evidence(
            database, objects, references, original=formed, cutoff=known, observation_end=condition.get('observation_end')):
        raise ValueError('a factual outcome requires actual later observations within the original condition window')
    if next_review_at is not None:
        if outcome not in {'PARTIAL', 'NOT_TRIGGERED', 'UNKNOWN'} or _time(next_review_at, 'next review') <= known:
            raise ValueError('only open review outcomes can have a later recheck')
    object_id = _review_id(original, condition_id)
    previous = objects.get('REVIEW', object_id)
    prior_revision = (last['payload'].get('revision_reviewed_ms', last['known_ms'])
                      if last and last['payload']['revision_required'] else None)
    revision_reviewed = known if impact == 'REVISE' else prior_revision
    meaning = {'subject': binding(original), 'condition_id': condition_id, 'outcome': outcome, 'impact': impact,
               'revision_required': revision_reviewed is not None, 'revision_reviewed_ms': revision_reviewed,
               'explanation': _text(explanation, 'review explanation'), 'next_review_at': next_review_at}
    if impact == 'REVISE':
        meaning['revision_review_version'] = (previous['version'] if previous else 0) + 1
    elif last and 'revision_review_version' in last['payload']:
        meaning['revision_review_version'] = last['payload']['revision_review_version']
    return _save(objects, kind='REVIEW', object_id=object_id, scope=original['scope'], payload=meaning,
                 evidence=[{'type': 'BLOB', 'digest': original['digest'], 'relation': 'ORIGINAL_JUDGEMENT'}, *references],
                 run=run, expected_version=previous['version'] if previous else 0, reason=meaning['explanation'])


def _condition_check(objects, record, condition, *, at, versions):
    origin, review = _condition_history(objects, record, condition, at=at, versions=versions)
    last = review['payload'] if review else None
    deadline = last['next_review_at'] if last and last['next_review_at'] else condition['review_at']
    original_deadline = millis(condition['review_at']) if condition['review_at'] else None
    open_check = (last is None and 'original_review' not in condition
                  or last is not None and (last['outcome'] in {'PARTIAL', 'UNKNOWN'} or bool(last['next_review_at'])
                                           or (last['outcome'] == 'NOT_TRIGGERED' and
                                               (original_deadline is None or review['known_ms'] < original_deadline))))
    now = millis(at)
    window = {'observation_closed': millis(condition['observation_end']) <= now} if condition.get('observation_end') else {}
    return {**condition, **window, 'target': binding(record), 'origin': binding(origin), 'next_review_at': deadline, 'review': last,
            'pending': open_check, 'due': open_check and deadline is not None and millis(deadline) <= now,
            'prior_due_unchecked': original_deadline is not None and original_deadline <= now and review is None,
            'reviewed_ms': review['recorded_ms'] if review else None,
            'review_ref': binding(review) if review else None, 'review_known_ms': review['known_ms'] if review else None}


def _condition_detail(objects, record, condition, *, at, versions):
    check = _condition_check(objects, record, condition, at=at, versions=versions)
    if 'predecessor' in condition:
        prior, original = _linked_condition(objects, condition['predecessor'], owner=record,
                                            max_version=record['version'] - 1)
        check['prior_check'] = _condition_check(objects, prior, original, at=at, versions=versions)
        check['prior_review_at_adoption'] = record['payload'].get('predecessor_reviews', {}).get(condition['id'])
    return check


def _revised_since(check, reviewed_version, record):
    last = check['review'] or {}
    if 'revision_review_version' in last:
        return last['revision_review_version'] > reviewed_version
    return bool(last.get('revision_required') and
                last.get('revision_reviewed_ms', check['review_known_ms']) >= record['known_ms'])


def condition_detail(database, *, target, condition_id, scope, at):
    objects = Entities(database)
    record = _bound_record(objects, target)
    if record['scope'] != scope or record['known_ms'] > millis(at):
        raise ValueError('condition is outside the current phase and knowledge time')
    condition = next((c for c in record['payload']['result']['conditions'] if c['id'] == condition_id), None)
    if condition is None:
        raise ValueError('condition does not belong to the referenced decision')
    return {'condition': _condition_detail(objects, record, condition, at=at, versions={})}


def decision_state(database, record, *, at, policy=None):
    """Return applicability and scoped due conditions under the requested clock."""
    if record is None:
        return {'status': 'NOT_FORMED', 'conditions': []}
    as_of = _time(at, 'applicability time')
    objects = Entities(database)
    payload = record['payload']
    result = payload['result']
    if record['known_ms'] > as_of:
        return {'status': 'NOT_YET_KNOWN', 'conditions': []}
    checks = []
    requires_revision = False
    versions = {}
    for condition in result['conditions']:
        check = _condition_detail(objects, record, condition, at=at, versions=versions)
        last = check['review']
        if last and last['revision_required']:
            requires_revision |= last.get('revision_reviewed_ms', check['review_known_ms']) >= record['known_ms']
        if 'prior_check' in check:
            seen = check['prior_review_at_adoption'] or {}
            requires_revision |= _revised_since(check['prior_check'], seen.get('version', 0), record)
        checks.append(check)
    for ref in payload.get('open_prior_conditions', []):
        prior, original = _linked_condition(objects, ref, owner=record, max_version=record['version'] - 1)
        check = _condition_check(objects, prior, original, at=at, versions=versions)
        changed = _revised_since(check, ref.get('review_version', 0), record)
        requires_revision |= changed
        if changed or check['pending']:
            checks.append({**check, 'from_prior_version': True})
    if 'original' in payload:
        return {'status': 'HISTORICAL', 'conditions': checks}
    expired = millis(result['valid_until']) <= as_of
    applied_policy = payload.get('policy', {})
    policy_changed = policy is not None and applied_policy.get('basis') != policy.get('basis')
    if policy is not None and policy.get('kind') == 'POSITION':
        entry = _directional_entry(policy)
        expected_mode = PRICE_BASELINE_DIRECTIONAL if entry is not None else None
        policy_changed |= (applied_policy.get('entry_reference') != entry
                           or applied_policy.get('analysis_mode') != expected_mode)
        if payload['subject'] == POSITION_SUBJECT and policy.get('position_status') != 'ABSENT':
            requires_revision |= 'direction_comparison' not in result
    if expired:
        status = 'EXPIRED'
    elif policy_changed or requires_revision:
        status = 'NEEDS_REVIEW'
    elif result.get('decision') == 'UNRESOLVED':
        status = 'UNRESOLVED'
    else:
        status = 'CURRENT'
    state = {'status': status, 'conditions': checks}
    if payload.get('market'):
        source = payload['market']
        market = objects.get('DECISION', source['object_id'], as_of=at)
        state['market_changed'] = bool(market and binding(market) != source)
    return state


def review_targets(database, references, *, at):
    objects = Entities(database)
    records = [_bound_record(objects, target) for target in references]
    return [{'subject': record['payload']['subject'], 'original': record['payload']['original'],
             **condition}
            for record in records
            for condition in decision_state(database, record, at=at)['conditions']]
