"""Offline V1 serialized arithmetic compatibility, no real market fixtures."""
from copy import deepcopy
import base64
import hashlib
import json
import unittest
from urllib.parse import urlencode

from test_market_observation import PublicFixtureClient, CUTOFF
import market_observation as m


def identity(value):
    value['evidence_id'] = m.evidence_identity(value)
    value['retrieval_id'] = m.digest({'evidence_id': value['evidence_id'], 'retrievals': value['retrievals']})
    return value


def current_fixture():
    value = m.collect(m.iso(CUTOFF), client=PublicFixtureClient())
    for dataset in value['datasets'].values():
        if dataset['kind'] != 'CANDLES': continue
        for row in dataset['rows']:
            row['volume_btc'] = 10.1
            if dataset['group'] == 'BINANCE':
                row['quote_volume'] = 1000.1
                row['taker_buy_btc'] = 4.3
                row['taker_buy_quote'] = 430.3
    value['windows'] = m.compute_windows(value['datasets'], int(m.timestamp(value['cutoff'])))
    return identity(value)


def old_fixture():
    value = current_fixture()
    value['windows'] = m.compute_windows(value['datasets'], int(m.timestamp(value['cutoff'])), sum_values=m._v1_sequential_sum)
    return identity(value)


class WindowArithmeticTests(unittest.TestCase):
    def test_current_native_sum_and_original_sequential_v1_both_validate_without_mutation(self):
        for value in (current_fixture(), old_fixture()):
            before = json.dumps(value, sort_keys=True)
            self.assertIs(m.validate_evidence(value), value)
            self.assertEqual(json.dumps(value, sort_keys=True), before)

    def test_rehashed_tampered_old_measurement_is_not_a_tolerated_float_error(self):
        value = old_fixture()
        value['windows']['240h']['binance_spot_1h']['measurements']['net_taker_quote'] += 0.0001
        identity(value)
        with self.assertRaisesRegex(m.ObservationError, 'window measurement mismatch'):
            m.validate_evidence(value)

    def test_mixing_current_and_legacy_windows_cannot_claim_one_original_arithmetic(self):
        current = current_fixture(); old = old_fixture()
        if current['windows'] == old['windows']:
            self.skipTest('this Python uses the original sum; mixed rounding requires newer runtime')
        value = deepcopy(old)
        value['windows']['240h'] = current['windows']['240h']
        self.assertNotEqual(value['windows'], old['windows'])
        self.assertNotEqual(value['windows'], current['windows'])
        identity(value)
        with self.assertRaisesRegex(m.ObservationError, 'window measurement mismatch'):
            m.validate_evidence(value)

    def test_legacy_arithmetic_does_not_relax_identity_coverage_or_retrieval(self):
        mutations = (
            ('unit', lambda v: v['datasets']['binance_spot_1h'].__setitem__('quote_unit', 'USD')),
            ('coverage', lambda v: v['datasets']['binance_spot_1h']['coverage'].__setitem__('actual_count', 1)),
            ('request', lambda v: v['retrievals']['binance_spot_1h']['parameters'].__setitem__('symbol', 'ETHUSDT')),
            ('clock', lambda v: v['retrievals']['binance_spot_1h'].__setitem__('retrieved_at', '2099-01-01T00:00:00Z')),
        )
        for label, change in mutations:
            value = old_fixture(); change(value); identity(value)
            with self.subTest(label=label), self.assertRaises(m.ObservationError): m.validate_evidence(value)

    def test_unrecognized_old_runtime_label_cannot_bypass_exact_contract(self):
        value = old_fixture(); value['arithmetic'] = 'OLD_RUNTIME_PLEASE_SKIP'
        identity(value)
        with self.assertRaisesRegex(m.ObservationError, 'fields'): m.validate_evidence(value)

    def locked(self):
        params = {'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 240, 'endTime': CUTOFF * 1000 - 1}
        rows = [[t * 1000, '100', '103', '99', '102', '10.1', (t + 3600) * 1000 - 1,
                 '1000.1', 30, '4.3', '430.3', '0'] for t in range(CUTOFF - 240 * 3600, CUTOFF, 3600)]
        body = json.dumps(rows).encode()
        inputs = {'binance_spot_1h': {'source_id': 'SYNTHETIC_TEST_RESPONSE',
            'url': 'https://data-api.binance.vision/api/v3/klines?' + urlencode(params),
            'retrieved_at': m.iso(CUTOFF + 60), 'raw_hash': 'sha256:' + hashlib.sha256(body).hexdigest(),
            'bytes_base64': base64.b64encode(body).decode()}}
        return m.normalize_locked(m.iso(CUTOFF), inputs)

    def test_new_locked_replay_keeps_exact_current_reconstruction(self):
        value = self.locked(); before = json.dumps(value, sort_keys=True)
        self.assertIs(m.validate_evidence(value), value)
        self.assertEqual(json.dumps(value, sort_keys=True), before)

    def test_new_locked_replay_cannot_disguise_legacy_windows_or_unknown_label(self):
        value = self.locked(); expected = deepcopy(value)
        value['windows'] = m.compute_windows(value['datasets'], int(m.timestamp(value['cutoff'])), sum_values=m._v1_sequential_sum)
        if value['windows'] != expected['windows']:
            with self.assertRaisesRegex(m.ObservationError, 'locked replay differs'): m.validate_evidence(value)
        value = self.locked(); value['normalization']['kind'] = 'OLD_SEQUENTIAL_REPLAY'
        with self.assertRaisesRegex(m.ObservationError, 'normalization kind'): m.validate_evidence(value)

    def test_changed_original_locked_inputs_hash_is_rejected(self):
        value = self.locked()
        value['normalization']['inputs']['binance_spot_1h']['raw_hash'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(m.ObservationError, 'invalid locked raw response'): m.validate_evidence(value)


if __name__ == '__main__': unittest.main()
