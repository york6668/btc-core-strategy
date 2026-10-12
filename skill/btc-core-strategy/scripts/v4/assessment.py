"""Validate audit summaries and bindings; market causality remains model-owned."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
import re
import decision_report

from . import domain, evidence, report
from .common import atoms, digest, exact, facts, instant, numbered, pointer, read, require, text


def _source_matches_raw(source, raw, directory):
    """Bind a quote to its frozen request, including separately stored params."""
    url = source.get('endpoint_url')
    if url == raw['url']:
        return True
    if source.get('payload_kind') != 'BTC_SPOT_CANDLE_CLOSE':
        return False
    from event_contract import public_candle_provenance
    registered = public_candle_provenance(url)
    if not registered or registered[:2] != (source.get('canonical_origin'), source.get('venue')):
        return False
    payload = read(Path(directory) / 'raw' / (raw['raw_hash'][7:] + '.json'))
    retrieval = payload.get('retrieval', {}) if isinstance(payload, dict) else {}
    if not isinstance(retrieval, dict) or retrieval.get('endpoint') != raw['url']:
        return False
    parsed = urlsplit(url)
    if parsed._replace(query='').geturl() != raw['url']:
        return False
    params = retrieval.get('parameters')
    if not isinstance(params, dict) or any(type(k) is not str or type(v) not in (str, int) for k, v in params.items()):
        return False
    # The registry rejects duplicate/unknown query keys. Ordering and URL
    # escaping can differ; asset, interval and every request bound must match.
    return dict(parse_qsl(parsed.query, keep_blank_values=True)) == {k: str(v) for k, v in params.items()}


def scenario(value):
    exact(value, 'direction horizon mechanism formation_conditions invalidation_conditions', 'scenario')
    require(value['direction'] in ('UPSIDE_SKEW', 'DOWNSIDE_SKEW', 'RANGE', 'TWO_SIDED', 'UNRESOLVED'), 'scenario direction invalid')
    exact(value['horizon'], 'unit minimum maximum', 'horizon')
    require(value['horizon']['unit'] in ('HOURS', 'DAYS', 'WEEKS', 'MONTHS', 'PROCESS'), 'invalid horizon unit')
    low, high = value['horizon']['minimum'], value['horizon']['maximum']
    if value['horizon']['unit'] == 'PROCESS':
        require(low is None and high is None, 'mechanism progress cannot inherit calendar duration')
    else:
        require(type(low) is int and type(high) is int and 0 < low <= high, 'horizon range invalid')
    for key in ('mechanism', 'formation_conditions', 'invalidation_conditions'):
        atoms(value[key], 'scenario.' + key)


def validate_outlook(outlook, revision, resolve, cutoff=None, prior_market=None, lifecycle_root=None):
    """Public preflight before portfolio disclosure; no private inputs needed."""
    exact(outlook, 'semantic narrative refs' + (' market_assessment dispute_zones' if revision >= 5 else ''), 'outlook')
    if revision >= 5:
        from . import market_state
        market_state.validate_assessment(outlook['market_assessment'], resolve, cutoff, revision=revision, prior=prior_market, lifecycle_root=lifecycle_root)
        market_state.validate_zones(outlook['dispute_zones'], resolve, cutoff=cutoff, revision=revision)
    exact(outlook['semantic'], 'status baseline alternatives next_evidence', 'outlook semantics')
    require(outlook['semantic']['status'] == 'ASSESSED', 'new assessment cannot hide outlook as historical unknown')
    scenario(outlook['semantic']['baseline'])
    require(isinstance(outlook['semantic']['alternatives'], list) and outlook['semantic']['alternatives'], 'reasonable alternative scenario required')
    for alternative in outlook['semantic']['alternatives']:
        scenario(alternative)
    if revision >= 9:
        scenarios = [outlook['semantic']['baseline']] + outlook['semantic']['alternatives']
        dated = all(h['forward']['lifecycle']['claim']['scale']['end_ref'] is not None
                    for m in outlook['market_assessment']['markets'] for h in m['horizons'])
        require(dated or all(s['horizon']['unit'] == 'PROCESS' for s in scenarios),
                'new outlook cannot mechanically inherit long calendar forecasts')
    atoms(outlook['semantic']['next_evidence'], 'next discriminating evidence')
    text(outlook['narrative'], 'forward outlook explanation')
    for ref in outlook['refs']:
        resolve(ref)


def validate_analysis_sections(draft, revision, resolve, lifecycle_root=None, frozen_public=False):
    """Reuse the publication structure checks before disclosing a cycle candidate."""
    origin = draft.get('section_provenance', {}).get('outlook', {})
    cutoff = origin.get('assessed_at') if origin.get('mode') == 'REUSED' else draft.get('temporal_scope', {}).get('knowledge_cutoff')
    public_revision=revision
    if revision>=8 and origin.get('mode')!='ASSESSED':
        from .market_state import public_schema_revision
        public_revision=public_schema_revision(draft['outlook'])
    prior_market = None
    if lifecycle_root is not None and public_revision >= 9 and origin.get('mode') != 'REUSED':
        from . import market_state
        prior_market = market_state.effective(lifecycle_root).get('market_assessment')
    historical_read = public_revision >= 9 and (frozen_public or origin.get('mode') == 'REUSED')
    if historical_read:
        if not frozen_public:
            # Reuse is only a read of the exact original ancestor and its
            # original time/evidence identity, never an input-authored bypass.
            require(lifecycle_root is not None, 'reused public result needs its original owner')
            from . import storage, main_flow
            pinned = storage.current(lifecycle_root)
            main_flow.validate_reuse(lifecycle_root, pinned,
                {'revision':0,'outlook':draft['outlook'],'section_provenance':{'outlook':origin}})
        from .run_flow import refs_in
        for ref in refs_in(draft['outlook']): resolve(ref)
        # The original immutable body retains its original verification. A
        # reread does not rerun INITIAL/PENDING/REALIZED against today's prior
        # or grant a newly composed successor any verification privilege.
    else:
        validate_outlook(draft['outlook'], public_revision, resolve, cutoff, prior_market, lifecycle_root)
    robust = draft['robustness']
    exact(robust, 'semantic narrative refs', 'robustness')
    exact(robust['semantic'], 'failed_primary module_impacts adjustment_conditions', 'robustness semantics')
    atoms(robust['semantic']['failed_primary'], 'failed primary explanation')
    require(set(robust['semantic']['module_impacts']) == set(decision_report.MODULES), 'stress case must cover five modules')
    for module, effect in robust['semantic']['module_impacts'].items():
        atoms(effect, module + ' stress impact')
    atoms(robust['semantic']['adjustment_conditions'], 'stress adjustment conditions')
    text(robust['narrative'], 'portfolio robustness explanation')
    for ref in robust['refs']:
        resolve(ref)
    require(set(draft['portfolio_analysis']) == set(decision_report.MODULES), 'five modules required')
    for module, value in draft['portfolio_analysis'].items():
        exact(value, 'semantic narrative', 'module analysis')
        exact(value['semantic'], 'protection_purpose amount_basis' + (' scenario_budget' if revision == 1 else ''), 'module semantics')
        atoms(value['semantic']['protection_purpose'], 'portfolio purpose')
        atoms(value['semantic']['amount_basis'], 'amount basis')
        budget = value['semantic'].get('scenario_budget')
        if budget is not None:
            require(module in ('new_spot', 'new_short'), 'scenario budget only belongs to prospective modules')
            exact(budget, 'amount_u unit eligibility conditions invalidations rationale', 'scenario budget')
            require(budget['unit'] == ('SPOT_CAPITAL_U' if module == 'new_spot' else 'SHORT_NOTIONAL_U'), 'scenario budget unit mismatch')
            require(budget['eligibility'] == 'THESIS_ONLY', 'scenario budget cannot grant entry qualification')
            from decimal import Decimal
            require(Decimal(numbered(budget['amount_u'])) > 0, 'scenario budget must be positive or absent')
            atoms(budget['conditions'], 'budget conditions')
            atoms(budget['invalidations'], 'budget invalidations')
            text(budget['rationale'], 'budget rationale')
        text(value['narrative'], 'module rationale')


def validate(draft, evidence_dir, prior, project, review):
    revision = draft.get('revision', 1)
    require(revision in (1, 2, 3, 4, 5, 6, 7, 8, 9), 'unsupported assessment revision')
    extra = ' revision recommendations maintenance_binding' if revision >= 2 else ''
    if revision >= 3:
        extra += ' conclusion section_provenance research_absorptions'
    if revision >= 4:
        extra += ' factor_assessment'
    report_key = '' if revision >= 3 else ' report'
    exact(draft, 'schema draft_id evidence_id evidence_bindings fact_version temporal_scope domain outlook robustness portfolio_analysis provenance stop_reason' + report_key + extra, 'AssessmentDraft')
    require(draft['schema'] == 'BTC_ASSESSMENT_DRAFT_V4', 'wrong assessment schema')
    draft_hash = digest({k: v for k, v in draft.items() if k != 'draft_id'})
    require(draft['draft_id'] == draft_hash, 'draft identity mismatch')
    package = evidence.validate(read(Path(evidence_dir) / 'evidence.json'), evidence_dir)
    require(draft['evidence_id'] == package['evidence_id'], 'assessment binds different evidence version')
    scope = draft['temporal_scope']
    exact(scope, 'market_cutoff knowledge_cutoff domain_market_cutoff current_market_refs', 'assessment temporal scope')
    require(instant(scope['knowledge_cutoff']) == instant(package['cutoff']), 'knowledge cutoff must bind frozen evidence version')
    require(instant(scope['domain_market_cutoff']) <= instant(scope['market_cutoff']) <= instant(scope['knowledge_cutoff']), 'historical and current market windows are reversed')
    require(isinstance(scope['current_market_refs'], list) and scope['current_market_refs'], 'current market window needs raw bindings')
    raw_sources = {s['id']: s for s in package['sources']}
    for ref in scope['current_market_refs']:
        evidence.resolve(package, evidence_dir, ref, verified=True)
        require(instant(raw_sources[ref['source_id']]['end']) == instant(scope['market_cutoff']), 'current market reference has a different cutoff')
    require(draft['fact_version'] == facts(project), 'local confirmed facts changed during assessment')
    exact(review, 'schema draft_id evidence_id reviewer_role checks unresolved_issues material_new_outlook', 'main review')
    require(type(review['material_new_outlook']) is bool, 'migration outlook materiality needs main-task judgement')
    require(review['schema'] == 'BTC_MAIN_REVIEW_V4' and review['reviewer_role'] == 'MAIN', 'main task review required')
    require(review['draft_id'] == draft_hash and review['evidence_id'] == package['evidence_id'], 'review is stale')
    from . import main_flow
    checks = main_flow.CHECKS if revision >= 3 else 'raw_evidence competing_explanations portfolio_completeness report_fidelity semantic_continuity'
    exact(review['checks'], checks, 'review checks')
    for result in review['checks'].values():
        text(result, 'audit review finding')
    require(review['unresolved_issues'] == [], 'concrete review contradictions remain unresolved')
    if revision >= 3:
        main_flow.validate_contract(draft)
    else:
        exact(draft['provenance'], 'model effort agent_id independent_review candidate_review', 'evaluation provenance')
        require(draft['provenance']['model'] == 'gpt-6-astra' and draft['provenance']['effort'] == 'max', 'full assessment requires GPT-6 Astra/max')
        for key in ('agent_id', 'independent_review', 'candidate_review'):
            text(draft['provenance'][key], 'actual evaluation ' + key)
    require(draft['stop_reason'] in ('SUFFICIENT_FOR_CHOICE', 'INSUFFICIENT_CONSERVATIVE_COMPLETE'), 'evaluation stopping reason required')
    if revision >= 4:
        from . import factors
        factor_revision=revision
        if revision>=8 and draft.get('section_provenance',{}).get('factor_assessment',{}).get('mode')=='REUSED':
            factor_revision=8 if all('decision_review' in i for i in draft['factor_assessment']['items']) else 4
        factors.validate(draft['factor_assessment'], package, evidence_dir, revision=factor_revision)
    validate_analysis_sections(draft, revision, evidence.forward_resolver(package, evidence_dir),
                               Path(project) / '.strategy' / 'v4')
    if revision >= 2:
        from .recommendations import validate as validate_recommendations
        from .closed_loop import public_hash
        validate_recommendations(draft['recommendations'], scope['knowledge_cutoff'],
                                 lambda ref: evidence.resolve(package, evidence_dir, ref, verified=True), revision,
                                 public_hash(draft) if revision >= 6 else None, draft['fact_version'], project=project)
    require(isinstance(draft['evidence_bindings'], dict), 'domain evidence bindings required')
    source_by_id = {s['id']: s for s in package['sources'] if s['trust'] == 'PUBLIC_RAW'}
    for dimension, entry in draft['domain']['evidence_matrix'].items():
        if dimension == 'account_impact' or entry['status'] != 'VERIFIED':
            continue
        for index, source in enumerate(entry['sources']):
            ref_path = '/evidence_matrix/' + dimension + '/sources/' + str(index)
            refs = draft['evidence_bindings'].get(ref_path)
            require(isinstance(refs, list) and refs, 'verified source needs explicit raw-value binding')
            for ref in refs:
                require(ref['source_id'] in source_by_id, 'verified source missing frozen public raw')
                raw = source_by_id[ref['source_id']]
                require(_source_matches_raw(source, raw, evidence_dir), 'domain source points at unrelated raw source')
                require(instant(source['as_of']) <= instant(raw['end']), 'domain source exceeds raw time coverage')
                evidence.resolve(package, evidence_dir, ref, verified=True)
    require(instant(draft['domain']['observation']['observed_at']) <= instant(package['cutoff']), 'decision exceeds evidence cutoff')
    for path, refs in draft['evidence_bindings'].items():
        pointer(draft['domain'], path)
        for ref in refs:
            evidence.resolve(package, evidence_dir, ref, verified=True)
    # Reference existence alone is insufficient for quoted measurements.
    for venue, price in draft['domain']['observation']['raw_prices'].items():
        path = '/observation/raw_prices/' + venue
        refs = draft['evidence_bindings'].get(path, [])
        require(len(refs) == 1, 'each quoted price needs one exact raw numeric binding')
        raw_value = evidence.resolve_number(package, evidence_dir, refs[0], verified=True)
        require(raw_value == numbered(price), 'quoted price differs from frozen raw value')
        sources = draft['domain']['evidence_matrix']['price_quotes']['sources']
        matched = next((s for s in sources if s['venue'] == venue), None)
        require(matched and _source_matches_raw(matched, source_by_id[refs[0]['source_id']], evidence_dir), 'quoted price bound to wrong venue')
    validated = deepcopy(draft)
    historical_clock = prior.get('_verified_domain_replay_clock') if revision >= 3 else None
    validated['domain'] = domain.validate(draft['domain'], prior['canonical_domain'], project, historical_clock,
                                         cycle_recheck_at=prior.get('_cycle_recheck_at'), model_market=revision >= 5)
    if revision < 3:
        report.render(validated)
    return validated
