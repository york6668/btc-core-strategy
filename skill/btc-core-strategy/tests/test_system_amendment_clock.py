"""Generated amendment-clock regressions; no actual author/runtime evidence."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest import mock


import test_system_research_transport as transport_fixture
from test_forward_lifecycle import T2
from v4 import forward_lifecycle as life, research
from v4.common import digest, instant, read


class AmendmentRegistrationClockTests(unittest.TestCase):
    def setUp(self):
        self.fixture = transport_fixture.NativeResearchTransportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        with mock.patch('v4.forward_lifecycle.now', return_value=T2):
            validated = self.fixture.public_result()
            self.initial = research._save(self.root, 'independent', 'independent_id', {
                'schema': 'BTC_RESEARCH_INDEPENDENT_V4',
                'request_id': self.fixture.req['request_id'],
                'agent_id': 'GENERATED_CLOCK_TEST_NOT_ACTUAL_AUTHOR',
                'turn_id': 'SYNTHETIC_INITIAL', 'input_id': self.fixture.req['independent_input_id'],
                'rollout_path': str(self.root / 'not-an-actual-runtime.jsonl'),
                'started_at': instant(T2).timestamp() - 1,
                'completed_at': instant(T2).timestamp(), **validated,
            })
        self.old_records = {path: path.read_bytes()
                            for path in (self.root / 'market-lifecycle/claims').glob('*.json')}
        self.later = instant(T2) + timedelta(days=1)
        self.freeze_at = (self.later + timedelta(seconds=11)).isoformat()

    def amend(self, public):
        reference = self.fixture.output(public)
        proof = {'started_at': self.later.timestamp() - 1,
                 'completed_at': self.later.timestamp() + 10,
                 'last_agent_message': json.dumps(reference)}
        with mock.patch('v4.research._completed_turn', return_value=proof), \
                mock.patch('v4.forward_lifecycle.now', return_value=self.freeze_at):
            # Only native author execution is simulated. The original output
            # resolver, full public validators, immutable saving and claim
            # registration execute against generated source-bound fixtures.
            return research.amend_market(self.root, self.initial['independent_id'],
                self.initial['rollout_path'], turn_id='SYNTHETIC_LATER_AMENDMENT')

    def assert_old_registrations_unchanged(self):
        for path, body in self.old_records.items():
            self.assertEqual(path.read_bytes(), body)

    def test_new_claim_first_clock_is_later_amendment_freeze(self):
        public = deepcopy(self.fixture.public)
        for market in public['outlook']['market_assessment']['markets']:
            for horizon in market['horizons']:
                forward = horizon['forward']
                claim = forward['lifecycle']['claim']
                claim['id'] += '_GENERATED_LATER_CORRECTION'
                claim['formed_at'] = self.later.isoformat()
                forward['judgement_window']['start'] = claim['formed_at']
        amended = self.amend(public)
        self.assertEqual(amended['lifecycle_frozen_at'], self.freeze_at)
        self.assertNotEqual(amended['lifecycle_frozen_at'], self.initial['lifecycle_frozen_at'])
        self.assertEqual(amended['parent_independent_id'], self.initial['independent_id'])
        self.assertEqual(amended['independence_status'], 'CONTINUED_PUBLIC_CORRECTION')
        for market in public['outlook']['market_assessment']['markets']:
            for horizon in market['horizons']:
                claim = horizon['forward']['lifecycle']['claim']
                record = life.registration(self.root, digest(claim))
                self.assertEqual(record['registered_at'], self.freeze_at)
                self.assertGreater(instant(record['registered_at']), instant(claim['formed_at']))
        self.assert_old_registrations_unchanged()

    def test_unchanged_claim_keeps_its_original_first_clock(self):
        public = deepcopy(self.fixture.public)
        public['market_report'] += '\nGenerated public wording correction, without changing claims.'
        amended = self.amend(public)
        self.assertEqual(amended['lifecycle_frozen_at'], self.freeze_at)
        self.assertEqual(len(list((self.root / 'market-lifecycle/claims').glob('*.json'))),
                         len(self.old_records))
        for path in self.old_records:
            self.assertEqual(read(path)['registered_at'], json.loads(self.old_records[path])['registered_at'])
        self.assert_old_registrations_unchanged()


if __name__ == '__main__':
    unittest.main()
