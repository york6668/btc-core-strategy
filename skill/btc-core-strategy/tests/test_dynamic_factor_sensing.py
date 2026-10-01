"""Dynamic evidence can inform observation and questions without deciding strategy."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_workflow_main as main_fixture
import test_market_observation as market_fixture
from v4 import evidence, sensing, storage
from v4.common import Invalid, create, digest, read


class DynamicSensingTests(unittest.TestCase):
    def setUp(self):
        self.f = main_fixture.MainTests('test_sol_publishes_complete_strategy_without_astra_or_report')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.commit()
        self.db = self.f.project / '.strategy/market_observation.sqlite3'

    def ctx(self, directory=None):
        f = self.f
        return sensing.context(f.store, self.db, f.project, f.raw, evidence_dir=directory or f.evidence_dir)

    def review(self, items=None):
        ctx = self.ctx()
        return {'schema': 'BTC_SENSING_REVIEW_V4', 'revision': 4, 'context_id': ctx['context_id'],
                'market_review': market_fixture.review(self.f.raw, {'context_id': ctx['context_id'], 'baselines': {}}, required=False),
                'recommendation_checks': [{'side': side, 'findings': ['NO_MATERIAL_CHANGE'],
                    'refs': ['windows.4h.binance_spot_1h'], 'explanation': 'Synthetic current recommendations remain applicable.'}
                    for side in ('upper_short', 'lower_spot')],
                'extra_findings': [], 'main_evaluation': {'required': False, 'reasons': []}, 'research_findings': [],
                'factor_checks': {'items': items or [], 'summary': 'Synthetic dynamic selection, no automatic strategy implication.'}}

    def factor(self):
        source = self.f.evidence['sources'][0]
        raw = read(self.f.evidence_dir / 'raw' / (source['raw_hash'][7:] + '.json'))
        key = next(iter(raw))
        ref = {'source_id': source['id'], 'pointer': '/' + key.replace('~', '~0').replace('/', '~1'),
               'value_hash': digest(raw[key])}
        return {'id': 'previously-unlisted-mechanism', 'name': 'Synthetic novel mechanism',
                'assessment': 'Tentative explanation, uncertain duration and direction.',
                'change_reason': 'New question identified in the available public evidence.', 'refs': [ref], 'counter_refs': []}

    def record(self, review):
        f = self.f
        return sensing.record(f.store, self.db, f.project, f.raw, review, evidence_dir=f.evidence_dir)

    def test_empty_or_unlisted_selection_does_not_force_research_or_publish(self):
        before = storage.current(self.f.store)['decision_id']
        first = self.record(self.review())
        second = self.record(self.review([self.factor()]))
        self.assertNotEqual(first['observation_id'], second['observation_id'])
        self.assertFalse(second['research_eligible'])
        self.assertEqual(storage.current(self.f.store)['decision_id'], before)
        saved = sensing.observation(self.f.store, second['observation_id'])
        directory = self.f.store / 'evidence' / saved['context']['identity']['factor_evidence_id'][7:]
        self.assertTrue((directory / 'evidence.json').exists())
        self.assertIn('factor_checks', self.ctx()['previous_hour']['review'])

    def test_damaged_counterevidence_is_rejected(self):
        item = self.factor()
        item['counter_refs'] = [dict(item['refs'][0], value_hash='sha256:' + '0' * 64)]
        with self.assertRaisesRegex(Invalid, 'reference mismatch'):
            self.record(self.review([item]))

    def test_initial_important_question_accepts_non_market_raw_reference(self):
        item = self.factor()
        review = self.review([item])
        review['research_findings'] = [{'issue_key': 'NEW_TRANSMISSION_QUESTION', 'scope': ['/factor_assessment'],
            'question': 'Does this initially observed mechanism change the forward value path?',
            'material_change': True, 'change_kind': 'INITIAL_QUESTION', 'change_reason': 'First material unresolved question.',
            'refs': item['refs'], 'reopen_condition': None}]
        result = self.record(review)
        self.assertTrue(result['research_eligible'])
        self.assertTrue(sensing.gate(self.f.store, result['observation_id'])['research_eligible'])
        self.assertFalse((self.f.store / 'deep-current.json').exists())

    def test_new_evidence_with_same_crypto_package_invalidates_old_context(self):
        old_review = self.review()
        spec = {k: deepcopy(v) for k, v in self.f.evidence.items() if k not in ('schema', 'evidence_id')}
        spec['parent_id'] = None
        files = {s['id']: str(self.f.evidence_dir / 'raw' / (s['raw_hash'][7:] + '.json')) for s in spec['sources']}
        payload = self.f.base / 'new-public-release.json'
        create(payload, {'period': 'previous-month', 'revision': 'new-public-release', 'value': 7})
        source = dict(spec['sources'][0], id='new-public-release', url='https://www.eia.gov/public-release',
                      origin=evidence.canonical_origin('https://www.eia.gov/public-release'), raw_hash=digest(payload.read_bytes()))
        spec['sources'].append(source)
        files[source['id']] = str(payload)
        directory = self.f.base / 'new-factor-root'
        evidence.freeze(spec, files, directory)
        self.assertNotEqual(self.ctx()['context_id'], self.ctx(directory)['context_id'])
        with self.assertRaisesRegex(Invalid, 'stale recommendation sensing context'):
            sensing.record(self.f.store, self.db, self.f.project, self.f.raw, old_review, evidence_dir=directory)

    def test_guide_revision_invalidates_observation_gate(self):
        from unittest import mock
        guide = self.f.project / '价值因子.md'
        create(guide, 'Synthetic dynamic research guidance.')
        with mock.patch('node_config.factor_guide', return_value=guide):
            result = self.record(self.review())
        guide.write_text('Revised synthetic guidance, no fixed required factors.')
        with self.assertRaisesRegex(Invalid, 'factor guide changed'):
            sensing.gate(self.f.store, result['observation_id'])

    def test_factor_selection_change_does_not_invent_new_public_data(self):
        self.record(self.review([self.factor()]))
        ctx = self.ctx()
        self.assertEqual(ctx['factor_context']['current']['status'], 'HISTORY_UNASSESSED')
        self.assertEqual(ctx['identity']['factor_evidence_id'], self.f.evidence['evidence_id'])

    def test_changed_model_interpretation_of_same_raw_is_not_discarded(self):
        first = self.record(self.review())
        revised = self.review()
        revised['market_review']['deep_review'] = {'required': True, 'reasons': ['Synthetic newly identified market contradiction.']}
        second = self.record(revised)
        self.assertNotEqual(first['observation_id'], second['observation_id'])
        self.assertIn('MARKET_CHANGE', second['triggers'])
        self.assertFalse(second['research_eligible'])

    def test_context_rejects_factor_package_without_matching_current_market(self):
        spec = {k: deepcopy(v) for k, v in self.f.evidence.items() if k not in ('schema', 'evidence_id')}
        spec.update(parent_id=None, sources=[spec['sources'][0]], measurements=[], verified_facts=[])
        source = spec['sources'][0]
        directory = self.f.base / 'no-current-market'
        evidence.freeze(spec, {source['id']: str(self.f.evidence_dir / 'raw' / (source['raw_hash'][7:] + '.json'))}, directory)
        with self.assertRaisesRegex(Invalid, 'exact current public market package'):
            self.ctx(directory)

    def test_untrusted_factor_can_be_observed_but_cannot_alone_open_research(self):
        from v4 import research
        package = deepcopy(self.f.evidence)
        source_id = self.factor()['refs'][0]['source_id']
        next(s for s in package['sources'] if s['id'] == source_id)['trust'] = 'UNTRUSTED_LEAD'
        question = {'issue_key': 'UNTRUSTED_FIRST_QUESTION', 'scope': ['/factor_assessment'],
            'question': 'Could this lead matter?', 'material_change': True, 'change_kind': 'INITIAL_QUESTION',
            'change_reason': 'Unverified lead only.', 'refs': self.factor()['refs'], 'reopen_condition': None}
        with self.assertRaisesRegex(Invalid, 'untrusted lead cannot support verified fact'):
            research.findings(self.f.store, [question], set(), package, self.f.evidence_dir)


if __name__ == '__main__':
    unittest.main()
