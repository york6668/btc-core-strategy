"""Project existing validated contracts without inventing decisions or facts."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from .codec import Conflict, encode, fingerprint, identity, millis, stamp
from .entities import Entities
from .series import Series


class Projection:
    def __init__(self, db, method_version):
        self.db, self.method_version = db, method_version
        self.objects = Entities(db)
        self.results = []
        self.source_digests, self.source_dependencies = {}, {}

    def put(self, kind, object_id, payload, source, known, *, scope='PUBLIC', product=None,
            observed=None, active=True, due=None, expires=None, dependencies=(), identified=None, outcome_review=None):
        current = self.objects.get(kind, object_id)
        source = Path(source)
        if source not in self.source_digests:
            self.source_digests[source] = self.db.put_blob(source.read_bytes())
        source_digest = self.source_digests[source]
        # Validated old artifacts are immutable historical evidence. Re-reading
        # them must not overwrite a newer service judgement or revive retirement.
        if current and (current['known_ms'] > millis(known) or (not current['active'] and active)):
            return current
        if current and current['payload'] == payload and current['active'] == active and current.get('review_at') == due and current.get('valid_until') == expires:
            # Re-reading unchanged domain content is an observation, not a new
            # version of the judgement or a fresh prediction.
            self.db.execute('UPDATE entities SET last_checked_ms=%s WHERE entity_key=%s', (millis(known), current['entity_key']))
            return current
        spec = {'kind': kind, 'object_id': object_id, 'scope': scope, 'product': product,
                'observed_at': observed or known, 'known_at': known, 'change_reason': 'validated source projection',
                'source_ref': str(source), 'method_version': self.method_version, 'payload': deepcopy(payload),
                'active': active, 'review_at': due, 'valid_until': expires,
                'dependencies': list(dependencies) + [{'type': 'BLOB', 'digest': source_digest, 'relation': 'ORIGINAL'}]}
        spec['dependencies'].extend(self.evidence_dependencies(source))
        if identified and not current:
            spec['identified_at'] = identified
        if outcome_review:
            spec['outcome_review'] = outcome_review
        if current and not outcome_review and current.get('valid_until') != expires and current.get('valid_until') and millis(current['valid_until']) <= millis():
            # Preserve the expired deadline as a pending review. The model must
            # explicitly evaluate it; migration never counts as outcome evidence.
            spec['valid_until'] = current['valid_until']
        if current and millis(spec['observed_at']) < current['observed_ms']:
            spec['correction_of'] = current['version']
        result = self.objects.put(spec, expected_version=current['version'] if current else 0,
                                  trusted_projection=kind in {'FACT_PROJECTION', 'PROFILE'})
        self.results.append({'kind': kind, **result})
        return self.objects.get(kind, object_id)

    def evidence_dependencies(self, source):
        """Pin original evidence manifests/raw bytes, never their freshness."""
        source = Path(source)
        if source in self.source_dependencies:
            return self.source_dependencies[source]
        try:
            value = json.loads(Path(source).read_bytes())
        except (ValueError, UnicodeError):
            return []
        evidence_ids, hashes = set(), set()
        def visit(item, field=None):
            if isinstance(item, dict):
                for k, v in item.items(): visit(v, k)
            elif isinstance(item, list):
                for v in item: visit(v, field)
            elif isinstance(item, str) and item.startswith('sha256:') and len(item) == 71:
                hashes.add(item[7:])
                if field in {'evidence_id', 'factor_evidence_id'}:
                    evidence_ids.add(item[7:])
        visit(value)
        for evidence_id in evidence_ids:
            # A bundle's semantic identity can differ from its serialized hash.
            # Resolve its actual path rather than pretending the two are equal.
            rows = self.db.rows("SELECT digest FROM documents WHERE namespace='strategy' AND path=%s",
                                ('v4/evidence/' + evidence_id + '/evidence.json',))
            for row in rows:
                hashes.add(row['digest'])
                visit(self.db.value(row['digest']))
        result = [{'type': 'BLOB', 'digest': digest, 'relation': 'KEY_EVIDENCE'} for digest in sorted(hashes)
                  if self.db.one('SELECT digest FROM blobs WHERE digest=%s', (digest,))]
        self.source_dependencies[source] = result
        return result

    def prices(self, package, source):
        series = Series(self.db)
        original = fingerprint(Path(source).read_bytes())
        counts = []
        for name, dataset in package.get('datasets', {}).items():
            # Source product/market and units are part of identity; never use the
            # display asset alone to combine an index, proxy, mark or contract.
            instrument = {'market': dataset['market'], 'symbol': dataset.get('instrument') or name,
                          'product_type': dataset['market'], 'quote_unit': dataset['quote_unit'],
                          'source_identity': dataset['source']}
            # Index and mark are distinct quoted references to the contract;
            # candles/funding/OI are series on the same identified product.
            if dataset['kind'] in {'INDEX', 'MARK'}:
                instrument['product_type'] += ':' + dataset['kind']
            product = 'source:' + identity(instrument)
            series.instrument(product, instrument)
            known = package['retrievals'][name]['retrieved_at']
            rows = [{'item_key': str(row['time']), 'observed_at': row['time'], 'known_at': known,
                     'payload': row} for row in dataset.get('rows', [])]
            for start in range(0, len(rows), 10000):
                counts.append(series.ingest(product, name, rows[start:start+10000],
                    {'package_id': package.get('evidence_id'), 'original_digest': original,
                     'retrieval': package['retrievals'][name], 'quality': dataset.get('issues', [])},
                    interval_ms=dataset['interval_seconds'] * 1000))
        return counts

    def public(self, public, source, known, *, reference):
        assessment = public.get('market_assessment')
        result = self.put('DECISION', reference, public, source, known, active=False)
        market_dependency = {'type': 'ENTITY', 'kind': 'DECISION', 'object_id': reference, 'relation': 'MARKET_BASIS'}
        for zone in public.get('dispute_zones', []):
            first = [side['identified_at'] for side in (zone.get('boundary_review') or {}).values()
                     if isinstance(side, dict) and side.get('identified_at') and millis(side['identified_at']) <= millis(known)]
            self.put('REGION', zone['id'], zone, source, known, observed=zone['as_of'],
                     product=zone.get('market'), identified=min(first, key=millis) if first else None,
                     active=zone['status'] != 'RETIRED', dependencies=[market_dependency])
        for market in (assessment or {}).get('markets', []):
            self.put('CYCLE', 'market:' + market['id'], market, source, known,
                     observed=market.get('as_of', known), product=market['id'], dependencies=[market_dependency])
            for horizon in market.get('horizons', []):
                object_id = 'market:' + market['id'] + ':' + horizon['id']
                forward = horizon.get('forward') or {}
                realization = forward.get('realization_window') or {}
                judgement = forward.get('judgement_window') or {}
                old = self.objects.get('HYPOTHESIS', object_id)
                prior = forward.get('prior_check') or {}
                reviewed = None
                if (old and old.get('valid_until') and old['known_ms'] <= millis(known)
                        and old['payload'] != {'market': market['id'], **horizon}
                        and prior.get('outcome') in {'REALIZED', 'FAILED', 'REVISE'}):
                    reviewed = 'forward:' + identity(object_id, old['version'], reference)
                    self.put('REVIEW', reviewed, {'subject': {'kind': 'HYPOTHESIS', 'object_id': object_id,
                             'version': old['version']}, **prior, 'original_deadline': old['valid_until']},
                             source, known, active=False, dependencies=[market_dependency,
                             {'type': 'ENTITY', 'kind': 'HYPOTHESIS', 'object_id': object_id,
                              'version': old['version'], 'relation': 'OUTCOME_SUBJECT'}])
                self.put('HYPOTHESIS', object_id, {'market': market['id'], **horizon}, source, known, product=market['id'],
                         observed=market.get('as_of', known), due=realization.get('end'),
                         expires=judgement.get('end'), dependencies=[market_dependency], outcome_review=reviewed)
        return result

    def plans(self, recommendations, binding, source, known, *, adopted_at,
              fact_fields=None, due=None, dependencies=()):
        """A formal adoption owns its plans; a side name is not a plan identity.

        Re-reading that adoption reuses its objects. A replacement never extends
        the old plan's deadline or supplies its missing outcome evaluation.
        """
        decision_id = binding['object_id']
        existing, after = [], None
        while True:
            page = self.objects.list(scope='PERSONAL', kind='PLAN', active=None, limit=500, after=after)
            existing.extend(self.objects.get('PLAN', row['object_id']) for row in page['objects']
                            if row['object_id'].startswith('recommendation:'))
            after = page['next']
            if not after:
                break
        basis = {'type': 'ENTITY', 'kind': 'DECISION', 'object_id': decision_id,
                 'version': binding['version'], 'digest': binding['digest'], 'relation': 'FORMAL_PLAN_BASIS'}
        current_ids = {}
        for name, plan in recommendations.items():
            if name == 'portfolio':
                continue
            object_id = 'recommendation:' + name + ':' + decision_id
            current_ids[name] = object_id
            previous = self.objects.get('PLAN', object_id)
            expires = (plan.get('validity') or {}).get('until')
            if previous and previous['active'] and previous.get('valid_until') != expires:
                # Do not pair changed source content with the guarded old clock.
                raise Conflict('formal plan identity has a different deadline; preserve its outcome review')
            payload = {'formal_decision_id': decision_id, 'formal_created_at': adopted_at,
                       'recommendation_name': name, 'market_binding': binding,
                       'recommendation': plan, **(fact_fields or {})}
            self.put('PLAN', object_id, payload, source, known, observed=adopted_at,
                     identified=adopted_at, scope='PERSONAL', due=due, expires=expires,
                     dependencies=[basis, *dependencies])

        newer = [old for old in existing if old['payload'].get('formal_created_at')
                 and millis(old['payload']['formal_created_at']) > millis(adopted_at)]
        if newer:
            # A late historical source gets its original version, then the known
            # replacement. It cannot retire or revive the current formal plans.
            successor = max(newer, key=lambda old: millis(old['payload']['formal_created_at']))
            superseding = successor['payload']['market_binding']
            at = successor['payload']['formal_created_at']
            for name, object_id in current_ids.items():
                replacements = [old for old in newer if old['payload'].get('recommendation_name') == name
                                and old['payload']['formal_decision_id'] == superseding['object_id']]
                self.retire_plan(self.objects.get('PLAN', object_id), source,
                                 max(known, at, key=millis), superseding,
                                 replacements[0]['object_id'] if replacements else None)
            return
        for old in existing:
            if old['object_id'] in current_ids.values() or not old['active'] or old['known_ms'] > millis(known):
                continue
            name = old['payload'].get('recommendation_name') or old['object_id'].removeprefix('recommendation:')
            self.retire_plan(old, source, known, binding, current_ids.get(name))

    def retire_plan(self, old, source, known, binding, replacement):
        if not old['active'] or old['known_ms'] > millis(known):
            return
        payload = {**old['payload'], 'superseded_by': replacement,
                   'superseding_decision': binding, 'outcome_inferred': False}
        self.put('PLAN', old['object_id'], payload, source, known, scope='PERSONAL',
                 observed=known, active=False, due=old.get('review_at'), expires=old.get('valid_until'),
                 dependencies=old['dependencies'])
        if old.get('valid_until') and millis(old['valid_until']) <= millis(known):
            # Retirement is an applicability change, not an outcome review. Keep
            # the original version as a pending subject, including legacy IDs.
            requirement = identity(old['entity_key'], 'PLAN_OUTCOME', old['version'])
            self.db.execute('INSERT IGNORE INTO review_requirements(requirement_key,entity_key,cause_key,'
                            'cause_version,reason,opened_ms) VALUES(%s,%s,%s,%s,%s,%s)',
                            (requirement, old['entity_key'], old['entity_key'], old['version'],
                             'Superseded plan requires outcome review of original deadline ' + old['valid_until'] +
                             '; replacement implies neither realization nor execution.', millis(known)))

    def refresh(self, project, root):
        # Import only through the real validators and current-pointer authority.
        from v4 import storage, market_state, research, delivery
        from v4.common import facts
        project, root = Path(project), Path(root)
        from .history import project_history
        history = project_history(self, root)
        decision = storage.current(root)
        if not decision:
            return {'objects': [], 'status': 'NO_BASELINE'}
        source = root / 'versions' / decision['decision_id'] / 'decision.json'
        assessment = decision.get('assessment') or {}
        frozen_time = decision['created_at']
        public = market_state.public_content(assessment)
        if public:
            projected_public = {'market_assessment': public['outlook'].get('market_assessment'),
                                'dispute_zones': public['outlook'].get('dispute_zones', []),
                                'outlook': public['outlook'], 'factor_assessment': public['factor_assessment']}
            market = self.public(projected_public, source, frozen_time, reference=decision['decision_id'])
            binding = {k: market[k] for k in ('object_id', 'version', 'digest')}
            for factor in public['factor_assessment'].get('items', []):
                self.put('FACTOR', factor['id'], factor, source, frozen_time)
        else:
            binding = None
        effective = market_state.effective(root, assessment, decision['decision_id'])
        if effective.get('observation_id'):
            observed_path = root / 'sensing' / (effective['observation_id'].removeprefix('sha256:') + '.json')
            observed = json.loads(observed_path.read_bytes())
            known = observed.get('created_at') or observed.get('recorded_at')
            if known is None:
                known = stamp(datetime.fromtimestamp(observed_path.stat().st_mtime, timezone.utc))
            self.public(effective, observed_path, known, reference=effective['observation_id'])
        fact_version = facts(project)
        match = fact_version == decision['fact_version']
        for name, digest in fact_version.items():
            fact_path = project / name
            self.put('FACT_PROJECTION', name, {'file_hash': digest, 'authority': 'CONFIRMED_LOCAL_FILE',
                     'structured_status': 'MATCHED_FORMAL_VIEW' if match else 'NEEDS_EXTRACTION',
                     'formal_fact_version': decision['fact_version'],
                     'structured_view': assessment.get('domain', {}).get('account_facts') if match and name == '资产配置.md' else None},
                     fact_path, stamp(), scope='PERSONAL')
        fact_dependencies = [{'type': 'ENTITY', 'kind': 'FACT_PROJECTION', 'object_id': name,
                              'relation': 'CONFIRMED_FACT'} for name in fact_version]
        checked_time = stamp()
        fact_known = max(self.objects.get('FACT_PROJECTION', name)['known_ms'] for name in fact_version)
        review_due = stamp(datetime.fromtimestamp(fact_known / 1000, timezone.utc)) if not match else None
        self.put('ALLOCATION', 'portfolio', {'market_binding': binding, 'fact_version': decision['fact_version'],
                 'current_fact_version': fact_version, 'fact_review': 'CURRENT' if match else 'REQUIRED',
                 'portfolio_analysis': assessment.get('portfolio_analysis'),
                 'portfolio': assessment.get('recommendations', {}).get('portfolio'),
                 'fact_authority': 'bound source version'}, source, checked_time, observed=frozen_time,
                 due=review_due, scope='PERSONAL', dependencies=fact_dependencies)
        if binding:
            self.plans(assessment.get('recommendations', {}), binding, source, checked_time,
                       adopted_at=frozen_time, due=review_due, dependencies=fact_dependencies,
                       fact_fields={'fact_review': 'CURRENT' if match else 'REQUIRED',
                                    'fact_version': decision['fact_version'], 'current_fact_version': fact_version})
        for issue in research.context(root)['issues']:
            # Full research context can contain portfolio findings. Existing
            # independent-phase material is still prepared by the native contract.
            req = root / 'research/requests' / (issue['latest_request']['request_id'].removeprefix('sha256:') + '.json')
            self.put('RESEARCH', issue['issue_id'], issue, req, stamp(), scope='PERSONAL',
                     due=issue['latest_request']['created_at'] if issue['status'] == 'REQUESTED' else None)
        attempts = delivery.attempt_recovery(root) if hasattr(delivery, 'recovery_actions') else None
        actions = delivery.recovery_actions(root, attempts) if attempts is not None else []
        for path in (root / 'outbox').glob('*/status.json'):
            status = json.loads(path.read_bytes())
            artifact = path.with_name('artifact.json')
            if not artifact.exists():
                raise Conflict('delivery state has no frozen artifact')
            payload = {'status': status, 'artifact_digest': self.db.put_blob(artifact.read_bytes())}
            if attempts is not None:
                payload['recovery'] = {
                    'attempts': [v for v in attempts if v['delivery_id'] == path.parent.name],
                    'actions': [v for v in actions if v['delivery_id'] == path.parent.name]}
            else:
                # Original-version recovery still owns transitions. An unchanged
                # historical read must not erase a newer derived classification.
                previous = self.objects.get('DELIVERY', path.parent.name)
                if previous and previous['payload'].get('status') == status and 'recovery' in previous['payload']:
                    payload['recovery'] = previous['payload']['recovery']
            # Classification may use a later covering report. Keep the original
            # event time, but never backdate knowledge of this derived view.
            self.put('DELIVERY', path.parent.name, payload, path, stamp(), observed=status['updated_at'],
                     scope='PERSONAL', active=status['state'] not in {'DELIVERED', 'SUPERSEDED'},
                     dependencies=[{'type': 'BLOB', 'digest': self.db.put_blob(artifact.read_bytes()), 'relation': 'FROZEN_REPORT'}])
        return {'objects': self.results, 'market_binding': binding, 'facts_match': match, 'history': history}
