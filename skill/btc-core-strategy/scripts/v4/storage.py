"""Immutable generations and a single durable current pointer with compare-and-swap."""
from pathlib import Path
import os
import shutil
import uuid

from . import assessment, domain, report, semantics
from .common import create, digest, facts, fsync_dir, locked, now, read, replace, require, sync_tree


NEW_WRITE_REVISION = 9

def current(root):
    root = Path(root)
    link = root / 'current'
    if not link.is_symlink():
        require(not link.exists(), 'current pointer must be a symlink')
        return None
    target = link.resolve()
    require(target.parent == (root / 'versions').resolve(), 'current pointer escapes versions')
    value = read(target / 'decision.json')
    require(value['decision_id'] == target.name, 'current generation identity mismatch')
    require(value['decision_id'] == digest({k: v for k, v in value.items() if k != 'decision_id'})[7:], 'decision content changed')
    for filename, expected in value['view_hashes'].items():
        require(digest((target / filename).read_bytes()) == expected, 'generation view changed')
    return value


def domain_baseline(root, pinned):
    """v3 expects a genuinely written D/E baseline. Outlook-only v4 changes
    preserve that historical invariant anchor instead of inventing a v3 D event.
    Current outlook and decisions always come from the pinned v4 assessment.
    """
    value = pinned
    seen = set()
    while not value['canonical_domain']['control']['project_patch_required']:
        identity = value['parent_id']
        require(identity and identity not in seen, 'invalid domain baseline ancestry')
        seen.add(identity)
        value = read(Path(root) / 'versions' / identity / 'decision.json')
        require(value['decision_id'] == digest({k: v for k, v in value.items() if k != 'decision_id'})[7:], 'domain ancestor damaged')
    return value


def _publish(root, value, views, expected, fault=None):
    root = Path(root)
    actual = current(root)
    require((actual or {}).get('decision_id') == expected, 'concurrent assessment changed current version')
    value['view_hashes'] = {name: digest(body) for name, body in views.items()}
    value['decision_id'] = digest(value)[7:]
    generation = root / 'versions' / value['decision_id']
    if not generation.exists():
        stage = root / 'versions' / ('.staging-' + uuid.uuid4().hex)
        for name, body in views.items():
            create(stage / name, body)
        create(stage / 'decision.json', value)
        if fault:
            fault('before_generation')
        os.rename(stage, generation)
        fsync_dir(generation.parent)
    else:
        require(read(generation / 'decision.json') == value, 'generation identity collision')
    if fault:
        fault('before_pointer')
    if (value.get('assessment') or {}).get('revision', 1) >= 9:
        from .forward_lifecycle import register
        register(root, value['assessment']['outlook']['market_assessment'], generation / 'decision.json', '/created_at')
    tmp = root / ('.current-' + uuid.uuid4().hex)
    tmp.symlink_to(Path('versions') / value['decision_id'], target_is_directory=True)
    os.replace(tmp, root / 'current')
    fsync_dir(root)
    return value


def enforce_write_policy(root, draft, prior):
    policy_path = Path(root) / 'write-policy.json'
    policy = read(policy_path) if policy_path.exists() else {}
    previous = prior.get('assessment') or {}
    require(draft.get('revision', 1) >= max(NEW_WRITE_REVISION, policy.get('minimum_assessment_revision', 1), previous.get('revision', 1)), 'legacy draft is replay-only after recommendation activation')
    report_format = policy.get('required_report_format') or previous.get('report', {}).get('format')
    if report_format:
        if draft.get('revision', 1) < 3:
            require(draft['report'].get('format') == report_format, 'new assessment requires concise report format')


def commit(root, project, draft, evidence_dir, review, expected, fault=None):
    root = Path(root)
    with locked(root):
        prior = current(root)
        require(prior is not None, 'verified migration baseline required')
        require(prior['decision_id'] == expected, 'stale deep assessment')
        enforce_write_policy(root, draft, prior)
        if draft.get('revision', 1) >= 3:
            return _commit_main(root, project, draft, evidence_dir, review, prior, expected, fault)
        if (root / 'deep-current.json').exists():
            from .common import instant
            latest_deep = read(root / read(root / 'deep-current.json')['completion'])
            require(latest_deep['completion_hash'] == digest({k: v for k, v in latest_deep.items() if k != 'completion_hash'}), 'latest deep baseline damaged')
            for field in ('market_cutoff', 'knowledge_cutoff'):
                require(instant(draft['temporal_scope'][field]) >= instant(latest_deep['assessment']['temporal_scope'][field]), 'older deep assessment cannot replace newer baseline')
        accepted = assessment.validate(draft, evidence_dir, domain_baseline(root, prior), project, review)
        if draft.get('revision') == 2:
            from .sensing import assert_current
            assert_current(root, project, draft, evidence_dir)
        comparison = semantics.compare(accepted, prior.get('assessment'), prior['canonical_domain'], review['material_new_outlook'])
        report.validate_changes(accepted, comparison)
        final_event = accepted['domain']['event']['final_event_reached']
        baseline = {'draft_id': draft['draft_id'], 'evidence_id': accepted['evidence_id'], 'completed_at': now(),
                    'fact_version': accepted['fact_version'], 'submitted_draft': draft, 'assessment': accepted, 'review': review}
        baseline['completion_hash'] = digest(baseline)
        dest = root / 'evidence' / accepted['evidence_id'][7:]
        if not dest.exists():
            stage = root / 'evidence' / ('.staging-' + uuid.uuid4().hex)
            shutil.copytree(evidence_dir, stage)
            from .evidence import validate as validate_evidence
            validate_evidence(read(stage / 'evidence.json'), stage)
            sync_tree(stage)
            os.rename(stage, dest)
            fsync_dir(dest.parent)
        from .evidence import validate as validate_evidence
        validate_evidence(read(dest / 'evidence.json'), dest)
        # Never discard a successful unchanged assessment or replace its first completion time.
        completion_path = root / 'deep' / (draft['draft_id'][7:] + '.json')
        if completion_path.exists():
            baseline = read(completion_path)
        else:
            create(completion_path, baseline)
        require(facts(project) == accepted['fact_version'], 'facts changed before commit')
        if draft.get('revision') == 2:
            assert_current(root, project, draft, evidence_dir)
            from .common import instant
            require(all(instant(accepted['recommendations'][s]['validity']['until']) > instant(now()) for s in ('upper_short', 'lower_spot')), 'recommendation expired during deep review')
        if not comparison['report_required'] and not final_event and prior.get('assessment') is not None:
            replace(root / 'deep-current.json', {'completion': str(completion_path.relative_to(root))})
            from .hourly import complete_deep
            complete_deep(root, completion_path, Path(project) / '.strategy/market_observation.sqlite3')
            return {'status': 'UNCHANGED', 'decision_id': expected, 'report_required': False}
        body = report.render(accepted)
        state = ('# 当前策略评估\n\n市场数据截止：' + accepted['temporal_scope']['market_cutoff'] + '\n\n知识截止：' + accepted['temporal_scope']['knowledge_cutoff'] + '\n\n' + body + '\n').encode()
        value = {'schema': 'BTC_DECISION_SNAPSHOT_V4', 'parent_id': expected, 'created_at': baseline['completed_at'],
                 'canonical_domain': domain.canonical(accepted['domain']), 'assessment': accepted,
                 'review': review, 'evidence_id': accepted['evidence_id'], 'fact_version': accepted['fact_version'],
                 'report_required': comparison['report_required'], 'comparison': comparison}
        views = {'state.md': state, 'plan.md': body.encode(), 'report.md': body.encode()}
        result = _publish(root, value, views, expected, fault)
        if fault:
            fault('after_pointer')
        replace(root / 'deep-current.json', {'completion': str(completion_path.relative_to(root))})
        from .hourly import complete_deep
        complete_deep(root, completion_path, Path(project) / '.strategy/market_observation.sqlite3')
        return result


def recover(root):
    """Read-only inventory. Orphan generations never become current by guessing."""
    root = Path(root)
    pinned = current(root)
    queued = {read(p / 'artifact.json')['decision_id'] for p in (root / 'outbox').glob('*') if (p / 'artifact.json').exists()}
    required = []
    node = pinned
    while node:
        if node.get('report_required') and node['decision_id'] not in queued:
            required.append(node['decision_id'])
        node = read(root / 'versions' / node['parent_id'] / 'decision.json') if node.get('parent_id') else None
    # Unsent historical candidates are reconsidered from the current complete strategy.
    if (pinned or {}).get('assessment', {}).get('revision', 1) >= 3:
        from .main_flow import report_comparison
        comparison = report_comparison(root, pinned['assessment'], pinned, pinned['review']['material_new_outlook'])
        required = [pinned['decision_id']] if comparison['report_required'] and pinned['decision_id'] not in queued else []
    from . import reporting, report_care, delivery
    attempts = delivery.attempt_recovery(root)
    return {'report_care':report_care.status(root), 'current': (pinned or {}).get('decision_id'), 'reports_not_enqueued': required,
            'report_reviews_not_enqueued': reporting.pending(root),
            'report_delivery_obligations': reporting.obligations(root),
            'attempt_recovery': attempts,
            'delivery_recovery': delivery.recovery_actions(root, attempts),
            'report_review_required': reporting.review_required(root),
            'staging': sorted(p.name for p in (root / 'versions').glob('.staging-*')),
            'outbox': [{'delivery_id': p.parent.name, **read(p)} for p in sorted((root / 'outbox').glob('*/status.json'))]}


def install_views(root, project, expected_hashes):
    root, project = Path(root), Path(project)
    with locked(root):
        require(current(root) is not None, 'no valid current assessment')
        names = {'策略状态.md': 'state.md', '当前预期计划.md': 'plan.md'}
        for name in names:
            require(digest((project / name).read_bytes()) == expected_hashes[name], 'compatibility file changed: ' + name)
        # Scheduler must be paused for this one-time installation. Readers thereafter pin current.
        for name, target in names.items():
            tmp = project / ('.v4-view-' + uuid.uuid4().hex)
            tmp.symlink_to(root / 'current' / target)
            os.replace(tmp, project / name)
        fsync_dir(project)


def validate_main(root, project, draft, evidence_dir, review, prior=None):
    """The same read-only checks serve preflight and the locked publication.

    Independent failures are collected in one response; dependent checks run
    only after their prerequisite produced a valid value.
    """
    from . import main_flow, sensing
    from .common import instant, Invalid
    import json
    prior = prior or current(root)
    errors = []

    def check(name, fn):
        try:
            return fn()
        except (Invalid, KeyError, TypeError) as exc:
            errors.append({'check': name, 'error': str(exc)})

    check('write_policy', lambda: enforce_write_policy(root, draft, prior))
    previous = prior.get('assessment')
    if previous:
        for field in ('market_cutoff', 'knowledge_cutoff'):
            check(field, lambda field=field: require(instant(draft['temporal_scope'][field]) >= instant(previous['temporal_scope'][field]), 'older main assessment cannot replace current conclusion'))
    check('facts', lambda: require(facts(project) == draft['fact_version'], 'facts changed before main publication'))
    for side in ('upper_short', 'lower_spot'):
        if draft['recommendations'][side]['status'] == 'RECOMMENDED':
            check(side + '_expiry', lambda side=side: require(instant(draft['recommendations'][side]['validity']['until']) > instant(now()), 'recommendation expired during main assessment'))
    if draft.get('revision',0)>=8:
        from .investment import decision as investment_decision
        check('investment_expiry',lambda: require(instant(investment_decision(draft)['valid_until'])>instant(now()), 'investment decision expired during main assessment; complete a current review before adoption'))
    baseline = check('domain_baseline', lambda: main_flow.domain_validation_baseline(root, prior, draft))
    accepted = check('assessment', lambda: assessment.validate(draft, evidence_dir, baseline, project, review)) if baseline else None
    if accepted:
        check('normalization', lambda: domain.validate_normalization(draft, accepted))
        check('publication', lambda: main_flow.validate_publication(root, prior, accepted))
    check('current_bindings', lambda: sensing.assert_current(root, project, draft, evidence_dir))
    require(not errors, 'main preflight failed: ' + json.dumps(errors, ensure_ascii=False))
    return accepted


def _commit_main(root, project, draft, evidence_dir, review, prior, expected, fault=None):
    """Called under the shared writer lock. Main success never advances DEEP."""
    from . import main_flow
    from .evidence import validate as validate_evidence
    previous = prior.get('assessment')
    accepted = validate_main(root, project, draft, evidence_dir, review, prior)
    comparison = main_flow.report_comparison(root, accepted, prior, review['material_new_outlook'])
    fresh_final = accepted['section_provenance']['domain']['mode'] == 'ASSESSED' and accepted['domain']['event']['final_event_reached']
    changed = main_flow.content(accepted) != main_flow.content(previous) or fresh_final
    # Finish all checks before saving any completion receipt.
    dest = root / 'evidence' / accepted['evidence_id'][7:]
    if not dest.exists():
        stage = root / 'evidence' / ('.staging-' + uuid.uuid4().hex)
        shutil.copytree(evidence_dir, stage)
        validate_evidence(read(stage / 'evidence.json'), stage)
        sync_tree(stage)
        os.rename(stage, dest)
        fsync_dir(dest.parent)
    validate_evidence(read(dest / 'evidence.json'), dest)
    completed_at = now()
    # Evidence archival may outlast preflight validity. Check the actual
    # completion time again before any decision or completion pointer advances.
    from .common import instant
    for side in ('upper_short', 'lower_spot'):
        recommendation = accepted['recommendations'][side]
        if recommendation['status'] == 'RECOMMENDED':
            require(instant(recommendation['validity']['until']) > instant(completed_at),
                    'recommendation expired before main completion; reassess before adoption')
    if accepted.get('revision', 0) >= 8:
        from .investment import decision as investment_decision
        from .closed_loop import risk
        require(instant(investment_decision(accepted)['valid_until']) > instant(completed_at),
                'investment decision expired before main completion; reassess before adoption')
        require(instant(risk(accepted)['valid_until']) > instant(completed_at),
                'risk assessment expired before main completion; reassess before adoption')
    if changed:
        body = main_flow.projection(accepted).encode()
        value = {'schema': 'BTC_DECISION_SNAPSHOT_V4', 'parent_id': expected, 'created_at': completed_at,
                 'canonical_domain': domain.canonical(accepted['domain']), 'assessment': accepted,
                 'review': review, 'evidence_id': accepted['evidence_id'], 'fact_version': accepted['fact_version'],
                 'report_required': comparison['report_required'], 'comparison': comparison}
        result = _publish(root, value, {'state.md': body, 'plan.md': body}, expected, fault)
        if accepted.get('revision', 1) >= 9:
            from .forward_lifecycle import register
        if fault:
            fault('after_pointer')
    else:
        result = {'status': 'UNCHANGED', 'decision_id': expected, 'report_required': comparison['report_required'], 'comparison': comparison}
    receipt = {'schema': 'BTC_MAIN_COMPLETION_V3', 'draft_id': draft['draft_id'], 'evidence_id': accepted['evidence_id'],
               'completed_at': completed_at, 'decision_id': result['decision_id'],
               'status': 'PUBLISHED' if changed else 'CONFIRMED_UNCHANGED', 'assessment': accepted, 'review': review}
    if accepted != draft:
        receipt['submitted_draft'] = draft
    receipt['completion_hash'] = digest(receipt)
    identity = receipt['completion_hash'] if 'submitted_draft' in receipt else draft['draft_id']
    path = root / 'main' / (identity[7:] + '.json')
    main_flow.validate_completion(receipt, path)
    if not path.exists():
        create(path, receipt)
    replace(root / 'main-current.json', {'completion': str(path.relative_to(root))})
    return result


def repair_main_completion(root, project, submitted):
    """Under the writer lock, recover a normalized published receipt in place.

    The published generation and original receipt remain immutable. This only
    repairs the active completion pointer after checking the exact publication.
    """
    from . import sensing
    root = Path(root)
    pinned = current(root)
    target = read(root / 'main-current.json')
    require(set(target) == {'completion'}, 'invalid main completion pointer')
    path = root / target['completion']
    require(path.resolve().parent == (root / 'main').resolve(), 'main completion escapes receipt directory')
    old = read(path)
    require(old['completion_hash'] == digest({k: v for k, v in old.items() if k != 'completion_hash'}), 'main completion damaged')
    require(old['status'] == 'PUBLISHED' and old['decision_id'] == pinned['decision_id'], 'repair requires the actual current main publication')
    require(old['assessment'] == pinned['assessment'] and old['review'] == pinned['review'], 'repair differs from the actual published decision')
    if 'submitted_draft' in old:
        require(old['submitted_draft'] == submitted, 'repair submission differs from completed main')
        return sensing.main_event_review(root, pinned, facts(project))
    require(old['draft_id'] == 'sha256:' + path.stem, 'repair source path binding mismatch')
    repaired = dict(old, submitted_draft=submitted, completed_at=now())
    repaired['completion_hash'] = digest({k: v for k, v in repaired.items() if k != 'completion_hash'})
    destination = root / 'main' / (repaired['completion_hash'][7:] + '.json')
    result = sensing._main_event_review_value(root, pinned, facts(project), destination, repaired)
    create(destination, repaired)
    replace(root / 'main-current.json', {'completion': str(destination.relative_to(root))})
    return dict(result, decision_id=pinned['decision_id'], status='MAIN_COMPLETION_REPAIRED')


def archive_evidence(root, directory):
    """Archive verified raw evidence for a request without publishing a strategy."""
    from .evidence import validate
    directory, root = Path(directory), Path(root)
    package = validate(read(directory / 'evidence.json'), directory)
    with locked(root):
        dest = root / 'evidence' / package['evidence_id'][7:]
        if not dest.exists():
            stage = root / 'evidence' / ('.staging-' + uuid.uuid4().hex)
            shutil.copytree(directory, stage)
            validate(read(stage / 'evidence.json'), stage)
            sync_tree(stage)
            os.rename(stage, dest)
            fsync_dir(dest.parent)
        validate(read(dest / 'evidence.json'), dest)
    return {'evidence_id':package['evidence_id'],'directory':str(dest),'status':'EVIDENCE_ARCHIVED_ONLY'}
