"""The joint decision and the specific exposures its choices depend on."""
from __future__ import annotations

from . import trading_decisions as decisions
from .entities import Entities


OBJECT_ID = 'trading/portfolio/allocation'


def current(database):
    return Entities(database).get('PLAN', OBJECT_ID)


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
    result = payload['result']
    actions = {state: branch['decision'] for state, branch in result.get('conditional_actions', {}).items()}
    zone = result.get('value_zone', {})
    return (payload['policy']['basis'], result['decision'], result.get('amount'),
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
