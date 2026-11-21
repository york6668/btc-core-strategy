"""Normal trading workflow. The node commits results, the host runs the model and transport."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from . import deep_gate, fact_views, trading_archive, trading_policy, trading_portfolio
from .codec import Conflict, encode, fingerprint, identity, millis, stamp
from .entities import Entities

MARKET = 'allocation'
SET_ENTRY_REFERENCE = 'trading.set_entry_reference'
SERVICE_SOURCES = ('api.py', 'contracts.py', 'execution.py', 'mcp.py', 'trading.py', 'trading_decisions.py',
                   'trading_delivery.py', 'trading_policy.py', 'trading_archive.py', 'trading_portfolio.py')
READS = {'trading.public', 'trading.personal', 'trading.personal_ready', 'trading.feedback', 'trading.source',
         'trading.research_archive', 'trading.research_result'}
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


def market_ready(db, run, *, evidence, market=None, expected_version=None, result=None, reason=None, notification=None):
    from . import trading_decisions as decisions
    material(db, run)
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
    bound = reference(record)
    db.metadata(material_key(run['ref']), {**material(db, run), 'market_binding': bound,
                 'market_review': {'reason': reason, 'evidence': refs}}, write=True)
    if notification is not None:
        if result is None:
            raise ValueError('a new decision obligation must reference the adopted change')
        register_notification(db, record, notification)
    return {'market': record, 'market_binding': bound}


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
    review = {subject: personal_state(db, record, scope[subject], files, run) for subject, record in records.items()}
    portfolio = trading_portfolio.current(db)
    for subject, changed in trading_portfolio.changed_inputs(db, portfolio, records).items():
        state = review[subject]
        if state['status'] in {'CURRENT', 'UNRESOLVED'}:
            state['status'] = 'NEEDS_REVIEW'
        state['changed_portfolio_inputs'] = changed
    return {'market': reference(market), 'facts': files,
            'market_inputs': decisions.market_inputs(market),
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


def personal_state(db, record, policy, files, run):
    from . import trading_decisions as decisions
    state = decisions.decision_state(db, record, at=run['known_at'], policy=policy)
    if record:
        absent = [ref['file'] for ref in record['payload'].get('fact_refs', []) if not current_quote(files, ref)]
        history = record['payload'].get('fact_history', {})
        absent.extend(name for name, digest in history.items() if files.get(name, {}).get('digest') != digest)
        if state['status'] in {'CURRENT', 'UNRESOLVED'}:
            if absent:
                state.update(status='NEEDS_REVIEW', changed_fact_sources=sorted(set(absent)))
            if state.get('market_changed'):
                market = decisions.current_market(db, MARKET)
                now = decisions.market_inputs(market)
                changed = [key for key, value in record['payload']['market_inputs'].items() if now.get(key) != value]
                if changed:
                    state.update(status='NEEDS_REVIEW', changed_market_inputs=changed)
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
    directional = isinstance(held_choice, dict) and held_choice.get('decision') in ('KEEP', 'REDUCE', 'EXIT')
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


def portfolio_decision(db, run, release, *, expected_version, market, primary, alternatives, interactions, decisions, reuse):
    from .trading_decisions import current_decision

    frozen = frozen_market(db, run)
    if reference(frozen) != market:
        raise Conflict('the portfolio must use the exact frozen market that was assessed')
    scope, _ = policies(db, release)
    files = confirmed_files(db)
    previous = trading_portfolio.current(db)
    if expected_version != (previous['version'] if previous else 0):
        raise Conflict('the portfolio decision changed before submission')
    assessed = trading_portfolio.assessment(frozen, scope, primary, alternatives, interactions)
    submitted = {proposal['subject']: proposal for proposal in decisions}
    if (not scope or len(submitted) != len(decisions) or submitted.keys() & reuse.keys()
            or submitted.keys() | reuse.keys() != scope.keys()):
        raise ValueError('supply each applicable portfolio decision once, as a new choice or an exact reuse')

    former = previous['payload']['decisions'] if previous else {}
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
            or set(notification) - {'reason', 'kind', 'due_at'}):
        raise ValueError('provide the actual reason for a material feedback obligation')
    delivery.require_report(db, subject=record['payload']['subject'],
                            trigger_ref=record['digest'], **notification)


def validate_report_decisions(db, run, release, refs, *, complete=False, require_portfolio=False):
    market = frozen_market(db, run)
    applicable, status = policies(db, release)
    if complete and status['state'] != 'CURRENT':
        raise Conflict('recheck the confirmed policies before issuing a complete personal report')
    subjects = {}
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
            portfolio = record['payload']
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
    if reference(market) not in refs:
        raise ValueError('report needs the independent market judgement it presents')
    if complete and applicable.keys() != subjects.keys():
        raise Conflict('complete personal feedback needs each currently applicable decision')
    if subjects and require_portfolio and portfolio is None:
        raise Conflict('personal feedback requires its adopted joint portfolio decision')
    if portfolio is None:
        return
    if not subjects or portfolio['market'] != reference(market) or any(
            portfolio['decisions'].get(subject) != ref for subject, ref in subjects.items()):
        raise Conflict('report choices must match their joint portfolio and frozen market')
    for source in trading_portfolio.linked_sources(portfolio['interactions'], subjects.keys()):
        ref = portfolio['decisions'][source]
        record = objects.get(ref['kind'], ref['object_id'])
        if (source not in applicable or not record or reference(record) != ref
                or personal_state(db, record, applicable[source], files, run)['status'] not in {'CURRENT', 'UNRESOLVED'}):
            raise Conflict('reassess the linked portfolio input before reporting: ' + source)


def feedback(db, run, release, action, params):
    from . import trading_delivery as delivery
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
        before = delivery.context(db, destination=target, at=run['known_at'])
        if not material(db, run).get('market_binding'):
            return {'day': before['day'], 'daily_due': before['daily_due'],
                    'outstanding_feedback': len(before['obligations']),
                    'outstanding_attempts': len(before['reconcile'])}
        frozen_market(db, run)
        return before
    if action == 'report':
        purpose = params.pop('purpose')
        if purpose not in ('DAILY', 'UPDATE', 'RISK', 'CORRECTION'):
            raise ValueError('report purpose must describe the actual feedback responsibility')
        refs = params['decisions']
        complete = params.pop('complete', purpose == 'DAILY')
        validity = params.pop('valid_until', None)
        if purpose == 'DAILY' and not complete:
            raise ValueError('a daily report requires the complete applicable decisions')
        support = None
        if purpose == 'RISK' and not refs and not complete:
            if validity is None:
                raise ValueError('limited public risk feedback needs its own useful lifetime')
            support = risk_support(db, run, params['event_ref'], validity)
            if any(params.get(key) for key in ('obligation_ids', 'supersedes', 'legacy_coverage')):
                raise Conflict('independent public risk feedback has only its own public event obligation')
        else:
            validate_report_decisions(db, run, release, refs, complete=complete, require_portfolio=True)
        context = delivery.context(db, destination=target, at=run['known_at'])
        obligations = set(params.pop('obligation_ids', []))
        if purpose == 'DAILY':
            if not context['daily_due']:
                raise Conflict('the daily feedback has a confirmed receipt already')
            obligation = delivery.require_report(db, subject='daily-feedback', trigger_ref=context['day'],
                                                 reason=params['reason'], kind=purpose, legacy_destination=target)
        else:
            event = plain(params['event_ref'], 'original material event reference')
            if not (event in {item['digest'] for item in refs}
                    or db.metadata('trading.public.' + event.removeprefix('sha256:'))):
                raise ValueError('refer to the actual changed decision or stored event evidence')
            obligation = delivery.require_report(db, subject='material-feedback',
                trigger_ref=identity(sorted(refs, key=encode), event), reason=params['reason'], kind=purpose,
                legacy_destination=target)
        params.pop('event_ref', None)
        obligations.add(obligation['obligation_id'])
        return delivery.prepare(db, destination=target, run_ref=run['ref'], complete=complete,
                                obligation_ids=sorted(obligations), support=support, **params)
    if action == 'begin_delivery':
        if params.pop('expected_destination') != target:
            raise Conflict('host and service disagree about the original approved destination')
        existing = delivery.get(db, report_id=params['report_id'])
        if existing.get('support'):
            risk_support(db, run, **existing['support'])
        else:
            validate_report_decisions(db, run, release, existing['decisions'], complete=existing['complete'])
        return delivery.begin(db, destination=target, run_ref=run['ref'], **params)
    raise ValueError('unsupported trading operation')


def risk_support(db, run, event_ref, valid_until):
    material(db, run)
    provenance = db.metadata('trading.public.' + event_ref.removeprefix('sha256:'))
    if (not provenance or provenance['provider'] != 'PUBLIC_SOURCE_RETRIEVAL'
            or not millis(provenance['known_at']) <= millis(run['known_at']) < millis(valid_until)):
        raise Conflict('limited risk feedback requires an actual public event within its reviewed relevance window')
    return {'event_ref': event_ref, 'valid_until': valid_until}


def _require_current_entry_review(db, run, release, obligations):
    from .trading_decisions import current_decision

    scope, _ = policies(db, release)
    subject = trading_policy.POSITION_SUBJECT
    policy = scope.get(subject)
    if not policy or (not policy.get('entry_reference') and policy.get('position_status') != 'ABSENT'):
        return
    current = current_decision(db, subject)
    portfolio = trading_portfolio.current(db)
    if (not current or not portfolio
            or portfolio['payload']['decisions'].get(subject) != reference(current)
            or personal_state(db, current, policy, confirmed_files(db), run)['status'] not in {'CURRENT', 'UNRESOLVED'}):
        raise Conflict('reassess and adopt the current opening price or confirmed closure in the portfolio before choosing silence')
    if any(item['status'] == 'OPEN' and item['subject'] == subject and item['trigger_ref'] == current['digest']
           for item in obligations):
        raise Conflict('the adopted position decision has outstanding material feedback')


def assess_feedback(db, run, release, *, outcome, reason, evidence):
    from . import trading_delivery as delivery
    snapshot = material(db, run)
    if outcome not in ('SILENT', 'EVIDENCE_LIMITED', 'REPORT_REQUIRED'):
        raise ValueError('record the real feedback determination')
    refs = public_evidence(db, evidence, required=outcome != 'EVIDENCE_LIMITED')
    if outcome == 'SILENT':
        frozen_market(db, run)
        status = delivery.context(db, destination=destination(db), at=run['known_at'])
        if status['daily_due']:
            raise Conflict('outstanding feedback cannot be closed by a silent observation')
        _require_current_entry_review(db, run, release, status['obligations'])
    result = {'outcome': outcome, 'reason': plain(reason, 'actual feedback basis'), 'evidence': refs,
              'decided_at': run['known_at']}
    db.metadata(material_key(run['ref']), {**snapshot, 'feedback_assessment': result}, write=True)
    return result
