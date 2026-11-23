"""Select adopted sources for each part of the investment report."""
from __future__ import annotations

from .codec import Conflict
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


def _fields(source, names):
    return {name: source[name] for name in names if name in source}


def _conditions(result):
    return [_fields(item, ('id', 'criterion', 'review_at', 'observation_end')) for item in result['conditions']]


def writing_inputs(market, portfolio, records, position_line):
    """Project public analysis once and expose only choices adopted by the matching portfolio."""
    if market['kind'] != 'DECISION' or market['scope'] != 'PUBLIC':
        raise Conflict('report writing needs the frozen public market judgement')
    market_ref = binding(market)
    result = market['payload']['result']
    public = _fields(result, ('conclusion', 'primary', 'alternatives', 'value', 'assessments', 'valid_until'))
    public.update(decision=market_ref, known_ms=market['known_ms'], valuations=result.get('valuations', {}),
                  conditions=_conditions(result),
                  evidence=[_fields(ref, ('type', 'kind', 'object_id', 'version', 'digest', 'product',
                                           'series', 'item_key', 'observation_key')) for ref in market['dependencies']])
    allocation = {'portfolio': None, 'members': {}, 'cash_sources': [], 'unavailable_members': []}
    inputs = {'public': public, 'module_sources': MODULE_SOURCES, 'allocation': allocation}
    if portfolio is None or portfolio['payload']['market'] != market_ref:
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
