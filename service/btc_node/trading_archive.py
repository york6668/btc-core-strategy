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


def _public_history(root, as_of):
    try:
        from v4 import forward_lifecycle, market_state, storage
        from v4.common import digest

        owner = storage.current(root)
        if not owner:
            return {'status': 'NO_ORIGINAL_DECISION', 'conditions': []}
        if millis(owner['created_at']) > millis(as_of):
            return {'status': 'ORIGINAL_DECISION_NOT_YET_KNOWN', 'conditions': []}
        assessment = owner['assessment']
        public = market_state.public_content(assessment)
        if not public:
            return {'status': 'NO_INDEPENDENT_PUBLIC_RECORD', 'conditions': []}
        market = public['outlook']['market_assessment']
        basis = {'prior_decision': owner['decision_id'], 'formed_at': owner['created_at'],
                 'knowledge_cutoff': assessment['knowledge_cutoff'], 'market_assessment': market,
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
    """Capture original references inside the existing single writer transaction."""
    manifest = db.metadata(ARCHIVE)
    if manifest is not None:
        return
    from .execution import load_methods

    original = _archive_method(db, Path(release))
    load_methods(original)
    references = _entity_references(db, as_of)
    public = _public_history(_archive_root(db), as_of)
    issues = _research_index(db, references)
    requirements = db.rows("""SELECT r.requirement_key,r.cause_key,r.cause_version,r.opened_ms,r.reason,
                                     e.scope,e.kind,e.object_id
                                FROM review_requirements r JOIN entities e USING(entity_key)
                               WHERE r.resolved_ms IS NULL AND r.opened_ms<=%s AND e.object_id NOT LIKE %s
                            ORDER BY r.opened_ms,r.requirement_key""", (millis(as_of), 'trading/%'))
    db.metadata(ARCHIVE, {'captured_at': stamp(as_of), 'source_release': original.name,
                         'public': public, 'research': issues, 'references': references,
                         'review_requirements': requirements}, write=True)


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


def context(db, *, scope):
    """The service calls PERSONAL only after the current run's market freeze."""
    if scope not in ('PUBLIC', 'PERSONAL'):
        raise ValueError('the archive read requires PUBLIC or PERSONAL scope')
    manifest = db.metadata(ARCHIVE)
    if manifest is None:
        return {'status': 'ARCHIVE_NOT_CAPTURED'}
    issues = manifest['research']
    native = _native_states(db, issues, scope)
    requirements = [item for item in manifest['review_requirements'] if item['scope'] == scope]
    if scope == 'PUBLIC':
        allowed = {'issue_id', 'request_id', 'independent_id', 'requested_at', 'public_completed_at'}
        research = [{key: value for key, value in item.items() if key in allowed}
                    for item in issues if item.get('independent_id')]
        return {**manifest['public'], 'captured_at': manifest['captured_at'], 'research': research,
                'native_research': native, 'review_requirements': requirements}
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
            'records': records, 'native_research': native, 'review_requirements': requirements}


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
