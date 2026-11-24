"""The joint decision and the specific exposures its choices depend on."""
from __future__ import annotations

from . import trading_decisions as decisions
from .entities import Entities


OBJECT_ID = 'trading/portfolio/allocation'


def current(database):
    return Entities(database).get('PLAN', OBJECT_ID)


def scenario_review(database, portfolio, market):
    """Show the paths behind the adopted joint assessment when the public basis has moved."""
    if not portfolio:
        return None
    previous = portfolio['payload']['market']
    market_ref = decisions.binding(market)
    if previous == market_ref:
        return None
    original = Entities(database).get(previous['kind'], previous['object_id'], version=previous['version'])
    current_result = market['payload']['result']
    current_paths = {key: current_result[key] for key in ('primary', 'alternatives')}
    prior_paths = None
    if original and decisions.binding(original) == previous:
        prior_paths = {key: original['payload']['result'][key] for key in current_paths}
    if prior_paths == current_paths:
        return None
    requires_adoption = (
        prior_paths is None
        or len(prior_paths['alternatives']) != len(current_paths['alternatives'])
        or current_paths['primary'] in prior_paths['alternatives']
        or prior_paths['primary'] in current_paths['alternatives']
    )
    return {'portfolio': decisions.binding(portfolio), 'market': market_ref, 'prior_market': previous,
            'previous_paths': prior_paths, 'current_paths': current_paths,
            'requires_adoption': requires_adoption}


def _choice(record):
    payload = record['payload']
    result = payload['result']
    choice = {key: result[key] for key in ('decision', 'amount', 'month') if key in result}
    if 'amount' in choice and 'currency' in payload['policy']:
        choice['currency'] = payload['policy']['currency']
    if 'conditional_actions' in result:
        choice['conditional_actions'] = {state: branch['decision']
                                         for state, branch in result['conditional_actions'].items()}
    if 'value_zone' in result:
        choice['value_zone'] = {key: result['value_zone'][key] for key in ('lower', 'upper', 'quote_unit')}
    return choice


def compare_members(database, prior_refs, records, reuse, reviews):
    """Bind each author's economic comparison to the actual previous and adopted choices."""
    if not isinstance(reviews, dict) or not reviews.keys() <= records.keys():
        raise ValueError('member reviews must refer to applicable adopted decisions')
    objects = Entities(database)
    compared = {}
    for subject, current_record in records.items():
        previous = prior_refs.get(subject)
        prior_choice = None
        if previous:
            prior = current_record if decisions.binding(current_record) == previous else objects.get(
                previous['kind'], previous['object_id'], version=previous['version'])
            if prior and decisions.binding(prior) == previous:
                prior_choice = _choice(prior)
        current_choice = _choice(current_record)
        row = {'previous': previous, 'previous_choice': prior_choice, 'current_choice': current_choice,
               'adoption': 'REUSED' if subject in reuse else 'ASSESSED'}
        if subject in reviews:
            review = reviews[subject]
            if not isinstance(review, dict) or set(review) != {'relation', 'reason'}:
                raise ValueError('a member review needs its relation and decisive reason')
            relation, reason = review['relation'], review['reason']
            if (relation not in ('CONTINUE', 'REPRICE', 'REVISE', 'NEW', 'UNRESOLVED')
                    or not isinstance(reason, str) or not reason.strip()):
                raise ValueError('invalid relation or empty reasoning for a member review')
            if prior_choice is None and relation in ('CONTINUE', 'REPRICE', 'REVISE'):
                raise ValueError('a continuing or revised arrangement requires its previous decision')
            if subject in reuse and relation not in ('CONTINUE', 'UNRESOLVED'):
                raise ValueError('a new or revised arrangement requires a newly assessed decision')
            if relation == 'CONTINUE' and prior_choice != current_choice:
                raise ValueError('changed action, amount, month or established price terms need a revised assessment')
            if relation == 'REPRICE' and any(prior_choice.get(k) != current_choice.get(k)
                                             for k in ('decision', 'amount', 'currency', 'month', 'conditional_actions')):
                raise ValueError('repricing the same arrangement must preserve its action and size')
            row.update(relation=relation, reason=reason.strip())
        compared[subject] = row
    return compared


def assessment(market, scopes, primary, alternatives, interactions):
    if not primary.strip() or len(alternatives) != len(market['payload']['result']['alternatives']):
        raise ValueError('assess the primary scenario and each frozen alternative')
    if any(not effect.strip() for effect in alternatives):
        raise ValueError('each frozen alternative needs its joint portfolio effect')
    links = set()
    for entry in interactions:
        subject, source = entry['subject'], entry['depends_on']
        link = (subject, source)
        if (subject not in scopes or source not in scopes or subject == source
                or link in links or not entry['reason'].strip()):
            raise ValueError('portfolio interactions need distinct applicable subjects and an actual dependency')
        links.add(link)
    return {'primary': primary, 'alternatives': alternatives, 'interactions': interactions}


def _selection(record):
    if record is None:
        return None
    payload = record['payload']
    policy = payload['policy']
    result = payload['result']
    actions = {state: branch['decision'] for state, branch in result.get('conditional_actions', {}).items()}
    zone = result.get('value_zone', {})
    entry = policy.get('entry_reference') if policy.get('position_status') == 'PRESENT' else None
    return (policy['basis'], result['decision'], result.get('amount'), entry['price'] if entry else None,
            actions, tuple(zone.get(key) for key in ('lower', 'upper', 'quote_unit')),
            {(ref['file'], ref['quote']) for ref in payload['fact_refs']})


def _links(interactions):
    return {(entry['subject'], entry['depends_on']) for entry in interactions}


def linked_sources(interactions, subjects):
    links = _links(interactions)
    seen = set(subjects)
    while sources := {source for subject, source in links if subject in seen} - seen:
        seen.update(sources)
    return seen.difference(subjects)


def changed_inputs(database, previous, records, *, interactions=None):
    """Return dependents whose linked selection or dependency actually changed."""
    if previous is None:
        return {}
    payload = previous['payload']
    old_links = _links(payload['interactions'])
    new_links = old_links if interactions is None else _links(interactions)
    objects = Entities(database)
    changes = {}
    changed_sources = {}
    for subject, source in sorted(old_links | new_links):
        if subject not in records:
            continue
        link = (subject, source)
        if link in old_links and link in new_links:
            if source not in changed_sources:
                ref = payload['decisions'][source]
                new = records.get(source)
                changed_sources[source] = False
                if new is None or decisions.binding(new) != ref:
                    old = objects.get('PLAN', ref['object_id'], version=ref['version'])
                    changed_sources[source] = (old is None or decisions.binding(old) != ref
                                               or _selection(old) != _selection(new))
            if not changed_sources[source]:
                continue
        changes.setdefault(subject, []).append(source)
    return changes


def save(database, *, market, records, assessed, run, expected_version):
    objects = Entities(database)
    market_ref = decisions.binding(market)
    refs = {subject: decisions.binding(record) for subject, record in records.items()}
    payload = {'market': market_ref, 'decisions': refs, **assessed}
    evidence = [{'type': 'BLOB', 'digest': market_ref['digest'], 'relation': 'FROZEN_MARKET_BASIS'}]
    evidence += [{'type': 'BLOB', 'digest': refs[subject]['digest'], 'relation': 'PORTFOLIO_DECISION'}
                 for subject in sorted(refs)]
    objects.put({
        'kind': 'PLAN', 'object_id': OBJECT_ID, 'scope': 'PERSONAL',
        'payload': payload, 'known_at': run['known_at'], 'observed_at': run['known_at'],
        'source_ref': run['ref'], 'method_version': run['method_version'],
        'change_reason': 'Adopt the joint spot and position decisions',
        'dependencies': evidence,
    }, expected_version=expected_version)
    return current(database)
