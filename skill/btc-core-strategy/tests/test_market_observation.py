"""Deterministic measurement/storage tests; no live account or network access."""
import copy
import importlib.util
import json
import sqlite3
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
_spec = importlib.util.spec_from_file_location('market_observation', Path(__file__).resolve().parents[1] / 'scripts' / 'market_observation.py')
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)
CUTOFF = 1788436800  # 2026-09-03T12:00:00Z


class PublicFixtureClient:
    def __init__(self, cutoff=CUTOFF, buy_ratio=.45, missing=None):
        self.cutoff, self.buy_ratio, self.missing = cutoff, buy_ratio, missing
        self.calls = []

    def get(self, endpoint, params):
        self.calls.append((endpoint, params))
        if self.missing and self.missing in endpoint:
            raise TimeoutError('not persisted')
        if 'coinbase' in endpoint:
            start, end = int(m.timestamp(params['start'])), int(m.timestamp(params['end']))
            return [[at, 99, 103, 100, 102, 10] for at in range(start, end + 3600, 3600)][::-1]
        if 'kraken' in endpoint:
            return {'error': [], 'result': {'XXBTZUSD': [[at, '100', '103', '99', '102', '101', '10', 30] for at in range(params['since'], self.cutoff + 3600, 3600)], 'last': self.cutoff}}
        start = params['startTime'] // 1000
        end = params['endTime'] // 1000
        if 'openInterestHist' in endpoint:
            return [{'symbol': 'BTCUSDT', 'timestamp': at * 1000, 'sumOpenInterest': str(1000 + (at - start) / 3600), 'sumOpenInterestValue': str(100000 + (at - start) / 36)} for at in range(start, end + 1, 3600)]
        if 'fundingRate' in endpoint:
            return [{'symbol': 'BTCUSDT', 'fundingTime': at * 1000, 'fundingRate': '.0001'} for at in range(start, end + 1, 4 * 3600)]
        interval = {'1h': 3600, '1d': 86400, '1w': 604800}[params['interval']]
        return [[at * 1000, '100', '103', '99', '102', '10', (at + interval) * 1000 - 1, '1000', 30, str(10 * self.buy_ratio), str(1000 * self.buy_ratio), '0'] for at in range(start, end + 1, interval)]


def evidence(cutoff=CUTOFF, **kwargs):
    with mock.patch.object(m.time, 'time', return_value=cutoff + 60):
        return m.collect(m.iso(cutoff), client=PublicFixtureClient(cutoff, **kwargs))


def review(e, context=None, required=True):
    context = context or m.read_context('/does/not/exist-observation-fixture')
    return {'schema': m.REVIEW_SCHEMA, 'evidence_id': e['evidence_id'], 'observed_hour': e['observed_hour'], 'context_id': context['context_id'], 'model': dict(m.CURRENT_HOURLY_MODEL), 'assessment_summary': 'Observed competing explanations; no participant identity is established.',
            'patterns': [{'kind': kind, 'stage': 'UNRESOLVED', 'confidence': 'LOW', 'support_refs': ['windows.4h.binance_spot_1h'], 'counter_refs': ['windows.24h.binance_futures_1h'], 'interpretation': 'Evidence is mixed and needs independent discrimination.', 'next_evidence': ['Observe the next complete period and compare spot participation.']} for kind in sorted(m.PATTERN_KINDS)],
            'changes_since_hour': ['Price and flow relations changed in the new complete hour.'] if context['baselines'].get('HOUR') else [],
            'changes_since_deep': ['Accumulated spot/derivative relationship needs review.'] if context['baselines'].get('DEEP') else [],
            'deep_review': {'required': required, 'reasons': ['Competing price-flow relations need independent review.'] if required else []}}


def accepted_assessment():
    return {
        'schema': 'BTC_ACCEPTED_MARKET_ASSESSMENT_V1',
        'causal_semantics': {'driver_regime': 'UNRESOLVED', 'driver_components': [], 'selected_hypothesis_id': 'H_UNRESOLVED', 'mechanism': [], 'decision_evidence_status': 'EVIDENCE_LIMITED', 'next_evidence': ['CROSS_VENUE_SPOT_AND_OI_TIMELINE'], 'falsification_conditions': ['OPPOSING_DRIVER_SEQUENCE_VERIFIED']},
        'material_pattern': {'schema': 'BTC_MATERIAL_PATTERN_V1', 'hypotheses': [{'kind': 'ABSORPTION', 'stage': 'FALSIFIED', 'confidence': 'MEDIUM', 'uncertainties': ['CAUSAL_SEQUENCE_UNRESOLVED'], 'next_evidence': ['INDEPENDENT_SPOT_FLOW']}], 'impact': ['CAUSAL_EXPLANATION']},
        'evidence_refs': {name: {'status': 'UNAVAILABLE', 'references': []} for name in ['price_quotes', 'cross_exchange_acceptance', 'spot_flow', 'derivatives_flow', 'oi_timeline', 'funding_basis', 'capital_source', 'macro', 'relative_strength']},
    }


def complete(db_path, kind, binding, proof_identity, completed_at, outcome='SUCCEEDED', **kwargs):
    if outcome == 'SUCCEEDED':
        kwargs.setdefault('accepted_market_assessment', accepted_assessment())
        if kind == 'REPORT':
            kwargs.setdefault('report_fingerprint', 'sha256:' + 'b' * 64)
    return m.record_completion(db_path, kind, binding, proof_identity, completed_at, outcome, **kwargs)


class MeasurementTests(unittest.TestCase):
    def test_frozen_validation_uses_owned_runtime_only_for_exact_compatibility(self):
        from v4 import market_runtime as runtime
        from v4.common import Invalid
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); frozen=root/'market.json'; frozen.write_text('{}')
            owned=root/'python'; owned.write_text('retained runtime')
            mismatch=runtime.market_observation.ObservationError('window measurement mismatch')
            with mock.patch.object(runtime, 'BUNDLED_PYTHON', owned), \
                 mock.patch.object(runtime.market_observation, 'validate_evidence', side_effect=mismatch), \
                 mock.patch.object(runtime.subprocess, 'run', return_value=mock.Mock(returncode=0,stdout='3.12.14\n')), \
                 mock.patch.object(runtime.node_config, 'load', return_value={'frozen_market_python_version':'3.12.14'}):
                self.assertEqual(runtime.validate_file(frozen)['runtime'],str(owned))
                with mock.patch.object(runtime.node_config, 'load', return_value={'frozen_market_python_version':'3.12.0'}):
                    with self.assertRaisesRegex(Invalid,'node-pinned version'):runtime.validate_file(frozen)
                owned.unlink()
                with self.assertRaisesRegex(Invalid,'node-owned'):runtime.validate_file(frozen)
                self.assertEqual(frozen.read_text(),'{}')

    def test_closed_windows_flow_amount_and_ratio_are_distinct(self):
        e = evidence()
        m.validate_evidence(e)
        w = e['windows']['4h']['binance_spot_1h']
        self.assertTrue(w['complete'])
        self.assertEqual(w['measurements']['net_taker_btc'], -4)
        self.assertEqual(w['measurements']['net_taker_btc_ratio'], -.1)
        self.assertEqual(w['measurements']['net_taker_quote'], -400)
        self.assertIsNone(e['windows']['4h']['coinbase_spot_1h']['measurements']['net_taker_quote'])

    def test_three_independent_spot_groups_and_derivatives_same_group(self):
        e = evidence()
        groups = {e['datasets'][k]['group'] for k in ['binance_spot_1h', 'coinbase_spot_1h', 'kraken_spot_1h']}
        self.assertEqual(groups, {'BINANCE', 'COINBASE', 'KRAKEN'})
        self.assertEqual(e['datasets']['binance_oi_1h']['group'], 'BINANCE')
        self.assertEqual(e['datasets']['binance_spot_1h']['quote_unit'], 'USDT')
        self.assertEqual(e['datasets']['coinbase_spot_1h']['quote_unit'], 'USD')

    def test_open_kraken_coinbase_candles_excluded(self):
        e = evidence()
        for name in ['coinbase_spot_1h', 'kraken_spot_1h']:
            self.assertEqual(e['datasets'][name]['coverage']['actual_count'], 240)
            self.assertIn('UNCLOSED_OR_FUTURE', e['datasets'][name]['issues'])
            self.assertTrue(all(m.timestamp(row['end']) <= CUTOFF for row in e['datasets'][name]['rows']))

    def test_oi_quantity_is_not_notional_and_needs_both_boundary_samples(self):
        e = evidence()
        w = e['windows']['4h']['binance_oi_1h']
        self.assertEqual(w['actual_count'], 5)
        self.assertEqual(w['measurements']['quantity_change_btc'], 4)
        self.assertEqual(w['measurements']['notional_change_usdt'], 400)

    def test_funding_end_inclusive_start_exclusive_without_eight_hour_assumption(self):
        e = evidence()
        rows = e['windows']['4h']['binance_funding']['measurements']['rates']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['time'], m.iso(CUTOFF))
        self.assertIsNone(e['windows']['4h']['binance_funding']['complete'])
        self.assertEqual(len(e['windows']['24h']['binance_funding']['measurements']['rates']), 6)

    def test_funding_preserves_milliseconds_and_rejects_future_boundary(self):
        s = m.spec('binance_funding', 'binance_futures', 'FUNDING')
        raw = [{'symbol': 'BTCUSDT', 'fundingTime': CUTOFF * 1000 - 1, 'fundingRate': '.0001'}, {'symbol': 'BTCUSDT', 'fundingTime': CUTOFF * 1000 + 1, 'fundingRate': '.0001'}]
        ds = m.normalize(s, raw, CUTOFF)
        self.assertEqual(len(ds['rows']), 1)
        self.assertTrue(ds['rows'][0]['time'].endswith('.999Z'))
        self.assertIn('OUTSIDE_REQUESTED_WINDOW', ds['issues'])

    def test_oi_subsecond_alignment_not_silently_truncated(self):
        s = m.spec('binance_oi_1h', 'binance_futures', 'OI')
        raw = [{'symbol': 'BTCUSDT', 'timestamp': CUTOFF * 1000 - 3600000 + 1, 'sumOpenInterest': '1', 'sumOpenInterestValue': '100'}]
        ds = m.normalize(s, raw, CUTOFF)
        self.assertFalse(ds['rows'])
        self.assertIn('INVALID_SAMPLE', ds['issues'])

    def test_missing_not_zero_and_unknown_coverage_stays_explicit(self):
        e = evidence(missing='openInterestHist')
        self.assertIsNone(e['windows']['4h']['binance_oi_1h']['measurements'])
        self.assertFalse(e['windows']['4h']['binance_oi_1h']['complete'])
        self.assertIn('binance_oi_1h', e['gaps'])
        m.validate_evidence(e)

    def test_partial_window_not_called_complete(self):
        s = m.spec('binance_spot_1h', 'binance_spot')
        client = PublicFixtureClient()
        raw, _ = m._raw_request(s, CUTOFF, client)
        ds = m.normalize(s, raw[:-1], CUTOFF)
        w = m.compute_windows({'binance_spot_1h': ds}, CUTOFF)['4h']['binance_spot_1h']
        self.assertEqual(w['actual_count'], 3)
        self.assertFalse(w['complete'])
        self.assertEqual(w['measurements']['volume_btc'], 30)

    def test_duplicates_do_not_double_volume_and_conflicts_are_dropped(self):
        s = m.spec('binance_spot_1h', 'binance_spot')
        raw, _ = m._raw_request(s, CUTOFF, PublicFixtureClient())
        ds = m.normalize(s, raw + [raw[-1]], CUTOFF)
        self.assertEqual(len(ds['rows']), 240)
        conflict = copy.deepcopy(raw[-1]); conflict[4] = '101'
        ds = m.normalize(s, raw + [conflict], CUTOFF)
        self.assertEqual(len(ds['rows']), 239)
        self.assertIn('CONFLICTING_DUPLICATE', ds['issues'])

    def test_malformed_nan_impossible_ohlc_and_taker_volume_rejected(self):
        s = m.spec('binance_spot_1h', 'binance_spot')
        raw, _ = m._raw_request(s, CUTOFF, PublicFixtureClient())
        for field, value in [(4, 'NaN'), (2, '1'), (9, '100000')]:
            broken = copy.deepcopy(raw[-1]); broken[field] = value
            ds = m.normalize(s, [broken], CUTOFF)
            self.assertFalse(ds['rows'])

    def test_future_as_of_forbidden(self):
        with self.assertRaises(m.ObservationError):
            m.collect('2099-01-01T00:00:00Z', client=PublicFixtureClient())

    def test_naive_as_of_forbidden(self):
        with self.assertRaises(m.ObservationError):
            m.utc_cutoff('2026-09-01T00:00:00')

    def test_measured_identity_ignores_retrieval_clock(self):
        e = evidence()
        old = e['evidence_id']
        e['retrievals']['binance_spot_1h']['retrieved_at'] = '2026-09-03T15:00:00Z'
        e['retrieval_id'] = m.digest({'evidence_id': old, 'retrievals': e['retrievals']})
        self.assertEqual(m.evidence_identity(e), old)
        m.validate_evidence(e)

    def test_tampered_window_or_units_rejected(self):
        e = evidence()
        e['windows']['4h']['binance_spot_1h']['measurements']['net_taker_quote'] = 0
        e['evidence_id'] = m.evidence_identity(e)
        with self.assertRaises(m.ObservationError):
            m.validate_evidence(e)
        e = evidence(); e['datasets']['binance_oi_1h']['quote_unit'] = 'BTC'
        with self.assertRaises(m.ObservationError):
            m.validate_evidence(e)

    def test_deep_has_exact_90_days_and_52_closed_monday_weeks(self):
        e = m.collect(m.iso(CUTOFF), deep=True, client=PublicFixtureClient())
        m.validate_evidence(e)
        self.assertEqual(len(e['datasets']['binance_spot_1d']['rows']), 90)
        weekly = e['datasets']['binance_spot_1w']['rows']
        self.assertEqual(len(weekly), 52)
        self.assertEqual(m.datetime.fromisoformat(weekly[-1]['time'].replace('Z', '+00:00')).weekday(), 0)
        self.assertLessEqual(m.timestamp(weekly[-1]['end']), CUTOFF)

    def test_fixed_public_endpoint_and_credential_parameter_allowlist(self):
        client = m.PublicClient()
        with self.assertRaises(m.ObservationError):
            client.get('https://example.com/account', {})
        with self.assertRaises(m.ObservationError):
            client.get('https://api.binance.com/api/v3/klines', {'api_key': 'unusable-test-placeholder'})


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'observation.sqlite3'
        self.e = evidence()

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, e=None, required=True):
        e = e or self.e
        return m.record(e, review(e, m.read_context(self.db), required), self.db)

    def test_old_model_is_readable_and_retryable_but_cannot_write_new_review(self):
        old = review(self.e, m.read_context(self.db))
        old['model'] = {'name': 'gpt-6.1-sol', 'reasoning_effort': 'high'}
        with mock.patch.object(m, 'CURRENT_HOURLY_MODEL', old['model']):
            saved = m.record(self.e, old, self.db)
        self.assertEqual(m.load_review_binding(saved['binding'], self.db)['review'], old)

        self.assertEqual(m.record(self.e, old, self.db)['status'], 'UNCHANGED')
        newer = evidence(CUTOFF + 3600)
        candidate = review(newer, m.read_context(self.db))
        candidate['model'] = dict(old['model'])
        with self.assertRaisesRegex(m.ObservationError, 'gpt-5.6-sol/high'):
            m.record(newer, candidate, self.db)
        candidate['model'] = dict(m.CURRENT_HOURLY_MODEL)
        self.assertEqual(m.record(newer, candidate, self.db)['status'], 'RECORDED')
        self.assertEqual(m.load_review_binding(saved['binding'], self.db)['review'], old)

    def test_previous_gpt6_is_readable_but_not_a_new_writer(self):
        candidate=review(self.e,m.read_context(self.db))
        candidate['model']={'name':'gpt-6-sol','reasoning_effort':'high'}
        m.validate_review(self.e,candidate,m.read_context(self.db))
        with self.assertRaisesRegex(m.ObservationError,'gpt-5.6-sol/high'):
            m.record(self.e,candidate,self.db)

    def deep(self, binding, at=CUTOFF + 600, outcome='SUCCEEDED'):
        return complete(self.db, 'DEEP', binding, 'a' * 64, m.iso(at), outcome)

    def test_read_missing_context_does_not_create_database(self):
        c = m.read_context(self.db)
        self.assertFalse(self.db.exists())
        self.assertEqual(c['baselines'], {})

    def test_initial_record_advances_hour_only_and_binding_roundtrips(self):
        result = self.record()
        c = m.read_context(self.db)
        self.assertEqual(set(c['baselines']), {'HOUR'})
        loaded = m.load_review_binding(result['binding'], self.db)
        self.assertTrue(loaded['deep_eligible'])
        self.assertEqual(loaded['evidence']['evidence_id'], self.e['evidence_id'])

    def test_identical_input_rerun_is_idempotent(self):
        first_review = review(self.e)
        first = m.record(self.e, first_review, self.db)
        second = m.record(self.e, first_review, self.db)
        self.assertEqual(second['status'], 'UNCHANGED')
        self.assertEqual(second['binding'], first['binding'])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM review').fetchone()[0], 1)

    def test_retrieval_only_rerun_does_not_create_revision(self):
        first = self.record()
        e2 = copy.deepcopy(self.e)
        e2['retrievals']['binance_spot_1h']['retrieved_at'] = m.iso(CUTOFF + 800)
        e2['retrieval_id'] = m.digest({'evidence_id': e2['evidence_id'], 'retrievals': e2['retrievals']})
        second = m.record(e2, review(e2, m.read_context(self.db)), self.db)
        self.assertEqual(first['binding'], second['binding'])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM retrieval').fetchone()[0], 2)

    def test_same_hour_corrected_data_creates_immutable_revision(self):
        first = self.record()
        second = self.record(evidence(buy_ratio=.55))
        self.assertNotEqual(first['binding'], second['binding'])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT revision FROM evidence ORDER BY revision')], [1, 2])

    def test_stale_context_and_older_hour_rejected(self):
        stale = m.read_context(self.db)
        self.record()
        e2 = evidence(CUTOFF + 3600)
        with self.assertRaises(m.ObservationError):
            m.record(e2, review(e2, stale), self.db)
        older = evidence(CUTOFF - 3600)
        with self.assertRaises(m.ObservationError):
            m.record(older, review(older, m.read_context(self.db)), self.db)

    def test_failed_deep_and_failed_delivery_do_not_advance_success_baselines(self):
        first = self.record()
        self.deep(first['binding'], outcome='FAILED')
        self.assertEqual(set(m.read_context(self.db)['baselines']), {'HOUR'})
        self.deep(first['binding'])
        complete(self.db, 'REPORT', first['binding'], 'b' * 64, m.iso(CUTOFF + 700), 'FAILED')
        self.assertEqual(set(m.read_context(self.db)['baselines']), {'HOUR', 'DEEP'})

    def test_report_requires_successful_deep(self):
        first = self.record()
        with self.assertRaises(m.ObservationError):
            complete(self.db, 'REPORT', first['binding'], 'b' * 64, m.iso(CUTOFF + 700))
        self.deep(first['binding'])
        complete(self.db, 'REPORT', first['binding'], 'b' * 64, m.iso(CUTOFF + 700))
        self.assertEqual(set(m.read_context(self.db)['baselines']), {'HOUR', 'DEEP', 'REPORT'})

    def test_three_baselines_diverge_and_restart_restores_accumulation(self):
        first = self.record(); self.deep(first['binding'])
        complete(self.db, 'REPORT', first['binding'], 'b' * 64, m.iso(CUTOFF + 700))
        second = self.record(evidence(CUTOFF + 3600), required=False)
        context = m.read_context(self.db)
        self.assertEqual(context['baselines']['HOUR']['review_id'], second['binding']['review_id'])
        self.assertEqual(context['baselines']['DEEP']['review_id'], first['binding']['review_id'])
        self.assertEqual(context['baselines']['REPORT']['review_id'], first['binding']['review_id'])
        self.assertEqual(len(context['since_deep']), 2)

    def test_24_hours_requires_new_valid_evidence_not_just_clock(self):
        first = self.record(); self.deep(first['binding'], at=CUTOFF)
        later = evidence(CUTOFF + 24 * 3600)
        result = m.validate_review(later, review(later, m.read_context(self.db), False), m.read_context(self.db))
        self.assertTrue(result['periodic_review_due'])
        self.assertTrue(result['deep_eligible'])
        self.assertFalse(m._has_new_rows(self.e, self.e))

    def test_first_pack_cannot_claim_reversal_or_relative_changes(self):
        r = review(self.e); r['patterns'][0]['stage'] = 'REVERSED'
        with self.assertRaises(m.ObservationError):
            m.validate_review(self.e, r, m.read_context(self.db))
        r = review(self.e); r['changes_since_deep'] = ['Changed']
        with self.assertRaises(m.ObservationError):
            m.validate_review(self.e, r, m.read_context(self.db))

    def test_all_five_patterns_and_valid_reference_binding_required(self):
        r = review(self.e); r['patterns'].pop()
        with self.assertRaises(m.ObservationError):
            m.validate_review(self.e, r, m.read_context(self.db))
        r = review(self.e); r['patterns'][0]['support_refs'] = ['model imagination']
        with self.assertRaises(m.ObservationError):
            m.validate_review(self.e, r, m.read_context(self.db))

    def test_account_payload_and_credentials_not_persisted(self):
        r = review(self.e); r['account_balances'] = {'anything': 1}
        with self.assertRaises(m.ObservationError):
            m.record(self.e, r, self.db)
        r = review(self.e); r['assessment_summary'] = 'Authorization: prohibited-placeholder'
        with self.assertRaises(m.ObservationError):
            m.record(self.e, r, self.db)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM review').fetchone()[0], 0)

    def test_no_raw_chain_of_thought_extra_field(self):
        r = review(self.e); r['reasoning_trace'] = 'not accepted'
        with self.assertRaises(m.ObservationError):
            m.validate_review(self.e, r, m.read_context(self.db))

    def test_no_data_cannot_claim_novelty_but_allows_conservative_deep(self):
        e = m.collect(m.iso(CUTOFF), client=PublicFixtureClient(missing='https://'))
        result = m.validate_review(e, review(e), m.read_context(self.db))
        self.assertFalse(result['has_new_valid_evidence'])
        self.assertTrue(result['deep_eligible'])
        self.assertTrue(result['initialization_review_due'])

    def test_forged_binding_and_completion_proof_rejected(self):
        first = self.record(); bad = dict(first['binding']); bad['context_id'] = 'sha256:' + 'f' * 64
        with self.assertRaises(m.ObservationError):
            m.load_review_binding(bad, self.db)
        with self.assertRaises(m.ObservationError):
            complete(self.db, 'DEEP', first['binding'], 'success=true', m.iso(CUTOFF + 600))

    def test_old_binding_remains_readable_after_baselines_advance(self):
        first = self.record(); self.deep(first['binding'])
        self.record(evidence(CUTOFF + 3600))
        self.assertTrue(m.load_review_binding(first['binding'], self.db)['valid'])

    def test_late_completion_cannot_replace_newer_deep(self):
        first = self.record(); self.deep(first['binding'])
        second = self.record(evidence(CUTOFF + 3600))
        self.deep(second['binding'], CUTOFF + 4200)
        replay = self.deep(first['binding'], CUTOFF + 4300)
        self.assertEqual(replay['status'], 'UNCHANGED')
        self.assertEqual(m.read_context(self.db)['baselines']['DEEP']['review_id'], second['binding']['review_id'])

    def test_success_replay_with_new_proof_does_not_reset_24_hour_clock(self):
        first = self.record(); self.deep(first['binding'])
        before = m.read_context(self.db)
        replay = complete(self.db, 'DEEP', first['binding'], 'c' * 64, m.iso(CUTOFF + 3600))
        after = m.read_context(self.db)
        self.assertEqual(replay['status'], 'UNCHANGED')
        self.assertEqual(before['context_id'], after['context_id'])
        self.assertEqual(after['baselines']['DEEP']['completed_at'], m.iso(CUTOFF + 600))

    def test_gap_only_cannot_support_resolved_pattern(self):
        e = m.collect(m.iso(CUTOFF), client=PublicFixtureClient(missing='https://'))
        r = review(e); r['patterns'][0]['stage'] = 'FORMING'
        with self.assertRaises(m.ObservationError):
            m.validate_review(e, r, m.read_context(self.db))

    def test_same_hour_older_revision_cannot_finish_over_newer_deep(self):
        older = self.record()
        newer = self.record(evidence(buy_ratio=.6))
        self.deep(newer['binding'])
        with self.assertRaises(m.ObservationError):
            self.deep(older['binding'], CUTOFF + 700)

    def test_required_candidate_can_retry_without_new_market_rows(self):
        first = self.record(); self.deep(first['binding'], at=CUTOFF)
        context = m.read_context(self.db)
        r = review(self.e, context, True)
        valid = m.validate_review(self.e, r, context)
        self.assertFalse(valid['has_new_valid_evidence'])
        self.assertTrue(valid['deep_eligible'])

    def test_same_measurement_repeat_after_deep_not_periodic_new_evidence(self):
        first = self.record(); self.deep(first['binding'], at=CUTOFF)
        context = m.read_context(self.db)
        e = copy.deepcopy(self.e)
        for retrieval in e['retrievals'].values():
            retrieval['retrieved_at'] = m.iso(CUTOFF + 25 * 3600)
        e['retrieval_id'] = m.digest({'evidence_id': e['evidence_id'], 'retrievals': e['retrievals']})
        with mock.patch.object(m.time, 'time', return_value=CUTOFF + 26 * 3600):
            valid = m.validate_review(e, review(e, context, False), context)
        self.assertFalse(valid['periodic_review_due'])
        self.assertFalse(valid['deep_eligible'])

    def test_funding_exact_boundary_is_requested(self):
        req = m._request_plan(m.spec('binance_funding', 'binance_futures', 'FUNDING'), CUTOFF)
        self.assertEqual(req['parameters']['endTime'], CUTOFF * 1000)

    def test_retrieval_arbitrary_payload_is_rejected(self):
        e = copy.deepcopy(self.e)
        e['retrievals']['binance_spot_1h']['parameters']['private_data'] = 'forbidden'
        e['retrieval_id'] = m.digest({'evidence_id': e['evidence_id'], 'retrievals': e['retrievals']})
        with self.assertRaises(m.ObservationError):
            m.validate_evidence(e)

    def test_deep_and_report_context_show_accepted_falsification_not_entry_guess(self):
        r = review(self.e)
        absorption = next(p for p in r['patterns'] if p['kind'] == 'ABSORPTION')
        absorption['stage'] = 'FORMING'
        first = m.record(self.e, r, self.db)
        self.deep(first['binding'])
        complete(self.db, 'REPORT', first['binding'], 'b' * 64, m.iso(CUTOFF + 700))
        context = m.read_context(self.db)
        for key in ['successful_deep', 'reported_conclusion']:
            baseline = context[key]
            self.assertNotIn('review', baseline)
            self.assertEqual(baseline['accepted_market_assessment']['material_pattern']['hypotheses'][0]['stage'], 'FALSIFIED')
            original = next(p for p in baseline['original_hourly_candidate']['patterns'] if p['kind'] == 'ABSORPTION')
            self.assertEqual(original['stage'], 'FORMING')
        self.assertEqual(context['reported_conclusion']['report_fingerprint'], 'sha256:' + 'b' * 64)

    def test_successful_completion_requires_accepted_market_only_payload(self):
        first = self.record()
        with self.assertRaises(m.ObservationError):
            m.record_completion(self.db, 'DEEP', first['binding'], 'a' * 64, m.iso(CUTOFF + 600))
        bad = accepted_assessment(); bad['portfolio'] = {'cash': 1}
        with self.assertRaises(m.ObservationError):
            complete(self.db, 'DEEP', first['binding'], 'a' * 64, m.iso(CUTOFF + 600), accepted_market_assessment=bad)

    def test_report_cannot_relabel_deep_accepted_market_assessment(self):
        first = self.record(); self.deep(first['binding'])
        bad = accepted_assessment(); bad['material_pattern']['hypotheses'][0]['stage'] = 'FORMING'
        with self.assertRaises(m.ObservationError):
            complete(self.db, 'REPORT', first['binding'], 'b' * 64, m.iso(CUTOFF + 700), accepted_market_assessment=bad)

    def test_context_truncation_is_explicit(self):
        self.record(); self.record(evidence(CUTOFF + 3600))
        c = m.read_context(self.db, 1)
        self.assertEqual(c['since_deep_total'], 2)
        self.assertTrue(c['since_deep_truncated'])

    def test_retention_pins_current_deep_report_and_pending(self):
        # Time-shift under a mocked present so no live database/time is touched.
        old_cutoff = CUTOFF - 100 * 86400
        pending = self.record(evidence(old_cutoff))
        ordinary = self.record(evidence(old_cutoff + 3600), required=False)
        pinned = self.record(evidence(old_cutoff + 7200))
        self.deep(pinned['binding'], old_cutoff + 7800)
        complete(self.db, 'REPORT', pinned['binding'], 'b' * 64, m.iso(old_cutoff + 7900))
        self.record(self.e, required=False)
        result = m.prune(self.db, m.iso(CUTOFF))
        self.assertEqual(result['deleted_reviews'], 2)
        with self.assertRaises(m.ObservationError):
            m.load_review_binding(pending['binding'], self.db)
        m.load_review_binding(pinned['binding'], self.db)
        with self.assertRaises(m.ObservationError):
            m.load_review_binding(ordinary['binding'], self.db)


if __name__ == '__main__':
    unittest.main()
