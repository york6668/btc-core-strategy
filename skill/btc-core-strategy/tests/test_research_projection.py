"""Requirement regressions over original public freezes; synthetic data only."""
from copy import deepcopy
import json
import unittest
from unittest import mock

import test_forward_lifecycle as fixture
from v4 import forward_lifecycle as life
from v4.common import Invalid, create, digest

T0, T1, T2 = fixture.T0, fixture.T1, fixture.T2


class DeadlineTests(unittest.TestCase):
    setUp = fixture.ForwardTests.setUp
    derived = fixture.ForwardTests.derived
    check = fixture.ForwardTests.check

    def dated(self, end):
        raw = {'announced_end': end}
        path = self.directory / 'calendar.json'
        create(path, raw)
        raw_hash = digest(path.read_bytes())
        create(self.directory / 'raw' / (raw_hash[7:] + '.json'), path.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][1],
            id='calendar', raw_hash=raw_hash))
        self.old = fixture.proposition('dated-original')
        self.old['scale'].update(end=end, end_ref={
            'source_id': 'calendar', 'pointer': '/announced_end',
            'value_hash': digest(end)})
        self.prior = {'forward': fixture.forward(self.old)}
        with mock.patch.object(life, 'now', return_value=T0):
            fixture.register_public(self.root, {'markets': [
                {'id': 'BTC', 'horizons': [dict(self.prior, id='cycle')]}]})
        self.review['prior_claim_hash'] = digest(self.old)
        return life.registration(self.root, digest(self.old))

    def test_later_success_cannot_validate_expired_original(self):
        self.dated('2024-01-01T12:00:00Z')
        with self.assertRaisesRegex(Invalid, 'original forecast deadline'):
            self.check()

    def test_exact_deadline_observation_with_late_publication_still_counts(self):
        self.dated(T1)
        result = self.check()
        self.assertEqual(result['verification'][0]['verdict'], 'SUPPORTED')

    def test_late_observation_stays_unknown_without_rolling_end(self):
        before = self.dated('2024-01-01T12:00:00Z')
        review = deepcopy(self.review)
        review['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        value = fixture.forward(fixture.proposition('independent', T2), [review])
        result = self.check(value)
        self.assertEqual(result['verification'][0]['verdict'], 'UNKNOWN')
        self.assertEqual(life.registration(self.root, digest(self.old)), before)

    def test_in_window_failure_remains_failed_after_later_success(self):
        self.dated(T1)
        bad = deepcopy(self.raw[1])
        bad[4] = '90'
        path = self.directory / 'failed-price.json'
        create(path, [bad])
        raw_hash = digest(path.read_bytes())
        create(self.directory / 'raw' / (raw_hash[7:] + '.json'), path.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][0],
            id='failed-price', raw_hash=raw_hash))
        failed = deepcopy(self.review)
        failed['samples'][0]['ref'] = {
            'source_id': 'failed-price', 'pointer': '/0',
            'value_hash': digest(bad)}
        failed['outcomes'][0].update(verdict='FALSIFIED', supported_scope={})
        value = fixture.forward(fixture.proposition('corrected', T2,
            'FACT_CORRECTION'), [failed])
        value['prior_check']['outcome'] = 'REVISE'
        result = self.check(value)
        self.assertEqual(result['verification'][0]['verdict'], 'FALSIFIED')
        value = self.derived()
        value['lifecycle']['reviews'].insert(0, failed)
        with self.assertRaisesRegex(Invalid, 'falsified'):
            self.check(value)


class PersistedFailureTests(unittest.TestCase):
    setUp = fixture.ForwardTests.setUp
    derived = fixture.ForwardTests.derived
    check = fixture.ForwardTests.check

    def market(self, claim, reviews=()):
        return {'markets': [{'id': 'BTC', 'horizons': [
            {'id': 'cycle', 'forward': fixture.forward(claim, reviews)}]}]}

    def save(self, claim, reviews=()):
        from v4 import research
        with mock.patch.object(life, 'now', return_value=T2):
            return research._save(self.root, 'independent', 'independent_id',
                {'market_result': {'outlook': {'market_assessment': self.market(claim, reviews)}}})

    def failure(self):
        bad = deepcopy(self.raw[1]); bad[4] = '90'
        raw_hash = digest(json.dumps([bad]).encode())
        create(self.directory / 'raw' / (raw_hash[7:] + '.json'), json.dumps([bad]).encode())
        self.bundle['sources'].append(dict(self.bundle['sources'][0], id='failed-price', raw_hash=raw_hash))
        failed = deepcopy(self.review)
        failed['samples'][0]['ref'] = {'source_id': 'failed-price', 'pointer': '/0', 'value_hash': digest(bad)}
        failed['outcomes'][0].update(verdict='FALSIFIED', supported_scope={})
        return failed

    def persist_failure(self):
        self.failed = self.failure()
        self.corrected = fixture.proposition('corrected', T2, 'FACT_CORRECTION')
        value = fixture.forward(self.corrected, [self.failed])
        value['prior_check']['outcome'] = 'REVISE'
        self.assertTrue(self.check(value)['falsified_prior'])
        self.save(self.corrected, [self.failed])
        return {'forward': value}

    def test_omitted_archived_failure_cannot_regrant_original_or_successor(self):
        prior = self.persist_failure()
        for reviews in ([], [self.review]):
            value = fixture.forward(self.old, reviews)
            value['prior_check']['outcome'] = 'REALIZED' if reviews else 'EVIDENCE_LIMITED'
            with self.subTest(reviews=len(reviews)), self.assertRaisesRegex(Invalid, 'falsified'):
                self.check(value)
        with self.assertRaisesRegex(Invalid, 'falsified'):
            self.check(self.derived(), prior)
        # The next immediate prior no longer even contains the failed review.
        hop = fixture.proposition('unrelated-hop', T2, 'INDEPENDENT_NEW_FACTS')
        self.save(hop)
        with self.assertRaisesRegex(Invalid, 'falsified'):
            self.check(self.derived(), {'forward': fixture.forward(hop)})

    def test_correction_and_independent_facts_continue_without_old_raw_samples(self):
        self.persist_failure()
        self.bundle['sources'] = [s for s in self.bundle['sources'] if s['id'] != 'failed-price']
        for kind in ('FACT_CORRECTION', 'INDEPENDENT_NEW_FACTS', 'RISK_RESPONSE'):
            value = fixture.forward(fixture.proposition('new-' + kind, T2, kind))
            value['prior_check']['outcome'] = 'REVISE'
            result = self.check(value)
            self.assertTrue(result['falsified_prior'])
            self.assertEqual(result['granted_dependencies'], 0)
            self.assertEqual(result['persisted_falsifications'][0]['review_hash'], digest(self.failed))
        unrelated = fixture.forward(fixture.proposition('unlinked', T2, 'INDEPENDENT_NEW_FACTS'))
        self.assertEqual(self.check(unrelated, {})['persisted_falsifications'], [])

    def test_new_version_can_earn_its_own_later_support(self):
        from v4 import closed_loop
        from v4.common import instant
        self.persist_failure()
        later = '2024-01-04T00:00:00Z'
        row = deepcopy(self.raw[1]); row[6] = int(instant(later).timestamp() * 1000)
        row[0] = row[6] - 1000
        raw = json.dumps([row]).encode(); raw_hash = digest(raw)
        create(self.directory / 'raw' / (raw_hash[7:] + '.json'), raw)
        self.bundle['sources'].append(dict(self.bundle['sources'][0], id='new-version-price',
            raw_hash=raw_hash, end=later, retrieved_at=later))
        review = deepcopy(self.review); review.update(prior_claim_hash=digest(self.corrected), evaluated_at=later)
        review['samples'][0]['ref'] = {'source_id': 'new-version-price', 'pointer': '/0', 'value_hash': digest(row)}
        successor = fixture.proposition('new-version-successor', later, 'VALIDATED_PREDECESSOR')
        dependency = {'claim_hash': digest(self.corrected), 'expectation_id': 'price-response',
                      'review_hash': digest(review), 'scope': deepcopy(fixture.BASE)}
        value = fixture.forward(successor, [review], [dependency])
        with mock.patch.object(life, 'now', return_value=later):
            result = closed_loop.validate_forward(value, later, self.resolve,
                {'forward': fixture.forward(self.corrected)}, revision=9, lifecycle_root=self.root)
        self.assertEqual(result['granted_dependencies'], 1)
        self.assertFalse(result['falsified_prior'])

    def test_only_failed_component_is_removed_from_predecessor_grants(self):
        self.old = fixture.proposition('two-components')
        healthy = deepcopy(self.old['expectations'][0]); healthy['id'] = 'healthy'
        healthy['falsification']['op'] = 'EQ'
        self.old['expectations'].append(healthy)
        self.prior = {'forward': fixture.forward(self.old)}
        with mock.patch.object(life, 'now', return_value=T0):
            fixture.register_public(self.root, self.market(self.old))
        self.review['prior_claim_hash'] = digest(self.old)
        prior = self.persist_failure()
        healthy_review = deepcopy(self.review); healthy_review['outcomes'][0]['expectation_id'] = 'healthy'
        value = self.derived(healthy_review)
        value['lifecycle']['dependencies'][0]['expectation_id'] = 'healthy'
        self.assertEqual(self.check(value, prior)['granted_dependencies'], 1)

    def test_later_same_claim_research_owner_is_not_lost(self):
        holder = fixture.proposition('existing-independent', T2, 'INDEPENDENT_NEW_FACTS')
        self.save(holder)
        failed = self.failure()
        value = fixture.forward(holder, [failed])
        self.check(value, {'forward': fixture.forward(holder)})
        self.save(holder, [failed])  # register() correctly keeps the first owner.
        first = life.registration(self.root, digest(holder))
        self.assertNotIn(b'FALSIFIED', (self.root / first['owner']['path']).read_bytes())
        with self.assertRaisesRegex(Invalid, 'falsified'):
            self.check(self.derived(), {'forward': fixture.forward(holder)})

    def test_later_same_claim_decision_and_observation_owners_are_consumed(self):
        from v4 import storage
        holder = fixture.proposition('existing-holder', T2, 'INDEPENDENT_NEW_FACTS')
        self.save(holder)
        failed = self.failure(); market = self.market(holder, [failed])
        # Existing producer-shaped, content-bound owners, no new registry.
        for kind in ('DECISION', 'OBSERVATION'):
            if kind == 'DECISION':
                storage._publish(self.root, {'created_at': T2, 'assessment': {
                    'revision': 9, 'outlook': {'market_assessment': market}}}, {}, None)
            else:
                value = {'created_at': T2, 'review': {'market_assessment': market}}
                value['observation_id'] = digest(value)
                create(self.root / 'sensing' / (value['observation_id'][7:] + '.json'), value)
            with self.subTest(kind=kind), self.assertRaisesRegex(Invalid, 'falsified'):
                self.check(self.derived(), {'forward': fixture.forward(holder)})
            if kind == 'DECISION':
                # Remove only this temporary fixture owner to isolate the next source.
                import shutil
                (self.root / 'current').unlink()
                shutil.rmtree(self.root / 'versions')

    def test_projection_recovers_omitted_failure_and_owner_without_copying_private_text(self):
        self.persist_failure()
        current = fixture.proposition('unrelated-visible', T2, 'INDEPENDENT_NEW_FACTS')
        self.save(current, [self.review])
        projection = life.research_projection(self.root, self.market(current, [self.review]), as_of=T2)
        rows = {r['forecast_id']: r for r in projection['records']}
        self.assertIn(digest(self.corrected), rows)
        old_reviews = rows[digest(self.old)]['recorded_reviews']
        self.assertEqual({v['verdict'] for r in old_reviews for v in r['outcomes']}, {'SUPPORTED', 'FALSIFIED'})
        self.assertFalse(projection['external_read_verified'])
        self.assertNotIn('The referenced later close', json.dumps(projection))

    def test_duplicate_recovery_preserves_failure_once_and_original_bytes(self):
        from pathlib import Path
        import shutil
        import tempfile
        self.persist_failure()
        before = {p: p.read_bytes() for p in (self.root / 'market-lifecycle').rglob('*.json')}
        first = life.persisted_failures(self.root, {digest(self.old)}, T2)
        self.assertEqual(len(first), 1)  # Research owner + first archive deduplicate.
        self.save(self.corrected, [self.failed])
        self.assertEqual(life.persisted_failures(self.root, {digest(self.old)}, T2), first)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        with tempfile.TemporaryDirectory(prefix='persisted-failure-restore-') as directory:
            restored = Path(directory)
            shutil.copytree(self.root / 'market-lifecycle', restored / 'market-lifecycle')
            self.assertEqual(life.persisted_failures(restored, {digest(self.old)}, T2), first)
            with mock.patch.object(self, 'root', restored), self.assertRaisesRegex(Invalid, 'falsified'):
                self.check(self.derived())

    def test_persisted_knowledge_clock_does_not_leak_future_failure(self):
        self.persist_failure()
        self.assertEqual(life.persisted_failures(self.root, {digest(self.old)}, T1), [])
        self.assertEqual(len(life.persisted_failures(self.root, {digest(self.old)}, T2)), 1)

    def test_damaged_related_history_is_unknown_and_cannot_grant_support(self):
        self.persist_failure()
        owner = life.registration(self.root, digest(self.corrected))['owner']
        path = self.root / owner['path']; path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(Invalid, 'freeze'):
            self.check(self.derived())
        result = life.research_projection(self.root, self.market(self.old), as_of=T2)
        self.assertIn('HISTORY_PERSISTENCE_UNVERIFIED', result['gaps'])
        # A new, unlinked hypothesis does not consume the damaged history.
        unrelated = fixture.forward(fixture.proposition('fresh-unlinked', T2, 'INDEPENDENT_NEW_FACTS'))
        self.assertEqual(self.check(unrelated, {})['granted_dependencies'], 0)

    def test_request_input_is_not_a_persisted_market_verdict(self):
        # A hash-bound request may contain authored input. Only its completed
        # market_result is the original producer's public assessment.
        value = {'lifecycle_frozen_at': T2, 'spec': self.market(self.old, [self.failure()])}
        value['request_id'] = digest(value)
        create(self.root / 'research/requests' / (value['request_id'][7:] + '.json'), value)
        self.assertEqual(self.check()['granted_dependencies'], 1)

    def test_recorded_unknown_does_not_become_a_failure_or_a_grant(self):
        unknown = deepcopy(self.review); unknown['outcomes'][0].update(verdict='UNKNOWN', supported_scope={})
        holder = fixture.proposition('unknown-holder', T2, 'FACT_CORRECTION')
        self.check(fixture.forward(holder, [unknown]))
        self.save(holder, [unknown])
        self.assertEqual(life.persisted_failures(self.root, {digest(self.old)}, T2), [])
        self.assertEqual(self.check()['granted_dependencies'], 1)

    def test_same_claim_history_corruption_cannot_silently_restore_support(self):
        holder = fixture.proposition('same-claim-damage', T2, 'INDEPENDENT_NEW_FACTS')
        self.save(holder)
        saved = self.save(holder, [self.failure()])
        path = self.root / 'research/independent' / (saved['independent_id'][7:] + '.json')
        saved['lifecycle_frozen_at'] = T1
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(Invalid, 'owner content changed'):
            self.check()
        projection = life.research_projection(self.root, self.market(self.old), as_of=T2)
        self.assertIn('HISTORY_PERSISTENCE_UNVERIFIED', projection['gaps'])

    def test_same_claim_restore_consumes_its_original_research_store(self):
        from pathlib import Path
        import shutil
        import tempfile
        holder = fixture.proposition('same-claim-restored', T2, 'INDEPENDENT_NEW_FACTS')
        self.save(holder); self.save(holder, [self.failure()])
        first = life.persisted_failures(self.root, {digest(self.old)}, T2)
        with tempfile.TemporaryDirectory(prefix='same-claim-restore-') as directory:
            restored = Path(directory)
            for name in ('market-lifecycle', 'research'):
                shutil.copytree(self.root / name, restored / name)
            self.assertEqual(life.persisted_failures(restored, {digest(self.old)}, T2), first)
            with mock.patch.object(self, 'root', restored), self.assertRaisesRegex(Invalid, 'falsified'):
                self.check()


class PublicFreezeHistoryTests(unittest.TestCase):
    setUp = fixture.ForwardTests.setUp
    check = fixture.ForwardTests.check
    derived = fixture.ForwardTests.derived
    market = PersistedFailureTests.market
    save = PersistedFailureTests.save
    failure = PersistedFailureTests.failure

    def freeze_run(self, directory, claim, reviews=(), stamp=T2):
        from pathlib import Path
        from v4 import run_flow
        payload = {'market_assessment': self.market(claim, reviews)}
        value = {'payload': payload, 'result_hash': digest(payload),
                 'market_context_id': digest('synthetic-public-context'), 'lifecycle_frozen_at': stamp}
        path = Path(directory) / 'market-result.json'
        run_flow.save(path, value)
        with mock.patch.object(life, 'now', return_value=T2):
            life.register(self.root, payload['market_assessment'], path, '/lifecycle_frozen_at')
        return path, value

    def assert_archived_owner(self, kind, legacy=False):
        from v4 import storage
        import tempfile
        holder = fixture.proposition('existing-' + kind, T2, 'INDEPENDENT_NEW_FACTS')
        self.save(holder)
        before = life.registration(self.root, digest(holder))
        failed = self.failure(); market = self.market(holder, [failed])
        self.check(fixture.forward(holder, [failed]), {'forward': fixture.forward(holder)})
        if kind == 'RESEARCH':
            saved = self.save(holder, [failed])
            path = self.root / 'research/independent' / (saved['independent_id'][7:] + '.json')
        elif kind == 'DECISION':
            saved = storage._publish(self.root, {'created_at': T2, 'assessment': {
                'revision': 9, 'outlook': {'market_assessment': market}}}, {}, None)
            path = self.root / 'versions' / saved['decision_id'] / 'decision.json'
        elif kind == 'OBSERVATION':
            value = {'schema': 'BTC_SENSING_OBSERVATION_V4', 'created_at': T2,
                     'revision': 9, 'review': {'market_assessment': market}}
            value['observation_id'] = digest(value)
            path = self.root / 'sensing' / (value['observation_id'][7:] + '.json')
            create(path, value)
            with mock.patch.object(life, 'now', return_value=T2):
                life.register(self.root, market, path, '/created_at')
        else:
            with tempfile.TemporaryDirectory(prefix='external-public-run-') as directory:
                path, _ = self.freeze_run(directory, holder, [failed])
                expected_hash = digest(path.read_bytes())
            self.assertFalse(path.exists())
        if kind != 'RUN':
            expected_hash = digest(path.read_bytes())
            if legacy:
                # Simulate pre-upgrade same-claim native owners, whose first
                # registration alone was archived. Do not remove their owner.
                (self.root / 'market-lifecycle/freezes' / (expected_hash[7:] + '.json')).unlink()
            else:
                path.unlink()  # Only this temporary original; isolate archive consumption.
        self.assertEqual(life.registration(self.root, digest(holder)), before)
        history = life.persisted_failures(self.root, {digest(self.old)}, T2)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['recorded_in'], expected_hash)
        self.assertEqual(history[0]['review'], failed)
        with self.assertRaisesRegex(Invalid, 'falsified'):
            self.check(self.derived(), {'forward': fixture.forward(holder)})
        projection = life.research_projection(self.root, self.market(self.old), as_of=T2)
        row = next(r for r in projection['records'] if r['forecast_id'] == digest(self.old))
        self.assertEqual(row['recorded_reviews'][0]['outcomes'][0]['verdict'], 'FALSIFIED')
        return expected_hash, history

    def test_unchanged_run_survives_external_cleanup_and_archive_only_restore(self):
        from pathlib import Path
        import shutil
        import tempfile
        owner_hash, history = self.assert_archived_owner('RUN')
        before = {p: p.read_bytes() for p in (self.root / 'market-lifecycle').rglob('*.json')}
        with tempfile.TemporaryDirectory(prefix='restore-public-freezes-') as directory:
            restored = Path(directory)
            shutil.copytree(self.root / 'market-lifecycle', restored / 'market-lifecycle')
            self.assertEqual(life.persisted_failures(restored, {digest(self.old)}, T2), history)
            with mock.patch.object(self, 'root', restored), self.assertRaisesRegex(Invalid, 'falsified'):
                self.check()
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertTrue((self.root / 'market-lifecycle/freezes' / (owner_hash[7:] + '.json')).is_file())

    def test_unchanged_research_freeze_survives_original_cleanup(self):
        self.assert_archived_owner('RESEARCH')

    def test_unchanged_decision_freeze_survives_original_cleanup(self):
        self.assert_archived_owner('DECISION')

    def test_unchanged_observation_freeze_survives_original_cleanup(self):
        self.assert_archived_owner('OBSERVATION')

    def test_preupgrade_research_owner_remains_readable_without_later_archive(self):
        self.assert_archived_owner('RESEARCH', legacy=True)

    def test_preupgrade_decision_owner_remains_readable_without_later_archive(self):
        self.assert_archived_owner('DECISION', legacy=True)

    def test_preupgrade_observation_owner_remains_readable_without_later_archive(self):
        self.assert_archived_owner('OBSERVATION', legacy=True)

    def test_repeated_run_freeze_deduplicates_without_rewriting_first_registration(self):
        import tempfile
        holder = fixture.proposition('repeat-run', T2, 'FACT_CORRECTION')
        failed = self.failure()
        with tempfile.TemporaryDirectory(prefix='repeat-public-freeze-') as directory:
            _, value = self.freeze_run(directory, holder, [failed])
            before = {p: p.read_bytes() for p in (self.root / 'market-lifecycle').rglob('*.json')}
            self.freeze_run(directory, holder, [failed])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        history = life.persisted_failures(self.root, {digest(self.old)}, T2)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['recorded_at'], value['lifecycle_frozen_at'])

    def test_unknown_ambiguous_and_malformed_archive_envelopes_do_not_become_empty_history(self):
        for name in ('unknown', 'ambiguous', 'malformed'):
            market = self.market(self.old)
            if name == 'unknown':
                value = {'future_public_result': market, 'lifecycle_frozen_at': T2}
            elif name == 'ambiguous':
                value = {'market_assessment': market, 'created_at': T2,
                         'market_result': {'outlook': {'market_assessment': market}}, 'lifecycle_frozen_at': T2}
            else:
                payload = {'unexpected_market': market}
                value = {'payload': payload, 'result_hash': digest(payload),
                         'market_context_id': digest('synthetic'), 'lifecycle_frozen_at': T2}
            path = self.root / 'market-lifecycle/freezes' / (digest(value)[7:] + '.json')
            create(path, value)
            try:
                with self.subTest(shape=name), self.assertRaises((Invalid, KeyError)):
                    self.check()
                projection = life.research_projection(self.root, self.market(self.old), as_of=T2)
                self.assertIn('HISTORY_PERSISTENCE_UNVERIFIED', projection['gaps'])
            finally:
                path.unlink()  # The intentionally malformed temporary control only.

    def test_changed_payload_hash_and_missing_original_clock_cannot_register_history(self):
        import tempfile
        from pathlib import Path
        holder = fixture.proposition('invalid-public-run', T2, 'FACT_CORRECTION')
        payload = {'market_assessment': self.market(holder)}
        for name in ('hash', 'clock'):
            value = {'payload': payload, 'result_hash': digest(payload),
                     'market_context_id': digest('synthetic'), 'lifecycle_frozen_at': T2}
            if name == 'hash': value['result_hash'] = digest('not-this-payload')
            else: del value['lifecycle_frozen_at']
            before = {p: p.read_bytes() for p in (self.root / 'market-lifecycle').rglob('*.json')}
            with tempfile.TemporaryDirectory(prefix='invalid-public-freeze-') as directory:
                path = Path(directory) / 'market-result.json'; create(path, value)
                with self.subTest(reason=name), mock.patch.object(life, 'now', return_value=T2), self.assertRaises(Invalid):
                    life.register(self.root, payload['market_assessment'], path, '/lifecycle_frozen_at')
            self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_actual_legacy_field_versions_remain_posthoc_without_hiding_registered_failure(self):
        from v4 import storage
        seed = storage._publish(self.root, {'created_at': T0, 'assessment': None,
            'historical_outlook': {'status': 'PREVIOUSLY_UNKNOWN'}}, {}, None)
        expected = seed['decision_id']
        for revision in range(1, 9):
            outlook = {'narrative': 'Synthetic legacy prose.'}
            if revision >= 5:
                horizon = {'id': 'legacy'}
                if revision >= 6: horizon['forward'] = {'prior_check': {'outcome': 'EVIDENCE_LIMITED'}}
                outlook['market_assessment'] = {'markets': [{'horizons': [horizon]}]}
            saved = storage._publish(self.root, {'created_at': T1,
                'assessment': {'revision': revision, 'outlook': outlook}}, {}, expected)
            expected = saved['decision_id']
        self.assertEqual(life.persisted_failures(self.root, {digest(self.old)}, T2), [])
        self.assertEqual(self.check()['granted_dependencies'], 1)
        failed = self.failure(); holder = fixture.proposition('after-legacy', T2, 'FACT_CORRECTION')
        self.save(holder, [failed])
        with self.assertRaisesRegex(Invalid, 'falsified'):
            self.check()

    def test_one_freeze_reusing_a_claim_across_horizons_registers_once(self):
        import tempfile
        from pathlib import Path
        from v4 import run_flow
        claim = fixture.proposition('shared-original', T2)
        market = self.market(claim)
        market['markets'][0]['horizons'].append(dict(market['markets'][0]['horizons'][0], id='other'))
        payload = {'market_assessment': market}
        value = {'payload': payload, 'result_hash': digest(payload),
                 'market_context_id': digest('shared-synthetic'), 'lifecycle_frozen_at': T2}
        with tempfile.TemporaryDirectory(prefix='shared-public-freeze-') as directory:
            path = Path(directory) / 'market-result.json'; run_flow.save(path, value)
            with mock.patch.object(life, 'now', return_value=T2):
                life.register(self.root, market, path, '/lifecycle_frozen_at')
                life.register(self.root, market, path, '/lifecycle_frozen_at')
        self.assertEqual(life.registration(self.root, digest(claim))['registered_at'], T2)
        self.assertEqual(len(list((self.root / 'market-lifecycle/claims').glob('*.json'))), 2)


class ProjectionTests(unittest.TestCase):
    setUp = fixture.ForwardTests.setUp

    def market(self, claim=None, reviews=()):
        return {'markets': [{'id': 'BTC', 'horizons': [
            {'id': 'horizon', 'forward': fixture.forward(claim or self.old,
                                                       reviews)}]}]}

    def projection(self, market=None, as_of=T2):
        feedback = life.public_feedback(self.root, market or self.market(),
                                        as_of=as_of)
        return feedback['research_projection']

    def test_projection_uses_original_identity_and_cannot_write_state(self):
        paths = list((self.root / 'market-lifecycle').rglob('*'))
        before = {p: p.read_bytes() for p in paths if p.is_file()}
        first = self.projection()
        second = self.projection()
        self.assertEqual(first, second)
        self.assertEqual({p: p.read_bytes() for p in paths if p.is_file()}, before)
        row = first['records'][0]
        self.assertEqual(row['forecast_id'], digest(self.old))
        self.assertEqual(row['first_registration'], T0)
        self.assertEqual(row['verification_basis'], 'RECORDED_NOT_REVERIFIED')
        self.assertFalse(first['external_read_verified'])

    def test_private_free_text_paths_and_unrecognized_fields_are_omitted(self):
        sentinel = 'PRIVATE_ACCOUNT_TOKEN_REPORT_SENTINEL'
        claim = fixture.proposition(sentinel)
        claim['scale']['process'] = sentinel
        claim['basis_refs'][0]['source_id'] = sentinel
        claim['expectations'][0]['id'] = sentinel
        claim['expectations'][0]['mechanism'] = sentinel
        market = self.market(claim)
        market['markets'][0]['id'] = sentinel
        market['markets'][0]['horizons'][0]['id'] = sentinel
        market['account'] = sentinel
        with mock.patch.object(life, 'now', return_value=T0):
            fixture.register_public(self.root, market)
        result = self.projection(market)
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(result['records'][0]['subjects'], ['BTC'])
        self.assertEqual(set(result), {'schema', 'as_of', 'records',
            'gaps', 'external_read_verified'})

    def test_pre_registration_cutoff_does_not_expose_future_record(self):
        result = self.projection(as_of='2023-12-31T23:59:00Z')
        self.assertEqual(result['records'], [])
        self.assertIn('RECORD_NOT_KNOWN_AT_AS_OF', result['gaps'])

    def test_unregistered_legacy_is_not_forged_into_a_prediction(self):
        path = self.root / 'market-lifecycle/claims' / (digest(self.old)[7:] + '.json')
        path.unlink()
        row = self.projection()['records'][0]
        self.assertEqual(row['registration_status'], 'POSTHOC_ONLY')
        self.assertIsNone(row['formed_at'])
        self.assertIsNone(row['first_registration'])
        self.assertEqual(row['due_status'], 'UNKNOWN')

    def test_original_and_successor_coexist_with_failed_review(self):
        review = deepcopy(self.review)
        review['outcomes'][0].update(verdict='FALSIFIED',
                                    supported_scope={}, explanation='private')
        successor = fixture.proposition('corrected', T2, 'FACT_CORRECTION')
        market = self.market(successor, [review])
        with mock.patch.object(life, 'now', return_value=T2):
            fixture.register_public(self.root, market)
        rows = self.projection(market)['records']
        self.assertEqual({r['forecast_id'] for r in rows},
                         {digest(self.old), digest(successor)})
        old = next(r for r in rows if r['forecast_id'] == digest(self.old))
        self.assertEqual(old['recorded_reviews'][0]['outcomes'][0]['verdict'],
                         'FALSIFIED')
        self.assertNotIn('private', json.dumps(rows))
        self.assertEqual(old['first_registration'], T0)

    def test_future_review_is_not_knowledge_at_earlier_cutoff(self):
        result = self.projection(self.market(reviews=[self.review]), as_of=T1)
        self.assertEqual(result['records'][0]['recorded_reviews'], [])
        self.assertIn('REVIEW_NOT_KNOWN_AT_AS_OF', result['gaps'])

    def test_missing_query_cutoff_is_unknown_not_current_time(self):
        row = self.projection(as_of=None)['records'][0]
        self.assertEqual(row['due_status'], 'UNKNOWN')

    def test_unsealed_review_cannot_supply_a_forged_knowledge_clock(self):
        result = self.projection(self.market(reviews=[self.review]))
        self.assertEqual(result['records'][0]['recorded_reviews'], [])
        self.assertIn('REVIEW_PERSISTENCE_UNVERIFIED', result['gaps'])

    def test_future_owner_cannot_publish_an_earlier_authored_review(self):
        review = deepcopy(self.review)
        review['evaluated_at'] = T1
        successor = fixture.proposition('future-owner', T2)
        market = self.market(successor, [review])
        with mock.patch.object(life, 'now', return_value=T2):
            fixture.register_public(self.root, market)
        result = self.projection(market, as_of=T1)
        self.assertFalse(any(row['recorded_reviews'] for row in result['records']))
        self.assertIn('RECORD_NOT_KNOWN_AT_AS_OF', result['gaps'])

    def test_review_cannot_claim_evaluation_after_its_persisted_owner(self):
        claim = fixture.proposition('inconsistent-owner')
        market = self.market(claim, [self.review])
        with mock.patch.object(life, 'now', return_value=T0):
            fixture.register_public(self.root, market)
        result = self.projection(market)
        self.assertFalse(any(row['recorded_reviews'] for row in result['records']))
        self.assertIn('REVIEW_PERSISTENCE_UNVERIFIED', result['gaps'])

    def test_actual_research_save_registers_then_projects_without_second_owner(self):
        from v4 import research
        claim = fixture.proposition('actual-save', T2)
        market = self.market(claim)
        value = {'market_result': {'outlook': {'market_assessment': market}}}
        with mock.patch.object(life, 'now', return_value=T2):
            result = research._save(self.root, 'independent', 'independent_id', value)
        before = {p: p.read_bytes() for p in (self.root / 'market-lifecycle').rglob('*.json')}
        projection = self.projection(result['market_result']['outlook']['market_assessment'])
        self.assertEqual(projection['records'][0]['forecast_id'], digest(claim))
        self.assertEqual(projection['records'][0]['first_registration'], T2)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def publish_later_review(self):
        from v4 import storage
        market = self.market(reviews=[self.review])
        value = {'created_at': T2, 'assessment': {'revision': 9,
                 'outlook': {'market_assessment': market}}}
        decision = storage._publish(self.root, value, {}, None)
        return market, {'kind': 'DECISION', 'id': decision['decision_id']}

    def test_unchanged_claim_later_review_uses_actual_decision_owner(self):
        market, owner = self.publish_later_review()
        projection = life.public_feedback(self.root, market, as_of=T2,
                                         source_owner=owner)['research_projection']
        row = projection['records'][0]
        self.assertEqual(row['first_registration'], T0)
        self.assertEqual(row['recorded_reviews'][0]['recorded_at'], T2)
        self.assertEqual(row['recorded_reviews'][0]['outcomes'][0]['verdict'], 'SUPPORTED')
        earlier = life.public_feedback(self.root, market, as_of=T1,
                                       source_owner=owner)['research_projection']
        self.assertEqual(earlier['records'][0]['recorded_reviews'], [])

    def test_corrupt_or_mismatched_owner_cannot_certify_a_review(self):
        market, owner = self.publish_later_review()
        changed = deepcopy(market)
        changed['markets'][0]['horizons'][0]['forward']['lifecycle']['reviews'][0]['evaluated_at'] = T1
        result = life.public_feedback(self.root, changed, as_of=T2,
                                      source_owner=owner)['research_projection']
        self.assertEqual(result['records'][0]['recorded_reviews'], [])
        self.assertIn('REVIEW_PERSISTENCE_UNVERIFIED', result['gaps'])

    def test_synthetic_restore_retains_original_clock_review_and_dedup(self):
        import shutil
        import tempfile
        from pathlib import Path
        market, owner = self.publish_later_review()
        expected = life.public_feedback(self.root, market, as_of=T2,
                                        source_owner=owner)['research_projection']
        with tempfile.TemporaryDirectory(prefix='projection-restore-') as directory:
            restored = Path(directory) / 'original'
            shutil.copytree(self.root, restored, symlinks=True)
            for _ in range(2):
                actual = life.public_feedback(restored, market, as_of=T2,
                                              source_owner=owner)['research_projection']
                self.assertEqual(actual, expected)
        self.assertEqual(len(expected['records']), 1)
        self.assertEqual(len(expected['records'][0]['recorded_reviews']), 1)

    def test_existing_hash_bound_observation_is_an_equivalent_review_owner(self):
        market = self.market(reviews=[self.review])
        value = {'created_at': T2, 'review': {'market_assessment': market}}
        identity = digest(value)
        value['observation_id'] = identity
        create(self.root / 'sensing' / (identity[7:] + '.json'), value)
        owner = {'kind': 'OBSERVATION', 'id': identity}
        result = life.public_feedback(self.root, market, as_of=T2,
                                      source_owner=owner)['research_projection']
        row = result['records'][0]
        self.assertEqual(row['first_registration'], T0)
        self.assertEqual(row['recorded_reviews'][0]['recorded_at'], T2)
        self.assertEqual(row['recorded_reviews'][0]['outcomes'][0]['verdict'], 'SUPPORTED')

    def test_due_projection_keeps_original_deadline_and_repeated_claim_identity(self):
        original = DeadlineTests.dated(self, T1)
        market = self.market()
        market['markets'][0]['horizons'] *= 2
        result = self.projection(market)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['horizon_end'], T1)
        self.assertEqual(result['records'][0]['due_status'], 'DUE')
        self.assertEqual(life.registration(self.root, digest(self.old)), original)


if __name__ == '__main__':
    unittest.main()
