"""Normal trading workflow. The node commits results, the host runs the model and transport."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from . import deep_gate, fact_views, trading_archive, trading_policy, trading_portfolio, trading_public_report, trading_report
from .codec import Conflict, encode, fingerprint, identity, millis, stamp
from .entities import Entities

MARKET = 'allocation'
SET_ENTRY_REFERENCE = 'trading.set_entry_reference'
SERVICE_SOURCES = ('api.py', 'contracts.py', 'execution.py', 'mcp.py', 'trading.py', 'trading_decisions.py',
                   'trading_delivery.py', 'trading_policy.py', 'trading_archive.py', 'trading_portfolio.py',
                   'trading_report.py', 'trading_report_inputs.py', 'trading_public_report.py', 'trading_risk_seed.py')
READS = {'trading.public', 'trading.personal', 'trading.personal_ready', 'trading.feedback', 'trading.source',
         'trading.research_archive', 'trading.research_result', 'trading.preview_report'}
WRITES = {'trading.open', 'trading.evidence', 'trading.market', 'trading.personal_decision', 'trading.portfolio_decision',
          'trading.review', 'trading.research', 'trading.report', 'trading.begin_delivery',
          'trading.record_delivery', 'trading.assess_feedback', 'trading.inherit_feedback',
          'trading.policy', 'trading.seed_policy', SET_ENTRY_REFERENCE}


def plain(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + ' is required')
    return value


def run_record(db, run_ref):
    key = fingerprint(plain(run_ref, 'original run identity').encode())
    root = Path(db.settings['release_root']).resolve()
    record = json.loads((root.parent / 'runs' / (key + '.json')).read_bytes())
    commit = record.get('commit', '')
    if (record.get('run_id') != run_ref or record.get('status') != 'ACTIVE' or len(commit) != 40
            or any(c not in '0123456789abcdef' for c in commit)):
        raise Conflict('run is not active on the original installed release')
    release = root / commit
    selected = (root.parent / 'current').resolve()
    info = json.loads((release / 'RELEASE.json').read_bytes())
    if (info['commit'] != commit or info.get('data_schema') != 'BTC_NODE_DIRECT' or selected != release):
        raise Conflict('installed release does not match the run')
    run = {'ref': run_ref, 'known_at': stamp(), 'method_version': commit}
    return run, release


def destination(db):
    from . import trading_delivery

    source = db.settings.get('host_node_config')
    try:
        config = json.loads(Path(source).read_bytes()) if isinstance(source, str) and source else None
        if not isinstance(config, dict) or config.get('schema') != 'BTC_NODE_CONFIG_V1':
            raise ValueError('host node configuration is unavailable')
        return trading_delivery.validate_destination(config.get('report_destination'))
    except (OSError, ValueError) as error:
        raise Conflict('the authorized reporting destination is unavailable from the host node configuration') from error


def material_key(run_ref):
    return 'trading.material.' + identity(run_ref)


def material(db, run):
    value = db.metadata(material_key(run['ref']))
    if not value or value['release'] != run['method_version']:
        raise Conflict('prepare the same run with its original public evidence first')
    return value


def reference(record):
    return {key: record[key] for key in ('kind', 'object_id', 'version', 'digest')}


def open_run(db, run, release, *, market):
    identity_key = material_key(run['ref'])
    existing = db.metadata(identity_key)
    if existing:
        raise Conflict('use the public read operation to resume an already prepared run')
    from .execution import load_methods
    load_methods(release)
    from market_observation import validate_evidence
    validate_evidence(market)
    if millis(market['cutoff']) > millis(run['known_at']):
        raise ValueError('market cutoff is after the actual knowledge time')
    if any(millis(receipt['retrieved_at']) > millis(run['known_at'])
           for receipt in market['retrievals'].values()):
        raise ValueError('market retrieval occurs after the actual knowledge time')
    market_digest = db.put_blob(encode(market))
    db.metadata('trading.public.' + market_digest,
                {'digest': market_digest, 'observed_at': market['cutoff'], 'known_at': run['known_at'],
                 'provider': 'ANONYMOUS_MARKET_COLLECTION'}, write=True)
    value = {'release': run['method_version'], 'market': market_digest,
             'public_known_at': run['known_at'], 'public_cutoff': market['cutoff']}
    from .reader_store import observations
    from v4.collector import capture
    try:
        auxiliary = capture(None, items=observations(db.settings))
    except (OSError, ValueError, Conflict):
        auxiliary = {'status': 'UNAVAILABLE', 'reason': 'reader source unavailable'}
    auxiliary_digest = db.put_blob(encode(auxiliary))
    value['auxiliary'] = auxiliary_digest
    db.metadata('trading.public.' + auxiliary_digest, {'digest': auxiliary_digest, 'provider': 'AUXILIARY_READER',
                'observed_at': None, 'known_at': run['known_at']}, write=True)
    db.metadata(identity_key, value, write=True)
    trading_archive.capture(db, release=release, as_of=run['known_at'])
    return {'prepared': True, **value}


def public_context(db, run):
    from . import trading_decisions as decisions
    snapshot = material(db, run)
    market = db.value(snapshot['market'])
    current = decisions.current_market(db, MARKET)
    review = decisions.decision_state(db, current, at=run['known_at']) if current else None
    archive = trading_archive.context(db, scope='PUBLIC', at=run['known_at'])
    targets = archive.pop('review_targets', [])
    if current:
        targets.extend({'subject': current['payload']['subject'], **condition} for condition in review['conditions'])
    return {'as_of': snapshot['public_known_at'], 'market_cutoff': snapshot['public_cutoff'],
            'market_evidence': {'type': 'BLOB', 'digest': snapshot['market']},
            'windows': market['windows'], 'gaps': market['gaps'], 'retrievals': market['retrievals'],
            'limitations': market['limitations'], 'current_market': current,
            'auxiliary': db.value(snapshot['auxiliary']), 'auxiliary_evidence': {'type': 'BLOB', 'digest': snapshot['auxiliary']},
            'review': review, 'review_targets': targets, 'archive': archive,
            'research': research_records(db, 'PUBLIC')}


def register_evidence(db, run, *, source, url, observed_at, retrieved_at, content):
    """Preserve bytes obtained via the actual authorized public retrieval, with its clock."""
    material(db, run)
    parsed = urlsplit(plain(url, 'public source URL'))
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or any(key.lower() in {'key', 'api_key', 'apikey', 'token', 'signature'}
                                      for key, _ in parse_qsl(parsed.query))):
        raise ValueError('evidence URL must be a public HTTPS source without credentials')
    if not millis(observed_at) <= millis(retrieved_at) <= millis(run['known_at']):
        raise ValueError('evidence timing must preserve observation, retrieval and recording order')
    if len(plain(content, 'observed source content').encode()) > 2_000_000:
        raise ValueError('split source evidence exceeding 2 MB')
    evidence = {'source': plain(source, 'source name'), 'url': url, 'observed_at': stamp(observed_at),
                'known_at': stamp(retrieved_at), 'content': content}
    digest = db.put_blob(encode(evidence))
    db.metadata('trading.public.' + digest, {'digest': digest, 'observed_at': evidence['observed_at'],
                 'known_at': evidence['known_at'], 'provider': 'PUBLIC_SOURCE_RETRIEVAL'}, write=True)
    return {'type': 'BLOB', 'digest': digest}


def public_evidence(db, refs, *, required=True):
    if not isinstance(refs, list) or (required and not refs):
        raise ValueError('cite the actual public sources or observation records')
    for ref in refs:
        if ref['type'] == 'BLOB':
            if not db.metadata('trading.public.' + ref['digest'].removeprefix('sha256:')):
                raise ValueError('unclassified content cannot enter the public workflow')
        elif ref['type'] == 'ENTITY':
            owner = Entities(db).get(ref['kind'], ref['object_id'], version=ref.get('version'))
            if not owner or owner['scope'] != 'PUBLIC':
                raise ValueError('market evidence must be public')
        elif ref['type'] != 'OBSERVATION':
            raise ValueError('unsupported market evidence')
    return refs


def market_ready(db, run, *, evidence, market=None, expected_version=None, result=None, reason=None, notification=None,
                 public_copy=None):
    from . import trading_decisions as decisions
    independent = not material(db, run).get('market_binding')
    refs = public_evidence(db, evidence)
    if not deep_gate.check_scope(db, scope='btc/market')['allowed']:
        raise Conflict('known market defect limits new market conclusions')
    if result is not None:
        record = decisions.adopt_market(db, subject=MARKET, expected_version=expected_version,
                                       result=result, evidence=refs, run=run)
    else:
        plain(reason, 'why current market judgment still applies')
        record = decisions.current_market(db, MARKET)
        if not record or reference(record) != market:
            raise Conflict('reuse requires the exact current market judgment')
        if decisions.decision_state(db, record, at=run['known_at'])['status'] != 'CURRENT':
            raise Conflict('market judgment has expired; review the original conditions')
    copy_id = None
    if public_copy is not None:
        copy_id = trading_public_report.freeze(db, run, destination(db), record, public_copy, independent=independent)
    bound = reference(record)
    db.metadata(material_key(run['ref']), {**material(db, run), 'market_binding': bound,
                 'market_review': {'reason': reason, 'evidence': refs}}, write=True)
    if notification is not None:
        if result is None:
            raise ValueError('a new decision obligation must reference the adopted change')
        register_notification(db, record, notification)
    return {'market': record, 'market_binding': bound, **({'public_copy_id': copy_id} if copy_id else {})}


def frozen_market(db, run):
    from . import trading_decisions as decisions
    bound = material(db, run).get('market_binding')
    current = decisions.current_market(db, MARKET)
    if (not current or reference(current) != bound
            or decisions.decision_state(db, current, at=run['known_at'])['status'] != 'CURRENT'):
        raise Conflict('establish the independent current market result before personal work')
    return current


def confirmed_files(db):
    project = Path(db.settings['project'])
    documents = {}
    for name in fact_views.NAMES:
        path = project / name
        try:
            original = path.read_bytes()
        except FileNotFoundError:
            continue
        documents[name] = {'digest': fingerprint(original), 'text': original.decode('utf-8')}
    return documents


def policies(db, release):
    scope, status = trading_policy.inputs(db, release)
    subject = trading_policy.POSITION_SUBJECT
    policy = scope.get(subject)
    if policy is not None:
        inputs = dict(policy['inputs'])
        inputs.pop('baseline', None)
        position = dict(inputs.get('position') or {})
        position.pop('entry_reference', None)
        if 'position' in inputs:
            inputs['position'] = position
        # A price update changes the direction input, not the policy for actual exposure.
        basis = fingerprint(encode({'kind': policy['kind'], 'inputs': inputs}))
        opening = trading_policy.entry_reference(db)
        if opening and any(position.get(key) not in (None, opening[key]) for key in ('instrument', 'product')):
            opening = None
        scope[subject] = {**policy, 'inputs': inputs, 'basis': basis, 'entry_reference': opening}
    return scope, status


def personal_context(db, run, release):
    from . import trading_decisions as decisions
    market = frozen_market(db, run)
    scope, status = policies(db, release)
    files = confirmed_files(db)
    records = {subject: decisions.current_decision(db, subject) for subject in scope}
    inputs = decisions.market_inputs(db, market, at=run['known_at'])
    review = {subject: personal_state(db, record, scope[subject], files, run, market_inputs=inputs)
              for subject, record in records.items()}
    portfolio = trading_portfolio.current(db)
    for subject, changed in trading_portfolio.changed_inputs(db, portfolio, records).items():
        state = review[subject]
        if state['status'] in {'CURRENT', 'UNRESOLVED'}:
            state['status'] = 'NEEDS_REVIEW'
        state['changed_portfolio_inputs'] = changed
    return {'market': reference(market), 'facts': files,
            'market_inputs': inputs,
            'policy_status': status, 'policies': scope,
            'current': records, 'portfolio': portfolio,
            'review': review,
            'review_targets': [{'subject': subject, **condition}
                               for subject, state in review.items() for condition in state['conditions']],
            'research': research_records(db, 'PERSONAL'),
            'archive': trading_archive.context(db, scope='PERSONAL')}


def current_quote(files, ref):
    return (isinstance(ref, dict) and set(ref) == {'file', 'quote'}
            and isinstance(ref['quote'], str) and bool(ref['quote'].strip())
            and ref['file'] in files
            and ref['quote'] in files[ref['file']]['text'])


def personal_state(db, record, policy, files, run, *, market_inputs=None):
    from . import trading_decisions as decisions
    state = decisions.decision_state(db, record, at=run['known_at'], policy=policy)
    if record:
        absent = [ref['file'] for ref in record['payload'].get('fact_refs', []) if not current_quote(files, ref)]
        history = record['payload'].get('fact_history', {})
        absent.extend(name for name, digest in history.items() if files.get(name, {}).get('digest') != digest)
        if state['status'] in {'CURRENT', 'UNRESOLVED'}:
            if absent:
                state.update(status='NEEDS_REVIEW', changed_fact_sources=sorted(set(absent)))
            before = record['payload']['market_inputs']
            spot = policy['kind'] in {'MONTHLY', 'OPPORTUNITY'}
            if spot and not any(key == 'value' or key.startswith(('assessment/', 'valuation/')) for key in before):
                state.update(status='NEEDS_REVIEW', missing_valuation_binding=True)
            live_conditions = any(key.startswith('valuation/') and value.get('conditions')
                                  for key, value in before.items())
            if state.get('market_changed') or live_conditions:
                if market_inputs is None:
                    market = decisions.current_market(db, MARKET)
                    market_inputs = decisions.market_inputs(db, market, at=run['known_at'])
                changed = [key for key, value in before.items() if market_inputs.get(key) != value]
                if changed:
                    state.update(status='NEEDS_REVIEW', changed_market_inputs=changed)
                if spot and state.get('market_changed'):
                    # Revisit only a previously cited assessment newly identified as a valuation.
                    names = {key.removeprefix('assessment/') for key in before if key.startswith('assessment/')}
                    unbound = {name for name in names if 'valuation/' + name in market_inputs
                               and 'valuation/' + name not in before}
                    if unbound:
                        source = record['payload']['market']
                        original = Entities(db).get(source['kind'], source['object_id'], version=source['version'])
                        if not original or unbound.difference(original['payload']['result'].get('valuations', {})):
                            state.update(status='NEEDS_REVIEW', missing_valuation_binding=True)
    return state


def personal_decision(db, run, release, **params):
    market = frozen_market(db, run)
    scope, _ = policies(db, release)
    return adopt_personal(db, run, market, scope, confirmed_files(db), **params)


def adopt_personal(db, run, market, scope, files, *, subject, expected_version, policy_basis, facts, fact_refs,
                   market_refs, result, evidence, notification=None, entry_reference=None):
    from . import trading_decisions as decisions
    policy = scope.get(subject)
    if not policy or policy['basis'] != policy_basis:
        raise Conflict('the applicable policy for this decision changed or is unconfirmed')
    opening_price = policy.get('entry_reference') if policy.get('position_status') != 'ABSENT' else None
    if entry_reference != (opening_price['revision'] if opening_price else None):
        raise Conflict('assess the current opening price before submitting the position decision')
    if not isinstance(facts, dict) or any(files.get(k, {}).get('digest') != v for k, v in facts.items()):
        raise Conflict('a cited personal fact source changed; re-read only the affected inputs')
    if (not isinstance(fact_refs, list)
            or any(not current_quote(files, ref) or ref['file'] not in facts for ref in fact_refs)
            or set(facts) != {ref['file'] for ref in fact_refs}):
        raise ValueError('each used personal source must have its exact applicable excerpt')
    choice = result.get('decision')
    if policy['kind'] == 'POSITION' and choice != 'UNRESOLVED' and not (fact_refs or opening_price):
        raise ValueError('a current position action needs its actual confirmed source')
    actions = result.get('conditional_actions')
    held_choice = actions.get('PRESENT') if isinstance(actions, dict) else None
    directional = isinstance(held_choice, dict) and held_choice.get('decision') in (*decisions.SHORT_DIRECTIONS, 'EXIT')
    refs = list(public_evidence(db, evidence, required=choice != 'UNRESOLVED' or directional))
    for name in facts:
        digest = db.put_blob(files[name]['text'].encode())
        refs.append({'type': 'BLOB', 'digest': digest, 'relation': 'CONFIRMED_LOCAL_FACT'})
    owner_policy = {k: v for k, v in policy.items() if k != 'inputs'}
    fact_history = {name: files.get(name, {}).get('digest') for name in fact_views.NAMES[1:]}
    record = decisions.adopt_personal(db, subject=subject, expected_version=expected_version,
                                    market=reference(market), policy=owner_policy,
                                    result=result, evidence=refs, run=run, fact_refs=fact_refs, market_refs=market_refs,
                                    fact_history=fact_history if policy['kind'] == 'POSITION' and not opening_price else None)
    if notification is not None:
        register_notification(db, record, notification)
    return record


def portfolio_decision(db, run, release, *, expected_version, market, primary, alternatives, interactions,
                       decisions, reuse, member_reviews=None):
    from .trading_decisions import current_decision

    frozen = frozen_market(db, run)
    if reference(frozen) != market:
        raise Conflict('the portfolio must use the exact frozen market that was assessed')
    scope, policy_status = policies(db, release)
    files = confirmed_files(db)
    previous = trading_portfolio.current(db)
    if expected_version != (previous['version'] if previous else 0):
        raise Conflict('the portfolio decision changed before submission')
    assessed = trading_portfolio.assessment(frozen, scope, primary, alternatives, interactions)
    submitted = {proposal['subject']: proposal for proposal in decisions}
    if (len(submitted) != len(decisions) or submitted.keys() & reuse.keys()
            or submitted.keys() | reuse.keys() != scope.keys()):
        raise ValueError('supply each applicable portfolio decision once, as a new choice or an exact reuse')

    previous_payload = previous['payload'] if previous else {}
    former = previous_payload.get('decisions', {})
    prior_refs = {**previous_payload.get('unavailable_members', {}), **former}
    ended = set(policy_status['ended'])
    if not scope:
        # Missing sources do not establish that the last adopted scope ended.
        known = ended | policy_status['unavailable'].keys()
        previously_empty = bool(previous and not prior_refs and policy_status['state'] == 'CURRENT')
        if not ended or not prior_refs.keys() <= known or not (prior_refs.keys() & ended or previously_empty):
            raise Conflict('an empty portfolio needs a confirmed end to its previously adopted scope')
    records = {}
    for subject, ref in reuse.items():
        record = current_decision(db, subject)
        if not record or reference(record) != ref or former.get(subject) != ref:
            raise Conflict('reuse must refer to a current decision previously assessed in the portfolio')
        if personal_state(db, record, scope[subject], files, run)['status'] not in {'CURRENT', 'UNRESOLVED'}:
            raise Conflict('reassess the affected decision before reusing it: ' + subject)
        records[subject] = record
    for subject, proposal in submitted.items():
        records[subject] = adopt_personal(db, run, frozen, scope, files, **proposal)
        if scope[subject].get('position_status') == 'UNKNOWN' and not scope[subject].get('entry_reference'):
            if not records[subject]['payload']['result'].get('conditional_actions'):
                raise ValueError('record explicit in-position and absent conclusions for the unknown position')

    changed = trading_portfolio.changed_inputs(db, previous, records, interactions=interactions)
    affected = reuse.keys() & changed.keys()
    if affected:
        detail = ', '.join(f'{subject} ({", ".join(changed[subject])})' for subject in sorted(affected))
        raise Conflict('reassess decisions whose linked portfolio inputs changed: ' + detail)
    compared = trading_portfolio.compare_members(db, prior_refs, records, reuse,
                                                 {} if member_reviews is None else member_reviews)
    for edge in interactions:
        subject, source = edge['subject'], edge['depends_on']
        if (subject in reuse and compared[source].get('relation') in ('NEW', 'REVISE', 'REPRICE')
                and compared[subject].get('relation') != 'CONTINUE'):
            raise Conflict('explain the continued choice or reassess it after its linked arrangement changed: ' + subject)
    assessed.update(member_reviews=compared, policy_status=policy_status,
                    unavailable_members={subject: ref for subject, ref in prior_refs.items()
                                         if subject not in scope and subject not in ended})
    return trading_portfolio.save(db, market=frozen, records=records, assessed=assessed,
                                  run=run, expected_version=expected_version)


def research_records(db, scope):
    owners = Entities(db)
    return [owners.get('RESEARCH', row['object_id']) for row in owners.list(
        scope=scope, kind='RESEARCH', active=True)['objects'] if row['object_id'].startswith('trading/')]


def research(db, run, *, scope, subject, question, evidence, expected_version, outcome=None):
    material(db, run)
    if scope == 'PERSONAL':
        frozen_market(db, run)
    if scope not in ('PUBLIC', 'PERSONAL'):
        raise ValueError('research scope must match the actual question')
    issue = 'trading/research/' + identity(scope, plain(subject, 'research subject'), plain(question, 'question'))
    refs = public_evidence(db, evidence)
    entities = Entities(db)
    previous = entities.get('RESEARCH', issue)
    input_digest = 'sha256:' + identity(scope, subject, question, sorted(refs, key=encode))
    claim_scope = ('btc/market/' if scope == 'PUBLIC' else 'btc/portfolio/') + issue.rsplit('/', 1)[-1]
    phase = 'PUBLIC' if scope == 'PUBLIC' else 'PORTFOLIO'
    if outcome is not None and (not isinstance(outcome, dict) or not outcome.get('conclusion')
                                or not outcome.get('claim_id') or not outcome.get('result_ref')
                                or not outcome.get('body')):
        raise ValueError('research completion requires the actual original native result')
    if outcome is not None:
        claims = deep_gate.status(db)['claims']
        claim = claims.get(outcome['claim_id'])
        if (not claim or claim.get('issue_id') != issue or claim.get('state') != 'COMPLETE'
                or claim.get('input_digest') != input_digest or claim.get('phase') != phase
                or claim.get('scope') != claim_scope or not previous
                or previous['payload']['input_digest'] != input_digest
                or claim.get('result_ref') != outcome['result_ref']
                or outcome['origin_run'] != previous['source_ref']):
            raise Conflict('original research call has no completed bound result')
        raw = outcome['body'].encode()
        if ('sha256:' + fingerprint(raw) != outcome['result_ref']
                or not claim['claimed_at_ms'] <= millis(outcome['started_at']) <= millis(outcome['completed_at'])
                <= millis(run['known_at'])):
            raise Conflict('the research evidence differs from the original actual completed turn')
        outcome = {k: v for k, v in outcome.items() if k != 'body'}
        outcome['artifact_digest'] = db.put_blob(raw)
        refs = [*refs, {'type': 'BLOB', 'digest': outcome['artifact_digest'], 'relation': 'COMPLETED_RESEARCH'}]
    elif previous and previous['payload']['input_digest'] != input_digest:
        held = deep_gate.status(db)['claims'].values()
        if any(item.get('issue_id') == issue and item['state'] in {'CLAIMED', 'UNKNOWN'} for item in held):
            raise Conflict('resolve the original research attempt before changing its actual inputs')
    if previous and previous['payload'].get('result') is not None and previous['payload']['input_digest'] == input_digest:
        if outcome is None or outcome == previous['payload']['result']:
            return previous
        raise Conflict('retained independent result must preserve its original evidence')
    entities.put({'kind': 'RESEARCH', 'object_id': issue, 'scope': scope,
                  'active': True, 'payload': {'question': question, 'subject': subject,
                  'input_digest': input_digest, 'claim_scope': claim_scope, 'phase': phase,
                  'result': outcome}, 'dependencies': refs, 'observed_at': run['known_at'],
                  'known_at': run['known_at'], 'source_ref': run['ref'], 'method_version': run['method_version'],
                  'change_reason': 'research result received' if outcome else 'open decision question'},
                 expected_version=expected_version)
    return entities.get('RESEARCH', issue)


def review(db, run, *, target, condition_id, outcome, explanation, evidence, **updates):
    from . import trading_decisions as decisions
    material(db, run)
    record = Entities(db).get(target['kind'], target['object_id'], version=target['version'])
    if not record or record['digest'] != target['digest']:
        raise Conflict('review requires the original condition and decision')
    if record['scope'] == 'PERSONAL':
        frozen_market(db, run)
    return decisions.record_review(db, target=target, condition_id=condition_id, outcome=outcome,
                                   explanation=explanation, evidence=public_evidence(db, evidence, required=outcome != 'UNKNOWN'),
                                   run=run, **updates)


def invoke(db, method, params):
    params = dict(params)
    if method == SET_ENTRY_REFERENCE:
        return trading_policy.set_entry_reference(db, **params)
    run, release = run_record(db, params.pop('run_ref'))
    action = method.removeprefix('trading.')
    if action == 'open':
        return open_run(db, run, release, **params)
    if action in {'public', 'personal'} and 'condition' in params:
        from .trading_decisions import condition_detail
        if action == 'personal':
            frozen_market(db, run)
        else:
            material(db, run)
        return condition_detail(db, **params['condition'], scope=action.upper(), at=run['known_at'])
    if action == 'public':
        return public_context(db, run)
    if action == 'source':
        material(db, run)
        digest = params['digest'].removeprefix('sha256:')
        if not db.metadata('trading.public.' + digest):
            raise ValueError('public source identity not found')
        return db.value(digest)
    if action == 'research_archive':
        material(db, run)
        if params['scope'] == 'PERSONAL':
            frozen_market(db, run)
        return trading_archive.inspect_research(db, release=release, **params)
    if action == 'research_result':
        material(db, run)
        row = Entities(db).get('RESEARCH', params['object_id'], version=params['version'])
        if not row or row['digest'] != params['digest'] or not row['object_id'].startswith('trading/research/'):
            raise Conflict('read the exact confirmed original research')
        if row['scope'] == 'PERSONAL':
            frozen_market(db, run)
        outcome = row['payload']['result']
        if not outcome:
            raise ValueError('the independent research has no confirmed result yet')
        return {**outcome, 'body': db.blobs.get(outcome['artifact_digest']).decode()}
    if action == 'personal':
        return personal_context(db, run, release)
    if action == 'personal_ready':
        return {'market': reference(frozen_market(db, run))}
    if action == 'seed_policy':
        frozen_market(db, run)
        trading_policy.seed(db, release, run)
        return {'seeded': True}
    if action == 'policy':
        frozen_market(db, run)
        return trading_policy.record(db, run=run, **params)
    if action == 'personal_decision':
        return personal_decision(db, run, release, **params)
    if action == 'portfolio_decision':
        return portfolio_decision(db, run, release, **params)
    if action == 'market':
        return market_ready(db, run, **params)
    if action == 'evidence':
        return register_evidence(db, run, **params)
    if action == 'review':
        return review(db, run, **params)
    if action == 'research':
        return research(db, run, **params)
    if action == 'assess_feedback':
        return assess_feedback(db, run, release, **params)
    return feedback(db, run, release, action, params)


def register_notification(db, record, notification):
    from . import trading_delivery as delivery
    if (not isinstance(notification, dict) or 'reason' not in notification
            or set(notification) - {'reason', 'kind', 'due_at', 'scope'}):
        raise ValueError('provide the actual reason for a material feedback obligation')
    subject = record['payload']['subject']
    if (subject == trading_policy.POSITION_SUBJECT and record['kind'] == 'PLAN'
            and notification.get('kind', 'UPDATE') == 'UPDATE' and 'scope' not in notification):
        notification = {**notification, 'scope': 'DIRECTION'}
    if 'scope' in notification:
        if (notification['scope'] != 'DIRECTION' or record['kind'] != 'PLAN'
                or subject != trading_policy.POSITION_SUBJECT or notification.get('kind', 'UPDATE') != 'UPDATE'):
            raise ValueError('DIRECTION is the ordinary direction update of the original short')
        plain(notification['reason'], 'why the direction requires feedback')
        if notification.get('due_at') is None:
            try:
                target = destination(db)
            except Conflict:
                target = None  # An unavailable destination must preserve the obligation.
            if target and delivery.position_already_displayed(db, target, record, stamp()):
                return
    delivery.require_report(db, subject=subject, trigger_ref=record['digest'], decision=reference(record),
                            **notification)


def validate_report_decisions(db, run, release, refs, *, complete=False, whole_layout=False):
    market = frozen_market(db, run)
    applicable, status = policies(db, release)
    if complete and status['state'] != 'CURRENT':
        raise Conflict('recheck the confirmed policies before issuing a complete personal report')
    subjects = {}
    records = {}
    portfolio = None
    objects = Entities(db)
    files = confirmed_files(db)
    for ref in refs:
        record = objects.get(ref['kind'], ref['object_id'])
        if not record or reference(record) != ref:
            raise Conflict('a decision this report actually cites changed')
        if ref == reference(market):
            continue
        if ref['object_id'] == trading_portfolio.OBJECT_ID and ref['kind'] == 'PLAN':
            if portfolio is not None:
                raise ValueError('a report uses one joint portfolio decision')
            portfolio = record
            continue
        subject = (record.get('payload') or {}).get('subject')
        policy = applicable.get(subject)
        if (not policy or subject in subjects or record.get('scope') != 'PERSONAL'
                or not ref['object_id'].startswith('trading/personal/')):
            raise ValueError('report references must address the active market or a distinct applicable personal decision')
        state = personal_state(db, record, policy, files, run)
        if state['status'] not in {'CURRENT', 'UNRESOLVED'}:
            raise Conflict('reassess this decision against its actual evidence before reporting it')
        subjects[subject] = ref
        records[subject] = record
    if reference(market) not in refs:
        raise ValueError('report needs the independent market judgement it presents')
    if (complete or whole_layout) and applicable.keys() != subjects.keys():
        raise Conflict('complete personal feedback needs each currently applicable decision')
    if whole_layout and portfolio is None and (subjects or trading_portfolio.current(db)):
        raise Conflict('personal feedback requires its adopted joint portfolio decision')
    if portfolio is None:
        return market, records, status, None
    plan = portfolio['payload']
    if (not subjects and (plan['decisions'] or applicable)) or plan['market'] != reference(market) or any(
            plan['decisions'].get(subject) != ref for subject, ref in subjects.items()):
        raise Conflict('report choices must match their joint portfolio and frozen market')
    for source in trading_portfolio.linked_sources(plan['interactions'], subjects.keys()):
        ref = plan['decisions'][source]
        record = objects.get(ref['kind'], ref['object_id'])
        if (source not in applicable or not record or reference(record) != ref
                or personal_state(db, record, applicable[source], files, run)['status'] not in {'CURRENT', 'UNRESOLVED'}):
            raise Conflict('reassess the linked portfolio input before reporting: ' + source)
    return market, records, status, portfolio


def report_allocation(records, status, base, edits):
    """Describe the individual choices and the cash they leave in each planning currency."""
    terms, cash = {}, {}
    for subject, record in records.items():
        payload = record['payload']
        policy, choice = payload['policy'], payload['result']
        terms[subject] = {key: choice[key] for key in ('decision', 'amount', 'month') if key in choice}
        terms[subject].update({key: policy[key] for key in ('kind', 'budget', 'currency') if key in policy})
        if policy['kind'] == 'MONTHLY':
            unit = policy['currency']
            balance = cash.get(unit, Decimal(0))
            amount = choice['amount']
            cash[unit] = (balance + Decimal(policy['budget']) - Decimal(amount)
                          if balance is not None and amount is not None else None)
    prior_terms = (base or {}).get('allocation_terms', {})
    for subject in status['unavailable']:
        kind = prior_terms.get(subject, {}).get('kind')
        if subject == trading_policy.POSITION_SUBJECT:
            kind = 'POSITION'
        terms[subject] = {'kind': kind, 'unavailable': True}
    if 'cash' in terms:
        raise Conflict('a portfolio subject conflicts with the cash report entry')
    # Carry each category's displayed order through updates and temporary source gaps.
    prior_order = (base or {}).get('member_order', [])
    order = list(dict.fromkeys([*prior_order, *edits, *terms]))
    ranks = {'MONTHLY': 0, 'OPPORTUNITY': 2, 'POSITION': 3}
    members = [s for s in order if s in terms]
    members.append('cash')
    members.sort(key=lambda s: 1 if s == 'cash' else ranks.get(terms[s]['kind'], 2))
    terms['cash'] = {unit: format(value, 'f') if value is not None else None for unit, value in cash.items()}
    return members, terms


def report_basis(db, run, market, records, selected):
    """Track adopted inputs with a direct counterpart in the displayed edition."""
    public = market['payload']['result']
    basis = {'outlook.primary': public['primary'], 'outlook.alternative': public['alternatives'],
             'opportunities.comparison': public['value'], 'risks.conditions': public['conditions']}
    from . import trading_decisions
    inputs = trading_decisions.market_inputs(db, market, at=run['known_at'])
    inputs['evidence'] = market['dependencies']
    scoped = ('market.BTC', 'market.SPX', 'market.NDX', 'crosschecks.evidence', 'opportunities.comparison')
    if selected.keys() - set(scoped):
        raise ValueError('market_sources must refer to a core market, crosscheck or opportunity entry')
    for path in scoped:
        names = selected.get(path)
        if names is None:
            # With no narrower evidence attribution, review the complete public basis before reusing this entry.
            source = inputs
        else:
            if (not isinstance(names, list) or not names or any(not isinstance(n, str) or n not in inputs for n in names)
                    or len(names) != len(set(names))):
                raise ValueError('choose actual frozen market fields for ' + path)
            source = {name: inputs[name] for name in names}
        basis[path] = [basis.get(path), source]
    for subject, record in records.items():
        if subject != trading_policy.POSITION_SUBJECT:
            result = record['payload']['result']
            basis['allocation.' + subject] = {key: result[key] for key in
                ('decision', 'reason', 'risk', 'opportunity_cost', 'amount_basis', 'conditions') if key in result}
    return {entry: fingerprint(encode(value)) for entry, value in basis.items()}


def report_judgement_times(market, records, portfolio):
    """Resolve the adoption times of the exact decisions supporting each report entry."""
    market_at = stamp(datetime.fromtimestamp(market['known_ms'] / 1000, timezone.utc))
    times = {section + '.' + item: market_at for section, (_, items) in trading_report.SECTIONS.items()
             for item in items}
    for subject, record in records.items():
        times['allocation.' + subject] = stamp(datetime.fromtimestamp(record['known_ms'] / 1000, timezone.utc))
    if portfolio:
        times['allocation.cash'] = stamp(datetime.fromtimestamp(portfolio['known_ms'] / 1000, timezone.utc))
    return times


def compose_report(db, run, release, target, refs, support, complete, params):
    from . import trading_delivery as delivery, trading_risk_seed
    base = delivery.report_base(db, target)
    if params.pop('base_report_id') != (base['report_id'] if base else None):
        raise Conflict('the delivered baseline changed; read trading.feedback and reapply the affected changes')
    assessed_at = params.pop('assessed_at')
    if not millis(material(db, run)['public_known_at']) <= millis(assessed_at) <= millis(run['known_at']):
        raise ValueError('assessed_at must be the assessment time in this run')
    changes = params.pop('changes')
    public_seed = params.pop('public_seed', None)
    if not isinstance(changes, dict) or (not support and changes.keys() - {'allocation'}):
        raise ValueError('portfolio report changes are confined to allocation; freeze public wording with trading.market')
    if not support and (params.get('market_sources') or
                        any(p in trading_public_report.PATHS for p in params.get('reviewed_unchanged', []))):
        raise ValueError('market_sources and public text reviews belong to the independently frozen public copy')
    seed_sources = None
    risk_public_copy = None
    original_risk = None
    if support:
        if (not isinstance(changes, dict) or set(changes) != {'risks'} or not isinstance(changes['risks'], dict)
                or set(changes['risks']) != {'conditions'}):
            raise ValueError('limited feedback accepts only the verified public risk note')
        original_risk = delivery.risk_note(db, target, support['event_ref'])
        if original_risk and (support['valid_until'] != original_risk['valid_until']
                              or changes['risks']['conditions'] != original_risk['text']):
            raise Conflict('recover the original unreported public risk with its wording and expiry')
    if support and not (base and base.get('seeded')):
        base = trading_risk_seed.build(db, run, release, assessed_at, public_seed, base)
        seed_sources = base['seed_sources']
        risk_public_copy = base.get('risk_public_copy')
    elif support and not base.get('public_market'):
        base = trading_risk_seed.migrate_public(db, run, target, base, public_seed)
        risk_public_copy = base.get('risk_public_copy')
    elif public_seed is not None:
        raise ValueError('a public seed applies only when the risk edition still needs independent public wording')
    members, terms, line = None, (base or {}).get('allocation_terms', {}), None
    position_reviewed = False
    source_times = {}
    pending_review = params.pop('pending_review', [])
    if original_risk:
        applicable = {name + '.' + item for name, section in base['modules'].items() for item in section}
        pending_review = list(dict.fromkeys([*pending_review, *(path for path in original_risk['pending_review']
                                                               if path in applicable)]))
    reviewed = set(params.pop('reviewed_unchanged', []))
    chosen = dict((base or {}).get('market_sources', {}))
    updated_sources = params.pop('market_sources', {})
    if support and updated_sources:
        raise ValueError('independent public risk feedback only updates the risk event')
    chosen.update(updated_sources)
    expiries = dict((base or {}).get('valid_until', {}))
    basis = (base or {}).get('basis', {})
    if support is None:
        market, records, status, portfolio = validate_report_decisions(
            db, run, release, refs, complete=complete, whole_layout=True)
        public_copy = trading_public_report.for_report(db, run, target, market, base)
        chosen = public_copy['sources']
        reviewed.update(public_copy['reviewed'])
        changes = dict(changes)
        for path in public_copy['edited']:
            name, item = path.split('.', 1)
            changes.setdefault(name, {})[item] = public_copy['sections'][name][item]
        source_times = report_judgement_times(market, records, portfolio)
        allocation_edits = changes.get('allocation', {})
        if not isinstance(allocation_edits, dict):
            raise ValueError('allocation changes must be keyed by subject')
        members, terms = report_allocation(records, status, base, allocation_edits)
        basis = report_basis(db, run, market, records, chosen)
        market_end = market['payload']['result']['valid_until']
        expiries = {section + '.' + entry: market_end for section, (_, entries) in trading_report.SECTIONS.items()
                    for entry in entries}
        for subject, record in records.items():
            expiries['allocation.' + subject] = record['payload']['result']['valid_until']
        budgets = [expiries['allocation.' + subject] for subject, record in records.items()
                   if record['payload']['policy']['kind'] == 'MONTHLY']
        expiries['allocation.cash'] = min(budgets, key=millis, default=market_end)
        position = records.get(trading_policy.POSITION_SUBJECT)
        if position:
            line = delivery.position_line(position)
            marked = (base or {}).get('pending_review', {}).get('allocation.' + trading_policy.POSITION_SUBJECT)
            position_reviewed = bool(marked and position['known_ms'] > millis(marked))
        elif trading_policy.POSITION_SUBJECT in status['unavailable']:
            line = '原 BTCUSDT 保护性空单：方向待复评。'
    elif base:
        expired = [path for path, end in expiries.items()
                   if millis(end) <= millis(assessed_at) and path not in base['pending_review']]
        pending_review = list(dict.fromkeys([*pending_review, *expired]))
    body, document = trading_report.compose(base, changes, assessed_at, line, limited=support is not None,
                                            members=members, pending_review=pending_review,
                                            event_ref=params.get('event_ref'), position_reviewed=position_reviewed,
                                            risk_valid_until=support['valid_until'] if support else None,
                                            source_times=source_times, reviewed_unchanged=reviewed,
                                            risk_event_at=original_risk['assessed_at'] if original_risk else None)
    if support is None and base and base['seeded']:
        affected = {path for path, digest in basis.items() if base.get('basis', {}).get(path) != digest}
        edited = {section + '.' + key for section, items in changes.items() for key in items}
        if reviewed - (affected - edited):
            raise ValueError('reviewed_unchanged must name an unchanged item with revised adopted inputs')
        missing = sorted(affected - edited - reviewed)
        if missing:
            raise Conflict('update or explicitly review the unchanged text against revised inputs: ' + ', '.join(missing))
        before = base['modules']['allocation']
        after = document['modules']['allocation']
        for subject, prior in base.get('allocation_terms', {}).items():
            if subject == trading_policy.POSITION_SUBJECT or subject not in terms or prior == terms[subject]:
                continue
            if subject not in changes.get('allocation', {}):
                raise Conflict('review the allocation entry for the changed adopted terms: ' + subject)
            # A new month can legitimately keep the exact same wording and amounts.
            fields = ('amount', 'budget', 'currency')
            different_money = subject == 'cash' or any(prior.get(k) != terms[subject].get(k) for k in fields)
            if different_money and before.get(subject) == after.get(subject):
                raise Conflict('the changed monetary terms need an updated allocation entry: ' + subject)
    elif reviewed:
        raise ValueError('reviewed_unchanged applies only when reviewing an existing regular edition')
    if support is None:
        document['public_copy_id'] = public_copy['copy_id']
    elif risk_public_copy:
        document['risk_public_copy'] = risk_public_copy
    document['public_market'] = base['public_market'] if support else public_copy['market']
    document['allocation_terms'] = terms
    document['valid_until'] = expiries
    document['basis'] = basis
    document['market_sources'] = chosen
    if seed_sources:
        document['seed_sources'] = seed_sources
    return body, document, line, base


def feedback(db, run, release, action, params):
    from . import trading_delivery as delivery, trading_risk_seed
    target = destination(db)
    if action == 'record_delivery':
        material(db, run)
        if params['action'] == 'CONFIRM_ARCHIVED':
            return delivery.reconcile_archived(db, destination=target,
                                               report_id=params['report_id'], receipt=params['receipt'])
        original = delivery.get(db, report_id=params['report_id'])
        if original['destination']['thread_id'] != target['thread_id']:
            raise Conflict('recover the original report using its original approved destination')
        return delivery.record(db, **params)
    if action == 'inherit_feedback':
        frozen_market(db, run)
        from .execution import load_methods
        load_methods(release)
        return delivery.inherit_open(db, destination=target,
                                    legacy_root=Path(db.settings['project']) / '.strategy/v4', **params)
    if action == 'feedback':
        if params.get('public_writing_inputs'):
            if set(params) != {'public_writing_inputs'}:
                raise ValueError('request public author inputs separately')
            from . import trading_decisions
            material(db, run)
            market = trading_decisions.current_market(db, MARKET)
            base = delivery.report_base(db, target)
            return trading_public_report.context(db, target, market, base) if market else {
                'market': None, 'base_report_id': (base or {}).get('report_id'), 'copy_ready': False}
        if params.get('risk_seed_inputs'):
            if set(params) != {'risk_seed_inputs'}:
                raise ValueError('request public risk seed inputs separately')
            material(db, run)
            base = delivery.report_base(db, target)
            needed = not (base and base.get('seeded') and base.get('public_market'))
            return {'base_report_id': base['report_id'] if base else None, 'needs_public_seed': bool(needed),
                    'public': trading_risk_seed.public_source(db, base) if needed else None,
                    'pending_public_risks': delivery.pending_public_risks(db, target)}
        if 'before_report_id' in params and 'decision_id' not in params:
            raise ValueError('name the decision whose earlier display is needed')
        if not material(db, run).get('market_binding'):
            if params:
                raise Conflict('freeze the independent market before reading delivered decisions')
            before = delivery.context(db, destination=target, at=run['known_at'])
            return {'day': before['day'], 'daily_due': before['daily_due'],
                    'base_report_id': (delivery.report_base(db, target, identity_only=True) or {}).get('report_id'),
                    'outstanding_feedback': len(before['obligations']),
                    'outstanding_attempts': len(before['reconcile'])}
        market = frozen_market(db, run)
        base = delivery.report_base(db, target)
        edition = {'base_report_id': base['report_id'] if base else None, 'report_base': base,
                   'legacy_public_digests': trading_public_report.legacy_digests(base)}
        if params.pop('writing_inputs', False):
            if params:
                raise ValueError('request the report writing inputs separately from prior decision displays')
            from .trading_report_inputs import writing_inputs
            portfolio = trading_portfolio.current(db)
            records = {}
            if portfolio and portfolio['payload']['market'] == reference(market):
                objects = Entities(db)
                for subject, ref in portfolio['payload']['decisions'].items():
                    record = objects.get(ref['kind'], ref['object_id'])
                    if record and reference(record) == ref:
                        records[subject] = record
            position = records.get(trading_policy.POSITION_SUBJECT)
            return {**edition, 'writing_inputs': writing_inputs(
                market, portfolio, records, delivery.position_line(position) if position else None)}
        before = {**delivery.context(db, destination=target, at=run['known_at']), **edition}
        if 'decision_id' in params:
            before['previous_display'] = delivery.previous_display(db, destination=target, at=run['known_at'], **params)
            from .trading_decisions import PERSONAL_PREFIX
            if params['decision_id'].startswith(PERSONAL_PREFIX):
                subject = params['decision_id'][len(PERSONAL_PREFIX):]
                owner = trading_policy.confirmed_scope_end(db, subject)
                if owner:
                    before['recorded_scope_end'] = {'policy': reference(owner), 'reason': owner['payload']['ended_reason']}
        return before
    if action in ('report', 'preview_report'):
        purpose = params.pop('purpose')
        if purpose not in ('DAILY', 'UPDATE', 'RISK', 'CORRECTION'):
            raise ValueError('report purpose must describe the actual feedback responsibility')
        refs = params['decisions']
        complete = params.pop('complete', purpose == 'DAILY')
        validity = params.pop('valid_until', None)
        if purpose == 'DAILY' and not complete:
            raise ValueError('a daily report requires the complete applicable decisions')
        event = params.get('event_ref')
        displayed_correction = purpose == 'CORRECTION' and isinstance(event, str) and event.startswith(delivery.REPORT)
        support = None
        if purpose == 'RISK' and not refs and not complete:
            if validity is None:
                raise ValueError('limited public risk feedback needs its own useful lifetime')
            support = risk_support(db, run, params['event_ref'], validity, assessed_at=params['assessed_at'])
            if any(params.get(key) for key in ('obligation_ids', 'legacy_coverage', 'decision_coverage')):
                raise Conflict('independent public risk feedback has only its own public event obligation')
            if params.get('supersedes'):
                old = delivery.get(db, report_id=params['supersedes']['report_id'])
                if (old['destination'] != target
                        or (old.get('support') or {}).get('event_ref') != support['event_ref']):
                    raise Conflict('limited risk feedback can only replace the same outstanding public event')
        if support is not None:
            edits = params['changes']
            if (not isinstance(edits, dict) or set(edits) != {'risks'} or not isinstance(edits['risks'], dict)
                    or set(edits['risks']) != {'conditions'}):
                raise ValueError('a verified public risk can only submit its event note')
            risk_text = trading_public_report.risk_text(db, run, target, support, edits['risks']['conditions'])
            params['changes'] = {'risks': {'conditions': risk_text}}
        expected_copy = params.pop('public_copy_id', None)
        if support and expected_copy is not None:
            raise ValueError('the public event uses its own evidence and edition')
        recovery = dict(params) if support else None
        try:
            body, document, position_line, base = compose_report(db, run, release, target, refs, support, complete, params)
        except trading_risk_seed.SeedPending as gap:
            if action == 'preview_report':
                return {'status': 'AWAITING_REPORT_BASIS', 'missing': str(gap)}
            return delivery.defer_public_risk(db, target, support, recovery, str(gap))
        if support is None and ((action == 'report' or expected_copy is not None)
                                and expected_copy != document['public_copy_id']):
            raise Conflict('preview the current public copy, then submit its public_copy_id')
        document['purpose'] = purpose
        if displayed_correction:
            delivery.check_displayed_correction(db, target, params.get('supersedes'), event)
            document['corrects'] = event
        if action == 'preview_report':
            if support:
                return {'base_report_id': document['base_report_id'], 'assessed_at': document['assessed_at'],
                        'risk_update': next(entry['text'] for entry in document['risk_updates']
                                            if entry['event_ref'] == support['event_ref'])}
            return {'body': body, 'base_report_id': document['base_report_id'], 'public_copy_id': document['public_copy_id']}
        context = delivery.context(db, destination=target, at=run['known_at'])
        obligations = set(params.pop('obligation_ids', []))
        if purpose == 'DAILY':
            if not context['daily_due']:
                raise Conflict('the daily feedback has a confirmed receipt already')
            obligation = delivery.require_report(db, subject='daily-feedback', trigger_ref=context['day'],
                                                 reason=params['reason'], kind=purpose, legacy_destination=target)
        else:
            event = plain(params['event_ref'], 'original material event reference')
            if (purpose == 'UPDATE' and not obligations and base and base['seeded']
                    and document['modules'] == base['modules']
                    and document['pending_review'] == base['pending_review']
                    and document['risk_updates'] == base['risk_updates']
                    and trading_report.expired_risks(base, base['assessed_at'])
                    == trading_report.expired_risks(document, document['assessed_at'])):
                raise Conflict('the destination has already received these conclusions; check for a material change')
            if not (displayed_correction or event in {item['digest'] for item in refs}
                    or db.metadata('trading.public.' + event.removeprefix('sha256:'))):
                raise ValueError('refer to the actual changed decision or stored event evidence')
            obligation = delivery.require_report(db, subject='material-feedback',
                trigger_ref=event if displayed_correction else identity(sorted(refs, key=encode), event),
                reason=params['reason'], kind=purpose,
                legacy_destination=target)
        params.pop('event_ref', None)
        obligations.add(obligation['obligation_id'])
        report = delivery.prepare(db, destination=target, run_ref=run['ref'], complete=complete, body=body,
                                  obligation_ids=sorted(obligations), support=support, position_line=position_line,
                                  document=document, **params)
        if support is not None:
            return {k: report[k] for k in ('report_id', 'destination', 'body_hash', 'status', 'obligation_ids')}
        return report
    if action == 'begin_delivery':
        if params.pop('expected_destination') != target:
            raise Conflict('host and service disagree about the original approved destination')
        existing = delivery.get(db, report_id=params['report_id'])
        if existing['status'] not in {'PENDING', 'NOT_SENT_CONFIRMED'} or existing.get('superseded_by'):
            return delivery.begin(db, destination=target, run_ref=run['ref'], **params)
        if existing.get('support'):
            risk_support(db, run, **existing['support'])
            if existing.get('document', {}).get('risk_public_copy'):
                trading_public_report.verify_frozen(db, target, existing['document'])
            seed_sources = existing.get('document', {}).get('seed_sources')
            if seed_sources:
                trading_risk_seed.check_sources(db, run, release, seed_sources)
        else:
            market, *_ = validate_report_decisions(db, run, release, existing['decisions'], complete=existing['complete'],
                                                   whole_layout='document' in existing)
            document = existing.get('document', {})
            if document.get('public_copy_id'):
                staged = trading_public_report.for_report(db, run, target, market, delivery.report_base(db, target))
                if staged['copy_id'] != document['public_copy_id']:
                    raise Conflict('the public wording changed after review; recompose before first dispatch')
        return delivery.begin(db, destination=target, run_ref=run['ref'], **params)
    raise ValueError('unsupported trading operation')


def risk_support(db, run, event_ref, valid_until, assessed_at=None):
    material(db, run)
    provenance = db.metadata('trading.public.' + event_ref.removeprefix('sha256:'))
    if (not provenance or provenance['provider'] != 'PUBLIC_SOURCE_RETRIEVAL'
            or not millis(provenance['known_at']) <= millis(assessed_at or run['known_at'])
            <= millis(run['known_at']) < millis(valid_until)):
        raise Conflict('limited risk feedback requires an actual public event within its reviewed relevance window')
    return {'event_ref': event_ref, 'valid_until': valid_until}


def _unchecked_due_condition(state):
    for condition in state['conditions']:
        for check in (condition, condition.get('prior_check')):
            if check and check['due']:
                checked = check['review_known_ms']
                if checked is None or checked < millis(check['next_review_at']):
                    return check['id']
    return None


def _require_current_feedback_review(db, run, release, market):
    from . import trading_decisions as decisions
    due = _unchecked_due_condition(decisions.decision_state(db, market, at=run['known_at']))
    if due:
        raise Conflict('check the due market condition before choosing silence: ' + due)
    scope, scope_status = policies(db, release)
    portfolio = trading_portfolio.current(db)
    adopted = portfolio['payload']['decisions'] if portfolio else {}
    prior = portfolio['payload'].get('unavailable_members', {}) if portfolio else {}
    ended = (adopted.keys() | prior.keys()) & set(scope_status['ended'])
    if ended:
        raise Conflict('adopt the confirmed scope ending before choosing silence: ' + ', '.join(sorted(ended)))
    if not scope:
        return
    files = confirmed_files(db)
    records = {subject: decisions.current_decision(db, subject) for subject in scope}
    changes = trading_portfolio.changed_inputs(db, portfolio, records)
    for subject, record in records.items():
        if not record or adopted.get(subject) != reference(record) or subject in changes:
            raise Conflict('adopt the affected member in the current portfolio before choosing silence: ' + subject)
        state = personal_state(db, record, scope[subject], files, run)
        if state['status'] not in {'CURRENT', 'UNRESOLVED'}:
            raise Conflict('reassess the affected decision before choosing silence: ' + subject)
        due = _unchecked_due_condition(state)
        if due:
            raise Conflict('check the due decision condition before choosing silence: ' + subject + '/' + due)


def assess_feedback(db, run, release, *, outcome, reason, evidence):
    from . import trading_delivery as delivery
    snapshot = material(db, run)
    if outcome not in ('SILENT', 'EVIDENCE_LIMITED', 'REPORT_REQUIRED'):
        raise ValueError('record the real feedback determination')
    refs = public_evidence(db, evidence, required=outcome != 'EVIDENCE_LIMITED')
    if outcome == 'SILENT':
        market = frozen_market(db, run)
        status = delivery.context(db, destination=destination(db), at=run['known_at'])
        if status['daily_due'] or status['due_obligation_ids']:
            raise Conflict('daily or due material feedback is outstanding; check the original obligation and delivery')
        _require_current_feedback_review(db, run, release, market)
    result = {'outcome': outcome, 'reason': plain(reason, 'actual feedback basis'), 'evidence': refs,
              'decided_at': run['known_at']}
    db.metadata(material_key(run['ref']), {**snapshot, 'feedback_assessment': result}, write=True)
    return result
