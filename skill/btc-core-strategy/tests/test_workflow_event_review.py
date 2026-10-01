"""Event review receipts stay separate from core publication and question windows."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_workflow_main as main_support
import test_market_observation as fixtures
from v4 import evidence, research, run_flow, sensing, storage
from v4.common import Invalid, create, digest, locked, read, replace


class EventReviewTests(unittest.TestCase):
    setUp = main_support.MainTests.setUp
    bind = main_support.MainTests.bind
    commit = main_support.MainTests.commit
    reused_assembly = main_support.MainTests.reused_assembly

    def advance_market(self):
        self.raw = fixtures.evidence(1898604000)  # 14Z; first event just due.
        path = self.base / 'event-current-market.json'
        create(path, self.raw)
        spec = {k: deepcopy(v) for k, v in self.evidence.items() if k not in ('schema', 'evidence_id')}
        spec.update(parent_id=self.evidence['evidence_id'], cutoff='2030-03-01T14:02:00Z')
        files = {s['id']: str(self.evidence_dir / 'raw' / (s['raw_hash'][7:] + '.json')) for s in spec['sources']}
        source = dict(next(s for s in spec['sources'] if s['id'] == 'hourly'))
        source.update(id='hourly_14', published_at=self.raw['cutoff'], end=self.raw['cutoff'],
                      retrieved_at=next(iter(self.raw['retrievals'].values()))['retrieved_at'],
                      start=self.raw['datasets']['binance_spot_1h']['requested_start'], raw_hash=digest(path.read_bytes()))
        spec['sources'].append(source)
        files[source['id']] = str(path)
        directory = self.base / 'event-current-evidence'
        clock = mock.patch('v4.evidence.now', return_value='2030-03-01T16:10:00Z')
        clock.start()
        self.addCleanup(clock.stop)
        self.evidence = evidence.freeze(spec, files, directory, self.evidence_dir)
        self.evidence_dir = directory

    def context(self, raw=None):
        return sensing.context(self.store, self.project / '.strategy/market_observation.sqlite3', self.project, raw or self.raw)

    def observe(self):
        ctx = self.context()
        review = {'schema': 'BTC_SENSING_REVIEW_V4', 'revision': 3, 'context_id': ctx['context_id'],
                  'market_review': fixtures.review(self.raw, {'context_id': ctx['context_id'], 'baselines': {}}, required=False),
                  'recommendation_checks': [{'side': s, 'findings': ['NO_MATERIAL_CHANGE'], 'refs': ['windows.4h.binance_spot_1h'],
                                            'explanation': 'Synthetic main event relevance checked.'} for s in ('upper_short', 'lower_spot')],
                  'extra_findings': [], 'main_evaluation': {'required': True, 'reasons': ['Review the event against the current core.']}, 'research_findings': []}
        return sensing.record(self.store, self.project / '.strategy/market_observation.sqlite3', self.project, self.raw, review)

    def complete_unchanged(self):
        draft, review = self.reused_assembly()
        with mock.patch('v4.storage.now', return_value='2030-03-01T16:00:00Z'):
            result = storage.commit(self.store, self.project, draft, self.evidence_dir, review, storage.current(self.store)['decision_id'])
        self.assertEqual(result['status'], 'UNCHANGED')
        return draft, review

    def test_unchanged_review_consumes_only_event_window_not_question_b(self):
        initial = self.commit()
        spec = {'issue_key': 'QUESTION_B_LEVERAGE', 'scope': ['/robustness'], 'question': 'Does leverage contradict the independent spot explanation?',
                'decision_id': initial['decision_id'], 'evidence_id': self.evidence['evidence_id'],
                'change': {'kind': 'INITIAL_QUESTION', 'reason': 'An independent unresolved causal question.',
                           'refs': [{'source_id': 'hourly', 'pointer': '/datasets', 'value_hash': digest(self.raw['datasets'])}], 'reopen_condition': None},
                'reopen_conditions': ['Independent leverage sequence reverses.']}
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:00Z'):
            question_b = research.request(self.store, self.project, spec)
        self.advance_market()
        before = self.context()
        self.assertEqual(before['due_event_reviews'], ['RELEASE_ONE'])
        observed = self.observe()
        self.assertIn('EVENT_WINDOW_REVIEW', observed['triggers'])
        self.complete_unchanged()
        after = self.context()
        self.assertEqual(after['current_core'], before['current_core'])
        self.assertEqual(after['accumulation'], before['accumulation'])
        self.assertEqual(after['research']['issues'], before['research']['issues'])
        self.assertEqual(after['research']['issues'][0]['issue_id'], question_b['issue_id'])
        self.assertEqual(after['event_review_baseline']['market_cutoff'], '2030-03-01T14:00:00Z')
        self.assertEqual(after['due_event_reviews'], [])
        self.assertNotIn('EVENT_WINDOW_REVIEW', self.observe()['triggers'])
        later = self.context(fixtures.evidence(1898607600))  # 15Z final event.
        self.assertEqual(later['due_event_reviews'], ['RELEASE_FINAL'])
        self.assertEqual(later['research']['issues'][0]['accumulation']['start'], '2030-03-01T13:00:00Z')
        self.assertEqual(later['research']['issues'][0]['accumulation']['windows']['binance_spot_1h']['actual_count'], 2)
        self.assertEqual(storage.current(self.store)['decision_id'], initial['decision_id'])
        self.assertFalse((self.store / 'deep-current.json').exists())

    def test_completion_identity_invalidates_stale_gate_and_draft(self):
        self.commit()
        self.advance_market()
        observed = self.observe()
        stale, review = self.reused_assembly()
        old_context = self.context()['context_id']
        self.complete_unchanged()
        self.assertNotEqual(self.context()['context_id'], old_context)
        with self.assertRaisesRegex(Invalid, 'new main completion'):
            sensing.gate(self.store, observed['observation_id'])
        with self.assertRaisesRegex(Invalid, 'requires main review'):
            storage.commit(self.store, self.project, stale, self.evidence_dir, review, storage.current(self.store)['decision_id'])

    def test_first_publication_already_consumes_old_events_without_receipt(self):
        self.commit()
        self.advance_market()
        draft, review = self.reused_assembly('Synthetic first new core explanation at the later market cutoff.')
        published = storage.commit(self.store, self.project, draft, self.evidence_dir, review, storage.current(self.store)['decision_id'])
        self.assertEqual(published['assessment']['temporal_scope']['market_cutoff'], '2030-03-01T14:00:00Z')
        (self.store / 'main-current.json').unlink()
        self.assertEqual(self.context()['due_event_reviews'], [])
        self.assertEqual(self.context()['event_review_baseline']['status'], 'CURRENT_CORE_ONLY')

    def test_unpointed_receipts_never_promoted_and_active_damage_not_ignored(self):
        self.commit()
        self.advance_market()
        self.complete_unchanged()
        pointer = read(self.store / 'main-current.json')
        (self.store / 'main-current.json').unlink()
        self.assertEqual(self.context()['due_event_reviews'], ['RELEASE_ONE'])
        replace(self.store / 'main-current.json', pointer)
        receipt_path = self.store / pointer['completion']
        receipt = read(receipt_path)
        receipt['completed_at'] = '2030-03-01T17:00:00Z'
        replace(receipt_path, receipt)
        with self.assertRaisesRegex(Invalid, 'main completion damaged'):
            self.context()
        receipt_path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.context()

    def test_valid_receipt_for_old_facts_falls_back_to_current_core(self):
        self.commit()
        self.advance_market()
        self.complete_unchanged()
        (self.project / '资产配置.md').write_text('Changed synthetic fact; no real asset.\n')
        ctx = self.context()
        self.assertEqual(ctx['event_review_baseline']['status'], 'CURRENT_CORE_ONLY_MAIN_FACTS_CHANGED')
        self.assertEqual(ctx['due_event_reviews'], ['RELEASE_ONE'])

    def test_interrupted_publication_keeps_older_receipt_explicitly_inapplicable(self):
        self.commit()
        original_pointer = read(self.store / 'main-current.json')
        self.advance_market()
        draft, review = self.reused_assembly('A useful later explanation was published before an interruption.')
        def interrupt(stage):
            if stage == 'after_pointer':
                raise RuntimeError('Synthetic interruption before main receipt pointer.')
        with self.assertRaises(RuntimeError):
            storage.commit(self.store, self.project, draft, self.evidence_dir, review, storage.current(self.store)['decision_id'], interrupt)
        self.assertEqual(read(self.store / 'main-current.json'), original_pointer)
        ctx = self.context()
        self.assertEqual(ctx['event_review_baseline']['status'], 'CURRENT_CORE_ONLY_OLDER_MAIN_DECISION')
        self.assertEqual(ctx['event_review_baseline']['market_cutoff'], '2030-03-01T14:00:00Z')
        self.assertEqual(ctx['due_event_reviews'], [])

    def test_receipt_bound_to_unpublished_orphan_is_rejected(self):
        self.commit()
        self.advance_market()
        self.complete_unchanged()
        path = self.store / read(self.store / 'main-current.json')['completion']
        receipt = read(path)
        receipt['decision_id'] = 'f' * 64
        receipt['completion_hash'] = digest({k: v for k, v in receipt.items() if k != 'completion_hash'})
        replace(path, receipt)
        with self.assertRaisesRegex(Invalid, 'not current ancestry'):
            self.context()

    def normalized_publication(self):
        # Real publication derives legacy control from the canonical baseline.
        self.draft['domain']['control']['notification_class'] = 'SYNTHETIC_STALE'
        result = self.commit()
        path = self.store / read(self.store / 'main-current.json')['completion']
        return result, path, read(path)

    def test_normalized_submission_keeps_original_review_identity(self):
        result, path, receipt = self.normalized_publication()
        self.assertEqual(receipt['submitted_draft'], self.draft)
        self.assertNotEqual(receipt['assessment']['domain']['control'], self.draft['domain']['control'])
        self.assertEqual(receipt['review']['draft_id'], self.draft['draft_id'])
        self.assertEqual(path.stem, receipt['completion_hash'][7:])
        self.assertEqual(self.context()['event_review_baseline']['status'], 'VERIFIED_MAIN_COMPLETION')
        self.assertEqual(storage.current(self.store)['decision_id'], result['decision_id'])

    def test_resealed_normalization_cannot_change_reviewed_business_content(self):
        _, _, receipt = self.normalized_publication()
        receipt['assessment']['conclusion']['narrative'] = 'Unreviewed business change.'
        receipt['completion_hash'] = digest({k: v for k, v in receipt.items() if k != 'completion_hash'})
        path = self.store / 'main' / (receipt['completion_hash'][7:] + '.json')
        create(path, receipt)
        replace(self.store / 'main-current.json', {'completion': str(path.relative_to(self.store))})
        with self.assertRaisesRegex(Invalid, 'normalization changed reviewed content'):
            self.context()

    def test_repair_old_normalized_receipt_preserves_publication_and_resumes_intent(self):
        result, _, receipt = self.normalized_publication()
        receipt.pop('submitted_draft')
        receipt['completion_hash'] = digest({k: v for k, v in receipt.items() if k != 'completion_hash'})
        old_path = self.store / 'main' / (receipt['draft_id'][7:] + '.json')
        create(old_path, receipt)
        old_bytes = old_path.read_bytes()
        old_pointer = {'completion': str(old_path.relative_to(self.store))}
        replace(self.store / 'main-current.json', old_pointer)
        with self.assertRaisesRegex(Invalid, 'assessment damaged'):
            self.context()
        bad = deepcopy(self.draft)
        bad['conclusion']['narrative'] = 'Not the submitted draft.'
        with locked(self.store), self.assertRaises(Invalid):
            storage.repair_main_completion(self.store, self.project, bad)
        self.assertEqual(read(self.store / 'main-current.json'), old_pointer)
        with locked(self.store), mock.patch('v4.storage.now', return_value='2030-03-01T13:05:00Z'):
            repaired = storage.repair_main_completion(self.store, self.project, self.draft)
        self.assertEqual(repaired['status'], 'MAIN_COMPLETION_REPAIRED')
        self.assertEqual(storage.current(self.store)['decision_id'], result['decision_id'])
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(self.context()['event_review_baseline']['status'], 'VERIFIED_MAIN_COMPLETION')
        resumed = run_flow._already_completed({'root': str(self.store)}, 'main', {'draft': self.draft})
        self.assertEqual(resumed, {'decision_id': result['decision_id'], 'status': 'PUBLISHED'})
        with locked(self.store):
            self.assertEqual(storage.repair_main_completion(self.store, self.project, self.draft)['status'], 'VERIFIED_MAIN_COMPLETION')


if __name__ == '__main__':
    unittest.main()
