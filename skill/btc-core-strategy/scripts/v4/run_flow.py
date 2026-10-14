"""Thin, resumable adapters over the existing BTC owners; no model or transport calls."""
from . import market_state
from copy import deepcopy
import json
import os
from pathlib import Path

import market_observation as market
from . import evidence, main_flow, reporting, report_care, sensing, storage, cycle, material_reads, authored_report
from .common import create, digest, exact, fact_inputs, facts, instant, locked, now, pointer, read, require, text


def refs_in(value, root=None):
    """Retain the actual reference closure, never an entire parent evidence chain."""
    found = {}; visited = set()
    def walk(v):
        if isinstance(v, dict):
            if root is not None:
                from .forward_lifecycle import registration
                for key in ('claim_hash','prior_claim_hash'):
                    identity = v.get(key)
                    if identity and identity not in visited:
                        visited.add(identity)
                        original = registration(root, identity)
                        if original: walk(original['claim'])
            if set(('source_id', 'pointer', 'value_hash')) <= set(v):
                ref = {k: v[k] for k in ('source_id', 'pointer', 'value_hash')}
                found[digest(ref)] = ref
            else:
                for item in v.values(): walk(item)
        elif isinstance(v, list):
            for item in v: walk(item)
    walk(value)
    return list(found.values())


def save(path, value):
    """Immutable local work products can be retried only with identical contents."""
    path = Path(path)
    if path.exists():
        actual = path.read_bytes() if isinstance(value, bytes) else read(path)
        require(actual == value, 'run artifact already exists with different contents: ' + path.name)
    else:
        create(path, value)
    return str(path)


def ref_index(package, directory, raw):
    result = []
    for source in package['sources']:
        path = Path(directory) / 'raw' / (source['raw_hash'][7:] + '.json')
        value = read(path)
        item = {k: source[k] for k in ('id', 'origin', 'url', 'trust', 'published_at', 'retrieved_at', 'start', 'end')}
        item['raw_path'] = str(path)
        # Role comes from this frozen package, not recency or source trust.
        item['input_role'] = ('CURRENT_MARKET' if value == raw else
            'CURRENT_COLLECTOR' if source['id'] == (package.get('collector') or {}).get('source_id')
            else 'SUPPORTING_EVIDENCE')
        item['refs'] = []
        paths = []
        if value == raw:
            paths = ['/datasets/' + key for key in raw['datasets']] + ['/windows/' + key for key in raw['windows']]
        elif isinstance(value, dict) and value.get('schema') == 'MULTI_ASSET_AUXILIARY_V1':
            for asset, row in value['assets'].items():
                base = '/assets/' + asset
                paths.append(base)
                paths += [base + '/datasets/' + key for key in row.get('datasets', {})]
                paths += [base + '/sources/' + key for key in row.get('sources', {})]
        elif isinstance(value, dict):
            paths = ['/' + key.replace('~', '~0').replace('/', '~1') for key in value]
        for ptr in paths:
            item['refs'].append({'source_id': source['id'], 'pointer': ptr, 'value_hash': digest(pointer(value, ptr))})
        result.append(item)
    return result


def market_brief(raw):
    windows = deepcopy(raw['windows'])
    for span, datasets in windows.items():
        for dataset, window in datasets.items():
            measurements = window.get('measurements') or {}
            if isinstance(measurements.get('rows'), list):
                rows = measurements.pop('rows')
                window['raw_rows'] = {'count':len(rows), 'pointer':'/windows/'+span+'/'+dataset+'/measurements/rows'}
    datasets = {}
    for key, ds in raw['datasets'].items():
        latest = ds['rows'][-1] if ds['rows'] else None
        price_kind = ds['kind'] in ('CANDLES', 'MARK', 'INDEX')
        all_closed = bool(price_kind and ds['rows'] and all(
            market.timestamp(row['end']) <= market.timestamp(raw['cutoff'])
            for row in ds['rows']))
        require(not price_kind or not ds['rows'] or all_closed,
                'retained market price row exceeds the frozen cutoff')
        closed = bool(latest and all_closed)
        quote_flow = bool(closed and latest.get('quote_volume') is not None
                          and latest.get('taker_buy_quote') is not None)
        datasets[key] = {**{k:ds.get(k) for k in ('kind','quote_unit','market','instrument','coverage','issues')},
            'latest':latest,
            'issue_scope': ({'UNCLOSED_OR_FUTURE':'EXCLUDED_CANDIDATE_NOT_RETAINED_ROW'}
                            if 'UNCLOSED_OR_FUTURE' in ds['issues'] else {}),
            'retained_row_use': {
                'all_price_rows_closed_at_cutoff': all_closed,
                'latest_price_closed_at_cutoff': closed,
                'own_instrument_open_close_usable': closed,
                'quote_taker_balance_usable': quote_flow,
                'requested_window_complete': ds['coverage']['complete']}}
    return {'cutoff':raw['cutoff'], 'observed_hour':raw['observed_hour'],
        'datasets':datasets,
        'windows':windows, 'cycle_metrics': {k:v for k,v in reporting.metrics(raw, None).items() if v.get('interval_seconds', market.HOUR) > market.HOUR}}


def observation_template(ctx, raw):
    form = {'market_review': {
        'assessment_summary': None,
        'patterns': [{'kind': kind, 'stage': None, 'confidence': None, 'support_refs': [], 'counter_refs': [],
                      'interpretation': None, 'next_evidence': []} for kind in sorted(market.PATTERN_KINDS)],
        'changes_since_hour': [], 'changes_since_deep': [], 'deep_review': {'required': None, 'reasons': []}},
        'recommendation_checks': [{'side': target['side'], 'findings': [], 'refs': [], 'explanation': None}
                                  for target in ctx['watch_targets']],
        'extra_findings': [], 'main_evaluation': {'required': None, 'reasons': []}, 'research_findings': [],
        'factor_checks': {'items': [], 'summary': None}}
    if 'cycle_baseline' in ctx:
        form['applicability_review'] = {'basis_result_id': ctx['cycle_baseline']['result_id'],
            'outcome': None, 'explanation': None, 'refs': [], 'limitations': None}
    if ctx.get('revision', 1) >= 5:
        form.update(market_assessment=None, dispute_zones=[], market_result_hash=None)
    if ctx.get('revision', 1) >= 6:
        from .closed_loop import review_template
        form['allocation_review'] = review_template(ctx['revision'])
    return form


def market_template(ctx, raw):
    form = observation_template(ctx, raw)
    return {k: form[k] for k in market_state.MARKET_FIELDS if k in form}


def admit_new_market_run():
    """Stop affected recurring work before collection or a new model candidate."""
    import node_config
    settings = node_config.load()
    if not (os.environ.get('BTC_IN_CONTAINER') == '1' or
            (settings.get('docker_service') or {}).get('enabled')):
        return
    import model_daily
    result = model_daily.request('model.defect.check', {'scope': 'btc/market'})
    require(result.get('allowed') is True,
            'known public-market defect blocks a new run; inspect the original fault and repair evidence')


def report_template(ctx):
    previous = (ctx.get('report_care') or {}).get('active_maintenance') or {}
    retained = deepcopy(previous.get('triggers', []))
    if ctx.get('market_revision', 0) >= 6 and any('scope' not in t for t in retained):
        retained = []  # Legacy ownership needs actual model review, not inference.
    form = {'action': None, 'purposes': [], 'reason': None, 'next_observation': None,
        'baseline_review': None, 'applicability_review': None, 'information': {}, 'layout': None, 'delivery_coverage': [],
        'important_events': deepcopy(ctx.get('required_report_events', [])),
        'maintenance': {'kind': 'MARKET_REVIEW', 'thesis': None, 'support': None, 'unresolved': None,
            'triggers': retained, 'recheck_at': None,
            'daily': {'day': (ctx.get('report_care') or {}).get('day'),
                      'report_by': (ctx.get('report_care') or {}).get('report_by'), 'reason': None},
            'checked': {'prior_review_id': (ctx.get('report_care') or {}).get('prior_review_id'),
                'basis_review': None, 'trigger_results': [{'id': t['id'], 'state': None, 'evidence': None}
                                                        for t in previous.get('triggers', [])], 'outcome': None}}}
    if ctx.get('deep_report_policy_revision') == 1:
        basis = ctx['cycle_baseline']
        form['report_basis'] = {'result_id': basis['result_id'], 'report_hash': basis.get('report_hash'),
                                'mode': None, 'verification': None}
    if ctx.get('market_revision', 0) >= 5:
        form['maintenance']['retired'] = []
        if 'report_basis' in form:
            form['report_basis'].update(observation_id=ctx['observation']['observation_id'], evidence_id=ctx['identity'].get('factor_evidence_id'))
    if ctx.get('market_revision', 0) >= 6:
        from .closed_loop import report_inputs
        form['result_binding'] = report_inputs(ctx)['result_binding']
        form['maintenance']['checked']['outcomes'] = {'MARKET': None, 'PORTFOLIO': None}
        for item, prior in zip(form['maintenance']['checked']['trigger_results'], previous.get('triggers', [])):
            item.update(scope=prior.get('scope'), material=None)
    return form


def report_brief(ctx):
    # Retain every previous condition and actual display; omit duplicated full reviews.
    brief = {k: ctx.get(k) for k in ('current_strategy', 'cycle_policy_revision', 'deep_report_policy_revision', 'cycle_baseline', 'context_id', 'evaluated_at', 'reported', 'report_care', 'coverage',
        'knowledge_cutoff', 'silent_hours', 'unreported', 'unreported_total', 'unreported_truncated',
        'pending_delivery', 'pending_report_reviews', 'delivery_obligations', 'required_report_events', 'previous_silence', 'observation', 'public_evidence', 'market_revision', 'report_contract_revision', 'report_subjects', 'report_quote_policy', 'collector_observations')}
    brief = deepcopy(brief)
    basis = brief.get('cycle_baseline') or {}
    if basis.get('report_narrative') == (brief.get('current_strategy', {}).get('conclusion') or {}).get('narrative'):
        basis.pop('report_narrative', None)
        basis['report_narrative_ref'] = '/current_strategy/conclusion/narrative'
    if ctx.get('market_revision', 0) >= 6:
        from .closed_loop import report_inputs
        for key in ('current_strategy', 'observation', 'collector_observations'):
            brief.pop(key, None)
        basis = brief.get('cycle_baseline') or {}
        brief['cycle_baseline'] = {k: basis.get(k) for k in
            ('status', 'market_status', 'portfolio_status', 'result_id', 'report_hash', 'recheck_at')}
        brief.update(report_inputs(ctx))
    if brief.get('reported'):
        brief['reported']['input_role'] = 'DELIVERED_HISTORY_ONLY'
    brief.update(reporting.authoring_guidance(ctx))
    return brief


def main_template(core):
    return {'components': {'updates': {},
        'section_reviews': {key: None for key in main_flow.sections_for_revision(max(4, core.get('revision', 1)))},
        'evidence_bindings': deepcopy(core['evidence_bindings']),
        'domain_market_cutoff': core['temporal_scope']['domain_market_cutoff'], 'stop_reason': core['stop_reason']},
        'checks': {'checks': {key: None for key in main_flow.CHECKS.split()},
                   'unresolved_issues': [], 'material_new_outlook': None}}


def _latest(root, name):
    p = Path(root) / name
    return read(p) if p.exists() else None


def anchors(root, project):
    return {'decision_id': storage.current(root)['decision_id'], 'fact_version': facts(project),
        'sensing': _latest(root, 'sensing-current.json'), 'main': _latest(root, 'main-current.json'),
        'reported': _latest(root, 'reported-current.json'), 'display': _latest(root, 'last-display-current.json'),
        'report_review': _latest(root, 'report-review-current.json')}


def prepare(root, project, run_ref, directory, market_file=None, additional=None, deep=False, agent_id=None, collector_file=None, workflow_revision=None, resume_preparation=False, collector_snapshot=None, retained_source_bindings=None):
    text(run_ref, 'actual run reference')
    text(agent_id, 'actual main agent id')
    root, project, directory = Path(root).resolve(), Path(project).resolve(), Path(directory).resolve()
    require(root == (project / '.strategy/v4').resolve(), 'run store must belong to the supplied project')
    require(not (collector_file and collector_snapshot), 'reader export and frozen collector snapshot are different inputs')
    choices = read(retained_source_bindings) if retained_source_bindings else None
    compatibility = ({'retained_source_bindings': choices} if choices is not None else {})
    if collector_snapshot:
        compatibility['collector_snapshot_hash'] = digest(Path(collector_snapshot).read_bytes())
    with locked(directory):
        path = directory / 'run.json'
        if path.exists():
            run = read_run(path, root, project)
            require(run['run_ref'] == run_ref and run['agent_id'] == agent_id, 'run belongs to a different execution')
            require(run['anchors'] == anchors(root, project), 'run context changed; prepare a new directory and review the difference')
            require(not market_file or read(market_file) == read(run['market']), 'different raw input needs a new run directory')
            require(run.get('additional') == (str(Path(additional).resolve()) if additional else None), 'new supplemental evidence needs a new directory')
            require(not additional or digest(read(Path(additional) / 'evidence.json')) == run['additional_hash'], 'supplemental evidence changed; prepare a new directory')
            require(run['deep'] == deep, 'collection scope changed; prepare a new directory')
            require(run.get('collector_input') == (str(Path(collector_file).resolve()) if collector_file else None), 'collector input changed')
            require(not collector_file or digest(Path(collector_file).read_bytes()) == run.get('collector_input_hash'), 'collector input changed')
            require(run.get('preparation_compatibility', {}) == compatibility, 'preparation compatibility inputs changed')
            return result_paths(run, 'prepared')
        admit_new_market_run()
        marker = directory / 'preparation.json'
        partial = any((directory / name).exists() for name in ('market.json','collector.json','evidence'))
        require(not partial or resume_preparation,
                'incomplete preparation exists; use --resume-preparation to reuse frozen inputs, not another collection')
        require(not resume_preparation or marker.is_file(),
                'legacy incomplete preparation: use a new directory with --market and --collector-snapshot pointing to original frozen files')
        recovery = storage.recover(root)
        before = anchors(root, project)
        pinned = storage.current(root)
        core = pinned.get('assessment') or {}
        require(core.get('revision', 1) >= 3, 'run adapter needs the existing main workflow; use legacy migration explicitly')
        preparation = {'schema':'BTC_PREPARATION_V1','run_ref':run_ref,'agent_id':agent_id,
                       'anchors':before,'deep':deep,'workflow_revision':workflow_revision,
                       'market_input_hash':digest(Path(market_file).read_bytes()) if market_file else None,
                       'collector_input_hash':digest(Path(collector_file).read_bytes()) if collector_file else None,
                       'collector_snapshot_hash':compatibility.get('collector_snapshot_hash'),
                       'compatibility':compatibility,
                       'additional_hash':digest(read(Path(additional) / 'evidence.json')) if additional else None}
        if marker.exists():
            require(resume_preparation, 'incomplete preparation exists; use --resume-preparation')
            require(read(marker) == preparation, 'preparation identity or context changed; use a new directory with original frozen inputs')
        else:
            save(marker, preparation)
        # An explicitly supplied market file is for offline replay or same-study refresh.
        if resume_preparation and (directory / 'market.json').is_file():
            raw = read(directory / 'market.json')
        elif market_file:
            raw = read(market_file)
        elif cycle.enabled(root):
            observed = _latest(root, 'sensing-current.json')
            cached = reporting.raw_for_observation(root, sensing.observation(root, observed['observation_id'])) if observed else sensing.raw_market(root / 'evidence' / core['evidence_id'][7:], core)
            raw = market.collect(deep=deep, cycle_cache=cached)
        else:
            raw = market.collect(deep=deep)
        market.validate_evidence(raw)
        # Frozen source hashes bind bytes, including original serialization.
        raw_path = save(directory / 'market.json', Path(market_file).read_bytes() if market_file else raw)
        from . import collector
        if resume_preparation and (directory / 'collector.json').is_file():
            snapshot = collector.validate(read(directory / 'collector.json'))
        elif collector_snapshot:
            snapshot = collector.validate(read(collector_snapshot))
        else:
            snapshot = collector.capture(project, collector_file)
        collector_path = save(directory / 'collector.json', snapshot) if snapshot else None
        retained = refs_in(core, root)
        # Already-known conclusions remain part of this run's information set.
        # Reusing slow evidence must not move its original publication timestamps.
        cutoff = max([core['temporal_scope']['knowledge_cutoff']] + [r['retrieved_at'] for r in raw['retrievals'].values()] +
                     ([read(Path(additional) / 'evidence.json')['cutoff']] if additional else []) +
                     ([snapshot['captured_at']] if snapshot else []), key=instant)
        evidence_dir = directory / 'evidence'
        retained_directories = []
        if market_state.enabled(root, core):
            prior_market = market_state.effective(root, core, pinned['decision_id'])
            retained.extend(refs_in(prior_market, root))
            if prior_market['observation_id']:
                previous = sensing.observation(root, prior_market['observation_id'])
                prior_evidence = root / 'evidence' / previous['context']['identity']['factor_evidence_id'][7:]
                retained_directories.append(prior_evidence)
                cutoff = max(cutoff, read(prior_evidence / 'evidence.json')['cutoff'], key=instant)
        # A later quiet observation may omit a previously reported public fact.
        # Retain its raw evidence, without bringing mixed report prose or account
        # inputs into the independent market stage.
        display = _latest(root, 'last-display-current.json')
        if display:
            from . import delivery
            shown, _, _ = delivery.validate_delivered(root, display['delivery_id'])
            shown_context = shown.get('source', {}).get('report_review', {}).get('context', {})
            shown_evidence_id = shown_context.get('identity', {}).get('factor_evidence_id')
            if shown_evidence_id:
                shown_dir = root / 'evidence' / shown_evidence_id[7:]
                shown_bundle = evidence.validate(read(shown_dir / 'evidence.json'), shown_dir)
                public_ids = {s['id'] for s in shown_bundle['sources'] if s['trust'] == 'PUBLIC_RAW'}
                shown_refs = refs_in(market_state.public_review(shown_context.get('observation', {}).get('review', {})), root)
                retained.extend(r for r in shown_refs if r['source_id'] in public_ids)
                retained_directories.append(shown_dir)
                cutoff = max(cutoff, shown_bundle['cutoff'], key=instant)
        require(not evidence_dir.exists() or (evidence_dir / 'evidence.json').is_file(),
                'incomplete evidence freeze; preserve it and use a new directory with original frozen inputs')
        if (evidence_dir / 'evidence.json').is_file():
            package = evidence.validate(read(evidence_dir / 'evidence.json'), evidence_dir)
            require(any(s['raw_hash'] == digest(Path(raw_path).read_bytes()) for s in package['sources']),
                    'prepared raw market differs from the frozen evidence')
            if collector_path:
                require((package.get('collector') or {}).get('snapshot_id') == digest(snapshot),
                        'prepared collector differs from the frozen evidence')
            save(directory / 'preparation-compatibility.json', compatibility)
        else:
            package = evidence.compact_root(root / 'evidence' / core['evidence_id'][7:], raw_path, retained,
                                        evidence_dir, cutoff, additional=additional, retained_directories=retained_directories,
                                        collector_file=collector_path, retained_source_bindings=choices)
            save(directory / 'preparation-compatibility.json', compatibility)
        ctx = sensing.context(root, project / '.strategy/market_observation.sqlite3', project, raw,
                              evidence_dir=evidence_dir, workflow_revision=workflow_revision)
        # Preparation is not a publication, an observation completion, or a report completion.
        require(before == anchors(root, project), 'state changed during preparation; use a new run directory')
        save(directory / 'sensing-context.json', ctx)
        index = ref_index(package, evidence_dir, raw)
        save(directory / 'reference-index.json', index)
        save(directory / 'observation-template.json', observation_template(ctx, raw))
        save(directory / 'vocabulary.json', {'pattern_stages': sorted(market.STAGES),
            'confidence': sorted(market.CONFIDENCES), 'market_refs': sorted(market.reference_ids(raw)),
            'recommendation_findings': ['NO_MATERIAL_CHANGE','SUPPORT_WEAKENED','COUNTEREVIDENCE','AMOUNT_BASIS_CHANGED','GAP','OTHER_MATERIAL'],
            'research_change_kinds': ['INITIAL_QUESTION','NEW_KEY_EVIDENCE','MATERIAL_COUNTEREVIDENCE','REOPEN_CONDITION_MET','NO_NEW_KEY_EVIDENCE'],
            'extra_finding_fields': ['summary','refs','material'],
            'research_finding_fields': ['issue_key','scope','question','material_change','change_kind','change_reason','refs','reopen_condition'],
            'applicability_outcomes': ['VALID','EVIDENCE_LIMITED','REVISE'],
            'reference_formats': {'market_review_recommendation_extra':'Use market_refs string IDs.',
                                  'public_market':'For horizon refs, counter_refs and forward prior_check.refs use exact source_id/pointer/value_hash objects from reference_index; never omit value_hash.',
                                  'factor_research':'Use exact source_id/pointer/value_hash objects from reference_index.',
                                  'applicability':'Use exact source_id/pointer/value_hash objects from reference_index.'},
            'mobile_numbers': 'Author final Markdown in AUTHORED_REPORT_V1 body; bind verbatim excerpts to frozen scalar refs in bindings. No interpolation, reordering or automatic advice rows. Product prices require price.subject and price.unit.'})
        market_summary = market_brief(raw)
        save(directory / 'market-summary.json', market_summary)
        model_ctx = (sensing.compact_context(ctx) if any('samples' in t for m in ctx['measurements'] for t in m['tiers'])
                     else deepcopy(ctx))
        if model_ctx.get('previous_hour') and (ctx.get('previous_hour') or {}).get('review'):
            model_ctx['previous_hour']['assessment_summary'] = ctx['previous_hour']['review'].get('market_review', {}).get('assessment_summary')
            model_ctx['previous_hour']['raw_review_path'] = str(root / 'sensing' / (ctx['previous_hour']['observation_id'][7:] + '.json')) if ctx['previous_hour'].get('observation_id') else None
        brief = {'decision_id': pinned['decision_id'], 'assessed_at': core['temporal_scope'],
            'conclusion': core.get('conclusion'), 'outlook': core.get('outlook'), 'robustness': core.get('robustness'),
            'cycle_baseline': ctx.get('cycle_baseline'),
            'recommendations': core.get('recommendations'), 'portfolio_analysis': core.get('portfolio_analysis'),
            'factor_assessment': core.get('factor_assessment'), 'section_provenance': core.get('section_provenance'),
            'factor_context': model_ctx.get('factor_context'),
            'confirmed_account_facts': core.get('domain', {}).get('account_facts') if core['fact_version'] == before['fact_version'] else None,
            'fact_files': {name: str(project / name) for name in before['fact_version']},
            'facts_match_current': core['fact_version'] == before['fact_version'],
            'previous_hour': model_ctx.get('previous_hour'), 'accumulation': ctx['accumulation'],
            'measurements': model_ctx['measurements'], 'due_event_reviews': ctx.get('due_event_reviews'),
            'event_cycle': ctx.get('event_cycle'), 'research': ctx.get('research'),
            'next_evidence': (core.get('outlook') or {}).get('semantic', {}).get('next_evidence'),
            'report_care': report_care.status(root, run_ref=run_ref), 'last_actual_report': reporting.reported(root),
            'pending_delivery': recovery['delivery_recovery'],
            'pending_report_reviews': recovery['report_reviews_not_enqueued'],
            'delivery_obligations': recovery['report_delivery_obligations'],
            'attempt_recovery': [v for v in recovery['attempt_recovery'] if v['next_action'] != 'WAIT_FOR_NEW_EVIDENCE'],
            'paths': {'full_decision': str(root / 'versions' / pinned['decision_id'] / 'decision.json'),
                      'market_summary': str(directory / 'market-summary.json'),
                      'reference_index': str(directory / 'reference-index.json'), 'raw': raw_path}}
        if cycle.report_required(root):
            brief['method_references'] = cycle.method_references('observe', project)
            brief['spot_risk_review_instructions'] = cycle.SPOT_RISK_REVIEW
            # factor_context already holds the same discovery guidance once.
        # Keep the complete report exactly once; do not summarize away risk or
        # evidence, and keep the unmodified full context for validation.
        brief = deepcopy(brief)
        basis = brief.get('cycle_baseline') or {}
        if basis.get('report_narrative') == (brief.get('conclusion') or {}).get('narrative'):
            basis.pop('report_narrative', None)
            basis['report_narrative_ref'] = '/conclusion/narrative'
        brief['applicability_instructions'] = cycle.APPLICABILITY_REVIEW
        if ctx.get('revision', 1) >= 5:
            brief = market_state.market_projection(ctx)
            brief['method_references'] = cycle.method_references('observe', project)
            brief['pending_work'] = {
                'research': [{'issue_key': item['issue_key'], 'request_id': item['latest_request']['request_id'],
                    'progress': item['latest_request']['progress'], 'adopted_result': item.get('adopted_result'),
                    'completed_result': item.get('completed_result'), 'pending_candidates': item.get('pending_candidates', [])}
                    for item in ctx['research']['issues']],
                'delivery_attempts': [v for v in recovery['attempt_recovery'] if v['next_action'] != 'WAIT_FOR_NEW_EVIDENCE'],
                'undelivered_reports': len(recovery['report_delivery_obligations']),
                'delivery_details_stage': 'report'}
            save(directory / 'market-template.json', market_template(ctx, raw))
        save(directory / 'context.json', brief)
        save(directory / 'recovery.json', recovery)
        reads = material_reads.make(directory, 'observe', directory / 'context.json',
            required={'market_summary': directory / 'market-summary.json',
                      'observation_template': directory / ('market-template.json' if ctx.get('revision', 1) >= 5 else 'observation-template.json'),
                      'vocabulary': directory / 'vocabulary.json'},
            on_demand={'reference_index': str(directory / 'reference-index.json'),
                       'raw_market': raw_path, **({} if ctx.get('revision', 1) >= 5 else {'full_context': str(directory / 'sensing-context.json')})})
        run = {'schema': 'BTC_RUN_MATERIAL_V1', 'root': str(root), 'project': str(project), 'directory': str(directory),
               'run_ref': run_ref, 'agent_id': agent_id, 'created_at': now(), 'anchors': before,
               'market': raw_path, 'market_hash': digest(raw), 'evidence': str(evidence_dir), 'evidence_id': package['evidence_id'],
               'additional': str(Path(additional).resolve()) if additional else None,
               'additional_hash': digest(read(Path(additional) / 'evidence.json')) if additional else None,
               'deep': deep, 'context_id': ctx['context_id'], 'read_set': {'observe': reads}}
        if collector_path:
            run.update(collector=collector_path, collector_input=str(Path(collector_file).resolve()) if collector_file else None,
                       collector_input_hash=digest(Path(collector_file).read_bytes()) if collector_file else None)
        if compatibility:
            run['preparation_compatibility'] = compatibility
        if ctx.get('revision', 1) >= 6:
            run['workflow_revision'] = ctx['revision']
        save(path, run)
        return result_paths(run, 'prepared')


def result_paths(run, status):
    d = Path(run['directory'])
    return {'status': status, 'run': str(d / 'run.json'), 'context': str(d / 'context.json'),
        'market_summary': str(d / 'market-summary.json'), 'reference_index': str(d / 'reference-index.json'),
        'observation_template': str(d / 'observation-template.json'), 'vocabulary': str(d / 'vocabulary.json'),
        **({'market_template': str(d / 'market-template.json'), 'next_stage': 'market'} if (d / 'market-template.json').exists() else {}),
        'report_care_required': True, **({'read_set': run['read_set']} if run.get('read_set') else {})}


def identity(run):
    return {'role': 'MAIN', 'model': market.CURRENT_HOURLY_MODEL['name'], 'effort': market.CURRENT_HOURLY_MODEL['reasoning_effort'],
            'agent_id': run['agent_id'], 'run_ref': run['run_ref']}


def resolve_answers(run, answers):
    root = Path(run['root'])
    return material_reads.resolve_answers(answers,
        [run['directory'], root / 'research', root / 'versions'],
        read(Path(run['evidence']) / 'evidence.json'), run['evidence'])


def read_run(path, root=None, project=None, *, resolve_aliases=False):
    run = read(path)
    require(run['schema'] == 'BTC_RUN_MATERIAL_V1', 'invalid run material')
    def bound(actual, expected):
        # Native immutable reads can see the same mounted store through its
        # logical project symlink. Preserve the stored identity and bytes;
        # both aliases must still resolve to exactly the same owner.
        target = Path(expected).resolve() if resolve_aliases else Path(expected)
        return Path(actual).resolve() == target
    require(bound(path, Path(run['directory']) / 'run.json'), 'run directory binding differs')
    if root is not None: require(bound(root, run['root']), 'CLI root does not match run')
    if project is not None: require(bound(project, run['project']), 'CLI project does not match run')
    raw = read(run['market'])
    require(digest(raw) == run['market_hash'], 'run raw material changed')
    package = evidence.validate(read(Path(run['evidence']) / 'evidence.json'), run['evidence'])
    require(package['evidence_id'] == run['evidence_id'], 'run evidence changed')
    return run


def phase_material(run, label, stage='report'):
    root, d = Path(run['root']), Path(run['directory'])
    require(stage in ('report', 'report-body', 'main'), 'invalid material stage')
    base_reads = (run.get('read_set') or {}).get('observe')
    # Portfolio is already the preceding required stage. Reuse its exact
    # reading view as well as the public base instead of presenting it twice.
    portfolio_reads = list((d / 'reading').glob('portfolio-*/read-set.json'))
    if portfolio_reads:
        base_reads = str(max(portfolio_reads, key=lambda path: path.stat().st_mtime_ns))
    if stage == 'main':
        ctx = sensing.context(root, Path(run['project']) / '.strategy/market_observation.sqlite3',
                              run['project'], read(run['market']), evidence_dir=run['evidence'], frozen_review=True, workflow_revision=run.get('workflow_revision'))
        prefix = label + '-main-' + digest(ctx)[7:19]
        path = save(d / (prefix + '-context.json'), ctx)
        form = main_template(storage.current(root)['assessment'])
        form['_binding'] = {'context_file': path, 'context_hash': digest(ctx)}
        template = save(d / (prefix + '-main-template.json'), form)
        request = save(d / (prefix + '-cycle-request-template.json'), cycle.request_template(ctx)) if 'cycle_baseline' in ctx else None
        reads = material_reads.make(d, 'main', path, base=base_reads, required={'main_template': template},
            on_demand={'cycle_request_template': request, 'reference_index': str(d / 'reference-index.json')})
        return {'main_template': template, 'cycle_request_template': request, 'read_set': {'main': reads}}
    # Every observation still reviews report necessity. Body material is lazy.
    ctx = reporting.context(root)
    prefix = label + '-authored-report-' + digest(ctx)[7:19]
    full_path = save(d / (prefix + '-full.json'), ctx)
    brief_path = save(d / (prefix + '-context.json'), report_brief(ctx))
    form = report_template(ctx)
    form['_binding'] = {'context_file': full_path, 'context_hash': digest(ctx)}
    template_path = save(d / (prefix + '-template.json'), form)
    quality_keys = authored_report.quality_keys(ctx)
    layout = {
        'format': 'AUTHORED_REPORT_V1', 'body': None, 'bindings': [],
        'price_status': {'lower_spot':None,'upper_short':None},
        'quality_review': {key:None for key in sorted(quality_keys)}}
    if ctx.get('report_contract_revision', 1) >= 5:
        layout['reviewed_candidate_hash'] = None
    extra = {}
    if stage == 'report-body':
        extra = {'layout_template': save(d / (prefix + '-layout-template.json'), layout),
                 'report_metrics': save(d / (prefix + '-metrics.json'), ctx['metrics'])}
    reads = {'report': material_reads.make(d, 'report', brief_path, base=None if ctx.get('market_revision', 0) >= 6 else base_reads,
                required={'report_template': template_path},
                on_demand=({**extra} if ctx.get('market_revision', 0) >= 6 else {'full_context': full_path, **extra}))}
    recovery = ctx['pending_delivery']
    return {'report_context': brief_path, 'report_template': template_path, **extra,
            'delivery_recovery': recovery, 'delivery_obligations': ctx['delivery_obligations'],
            'next_material': {'main': 'run-material --stage main', 'body': 'run-material --stage report-body'},
            'read_set': reads}


def context_changes(before, after):
    """Explain only changed bindings; never silently rebind authored judgments."""
    old, new = before.get('identity', {}), after.get('identity', {})
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    return {'changed_bindings': changed,
            'research_progress': [{'issue_key': i['issue_key'], **i['latest_request']}
                                  for i in (after.get('research') or {}).get('issues', [])],
            'next_action': 'REFRESH_STAGE_MATERIAL_AND_REVIEW_CHANGED_BINDINGS_ONLY',
            'reuse': 'Retain original research request, independent answer, approved candidate and frozen evidence. '
                    'Do not recollect or restart research solely for an observation/report clock change.'}


def _bound_context(run, authored, stage):
    binding = authored.pop('_binding', None)
    if binding:
        require(Path(binding['context_file']).resolve().parent == Path(run['directory']), 'context must belong to this run')
        ctx = read(binding['context_file'])
        require(digest(ctx) == binding['context_hash'], 'authored context material changed')
    else:
        require(stage == 'observe', 'use the stage template returned after observation/publication')
        ctx = read(Path(run['directory']) / 'sensing-context.json')
    return ctx


def _already_completed(run, stage, intent):
    root = Path(run['root'])
    if stage == 'observe':
        latest = _latest(root, 'sensing-current.json')
        if latest:
            saved = sensing.observation(root, latest['observation_id'])
            if saved['review'] == intent['payload']:
                return {'observation_id': saved['observation_id'], 'status': 'RECOVERED_OBSERVATION'}
    elif stage == 'main':
        latest = _latest(root, 'main-current.json')
        if latest:
            saved = read(root / latest['completion'])
            if saved['draft_id'] == intent['draft']['draft_id']:
                return {'decision_id': saved['decision_id'], 'status': saved['status']}
    else:
        latest = _latest(root, 'report-review-current.json')
        if latest:
            saved = reporting.review(root, latest['review_id'])
            if all(saved.get(k) == v for k, v in intent['payload'].items()):
                return {'review_id': saved['review_id'], 'action': saved['action'], 'status': 'RECOVERED_REVIEW'}
    return None


def advance_market(run_path, input_path=None, root=None, project=None, template_path=None, answers_path=None, *, check_only=False):
    """Expose the actual public candidate for review, then freeze its contents."""
    run = read_run(run_path, root, project)
    d, root = Path(run['directory']), Path(run['root'])
    ctx, raw = read(d / 'sensing-context.json'), read(run['market'])
    require(ctx.get('revision', 1) >= 5, 'separate market phase requires revision 5')
    candidate = None
    if input_path is not None:
        require(template_path is None and answers_path is None, 'use input or template/answers')
        authored = read(input_path)
        if isinstance(authored, dict) and 'payload' in authored:
            candidate = authored
            exact(candidate, 'payload result_hash market_context_id' + (' lifecycle_frozen_at' if 'lifecycle_frozen_at' in candidate else ''), 'public market candidate')
            require(candidate['result_hash'] == digest(candidate['payload']), 'public market candidate changed')
            authored = candidate['payload']
    else:
        require(template_path is not None and answers_path is not None, 'market template and answers required')
        require(Path(template_path).resolve() == d / 'market-template.json', 'use this run market template')
        template = read(template_path)
        require(template == market_template(ctx, raw), 'market template changed')
        authored = material_reads.compose(template, resolve_answers(run, read(answers_path)))
    exact(authored, ' '.join(market_template(ctx, raw)), 'independent hourly market result')
    inputs = fact_inputs(run['project'])
    latest = sensing.context(root, Path(run['project']) / '.strategy/market_observation.sqlite3',
                             run['project'], raw, evidence_dir=run['evidence'], workflow_revision=run.get('workflow_revision'),
                             fact_snapshot=inputs)
    require(market_state.market_projection(ctx) == market_state.market_projection(latest),
            'public market inputs changed; refresh market material')
    package = evidence.validate(read(Path(run['evidence']) / 'evidence.json'), run['evidence'])
    resolve = lambda ref: evidence.resolve(package, run['evidence'], ref, verified=True)
    sensing.validate_public(root, ctx, raw, authored, run['evidence'])
    frozen_path = d / 'market-result.json'
    existing = read(frozen_path) if not check_only and frozen_path.is_file() else None
    if existing is not None:
        exact(existing, 'payload result_hash market_context_id' + (' lifecycle_frozen_at' if 'lifecycle_frozen_at' in existing else ''), 'frozen public market result')
        require(existing['result_hash'] == digest(existing['payload']), 'frozen public result hash changed')
        require(existing['payload'] == authored, 'cannot change an already frozen public result')
        original_view = {k:v for k,v in existing.items() if k != 'lifecycle_frozen_at'}
        original_candidate = d / ('market-candidate-' + digest(original_view)[7:] + '.json')
        require(existing['market_context_id'] == read(d / 'context.json').get('market_context_id')
                or (original_candidate.is_file() and read(original_candidate) == original_view),
                'frozen public context differs from original run material or candidate')
        # Instruction updates change a new projection's hash. Recovery retains
        # the original freeze and its bound run material, never rebinds history.
        frozen = existing
    else:
        frozen = {'payload': authored, 'result_hash': digest(authored),
                  'market_context_id': market_state.market_projection(ctx)['market_context_id']}
    candidate_view = {k:v for k,v in frozen.items() if k != 'lifecycle_frozen_at'}
    candidate_path = d / ('market-candidate-' + digest(candidate_view)[7:] + '.json')
    if candidate is not None:
        if existing is not None:
            require(candidate == existing or candidate == candidate_view, 'public market recovery differs from frozen result')
        else:
            require(candidate['market_context_id'] == frozen['market_context_id'],
                    'public market candidate context changed')
            require(candidate_path.is_file() and read(candidate_path) == frozen,
                    'use the actual prechecked public market candidate')
    if check_only:
        with locked(d):
            path = save(candidate_path, frozen)
            material = save(d / ('market-review-' + digest(frozen)[7:] + '.json'),
                {'market_candidate': frozen,
                 'instructions': (market_state.PUBLIC_ANALYSIS_REVIEW + market_state.CYCLE_THESIS_REVIEW + market_state.EVIDENCE_REASONING_REVIEW
                                  + market_state.PUBLIC_RESULT_REVIEW)})
            reads = material_reads.make(d, 'market-review', material, compact=True,
                on_demand={'public_context': str(d / 'context.json'),
                           'reference_index': str(d / 'reference-index.json'),
                           'raw_market': run['market']})
        return {'valid': True, 'status': 'MARKET_CANDIDATE_READY', 'market_candidate': path,
                'market_result_hash': frozen['result_hash'], 'review_material': material,
                'read_set': {'market_review': reads}, 'scope': 'structure_and_bindings_only',
                'review_read': {'arguments': ['run-read', '--run', str(run_path), '--read-set', reads, '--cursor', '0'],
                                'max_output_tokens': 16000},
                'next': 'Use review_read to return actual bounded content; follow next_cursor until complete. '
                        'Do not replace complete reread with selected fields or file parsing. '
                        'Review all candidate fields in their owning object and actual downstream scope; '
                        'after corrections and review, freeze this same candidate with --stage market --input <market_candidate>.'}
    with locked(d):
        # Existing freezes retain their identity and legacy recovery path.
        # A new freeze must use the exact exported public candidate, not a
        # freshly composed or copied judgment that bypasses the review step.
        require(candidate is not None or (d / 'market-result.json').is_file(),
                'new public result requires --check-only, actual candidate reread, then --input <market_candidate>')
        if ctx.get('revision', 1) >= 9:
            require(existing is None or 'lifecycle_frozen_at' in existing, 'missing original freeze clock remains posthoc; replay cannot backfill first registration')
            if existing is None: frozen['lifecycle_frozen_at'] = now()
        path = save(d / 'market-result.json', frozen)
        if ctx.get('revision', 1) >= 9:
            from .forward_lifecycle import register
            register(root, frozen['payload']['market_assessment'], path, '/lifecycle_frozen_at')
        core = storage.current(root)['assessment']
        portfolio = {'market_result': frozen, 'recommendations': core.get('recommendations'),
                     'portfolio_analysis': core.get('portfolio_analysis'),
                     'conclusion': core.get('conclusion'), 'robustness': core.get('robustness'),
                     'confirmed_account_facts': core.get('domain', {}).get('account_facts'),
                     'fact_files': {name: item['path'] for name, item in inputs['files'].items() if item['status'] == 'AVAILABLE'},
                     'fact_availability': inputs['files'],
                     'facts_match_current': core['fact_version'] == inputs['fact_version'],
                     'instructions': '围绕已冻结周期主线和现价价值，完成参与、防守、等待及现货与空头各自的条件含义；不得改写市场结果。账户缺口只约束依赖它的数量、覆盖或实际净动作，仍完成有独立依据的分项判断。内部到期触发实质重新分析，不以到期或未知结案、不自动续期；正式建议改变沿原研究审阅。'}
        if not portfolio['facts_match_current']:
            portfolio['confirmed_account_facts'] = None
        if package.get('collector') and ctx.get('revision', 1) < 6:
            from .collector import context as collector_context
            portfolio['collector_observations'] = collector_context(package, run['evidence'])
        form_path = d / 'observation-template.json'
        if ctx.get('revision', 1) >= 6:
            from .closed_loop import INSTRUCTIONS, allocation, risk, result_keys
            portfolio = {'market_result': frozen, result_keys(ctx['revision'])[0]: risk(core) if ctx['revision'] >= 7 else allocation(core),
                         'current_recommendations': core.get('recommendations'),
                         'fact_version': latest['identity']['fact_version'],
                         'confirmed_account_facts': portfolio['confirmed_account_facts'],
                         'fact_files': portfolio['fact_files'], 'fact_availability': portfolio['fact_availability'],
                         'instructions': INSTRUCTIONS}
            if ctx.get('revision', 1) >= 8:
                from .investment import open_issues, decision as investment_decision
                if ctx.get('revision', 1) >= 9:
                    portfolio['account_constraints'] = latest['account_constraints']
                portfolio['investment_decision'] = investment_decision(core)
                portfolio['open_investment_issues'] = open_issues(core)
                portfolio['investment_feedback'] = latest.get('investment_feedback')
                portfolio['recorded_policy_inputs'] = latest.get('recorded_policy_inputs',[])
            # A facts-only change can refresh this stage around the same public
            # freeze, without recollecting evidence or rebuilding research.
            context_path = save(d / ('portfolio-sensing-' + latest['context_id'][7:] + '.json'), latest)
            form = observation_template(latest, raw)
            form['_binding'] = {'context_file': context_path, 'context_hash': digest(latest)}
            form_path = d / ('portfolio-' + latest['context_id'][7:] + '-observation-template.json')
            save(form_path, form)
        portfolio_path = save(d / ('portfolio-context-' + digest(portfolio)[7:] + '.json' if ctx.get('revision', 1) >= 6 else 'portfolio-context.json'), portfolio)
        reads = material_reads.make(d, 'portfolio', portfolio_path,
            base=None if ctx.get('revision', 1) >= 6 else run.get('read_set', {}).get('observe'),
            required={'observation_template': form_path})
    return {'status': 'MARKET_RESULT_FROZEN', 'market_result': path,
            'market_result_hash': frozen['result_hash'], 'portfolio_context': portfolio_path,
            'observation_template': str(form_path), 'read_set': {'portfolio': reads},
            'next': 'Complete only portfolio checks and main_evaluation, then run-advance --stage observe.'}


def advance(run_path, stage, input_path=None, root=None, project=None, template_path=None, answers_path=None, *, check_only=False):
    require(not check_only or stage in ('market', 'main', 'report'), 'check-only applies to the market, main or report stage')
    if stage == 'market':
        return advance_market(run_path, input_path, root, project, template_path, answers_path, check_only=check_only)
    require(stage in ('observe', 'main', 'report'), 'invalid run stage')
    run = read_run(run_path, root, project)
    d, root = Path(run['directory']), Path(run['root'])
    if input_path is not None:
        require(template_path is None and answers_path is None, 'use input or template with answers, not both')
        authored = read(input_path)
    else:
        require(template_path is not None and answers_path is not None, 'template and answers are both required')
        template_path = Path(template_path).resolve()
        require(template_path.parent == d.resolve(), 'template must belong to this run')
        if stage == 'observe':
            require(template_path == d.resolve() / 'observation-template.json' or
                    (run.get('workflow_revision', 0) >= 6 and template_path.name.startswith('portfolio-') and template_path.name.endswith('-observation-template.json')), 'use this run observation template')
        else:
            require(template_path.name.endswith('-main-template.json') if stage == 'main' else
                    template_path.name.endswith('-template.json') and not template_path.name.endswith(('-main-template.json', '-layout-template.json', '-cycle-request-template.json')),
                    'template does not match stage')
        template = read(template_path)
        if stage == 'observe':
            bound = template.get('_binding')
            frozen_context = read(bound['context_file']) if bound else read(d / 'sensing-context.json')
            if bound:
                require(Path(bound['context_file']).resolve().parent == d.resolve() and digest(frozen_context) == bound['context_hash'], 'observation context binding changed')
            expected_template = observation_template(frozen_context, read(run['market']))
            if bound:
                expected_template['_binding'] = bound
        else:
            binding = template.get('_binding') or {}
            require(binding.get('context_file') and binding.get('context_hash'), 'use a bound stage template')
            require(Path(binding['context_file']).resolve().parent == d.resolve(), 'context must belong to this run')
            frozen_context = read(binding['context_file'])
            require(digest(frozen_context) == binding['context_hash'], 'template context material changed')
            if stage == 'report':
                expected_template = report_template(frozen_context)
            else:
                decision = frozen_context['identity']['decision_id']
                expected_template = main_template(read(root / 'versions' / decision / 'decision.json')['assessment'])
            expected_template['_binding'] = binding
        require(template == expected_template, 'template was edited; put judgments in answers instead')
        authored = material_reads.compose(template, resolve_answers(run, read(answers_path)))
        # Keep the same immutable input, intent, validation and retry identity as --input.
        if not check_only:
            save(d / (stage + '-authored-' + digest(authored)[7:] + '.json'), authored)
    if check_only:
        ctx = _bound_context(run, authored, stage)
        if stage == 'report':
            latest = reporting.context(root)
            require(ctx['context_id'] == latest['context_id'], 'stage context changed; ' + json.dumps(context_changes(ctx, latest), ensure_ascii=False))
            require(authored.get('action') == 'REPORT', 'report preflight requires a finished report candidate')
            payload = dict(authored, schema='BTC_REPORT_REVIEW_V1', context_id=ctx['context_id'],
                           provenance=identity(run), context=ctx)
            checked = authored_report.preview(payload, storage.current(root))
            review_material = {'candidate': payload, 'reviewed_candidate_hash': checked['reviewed_candidate_hash'],
                               'instructions': reporting.authoring_guidance(ctx)}
            # The reviewed text has a stable identity; each immutable reading artifact
            # also binds its current context and review notes without overwriting history.
            material = save(d / ('report-review-' + digest(review_material)[7:] + '.json'), review_material)
            reads = material_reads.make(d, 'report-review', material, compact=True)
            return dict(checked, status='REPORT_CANDIDATE_READY', review_material=material,
                        read_set={'report_review': reads},
                        review_read={'arguments': ['run-read', '--run', str(run_path), '--read-set', reads, '--cursor', '0']},
                        next_action='READ_AND_REVIEW_ACTUAL_FULL_BODY_THEN_COMMIT_THE_SAME_CANDIDATE')
        latest = sensing.context(root, Path(run['project']) / '.strategy/market_observation.sqlite3',
            run['project'], read(run['market']), evidence_dir=run['evidence'], frozen_review=True, workflow_revision=run.get('workflow_revision'))
        require(ctx['context_id'] == latest['context_id'], 'stage context changed; ' + json.dumps(context_changes(ctx, latest), ensure_ascii=False))
        exact(authored, 'components checks', 'authored main input')
        candidate, review = main_flow.assemble(root, run['project'], authored['components'], run['evidence'],
            read(run['market']), identity(run), authored['checks'], workflow_revision=run.get('workflow_revision'))
        storage.validate_main(root, run['project'], candidate, run['evidence'], review)
        return {'valid': True, 'draft_id': candidate['draft_id'], 'scope': 'structure_and_bindings_only',
                'next_action': 'COMMIT_WITH_LIVE_REVALIDATION'}
    if stage == 'observe' and read(d / 'sensing-context.json').get('revision', 1) >= 5:
        frozen = read(d / 'market-result.json')
        require(frozen['result_hash'] == digest(frozen['payload']), 'frozen market result damaged')
        for field, result in frozen['payload'].items():
            empty = observation_template(read(d / 'sensing-context.json'), read(run['market'])).get(field)
            require(authored.get(field) in (None, [], {}, empty) or authored.get(field) == result, 'portfolio stage cannot edit ' + field)
            authored[field] = deepcopy(result)
        authored['market_result_hash'] = frozen['result_hash']
    key = stage + '-' + digest(authored)[7:]
    with locked(d):
        receipt = d / (key + '-receipt.json')
        if receipt.exists():
            result = read(receipt)
            # Receipts describe the actual old result, never certify current applicability.
            return dict(result, replayed=True)
        intent_path = d / (key + '-intent.json')
        intent = read(intent_path) if intent_path.exists() else None
        result = _already_completed(run, stage, intent) if intent else None
        if result is None:
            ctx = _bound_context(run, authored, stage)
            require(facts(run['project']) == (ctx['identity']['fact_version'] if run.get('workflow_revision', 0) >= 6 else run['anchors']['fact_version']), 'facts changed; refresh portfolio material and reassess')
            if stage in ('observe', 'main'):
                latest = sensing.context(root, Path(run['project']) / '.strategy/market_observation.sqlite3',
                    run['project'], read(run['market']), evidence_dir=run['evidence'], frozen_review=stage == 'main', workflow_revision=run.get('workflow_revision'))
            else:
                latest = reporting.context(root)
            require(ctx['context_id'] == latest['context_id'],
                    'stage context changed; ' + json.dumps(context_changes(ctx, latest), ensure_ascii=False))
            if intent is None:
                if stage == 'observe':
                    exact(authored, 'market_review recommendation_checks extra_findings main_evaluation research_findings factor_checks' + (' applicability_review' if 'cycle_baseline' in ctx else '') + (' market_assessment dispute_zones market_result_hash' if ctx.get('revision', 1) >= 5 else '') + (' allocation_review' if ctx.get('revision', 1) >= 6 else ''), 'authored observation')
                    payload = deepcopy(authored)
                    payload.update(schema='BTC_SENSING_REVIEW_V4', revision=ctx.get('revision', 4), context_id=ctx['context_id'])
                    raw = read(run['market'])
                    payload['market_review'].update(schema=market.REVIEW_SCHEMA, evidence_id=raw['evidence_id'], observed_hour=raw['observed_hour'],
                        context_id=ctx['context_id'], model=dict(market.CURRENT_HOURLY_MODEL))
                    intent = {'payload': payload}
                elif stage == 'main':
                    exact(authored, 'components checks', 'authored main input')
                    draft, review = main_flow.assemble(root, run['project'], authored['components'], run['evidence'],
                        read(run['market']), identity(run), authored['checks'], workflow_revision=run.get('workflow_revision'))
                    intent = {'draft': draft, 'review': review, 'expected': ctx['identity']['decision_id']}
                else:
                    payload = deepcopy(authored)
                    payload.update(schema='BTC_REPORT_REVIEW_V1', context_id=ctx['context_id'], provenance=identity(run))
                    intent = {'payload': payload}
                save(intent_path, intent)
            if stage == 'observe':
                result = sensing.record(root, Path(run['project']) / '.strategy/market_observation.sqlite3', run['project'],
                                        read(run['market']), intent['payload'], evidence_dir=run['evidence'], workflow_revision=run.get('workflow_revision'))
            elif stage == 'main':
                result = storage.commit(root, run['project'], intent['draft'], run['evidence'], intent['review'], intent['expected'])
                result = {k: result[k] for k in ('status', 'decision_id', 'comparison', 'report_required') if k in result}
            else:
                result = reporting.record(root, intent['payload'], prepared_context=ctx)
        if stage == 'observe':
            result['gate'] = sensing.gate(root, result['observation_id'])
        if stage in ('observe', 'main'):
            result.update(phase_material(run, stage))
        elif result['action'] == 'REPORT':
            src = reporting.source(root, result['review_id'])
            result['source'] = save(d / (key + '-source.json'), src)
            result['body'] = src['body']
            result['next'] = 'enqueue and existing delivery tools; verify actual assistant display before completion'
        else:
            result['next'] = 'review complete; no cloud query unless recovering an existing uncertain delivery'
        save(receipt, result)
        return result
