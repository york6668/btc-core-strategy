"""Independent Astra research; Sol alone persists approved cycle assessments."""
from . import market_state
from copy import deepcopy
from pathlib import Path
import json
import re

from .common import create, digest, exact, fact_inputs, facts, instant, locked, now, pointer, read, require, text

SCOPE = ('/domain', '/factor_assessment', '/conclusion', '/outlook', '/robustness', '/portfolio_analysis', '/recommendations',
         '/domain/drivers', '/domain/value_zones', '/domain/value_band_map', '/domain/event', '/domain/forward_value_band_semantics')
CHANGES = {'INITIAL_QUESTION', 'NEW_KEY_EVIDENCE', 'MATERIAL_COUNTEREVIDENCE', 'REOPEN_CONDITION_MET'}
FORBIDDEN_INPUT = {'candidates', 'market_review', 'recommendation_checks', 'extra_findings',
                   'changes_since_hour', 'changes_since_deep', 'candidate_review', 'research_findings', 'factor_checks'}


def issue_identity(issue_key, scope):
    require(isinstance(issue_key, str) and re.fullmatch(r'[A-Z][A-Z0-9_]*', issue_key), 'research needs a stable issue key')
    require(isinstance(scope, list) and scope and len(set(scope)) == len(scope), 'research scope required')
    require(all(isinstance(p, str) and any(p == s or p.startswith(s + '/') for s in SCOPE) for p in scope), 'research scope is not a core conclusion')
    require(not re.search(r'(?:^|_)(?:20\d{6}|\d{10,}|\d{4}Z)(?:_|$)', issue_key), 'an hourly suffix cannot create a fresh research issue')
    # Scope is an applicability boundary, not an escape hatch for starting the
    # same question again after adding another affected conclusion.
    return digest({'issue_key': issue_key})


def _load(root, kind, identity):
    require(isinstance(identity, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', identity), 'invalid research identity')
    value = read(Path(root) / 'research' / kind / (identity[7:] + '.json'))
    key = {'requests': 'request_id', 'independent': 'independent_id', 'candidates': 'candidate_id', 'results': 'result_id'}[kind]
    require(value[key] == identity == digest({k: v for k, v in value.items() if k != key}), 'research artifact damaged')
    return value


def _save(root, kind, key, value):
    value = dict(value)
    if value.get('market_result') and market_state.public_schema_revision(value['market_result']['outlook']) >= 9:
        from .forward_lifecycle import now as freeze_now
        value.setdefault('lifecycle_frozen_at', freeze_now())
    value[key] = digest(value)
    path = Path(root) / 'research' / kind / (value[key][7:] + '.json')
    if path.exists():
        require(read(path) == value, 'research artifact collision')
    else:
        create(path, value)
    result = value.get('market_result')
    if result is not None:
        from .forward_lifecycle import register
        register(root, result['outlook']['market_assessment'], path, '/lifecycle_frozen_at')
    return value


def _guard_completed(root, req, phase, attempt_id, rollout_path):
    # The runtime dispatcher spends before launch. Every new result must bind
    # its real completed ticket. Older immutable studies are read, not re-saved.
    import model_guard
    require(req.get('issue_id'), 'guarded research needs original issue identity')
    try:
        model_guard.require_success(req['issue_id'], 'btc/research', phase, attempt_id, rollout_path)
    except model_guard.Blocked as exc:
        require(False, str(exc))


def context(root):
    """Small per-question baselines, independent of main assessment cadence."""
    from .storage import current
    pinned = current(root) or {}
    absorptions = {v['result_id']: v for v in (pinned.get('assessment') or {}).get('research_absorptions', [])
                   if v['decision'] in ('ADOPT', 'PARTIAL')}
    issues = {}
    for path in sorted((Path(root) / 'research' / 'requests').glob('*.json')):
        value = _load(root, 'requests', 'sha256:' + path.stem)
        identity = value['issue_id']
        slot = issues.setdefault(identity, {'issue_id': identity, 'issue_key': value['issue_key'], 'scope': value['scope'],
                                           'first_request': None, 'latest_request': None, 'latest_result': None})
        if slot['first_request'] is None or instant(value['created_at']) < instant(slot['first_request']['created_at']):
            slot['first_request'] = {k: value[k] for k in ('request_id', 'created_at', 'market_cutoff', 'market_evidence_id')}
        if slot['latest_request'] is None or instant(value['created_at']) > instant(slot['latest_request']['created_at']):
            slot['latest_request'] = {k: value[k] for k in ('request_id', 'created_at', 'market_cutoff', 'market_evidence_id', 'question', 'reopen_conditions')}
            slot['scope'] = value['scope']
    latest_ids = {slot['latest_request']['request_id'] for slot in issues.values()}
    stages = {identity: {} for identity in latest_ids}
    recorded_results = []
    candidates, results = {}, {}
    for kind, clock in (('independent', 'completed_at'), ('candidates', 'revealed_at'), ('results', 'completed_at')):
        for path in sorted((Path(root) / 'research' / kind).glob('*.json')):
            value = _load(root, kind, 'sha256:' + path.stem)
            if kind == 'results':
                recorded_results.append(value)
                results[value['result_id']] = value
            elif kind == 'candidates':
                candidates[value['candidate_id']] = value
            if value['request_id'] not in latest_ids:
                continue
            stage = stages[value['request_id']]
            previous = stage.get(kind)
            key = lambda v: (v[clock] if isinstance(v[clock], (int, float)) else instant(v[clock]).timestamp())
            if previous is None or key(value) > key(previous):
                stage[kind] = value
    for value in recorded_results:
        require(value['issue_id'] in issues, 'research result has no request')
        slot = issues[value['issue_id']]
        if slot['latest_result'] is None or (instant(value['market_cutoff']), instant(value['completed_at'])) > (instant(slot['latest_result']['market_cutoff']), instant(slot['latest_result']['completed_at'])):
            slot['latest_result'] = {k: value[k] for k in ('result_id', 'request_id', 'completed_at', 'market_cutoff', 'market_evidence_id', 'coverage', 'counterevidence', 'unresolved', 'reopen_conditions')}
    for slot in issues.values():
        baseline = slot['latest_result'] or slot['first_request']
        slot['accumulation_start'] = baseline['market_cutoff']
        slot['baseline_market_evidence_id'] = baseline['market_evidence_id']
        stage = stages[slot['latest_request']['request_id']]
        candidate = stage.get('candidates')
        result = stage.get('results')
        completed = result
        adopted = [results[key] for key in absorptions if key in results
                   and results[key]['request_id'] == slot['latest_request']['request_id']]
        if adopted:
            # An actually adopted mapping is authoritative for this request.
            # A later scratch candidate cannot send the hourly task backwards.
            result = max(adopted, key=lambda v: instant(v['completed_at']))
            candidate = candidates[result['candidate_id']]
        independent = (_load(root, 'independent', candidate['independent_id']) if candidate else stage.get('independent'))
        reviewed = {v['candidate_id'] for v in recorded_results}
        slot['pending_candidates'] = [
            {k: v[k] for k in ('candidate_id', 'candidate_hash', 'independent_id', 'revealed_at')}
            for v in sorted(candidates.values(), key=lambda v: instant(v['revealed_at']))
            if v['request_id'] == slot['latest_request']['request_id'] and v['candidate_id'] not in reviewed]
        slot['completed_result'] = ({k: completed[k] for k in ('result_id', 'candidate_id', 'completed_at')}
                                    if completed else None)
        slot['adopted_result'] = ({'result_id': result['result_id'], 'candidate_id': result['candidate_id'],
                                  'decision_id': pinned['decision_id'], 'disposition': absorptions[result['result_id']]['decision']}
                                 if adopted else None)
        # These are existing immutable artifacts, not a second workflow owner or
        # a claim that their runtime is still alive. A prior result cannot hide
        # a newer request, nor can a rejected candidate hide a later revision.
        progress = {'stage': 'REQUESTED', 'runtime_status': 'NOT_CHECKED'}
        if independent:
            require(independent['request_id'] == slot['latest_request']['request_id'], 'research phase belongs to another request')
            progress.update(stage='INDEPENDENT_COMPLETED', independent_id=independent['independent_id'],
                            agent_id=independent['agent_id'], rollout_path=independent['rollout_path'],
                            independent_completed_at=independent['completed_at'])
        if candidate:
            require(candidate['agent_id'] == independent['agent_id'], 'research candidate belongs to another agent')
            progress.update(stage='CANDIDATE_REVEALED', candidate_id=candidate['candidate_id'],
                            candidate_hash=candidate['candidate_hash'], revealed_at=candidate['revealed_at'])
        if result and candidate and result['candidate_id'] == candidate['candidate_id']:
            progress.update(stage='RESULT_RECORDED', result_id=result['result_id'],
                            result_completed_at=result['completed_at'],
                            cycle_approved=(result.get('cycle_review') or {}).get('approved'))
        slot['latest_request']['progress'] = progress
        slot['status'] = 'RESEARCHED' if progress['stage'] == 'RESULT_RECORDED' else 'REQUESTED'
        progress['resume_required'] = slot['status'] == 'REQUESTED'
    return {'schema': 'BTC_RESEARCH_CONTEXT_V4', 'issues': sorted(issues.values(), key=lambda v: v['issue_id']),
            'legacy_coverage': 'UNKNOWN', 'research_context_id': digest(issues)}


def findings(root, values, known_refs, bundle=None, evidence_dir=None):
    """Materiality remains Sol's judgement; labels and clocks are not research triggers."""
    require(isinstance(values, list), 'research findings must be a list')
    known = {v['issue_id']: v for v in context(root)['issues']}
    result, seen = [], set()
    for finding in values:
        exact(finding, 'issue_key scope question material_change change_kind change_reason refs reopen_condition', 'research finding')
        identity = issue_identity(finding['issue_key'], finding['scope'])
        require(identity not in seen, 'duplicate research issue in one observation')
        seen.add(identity)
        text(finding['question'], 'unresolved core question')
        require(type(finding['material_change']) is bool, 'research materiality must be explicit')
        text(finding['change_reason'], 'research evidence change explanation')
        require(isinstance(finding['refs'], list) and finding['refs'], 'research finding requires current raw references')
        for ref in finding['refs']:
            if isinstance(ref, str):
                require(ref in known_refs, 'research finding requires current raw references')
            else:
                from . import evidence
                require(bundle is not None and evidence_dir is not None, 'research raw reference needs frozen factor evidence')
                evidence.resolve(bundle, evidence_dir, ref, verified=finding['material_change'])
        previous = known.get(identity)
        require(finding['change_kind'] in CHANGES | {'NO_NEW_KEY_EVIDENCE'}, 'research needs a specific discriminating change')
        if finding['material_change']:
            require(finding['change_kind'] != 'NO_NEW_KEY_EVIDENCE', 'material research requires key evidence or a new unresolved question')
            require((previous is None) == (finding['change_kind'] == 'INITIAL_QUESTION'), 'existing issue cannot masquerade as initial research')
        else:
            require(finding['change_kind'] == 'NO_NEW_KEY_EVIDENCE', 'unchanged research must remain covered')
        conditions = ((previous or {}).get('latest_result') or (previous or {}).get('latest_request') or {}).get('reopen_conditions', [])
        if finding['change_kind'] == 'REOPEN_CONDITION_MET':
            require(finding['reopen_condition'] in conditions, 'reopen finding does not match a recorded discriminator')
        else:
            require(finding['reopen_condition'] is None, 'reopen condition is only for a matching condition trigger')
        # A legacy assessment is never fabricated into a researched issue. Sol
        # must still identify an unresolved question and actual materiality.
        result.append({'issue_id': identity, 'eligible': finding['material_change'],
                       'prior_result_id': (previous or {}).get('latest_result', {}).get('result_id') if (previous or {}).get('latest_result') else None,
                       'status': 'MATERIAL_QUESTION' if finding['material_change'] else 'NO_NEW_KEY_EVIDENCE', **finding})
    return result


def _candidate_free(value):
    if isinstance(value, dict):
        require(not FORBIDDEN_INPUT.intersection(value), 'independent input contains candidate-bearing data')
        for item in value.values():
            _candidate_free(item)
    elif isinstance(value, list):
        for item in value:
            _candidate_free(item)


def request(root, project, spec, workflow_revision=None):
    """Freeze a bounded independent input and an explicit new/reopened question."""
    from . import evidence
    from .storage import current, NEW_WRITE_REVISION
    manual = isinstance(spec, dict) and isinstance(spec.get('change'), dict) and spec['change'].get('kind') == 'USER_REQUESTED_REVIEW'
    exact(spec, 'issue_key scope question decision_id evidence_id change reopen_conditions' + (' user_request' if manual else ''), 'research request')
    if manual:
        exact(spec['user_request'], 'reference reason', 'explicit user research request')
        for key in ('reference', 'reason'):
            text(spec['user_request'][key], 'user research ' + key)
    require(isinstance(spec['evidence_id'], str) and re.fullmatch(r'sha256:[a-f0-9]{64}', spec['evidence_id']), 'invalid research evidence identity')
    exact(spec['change'], 'kind reason refs reopen_condition', 'research change')
    require(spec['change']['kind'] in CHANGES | {'USER_REQUESTED_REVIEW'}, 'research change kind invalid')
    text(spec['question'], 'research question')
    text(spec['change']['reason'], 'why research is needed')
    require(isinstance(spec['reopen_conditions'], list) and spec['reopen_conditions'], 'research needs explicit reopen conditions')
    for item in spec['reopen_conditions']:
        text(item, 'reopen condition')
    from . import cycle
    require(not manual or spec['issue_key'] == cycle.ISSUE_KEY, 'user-requested review uses the existing BTC_CYCLE')
    if spec['issue_key'] == cycle.ISSUE_KEY:
        require(set(cycle.SCOPE) <= set(spec['scope']), 'cycle research requires the full cycle and portfolio scope')
    root = Path(root)
    with locked(root):
        issue_id = issue_identity(spec['issue_key'], spec['scope'])
        ctx = context(root)
        previous = next((v for v in ctx['issues'] if v['issue_id'] == issue_id), None)
        # Bind a user request to its real study even when it resumes an already
        # running automatic study. This receipt is idempotency, not strategy state.
        user_path = (root / 'research' / 'user_requests' / (digest({'issue_id': issue_id,
                     'reference': spec['user_request']['reference']})[7:] + '.json')) if manual else None
        def remember(request_value):
            if user_path is not None and not user_path.exists():
                create(user_path, {'issue_id': issue_id, 'user_request': spec['user_request'],
                                   'request_id': request_value['request_id']})
            return request_value
        def reuse(request_value):
            progress = (previous['latest_request']['progress'] if previous and
                        previous['latest_request']['request_id'] == request_value['request_id'] else None)
            next_action = ('VERIFY_RUNTIME_AND_RESUME_EXISTING_STAGE_WITH_SUPPLEMENT'
                           if progress and progress['stage'] != 'RESULT_RECORDED'
                           else 'READ_ORIGINAL_STUDY_AND_CURRENT_APPLICABILITY')
            if progress and progress.get('cycle_approved') is False:
                next_action = 'RESUME_ORIGINAL_ASTRA_CANDIDATE_REVISION'
            return remember(dict(request_value, reused=True,
                **({'progress': progress} if progress else {}),
                next_action=next_action))
        if user_path is not None and user_path.exists():
            receipt = read(user_path)
            require(receipt['issue_id'] == issue_id and receipt['user_request']['reference'] == spec['user_request']['reference'],
                    'user research request binding mismatch')
            return reuse(_load(root, 'requests', receipt['request_id']))
        if manual:
            # Also recover a request saved immediately before receipt creation.
            for path in (root / 'research' / 'requests').glob('*.json'):
                old = _load(root, 'requests', 'sha256:' + path.stem)
                if old['issue_id'] == issue_id and (old.get('user_request') or {}).get('reference') == spec['user_request']['reference']:
                    return reuse(old)
        pinned = current(root)
        require(pinned is not None and pinned['decision_id'] == spec['decision_id'], 'research request has a stale core conclusion')
        if previous:
            same_initial = (spec['issue_key'] == cycle.ISSUE_KEY and spec['change']['kind'] == 'INITIAL_QUESTION'
                and all(_load(root, 'requests', previous['latest_request']['request_id']).get(k) == v for k,v in spec.items()))
            require(same_initial or spec['change']['kind'] != 'INITIAL_QUESTION', 'existing issue needs new key evidence, not a renamed initial request')
        else:
            require(manual or spec['change']['kind'] == 'INITIAL_QUESTION', 'new issue must identify its initial unresolved question')
        deadline_basis = cycle.deadline_reopen(root, pinned.get('assessment'), spec['change'])
        if spec['change']['kind'] == 'REOPEN_CONDITION_MET':
            conditions = ((previous or {}).get('latest_result') or (previous or {}).get('latest_request') or {}).get('reopen_conditions', [])
            require(deadline_basis or spec['change']['reopen_condition'] in conditions, 'research request does not match a recorded reopen condition')
        else:
            require(spec['change']['reopen_condition'] is None, 'unexpected reopen condition')
        directory = root / 'evidence' / spec['evidence_id'][7:]
        package = evidence.validate(read(directory / 'evidence.json'), directory)
        require(spec['evidence_id'] == package['evidence_id'], 'research evidence mismatch')
        require(isinstance(spec['change']['refs'], list) and spec['change']['refs'], 'research needs exact new key evidence references')
        for ref in spec['change']['refs']:
            evidence.resolve(package, directory, ref, verified=True)
        if spec['issue_key'] == cycle.ISSUE_KEY and previous:
            adopted, _ = cycle.adopted(root, pinned.get('assessment'))
            latest_request = previous['latest_request']
            if latest_request['request_id'] != (adopted or {}).get('request_id'):
                # New evidence belongs in the current study's supplement. Do
                # not discard an independent answer/candidate at an hour edge.
                original = _load(root, 'requests', latest_request['request_id'])
                return reuse(original)
        # New timestamps/source aliases with identical cited values are not new
        # key evidence. The model must choose genuinely discriminating values.
        evidence_change_id = digest(sorted({r['value_hash'] for r in spec['change']['refs']}))
        if manual:
            evidence_change_id = digest({'user_request': spec['user_request']['reference'], 'evidence': evidence_change_id})
        if deadline_basis:
            evidence_change_id = digest({'evidence': evidence_change_id, 'due_cycle_result': deadline_basis})
        for path in (root / 'research' / 'requests').glob('*.json'):
            old = _load(root, 'requests', 'sha256:' + path.stem)
            previous_change_id = old['evidence_change_id']
            if not manual and old['change']['kind'] == 'USER_REQUESTED_REVIEW':
                # A manual review may reuse old evidence; its user identity
                # must not make those same values look new to a later sniff.
                previous_change_id = digest(sorted({r['value_hash'] for r in old['change']['refs']}))
            require(old['issue_id'] != issue_id or previous_change_id != evidence_change_id, 'same issue and key evidence already requested')
        # Explicit source projection, never the full sensing object/handoff.
        sources = []
        market_raw = None
        for source in package['sources']:
            raw_path = directory / 'raw' / (source['raw_hash'][7:] + '.json')
            payload = read(raw_path)
            _candidate_free(payload)
            sources.append({'source': source, 'path': str(raw_path), 'raw_hash': source['raw_hash']})
            if isinstance(payload, dict) and payload.get('schema') == 'BTC_MARKET_EVIDENCE_V1':
                if market_raw is None or instant(payload['cutoff']) > instant(market_raw['cutoff']):
                    market_raw = payload
        # Avoid depending on a historical domain's reference names.
        import market_observation as market
        if market_raw is None:
            for source in package['sources']:
                payload = read(directory / 'raw' / (source['raw_hash'][7:] + '.json'))
                if isinstance(payload, dict) and payload.get('schema') == market.EVIDENCE_SCHEMA:
                    if market_raw is None or instant(payload['cutoff']) > instant(market_raw['cutoff']):
                        market_raw = payload
        require(market_raw is not None, 'research needs a current complete public market package')
        market.validate_evidence(market_raw)
        fact_version = facts(project)
        anchor = pinned.get('assessment') or {}
        packet = {'schema': 'BTC_RESEARCH_INDEPENDENT_INPUT_V4', 'question': spec['question'], 'scope': spec['scope'],
                  'evidence_id': package['evidence_id'], 'cutoff': package['cutoff'], 'sources': sources,
                  'measurements': package['measurements'], 'verified_facts': package['verified_facts'], 'gaps': package['gaps'],
                  'fact_files': {k: {'path': str(Path(project) / k), 'hash': v} for k, v in fact_version.items()},
                  'historical_anchor': {'decision_id': pinned['decision_id'], 'created_at': pinned['created_at'],
                      **{k: anchor[k] for k in ('temporal_scope', 'section_provenance') if k in anchor},
                      **{k: anchor.get(k) for k in cycle.SECTIONS}}}
        from .equity_products import subjects, REPORT_QUOTE_POLICY
        packet['report_subjects'] = subjects(package, market_state.effective(root, anchor, pinned['decision_id'])['dispute_zones'])
        packet['report_quote_policy'] = deepcopy(REPORT_QUOTE_POLICY)
        if package.get('collector'):
            from .collector import context as collector_context
            packet['collector_observations'] = collector_context(package, directory)
        if spec['issue_key'] == cycle.ISSUE_KEY:
            # Historical authored sections can mix market and personal advice,
            # including factor_assessment/outlook. Keep public raw sources and
            # dated provenance for the market pass; reveal advice afterwards.
            packet['historical_anchor'] = {k:v for k,v in packet['historical_anchor'].items()
                if k in {'decision_id','created_at','temporal_scope','section_provenance'}}
            if 'section_provenance' in packet['historical_anchor']:
                packet['historical_anchor']['section_provenance'] = {
                    section:{key:value for key,value in record.items()
                             if key in {'mode','assessed_at','decision_id','evidence_id'}}
                    for section,record in packet['historical_anchor']['section_provenance'].items()}
            packet['cycle_assignment'] = {'role': 'Astra/max cycle analysis', 'required_coverage': list(cycle.COVERAGE),
                'instructions': cycle.CORE_REPORT_REVIEW + cycle.ANALYSIS_FOCUS_REVIEW,
                'method_references': cycle.method_references('research', project)}
        policy_path = Path(root) / 'write-policy.json'
        minimum = read(policy_path).get('minimum_assessment_revision', 1) if policy_path.exists() else 1
        workflow_revision = max(NEW_WRITE_REVISION, 5, minimum, workflow_revision or 4, market_state.revision(anchor)) if market_state.enabled(root, anchor) else 4
        if workflow_revision >= 5:
            packet.pop('fact_files', None)
            packet['historical_anchor'] = market_state.public_content(anchor)
            packet['historical_dispute_zones'] = market_state.effective(root, anchor, pinned['decision_id'])['dispute_zones']
            packet['historical_zone_review'] = 'Historical hypotheses only. Rebuild boundaries from dated public raw evidence first, then retain, revise or reject each old area. An internal level crossing is not a whole-range breakout. Record when each boundary became knowable; never backdate later evidence.'
            packet['workflow_revision'] = workflow_revision
            if workflow_revision >= 9:
                from .forward_lifecycle import public_feedback
                packet['prospective_feedback'] = public_feedback(root,
                    anchor.get('outlook', {}).get('market_assessment'), as_of=package['cutoff'],
                    source_owner={'kind': 'DECISION', 'id': pinned['decision_id']})
            public_instructions = market_state.INSTRUCTIONS
            if workflow_revision >= 6:
                from .closed_loop import MARKET_INSTRUCTIONS
                public_instructions += MARKET_INSTRUCTIONS
            packet['market_result_contract'] = {
                'output': 'Single JSON object: outlook, factor_assessment, market_report. outlook includes semantic/narrative/refs/market_assessment/dispute_zones. No portfolio or account analysis.',
                'instructions': public_instructions,
                'schema_reference': str(Path(__file__).resolve().parents[2] / 'references/market-state.md'),
            }
            if 'cycle_assignment' in packet:
                # Share cumulative public duties; personal work stays after freezing.
                packet['cycle_assignment']['instructions'] = public_instructions
                packet['cycle_assignment']['required_coverage'] = [v for v in cycle.COVERAGE if v != 'portfolio_waiting']
                packet['cycle_assignment']['method_references'].pop('recommendations', None)
            if workflow_revision >= 6:
                packet['historical_hypotheses'] = {key: packet.pop(key) for key in ('historical_anchor', 'historical_dispute_zones')}
                for row in packet['report_subjects'].values():
                    if isinstance(row, dict):
                        row.pop('product_zones', None)
                        row.pop('underlying_zones', None)
        _candidate_free(packet)
        input_id = digest(packet)
        input_path = root / 'research' / 'inputs' / (input_id[7:] + '.json')
        if not input_path.exists():
            create(input_path, packet)
        return remember(_save(root, 'requests', 'request_id', {'schema': 'BTC_RESEARCH_REQUEST_V4', **spec, 'issue_id': issue_id,
            'workflow_revision': workflow_revision, 'fact_version': fact_version, 'knowledge_cutoff': package['cutoff'], 'market_cutoff': market_raw['cutoff'], 'market_evidence_id': market_raw['evidence_id'],
            'independent_input_id': input_id, 'independent_input_path': str(input_path.relative_to(root)),
            'evidence_change_id': evidence_change_id, 'prior_result_id': (previous or {}).get('latest_result', {}).get('result_id') if (previous or {}).get('latest_result') else None,
            'created_at': now()}))


def read_input(root, request_id, phase='independent', candidate_id=None, independent_id=None):
    """Auditable CLI read; the runtime trace binds this exact material to a phase."""
    req = _load(root, 'requests', request_id)
    if phase == 'independent':
        require(candidate_id is None, 'independent read cannot include candidates')
        value = read(Path(root) / req['independent_input_path'])
        identity = req['independent_input_id']
        require(digest(value) == identity, 'independent input changed')
        _candidate_free(value)
    elif phase == 'portfolio':
        require(candidate_id is None and independent_id is not None, 'portfolio input needs the actual independent result')
        independent = _load(root, 'independent', independent_id)
        require(independent['request_id'] == request_id and independent.get('market_result') is not None,
                'portfolio input requires a frozen public market result')
        project = Path(root).parent.parent
        inputs = fact_inputs(project)
        current_facts = inputs['fact_version']
        value = {'market_result': independent['market_result'], 'market_result_hash': independent['market_result_hash'],
                 'fact_version': current_facts,
                 'fact_files': {name: {'path': str(project / name), 'hash': hashed} for name, hashed in current_facts.items() if hashed is not None},
                 'fact_availability': inputs['files'],
                 'instructions': '仅完成个人组合应对；公开市场结果与原市场正文保持原文。追加个人部分，不改写市场方向、周期、风险和分歧区判断。'}
        public_input = read(Path(root) / req['independent_input_path'])
        require(digest(public_input) == req['independent_input_id'], 'independent input changed')
        if req.get('workflow_revision', 4) >= 6:
            from .closed_loop import INSTRUCTIONS, public_hash
            value['market_content_hash'] = public_hash(independent['market_result'])
            value['instructions'] = INSTRUCTIONS
            if req.get('workflow_revision',4) >= 8:
                from .storage import current
                from .investment import decision as investment_decision, open_issues, policy_inputs
                from .strategy_validation import feedback
                baseline=(current(root) or {}).get('assessment',{})
                value['investment_decision']=investment_decision(baseline)
                value['open_investment_issues']=open_issues(baseline)
                value['investment_feedback']=feedback(root,baseline)
                value['recorded_policy_inputs']=policy_inputs(root,current_facts)
                if req.get('workflow_revision',4) >= 9:
                    from .portfolio_ledger import constraints
                    value['account_constraints']=constraints(root, project, now())
        elif public_input.get('collector_observations'):
            value['collector_observations'] = deepcopy(public_input['collector_observations'])
        identity = digest(value)
    else:
        require(phase == 'candidate' and candidate_id is not None, 'invalid research input phase')
        value = _load(root, 'candidates', candidate_id)
        require(value['request_id'] == request_id, 'candidate belongs to another request')
        if 'evidence_context' in value:
            directory = Path(root) / 'evidence' / value['evidence_context']['evidence_id'][7:]
            require(_review_evidence(root, req, value['candidate'], directory) == value['evidence_context'],
                    'candidate supplementary evidence changed')
        if 'fact_context' in value:
            _result_facts(req, value)
        identity = candidate_id
    return {'schema': 'BTC_RESEARCH_INPUT_READ_V4', 'request_id': request_id, 'phase': phase, 'input_id': identity, 'payload': value}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _read_proofs(value):
    """Unwrap actual tool transport, retaining the whole frozen read contract."""
    decoder=json.JSONDecoder()
    pending=[(value,0)]
    nodes=characters=0
    while pending:
        item,depth=pending.pop()
        nodes+=1
        require(depth<=12 and nodes<=20000,'research read proof transport limit exceeded')
        if isinstance(item,dict):
            if item.get('schema')=='BTC_RESEARCH_INPUT_READ_V4':
                # The bridge's live status is outside the immutable contract.
                # The caller still compares every remaining field to read_input.
                proof=dict(item);proof.pop('continuity',None)
                yield proof
                continue
            if item.get('truncated') is True or item.get('output_truncated') is True:
                continue
            for key in ('output','stdout','text','content','value'):
                if key in item:pending.append((item[key],depth+1))
        elif isinstance(item,list):
            pending.extend((child,depth+1) for child in reversed(item))
        elif isinstance(item,str):
            characters+=len(item)
            require(len(item)<=16*1024*1024 and characters<=64*1024*1024,
                    'research read proof transport limit exceeded')
            body=item.strip()
            if not body:continue
            try:
                parsed=json.loads(body)
            except (ValueError,RecursionError):
                # A clipped JSON wrapper cannot certify an inner receipt.
                if body[0] in '{[\"':continue
                # Command tools may prefix the JSON with execution headers.
                # Decode outer objects once; do not scan inside source payloads.
                cursor=attempts=0
                while True:
                    match=re.compile(r'\{').search(body,cursor)
                    if match is None:break
                    index=match.start()
                    if re.search(r'(?i)(?:warning:\s*)?truncated output',body[:index]):break
                    attempts+=1
                    require(attempts<=1000,'research read proof transport limit exceeded')
                    try:parsed,finish=decoder.raw_decode(body,index)
                    except (ValueError,RecursionError):
                        cursor=index+1
                        continue
                    pending.append((parsed,depth+1))
                    cursor=finish
            else:
                if isinstance(parsed,(dict,list,str)):pending.append((parsed,depth+1))


def _completed_turn(path, agent_id, turn_id):
    """Read actual runtime events, not a model/effort string supplied in a draft."""
    model = effort = None
    session_id = None
    contexts, completed = {}, None
    active_turn = None
    calls, proof_reads, prior_personal_reads = {}, [], []
    for line in Path(path).read_text().splitlines():
        item = json.loads(line)
        payload = item.get('payload', {})
        if item.get('type') == 'session_meta':
            session_id = payload.get('id', payload.get('session_id'))
        elif item.get('type') == 'turn_context':
            settings = payload.get('collaboration_mode', {}).get('settings', {})
            contexts[payload.get('turn_id')] = (payload.get('model'), payload.get('effort', settings.get('reasoning_effort')))
            active_turn = payload.get('turn_id')
        elif item.get('type') == 'response_item':
            if payload.get('type') in ('custom_tool_call', 'function_call'):
                calls[payload.get('call_id')] = payload.get('input', payload.get('arguments', ''))
            elif payload.get('type') in ('custom_tool_call_output', 'function_call_output'):
                call = calls.get(payload.get('call_id'), '')
                if 'research-read' in call and 'strategy_v4.py' in call:
                    for proof in _read_proofs(payload.get('output')):
                        if proof['request_id'] in call:
                            if active_turn == turn_id:
                                proof_reads.append(proof)
                            elif completed is None and proof.get('phase') in ('portfolio', 'candidate'):
                                prior_personal_reads.append(proof)
        elif item.get('type') == 'event_msg' and payload.get('type') == 'task_complete' and payload.get('turn_id') == turn_id:
            completed = payload
            model, effort = contexts.get(turn_id, (None, None))
    require(session_id == agent_id and completed is not None, 'research phase needs an actual completed agent turn')
    require((model, effort) == ('gpt-6-astra', 'max'), 'research must actually run Astra/max')
    text(completed.get('last_agent_message'), 'completed research output')
    require(isinstance(completed.get('started_at'), (int, float)) and isinstance(completed.get('completed_at'), (int, float)), 'research runtime phase timestamps required')
    return dict(completed, proof_reads=proof_reads, prior_personal_reads=prior_personal_reads)


def independent(root, request_id, agent_id, rollout_path, turn_id, additional=None, guard_attempt=None):
    with locked(root):
        req = _load(root, 'requests', request_id)
        _guard_completed(root, req, 'PUBLIC', guard_attempt, rollout_path)
        packet = read(Path(root) / req['independent_input_path'])
        require(digest(packet) == req['independent_input_id'], 'independent input changed')
        _candidate_free(packet)
        turn = _completed_turn(rollout_path, agent_id, turn_id)
        require(turn['started_at'] >= int(instant(req['created_at']).timestamp()), 'independent phase predates this request')
        expected = read_input(root, request_id)
        require(expected in turn['proof_reads'], 'independent runtime did not read this frozen request input')
        require(not any(v['phase'] in ('portfolio', 'candidate') for v in turn['proof_reads'])
                and not turn.get('prior_personal_reads'),
                'independent runtime already read personal or candidate material; continue the existing study or use a fresh public-only context')
        supplement = {}
        if additional is not None:
            context = _review_evidence(root, req, None, additional)
            require(instant(context['knowledge_cutoff']).timestamp() <= turn['completed_at'],
                    'independent supplement is after the actual completed answer')
            supplement['evidence_context'] = context
        return _save(root, 'independent', 'independent_id', {'schema': 'BTC_RESEARCH_INDEPENDENT_V4',
            'request_id': request_id, 'agent_id': agent_id, 'turn_id': turn_id, 'rollout_path': str(Path(rollout_path).resolve()),
            'verification_scope': 'NATIVE_RUNTIME_PHASE_INPUT_READ',
            'input_id': req['independent_input_id'], 'summary': turn['last_agent_message'],
            **_public_result(root, req, turn['last_agent_message'], additional), **supplement,
            'started_at': turn['started_at'], 'completed_at': turn['completed_at']})


def _review_evidence(root, req, candidate, directory):
    """Bind later raw evidence to this review, retaining the original request."""
    from . import evidence
    import market_observation as market
    directory = Path(directory)
    package = evidence.validate(read(directory / 'evidence.json'), directory)
    require(instant(package['cutoff']) <= instant(now()), 'supplementary evidence is after this review')
    archived = Path(root) / 'evidence' / package['evidence_id'][7:]
    require((archived / 'evidence.json').exists(), 'archive supplementary evidence before disclosure')
    require(evidence.validate(read(archived / 'evidence.json'), archived) == package,
            'supplementary evidence differs from its archive')
    original = read(Path(root) / 'evidence' / req['evidence_id'][7:] / 'evidence.json')
    old_sources = {s['id']: s for s in original['sources']}
    require(old_sources.keys() <= {s['id'] for s in package['sources']},
            'supplement cannot omit an existing research source')
    sources, markets = [], []
    for source in package['sources']:
        require(source['id'] not in old_sources or source == old_sources[source['id']],
                'supplement cannot rewrite an existing research source')
        raw_path = archived / 'raw' / (source['raw_hash'][7:] + '.json')
        payload = read(raw_path)
        _candidate_free(payload)
        sources.append({'source': source, 'path': str(raw_path), 'raw_hash': source['raw_hash']})
        if isinstance(payload, dict) and payload.get('schema') in ('BTC_MARKET_EVIDENCE_V1', market.EVIDENCE_SCHEMA):
            market.validate_evidence(payload)
            markets.append(payload)
    require(markets, 'supplement must include the final complete market evidence package')
    current_market = max(markets, key=lambda item: instant(item['cutoff']))
    require(instant(req['market_cutoff']) <= instant(current_market['cutoff']) <= instant(package['cutoff']),
            'supplementary market time is inconsistent')
    require(instant(req['knowledge_cutoff']) <= instant(package['cutoff']), 'supplement cannot move knowledge cutoff backwards')
    clocks = {'market_cutoff': current_market['cutoff'], 'knowledge_cutoff': package['cutoff']}
    if req['issue_key'] == 'BTC_CYCLE' and candidate is not None:
        require(all(instant(candidate['assessment']['temporal_scope'][k]) == instant(v) for k, v in clocks.items()),
                'candidate clocks must match its supplementary evidence')
    return {'evidence_id': package['evidence_id'], 'market_evidence_id': current_market['evidence_id'],
            **clocks, 'sources': sources}


def _result_evidence(req, disclosed):
    context = disclosed.get('evidence_context', req)
    return {k: context[k] for k in ('evidence_id', 'market_evidence_id', 'market_cutoff', 'knowledge_cutoff')}


def _review_facts(root, req, candidate):
    """Freeze changed local facts in the same final review, without restarting it."""
    if req['issue_key'] != 'BTC_CYCLE':
        return None
    version = candidate['assessment']['fact_version']
    if version == req['fact_version']:
        return None
    owners = (read(Path(root) / req['independent_input_path']).get('fact_files') or
              {name: {'path': str(Path(root).parent.parent / name)} for name in req['fact_version']})
    require(set(version) == set(owners), 'candidate must retain the local fact owners')
    files = {}
    for name, owner in owners.items():
        if version[name] is None:
            require(facts(Path(root).parent.parent).get(name) is None, 'unavailable candidate fact changed before final review')
            files[name] = {'path': owner['path'], 'text': None}
        else:
            raw = Path(owner['path']).read_bytes()
            require(digest(raw) == version[name], 'candidate facts changed before final review')
            files[name] = {'path': owner['path'], 'text': raw.decode('utf-8')}
    return {'fact_version': version, 'files': files}


def _result_facts(req, disclosed):
    context = disclosed.get('fact_context')
    if context is None:
        return req['fact_version']
    require(req['issue_key'] == 'BTC_CYCLE', 'fact supplement belongs to the core report')
    exact(context, 'fact_version files', 'final review facts')
    version = disclosed['candidate']['assessment']['fact_version']
    require(context['fact_version'] == version and set(context['files']) == set(req['fact_version']),
            'fact supplement differs from the reviewed candidate')
    for name, item in context['files'].items():
        exact(item, 'path text', 'frozen local fact')
        require((item['text'] is None if version[name] is None else
                 isinstance(item['text'], str) and digest(item['text'].encode('utf-8')) == version[name]),
                'frozen final review fact changed')
    return version


def reveal(root, request_id, independent_id, candidate, additional=None, *, check_only=False):
    from contextlib import nullcontext
    with (nullcontext() if check_only else locked(root)):
        req = _load(root, 'requests', request_id)
        phase = _load(root, 'independent', independent_id)
        require(phase['request_id'] == request_id and phase['input_id'] == req['independent_input_id'], 'independent phase belongs to another request')
        require(phase['completed_at'] <= instant(now()).timestamp(), 'independent phase is not complete yet')
        require(isinstance(candidate, dict) and candidate, 'candidate disclosure must be explicit')
        if req.get('workflow_revision', 4) >= 5:
            market_state.ensure_research_mapping(phase, candidate,
                _companion_report=lambda: _candidate_companion_report(root, phase))
        from . import cycle
        if req['issue_key'] == cycle.ISSUE_KEY:
            cycle.validate_candidate(candidate)
        supplement = _review_evidence(root, req, candidate, additional) if additional else None
        fact_context = _review_facts(root, req, candidate)
        if req['issue_key'] == cycle.ISSUE_KEY and supplement is None:
            require(all(instant(candidate['assessment']['temporal_scope'][k]) == instant(req[k])
                        for k in ('market_cutoff', 'knowledge_cutoff')),
                    'later candidate evidence requires --additional disclosure')
        if req['issue_key'] == cycle.ISSUE_KEY:
            # Resolve every mapping reference before spending a final Astra
            # turn on it. The same check runs on actual disclosure.
            from . import evidence, factors
            from .storage import current
            from .run_flow import refs_in
            from .common import Invalid
            directory = Path(root) / 'evidence' / (supplement or req)['evidence_id'][7:]
            package = evidence.validate(read(directory / 'evidence.json'), directory)
            cycle.validate_candidate(candidate, evidence.forward_resolver(package, directory), root, Path(root).parent.parent,
                                     frozen_public_cutoff=(phase.get('public_evidence_inputs') or phase.get('evidence_context') or req)['knowledge_cutoff'])
            errors = []
            for ref in refs_in(candidate):
                try:
                    evidence.resolve(package, directory, ref)
                except Invalid as exc:
                    errors.append({'reference': ref, 'error': str(exc)})
            try:
                factors.validate(candidate['assessment']['factor_assessment'], package, directory, revision=market_state.revision(candidate['assessment']))
            except Invalid as exc:
                errors.append({'check': 'factor_assessment', 'error': str(exc)})
            pinned = current(root)
            if candidate['assessment'].get('outlook', {}).get('market_assessment'):
                try:
                    prior = market_state.effective(root, pinned['assessment'], pinned['decision_id'])['dispute_zones']
                    market_state.validate_zones(candidate['assessment']['outlook']['dispute_zones'],
                        lambda ref: evidence.resolve(package, directory, ref, verified=True), prior,
                        candidate['assessment']['temporal_scope']['knowledge_cutoff'],
                        revision=req.get('workflow_revision', 5))
                except Invalid as exc:
                    errors.append({'check': 'dispute_zones', 'error': str(exc)})
            require(not errors, 'candidate preflight failed: ' + json.dumps(errors, ensure_ascii=False))
        if check_only:
            return {'valid': True, 'candidate_hash': digest(candidate), 'scope': 'structure_and_bindings_only',
                    'next_action': 'DISCLOSE_TO_ORIGINAL_RESEARCHER_FOR_CONTENT_REVIEW'}
        assignment = ({'review_assignment': {'instructions': cycle.MAPPING_REVIEW,
                       'method_references': cycle.method_references('mapping')}}
                      if req['issue_key'] == cycle.ISSUE_KEY else {})
        return _save(root, 'candidates', 'candidate_id', {'schema': 'BTC_RESEARCH_CANDIDATE_V4', 'request_id': request_id,
            'independent_id': independent_id, 'agent_id': phase['agent_id'], 'candidate': candidate,
            **assignment,
            **({'evidence_context': supplement} if supplement else {}),
            **({'fact_context': fact_context} if fact_context else {}),
            'candidate_hash': digest(candidate), 'revealed_at': now()})


def result(root, spec, guard_attempt=None):
    exact(spec, 'request_id candidate_id agent_id rollout_path turn_id coverage counterevidence unresolved reopen_conditions analysis', 'research result')
    for key in ('coverage', 'counterevidence', 'unresolved', 'reopen_conditions'):
        require(isinstance(spec[key], list), 'research ' + key + ' must be a list')
        for item in spec[key]:
            text(item, 'research ' + key)
    require(spec['coverage'] and spec['reopen_conditions'], 'research result needs coverage and future discriminators')
    with locked(root):
        req = _load(root, 'requests', spec['request_id'])
        _guard_completed(root, req, 'APPROVAL', guard_attempt, spec['rollout_path'])
        disclosed = _load(root, 'candidates', spec['candidate_id'])
        phase = _load(root, 'independent', disclosed['independent_id'])
        _verify_market_amendment(phase, root)
        require(phase.get('verification_scope', 'NATIVE_RUNTIME_PHASE_INPUT_READ') == 'NATIVE_RUNTIME_PHASE_INPUT_READ', 'orchestrated research needs its orchestration result adapter')
        require(disclosed['request_id'] == req['request_id'] == phase['request_id'], 'research phases bind different requests')
        require(spec['agent_id'] == disclosed['agent_id'] == phase['agent_id'], 'research phases must use the same agent')
        require(spec['turn_id'] != phase['turn_id'], 'candidate phase must be a later independent turn')
        require(str(Path(spec['rollout_path']).resolve()) == phase['rollout_path'], 'candidate phase must use the same runtime trace')
        turn = _completed_turn(spec['rollout_path'], spec['agent_id'], spec['turn_id'])
        require(turn['started_at'] >= instant(disclosed['revealed_at']).timestamp(), 'candidate phase started before disclosure')
        require(read_input(root, req['request_id'], 'candidate', spec['candidate_id']) in turn['proof_reads'], 'candidate runtime did not read the disclosed candidate')
        require(spec['analysis'] == turn['last_agent_message'], 'research analysis differs from actual agent output')
        from .cycle import review_output
        cycle_review = review_output(req, disclosed, spec['analysis'], spec)
        return _save(root, 'results', 'result_id', {'schema': 'BTC_RESEARCH_RESULT_V4', **spec, 'issue_id': req['issue_id'],
            **({'cycle_review': cycle_review} if cycle_review is not None else {}),
            'verification_scope': 'NATIVE_RUNTIME_PHASE_INPUT_READ',
            'scope': req['scope'], 'fact_version': _result_facts(req, disclosed), **_result_evidence(req, disclosed),
            'independent_id': phase['independent_id'], 'completed_at': now()})


ORCHESTRATED_SCOPE = 'ORCHESTRATION_DISPATCH_RETURN_AND_MAIN_INPUT_REVIEW'
PUBLIC_ARTIFACT_SCOPES = {
    'PORTFOLIO': 'ORCHESTRATED_PORTFOLIO_PUBLIC_ARTIFACT_BINDING',
    'APPROVAL': 'ORCHESTRATED_APPROVAL_REVISION_PUBLIC_ARTIFACT_BINDING',
}


def _orchestration_lines(path, limit=None):
    """Decode one record at a time, respecting the runtime's byte boundary."""
    with Path(path).open('rb') as stream:
        while limit is None or stream.tell() < limit:
            line = stream.readline() if limit is None else stream.readline(limit - stream.tell())
            if not line:
                break
            yield json.loads(line)


def _orchestration_items(path, limit=None, seen=None):
    """Stream actual paginated history without retaining unrelated trace objects."""
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    require(path not in seen, 'cyclic orchestration history')
    seen.add(path)
    meta = next((v['payload'] for v in _orchestration_lines(path, limit)
                 if v.get('type') == 'session_meta'), {})
    history = meta.get('history_base') if meta.get('history_mode') == 'paginated' else None
    if history:
        parent_id = history.get('thread_id', '')
        require(re.fullmatch(r'[a-f0-9-]{36}', parent_id), 'invalid orchestration history identity')
        base = next((p for p in path.parents if p.name == 'sessions'), path.parent)
        matches = list(base.glob('**/rollout-*' + parent_id + '.jsonl'))
        require(len(matches) == 1, 'actual orchestration history segment missing or ambiguous')
        with matches[0].open() as stream:
            parent_meta = json.loads(next(stream)).get('payload', {})
        require(parent_meta.get('id') == meta.get('id'), 'orchestration history belongs to another session')
        end = history.get('end_byte_offset')
        require(isinstance(end, int) and end > 0 and end <= matches[0].stat().st_size,
                'invalid orchestration history boundary')
        yield from _orchestration_items(matches[0], end, seen)
    yield from _orchestration_lines(path, limit)


def _orchestration_turn(path, dispatch_id, return_id, method, agent_id=None):
    """Read real parent dispatch/return events; do not invent native child turns."""
    dispatch_count = return_count = 0
    dispatched = returned = None
    # The author is established by the unique final. A second streaming pass
    # checks only that author's interval; no unrelated messages are retained.
    for number, item in enumerate(_orchestration_items(path), 1):
        if item.get('type') != 'response_item':
            continue
        payload = item.get('payload', {})
        if payload.get('type') == 'function_call' and payload.get('call_id') == dispatch_id:
            dispatch_count += 1
            dispatch_line, dispatched = number, item
        if payload.get('type') == 'agent_message' and payload.get('id') == return_id:
            return_count += 1
            return_line, returned = number, item
    require(dispatch_count == return_count == 1, 'orchestration needs unique actual dispatch and final return events')
    call, message = dispatched['payload'], returned['payload']
    require(call.get('namespace') == 'collaboration' and call.get('name') == method, 'wrong orchestration dispatch method')
    args = json.loads(call['arguments'])
    author = message.get('author')
    require(isinstance(author, str) and author.startswith('/'), 'orchestration final return has no agent identity')
    if method == 'spawn_agent':
        require(args.get('fork_turns') == 'none', 'independent research must be dispatched without inherited conversation')
        require((args.get('model'), args.get('reasoning_effort')) == ('gpt-6-astra', 'max'), 'orchestration must dispatch Astra/max')
        require(args.get('task_name') == author.rsplit('/', 1)[-1], 'spawned agent differs from the actual final author')
    else:
        require(method == 'followup_task' and agent_id == author, 'candidate research must return from the same agent')
        require(args.get('target') in (author, author.rsplit('/', 1)[-1]), 'candidate follow-up targets a different agent')
    require(dispatch_line < return_line and instant(dispatched['timestamp']) <= instant(returned['timestamp']), 'orchestration return predates dispatch')
    receipt_count, output, continuations = 0, None, []
    for number, item in enumerate(_orchestration_items(path), 1):
        if not dispatch_line < number < return_line or item.get('type') != 'response_item':
            continue
        q = item['payload']
        if q.get('type') == 'function_call_output' and q.get('call_id') == dispatch_id:
            receipt_count += 1
            output = q.get('output')
        if q.get('type') == 'agent_message' and q.get('author') == author:
            prior = '\n'.join(c.get('text', '') for c in q.get('content', []))
            require(not prior.startswith('Message Type: FINAL_ANSWER\n'), 'earlier completed return must be registered before a later phase')
        if q.get('type') == 'function_call' and q.get('name') == 'followup_task':
            intervening = json.loads(q['arguments'])
            if intervening.get('target') in (author, author.rsplit('/', 1)[-1]):
                continuations.append({'call_id': q['call_id'], 'hash': digest(item)})
    require(receipt_count == 1, 'orchestration dispatch has no actual tool receipt')
    try:
        decoded = json.loads(output) if isinstance(output, str) else output
    except ValueError:
        decoded = output
    require(not (isinstance(decoded, dict) and (decoded.get('isError') or decoded.get('error'))), 'orchestration dispatch failed')
    if isinstance(decoded, dict) and 'task_name' in decoded:
        require(decoded['task_name'] == author, 'dispatch receipt identifies a different agent')
    content = '\n'.join(c.get('text', '') for c in message.get('content', []) if c.get('type') in ('input_text', 'text'))
    require(content.startswith('Message Type: FINAL_ANSWER\n') and '\nPayload:\n' in content, 'orchestration message is not an actual final answer')
    require('\nSender: ' + author + '\n' in content, 'orchestration final sender mismatch')
    analysis = content.split('\nPayload:\n', 1)[1]
    text(analysis, 'actual orchestration final analysis')
    return {'agent_id': author, 'dispatch_call_id': dispatch_id, 'return_message_id': return_id,
            'started_at': instant(dispatched['timestamp']).timestamp(), 'completed_at': instant(returned['timestamp']).timestamp(),
            'analysis': analysis, 'dispatch_line': dispatch_line, 'return_line': return_line,
            'dispatch_hash': digest(dispatched), 'return_hash': digest(returned), 'continuations':continuations}


def _main_input_review(value, request_id, input_id, dispatch_id):
    exact(value, 'reviewer_role request_id input_id dispatch_call_id material_review phase_order_review evidence_refs', 'main orchestration input review')
    require(value['reviewer_role'] == 'MAIN' and value['request_id'] == request_id and value['input_id'] == input_id
            and value['dispatch_call_id'] == dispatch_id, 'main input review is not bound to this actual dispatch and frozen input')
    for key in ('material_review', 'phase_order_review'):
        text(value[key], 'main ' + key)
    require(isinstance(value['evidence_refs'], list) and value['evidence_refs'], 'main input review requires concrete actual evidence references')
    for ref in value['evidence_refs']:
        text(ref, 'main input evidence reference')
    return value


def orchestrated_independent(root, request_id, rollout_path, dispatch_call_id, return_message_id, input_review, guard_attempt=None, additional=None):
    """Dispatch and actual final are verified; inner reads remain a declared main review."""
    with locked(root):
        req = _load(root, 'requests', request_id)
        _guard_completed(root, req, 'PUBLIC', guard_attempt, rollout_path)
        read_input(root, request_id)  # Recheck immutable digest and candidate-free structure.
        proof = _orchestration_turn(rollout_path, dispatch_call_id, return_message_id, 'spawn_agent')
        require(proof['started_at'] >= instant(req['created_at']).timestamp(), 'independent dispatch predates the frozen request')
        _main_input_review(input_review, request_id, req['independent_input_id'], dispatch_call_id)
        supplement = {}
        if additional is not None:
            context = _review_evidence(root, req, None, additional)
            require(instant(context['knowledge_cutoff']).timestamp() <= proof['completed_at'],
                    'independent supplement is after the actual completed answer')
            supplement['evidence_context'] = context
        return _save(root, 'independent', 'independent_id', {'schema': 'BTC_RESEARCH_INDEPENDENT_V4',
            'request_id': request_id, 'agent_id': proof['agent_id'], 'rollout_path': str(Path(rollout_path).resolve()),
            'input_id': req['independent_input_id'], 'summary': proof['analysis'],
            **_public_result(root, req, proof['analysis'], additional), **supplement, 'started_at': proof['started_at'],
            'completed_at': proof['completed_at'], 'verification_scope': ORCHESTRATED_SCOPE,
            'dispatch_call_id': dispatch_call_id, 'return_message_id': return_message_id,
            'trace_proof': {k: proof[k] for k in ('dispatch_line', 'return_line', 'dispatch_hash', 'return_hash')},
            'input_review': input_review, 'continuations':proof['continuations'],
            'inner_tool_trace_verified': False, 'os_input_isolation_verified': False})


def orchestrated_result(root, spec, guard_attempt=None):
    exact(spec, 'request_id candidate_id rollout_path dispatch_call_id return_message_id input_review coverage counterevidence unresolved reopen_conditions', 'orchestrated research result')
    for key in ('coverage', 'counterevidence', 'unresolved', 'reopen_conditions'):
        require(isinstance(spec[key], list), 'research ' + key + ' must be a list')
        for item in spec[key]:
            text(item, 'research ' + key)
    require(spec['coverage'] and spec['reopen_conditions'], 'research result needs coverage and future discriminators')
    with locked(root):
        req = _load(root, 'requests', spec['request_id'])
        _guard_completed(root, req, 'APPROVAL', guard_attempt, spec['rollout_path'])
        disclosed = _load(root, 'candidates', spec['candidate_id'])
        phase = _load(root, 'independent', disclosed['independent_id'])
        _verify_market_amendment(phase, root)
        amendment = phase.get('market_amendment')
        continued_public = (isinstance(amendment, dict)
            and amendment.get('source_phase') in PUBLIC_ARTIFACT_SCOPES
            and phase.get('verification_scope') == PUBLIC_ARTIFACT_SCOPES[amendment['source_phase']])
        require(phase.get('verification_scope') == ORCHESTRATED_SCOPE or continued_public,
                'native research must retain native input-read verification')
        require(req['request_id'] == disclosed['request_id'] == phase['request_id'], 'orchestrated phases bind different requests')
        # Recheck PUBLIC through its frozen proof. APPROVAL keeps its own
        # completed claim's byte-bound proof; neither phase needs a rewritten
        # file containing both histories. Paths alone are not provenance.
        first = _orchestration_turn(phase['rollout_path'], phase['dispatch_call_id'], phase['return_message_id'], 'spawn_agent')
        require(first['dispatch_hash'] == phase['trace_proof']['dispatch_hash'] and first['return_hash'] == phase['trace_proof']['return_hash'], 'independent orchestration proof changed')
        if 'continuations' in phase:
            require(first['continuations'] == phase['continuations'], 'independent phase continuation trace changed')
        proof = _orchestration_turn(spec['rollout_path'], spec['dispatch_call_id'], spec['return_message_id'], 'followup_task', phase['agent_id'])
        require(proof['started_at'] >= instant(disclosed['revealed_at']).timestamp(), 'candidate follow-up predates candidate disclosure')
        read_input(root, req['request_id'], 'candidate', spec['candidate_id'])
        _main_input_review(spec['input_review'], req['request_id'], spec['candidate_id'], spec['dispatch_call_id'])
        from .cycle import review_output
        cycle_review = review_output(req, disclosed, proof['analysis'], spec)
        return _save(root, 'results', 'result_id', {'schema': 'BTC_RESEARCH_RESULT_V4', **spec,
            **({'cycle_review': cycle_review} if cycle_review is not None else {}),
            'issue_id': req['issue_id'], 'agent_id': phase['agent_id'], 'analysis': proof['analysis'],
            'scope': req['scope'], 'fact_version': _result_facts(req, disclosed), **_result_evidence(req, disclosed),
            'independent_id': phase['independent_id'], 'verification_scope': ORCHESTRATED_SCOPE,
            'trace_proof': {k: proof[k] for k in ('dispatch_line', 'return_line', 'dispatch_hash', 'return_hash')},
            'inner_tool_trace_verified': False, 'os_input_isolation_verified': False, 'completed_at': now()})


def validate_absorptions(root, draft):
    values = draft.get('research_absorptions')
    require(isinstance(values, list), 'main assessment must declare research absorptions, including none')
    seen = set()
    for value in values:
        keys = 'issue_id request_id result_id decision rationale assessment_refs'
        exact(value, keys + (' applicability' if 'applicability' in value else ''), 'research absorption')
        require(value['decision'] in ('ADOPT', 'PARTIAL', 'REJECT'), 'main must decide how to absorb research')
        text(value['rationale'], 'main research disposition')
        require(value['result_id'] not in seen, 'same research result absorbed twice')
        seen.add(value['result_id'])
        saved = _load(root, 'results', value['result_id'])
        require(saved['request_id'] == value['request_id'] and saved['issue_id'] == value['issue_id'], 'absorption binds unrelated research')
        require(instant(saved['market_cutoff']) <= instant(draft['temporal_scope']['market_cutoff']), 'research uses a future market window')
        require(instant(saved['knowledge_cutoff']) <= instant(draft['temporal_scope']['knowledge_cutoff']), 'research uses evidence after the main knowledge cutoff')
        require(isinstance(value['assessment_refs'], list) and value['assessment_refs'], 'absorption must identify actual main conclusions')
        for path in value['assessment_refs']:
            require(any(path == scope or path.startswith(scope + '/') for scope in saved['scope']), 'research absorption is outside its question scope')
            pointer(draft, path)
        if saved['fact_version'] != draft['fact_version']:
            require('applicability' in value, 'changed local facts need a fresh main applicability assessment')
        if 'applicability' in value:
            check = value['applicability']
            exact(check, 'fact_version evidence_id explanation', 'research applicability')
            require(check['fact_version'] == draft['fact_version'] and check['evidence_id'] == draft['evidence_id'], 'research applicability is stale')
            text(check['explanation'], 'why prior research remains applicable or is rejected')
    return values


PUBLIC_RESULT_REF_SCHEMA='BTC_RESEARCH_PUBLIC_RESULT_REF_V1'


def _public_result_reference(analysis):
    from .cycle import parse_review
    value=parse_review(analysis)
    if value.get('schema')!=PUBLIC_RESULT_REF_SCHEMA:
        return None
    exact(value,'schema path content_hash','public result file reference')
    require(isinstance(value['content_hash'],str) and
            re.fullmatch(r'sha256:[0-9a-f]{64}',value['content_hash']),
            'public result reference needs an actual byte hash')
    expected='research/author_outputs/'+value['content_hash'][7:]+'.json'
    require(value['path']==expected,'public result reference must use its root-relative hash filename')
    return value


def _referenced_public_result(root, reference):
    root=Path(root).resolve()
    directory=root/'research/author_outputs'
    require(directory.resolve()==directory and not directory.is_symlink(),
            'public result reference directory escapes its owner')
    path=root/reference['path']
    require(not path.is_symlink() and path.resolve()==directory/path.name and path.is_file(),
            'public result reference file escapes its owner or is missing')
    require(path.stat().st_size<=16*1024*1024,'public result reference is too large')
    body=path.read_bytes()
    require(digest(body)==reference['content_hash'],'public result reference bytes changed')
    try:return body.decode('utf-8')
    except UnicodeDecodeError:
        require(False,'public result reference must contain UTF-8 JSON')


def _public_result(root, req, analysis, directory=None, *, prior_phase=None):
    if req.get('workflow_revision', 4) < 5:
        return {}
    from . import evidence, factors
    rev = req.get('workflow_revision', 5)
    reference=_public_result_reference(analysis)
    if reference is not None:
        require(rev>=9,'public result file references require revision 9')
        analysis=_referenced_public_result(root,reference)
    result = market_state.parse_result(analysis, rev)
    directory = Path(directory) if directory is not None else Path(root) / 'evidence' / req['evidence_id'][7:]
    package = evidence.validate(read(directory / 'evidence.json'), directory)
    resolve = evidence.forward_resolver(package, directory)
    packet = read(Path(root) / req['independent_input_path'])
    historical = packet.get('historical_hypotheses', packet)
    prior_result = (prior_phase or {}).get('market_result')
    prior_market = (prior_result['outlook']['market_assessment'] if prior_result else
                    ((historical.get('historical_anchor') or {}).get('outlook') or {}).get('market_assessment'))
    market_state.validate_assessment(result['outlook']['market_assessment'], resolve, package['cutoff'], rev, prior_market, lifecycle_root=root)
    previous = prior_result['outlook']['dispute_zones'] if prior_result else historical.get('historical_dispute_zones', [])
    market_state.validate_zones(result['outlook']['dispute_zones'], resolve, prior=previous, cutoff=package['cutoff'], revision=rev)
    from .assessment import validate_outlook
    validate_outlook(result['outlook'], rev, resolve, package['cutoff'], prior_market, root)
    factors.validate(result['factor_assessment'], package, directory, revision=rev)
    for ref in result['outlook']['refs']:
        resolve(ref)
    value={'market_result':result,'market_result_hash':digest(result)}
    if reference is not None:value['public_result_reference']=reference
    return value


def _author_artifact_path(root, reference):
    """Validate the supplied root spelling and children without reading a body."""
    root = Path(root)
    require(isinstance(reference, dict) and isinstance(reference.get('path'), str)
            and isinstance(reference.get('sha256'),str)
            and re.fullmatch(r'sha256:[0-9a-f]{64}', reference['sha256']),
            'author artifact requires its actual path and byte hash')
    path = Path(reference['path'])
    if not path.is_absolute(): path = root / path
    require('..' not in path.parts, 'author artifact must not traverse parent paths')
    # Keep the actual spelling: a single external file symlink into this root
    # is not a root alias. Logical project roots and physical roots both work.
    try:
        anchors = [p for p in path.parents if p.resolve() == root.resolve()]
    except (OSError, RuntimeError):
        require(False, 'author artifact path cannot resolve safely')
    require(anchors, 'author artifact escapes research root')
    anchor = anchors[-1]
    cursor = anchor
    for part in path.relative_to(anchor).parts:
        cursor = cursor / part
        require(not cursor.is_symlink(), 'author artifact must not use child symlinks')
    return path


def _author_artifact(root, reference, archived=None):
    root = Path(root)
    path = _author_artifact_path(root, reference)
    if archived is not None:
        path = root / archived['path']
        require(archived == {'schema':PUBLIC_RESULT_REF_SCHEMA,
                            'path':'research/author_outputs/' + reference['sha256'][7:] + '.json',
                            'content_hash':reference['sha256']}, 'archived author artifact identity changed')
        return _referenced_public_result(root, archived).encode('utf-8')
    require(path.is_file() and 0 < path.stat().st_size <= 16*1024*1024, 'author artifact missing or too large')
    body = path.read_bytes()
    require(digest(body) == reference['sha256'], 'author artifact bytes changed')
    return body


def _public_artifact_object(body):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        require(False, 'public artifact must contain UTF-8 JSON')
    require(isinstance(value,dict), 'public artifact must contain a JSON object')
    return value


def _portfolio_public_files(root, phase, proof, archived=None):
    """Extract only the explicitly signed public subproducts of a real FINAL."""
    from .cycle import parse_review
    final = parse_review(proof['analysis'])
    require(final.get('status') == 'AUTHOR_WORK_COMPLETED_READY_FOR_SOL_MAPPING',
            'PORTFOLIO public update needs the actual completed manifest FINAL, not approval')
    correction = final.get('public_correction', {})
    require(isinstance(correction,dict), 'actual FINAL public correction declaration required')
    require(correction.get('needed') is True and correction.get('original_public_modified') is False
            and correction.get('proposal_formally_registered') is False
            and final.get('formal_writes_or_delivery') is False,
            'actual FINAL must explicitly propose a later unregistered public update')
    require(isinstance(final.get('manifest'),dict) and isinstance(final.get('artifacts'),dict)
            and isinstance(final['artifacts'].get('public_proposal'),dict), 'actual FINAL public artifact references required')
    references = {'manifest': {k:final.get('manifest', {}).get(k) for k in ('path','sha256')},
                  'public_proposal': {k:final.get('artifacts', {}).get('public_proposal', {}).get(k) for k in ('path','sha256')}}
    if archived is not None:
        require(all(references[k] == archived['original_refs'][k] for k in references), 'actual manifest FINAL binding changed')
    body = _author_artifact(root, references['manifest'], (archived or {}).get('immutable_refs', {}).get('manifest'))
    manifest = _public_artifact_object(body)
    require(manifest.get('schema') == 'BTC_AUTHOR_DELIVERABLE_MANIFEST_V1'
            and manifest.get('status') == final['status'], 'author manifest contract invalid')
    require(isinstance(manifest.get('completed_at'),str), 'author manifest completion clock required')
    author_completed = instant(manifest['completed_at']).timestamp()
    require(proof['started_at'] <= author_completed <= proof['completed_at'], 'author manifest completion exceeds actual turn')
    entries = manifest.get('artifacts')
    require(isinstance(entries,list) and all(isinstance(x,dict) and isinstance(x.get('name'),str) for x in entries), 'manifest artifact entries required')
    require(len({x.get('name') for x in entries}) == len(entries), 'manifest artifact names must be unique')
    active = manifest.get('active_public_proposal')
    selected = next((x for x in entries if x.get('name') == active), None)
    review = next((x for x in entries if x.get('name') == 'public-supplement-review.json'), None)
    require(selected is not None and review is not None
            and {k:selected.get(k) for k in ('path','sha256')} == references['public_proposal']
            and Path(selected['path']).name == active, 'FINAL public artifact differs from active manifest entry')
    references['public_input_review'] = {k:review.get(k) for k in ('path','sha256')}
    bodies = {'manifest':body}
    for key, entry in (('public_proposal',selected),('public_input_review',review)):
        if archived is not None:
            require(references[key] == archived['original_refs'][key], 'archived public artifact binding changed')
        bodies[key] = _author_artifact(root, references[key], (archived or {}).get('immutable_refs', {}).get(key))
        require(type(entry.get('bytes')) is int and len(bodies[key]) == entry['bytes'], 'manifest public artifact size changed')
    public_input = _public_artifact_object(bodies['public_input_review'])
    origin = public_input.get('original', {})
    require(isinstance(origin,dict), 'public input review original owner required')
    require(origin.get('request_id') == phase['request_id'] and origin.get('independent_id') == (phase['parent_independent_id'] if archived is not None else phase['independent_id']),
            'public input review belongs to another request or original public owner')
    scope = public_input.get('later_scope')
    require(isinstance(scope,dict) and all(isinstance(scope.get(k),str) for k in ('knowledge_cutoff','source_manifest_frozen_at'))
            and isinstance(public_input.get('formed_at'),str), 'public input review needs its actual clocks')
    cutoff = scope['knowledge_cutoff']
    require(instant(scope['source_manifest_frozen_at']) == instant(cutoff)
            and instant(cutoff) <= instant(public_input['formed_at'])
            and instant(public_input['formed_at']).timestamp() <= author_completed,
            'public input clocks exceed the actual author work')
    ids = scope.get('evidence_ids')
    require(isinstance(ids,list) and ids
            and all(isinstance(x,str) and re.fullmatch(r'sha256:[0-9a-f]{64}',x) for x in ids)
            and len(ids)==len(set(ids)),
            'public input review needs exact original archived bundle identities')
    metadata = {'original_refs':references, 'author_completed_at':manifest['completed_at'],
                'knowledge_cutoff':cutoff, 'evidence_ids':ids}
    return metadata, bodies


def _approval_public_files(root, phase, proof, archived=None):
    """Consume a rejected review's signed revision, never approval of that revision."""
    from .cycle import parse_review
    from .closed_loop import public_hash
    final = parse_review(proof['analysis'])
    require(final.get('approved') is False and isinstance(final.get('unresolved'), list),
            'APPROVAL public revision requires the actual rejected candidate review')
    # The real review format binds its deliverable by path and byte digest in
    # unresolved text. Do not accept a MAIN-supplied manifest or another phase.
    refs = re.findall(r'(/[^\n（）]+\.json)（(sha256:[0-9a-f]{64})）',
                      '\n'.join(x for x in final['unresolved'] if isinstance(x,str)))
    require(len(refs) == 1, 'rejected FINAL must bind exactly one revision manifest')
    references = {'manifest':dict(zip(('path','sha256'),refs[0]))}
    def artifact(key):
        if archived is not None:
            require(references[key] == archived['original_refs'].get(key), 'rejected FINAL artifact binding changed')
        return _author_artifact(root,references[key],(archived or {}).get('immutable_refs',{}).get(key))
    bodies = {'manifest':artifact('manifest')}
    manifest = _public_artifact_object(bodies['manifest'])
    require(manifest.get('status') == 'APPROVAL_COMPLETE_REJECTED_WITH_EXACT_NONFORMAL_REVISION'
            and manifest.get('approved') is False and manifest.get('later_public_update_required') is True
            and manifest.get('historical_public_correction_required') is False
            and manifest.get('request_id') == phase['request_id'], 'rejected revision manifest contract invalid')
    candidate = _load(root,'candidates',manifest.get('candidate_id'))
    require(candidate['request_id'] == phase['request_id'] and candidate['independent_id'] ==
            (phase['parent_independent_id'] if archived is not None else phase['independent_id'])
            and candidate['candidate_hash'] == manifest.get('candidate_hash') == final.get('candidate_hash')
            and manifest.get('original_report_hash') == final.get('report_hash') ==
                digest(candidate['candidate']['assessment']['conclusion']['narrative']),
            'rejected revision belongs to another exact candidate or public owner')
    completed = manifest.get('created_at')
    require(isinstance(completed,str) and proof['started_at'] <= instant(completed).timestamp() <= proof['completed_at'],
            'revision manifest completion exceeds actual author turn')
    entries = manifest.get('artifacts')
    require(isinstance(entries,dict), 'revision manifest artifact entries required')
    for key,name in (('public_proposal','public-update-proposal.json'),('public_input_review','public-supplement-review.json')):
        entry = entries.get(name)
        require(isinstance(entry,dict) and isinstance(entry.get('sha256'),str)
                and re.fullmatch('[0-9a-f]{64}',entry['sha256']) and type(entry.get('bytes')) is int,
                'revision public artifact identity required')
        references[key] = {'path':entry.get('path'),'sha256':'sha256:'+entry['sha256']}
        bodies[key] = artifact(key)
        require(Path(references[key]['path']).name == name and len(bodies[key]) == entry['bytes'],
                'revision public artifact name or size changed')
    review = _public_artifact_object(bodies['public_input_review'])
    result = market_state.parse_result(bodies['public_proposal'].decode('utf-8'))
    require(review.get('original_candidate_unchanged') == candidate['candidate_hash']
            and review.get('exact_candidate_approved') is False and review.get('current_public_update_required') is True
            and review.get('historical_public_correction_required') is False
            and isinstance(manifest.get('public_update_content_hash'),str)
            and re.fullmatch('sha256:[0-9a-f]{64}',manifest['public_update_content_hash'])
            and review.get('public_proposal_content_hash') == manifest['public_update_content_hash'] == public_hash(result),
            'public revision review differs from actual rejected candidate and proposal')
    cutoff = manifest.get('actual_21z_read_at')
    require(isinstance(cutoff,str) and instant(cutoff) <= instant(review['formed_at']) <= instant(completed),
            'public revision needs its actual input-read clock before author formation')
    sources = review.get('sources')
    require(isinstance(sources,list) and sources and all(isinstance(x,dict) for x in sources),
            'revision review needs actual public raw source bindings')
    market_sources = []
    import market_observation as market
    for index,source in enumerate(sources):
        key = 'market_input_'+str(index)
        references[key] = {'path':source.get('path'),'sha256':'sha256:'+str(source.get('bytes_sha256'))}
        bodies[key] = artifact(key)
        raw = _public_artifact_object(bodies[key]);market.validate_evidence(raw)
        require(not raw.get('normalization'), 'revision raw input cannot upgrade a derived replay to PUBLIC_RAW')
        retrieved = max((x['retrieved_at'] for x in raw['retrievals'].values()),key=instant)
        require(raw['evidence_id'] == source.get('evidence_id') and raw['cutoff'] == source.get('cutoff')
                and retrieved == source.get('last_retrieved_at') and instant(retrieved) <= instant(cutoff),
                'revision public raw identity or knowledge clock changed')
        if source.get('actual_read_at') is not None:
            require(instant(source['actual_read_at']) == instant(cutoff), 'revision actual raw read clock changed')
        market_sources.append({'id':'market_'+raw['evidence_id'][7:27], 'origin':'BINANCE',
            'url':'https://data-api.binance.vision/api/v3/klines','trust':'PUBLIC_RAW',
            'published_at':raw['cutoff'],'retrieved_at':retrieved,
            'start':min((x['requested_start'] for x in raw['datasets'].values()),key=instant),
            'end':raw['cutoff'],'raw_hash':digest(bodies[key]), 'artifact_key':key})
    inputs = phase.get('public_evidence_inputs') or {}
    return {'original_refs':references,'author_completed_at':completed,'knowledge_cutoff':cutoff,
            'evidence_ids':inputs.get('evidence_ids',[]),'market_sources':market_sources,
            'public_semantic_hash':public_hash(result),
            'parsed_public_content_hash':digest(result)},bodies


def _public_manifest_files(root, phase, proof, source_phase, archived=None):
    return (_approval_public_files if source_phase == 'APPROVAL' else _portfolio_public_files)(root,phase,proof,archived)


def _portfolio_public_view(root, req, metadata, directory, bodies=None):
    """Ephemeral validation union; never backdate a newly frozen author input."""
    from . import evidence
    cutoff = metadata['knowledge_cutoff']
    sources, files = {}, {}
    for identity in dict.fromkeys([req['evidence_id'], *metadata['evidence_ids']]):
        path = Path(root) / 'evidence' / identity[7:]
        require((path/'evidence.json').is_file() and not path.is_symlink()
                and path.resolve() == Path(root).resolve()/'evidence'/identity[7:],
                'original archived public input bundle missing or escaped')
        package = evidence.validate(read(path/'evidence.json'), path)
        require(package['evidence_id'] == identity and instant(package['cutoff']) <= instant(cutoff),
                'public source bundle was frozen after the author input cutoff')
        for source in package['sources']:
            require(instant(source['retrieved_at']) <= instant(cutoff), 'public source was obtained after the author input cutoff')
            require(source['id'] not in sources or sources[source['id']] == source,
                    'public input bundles rewrite an original source')
            raw = path/'raw'/(source['raw_hash'][7:]+'.json')
            _candidate_free(read(raw))
            sources[source['id']] = source
            files[source['id']] = str(raw)
    for descriptor in metadata.get('market_sources',[]):
        source = {k:v for k,v in descriptor.items() if k != 'artifact_key'}
        require(source['id'] not in sources or sources[source['id']] == source, 'revision public source collision')
        raw = Path(directory).parent/(descriptor['artifact_key']+'.json')
        create(raw,bodies[descriptor['artifact_key']])
        sources[source['id']] = source
        files[source['id']] = str(raw)
    spec = {'parent_id':None,'cutoff':cutoff,'sources':list(sources.values()),
            'measurements':[],'verified_facts':[],'candidates':[],'gaps':[],
            'handoff':{'previous_hour':None,'successful_deep':None,'changes_since_hour':[],
                       'changes_since_deep':[],'active_hypotheses':[],'open_questions':[]}}
    return evidence.freeze(spec, files, directory)


def _archive_public_files(root, metadata, bodies):
    root = Path(root).resolve()
    directory = root/'research/author_outputs'
    require(directory.resolve()==directory and not directory.is_symlink(), 'public artifact archive escapes research root')
    immutable = {}
    for key, body in bodies.items():
        identity = digest(body)
        ref = {'schema':PUBLIC_RESULT_REF_SCHEMA,'path':'research/author_outputs/'+identity[7:]+'.json','content_hash':identity}
        path = Path(root)/ref['path']
        require(not path.is_symlink(), 'archived public artifact must not be a symlink')
        if path.exists(): require(path.read_bytes()==body, 'archived public artifact collision')
        else: create(path,body)
        immutable[key] = ref
    return dict(metadata,immutable_refs=immutable)


def _verify_market_amendment(phase, root=None):
    amendment = phase.get('market_amendment')
    if amendment:
        if amendment.get('turn_id'):
            proof = _completed_turn(amendment['rollout_path'], phase['agent_id'], amendment['turn_id'])
            analysis = proof['last_agent_message']
            require(digest(analysis) == amendment['analysis_hash'], 'public market amendment trace changed')
        else:
            proof = _orchestration_turn(amendment['rollout_path'], amendment['dispatch_call_id'],
                amendment['return_message_id'], 'followup_task', phase['agent_id'])
            require(proof['dispatch_hash'] == amendment['dispatch_hash'] and proof['return_hash'] == amendment['return_hash'],
                    'public market amendment trace changed')
            analysis = proof['analysis']
        if amendment.get('source_phase') in PUBLIC_ARTIFACT_SCOPES:
            import model_daily
            require(amendment.get('guard_attempt') and amendment.get('guard_proof_hash') == model_daily.sha256(amendment['rollout_path']),
                    'public artifact original completion proof binding changed')
            require(root is not None, 'public artifact amendment readback needs its original root')
            key = amendment['source_phase'].lower()+'_public'
            metadata, bodies = _public_manifest_files(root, phase, proof, amendment['source_phase'], amendment[key])
            require(all(metadata[k] == amendment[key][k] for k in metadata), 'public input metadata changed')
            require(phase.get('public_evidence_inputs') == {k:metadata[k] for k in ('knowledge_cutoff','evidence_ids')},
                    'public evidence clocks or original bundle identities changed')
            require(market_state.parse_result(bodies['public_proposal'].decode('utf-8')) == phase['market_result'],
                    'archived public artifact result changed')
            return proof
        reference=_public_result_reference(analysis)
        if reference is None:
            require(market_state.parse_result(analysis)==phase['market_result'],'public amendment output changed')
        else:
            # The initial acceptance checked bytes and all validators. The
            # immutable Owner contains the complete result; later readback
            # binds its original final reference without reopening the file.
            require(reference==phase.get('public_result_reference'),'public amendment result reference changed')


def _candidate_companion_report(root, phase):
    """Derive an opaque full-report identity from the verified existing FINAL.

    No private file is read or archived here. The candidate already holds its
    private narrative; its exact bytes are compared only at candidate review.
    """
    amendment = phase.get('market_amendment') or {}
    if amendment.get('source_phase') not in PUBLIC_ARTIFACT_SCOPES:
        return None
    proof = _verify_market_amendment(phase, root)
    from .cycle import parse_review
    final = parse_review(proof['analysis'])
    source_phase = amendment['source_phase']
    archive = amendment[source_phase.lower()+'_public']
    manifest_ref = archive['immutable_refs']['manifest']
    manifest = _public_artifact_object(_referenced_public_result(root,manifest_ref).encode('utf-8'))
    if source_phase == 'APPROVAL':
        entry = manifest['artifacts'].get('full-report.revised.zh.md')
        require(isinstance(entry,dict) and manifest.get('proposed_report_bytes_sha256') == entry.get('sha256'),
                'rejected revision full report binding changed')
        reference = {'path':entry.get('path'),'sha256':'sha256:'+str(entry.get('sha256'))}
        path = _author_artifact_path(root,reference)
        require(path.name == 'full-report.revised.zh.md' and type(entry.get('bytes')) is int and entry['bytes'] > 0,
                'rejected revision full report identity required')
        return {'sha256':reference['sha256'],'bytes':entry['bytes']}
    reference = final.get('artifacts', {}).get('full_report')
    if reference is None:
        return None
    path = _author_artifact_path(root, reference)
    active = manifest.get('active_full_report')
    entry = next((x for x in manifest['artifacts'] if x['name']==active),None)
    require(entry is not None and isinstance(entry.get('path'),str) and Path(entry['path']).name==active
            and entry.get('path')==reference['path'] and entry.get('sha256')==reference['sha256']
            and path.name==active and type(entry.get('bytes')) is int and entry['bytes']>0,
            'full report is not bound to the same actual FINAL and active manifest entry')
    return {'sha256':reference['sha256'],'bytes':entry['bytes']}


def amend_market(root, independent_id, rollout_path, dispatch_call_id=None, return_message_id=None, additional=None, turn_id=None, guard_attempt=None, *, source_phase='PUBLIC_CORRECTION'):
    """Continue the same research with an actual public-evidence correction.

    Original independent provenance stays intact; this is explicitly a later
    amendment, not a claim of a new blind analysis after portfolio disclosure.
    """
    with locked(root):
        phase = _load(root, 'independent', independent_id)
        req = _load(root, 'requests', phase['request_id'])
        require(source_phase in ('PUBLIC_CORRECTION',*PUBLIC_ARTIFACT_SCOPES), 'unsupported public amendment source phase')
        manifest_phase = source_phase in PUBLIC_ARTIFACT_SCOPES
        require(not manifest_phase or (turn_id is None and additional is None),
                'manifest amendment requires actual orchestrated FINAL and its original input bundles')
        _guard_completed(root, req, source_phase, guard_attempt, rollout_path)
        require(req.get('workflow_revision', 4) >= 5, 'public amendment requires revision 5')
        require(not manifest_phase or req.get('workflow_revision',4) >= 9,
                'manifest public updates require revision 9')
        if turn_id:
            require(dispatch_call_id is None and return_message_id is None, 'choose native turn or orchestrated amendment')
            proof = _completed_turn(rollout_path, phase['agent_id'], turn_id)
            proof['analysis'] = proof['last_agent_message']
            amendment = {'turn_id': turn_id, 'analysis_hash': digest(proof['analysis'])}
        else:
            require(dispatch_call_id is not None and return_message_id is not None, 'actual amendment phase required')
            proof = _orchestration_turn(rollout_path, dispatch_call_id, return_message_id, 'followup_task', phase['agent_id'])
            amendment = {'dispatch_call_id': dispatch_call_id, 'return_message_id': return_message_id,
                         'dispatch_hash': proof['dispatch_hash'], 'return_hash': proof['return_hash']}
        require(proof['started_at'] >= phase['completed_at'], 'amendment predates the independent result')
        supplement = {}
        if additional is not None:
            context = _review_evidence(root, req, None, additional)
            require(instant(context['knowledge_cutoff']).timestamp() <= proof['completed_at'],
                    'public correction supplement is after the actual completed answer')
            supplement['evidence_context'] = context
        if manifest_phase:
            from tempfile import TemporaryDirectory
            metadata, bodies = _public_manifest_files(root, phase, proof, source_phase)
            with TemporaryDirectory(prefix='btc-public-validation-view-') as temp:
                directory = Path(temp)/'view'
                _portfolio_public_view(root, req, metadata, directory, bodies)
                result = _public_result(root, req, bodies['public_proposal'].decode('utf-8'), directory, prior_phase=phase)
            import model_daily
            amendment.update(source_phase=source_phase, guard_attempt=guard_attempt,
                             guard_proof_hash=model_daily.sha256(rollout_path))
            supplement['public_evidence_inputs'] = {k:metadata[k] for k in ('knowledge_cutoff','evidence_ids')}
            supplement.update(verification_scope=PUBLIC_ARTIFACT_SCOPES[source_phase],
                              inner_tool_trace_verified=False, os_input_isolation_verified=False)
        else:
            result = _public_result(root, req, proof['analysis'], additional, prior_phase=phase)
        market_state.validate_zones(result['market_result']['outlook']['dispute_zones'], lambda ref: None,
            phase['market_result']['outlook']['dispute_zones'], revision=req.get('workflow_revision', 5))
        if manifest_phase:
            amendment[source_phase.lower()+'_public'] = _archive_public_files(root,metadata,bodies)
            result['public_result_reference'] = amendment[source_phase.lower()+'_public']['immutable_refs']['public_proposal']
        updated = {k: v for k, v in phase.items() if k != 'independent_id'}
        updated.pop('public_result_reference',None)
        updated.pop('public_evidence_inputs',None)
        # This later immutable public owner supplies the first real freeze for
        # new claims. Existing claim registrations keep their original owner
        # and clock; an amendment cannot backdate new standards to that clock.
        updated.pop('lifecycle_frozen_at', None)
        updated.update(result, **supplement, parent_independent_id=independent_id,
            independence_status=('DISCLOSED_SAME_AUTHOR_PUBLIC_UPDATE_FROM_'+source_phase if manifest_phase
                                 else 'CONTINUED_PUBLIC_CORRECTION'),
            initial_completed_at=phase.get('initial_completed_at', phase['completed_at']),
            completed_at=proof['completed_at'], market_amendment={
            'rollout_path': str(Path(rollout_path).resolve()), **amendment,
            'completed_at': proof['completed_at']})
        # A lost reply must not give the same actual author return a second
        # lifecycle freeze or another public owner. Revalidate its artifacts
        # above, then reuse the exact already-saved result.
        for path in (Path(root)/'research/independent').glob('*.json'):
            saved = _load(root,'independent','sha256:'+path.stem)
            if (saved.get('parent_independent_id') == independent_id
                    and saved.get('market_amendment') == updated['market_amendment']):
                require({k:v for k,v in saved.items() if k not in ('independent_id','lifecycle_frozen_at')} == updated,
                        'same actual public amendment cannot change its saved content')
                return saved
        return _save(root, 'independent', 'independent_id', updated)
