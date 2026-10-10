"""Version-bound sensing. Public raw storage contains no recommendation or assets."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json
import re
import sqlite3
import market_observation as market
from .common import create, digest, exact, facts, instant, locked, now, read, replace, require, text
from . import recommendations


def observation(root, identity):
    require(isinstance(identity, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', identity), 'invalid sensing identity')
    value = read(Path(root) / 'sensing' / (identity[7:] + '.json'))
    require(value['observation_id'] == identity == digest({k: v for k, v in value.items() if k != 'observation_id'}), 'sensing observation damaged')
    return value


def deep(root):
    root = Path(root)
    pointer = root / 'deep-current.json'
    if not pointer.exists():
        return None, None
    value = read(root / read(pointer)['completion'])
    require(value['completion_hash'] == digest({k: v for k, v in value.items() if k != 'completion_hash'}), 'deep baseline damaged')
    return value, raw_market(root / 'evidence' / value['evidence_id'][7:], value['assessment'])


def raw_market(directory, assessment):
    from . import evidence
    from .market_runtime import validate_file
    directory = Path(directory)
    package = evidence.validate(read(directory / 'evidence.json'), directory)
    sources = {s['id']: s for s in package['sources']}
    for ref in assessment['temporal_scope']['current_market_refs']:
        evidence.resolve(package, directory, ref, verified=True)
        path = directory / 'raw' / (sources[ref['source_id']]['raw_hash'][7:] + '.json')
        value = read(path)
        if value.get('schema') == market.EVIDENCE_SCHEMA:
            validate_file(path)
            require(instant(value['cutoff']) == instant(assessment['temporal_scope']['market_cutoff']), 'raw deep cutoff mismatch')
            return value
    return None


def main_event_review(root, pinned, fact_version):
    """Only the pointed, verified main success can advance event review coverage.

    Unpointed receipts are never discovered or promoted. A damaged active
    pointer/receipt fails closed; a valid receipt for an earlier current
    ancestor or old facts is explicitly inapplicable. Completion/creation
    clocks do not stand in for a frozen market cutoff.
    """
    from . import main_flow
    root = Path(root)
    core = (pinned or {}).get('assessment') or {}
    baseline = {'status': 'CURRENT_CORE_ONLY', 'completion_hash': None,
                'market_cutoff': core.get('temporal_scope', {}).get('market_cutoff')}
    pointer = root / 'main-current.json'
    if not pointer.exists():
        return baseline
    target = read(pointer)
    exact(target, 'completion', 'main completion pointer')
    require(isinstance(target['completion'], str) and re.fullmatch(r'main/[a-f0-9]{64}\.json', target['completion']), 'invalid main completion path')
    path = root / target['completion']
    require(path.resolve().parent == (root / 'main').resolve(), 'main completion escapes receipt directory')
    value = read(path)
    return _main_event_review_value(root, pinned, fact_version, path, value)


def _main_event_review_value(root, pinned, fact_version, path, value):
    from . import main_flow
    core = (pinned or {}).get('assessment') or {}
    baseline = {'status': 'CURRENT_CORE_ONLY', 'completion_hash': None,
                'market_cutoff': core.get('temporal_scope', {}).get('market_cutoff')}
    main_flow.validate_completion(value, path)
    accepted = value['assessment']
    node, seen = pinned, set()
    while node and node['decision_id'] != value['decision_id']:
        parent = node.get('parent_id')
        require(parent not in seen, 'main completion ancestry cycle')
        seen.add(parent)
        node = read(root / 'versions' / parent / 'decision.json') if parent else None
        if node:
            require(node['decision_id'] == digest({k: v for k, v in node.items() if k != 'decision_id'})[7:], 'main completion ancestor damaged')
    require(node is not None, 'main completion decision is not current ancestry')
    published = node.get('assessment') or {}
    require(main_flow.content(accepted) == main_flow.content(published), 'main completion does not confirm its bound decision')
    if value['status'] == 'PUBLISHED':
        require(accepted == published and value['review'] == node['review'], 'published main receipt differs from its actual decision')
    raw = raw_market(root / 'evidence' / value['evidence_id'][7:], accepted)
    require(raw is not None, 'main completion needs verified raw market evidence')
    baseline['completion_hash'] = value['completion_hash']
    if value['decision_id'] != (pinned or {}).get('decision_id'):
        baseline['status'] = 'CURRENT_CORE_ONLY_OLDER_MAIN_DECISION'
    elif accepted['fact_version'] != fact_version or accepted['fact_version'] != core.get('fact_version'):
        baseline['status'] = 'CURRENT_CORE_ONLY_MAIN_FACTS_CHANGED'
    else:
        require(accepted['domain']['event']['cycle'] == core['domain']['event']['cycle'], 'main completion event cycle differs from current core')
        require(instant(raw['cutoff']) >= instant(baseline['market_cutoff']), 'main completion market coverage predates current core')
        baseline.update(status='VERIFIED_MAIN_COMPLETION', market_cutoff=raw['cutoff'])
    return baseline


def _history(db, cutoff):
    if not Path(db).exists():
        return []
    with market._connect(db, readonly=True) as conn:
        # Include the baseline hour itself: corrected measurements keep their original time.
        start = (instant(cutoff) - timedelta(hours=1)).isoformat().replace('+00:00', 'Z') if cutoff else '0000'
        return [json.loads(r[0]) for r in conn.execute('SELECT payload FROM evidence WHERE observed_hour>=? ORDER BY first_seen,evidence_id', (start,))]


def accumulation(prior, history, current):
    if prior is None:
        return {'status': 'RAW_BASELINE_UNAVAILABLE', 'start': None, 'end': current['cutoff'], 'windows': {}, 'revisions': []}
    start, end = market.timestamp(prior['cutoff']), market.timestamp(current['cutoff'])
    require(end >= start, 'observation predates actual successful deep window')
    merged = {k: {r['time']: r for r in d['rows']} for k, d in prior['datasets'].items()}
    old = deepcopy(merged)
    for package in [*history, current]:
        for key, dataset in package['datasets'].items():
            merged.setdefault(key, {}).update({r['time']: r for r in dataset['rows']})
    windows, revisions = {}, []
    for key, dataset in current['datasets'].items():
        rows = sorted(merged[key].values(), key=lambda r: market.timestamp(r['time']))
        for stamp, row in old.get(key, {}).items():
            if stamp in merged[key] and row != merged[key][stamp]:
                revisions.append({'dataset': key, 'time': stamp, 'previous': row, 'revised': merged[key][stamp]})
        # Hourly cumulative windows never add overlapping 4/24/72-hour summaries.
        if dataset['interval_seconds'] == market.HOUR:
            windows[key] = (market.summarize_candles(rows, start, end, market.HOUR) if dataset['kind'] in ('CANDLES', 'MARK', 'INDEX')
                            else market.summarize_point(rows, start, end, dataset['kind']))
    return {'status': 'AVAILABLE', 'start': prior['cutoff'], 'end': current['cutoff'], 'windows': windows, 'revisions': revisions}


def context(root, db, project, current_market, limit=240, compact=True, evidence_dir=None, *, frozen_review=False, workflow_revision=None, fact_snapshot=None):
    from .storage import current
    root = Path(root)
    market.validate_evidence(current_market)
    require(1 <= limit <= 10000, 'context limit out of range')
    pinned = current(root)
    policy = read(root / 'write-policy.json') if (root / 'write-policy.json').exists() else {}
    active_revision = max(((pinned or {}).get('assessment') or {}).get('revision', 1), policy.get('minimum_assessment_revision', 1))
    if workflow_revision is not None:
        require(workflow_revision in (5, 6, 7), 'invalid requested workflow revision')
        active_revision = max(active_revision, workflow_revision)
    factor_workflow = active_revision >= 4 or evidence_dir is not None
    require(not factor_workflow or evidence_dir is not None, 'revision 4 context requires frozen --evidence')
    workflow = active_revision >= 3 or factor_workflow
    # Main strategy operation must not depend on the integrity or continued
    # availability of any legacy specialist-completion artifact.
    completed, prior = (None, None) if workflow else deep(root)
    if pinned and pinned.get('assessment') and not workflow:
        require(completed is not None and instant(completed['completed_at']) >= instant(pinned['created_at']), 'formal version completion pointer needs repair-completion')
    core = ((pinned or {}).get('assessment') or {})
    if workflow:
        prior = raw_market(root / 'evidence' / core['evidence_id'][7:], core) if core else None
    from . import cycle
    cycle_workflow = workflow and cycle.enabled(root)
    # The cycle baseline and identity describe the same personal snapshot.
    # A caller may also reuse its file availability and hashes downstream.
    current_facts = deepcopy(fact_snapshot['fact_version']) if fact_snapshot is not None else facts(project)
    cycle_basis = cycle.baseline(root, core, current_facts) if cycle_workflow else None
    if cycle_workflow:
        prior = cycle.raw_baseline(root, cycle_basis)
    legacy = market.read_context(db, 1)
    latest = read(root / 'sensing-current.json') if (root / 'sensing-current.json').exists() else None
    previous = None
    post_evidence_observation = None
    if latest:
        older = instant(current_market['observed_hour']) < instant(latest['observed_hour'])
        require(frozen_review or not older, 'late older-hour observation cannot replace latest sensing')
        saved = observation(root, latest['observation_id'])
        active_revision = max(active_revision, saved.get('revision', 1))
        require(saved['observed_hour'] == latest['observed_hour'], 'sensing pointer hour mismatch')
        previous = {k: saved[k] for k in ('observation_id', 'observed_hour', 'created_at', 'review', 'measurements', 'triggers')}
        if older:
            # A completed frozen study may be reviewed after another hourly
            # observation. Keep that newer identity for race detection without
            # folding its evidence into the older study or moving its pointer.
            post_evidence_observation, previous = previous, None
    baseline_cutoff = (core.get('temporal_scope') or {}).get('market_cutoff') if workflow else (completed['assessment']['temporal_scope']['market_cutoff'] if completed else None)
    if cycle_workflow:
        baseline_cutoff = cycle_basis['market_cutoff']
    history = _history(db, baseline_cutoff)
    history = [p for p in history if not prior or p['evidence_id'] != prior['evidence_id']]
    def within_frozen_evidence(package):
        return (instant(package['cutoff']) <= instant(current_market['cutoff']) and
                max(instant(r['retrieved_at']) for r in package['retrievals'].values()) <=
                max(instant(r['retrieved_at']) for r in current_market['retrievals'].values()))
    if frozen_review:
        history = [p for p in history if within_frozen_evidence(p)]
    for package in history:
        market.validate_evidence(package)
        later = max(instant(r['retrieved_at']) for r in package['retrievals'].values()) > max(instant(r['retrieved_at']) for r in current_market['retrievals'].values())
        require(not (later and market._has_new_rows(package, current_market)), 'newer public revision requires a new current evidence package')
    event_review = main_event_review(root, pinned, current_facts) if workflow else None
    identity = {'decision_id': (pinned or {}).get('decision_id'),
                'deep_completion_hash': (completed or {}).get('completion_hash'),
                'observation_id': (latest or {}).get('observation_id'),
                'legacy_hour': legacy['baselines'].get('HOUR'), 'fact_version': current_facts,
                'market_evidence_id': current_market['evidence_id'], 'retrieval_id': current_market['retrieval_id'],
                'history_ids': [p['evidence_id'] for p in history]}
    if active_revision >= 6:
        identity['workflow_revision'] = min(7, active_revision)
    research_context = None
    if workflow:
        from . import research
        research_context = research.context(root)
        identity['research_context_id'] = research_context['research_context_id']
        identity['main_completion_hash'] = event_review['completion_hash']
        if cycle_workflow:
            identity['cycle_baseline_id'] = cycle_basis['result_id']
            identity['cycle_baseline_status'] = cycle_basis['status']
            identity['cycle_policy_revision'] = 1
    factor_context = None
    if factor_workflow:
        from . import factors
        factor_context = factors.context(root, project, evidence_dir)
        require(any(source['trust'] == 'PUBLIC_RAW' and read(source['raw_path']) == current_market
                    for source in factor_context['evidence']['sources']),
                'factor evidence must contain the exact current public market package')
        identity['factor_evidence_id'] = factor_context['evidence']['evidence_id']
        identity['factor_guide_hash'] = factor_context['guide']['hash']
    legacy_previous = legacy['previous_hour']
    if frozen_review and legacy_previous:
        legacy_hour = legacy_previous.get('observed_hour') or legacy_previous.get('binding', {}).get('observed_hour')
        if legacy_hour and instant(legacy_hour) > instant(current_market['observed_hour']):
            legacy_previous = None
    value = {'schema': 'BTC_SENSING_CONTEXT_V4', 'context_id': digest(identity), 'identity': identity,
             'current_recommendations': ((pinned or {}).get('assessment') or {}).get('recommendations'),
             'recommendations_issued_at': (pinned or {}).get('created_at'),
             'watch_targets': recommendations.watch_targets(pinned),
             'successful_deep': ({k: completed[k] for k in ('completion_hash', 'completed_at', 'fact_version', 'evidence_id')} | {'assessment': {k: completed['assessment'][k] for k in ('temporal_scope', 'outlook', 'robustness', 'portfolio_analysis')}, 'recommendations': completed['assessment'].get('recommendations')}) if completed else None,
             'previous_hour': previous or legacy_previous,
             'since_deep': [{'evidence_id': p['evidence_id'], 'observed_hour': p['observed_hour'], 'cutoff': p['cutoff']} for p in history[-limit:]],
             'since_deep_total': len(history), 'since_deep_truncated': len(history) > limit,
             'accumulation': accumulation(prior, history, current_market),
             'reported': read(root / 'reported-current.json') if (root / 'reported-current.json').exists() else None}
    if post_evidence_observation:
        value['post_evidence_observation'] = post_evidence_observation
        value['post_evidence_review_required'] = 'Review later observation applicability separately; it is not evidence available at the frozen study cutoff.'
    value['event_cycle'] = core.get('domain', {}).get('event') if workflow else (completed['assessment']['domain']['event'] if completed else None)
    event_cutoff = event_review['market_cutoff'] if workflow else baseline_cutoff
    value['due_event_reviews'] = [event['event_id'] for event in ((value['event_cycle'] or {}).get('cycle') or {}).get('events', [])
                                  if event_cutoff and instant(event_cutoff) < instant(event['due_at']) <= instant(current_market['cutoff'])]
    # This is an observation of conditions, never a maintained/withdrawn conclusion.
    value['measurements'] = measure(value, current_market)
    if workflow:
        value['revision'] = min(7, active_revision) if active_revision >= 5 else 4 if factor_workflow else 3
        if factor_workflow:
            value['factor_context'] = factor_context
        value['current_conclusion'] = core.get('conclusion')
        value['event_review_baseline'] = event_review
        value['current_core'] = {'decision_id': (pinned or {}).get('decision_id'), 'created_at': (pinned or {}).get('created_at'),
            'market_cutoff': baseline_cutoff, 'fact_version': core.get('fact_version'),
            'outlook': core.get('outlook'), 'robustness': core.get('robustness'), 'portfolio_analysis': core.get('portfolio_analysis')}
        value['accumulation']['baseline_role'] = 'CURRENT_CORE'
        value['research'] = research_context
        if cycle_workflow:
            value['cycle_baseline'] = cycle_basis
            value['accumulation']['baseline_role'] = 'ADOPTED_ASTRA_CYCLE'
            value['current_core']['market_cutoff'] = core.get('temporal_scope', {}).get('market_cutoff')
        for issue in research_context['issues']:
            records = _history(db, issue['accumulation_start'])
            if frozen_review:
                records = [p for p in records if within_frozen_evidence(p)]
            base = next((p for p in records if p['evidence_id'] == issue['baseline_market_evidence_id']), None)
            if base is None:
                # A research request may precede the corresponding public DB
                # append; its immutable package is still a valid raw anchor.
                latest = issue['latest_result'] or issue['first_request']
                req = research._load(root, 'requests', latest['request_id'])
                directory = root / 'evidence' / req['evidence_id'][7:]
                package = read(directory / 'evidence.json')
                for source in package['sources']:
                    candidate_raw = read(directory / 'raw' / (source['raw_hash'][7:] + '.json'))
                    if isinstance(candidate_raw, dict) and candidate_raw.get('evidence_id') == issue['baseline_market_evidence_id']:
                        base = candidate_raw
                        break
            if base:
                market.validate_evidence(base)
            issue['accumulation'] = accumulation(base, records, current_market)
        if active_revision >= 5:
            from . import market_state
            value['market_baseline'] = market_state.effective(root, core, (pinned or {}).get('decision_id'))
        if compact:
            value = compact_context(value)
    return value


def compact_context(value):
    """Keep decisions and evidence addresses; raw measurements remain available on demand."""
    value = deepcopy(value)
    previous = value.get('previous_hour')
    if previous and 'review' in previous:
        previous['review'] = {k: previous['review'][k] for k in ('main_evaluation', 'research_findings', 'factor_checks', 'applicability_review') if k in previous['review']}
        previous.pop('measurements', None)
    if value.get('successful_deep'):
        value['successful_deep'] = {k: value['successful_deep'][k] for k in ('completion_hash', 'completed_at', 'fact_version', 'evidence_id')}
        value['successful_deep']['role'] = 'LEGACY_RESEARCH_HISTORY_NOT_CURRENT_CORE'
    for observed in value['measurements']:
        for tier in observed['tiers']:
            samples = tier.pop('samples')
            tier['sample_count'] = len(samples)
            tier['touched'] = any(s['touched'] for s in samples)
            tier['observed_relations'] = sorted({s['close_relation'] for s in samples})
            tier['raw_addresses'] = [{'dataset': s['dataset'], 'time': s['time']} for s in samples if s['touched']]
    return value


def measure(ctx, raw):
    evaluated = max(instant(r['retrieved_at']) for r in raw['retrievals'].values())
    result = []
    for target in ctx['watch_targets']:
        side = ctx['current_recommendations'][target['side']]
        issued = max(instant(side['validity']['from']), instant(ctx['recommendations_issued_at']))
        previous = ctx.get('previous_hour') or {}
        prior_hour = previous.get('observed_hour') or previous.get('binding', {}).get('observed_hour')
        window_start = issued.replace(minute=0, second=0, microsecond=0)
        if prior_hour:
            window_start = max(window_start, instant(prior_hour) + timedelta(hours=1))
        window_end = instant(raw['cutoff'])
        expected = max(0, int((window_end - window_start).total_seconds() / 3600))
        observed = {'side': target['side'], 'expired': evaluated >= instant(side['validity']['until']), 'tiers': [], 'gaps': []}
        for index, tier in enumerate(side['tiers']):
            samples = []
            for name in ('binance_spot_1h', 'coinbase_spot_1h', 'kraken_spot_1h', 'binance_futures_1h'):
                dataset = raw['datasets'].get(name)
                rows = [r for r in (dataset or {}).get('rows', []) if window_start <= instant(r['time']) < window_end]
                if len(rows) != expected:
                    observed['gaps'].append(name + ': aligned candles ' + str(len(rows)) + '/' + str(expected))
                for row in rows:
                    start = instant(row['time'])
                    after_issue = start >= issued
                    overlaps = start + timedelta(hours=1) > issued
                    hit = row['low'] <= float(tier['price_high_usd']) and row['high'] >= float(tier['price_low_usd'])
                    samples.append({'dataset': name, 'quote_unit': dataset['quote_unit'], 'comparison': 'DIRECT_USDT_REFERENCE' if dataset['quote_unit'] == 'USDT' else 'USD_REFERENCE_ONLY_NO_FX_CONVERSION', 'source_group': dataset['group'], 'time': row['time'], 'high': row['high'], 'low': row['low'], 'close': row['close'],
                                'touched': bool(hit and overlaps), 'timing': 'AFTER_ISSUE' if after_issue else ('PARTIAL_ISSUE_HOUR' if overlaps else 'BEFORE_ISSUE'),
                                'close_relation': 'BELOW' if row['close'] < float(tier['price_low_usd']) else ('ABOVE' if row['close'] > float(tier['price_high_usd']) else 'WITHIN')})
            observed['tiers'].append({'tier_ref': target['recommendation_ref'] + '/tiers/' + str(index), 'samples': samples,
                                     'cross_source_conflict': any(len({s['touched'] for s in samples if s['time'] == stamp}) > 1 for stamp in {s['time'] for s in samples})})
        result.append(observed)
    return result


def validate_extra_findings(findings, known):
    require(isinstance(findings, list), 'extra_findings: expected list')
    for index, item in enumerate(findings):
        field = 'extra_findings[' + str(index) + ']'
        exact(item, 'summary refs material', field)
        text(item['summary'], field + '.summary')
        refs = item['refs']
        require(isinstance(refs, list) and refs and all(isinstance(ref, str) for ref in refs),
                field + '.refs: expected nonempty list of market_refs string IDs from vocabulary.json')
        require(set(refs) <= known, field + '.refs: unknown current market_refs ID')
        require(type(item['material']) is bool, field + '.material: expected boolean')


def validate_public(root, ctx, raw, authored, evidence_dir):
    """The same public preflight runs before freeze and before observation commit."""
    from . import evidence, factors, market_state, cycle, research
    bundle = evidence.validate(read(Path(evidence_dir) / 'evidence.json'), evidence_dir)
    resolve = lambda ref: evidence.resolve(bundle, evidence_dir, ref, verified=True)
    rev = ctx.get('revision', 5)
    market_state.validate_assessment(authored['market_assessment'], resolve, bundle['cutoff'], rev,
        ctx.get('market_baseline', {}).get('market_assessment'))
    market_state.validate_zones(authored['dispute_zones'], resolve,
        ctx.get('market_baseline', {}).get('dispute_zones', []), bundle['cutoff'], rev)
    factors.validate(authored['factor_checks'], bundle, evidence_dir)
    if 'cycle_baseline' in ctx:
        cycle.validate_applicability(authored['applicability_review'], ctx, bundle, evidence_dir)
    candidate = deepcopy(authored['market_review'])
    candidate.update(schema=market.REVIEW_SCHEMA, evidence_id=raw['evidence_id'], observed_hour=raw['observed_hour'],
                     context_id=ctx['context_id'], model=dict(market.CURRENT_HOURLY_MODEL))
    completed = ctx.get('cycle_baseline') if ctx.get('cycle_baseline', {}).get('result_id') else ctx.get('current_core')
    validation = {'context_id': ctx['context_id'], 'baselines': {}, 'successful_deep': None}
    if ctx.get('previous_hour'):
        validation['baselines']['HOUR'] = True
    if completed:
        validation['baselines']['DEEP'] = {'completed_at': completed.get('completed_at') or completed.get('created_at')}
    market.validate_review(raw, candidate, validation, model_market=True)
    known = market.reference_ids(raw)
    validate_extra_findings(authored['extra_findings'], known)
    research.findings(root, authored['research_findings'], known, bundle, evidence_dir)


def record(root, db, project, raw, review, evidence_dir=None, workflow_revision=None):
    root = Path(root)
    if evidence_dir is not None:
        from .storage import archive_evidence
        evidence_dir = archive_evidence(root, evidence_dir)['directory']
    with locked(root):
        ctx = context(root, db, project, raw, compact=False, evidence_dir=evidence_dir, workflow_revision=workflow_revision)
        workflow = ctx.get('revision', 1) >= 3
        factor_workflow = ctx.get('revision', 1) >= 4
        cycle_workflow = 'cycle_baseline' in ctx
        if cycle_workflow:
            require('applicability_review' in review, 'applicability_review required; use a current observation template')
        exact(review, 'schema context_id market_review recommendation_checks extra_findings' + (' revision main_evaluation research_findings' if workflow else '') + (' factor_checks' if factor_workflow else '') + (' applicability_review' if cycle_workflow else '') + (' market_assessment dispute_zones market_result_hash' if ctx.get('revision', 1) >= 5 else '') + (' allocation_review' if ctx.get('revision', 1) >= 6 else ''), 'v4 sensing review')
        require(review['schema'] == 'BTC_SENSING_REVIEW_V4' and review['context_id'] == ctx['context_id'], 'stale recommendation sensing context')
        if workflow:
            require(review['revision'] == ctx['revision'], 'core workflow sensing revision mismatch')
            exact(review['main_evaluation'], 'required reasons', 'main evaluation')
            require(type(review['main_evaluation']['required']) is bool and isinstance(review['main_evaluation']['reasons'], list), 'main evaluation judgement required')
            require(not review['main_evaluation']['required'] or review['main_evaluation']['reasons'], 'main evaluation needs its core reason')
            for reason in review['main_evaluation']['reasons']:
                text(reason, 'main evaluation reason')
        factor_package = None
        if factor_workflow:
            from . import evidence, factors
            factor_package = evidence.validate(read(Path(evidence_dir) / 'evidence.json'), evidence_dir)
            factors.validate(review['factor_checks'], factor_package, evidence_dir)
        if ctx.get('revision', 1) >= 5:
            from . import market_state
            validate_public(root, ctx, raw, review, evidence_dir)
            resolve = lambda ref: evidence.resolve(factor_package, evidence_dir, ref, verified=True)
            public = {k: review[k] for k in market_state.MARKET_FIELDS if k in review}
            public['market_review'] = {k:v for k,v in public['market_review'].items() if k not in ('schema','context_id','model','evidence_id','observed_hour')}
            require(review['market_result_hash'] == digest(public), 'portfolio stage changed the frozen market result')
            if ctx['revision'] >= 6:
                from .closed_loop import validate_allocation_review
                validate_allocation_review(review['allocation_review'], ctx, review['market_result_hash'])
        if cycle_workflow:
            from .cycle import validate_applicability
            validate_applicability(review['applicability_review'], ctx, factor_package, evidence_dir)
        candidate = review['market_review']
        require(candidate['model'] == market.CURRENT_HOURLY_MODEL, 'hourly sensing requires gpt-6.1-sol/high')
        completed = (ctx['cycle_baseline'] if ctx['cycle_baseline']['result_id'] else None) if cycle_workflow else (ctx['current_core'] if workflow else ctx['successful_deep'])
        validation_context = {'context_id': ctx['context_id'], 'baselines': {}, 'successful_deep': None}
        if ctx['previous_hour']:
            validation_context['baselines']['HOUR'] = True
        if completed:
            validation_context['baselines']['DEEP'] = {'completed_at': completed['completed_at'] if cycle_workflow else (completed.get('created_at') if workflow else completed['completed_at'])}
            if cycle_workflow:
                from .cycle import raw_baseline
                prior = raw_baseline(root, ctx['cycle_baseline'])
            elif workflow:
                from .storage import current
                accepted = current(root)['assessment']
                prior = raw_market(root / 'evidence' / accepted['evidence_id'][7:], accepted)
            else:
                _, prior = deep(root)
            validation_context['successful_deep'] = {'evidence': prior}
        market.validate_review(raw, candidate, validation_context, model_market=ctx.get('revision', 1) >= 5)
        checks = review['recommendation_checks']
        require(isinstance(checks, list) and len(checks) == len(ctx['watch_targets']) and {c.get('side') for c in checks} == {t['side'] for t in ctx['watch_targets']}, 'each current recommendation needs a sensing check')
        known_refs = market.reference_ids(raw)
        for check in checks:
            exact(check, 'side findings refs explanation', 'recommendation sensing check')
            require(isinstance(check['findings'], list) and check['findings'] and set(check['findings']) <= {'NO_MATERIAL_CHANGE', 'SUPPORT_WEAKENED', 'COUNTEREVIDENCE', 'AMOUNT_BASIS_CHANGED', 'GAP', 'OTHER_MATERIAL'}, 'invalid recommendation finding')
            require(isinstance(check['refs'], list) and check['refs'] and set(check['refs']) <= known_refs, 'recommendation finding needs current evidence refs')
            text(check['explanation'], 'sensing explanation')
        validate_extra_findings(review['extra_findings'], known_refs)
        triggers = []
        if ctx.get('revision', 1) >= 6 and review['allocation_review']['outcome'] == 'REVISE':
            triggers.append('ALLOCATION_REVIEW_REQUIRED')
        if candidate['deep_review']['required']:
            triggers.append('MARKET_CHANGE')
        for check in checks:
            triggers.extend(check['side'] + ':' + f for f in check['findings'] if f not in ('NO_MATERIAL_CHANGE', 'GAP'))
        for measured in ctx['measurements']:
            if measured['expired']:
                triggers.append(measured['side'] + ':EXPIRY')
            if any(s['touched'] for t in measured['tiers'] for s in t['samples']):
                triggers.append(measured['side'] + ':PRICE_TOUCH_REVIEW')
        if any(f['material'] for f in review['extra_findings']):
            triggers.append('EXTRA_DISCOVERY')
        if ctx['due_event_reviews']:
            triggers.append('EVENT_WINDOW_REVIEW')
        fact_baseline = (ctx.get('current_core') or {}).get('fact_version') if workflow else (completed or {}).get('fact_version')
        if ctx['identity']['fact_version'] != fact_baseline:
            triggers.append('FACTS_CHANGED')
        if not ctx['current_recommendations']:
            triggers.append('RECOMMENDATION_BASELINE_MISSING')
        research_issues = []
        if workflow:
            from . import research
            research_issues = research.findings(root, review['research_findings'], known_refs, factor_package, evidence_dir)
            gate = {'research_eligible': any(v['eligible'] for v in research_issues),
                    'main_eligible': bool(triggers or review['main_evaluation']['required']),
                    'baseline_comparison': 'CURRENT_CORE', 'periodic_review_due': False,
                    'initialization_review_due': False, 'has_new_valid_evidence': None}
            gate['deep_eligible'] = gate['research_eligible']
            if cycle_workflow:
                from .cycle import review_gate
                cycle_gate = review_gate(root, ctx, review, research_issues)
                gate.update(cycle_review=cycle_gate, baseline_comparison='ADOPTED_ASTRA_CYCLE',
                    initialization_review_due=ctx['cycle_baseline']['result_id'] is None,
                    periodic_review_due=ctx['cycle_baseline']['status'] == 'REVIEW_DUE',
                    # These clocks/touches require the observation just made,
                    # not rewriting every approved section or extending advice.
                    main_eligible=bool(review['main_evaluation']['required'] or
                        [t for t in triggers if not (t.endswith(':EXPIRY') or t.endswith(':PRICE_TOUCH_REVIEW')
                                                    or t == 'EVENT_WINDOW_REVIEW')]),
                    research_eligible=cycle_gate['required'], deep_eligible=cycle_gate['required'])
        else:
            from .hourly import review_gate
            _, prior = deep(root)
            gate = review_gate(candidate, raw, (completed or {}).get('completed_at'), prior)
        if (root / 'sensing-current.json').exists():
            previous = observation(root, read(root / 'sensing-current.json')['observation_id'])
            before = previous['context']['identity']
            keys = ('decision_id', 'deep_completion_hash', 'fact_version', 'market_evidence_id') + (('research_context_id', 'main_completion_hash') if workflow else ()) + (('factor_evidence_id', 'factor_guide_hash') if factor_workflow else ()) + (('cycle_baseline_id', 'cycle_baseline_status', 'cycle_policy_revision') if cycle_workflow else ())
            same_judgement = not workflow or all(previous['review'].get(k) == review.get(k) for k in ('main_evaluation', 'research_findings', 'recommendation_checks', 'extra_findings', 'factor_checks', 'applicability_review'))
            if ctx.get('revision', 1) >= 5:
                same_judgement = same_judgement and all(previous['review'].get(k) == review.get(k)
                    for k in ('market_assessment', 'dispute_zones', 'allocation_review'))
            if workflow:
                # Re-reading the same raw facts can change the model's account
                # of them. Only the context identity is bookkeeping here.
                prior_market = {k: v for k, v in previous['review']['market_review'].items() if k != 'context_id'}
                reviewed_market = {k: v for k, v in candidate.items() if k != 'context_id'}
                same_judgement = same_judgement and prior_market == reviewed_market
            if previous['gate'] == gate and all(before.get(k) == ctx['identity'].get(k) for k in keys) and same_judgement and [m['expired'] for m in previous['measurements']] == [m['expired'] for m in ctx['measurements']]:
                result = {'status': 'UNCHANGED', 'observation_id': previous['observation_id'], 'deep_eligible': previous['deep_eligible'], 'triggers': previous['triggers'], 'maintenance_status': 'OBSERVATION_ONLY'}
                if workflow:
                    result.update(main_eligible=previous['main_eligible'], research_eligible=previous['research_eligible'], research_findings=previous['research_findings'])
                return result
        # Raw data alone goes to the existing public DB. Recommendation text stays here.
        with market._connect(db) as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM evidence WHERE evidence_id=?', (raw['evidence_id'],)).fetchone() is None:
                revision = conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM evidence WHERE observed_hour=?', (raw['observed_hour'],)).fetchone()[0]
                conn.execute('INSERT INTO evidence VALUES(?,?,?,?,?)', (raw['evidence_id'], raw['observed_hour'], revision, market.canonical(raw), now()))
            market._store_retrieval(conn, raw)
        value = {'schema': 'BTC_SENSING_OBSERVATION_V4', 'context': ctx, 'review': review, 'measurements': ctx['measurements'],
                 'market_evidence_id': raw['evidence_id'], 'observed_hour': raw['observed_hour'], 'created_at': now(),
                 'triggers': sorted(set(triggers)), 'deep_eligible': gate['deep_eligible'] if workflow else bool(triggers or gate['deep_eligible']), 'gate': gate}
        if workflow:
            value.update(revision=ctx['revision'], main_eligible=gate['main_eligible'], research_eligible=gate['research_eligible'], research_findings=research_issues)
        value['observation_id'] = digest(value)
        create(root / 'sensing' / (value['observation_id'][7:] + '.json'), value)
        replace(root / 'sensing-current.json', {'observation_id': value['observation_id'], 'observed_hour': value['observed_hour']})
        result = {'observation_id': value['observation_id'], 'deep_eligible': value['deep_eligible'], 'triggers': value['triggers'], 'maintenance_status': 'OBSERVATION_ONLY'}
        if workflow:
            result.update(main_eligible=value['main_eligible'], research_eligible=value['research_eligible'], research_findings=research_issues, report_review_required=True)
        return result


def gate(root, identity):
    from .storage import current
    root = Path(root)
    value = observation(root, identity)
    require(read(root / 'sensing-current.json')['observation_id'] == identity, 'new observation supersedes this gate')
    bound = value['context']['identity']
    require(bound['decision_id'] == current(root)['decision_id'], 'old sensing judgement cannot apply to new core conclusion')
    if value.get('revision', 1) >= 3:
        if value.get('revision', 1) >= 4:
            from . import evidence
            directory = root / 'evidence' / bound['factor_evidence_id'][7:]
            package = evidence.validate(read(directory / 'evidence.json'), directory)
            require(package['evidence_id'] == bound['factor_evidence_id'], 'factor evidence binding changed')
            guide = Path(value['context']['factor_context']['guide']['path'])
            current_hash = digest(guide.read_bytes()) if guide.exists() else None
            require(current_hash == bound['factor_guide_hash'], 'factor guide changed; refresh observation context')
        from . import research
        completed = main_event_review(root, current(root), bound['fact_version'])
        require(bound.get('main_completion_hash') == completed['completion_hash'], 'new main completion requires a fresh sensing judgement')
        result = {k: value[k] for k in ('observation_id', 'main_eligible', 'research_eligible', 'deep_eligible', 'research_findings', 'triggers', 'gate')}
        from . import reporting
        result['report_review_required']=reporting.review_required(root)
        if bound['research_context_id'] != research.context(root)['research_context_id']:
            # An optional result must be absorbed by the main assessment. It
            # cannot force a new hour or prohibit committing that assessment.
            result.update(research_eligible=False, deep_eligible=False, research_revalidation_required=True)
            if 'cycle_baseline' in value['context']:
                result['main_eligible'] = True  # Absorb/reject the completed study after real review.
        return result
    completed, _ = deep(root)
    require(bound['deep_completion_hash'] == (completed or {}).get('completion_hash'), 'old sensing judgement cannot apply to new recommendation/deep baseline')
    return {k: value[k] for k in ('observation_id', 'deep_eligible', 'triggers', 'gate')}


def assert_current(root, project, draft, evidence_dir):
    binding = draft['maintenance_binding']
    exact(binding, 'context_id market_evidence_id', 'maintenance binding')
    raw = raw_market(evidence_dir, draft)
    require(raw is not None, 'recommendation assessment requires full current public market package')
    ctx = context(root, Path(project) / '.strategy/market_observation.sqlite3', project, raw,
                  evidence_dir=evidence_dir if draft.get('revision', 1) >= 4 else None,
                  frozen_review=draft.get('revision', 1) >= 3)
    require(binding == {'context_id': ctx['context_id'], 'market_evidence_id': raw['evidence_id']}, 'new observation, fact, recommendation or deep baseline requires main review before commit')
    if draft['domain']['runtime']['trigger_type'] == 'SCHEDULED_STRATEGY_BOUNDARY':
        require(ctx['identity']['observation_id'] is not None, 'scheduled assessment requires actual sensing observation')
        field = 'main_eligible' if draft.get('revision', 1) >= 3 else 'deep_eligible'
        require(gate(root, ctx['identity']['observation_id'])[field], 'scheduled assessment needs actual v4 sensing trigger')
