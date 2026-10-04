"""Main strategy ownership. No market inference and no delivery side effects."""
from copy import deepcopy
from pathlib import Path
from .common import digest, exact, instant, pointer, read, require, text
from . import semantics

SECTIONS = ('domain', 'outlook', 'robustness', 'portfolio_analysis', 'recommendations', 'conclusion')
CHECKS = 'raw_evidence competing_explanations portfolio_completeness semantic_continuity research_absorption'


def validate_completion(value, path):
    """Bind the submitted identity separately from its validated legacy view."""
    from . import domain
    keys = 'schema draft_id evidence_id completed_at decision_id status assessment review completion_hash'
    if 'submitted_draft' in value:
        keys += ' submitted_draft'
    exact(value, keys, 'main completion')
    require(value['schema'] == 'BTC_MAIN_COMPLETION_V3' and value['status'] in ('PUBLISHED', 'CONFIRMED_UNCHANGED'), 'invalid main completion status')
    require(value['completion_hash'] == digest({k: v for k, v in value.items() if k != 'completion_hash'}), 'main completion damaged')
    accepted = value['assessment']
    submitted = value.get('submitted_draft', accepted)
    require(accepted.get('revision', 1) >= 3 and value['draft_id'] == accepted['draft_id'] == submitted['draft_id'], 'main completion draft binding mismatch')
    require(submitted['draft_id'] == digest({k: v for k, v in submitted.items() if k != 'draft_id'}), 'main completion assessment damaged')
    domain.validate_normalization(submitted, accepted)
    identity = value['completion_hash'] if 'submitted_draft' in value else value['draft_id']
    require(identity == 'sha256:' + Path(path).stem, 'main completion path binding mismatch')
    require(value['review']['draft_id'] == value['draft_id'] and value['review']['evidence_id'] == value['evidence_id'] == accepted['evidence_id'], 'main completion evidence/review binding mismatch')
    validate_contract(accepted, historical=True)
    instant(value['completed_at'])


def sections_for_revision(revision):
    require(revision in (3, 4, 5, 6, 7), 'unsupported main assessment revision')
    return SECTIONS + (('factor_assessment',) if revision >= 4 else ())


def validate_contract(draft, *, historical=False):
    exact(draft['provenance'], 'role model effort agent_id run_ref', 'main provenance')
    p = draft['provenance']
    models = {'gpt-6.1-sol', 'gpt-6-sol', 'gpt-5.6-sol'} if historical else {'gpt-6.1-sol'}
    require(p['role'] == 'MAIN' and p['model'] in models and p['effort'] == 'high',
            'new main assessment requires actual gpt-6.1-sol/high (Sol/high MAIN); historical completions retain their original model')
    for key in ('agent_id', 'run_ref'):
        text(p[key], 'actual main ' + key)
    c = draft['conclusion']
    exact(c, 'business_meaning narrative', 'current core conclusion')
    require(isinstance(c['business_meaning'], dict) and c['business_meaning'], 'completed core conclusion needs reviewed business meaning')
    # Stable keys are open-ended; the reviewed statements carry meaning, not taxonomy labels.
    for key, statement in c['business_meaning'].items():
        text(key, 'business meaning key')
        text(statement, 'business meaning statement')
    text(c['narrative'], 'current explanation')
    require(isinstance(draft['research_absorptions'], list), 'main research absorption must be a list')
    exact(draft['section_provenance'], ' '.join(sections_for_revision(draft['revision'])), 'section provenance')
    for section, provenance in draft['section_provenance'].items():
        exact(provenance, 'mode decision_id assessed_at evidence_id explanation', 'section provenance ' + section)
        require(provenance['mode'] in ('ASSESSED', 'REUSED'), 'invalid section reuse mode')
        require(instant(provenance['assessed_at']) <= instant(draft['temporal_scope']['knowledge_cutoff']), 'section provenance exceeds knowledge cutoff')
        text(provenance['explanation'], 'section applicability review')
        if provenance['mode'] == 'ASSESSED':
            require(provenance['decision_id'] is None, 'newly assessed section cannot claim prior authorship')
            require(provenance['evidence_id'] == draft['evidence_id'], 'assessed section needs current evidence binding')
        else:
            text(provenance['decision_id'], 'reused section version')
            text(provenance['evidence_id'], 'reused evidence version')


def validate_publication(root, prior, draft):
    """A carried section keeps its actual original evidence time and identical payload."""
    for section, p in draft['section_provenance'].items():
        if p['mode'] != 'REUSED':
            continue
        node = prior
        while node and node['decision_id'] != p['decision_id']:
            node = read(Path(root) / 'versions' / node['parent_id'] / 'decision.json') if node.get('parent_id') else None
        require(node and node.get('assessment'), 'reused section is not an actual ancestor assessment')
        original = node['assessment']
        require(original.get(section) == draft[section], 'reused section changed its original content: ' + section)
        origin = original.get('section_provenance', {}).get(section)
        actual_time = origin['assessed_at'] if origin else original['temporal_scope']['knowledge_cutoff']
        actual_evidence = origin['evidence_id'] if origin else original['evidence_id']
        require(instant(p['assessed_at']) == instant(actual_time) and p['evidence_id'] == actual_evidence, 'reused section falsifies evidence provenance')
    if draft.get('revision', 1) >= 5:
        from . import market_state
        previous_zones = market_state.effective(root, prior['assessment'], prior['decision_id'])['dispute_zones']
        # Identical carried market sections retain their original validation.
        if draft['section_provenance']['outlook']['mode'] != 'REUSED':
            market_state.validate_zones(draft['outlook']['dispute_zones'], lambda ref: None, prior=previous_zones, revision=draft['revision'])
            market_state.validate_assessment(draft['outlook']['market_assessment'], lambda ref: None,
                draft['temporal_scope']['knowledge_cutoff'], draft['revision'], prior['assessment'].get('outlook', {}).get('market_assessment'))
    from .research import validate_absorptions
    validate_absorptions(root, draft)
    from .cycle import validate_publication as validate_cycle_publication
    validate_cycle_publication(root, prior, draft)


def reported_assessment(root):
    path = Path(root) / 'reported-current.json'
    if not path.exists():
        return None
    reported = read(path)
    identity = reported.get('decision_id')
    if identity:
        version = Path(root) / 'versions' / identity / 'decision.json'
        require(version.exists(), 'reported baseline decision missing')
        return read(version).get('assessment')
    return None


def domain_validation_baseline(root, prior, draft):
    """Replay a carried domain against its original transition baseline.

    Its old final-event receipt is historical, not another cycle completion.
    The current cycle still has to be reviewed when its real deadline arrives.
    """
    from .storage import domain_baseline
    from .common import now
    p = draft['section_provenance']['domain']
    if p['mode'] != 'REUSED':
        baseline = domain_baseline(root, prior)
        from . import cycle
        if cycle.enabled(root):
            # The actual adopted Astra mapping replaces legacy hourly-pattern
            # authority, and can shorten an outstanding event review deadline.
            cycle.validate_publication(root, prior, draft)
            saved, _ = cycle.adopted(root, draft)
            if saved is not None:
                baseline = dict(baseline, _cycle_recheck_at=saved['cycle_review']['recheck_at'])
        return baseline
    validate_publication(root, prior, draft)
    cycle = draft['domain']['event']['cycle']
    final = next(e for e in cycle['events'] if e['event_id'] == cycle['final_expiry_event_id'])
    require(instant(final['due_at']) > max(instant(now()), instant(draft['temporal_scope']['knowledge_cutoff'])),
            'current final event requires a fresh main domain review, not historical reuse')
    origin = read(Path(root) / 'versions' / p['decision_id'] / 'decision.json')
    while origin['assessment'].get('section_provenance', {}).get('domain', {}).get('mode') == 'REUSED':
        identity = origin['assessment']['section_provenance']['domain']['decision_id']
        origin = read(Path(root) / 'versions' / identity / 'decision.json')
    require(origin.get('parent_id'), 'reused domain has no original transition baseline')
    parent = read(Path(root) / 'versions' / origin['parent_id'] / 'decision.json')
    baseline = dict(domain_baseline(root, parent))
    baseline['_verified_domain_replay_clock'] = instant(origin['assessment']['temporal_scope']['knowledge_cutoff'])
    return baseline


def report_comparison(root, accepted, prior, material_new_outlook=False):
    """Only actual accepted delivery can move this baseline; main publication cannot."""
    reported = reported_assessment(root)
    if reported is not None:
        return semantics.compare(accepted, reported, material_new_outlook=material_new_outlook)
    # An imported legacy delivery may only have the domain projection.
    baseline_path = Path(root) / 'reported-current.json'
    if baseline_path.exists():
        baseline = read(baseline_path)
        artifact_path = Path(root) / 'outbox' / baseline.get('delivery_id', '') / 'artifact.json'
        if artifact_path.exists():
            artifact = read(artifact_path)
            old_id = artifact.get('decision_id')
            if old_id:
                old = read(Path(root) / 'versions' / old_id / 'decision.json')
                return semantics.compare(accepted, old.get('assessment'), old['canonical_domain'], material_new_outlook)
    return semantics.compare(accepted, None)


def content(assessment):
    """Raw bindings, evaluation clocks and task receipts are not a conclusion update."""
    if not assessment:
        return None
    value = {'semantic': semantics.stable(assessment)}
    for field in ('outlook', 'robustness'):
        value[field] = assessment[field]['narrative']
    value['portfolio'] = {k: v['narrative'] for k, v in assessment['portfolio_analysis'].items()}
    value['recommendations'] = {s: {k: assessment['recommendations'][s][k] for k in ('rationale', 'impact')} for s in ('upper_short', 'lower_spot')} if 'recommendations' in assessment else None
    value['conclusion'] = assessment.get('conclusion')
    value['research_absorptions'] = assessment.get('research_absorptions', [])
    # Model-authored factor analysis can change the internal current assessment
    # without changing adopted business meaning or reporting requirements.
    factors = assessment.get('factor_assessment')
    if factors is not None:
        value['factor_assessment'] = {'summary': factors['summary'], 'items': [
            {key: item[key] for key in ('id', 'name', 'assessment', 'change_reason')}
            for item in factors['items']]}
    return value


def projection(assessment):
    """Internal current-plan view; report authorship and acceptance are separate."""
    from .recommendations import sequences
    lines = ['# 当前核心策略', '', '行情截止：' + assessment['temporal_scope']['market_cutoff'],
             '证据截止：' + assessment['temporal_scope']['knowledge_cutoff'], '', assessment['conclusion']['narrative'], '',
             '## 价值判断', '']
    if assessment.get('revision', 1) >= 6:
        from .closed_loop import allocation, risk
        decision = risk(assessment) if assessment.get('revision', 1) >= 7 else allocation(assessment)
        title = '资产风险：' if assessment.get('revision', 1) >= 7 else '配置决定：'
        lines += ['市场结论：' + assessment['outlook']['market_assessment']['summary'], '',
                  title + decision['summary'], '']
        lines += [action['scope'] + '：' + action['arrangement'] for action in decision['actions']]
        if assessment.get('revision', 1) >= 7:
            lines += [action['scope'] + '：' + action['arrangement'] for action in assessment['recommendations']['portfolio']['strategy_advice']]
    lines += ['- ' + v for v in assessment['conclusion']['business_meaning'].values()]
    for side, title, unit in (('upper_short', '溢价区空单', 'U名义额'), ('lower_spot', '超卖区现货篮子', 'U投入')):
        v = assessment['recommendations'][side]
        if side == 'lower_spot' and any(set(t.get('allocation_percent', {})) == {'BTC'} for t in v['tiers']):
            title = '现货篮子与独立BTC建议'
        lines += ['', '## ' + title, '', '| BTC参考位置（USDT/BTC） | 建议金额 | 计划占用 |', '|---|---:|---:|']
        if v['status'] == 'NOT_RECOMMENDED':
            lines += ['| 当前不建议 | — | — |']
        elif v['status'] in ('EXECUTION_LIMITED', 'UNDETERMINED'):
            lines += ['| ' + ('方向已决定，价量执行受限' if v['status'] == 'EXECUTION_LIMITED' else '动作未决') + ' | 未知 | 未知 |']
        for tier in v['tiers']:
            price = tier['price_low_usd'] + ('—' + tier['price_high_usd'] if tier['price_low_usd'] != tier['price_high_usd'] else '')
            if side == 'lower_spot' and any('allocation_percent' in t for t in v['tiers']):
                from .recommendations import spot_label
                price = spot_label(tier) + ' ' + price
            lines += ['| ' + price + ' | ' + tier['amount_u'] + unit + ' | ' + (tier['capital_u'] + 'U' if tier['capital_u'] is not None else '未知') + ' |']
        lines += ['', v['rationale'], '', v['impact'], '', '适用至：' + v['validity']['until'],
                  '形成条件：' + '、'.join(v['validity']['conditions']), '失效条件：' + '、'.join(v['validity']['invalidations'])]
    lines += ['', '## 未来价值路径', '', assessment['outlook']['narrative'], '', '## 组合与反证', '', assessment['robustness']['narrative']]
    lines += ['', assessment['recommendations']['portfolio']['narrative'], '', assessment['recommendations']['portfolio']['existing_exposure'], '', assessment['recommendations']['portfolio']['uncertainty']]
    for name, v in assessment['portfolio_analysis'].items():
        lines += ['', name + '：' + v['narrative']]
    lines += ['', '两侧先后采用的资金路径（计划增量，不是成交）：', '', '| 顺序 | 累计已知占用 | 累计总占用 |', '|---|---:|---:|']
    for order, steps in sequences(assessment['recommendations']).items():
        for step in steps:
            lines += ['| ' + order + ' | ' + step['known_capital_u'] + 'U | ' + (step['cumulative_capital_u'] + 'U' if step['cumulative_capital_u'] is not None else '含未知空单占用') + ' |']
    return '\n'.join(lines) + '\n'


def assemble(root, project, components, evidence_dir, market, identity, checks):
    """Mechanically carry unchanged sections and seal main-authored changes once."""
    from .storage import current
    from . import evidence, sensing
    from .common import facts
    pinned = current(root)
    require(pinned and pinned.get('assessment'), 'assembly requires an existing complete assessment')
    previous = pinned['assessment']
    exact(components, 'updates section_reviews evidence_bindings domain_market_cutoff stop_reason', 'main authored components')
    require(isinstance(components['updates'], dict), 'main updates must be a mapping')
    from .closed_loop import allocation, risk
    proposed = {'recommendations': components['updates'].get('recommendations', previous.get('recommendations'))}
    revision = (7 if risk(proposed) is not None else 6 if allocation(proposed) is not None
                else 5 if 'market_assessment' in components['updates'].get('outlook', previous.get('outlook', {}))
                else 4 if previous.get('revision', 1) >= 4 or 'factor_assessment' in components['updates'] else 3)
    sections = sections_for_revision(revision)
    require(set(components['updates']) <= set(sections) | {'research_absorptions'}, 'unknown main update section')
    exact(components['section_reviews'], ' '.join(sections), 'main section reviews')
    package = evidence.validate(read(Path(evidence_dir) / 'evidence.json'), evidence_dir)
    for source in package['sources']:
        path = Path(evidence_dir) / 'raw' / (source['raw_hash'][7:] + '.json')
        payload = read(path)
        if isinstance(payload, dict) and payload.get('evidence_id') == market['evidence_id'] and payload == market:
            ref = {'source_id': source['id'], 'pointer':'/datasets', 'value_hash':digest(payload['datasets'])}
            break
    else:
        require(False, 'assembly needs the exact complete current market package in evidence')
    value = {'schema':'BTC_ASSESSMENT_DRAFT_V4', 'revision':revision, 'evidence_id':package['evidence_id'],
             'evidence_bindings':components['evidence_bindings'], 'fact_version':facts(project),
             'temporal_scope':{'market_cutoff':market['cutoff'], 'knowledge_cutoff':package['cutoff'],
                               'domain_market_cutoff':components['domain_market_cutoff'], 'current_market_refs':[ref]},
             'provenance':identity, 'stop_reason':components['stop_reason'], 'section_provenance':{}}
    for key in sections:
        if key in components['updates']:
            value[key] = deepcopy(components['updates'][key])
            value['section_provenance'][key] = {'mode':'ASSESSED','decision_id':None,'assessed_at':package['cutoff'],
                                               'evidence_id':package['evidence_id'],'explanation':components['section_reviews'][key]}
        else:
            require(key in previous, 'new section requires main-authored content: ' + key)
            value[key] = deepcopy(previous[key])
            origin = previous.get('section_provenance', {}).get(key, {})
            value['section_provenance'][key] = {'mode':'REUSED','decision_id':pinned['decision_id'],
                'assessed_at':origin.get('assessed_at',previous['temporal_scope']['knowledge_cutoff']),
                'evidence_id':origin.get('evidence_id',previous['evidence_id']),'explanation':components['section_reviews'][key]}
    value['research_absorptions'] = deepcopy(components['updates'].get('research_absorptions',previous.get('research_absorptions',[])))
    kwargs = {'evidence_dir': evidence_dir} if revision >= 4 else {}
    ctx = sensing.context(root, Path(project)/'.strategy/market_observation.sqlite3', project, market, frozen_review=True, **kwargs)
    value['maintenance_binding'] = {'context_id':ctx['context_id'],'market_evidence_id':market['evidence_id']}
    value['draft_id'] = digest(value)
    exact(checks, 'checks unresolved_issues material_new_outlook', 'main review content')
    review = {'schema':'BTC_MAIN_REVIEW_V4', 'draft_id':value['draft_id'], 'evidence_id':package['evidence_id'],
              'reviewer_role':'MAIN', **checks}
    return value, review


def report_source(root, decision_id, layout, constraints, author_review):
    from .storage import current
    from . import report
    pinned = current(root)
    require(pinned and pinned['decision_id'] == decision_id, 'report source must use the current complete conclusion')
    require(pinned['assessment'].get('revision', 1) < 6,
            'revision 6 report sources require report-review with final result bindings and applicability')
    require(pinned['assessment'].get('revision') in (3, 4, 5, 6), 'new report source requires main revision 3 or later')
    value = deepcopy(pinned['assessment'])
    value['report'] = layout
    comparison = report_comparison(root, value, pinned, pinned['review']['material_new_outlook'])
    report.validate_changes(value, comparison)
    body = report.render(value)
    return {'schema':'BTC_REPORT_SOURCE_V3','decision_id':decision_id,'body':body,'body_hash':digest(body.encode()),
            'semantic_hash':digest(semantics.stable(value)), 'layout':layout, 'constraints':constraints, 'author_review':author_review}
