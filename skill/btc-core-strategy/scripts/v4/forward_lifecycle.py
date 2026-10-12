"""Prospective claim lineage. Conditions are authored; facts are source-bound.

This is a domain-neutral contract: no asset, bar count, price level, flow gate,
trade decision or prose score. The business adapter supplies verified fact views.
An immutable claim remains owned by its existing market result/DecisionSnapshot.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from itertools import product
import re

from .common import create, digest, exact, instant, now, pointer, read, require, text

KINDS = ('FACTS', 'VALIDATED_PREDECESSOR', 'FACT_CORRECTION', 'INDEPENDENT_NEW_FACTS', 'RISK_RESPONSE')
REVIEW_KINDS = ('FORWARD_OBSERVATION', 'POSTHOC', 'ENGINEERING', 'BACKTEST', 'FROZEN_REPLAY')
VERDICTS = ('SUPPORTED', 'PARTIAL', 'FALSIFIED', 'UNKNOWN', 'POSTHOC_ONLY')


def scope(value):
    require(isinstance(value, dict) and value, 'claim needs an explicit evidence scope')
    for key, item in value.items():
        text(key, 'scope dimension')
        require(isinstance(item, (str, list)), 'scope dimension must name actual values')
        values = [item] if isinstance(item, str) else item
        require(values and all(isinstance(v, str) and v.strip() for v in values), 'empty scope dimension')
        require(len(set(values)) == len(values), 'duplicate scope member')


def covers(available, requested):
    """A supported subset never authorizes a broader venue/product/mechanism."""
    scope(available); scope(requested)
    def members(v): return {v} if isinstance(v, str) else set(v)
    return all(k in available and members(v) <= members(available[k]) for k, v in requested.items())


def covers_many(available, requested):
    scope(requested)
    keys = list(requested)
    values = [[requested[k]] if isinstance(requested[k], str) else requested[k] for k in keys]
    return all(any(covers(s, dict(zip(keys, assignment))) for s in available)
               for assignment in product(*values))


def predicate(value, resolve=None):
    require(isinstance(value, dict), 'test condition must be an expression')
    if 'all' in value or 'any' in value:
        key = 'all' if 'all' in value else 'any'
        exact(value, key, 'condition group')
        require(isinstance(value[key], list) and value[key], 'empty test condition')
        for child in value[key]: predicate(child, resolve)
        return
    exact(value, 'left op right', 'observable comparison')
    require(value['op'] in ('EQ', 'NE', 'GT', 'GE', 'LT', 'LE'), 'unknown comparison operator')
    for operand in (value['left'], value['right']):
        require(isinstance(operand, dict), 'condition operand must be source-bound')
        if 'sample' in operand:
            exact(operand, 'sample path', 'later observation operand')
            text(operand['sample'], 'observation role'); text(operand['path'], 'observation value pointer')
        else:
            exact(operand, 'anchor', 'original evidence operand')
            if resolve is not None: resolve(operand['anchor'])


def evaluate(condition, samples, resolve):
    """Check an authored test against actual referenced values, not its label."""
    if 'all' in condition or 'any' in condition:
        key = 'all' if 'all' in condition else 'any'
        values = [evaluate(c, samples, resolve) for c in condition[key]]
        if key == 'all': return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    def operand(item):
        if 'anchor' in item: return resolve(item['anchor'])
        if item['sample'] not in samples: return None
        if item['path'] in samples[item['sample']].get('unavailable_fields', set()): return None
        return pointer(samples[item['sample']]['value'], item['path'])
    a = operand(condition['left'])
    if a is None: return None
    b = operand(condition['right'])
    if b is None: return None
    op = condition['op']
    numeric = any('sample' in item and item['path'] in samples.get(item['sample'], {}).get('numeric_fields', [])
                  for item in (condition['left'], condition['right']))
    try:
        a, b = Decimal(str(a)), Decimal(str(b))
    except (InvalidOperation, ValueError, TypeError):
        if numeric: return None
        require(op in ('EQ','NE'), 'ordered test requires actual numeric observations')
        return (a == b) if op == 'EQ' else (a != b)
    require(a.is_finite() and b.is_finite(), 'nonfinite test observation')
    return {'EQ':a == b, 'NE':a != b, 'GT': a > b, 'GE': a >= b, 'LT': a < b, 'LE': a <= b}[op]


def sample_roles(condition):
    if 'all' in condition or 'any' in condition:
        return set().union(*(sample_roles(c) for c in condition.get('all', condition.get('any'))))
    return {v['sample'] for v in (condition['left'], condition['right']) if 'sample' in v}


def sample_fields(condition):
    if 'all' in condition or 'any' in condition:
        return set().union(*(sample_fields(c) for c in condition.get('all', condition.get('any'))))
    return {(v['sample'], v['path']) for v in (condition['left'], condition['right']) if 'sample' in v}


RELATION_KINDS=('AFTER','DISTINCT_OBSERVATION','NON_OVERLAPPING_PERIOD')


def relation_contract(value):
    if 'observation_relations' not in value:return None
    relations=value['observation_relations']
    require(isinstance(relations,list),'observation relations must be explicitly declared')
    parts={part['id']:part for part in value['expectations']};seen=set()
    fields={key:sample_fields(part['support'])|sample_fields(part['falsification']) for key,part in parts.items()}
    original_fields=set().union(*fields.values())
    for item in relations:
        exact(item,'id kind expectation_ids left right','ex-ante observation relation')
        text(item['id'],'observation relation identity')
        require(item['id'] not in seen,'duplicate observation relation');seen.add(item['id'])
        require(item['kind'] in RELATION_KINDS,'unknown observation relation')
        require(isinstance(item['expectation_ids'],list) and item['expectation_ids'] and
                len(set(item['expectation_ids']))==len(item['expectation_ids']) and
                set(item['expectation_ids'])<=set(parts),'relation must bind its actual expectation components')
        for side in ('left','right'):
            exact(item[side],'sample path','relation observation operand')
            text(item[side]['sample'],'relation sample role');text(item[side]['path'],'relation observed field')
        require(item['left']!=item['right'],'a relation requires two declared observation operands')
        operands={(item[side]['sample'],item[side]['path']) for side in ('left','right')}
        require(operands<=original_fields,'relation operand must belong to an original registered component field')
        require(all(operands&fields[key] for key in item['expectation_ids']),
                'every affected component must participate in its original observation relation')
    return relations


def relation_result(relation,samples,floor,evaluated_at,claim):
    """The actual adapter supplies clocks and economic identity, never a role label."""
    operands=[];affected=set()
    def matches(part,fact,field):
        actual=fact.get('field_scopes',{}).get(field)
        if actual is None:return False
        required=fact.get('qualifiers',{}).get(field,list(actual))
        return set(required)<=part['scope'].keys() and covers(part['scope'],
            {k:actual[k] for k in actual if k in required or k in part['scope']})
    for side in ('left','right'):
        operand=relation[side];fact=samples.get(operand['sample']);field=operand['path']
        if fact is None or fact.get('eligibility_error') or fact.get('field_errors',{}).get(field):return None
        clock=fact.get('field_times',{}).get(field)
        if field not in fact.get('field_scopes',{}) or clock is None:return None
        if not floor<instant(clock)<=instant(evaluated_at):return None
        start=fact.get('field_period_starts',{}).get(field)
        if start is not None and instant(start)<floor:return None
        owners=[part for part in claim['expectations'] if (operand['sample'],field) in
                (sample_fields(part['support'])|sample_fields(part['falsification'])) and matches(part,fact,field)]
        if not owners:return None
        affected.update(part['id'] for part in owners if part['id'] in relation['expectation_ids'])
        operands.append((fact,field,instant(clock)))
    if affected!=set(relation['expectation_ids']):return None
    if relation['kind']=='AFTER':return operands[0][2]>operands[1][2]
    observations=[fact.get('field_observations',{}).get(field) for fact,field,_ in operands]
    if any(item is None for item in observations):return None
    left,right=observations
    a,b=left['economic_period'],right['economic_period']
    if relation['kind']=='NON_OVERLAPPING_PERIOD':
        return instant(a['start'])>instant(b['end']) or instant(b['start'])>instant(a['end'])
    # Retrievals, raw hashes, ref aliases and source publisher changes do not
    # create a second economic event. Missing qualifiers are no distinction.
    if (instant(a['start']),instant(a['end']))!=(instant(b['start']),instant(b['end'])):return True
    common=set(left['scope'])&set(right['scope'])
    def members(v):return {v} if isinstance(v,str) else set(v)
    return any(not(members(left['scope'][key])&members(right['scope'][key])) for key in common)


def claim(value, cutoff, resolve):
    exact(value, 'id formed_at data_cutoff scope basis_kind basis_refs scale expectations'+
          (' observation_relations' if 'observation_relations' in value else ''), 'prospective claim')
    text(value['id'], 'stable claim identity')
    require(value['basis_kind'] in KINDS, 'invalid claim basis')
    require(instant(value['data_cutoff']) <= instant(cutoff), 'claim data cutoff exceeds frozen inputs')
    require(instant(value['data_cutoff']) <= instant(value['formed_at']) <= instant(now()), 'claim cannot backdate formation before its available inputs')
    scope(value['scope'])
    require(isinstance(value['basis_refs'], list) and value['basis_refs'], 'initial or independent claim needs real basis evidence')
    for ref in value['basis_refs']: resolve(ref)
    scale = value['scale']
    exact(scale, 'process end end_ref', 'mechanism observation scale')
    text(scale['process'], 'testable mechanism and next observable progress')
    if scale['end'] is None:
        require(scale['end_ref'] is None, 'process scale cannot carry a fabricated calendar anchor')
    else:
        require(instant(scale['end']) > instant(value['formed_at']), 'new expectation cannot inherit an expired or prior start')
        require(scale['end_ref'] is not None and instant(resolve(scale['end_ref'])) == instant(scale['end']),
                'dated expectation needs its own actual calendar evidence, not an inherited deadline')
    require(isinstance(value['expectations'], list) and value['expectations'], 'claim needs an ex-ante testable expectation')
    ids = set()
    for part in value['expectations']:
        exact(part, 'id mechanism scope support falsification', 'expectation component')
        text(part['id'], 'expectation identity'); text(part['mechanism'], 'specific mechanism response')
        require(part['id'] not in ids, 'duplicate expectation component'); ids.add(part['id'])
        require(covers(value['scope'], part['scope']), 'expectation exceeds its claim scope')
        predicate(part['support'], resolve); predicate(part['falsification'], resolve)
        require(part['support'] != part['falsification'], 'support and falsification cannot be identical')
    relation_contract(value)


def registration(root, identity):
    require(isinstance(identity, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', identity), 'invalid predecessor claim identity')
    if root is None: return None
    path = Path(root) / 'market-lifecycle' / 'claims' / (identity[7:] + '.json')
    if not path.exists(): return None
    value = read(path)
    exact(value, 'claim_hash registered_at claim owner', 'first prospective registration')
    require(value['claim_hash'] == identity == digest(value['claim']), 'claim registration changed')
    instant(value['registered_at'])
    owner = value['owner']; exact(owner, 'path hash time_pointer', 'original public freeze owner')
    require(isinstance(owner['hash'], str) and re.fullmatch(r'sha256:[a-f0-9]{64}', owner['hash']), 'invalid original freeze hash')
    location = Path('market-lifecycle') / 'freezes' / (owner['hash'][7:] + '.json')
    require(owner['path'] == str(location), 'original freeze owner escapes its archive identity')
    source = Path(root) / location
    require(source.resolve().parent == (Path(root)/'market-lifecycle/freezes').resolve(), 'original freeze archive escapes its store')
    require(source.is_file() and digest(source.read_bytes()) == owner['hash'], 'first registration lost or changed its original public freeze')
    original = read(source)
    require(instant(pointer(original, owner['time_pointer'])) == instant(value['registered_at']), 'first registration clock differs from its immutable freeze')
    require(instant(value['registered_at'])>=instant(value['claim']['formed_at']),'first registration predates claim formation')
    def owns(item):
        if isinstance(item, dict): return item == value['claim'] or any(owns(v) for v in item.values())
        return isinstance(item, list) and any(owns(v) for v in item)
    require(owns(original), 'original freeze does not own the registered claim')
    return value


def register(root, market_assessment, owner_path=None, time_pointer=None):
    """Only a first real public freeze/completion registers conditions; no replay upgrade."""
    for market in (market_assessment or {}).get('markets', []):
        for horizon in market.get('horizons', []):
            life = (horizon.get('forward') or {}).get('lifecycle')
            if not life: continue
            c = life['claim']; identity = digest(c)
            if registration(root, identity) is not None: continue
            require(owner_path is not None and time_pointer is not None, 'first registration requires the actual immutable public freeze owner')
            path = Path(owner_path).resolve(); require(path.is_file(), 'public freeze must exist before registration')
            body = path.read_bytes(); original = read(path); clock = pointer(original, time_pointer)
            require(instant(clock)>=instant(c['formed_at']),'first registration predates claim formation')
            # Archive the original freeze bytes, without altering its content,
            # identity or clock. Run folders may be temporary; root restores
            # retain this proof alongside its index and existing state.
            archive = Path(root) / 'market-lifecycle' / 'freezes' / (digest(body)[7:] + '.json')
            if archive.exists(): require(archive.read_bytes() == body, 'public freeze archive collision')
            else: create(archive, body)
            path = archive.resolve()
            require(instant(clock) <= instant(now()), 'public freeze clock is in the future')
            try: location = str(path.relative_to(Path(root).resolve()))
            except ValueError: location = str(path)
            record = {'claim_hash': identity, 'registered_at': clock, 'claim': deepcopy(c),
                      'owner': {'path':location, 'hash':digest(path.read_bytes()), 'time_pointer':time_pointer}}
            create(Path(root) / 'market-lifecycle' / 'claims' / (identity[7:] + '.json'), record)
            registration(root, identity)



def carried_nonprospective(review, previous, root, old_hash):
    """Retain exact archived history; it never establishes a new predecessor."""
    if review['kind'] == 'FORWARD_OBSERVATION' or root is None or old_hash is None:
        return False
    if review not in previous.get('reviews', []):
        return False
    record = registration(root, old_hash)
    if record is None:
        return False
    original = read(Path(root) / record['owner']['path'])
    def owns_history(item):
        if isinstance(item, dict):
            if item.get('claim') == record['claim'] and review in item.get('reviews', []):
                return True
            return any(owns_history(v) for v in item.values())
        return isinstance(item, list) and any(owns_history(v) for v in item)
    return owns_history(original)


def validate(value, cutoff, resolve, prior=None, fact_view=None, root=None):
    exact(value, 'claim reviews dependencies', 'forward lifecycle')
    current = value['claim']; claim(current, cutoff, resolve)
    previous = ((prior or {}).get('forward') or {}).get('lifecycle')
    old = previous.get('claim') if previous else None
    old_hash = digest(old) if old else None
    unchanged = old is not None and current['id'] == old['id']
    if unchanged:
        require(current == old, 'original claim and ex-ante standards are immutable; create a related successor')
    else:
        require(instant(current['data_cutoff']) == instant(cutoff), 'new expectation must bind its own current frozen inputs')
    require(isinstance(value['reviews'], list) and isinstance(value['dependencies'], list), 'claim review and dependency lists required')
    grants = {}; results = []; completed_reviews = []; falsified_claims = set(); falsified_components = set(); relation_checks = []
    for review in value['reviews']:
        exact(review, 'prior_claim_hash kind evaluated_at samples outcomes', 'specific predecessor review')
        require(review['kind'] in REVIEW_KINDS, 'unknown verification method')
        require(instant(review['evaluated_at']) <= instant(cutoff), 'review exceeds available evidence')
        require(unchanged or instant(review['evaluated_at']) <= instant(current['formed_at']), 'review was unavailable when successor formed')
        target = registration(root, review['prior_claim_hash']) if review['prior_claim_hash'] else None
        require(review['prior_claim_hash'] == old_hash or target is not None or
                carried_nonprospective(review, previous, root, old_hash),
                'review does not identify an actual predecessor claim')
        target_claim = target['claim'] if target else old
        require(isinstance(review['outcomes'], list) and review['outcomes'], 'review must identify concrete mechanisms')
        require(isinstance(review['samples'], list), 'later factual observations required')
        registered = target
        forward = review['kind'] == 'FORWARD_OBSERVATION'
        if forward:
            require(target_claim is not None and registered is not None, 'unknown formation or missing ex-ante registration permits only posthoc review')
        floor = max(instant(target_claim['formed_at']), instant(registered['registered_at'])) if registered else None
        samples = {}
        for sample in review['samples']:
            exact(sample, 'id ref time_pointer scope', 'later fact reference')
            text(sample['id'], 'sample role'); scope(sample['scope'])
            require(sample['id'] not in samples, 'duplicate observation role')
            require(fact_view is not None, 'prospective verification needs the actual business fact adapter')
            fact = fact_view(sample['ref'], sample['time_pointer'])
            fact = dict(fact)
            fact['eligibility_error'] = None if fact['kind'] == 'EXTERNAL_OBSERVATION' else 'process, forecast, simulation or old judgement is not a later fact'
            require(covers(fact['scope'], sample['scope']), 'single-source observation cannot expand its evidence scope')
            if instant(fact['known_at']) > instant(review['evaluated_at']):
                fact['eligibility_error'] = 'observation was not known at verification'
            if forward and not floor < instant(fact['observed_at']) <= instant(review['evaluated_at']):
                fact['eligibility_error'] = 'late-read old data, same-sample replay or future observation cannot validate a forecast'
            samples[sample['id']] = fact
        old_parts = {p['id']: p for p in (target_claim or {}).get('expectations', [])}
        declared_relations=relation_contract(target_claim) if target_claim is not None else None
        relation_values={}
        if forward and declared_relations is not None:
            for relation in declared_relations:
                checked=relation_result(relation,samples,floor,review['evaluated_at'],target_claim)
                relation_values[relation['id']]=checked
                relation_checks.append({'prior_claim_hash':review['prior_claim_hash'],'review_hash':digest(review),
                    'relation_id':relation['id'],'kind':relation['kind'],
                    'status':'SUPPORTED' if checked is True else 'NOT_MET' if checked is False else 'UNKNOWN'})
        seen = set()
        for outcome in review['outcomes']:
            exact(outcome, 'expectation_id verdict supported_scope explanation', 'mechanism verdict')
            text(outcome['explanation'], 'actual verification finding')
            require(outcome['verdict'] in VERDICTS, 'invalid mechanism verdict')
            part_id = outcome['expectation_id']
            require(part_id not in seen, 'duplicate mechanism verdict'); seen.add(part_id)
            if not forward:
                require(outcome['verdict'] in ('UNKNOWN', 'POSTHOC_ONLY') and not outcome['supported_scope'],
                        'engineering, backtest and retrospective review cannot grant prospective support')
                results.append(deepcopy(outcome)); continue
            require(part_id in old_parts, 'review targets an unregistered mechanism')
            part = old_parts[part_id]
            used = sample_fields(part['support']) | sample_fields(part['falsification'])
            component_samples = {k:dict(v, unavailable_fields=set()) for k,v in samples.items()}
            component_errors = {}
            for role, field in used:
                if role not in samples: continue
                fact = samples[role]
                error = fact.get('eligibility_error') or fact.get('field_errors', {}).get(field)
                if field not in fact.get('field_scopes', {}):
                    error = error or 'unclassified observation field cannot verify a mechanism'
                observed_scope = fact.get('field_scopes', {}).get(field)
                if observed_scope is not None:
                    required = fact.get('qualifiers', {}).get(field, list(observed_scope))
                    if not set(required) <= part['scope'].keys() or not covers(
                            part['scope'], {k: observed_scope[k] for k in observed_scope
                                           if k in required or k in part['scope']}):
                        error = error or 'observation field is outside the original mechanism scope'
                period_start = fact.get('field_period_starts', {}).get(field)
                if period_start is not None and instant(period_start) < floor:
                    error = error or 'economic observation period includes pre-formation events'
                clock = fact.get('field_times', {}).get(field)
                if clock is None or not floor < instant(clock) <= instant(review['evaluated_at']):
                    error = error or 'original test field predates formation or lacks its own later observation clock'
                if error:
                    component_errors[(role,field)] = error
                    component_samples[role]['unavailable_fields'].add(field)
            support = evaluate(part['support'], component_samples, resolve)
            failure = evaluate(part['falsification'], component_samples, resolve)
            if support is True and failure is True:
                require(outcome['verdict'] == 'UNKNOWN' and not outcome['supported_scope'],
                        'contradictory original tests remain unknown, not a supported mainline')
            elif failure is True:
                falsified_claims.add(review['prior_claim_hash'])
                falsified_components.add((review['prior_claim_hash'],part_id))
                require(outcome['verdict'] == 'FALSIFIED' and not outcome['supported_scope'], 'falsified expectation must be corrected or withdrawn before continuation')
            elif outcome['verdict'] in ('SUPPORTED', 'PARTIAL'):
                required_relations=[r['id'] for r in (declared_relations or []) if part_id in r['expectation_ids']]
                require(all(relation_values.get(r) is True for r in required_relations),
                        'original observation relation is not supported by its actual source events')
                require(support is True and failure is False, next(iter(component_errors.values()), 'support label is not supported by its original actual-value test'))
                require(part['scope'].keys() == outcome['supported_scope'].keys(), 'partial support cannot drop original scope qualifiers')
                require(covers(part['scope'], outcome['supported_scope']), 'partial support exceeds original mechanism scope')
                observed_scopes = []
                used = sample_fields(part['support']) | sample_fields(part['falsification'])
                for role, field in used:
                    require(role in samples, 'verification omits an original test observation')
                    if field in component_samples[role]['unavailable_fields']: continue
                    fact = samples[role]
                    require(field in fact.get('field_scopes', {}), 'unclassified observation field cannot verify a mechanism')
                    observed = fact['field_scopes'][field]
                    required = fact.get('qualifiers', {}).get(field, list(observed))
                    require(set(required) <= outcome['supported_scope'].keys(), 'verification cannot omit actual evidence qualifiers')
                    observed_scopes.append(observed)
                require(covers_many(observed_scopes, outcome['supported_scope']),
                        'supported mechanism scope exceeds its actual observations')
                grants[(review['prior_claim_hash'],part_id,digest(review))]={'scope':outcome['supported_scope'],
                    'support_kind':'DECLARED_RELATIONS' if required_relations else 'VALUE_ONLY',
                    'relation_ids':required_relations}
            else:
                require(outcome['verdict'] == 'UNKNOWN' and not outcome['supported_scope'], 'unverified response cannot become a supported mechanism')
            results.append(deepcopy(outcome))
        if review['prior_claim_hash'] == old_hash:
            completed_reviews.append(forward and declared_relations is not None and seen == set(old_parts) and
                all(v['verdict'] == 'SUPPORTED' and v['supported_scope'] == old_parts[v['expectation_id']]['scope'] for v in review['outcomes']) and
                covers_many([v['supported_scope'] for v in review['outcomes']], target_claim['scope']))
    # Historical value checks remain in results. Once the original component
    # is actually falsified before this successor, no older review hash can
    # grant it again. Evaluate all reviews before filtering, regardless of order.
    grants = {key:grant for key,grant in grants.items() if key[:2] not in falsified_components}
    dependency_scopes = []; dependency_support = []
    for dependency in value['dependencies']:
        exact(dependency,'claim_hash expectation_id review_hash scope'+
              (' support_kind relation_ids' if 'support_kind' in dependency or 'relation_ids' in dependency else ''),
              'successor dependency')
        key=(dependency['claim_hash'],dependency['expectation_id'],dependency['review_hash'])
        require(key in grants,'successor relies on an unknown, falsified or unverified predecessor mechanism')
        grant=grants[key];granted_scope=grant['scope']
        require(granted_scope.keys()==dependency['scope'].keys(),'dependency cannot drop verified scope qualifiers')
        require(covers(granted_scope,dependency['scope']),'partial support cannot authorize a broader successor premise')
        kind=dependency.get('support_kind','VALUE_ONLY');relations=dependency.get('relation_ids',[])
        require(kind in ('VALUE_ONLY','DECLARED_RELATIONS') and isinstance(relations,list) and
                len(set(relations))==len(relations),'invalid successor support class')
        if kind=='DECLARED_RELATIONS':
            require(grant['support_kind']=='DECLARED_RELATIONS' and set(relations)==set(grant['relation_ids']),
                    'successor relation support must bind every actual verified ex-ante relation')
        else:
            require(not relations,'value-only evidence cannot grant observation relationships')
            require(dependency['scope']==current['scope'],'value-only successor cannot widen or combine its observed scope')
            require(not current.get('observation_relations'),
                    'value-only predecessor cannot establish a successor relationship; form an independent fact-based hypothesis')
        dependency_scopes.append(dependency['scope'])
        dependency_support.append({'claim_hash':key[0],'expectation_id':key[1],'review_hash':key[2],
            'support_kind':kind,'relation_ids':relations,'scope':deepcopy(dependency['scope'])})
    if dependency_scopes:
        require(all(s.keys()==current['scope'].keys() for s in dependency_scopes),'successor cannot omit validated qualifiers')
        require(covers_many(dependency_scopes,current['scope']),'successor claim exceeds the validated scope it depends on')
    require(bool(value['dependencies']) == (current['basis_kind'] == 'VALIDATED_PREDECESSOR'),
            'validated derivation must cite actual supported mechanisms; corrections and independent facts remain independent')
    require(digest(current) not in falsified_claims, 'falsified original claim must be withdrawn or replaced by a corrected claim')
    return {'claim_hash': digest(current), 'verification': results, 'granted_dependencies': len(value['dependencies']),
            'observation_relation_checks':relation_checks,'dependency_support':dependency_support,
            'component_support':[{'claim_hash':key[0],'expectation_id':key[1],'review_hash':key[2],**deepcopy(grant)} for key,grant in grants.items()],
            'falsified_prior': old_hash is not None and old_hash in falsified_claims,
            'complete_prior_support': old_hash is not None and bool(completed_reviews) and all(completed_reviews)}


def public_feedback(root, market_assessment):
    claims = []
    for market in (market_assessment or {}).get('markets', []):
        for horizon in market.get('horizons', []):
            life = (horizon.get('forward') or {}).get('lifecycle')
            if not life:
                claims.append({'market': market['id'], 'horizon': horizon['id'],
                    'status': 'POSTHOC_ONLY', 'reason': 'No originally registered formation and test conditions.'})
                continue
            c = life['claim']; record = registration(root, digest(c))
            claims.append({'market': market['id'], 'horizon': horizon['id'], 'claim': deepcopy(c),
                'first_registration': record['registered_at'] if record else None,
                'status': 'TRACKING' if record else 'POSTHOC_ONLY',
                'prior_reviews': deepcopy(life['reviews']), 'history_is_not_external_fact': True,
                'observation_relation_status':'DECLARED' if 'observation_relations' in c else 'LEGACY_VALUE_ONLY_NO_COMPLETE_SUPPORT'})
    return {'claims': claims, 'ledger_is_not_market_verification': True,
            'new_fact_research_corrections_and_risk_response_allowed': True}
