"""Select adopted sources for each part of the investment report."""
from __future__ import annotations

from .codec import Conflict, millis
from .trading_decisions import binding
from .trading_policy import POSITION_SUBJECT


MODULE_SOURCES = {
    'market': ['public.conclusion', 'public.assessments', 'public.valuations'],
    'outlook': {'primary': 'public.primary', 'alternative': 'public.alternatives'},
    'opportunities': {'comparison': ['public.value', 'public.valuations', 'public.assessments']},
    'allocation': ['allocation.members', 'allocation.cash_sources'],
    'crosschecks': {'evidence': ['public.assessments', 'public.evidence']},
    'risks': {'conditions': ['public.conditions', 'public.valid_until']},
}


def _cash_components(terms):
    return {name: {key: member.get(key) for key in ('currency', 'budget', 'amount', 'month', 'unavailable')}
            for name, member in terms.items() if member.get('kind') == 'MONTHLY'}


def allocation_changes(base, edits, terms, basis, records, portfolio, *, correction, assessed_at):
    """Apply edits to confirmed allocation text only where its own sources or scope warrant them."""
    from .trading_report import edit_item
    if not base or not base['seeded']:
        return edits
    old_terms = base.get('allocation_terms', {})
    output = {}
    for subject, change in edits.items():
        prior = base['modules']['allocation'].get(subject)
        if prior is None or subject == POSITION_SUBJECT or change == prior:
            output[subject] = change
            continue
        path = 'allocation.' + subject
        if subject == 'cash':
            was, now = _cash_components(old_terms), _cash_components(terms)
            changed = old_terms.get('cash') != terms['cash'] or was != now
            replaced = was.keys() != now.keys() or old_terms.get('cash', {}).keys() != terms['cash'].keys()
            record = portfolio
        else:
            was, now = old_terms.get(subject, {}), terms.get(subject, {})
            changed = was != now or base.get('basis', {}).get(path) != basis.get(path)
            replaced = any(was.get(key) != now.get(key) for key in ('kind', 'decision', 'currency', 'unavailable'))
            record = records.get(subject) or portfolio
        # A correction to a displayed fact can be necessary even when its formal source stayed the same.
        # Bind this exception to the item being corrected, not to the purpose of the entire report.
        whole = isinstance(change, dict) and 'replace_all' in change
        explanation = change if whole else (change.get('correction') if isinstance(change, dict) else None)
        explained = (isinstance(explanation, dict) and record is not None and explanation.get('source') == binding(record)
                     and isinstance(explanation.get('reason'), str) and bool(explanation['reason'].strip()))
        expiry = base.get('valid_until', {}).get(path)
        due = path in base.get('pending_review', {}) or (expiry and millis(expiry) <= millis(assessed_at))
        correcting_item = explained and (correction or due)
        if not changed and not correcting_item:
            raise Conflict('retain the confirmed wording for unchanged allocation inputs: ' + subject)
        if whole and not (explained and (replaced or correcting_item)):
            raise Conflict('preserve the valid parts of the allocation item or bind its replacement to its decision: ' + subject)
        if correcting_item and not whole:
            change = {key: value for key, value in change.items() if key != 'correction'}
        output[subject] = edit_item(prior, change, allow_full=whole)
    return output


def _fields(source, names):
    return {name: source[name] for name in names if name in source}


def _conditions(result):
    return [_fields(item, ('id', 'criterion', 'review_at', 'observation_end')) for item in result['conditions']]


def public_inputs(market):
    if market['kind'] != 'DECISION' or market['scope'] != 'PUBLIC':
        raise Conflict('report writing needs the frozen public market judgement')
    result = market['payload']['result']
    public = _fields(result, ('conclusion', 'primary', 'alternatives', 'value', 'assessments', 'valid_until'))
    public.update(decision=binding(market), known_ms=market['known_ms'], valuations=result.get('valuations', {}),
                  conditions=_conditions(result),
                  evidence=[_fields(ref, ('type', 'kind', 'object_id', 'version', 'digest', 'product',
                                           'series', 'item_key', 'observation_key')) for ref in market['dependencies']])
    return public


def writing_inputs(market, portfolio, records, position_line):
    """Project public analysis once and expose only choices adopted by the matching portfolio."""
    public = public_inputs(market)
    allocation = {'portfolio': None, 'members': {}, 'cash_sources': [], 'unavailable_members': []}
    inputs = {'public': public, 'module_sources': MODULE_SOURCES, 'allocation': allocation}
    if portfolio is None or portfolio['payload']['market'] != public['decision']:
        allocation['status'] = 'NEEDS_ADOPTION'
        return inputs
    plan = portfolio['payload']
    allocation['portfolio'] = binding(portfolio)
    unavailable = set(plan.get('unavailable_members', {})) | (records.keys() - plan['decisions'].keys())
    for subject, ref in plan['decisions'].items():
        record = records.get(subject)
        if (not record or record['scope'] != 'PERSONAL' or binding(record) != ref
                or record['payload']['subject'] != subject):
            unavailable.add(subject)
            continue
        if subject == POSITION_SUBJECT:
            allocation['members'][subject] = {'direction': position_line}
            continue
        payload = record['payload']
        policy, choice = payload['policy'], payload['result']
        member = _fields(policy, ('kind', 'budget', 'currency'))
        member.update(_fields(choice, ('decision', 'amount', 'month', 'reason', 'risk', 'opportunity_cost',
                                       'amount_basis', 'gap', 'value_zone', 'valid_until')))
        member.update(source=ref, conditions=_conditions(choice), market_refs=list(payload['market_inputs']))
        previous = plan.get('member_reviews', {}).get(subject, {})
        if 'relation' in previous:
            member['previous_choice_review'] = _fields(previous, ('relation', 'reason'))
        allocation['members'][subject] = member
        if policy['kind'] == 'MONTHLY':
            allocation['cash_sources'].append(subject)
    allocation['unavailable_members'] = sorted(unavailable)
    allocation['status'] = 'PARTIAL' if unavailable else 'ADOPTED'
    return inputs
