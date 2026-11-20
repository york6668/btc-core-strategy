"""Decisions and their subsequent reviews, stored by their business subject.

The service owns the transaction, the frozen run context, and the selection of
the applicable policy and evidence. This module owns decision applicability.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .codec import Conflict, identity, millis, utc
from .entities import Entities


MARKET_PREFIX = 'trading/market/'
PERSONAL_PREFIX = 'trading/personal/'
REVIEW_PREFIX = 'trading/review/'
CHOICES = {
    'MONTHLY': {'CONTINUE', 'SKIP', 'UNRESOLVED'},
    'OPPORTUNITY': {'PARTICIPATE', 'WAIT', 'AVOID', 'UNRESOLVED'},
    'POSITION': {'KEEP', 'REDUCE', 'EXIT', 'HEDGE', 'NO_POSITION', 'UNRESOLVED'},
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


def _conditions(values, *, known_at, valid_until):
    if not isinstance(values, list) or not values:
        raise ValueError('a decision needs a testable review condition')
    ids = set()
    conditions = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('a review condition needs an identity, criterion and time')
        condition_id = _text(value.get('id'), 'condition identity')
        review_at = _time(value.get('review_at'), 'condition review time')
        if condition_id in ids or not known_at < review_at <= valid_until:
            raise ValueError('review conditions need unique identities and times within the decision horizon')
        ids.add(condition_id)
        conditions.append({'id': condition_id, 'criterion': _text(value.get('criterion'), 'review criterion'),
                           'review_at': value['review_at']})
    return conditions


def _horizon(result, run):
    cutoff = _time(run.get('known_at'), 'frozen knowledge cutoff')
    expiry = _time(result.get('valid_until'), 'decision expiry')
    if expiry <= cutoff or expiry <= millis():
        raise ValueError('a new decision must have a future expiry')
    return {'valid_until': result['valid_until'],
            'conditions': _conditions(result.get('conditions'), known_at=cutoff, valid_until=expiry)}


def current_market(database, subject):
    return Entities(database).get('DECISION', _object_id(MARKET_PREFIX, subject))


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
    if record is None or any(record[key] != reference[key] for key in ('version', 'digest')):
        raise Conflict('bound trading decision does not match its saved record')
    return record


def _save(objects, *, kind, object_id, scope, payload, evidence, run, expected_version, reason):
    previous = objects.get(kind, object_id)
    version = previous['version'] if previous else 0
    if type(expected_version) is not int or expected_version != version:
        raise Conflict('the affected trading decision changed before submission')
    if kind != 'REVIEW' and previous and previous['payload'] == payload:
        return previous
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


def adopt_market(database, *, subject, expected_version, result, evidence, run):
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
    if amount not in alternatives or ((amount == 0) != (choice == 'SKIP')):
        raise ValueError('the selected contribution must match the chosen basket alternative')
    judgement['amount'] = _amount_text(amount)
    if 0 < amount < budget:
        judgement['amount_basis'] = _text(result.get('amount_basis'), 'why a partial amount beats the full plan')


def _monthly_window(policy, judgement, run):
    try:
        tz = ZoneInfo(_text(policy.get('calendar'), 'applicable planning calendar'))
    except ZoneInfoNotFoundError as exc:
        raise ValueError('unknown planning calendar') from exc
    decided = utc(run['known_at']).astimezone(tz)
    year, month = (decided.year + 1, 1) if decided.month == 12 else (decided.year, decided.month + 1)
    if millis(judgement['valid_until']) > millis(datetime(year, month, 1, tzinfo=tz)):
        raise ValueError('a monthly choice cannot outlive the confirmed planning month')
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
    if not isinstance(zone, dict):
        raise ValueError('participation requires an established value zone')
    low = _decimal(zone.get('lower'), 'value zone lower bound')
    high = _decimal(zone.get('upper'), 'value zone upper bound')
    if not 0 < low < high:
        raise ValueError('established value zone needs ordered positive bounds')
    judgement['value_zone'] = {'lower': _amount_text(low), 'upper': _amount_text(high),
                               'quote_unit': _text(zone.get('quote_unit'), 'value zone quote unit'),
                               'basis': _text(zone.get('basis'), 'value zone evidence basis')}


def market_inputs(record):
    result = record['payload']['result']
    return {**{key: result[key] for key in ('conclusion', 'primary', 'alternatives', 'value')},
            **{'assessment/' + key: val for key, val in result['assessments'].items()}}


def adopt_personal(database, *, subject, expected_version, market, policy, result, evidence, run,
                   fact_refs=(), fact_history=None, market_refs=('conclusion',)):
    objects = Entities(database)
    public = _bound_record(objects, market, current=True)
    if public['kind'] != 'DECISION' or public['scope'] != 'PUBLIC':
        raise ValueError('personal decisions require an independently frozen public judgement')
    as_of = _time(run.get('known_at'), 'frozen knowledge cutoff')
    if public['known_ms'] > as_of or _time(public['payload']['result']['valid_until'], 'market expiry') <= as_of:
        raise Conflict('the public judgement is outside the applicable decision window')
    if decision_state(database, public, at=run['known_at'])['status'] != 'CURRENT':
        raise Conflict('the public judgement requires reassessment before personal adoption')
    inputs = market_inputs(public)
    if (not isinstance(market_refs, (list, tuple)) or not market_refs or len(set(market_refs)) != len(market_refs)
            or any(field not in inputs for field in market_refs)):
        raise ValueError('select the exact parts of the market judgement this decision depends on')
    kind = policy.get('kind')
    choice = result.get('decision')
    if kind not in CHOICES or choice not in CHOICES[kind]:
        raise ValueError('the choice must match its investment purpose')
    applied_policy = {'kind': kind, 'basis': _text(policy.get('basis'), 'applicable policy and facts')}
    judgement = {'decision': choice, 'reason': _text(result.get('reason'), 'decisive reason'),
                 'risk': _text(result.get('risk'), 'principal risk'),
                 'opportunity_cost': _text(result.get('opportunity_cost'), 'forgone opportunity'),
                 **_horizon(result, run)}
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
        else:
            _opportunity(judgement, policy, result, budget)
    if kind == 'POSITION':
        held = policy.get('position_status')
        if choice == 'NO_POSITION' and held != 'ABSENT':
            raise ValueError('absence of a position needs confirmed facts')
        if held == 'UNKNOWN' and choice != 'UNRESOLVED':
            raise ValueError('an unknown position has no unconditional holding decision')
        if held == 'ABSENT' and choice != 'NO_POSITION':
            raise ValueError('an absent position cannot supply current holding or protection')
    return _save(objects, kind='PLAN', object_id=_object_id(PERSONAL_PREFIX, subject), scope='PERSONAL',
                 payload={'subject': subject, 'market': binding(public), 'policy': applied_policy,
                          'market_inputs': {field: inputs[field] for field in market_refs},
                          'fact_refs': list(fact_refs), 'fact_history': fact_history or {}, 'result': judgement},
                 evidence=[{'type': 'BLOB', 'digest': public['digest'], 'relation': 'FROZEN_MARKET_BASIS'},
                           *_evidence(evidence, required=choice != 'UNRESOLVED')], run=run,
                 expected_version=expected_version, reason=judgement['reason'])


def _review_id(record, condition_id):
    return REVIEW_PREFIX + identity(record['kind'], record['object_id'], record['version'], condition_id)


def _later_evidence(database, objects, evidence, *, original, cutoff):
    for reference in evidence:
        if reference['type'] == 'OBSERVATION':
            sample = database.one('SELECT observed_ms,known_ms FROM observations WHERE observation_key=%s '
                                  'AND product=%s AND series=%s AND item_key=%s',
                                  tuple(reference[k] for k in ('observation_key', 'product', 'series', 'item_key')))
        elif reference['type'] == 'ENTITY' and reference['kind'] == 'PRICE_EVENT':
            sample = objects.get(reference['kind'], reference['object_id'], version=reference['version'])
        elif reference['type'] == 'BLOB':
            tagged = database.metadata('trading.public.' + reference['digest'].removeprefix('sha256:'))
            if not tagged or tagged['provider'] != 'PUBLIC_SOURCE_RETRIEVAL':
                continue  # The newest packet clock cannot date its constituent observations.
            sample = {'observed_ms': millis(tagged['observed_at']), 'known_ms': millis(tagged['known_at'])}
        else:
            continue  # An undated blob or a prior judgement is supporting material only.
        if sample and original < sample['observed_ms'] <= sample['known_ms'] <= cutoff:
            return True
    return False


def record_review(database, *, target, condition_id, outcome, explanation, evidence, run,
                  impact='KEEP', next_review_at=None):
    objects = Entities(database)
    original = _bound_record(objects, target)
    condition = next((item for item in original['payload']['result']['conditions'] if item['id'] == condition_id), None)
    if condition is None or outcome not in OUTCOMES or impact not in {'KEEP', 'REVISE'}:
        raise ValueError('review must address an original condition and its effect')
    if outcome == 'UNKNOWN' and impact != 'KEEP':
        raise ValueError('missing evidence cannot revise the original judgement by itself')
    known = _time(run.get('known_at'), 'review knowledge cutoff')
    formed = max(original['known_ms'], original['recorded_ms'])
    if known <= formed:
        raise ValueError('a review must occur after the original judgement was formed')
    references = _evidence(evidence, required=outcome != 'UNKNOWN')
    if outcome != 'UNKNOWN' and not _later_evidence(database, objects, references, original=formed, cutoff=known):
        raise ValueError('a factual outcome requires actual observations after the original judgement')
    if next_review_at is not None:
        if outcome not in {'PARTIAL', 'NOT_TRIGGERED', 'UNKNOWN'} or _time(next_review_at, 'next review') <= known:
            raise ValueError('only open review outcomes can have a later recheck')
    object_id = _review_id(original, condition_id)
    previous = objects.get('REVIEW', object_id)
    revision_required = impact == 'REVISE' or bool(previous and previous['payload']['revision_required'])
    meaning = {'subject': binding(original), 'condition_id': condition_id, 'outcome': outcome, 'impact': impact,
               'revision_required': revision_required,
               'explanation': _text(explanation, 'review explanation'), 'next_review_at': next_review_at}
    return _save(objects, kind='REVIEW', object_id=object_id, scope=original['scope'], payload=meaning,
                 evidence=[{'type': 'BLOB', 'digest': original['digest'], 'relation': 'ORIGINAL_JUDGEMENT'}, *references],
                 run=run, expected_version=previous['version'] if previous else 0, reason=meaning['explanation'])


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
    for condition in result['conditions']:
        review = objects.get('REVIEW', _review_id(record, condition['id']), as_of=at)
        last = review['payload'] if review else None
        deadline = last['next_review_at'] if last and last['next_review_at'] else condition['review_at']
        original_deadline = millis(condition['review_at'])
        open_check = (last is None or last['outcome'] in {'PARTIAL', 'UNKNOWN'} or bool(last['next_review_at'])
                      or (last['outcome'] == 'NOT_TRIGGERED' and review['known_ms'] < original_deadline))
        checks.append({**condition, 'next_review_at': deadline, 'review': last,
                       'due': open_check and millis(deadline) <= as_of,
                       'prior_due_unchecked': original_deadline <= as_of and review is None,
                       'reviewed_ms': review['recorded_ms'] if review else None})
    expired = millis(result['valid_until']) <= as_of
    policy_changed = policy is not None and payload.get('policy', {}).get('basis') != policy.get('basis')
    requires_revision = any(item['review'] and item['review']['revision_required'] for item in checks)
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
