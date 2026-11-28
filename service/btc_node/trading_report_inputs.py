"""Select adopted sources for each part of the investment report."""
from __future__ import annotations

from .codec import Conflict, millis
from .trading_decisions import binding, product_valuations
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
        correcting_item = explained and (correction or due or path in base.get('_excerpted_items', ()))
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


def previous_display(base):
    if not base:
        return None
    from .trading_report import displayed_modules
    presentation = base.get('presentation', {})
    display = {'modules': displayed_modules(base), 'lead': presentation.get('lead')}
    if presentation.get('additional_lead'):
        display['additional_lead'] = presentation['additional_lead']
    if base.get('execution_updates'):
        display['execution_updates'] = base['execution_updates']
    return display


def public_inputs(market):
    if market['kind'] != 'DECISION' or market['scope'] != 'PUBLIC':
        raise Conflict('report writing needs the frozen public market judgement')
    result = market['payload']['result']
    public = _fields(result, ('conclusion', 'primary', 'alternatives', 'value', 'assessments', 'valid_until'))
    if result.get('event_comparisons'):
        public['event_comparisons'] = result['event_comparisons']
    public.update(decision=binding(market), known_ms=market['known_ms'], valuations=product_valuations(result),
                  theses=result.get('theses', {}),
                  conditions=_conditions(result),
                  evidence=evidence_refs(market['dependencies']))
    return public


def evidence_refs(dependencies):
    return [_fields(ref, ('type', 'kind', 'object_id', 'version', 'digest', 'product',
                          'series', 'item_key', 'observation_key', 'sample')) for ref in dependencies]


def prior_position(db, destination, position, at):
    from .entities import Entities
    from . import trading_portfolio
    from .trading_delivery import position_action, previous_display
    shown = previous_display(db, destination=destination, decision_id=position['object_id'], at=at)
    if not shown:
        return None
    ref = shown['decision']
    objects = Entities(db)
    prior = objects.get(ref['kind'], ref['object_id'], version=ref['version'])
    view = _fields(shown, ('report_id', 'completed_at', 'decision', 'position_line', 'position_pending_review'))
    if not prior or binding(prior) != ref:
        return view
    result = prior['payload']['result']
    active = result.get('conditional_actions', {}).get('PRESENT', result)
    original = trading_portfolio._arrangement(objects, prior, at)
    previous_root = binding(original)
    view.update(action=position_action(prior), basis=active['reason'], arrangement=previous_root,
                value_zone=trading_portfolio._choice(prior).get('value_zone'))
    if prior['digest'] == position['digest']:
        return view
    current_root = trading_portfolio._arrangement(objects, position, at)
    if binding(current_root) == previous_root:
        return view
    adoption = trading_portfolio.first_member_review(objects, current_root, at)
    source = adoption.get('previous')
    if adoption.get('relation') == 'ADDITIONAL' and source:
        predecessor = objects.get(source['kind'], source['object_id'], version=source['version'])
        if (predecessor and binding(predecessor) == source and
                binding(trading_portfolio._arrangement(objects, predecessor, at)) == previous_root):
            view['addition_to_displayed_arrangement'] = True
            view['execution'] = trading_portfolio._followup(objects, original, at)['execution']
    return view


def position_explanation(record, previous, base, edit, member_review=None):
    """Keep current reasoning reusable. Relative wording belongs to this comparison only."""
    from .trading_delivery import position_action
    ref = binding(record)
    stored = (base or {}).get('position_explanation') or {}
    previous_id = previous and previous['report_id']
    same_decision = bool(previous and previous['decision'] == ref)
    comparison = None
    if edit is None:
        if not same_decision or stored.get('decision') != ref:
            raise Conflict('explain the adopted protective-short choice against its last confirmed display')
        reason = stored['reason']
    else:
        required = {'decision', 'previous_report_id', 'reason'}
        if not isinstance(edit, dict) or set(edit) not in (required, required | {'comparison'}):
            raise ValueError('the position item needs decision, previous_report_id, reason and optional comparison')
        if edit['decision'] != ref or edit['previous_report_id'] != previous_id:
            raise Conflict('review the current position decision and its last confirmed display')
        reason = edit['reason']
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('explain why this position choice is preferable at the current price')
        if 'comparison' in edit:
            comparison = edit['comparison']
            if not previous or not isinstance(comparison, str) or not comparison.strip():
                raise ValueError('compare with an actual previous display, using its bound decision')
    action = position_action(record)
    if action in ('REDUCE', 'INCREASE') and previous:
        reviewed = member_review or {}
        last_shown = previous['decision']
        root = reviewed.get('arrangement')
        if previous.get('arrangement') and root:
            same_arrangement = previous['arrangement'] == root
        else:
            same_arrangement = (reviewed.get('relation') in ('CONTINUE', 'READY', 'REPRICE') and
                                last_shown in (root, reviewed.get('previous')))
        verb = '减持' if action == 'REDUCE' else '增持'
        execution = reviewed.get('actual_execution')
        if same_decision or same_arrangement:
            from .trading_portfolio import _choice
            zone = _choice(record).get('value_zone')
            if 'value_zone' in previous and (zone is not None or previous['value_zone'] is not None):
                repriced = previous['value_zone'] != zone
            else:
                repriced = not same_decision and reviewed.get('relation') == 'REPRICE'
            if repriced and execution == 'PARTIALLY_EXECUTED':
                comparison = f'上次{verb}已确认部分执行，本轮重估的价格只适用于尚未执行的部分。'
            elif reviewed.get('user_adoption') == 'DECLINED' and execution != 'PARTIALLY_EXECUTED':
                comparison = f'你已明确未采纳上次{verb}安排，本轮仍是同一笔建议。'
            elif repriced:
                comparison = f'本轮仅重估上次{verb}的适用价格。若原安排仍有未执行部分，沿用原定规模。'
            elif execution == 'PARTIALLY_EXECUTED':
                comparison = f'上次{verb}安排已确认部分执行，本轮只承接尚未执行的部分。'
            elif execution == 'NOT_EXECUTED':
                comparison = f'上次{verb}安排已确认尚未执行，本轮沿用该安排。'
            elif 'arrangement' in reviewed:
                comparison = f'上次{verb}的执行情况尚未确认。若仍有未执行部分，本轮只沿用该部分。'
            else:
                comparison = f'沿用上次的{verb}建议，本轮没有新增{verb}安排。'
        elif (previous.get('action') == action and
              (previous.get('addition_to_displayed_arrangement') or
               (reviewed.get('relation') == 'ADDITIONAL' and reviewed.get('previous') == last_shown))):
            comparison = f'本轮建议在上次{verb}安排之外追加一笔。'
            if execution == 'PARTIALLY_EXECUTED':
                comparison = f'上次{verb}安排之外追加的一笔已确认部分执行，本轮只承接该笔尚未执行的部分。'
            if previous.get('execution', 'UNKNOWN') == 'UNKNOWN':
                comparison += f'上次{verb}的执行情况尚未确认。'
        elif comparison is None and previous.get('action') in (None, action):
            raise Conflict('explain whether this adjustment continues, reprices or adds to the previously displayed plan')
    summary = {'decision': ref, 'reason': reason.strip()}
    if comparison:
        summary['comparison'] = comparison.strip()
        if not same_decision:
            summary['previous_report_id'] = previous_id
    return summary


def writing_inputs(market, portfolio, records, position_line, previous_position=None, *, confirmations=None):
    """Project public analysis once and expose only choices adopted by the matching portfolio."""
    public = public_inputs(market)
    allocation = {'portfolio': None, 'members': {}, 'cash_sources': [], 'unavailable_members': []}
    inputs = {'public': public, 'module_sources': MODULE_SOURCES, 'allocation': allocation}
    if portfolio is None or portfolio['payload']['market'] != public['decision']:
        allocation['status'] = 'NEEDS_ADOPTION'
        return inputs
    plan = portfolio['payload']
    allocation['portfolio'] = binding(portfolio)
    allocation['scenarios'] = _fields(plan, ('primary', 'alternatives'))
    allocation['interactions'] = plan.get('interactions', [])
    unavailable = set(plan.get('unavailable_members', {})) | (records.keys() - plan['decisions'].keys())
    for subject, ref in plan['decisions'].items():
        record = records.get(subject)
        if (not record or record['scope'] != 'PERSONAL' or binding(record) != ref
                or record['payload']['subject'] != subject):
            unavailable.add(subject)
            continue
        payload = record['payload']
        policy, choice = payload['policy'], payload['result']
        member = _fields(policy, ('kind', 'budget', 'currency'))
        member.update(_fields(choice, ('decision', 'amount', 'month', 'reason', 'risk', 'opportunity_cost',
                                       'comparisons', 'runner_up_amount', 'amount_basis', 'gap', 'value_zone', 'valid_until', 'plan_steps')))
        member.update(source=ref, conditions=_conditions(choice), market_refs=list(payload['market_inputs']))
        previous = plan.get('member_reviews', {}).get(subject, {})
        if previous.get('input_review'):
            member['input_review'] = previous['input_review']
        if 'relation' in previous:
            member['previous_choice_review'] = _fields(previous, ('previous', 'relation', 'reason'))
        if 'arrangement' in previous:
            member['arrangement'] = previous['arrangement']
            member['user_adoption'] = previous.get('user_adoption', 'UNKNOWN')
            member['actual_execution'] = previous.get('actual_execution', 'UNKNOWN')
            member.update(_fields(previous, ('accepted_arrangement', 'last_user_feedback', 'last_retrospective', 'prior_retrospective')))
        if (confirmations or {}).get(subject):
            member['confirmations'] = confirmations[subject]
        if subject == POSITION_SUBJECT:
            member.update(direction=position_line, previous_display=previous_position,
                          position_status=policy.get('position_status', 'UNKNOWN'))
            member.update(_fields(choice, ('direction_comparison', 'conditional_actions')))
            member['fact_refs'] = payload.get('fact_refs', [])
        allocation['members'][subject] = member
        if policy['kind'] == 'MONTHLY':
            allocation['cash_sources'].append(subject)
    allocation['unavailable_members'] = sorted(unavailable)
    allocation['status'] = 'PARTIAL' if unavailable else 'ADOPTED'
    return inputs
