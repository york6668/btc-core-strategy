"""The joint decision and the specific exposures its choices depend on."""
from __future__ import annotations

from . import trading_decisions as decisions
from .codec import Conflict, identity, millis, stamp
from .entities import Entities, key


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
            if not isinstance(review, dict) or not {'relation', 'reason'} <= review.keys() <= {
                    'relation', 'reason', 'user_feedback', 'retrospective', 'prior_retrospective'}:
                raise ValueError('a member review needs its relation and decisive reason')
            relation, reason = review['relation'], review['reason']
            if (relation not in ('CONTINUE', 'READY', 'ADDITIONAL', 'REPRICE', 'REVISE', 'NEW', 'UNRESOLVED')
                    or not isinstance(reason, str) or not reason.strip()):
                raise ValueError('invalid relation or empty reasoning for a member review')
            if prior_choice is None and relation in ('CONTINUE', 'READY', 'ADDITIONAL', 'REPRICE', 'REVISE'):
                raise ValueError('a continuing or revised arrangement requires its previous decision')
            if subject in reuse and relation not in ('CONTINUE', 'READY', 'UNRESOLVED'):
                raise ValueError('a new or revised arrangement requires a newly assessed decision')
            if relation in ('CONTINUE', 'READY') and prior_choice != current_choice:
                raise ValueError('changed action, amount, month or established price terms need a revised assessment')
            if relation == 'REPRICE' and any(prior_choice.get(k) != current_choice.get(k)
                                             for k in ('decision', 'amount', 'currency', 'month', 'conditional_actions')):
                raise ValueError('repricing the same arrangement must preserve its action and size')
            if relation == 'ADDITIONAL' and decisions.binding(current_record) == previous:
                raise ValueError('an additional action must be a separately assessed choice')
            row.update(relation=relation, reason=reason.strip())
        compared[subject] = row
    return compared


def followup(database, target, *, as_of):
    """Look up user feedback and the position review for the exact recommendation, including older plans."""
    objects = Entities(database)
    original = decisions._bound_record(objects, target)
    if original['kind'] != 'PLAN' or original['scope'] != 'PERSONAL' or original['known_ms'] > millis(as_of):
        raise ValueError('follow-up needs a personal recommendation already known at this time')
    root = _arrangement(objects, original, as_of)
    view = _followup(objects, root, as_of)
    if original['digest'] != root['digest']:
        view['queried_decision'] = target
        view['queried_terms'] = _choice(original)
    return view


def first_member_review(objects, member, at):
    """Read the joint decision that first adopted these exact terms."""
    row = objects.db.one(
        'SELECT v.digest FROM dependencies d JOIN entity_versions v ON v.entity_key=d.source_key '
        'AND v.version=d.source_version WHERE d.target_key=%s AND d.target_type=%s AND d.relation=%s '
        'AND d.source_key=%s AND v.known_ms<=%s ORDER BY d.source_version LIMIT 1',
        (member['digest'], 'BLOB', 'PORTFOLIO_DECISION', key('PLAN', OBJECT_ID), millis(at)))
    if row:
        joint = objects.db.value(row['digest'])['payload']
        return joint.get('member_reviews', {}).get(member['payload']['subject'], {})
    return {}


def _arrangement(objects, member, at):
    root = first_member_review(objects, member, at).get('arrangement')
    return decisions._bound_record(objects, root) if root and root != decisions.binding(member) else member


def _followup(objects, original, at):
    exact = decisions.binding(original)
    key = 'trading/review/arrangement/' + identity(exact['object_id'], exact['version'], exact['digest'])
    saved = objects.get('REVIEW', key, as_of=at)
    result = original['payload']['result']
    return {'target': exact, 'object_id': key, 'record': decisions.binding(saved) if saved else None,
            'expected': {k: result[k] for k in ('decision', 'reason', 'risk', 'opportunity_cost', 'comparisons',
                                                'direction_comparison', 'conditional_actions') if k in result},
            **(saved['payload'] if saved else {'confirmations': [], 'adoption': 'UNKNOWN', 'execution': 'UNKNOWN'})}


_ADOPTION = {'ACCEPTED', 'DECLINED'}
_EXECUTION = {'NOT_EXECUTED', 'PARTIALLY_EXECUTED', 'EXECUTED'}


def _confirmation(item, files, original, run):
    if not isinstance(item, dict) or set(item) != {'target', 'type', 'confirmed_at', 'fact_ref'}:
        raise ValueError('confirmation needs the original decision, type, actual time and literal source')
    status, source = item['type'], item['fact_ref']
    if status not in _ADOPTION | _EXECUTION or not isinstance(source, dict) or set(source) != {'file', 'quote'}:
        raise ValueError('invalid user confirmation or source')
    when = decisions._time(item['confirmed_at'], 'actual user confirmation time')
    if not max(original['known_ms'], original['recorded_ms']) <= when <= millis(run['known_at']):
        raise ValueError('confirmation must be known after the recommendation and by this evaluation')
    fact = files.get(source['file'])
    if (not fact or not isinstance(source['quote'], str) or not source['quote'].strip()
            or fact['text'].count(source['quote']) != 1):
        raise Conflict('identify one user confirmation in its authoritative source, including enough context')
    return {'target': item['target'], 'type': status, 'confirmed_at': stamp(item['confirmed_at']), 'fact_ref': source,
            'source_digest': fact['digest']}


def _save_followup(db, original, entries, recap, files, run):
    from .trading import public_evidence
    objects = Entities(db)
    prior = _followup(objects, original, run['known_at'])
    target = prior['target']
    payload = {k: prior[k] for k in ('target', 'adoption', 'execution', 'retrospective') if k in prior}
    confirmed = list(prior['confirmations'])
    evidence = [{'type': 'BLOB', 'digest': target['digest'], 'relation': 'ORIGINAL_ARRANGEMENT'}]
    if prior['record']:
        evidence.append({'type': 'BLOB', 'digest': prior['record']['digest'], 'relation': 'PREVIOUS_REVIEW'})
    for item in entries:
        cited = decisions._bound_record(objects, item['target'])
        event = _confirmation(item, files, cited, run)
        axis = _ADOPTION if event['type'] in _ADOPTION else _EXECUTION
        same_source = next((e for e in confirmed if e['type'] in axis and e['fact_ref'] == event['fact_ref']), None)
        if same_source:
            if (same_source['type'], same_source['confirmed_at']) != (event['type'], event['confirmed_at']):
                raise Conflict('the same user confirmation cannot become a different event')
            continue
        same = [e for e in confirmed if e['type'] in axis and e['confirmed_at'] == event['confirmed_at']]
        if same and any(e['type'] != event['type'] for e in same):
            raise Conflict('conflicting confirmation for the same arrangement and time')
        digest = db.put_blob(files[event['fact_ref']['file']]['text'].encode())
        if digest != event['source_digest']:
            raise Conflict('confirmed source version changed')
        evidence.append({'type': 'BLOB', 'digest': digest, 'relation': 'CONFIRMED_LOCAL_FACT'})
        if event['target'] != target:
            evidence.append({'type': 'BLOB', 'digest': event['target']['digest'], 'relation': 'CONFIRMED_PLAN_TERMS'})
        confirmed.append(event)
    confirmed.sort(key=lambda e: millis(e['confirmed_at']))
    for event in confirmed:
        axis = 'adoption' if event['type'] in _ADOPTION else 'execution'
        payload[axis] = event['type']
    payload['confirmations'] = confirmed
    if recap is not None:
        if not isinstance(recap, dict) or set(recap) != {'target', 'scope', 'position_result', 'application', 'evidence'}:
            raise ValueError('position review requires its original plan, outcome, next use and subsequent evidence')
        scope = recap['scope']
        if scope not in ('PLAN_ONLY', 'CONFIRMED_EXECUTION'):
            raise ValueError('distinguish a plan outcome from an actual execution outcome')
        if scope == 'CONFIRMED_EXECUTION' and payload['execution'] not in ('PARTIALLY_EXECUTED', 'EXECUTED'):
            raise Conflict('actual execution outcomes require a confirmed operation for this arrangement')
        reviewed = decisions._bound_record(objects, recap['target'])
        if recap['target'] != target:
            evidence.append({'type': 'BLOB', 'digest': reviewed['digest'], 'relation': 'REVIEWED_PLAN_TERMS'})
            if scope == 'CONFIRMED_EXECUTION' and not any(
                    e.get('target') == recap['target'] and e['type'] in ('PARTIALLY_EXECUTED', 'EXECUTED')
                    for e in confirmed):
                raise Conflict('reviewing execution at revised terms requires a confirmation of those terms')
        refs = decisions._evidence(public_evidence(db, recap['evidence']))
        if not decisions._later_evidence(db, objects, refs, original=max(reviewed['known_ms'], reviewed['recorded_ms']),
                                         cutoff=millis(run['known_at'])):
            raise ValueError('a position retrospective needs observations made after the original decision')
        payload['retrospective'] = {'target': recap['target'], 'scope': scope, 'position_result': decisions._text(recap['position_result'], 'outcome versus original trade-off'),
                                    'application': decisions._text(recap['application'], 'applicability in a future decision'),
                                    'evidence': refs}
        evidence.extend(refs)
    if prior['record'] and all(prior.get(k) == v for k, v in payload.items()):
        return prior['record']
    objects.put({'kind': 'REVIEW', 'object_id': prior['object_id'], 'scope': 'PERSONAL', 'payload': payload,
                 'known_at': run['known_at'], 'observed_at': run['known_at'], 'source_ref': run['ref'],
                 'method_version': run['method_version'], 'dependencies': evidence,
                 'change_reason': 'Review the original plan against confirmed user feedback or subsequent facts'},
                expected_version=prior['record']['version'] if prior['record'] else 0)
    return decisions.binding(objects.get('REVIEW', prior['object_id']))


def apply_followups(db, compared, reviews, records, previous, files, run):
    objects = Entities(db)
    for subject, row in compared.items():
        old = previous.get(subject, {})
        current = decisions.binding(records[subject])
        same = row['previous'] == current or row.get('relation') in ('CONTINUE', 'READY', 'REPRICE')
        root = (old.get('arrangement') or row['previous']) if same else current
        row['arrangement'] = root
        for key in ('last_user_feedback', 'last_confirmation_at', 'last_retrospective', 'accepted_arrangement', 'last_adoption'):
            if old.get(key):
                row[key] = old[key]
        review = reviews.get(subject, {})
        updates = review.get('user_feedback', [])
        if not isinstance(updates, list):
            raise ValueError('user feedback must be a list of confirmations')
        recap = review.get('retrospective')
        groups = {}
        for item in [*updates, *([recap] if recap is not None else [])]:
            if not isinstance(item, dict) or not isinstance(item.get('target'), dict):
                raise ValueError('each follow-up must refer to its exact original arrangement')
            ref = item['target']
            if ref.get('kind') != 'PLAN' or ref.get('object_id') != current['object_id']:
                raise ValueError('the confirmed arrangement must belong to this portfolio member')
            original = decisions._bound_record(objects, ref)
            if decisions.binding(original) != ref:
                raise ValueError('use the exact original arrangement reference')
            original = _arrangement(objects, original, run['known_at'])
            group = groups.setdefault(original['version'], {'plan': original, 'events': [], 'recap': None})
            if item is recap:
                group['recap'] = item
            else:
                group['events'].append(item)
        for group in groups.values():
            original, entries, match = group['plan'], group['events'], group['recap']
            original_ref = decisions.binding(original)
            saved = _save_followup(db, original, entries, match, files, run)
            if entries:
                when = max(millis(event['confirmed_at']) for event in entries)
                if when >= row.get('last_confirmation_at', 0):
                    row.update(last_user_feedback=saved, last_confirmation_at=when)
            if any(entry['type'] in _ADOPTION for entry in entries):
                verified = objects.get('REVIEW', saved['object_id'], version=saved['version'])['payload']
                event = next(e for e in reversed(verified['confirmations']) if e['type'] in _ADOPTION)
                when = millis(event['confirmed_at'])
                latest = row.get('last_adoption') or {}
                changes_baseline = event['type'] == 'ACCEPTED' or (row.get('accepted_arrangement') and latest.get('target') == original_ref)
                if changes_baseline and when >= latest.get('confirmed_ms', 0):
                    if latest.get('confirmed_ms') == when and latest.get('target') != original_ref:
                        raise Conflict('order the user decisions before changing the accepted arrangement')
                    row['last_adoption'] = {'confirmed_ms': when, 'target': original_ref, 'type': event['type']}
                    if event['type'] == 'ACCEPTED':
                        row['accepted_arrangement'] = event.get('target', original_ref)
                    else:
                        row.pop('accepted_arrangement', None)
            if match:
                row['last_retrospective'] = saved
        current_followup = _followup(objects, decisions._bound_record(objects, root), run['known_at'])
        if current_followup['record']:
            row['arrangement_feedback'] = current_followup['record']
            row['user_adoption'] = current_followup['adoption']
            row['actual_execution'] = current_followup['execution']
        if same and row.get('actual_execution') == 'EXECUTED':
            result = records[subject]['payload']['result']
            active = result.get('conditional_actions', {}).get('PRESENT', result)['decision']
            if active in ('REDUCE', 'INCREASE', 'EXIT', 'PARTICIPATE', 'CONTINUE'):
                raise Conflict('a completed action cannot be issued again as a pending step of the same arrangement')

        cited = review.get('prior_retrospective')
        if cited is not None:
            if not isinstance(cited, dict) or set(cited) != {'review', 'applicability'}:
                raise ValueError('cite an exact prior position review and explain its applicability')
            source = cited['review']
            if (not isinstance(source, dict) or set(source) != {'kind', 'object_id', 'version', 'digest'}
                    or source['kind'] != 'REVIEW' or not isinstance(source['object_id'], str)
                    or not source['object_id'].startswith('trading/review/arrangement/')):
                raise ValueError('an exact personal retrospective is required')
            historical = objects.get('REVIEW', source.get('object_id'), version=source.get('version'))
            if (not historical or decisions.binding(historical) != source or historical['scope'] != 'PERSONAL'
                    or 'retrospective' not in historical['payload'] or historical['known_ms'] > millis(run['known_at'])
                    or historical['source_ref'] == run['ref']):
                raise Conflict('cite an existing personal position retrospective from an earlier evaluation')
            row['prior_retrospective'] = {'review': source, 'applicability': decisions._text(
                cited['applicability'], 'why the prior experience applies or differs now')}


def arrangement_confirmations(db, portfolio):
    """User statements tied to the tranche currently described by each report item."""
    result = {}
    for subject, row in (portfolio or {}).get('payload', {}).get('member_reviews', {}).items():
        ref = row.get('arrangement_feedback')
        if ref:
            review = Entities(db).get('REVIEW', ref['object_id'], version=ref['version'])
            if not review or decisions.binding(review) != ref or review['payload']['target'] != row['arrangement']:
                raise Conflict('the user confirmation belongs to a different arrangement')
            if review['payload']['confirmations']:
                result[subject] = review['payload']['confirmations']
    return result


def followup_context(db, portfolio):
    objects = Entities(db)
    context = {}
    for subject, row in (portfolio or {}).get('payload', {}).get('member_reviews', {}).items():
        details = {}
        accepted = row.get('accepted_arrangement')
        if accepted:
            confirmed = decisions._bound_record(objects, accepted)
            details['accepted_arrangement'] = {'target': accepted, 'choice': _choice(confirmed),
                                               'reason': confirmed['payload']['result']['reason']}
        for name in ('arrangement_feedback', 'last_user_feedback', 'last_retrospective'):
            ref = row.get(name)
            if not ref:
                continue
            record = objects.get('REVIEW', ref['object_id'], version=ref['version'])
            if not record or decisions.binding(record) != ref:
                raise Conflict('the bound personal follow-up is unavailable')
            payload = record['payload']
            target = payload['target']
            if name == 'last_retrospective':
                target = payload['retrospective'].get('target', target)
            original = decisions._bound_record(objects, target)['payload']['result']
            details[name] = {'review': ref, 'original_reason': original['reason'], **payload}
        if details:
            context[subject] = details
    return context


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
    for row in assessed['member_reviews'].values():
        related = [row[k] for k in ('arrangement_feedback', 'last_user_feedback', 'last_retrospective') if k in row]
        if 'accepted_arrangement' in row:
            evidence.append({'type': 'BLOB', 'digest': row['accepted_arrangement']['digest'], 'relation': 'USER_ACCEPTED_PLAN'})
        if 'prior_retrospective' in row:
            related.append(row['prior_retrospective']['review'])
        for digest in dict.fromkeys(ref['digest'] for ref in related):
            evidence.append({'type': 'BLOB', 'digest': digest, 'relation': 'ARRANGEMENT_REVIEW'})
    objects.put({
        'kind': 'PLAN', 'object_id': OBJECT_ID, 'scope': 'PERSONAL',
        'payload': payload, 'known_at': run['known_at'], 'observed_at': run['known_at'],
        'source_ref': run['ref'], 'method_version': run['method_version'],
        'change_reason': 'Adopt the joint spot and position decisions',
        'dependencies': evidence,
    }, expected_version=expected_version)
    return current(database)
