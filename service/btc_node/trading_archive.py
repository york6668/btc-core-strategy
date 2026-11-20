"""One-time read-only continuity from the original trading archive."""
from __future__ import annotations

import json
from pathlib import Path
import re

from . import deep_gate
from .codec import Conflict, millis, stamp

ARCHIVE = 'trading.legacy-continuity'
DIGEST = re.compile(r'sha256:[a-f0-9]{64}')
COMMIT = re.compile(r'[a-f0-9]{40}')
READ_ERRORS = (OSError, ValueError, TypeError, KeyError, ImportError)
CONDITIONS = {'CYCLE', 'FACTOR', 'REGION', 'HYPOTHESIS', 'PLAN', 'ALLOCATION'}


def _archive_root(db):
    return Path(db.settings['project']) / '.strategy/v4'


def _archive_method(db, release):
    root = Path(db.settings['release_root']).resolve()
    try:
        binding = json.loads((root.parent / 'activation.json').read_bytes())
        previous = binding.get('previous')
        if binding.get('commit') == release.name and isinstance(previous, str) and COMMIT.fullmatch(previous):
            candidate = root / previous
            data = json.loads((candidate / 'RELEASE.json').read_bytes())
            if candidate.resolve().parent == root and data['commit'] == previous:
                return candidate
    except READ_ERRORS:
        pass
    return release


def _owner_at(root, as_of):
    from v4 import storage
    from v4.common import digest

    owner = storage.current(root)
    seen = set()
    while owner and millis(owner['created_at']) > millis(as_of):
        parent = owner['parent_id']
        if parent is None:
            return None
        if not re.fullmatch(r'[a-f0-9]{64}', parent) or parent in seen:
            raise ValueError('original decision parent identity is invalid')
        seen.add(parent)
        path = root / 'versions' / parent / 'decision.json'
        if path.resolve().parent.parent != (root / 'versions').resolve():
            raise ValueError('original decision parent escapes the archive')
        previous = json.loads(path.read_bytes())
        if (previous['decision_id'] != parent
                or digest({k: v for k, v in previous.items() if k != 'decision_id'}) != 'sha256:' + parent
                or millis(previous['created_at']) > millis(owner['created_at'])):
            raise ValueError('original decision parent no longer matches its source')
        owner = previous
    return owner


def _public_history(root, as_of, owner):
    try:
        from v4 import forward_lifecycle, market_state
        from v4.common import digest

        if not owner:
            return {'status': 'NO_ORIGINAL_DECISION', 'conditions': []}
        assessment = owner['assessment']
        public = market_state.public_content(assessment)
        if not public:
            return {'status': 'NO_INDEPENDENT_PUBLIC_RECORD', 'conditions': []}
        market = public['outlook']['market_assessment']
        cutoff = assessment['temporal_scope']['knowledge_cutoff']
        if not millis(cutoff) <= millis(owner['created_at']) <= millis(as_of):
            raise ValueError('original decision has invalid knowledge and formation clocks')
        basis = {'prior_decision': owner['decision_id'], 'formed_at': owner['created_at'],
                 'knowledge_cutoff': cutoff, 'market_assessment': market,
                 'public_case': market.get('cycle_case'), 'factor_assessment': public['factor_assessment']}
    except READ_ERRORS:
        return {'status': 'PUBLIC_BASELINE_UNKNOWN', 'conditions': []}

    conditions = []
    markets = market.get('markets') or []
    if not isinstance(markets, list):
        return {'status': 'ORIGINAL_MARKET_HORIZONS_UNKNOWN', 'basis': basis, 'conditions': []}
    for product in markets:
        if not isinstance(product, dict) or not isinstance(product.get('horizons'), list):
            conditions.append({'owner': owner['decision_id'], 'status': 'ORIGINAL_MARKET_HORIZONS_UNKNOWN'})
            continue
        for horizon in product['horizons']:
            condition = {'owner': owner['decision_id'], 'market': product.get('id')}
            try:
                condition['horizon'] = horizon['id']
                forward = horizon.get('forward') or {}
                lifecycle = forward.get('lifecycle')
                if not lifecycle:
                    conditions.append({**condition, 'status': 'POSTHOC_ONLY', 'original_forward': forward})
                    continue
                claim_id = digest(lifecycle['claim'])
                registration = forward_lifecycle.registration(root, claim_id)
                if registration is None:
                    conditions.append({**condition, 'claim_id': claim_id, 'status': 'ORIGINAL_REGISTRATION_UNKNOWN'})
                    continue
                if millis(registration['registered_at']) > millis(as_of):
                    conditions.append({**condition, 'claim_id': claim_id, 'status': 'REGISTRATION_NOT_YET_KNOWN'})
                    continue
                conditions.append({**condition, 'status': 'ORIGINALLY_REGISTERED', 'claim_id': claim_id,
                                   'registration': registration, 'original_forward': forward})
            except READ_ERRORS:
                conditions.append({**condition, 'status': 'ORIGINAL_CONDITION_UNKNOWN'})
    return {'status': 'ORIGINAL_PUBLIC_EVIDENCE', 'basis': basis, 'conditions': conditions}


def _public_review_targets(db, public, *, root, source, known_at, as_of):
    targets, gaps = [], set()
    if 'basis' not in public:
        return targets, gaps
    from . import trading_decisions as decisions
    from v4 import forward_lifecycle
    from v4.common import digest

    basis = public['basis']
    selected = {}
    for item in public['conditions']:
        if item['status'] == 'ORIGINALLY_REGISTERED':
            selected[item['claim_id']] = item
        elif item['status'] not in ('POSTHOC_ONLY', 'REGISTRATION_NOT_YET_KNOWN'):
            gaps.add(item['status'])
    for item in tuple(selected.values()):
        lifecycle = item['original_forward']['lifecycle']
        for dependency in lifecycle.get('dependencies', []):
            try:
                claim_id = dependency['claim_hash']
                expectation_id = dependency['expectation_id']
                if claim_id in selected:
                    expectations = selected[claim_id].get('dependent_expectations')
                    if expectations is not None:
                        expectations.add(expectation_id)
                    continue
                registration = forward_lifecycle.registration(root, claim_id)
                if not registration or millis(registration['registered_at']) > millis(as_of):
                    raise ValueError('original dependent condition cannot be verified')
                selected[claim_id] = {**item, 'claim_id': claim_id, 'registration': registration,
                                      'dependent_expectations': {expectation_id}}
            except READ_ERRORS:
                gaps.add('ORIGINAL_DEPENDENCY_UNKNOWN')
    failures = {}
    try:
        for result in forward_lifecycle.persisted_failures(root, set(selected), as_of):
            review = result['review']
            for outcome in review['outcomes']:
                if outcome['verdict'] == 'FALSIFIED':
                    key = review['prior_claim_hash'], outcome['expectation_id']
                    failures[key] = {'outcome': 'REFUTED', 'recorded_at': result['recorded_at'],
                                     'source_digest': result['recorded_in'], 'review_digest': digest(review)}
    except READ_ERRORS:
        gaps.add('ORIGINAL_REVIEW_HISTORY_UNKNOWN')
    for item in selected.values():
        try:
            registration = item['registration']
            claim = registration['claim']
            expected = item.get('dependent_expectations')
            conditions = []
            for part in claim['expectations']:
                if expected is not None and part['id'] not in expected:
                    continue
                condition = {'id': part['id'], 'criterion': part['mechanism'], 'scope': part['scope'],
                             'support': part['support'], 'falsification': part['falsification'],
                             'review_at': claim['scale']['end'], 'observation_end': claim['scale']['end'],
                             'observation_relations': [relation for relation in claim.get('observation_relations', [])
                                                       if part['id'] in relation['expectation_ids']]}
                if (prior := failures.get((item['claim_id'], part['id']))) is not None:
                    condition['original_review'] = prior
                conditions.append(condition)
            provenance = {'decision_id': basis['prior_decision'], 'claim_id': item['claim_id'],
                          'source_digest': registration['owner']['hash'], 'source_release': source,
                          'knowledge_cutoff': basis['knowledge_cutoff'], 'data_cutoff': claim['data_cutoff'],
                          'formed_at': claim['formed_at'], 'registered_at': registration['registered_at'],
                          'process': claim['scale']['process']}
            if conditions:
                targets.append(decisions.save_review_target(
                    db, subject=item['market'] + '/' + item['horizon'], source_key=item['claim_id'],
                    provenance=provenance, conditions=conditions, valid_until=claim['scale']['end'], known_at=known_at))
        except READ_ERRORS:
            gaps.add('ORIGINAL_CONDITION_UNKNOWN')
    return targets, gaps


def _entity_references(db, as_of):
    rows = db.rows("""SELECT e.kind,e.object_id,e.scope,e.review_at_ms,e.valid_until_ms,
                          v.version,v.digest,v.known_ms,v.recorded_ms
                     FROM entities e JOIN entity_versions v
                       ON v.entity_key=e.entity_key AND v.version=e.current_version
                    WHERE (e.active=1 OR EXISTS (SELECT 1 FROM review_requirements r
                              WHERE r.entity_key=e.entity_key AND r.resolved_ms IS NULL))
                      AND e.object_id NOT LIKE %s AND v.known_ms<=%s
                 ORDER BY e.scope,e.kind,e.object_id""", ('trading/%', millis(as_of)))
    return [row for row in rows if row['kind'] in CONDITIONS or row['kind'] == 'RESEARCH']


def _research_index(db, references):
    issues = []
    for record in references:
        if record['kind'] != 'RESEARCH':
            continue
        try:
            payload = db.value(record['digest'])['payload']
            request = payload['latest_request']
            if payload['issue_id'] != record['object_id'] or not DIGEST.fullmatch(request['request_id']):
                raise ValueError('unexpected original research identity')
            progress = request['progress']
            issues.append({'issue_id': payload['issue_id'], 'request_id': request['request_id'],
                           'independent_id': progress.get('independent_id'),
                           'stage': progress['stage'], 'requested_at': request['created_at'],
                           'public_completed_at': progress.get('independent_completed_at'),
                           'digest': record['digest']})
        except READ_ERRORS:
            issues.append({'issue_id': record['object_id'], 'stage': 'ORIGINAL_RESEARCH_UNKNOWN',
                           'digest': record['digest']})
    return issues


def capture(db, *, release, as_of):
    """Recover the cutover evidence once under the original source and cutoff."""
    original_pointer = db.one('SELECT digest FROM node_metadata WHERE name=%s', (ARCHIVE,))
    saved = db.value(original_pointer['digest']) if original_pointer else None
    manifest = saved
    if manifest and manifest.get('continuity', {}).get('status') == 'CAPTURED':
        return
    from .execution import load_methods

    if manifest is None:
        original = _archive_method(db, Path(release))
        references = _entity_references(db, as_of)
        requirements = db.rows("""SELECT r.requirement_key,r.cause_key,r.cause_version,r.opened_ms,r.reason,
                                     e.scope,e.kind,e.object_id
                                FROM review_requirements r JOIN entities e USING(entity_key)
                               WHERE r.resolved_ms IS NULL AND r.opened_ms<=%s AND e.object_id NOT LIKE %s
                            ORDER BY r.opened_ms,r.requirement_key""", (millis(as_of), 'trading/%'))
        manifest = {'captured_at': stamp(as_of), 'source_release': original.name,
                    'research': _research_index(db, references), 'references': references,
                    'review_requirements': requirements}
    else:
        source = manifest['source_release']
        if not COMMIT.fullmatch(source):
            raise Conflict('the original release identity of the cutover archive is invalid')
        original = Path(db.settings['release_root']).resolve() / source
        if 'original_capture' not in manifest:
            # The metadata writer pins this exact existing CAS object for
            # backup/maintenance, even after the active archive is enriched.
            manifest = {**manifest, 'original_capture': original_pointer['digest']}

    original_cutoff = manifest['captured_at']
    root = _archive_root(db)
    load_methods(original)
    try:
        try:
            if (original.resolve().parent != Path(db.settings['release_root']).resolve()
                    or json.loads((original / 'RELEASE.json').read_bytes())['commit'] != original.name):
                raise ValueError('original release does not match its archived identity')
            owner = _owner_at(root, original_cutoff)
            prior = (manifest.get('public') or {}).get('basis')
            if prior and (owner is None or owner['decision_id'] != prior['prior_decision']):
                raise ValueError('the original decision no longer matches the cutover archive')
            if owner is None and (manifest.get('public') or {}).get('status') == 'PUBLIC_BASELINE_UNKNOWN':
                raise ValueError('the source of an incomplete archive is still missing')
        except READ_ERRORS:
            owner = None
            public = {'status': 'PUBLIC_BASELINE_UNKNOWN', 'conditions': []}
        else:
            public = _public_history(root, original_cutoff, owner)
        targets, gaps = _public_review_targets(
            db, public, root=root, source=original.name, known_at=as_of, as_of=original_cutoff)
    finally:
        load_methods(Path(release))
    if public['status'] == 'PUBLIC_BASELINE_UNKNOWN':
        gaps.add('PUBLIC_BASELINE_UNKNOWN')
        if 'basis' in manifest.get('public', {}):
            public = manifest['public']
    existing = manifest.get('continuity', {}).get('targets', [])
    by_id = {target['object_id']: target for target in (*existing, *targets)}
    continuity = {'status': 'CAPTURED' if not gaps else 'INCOMPLETE',
                  'targets': list(by_id.values()), 'gaps': sorted(gaps)}
    updated = {**manifest, 'public': public, 'continuity': continuity}
    if updated != saved:
        db.metadata(ARCHIVE, updated, write=True)


def _native_states(db, issues, scope):
    try:
        claims = deep_gate.status(db)['claims']
    except READ_ERRORS:
        return {'status': 'NATIVE_STATUS_UNKNOWN', 'attempts': []}
    ids = {issue['issue_id'] for issue in issues}
    if scope == 'PUBLIC':
        claims = {key: v for key, v in claims.items() if v.get('phase') == 'PUBLIC'}
    return {'status': 'ORIGINAL_NATIVE_RECORDS',
            'attempts': [{key: value[key] for key in ('claim_id', 'issue_id', 'scope', 'phase', 'state',
                         'input_digest', 'claimed_at_ms', 'finished_at_ms', 'result_ref', 'failure_kind') if key in value}
                         for value in claims.values() if value.get('issue_id') in ids]}


def context(db, *, scope, at=None):
    """The service calls PERSONAL only after the current run's market freeze."""
    if scope not in ('PUBLIC', 'PERSONAL'):
        raise ValueError('the archive read requires PUBLIC or PERSONAL scope')
    manifest = db.metadata(ARCHIVE)
    if manifest is None:
        return {'status': 'ARCHIVE_NOT_CAPTURED'}
    issues = manifest['research']
    native = _native_states(db, issues, scope)
    if scope == 'PUBLIC':
        from . import trading_decisions as decisions

        continuity = manifest.get('continuity') or {}
        review_targets = decisions.review_targets(db, continuity.get('targets', []), at=at or stamp())
        allowed = {'issue_id', 'request_id', 'independent_id', 'requested_at', 'public_completed_at'}
        research = [{key: value for key, value in item.items() if key in allowed}
                    for item in issues if item.get('independent_id')]
        public = {key: value for key, value in manifest['public'].items() if key != 'conditions'}
        return {**public, 'captured_at': manifest['captured_at'], 'research': research,
                'native_research': native, 'review_targets': review_targets, 'review_gaps': continuity.get('gaps', [])}
    records = []
    for ref in manifest['references']:
        if ref['scope'] != scope:
            continue
        try:
            state = db.value(ref['digest'])
            records.append({**ref, **state})
        except READ_ERRORS:
            records.append({**ref, 'status': 'ORIGINAL_OBJECT_UNKNOWN'})
    return {'status': 'ORIGINAL_PERSONAL_EVIDENCE', 'captured_at': manifest['captured_at'],
            'records': records, 'native_research': native}


def inspect_research(db, *, release, scope, request_id, phase, evidence_id=None):
    """Read one exact artifact without advancing an old run or starting a worker."""
    if (scope not in ('PUBLIC', 'PERSONAL') or phase not in ('independent', 'public-result', 'candidate')
            or not isinstance(request_id, str) or not DIGEST.fullmatch(request_id)):
        raise ValueError('select an exact original research request and phase')
    if scope == 'PUBLIC' and phase != 'public-result':
        raise Conflict('only the verified independent market result is exposed in the public archive')
    manifest = db.metadata(ARCHIVE)
    issue = next((item for item in (manifest or {}).get('research', []) if item.get('request_id') == request_id), None)
    if not issue:
        raise Conflict('the original research request was not captured in this node archive')
    if phase == 'public-result' and (not issue['independent_id'] or evidence_id != issue['independent_id']):
        raise Conflict('select the captured original independent result for this request')
    if phase == 'candidate' and (not isinstance(evidence_id, str) or not DIGEST.fullmatch(evidence_id)):
        raise ValueError('select an exact original candidate identity')
    if phase == 'independent' and evidence_id is not None:
        raise ValueError('the original independent input is identified by its request')

    from .execution import load_methods

    root = Path(db.settings['release_root']).resolve()
    original = manifest['source_release']
    source = root / original if COMMIT.fullmatch(original) else Path(release)
    try:
        load_methods(source)
        from v4 import research
        archive = _archive_root(db)
        if phase == 'independent':
            request = research._load(archive, 'requests', request_id)
            input_id = request['independent_input_id']
            rel = Path(request['independent_input_path'])
            if (not DIGEST.fullmatch(input_id)
                    or rel != Path('research/inputs') / (input_id[7:] + '.json')
                    or (archive / rel).resolve().parent != (archive / 'research/inputs').resolve()):
                raise Conflict('original independent material escapes its archive')
        result = research.read_input(archive, request_id, phase=phase,
                                     candidate_id=evidence_id if phase == 'candidate' else None,
                                     independent_id=evidence_id if phase == 'public-result' else None)
    except READ_ERRORS:
        return {'status': 'ORIGINAL_ARTIFACT_UNKNOWN', 'request_id': request_id,
                'phase': phase, 'evidence_id': evidence_id}
    return {'status': 'ORIGINAL_ARTIFACT_VERIFIED', **result}
