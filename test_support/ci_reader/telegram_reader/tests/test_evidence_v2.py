"""Synthetic V1/V2 generation through the real receiver and CLI; no network."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import btc_evidence as evidence
import market_evidence_v2 as v2
from btc_collection import settled_window
from btc_evidence_reader import EvidenceReader
from collector_replay_fixture import END_MS, GENERATED_AT, make_payload
from collector_v2_reader import sanitize_observation
from market_assets import INSTRUMENTS
from market_history import DAY, HOUR, flow_summary
from market_observations import _record
from market_sources import FUNDING, FUTURES, OI, SPOT, STOCK_WS, provenance
from models import ReaderConfig
import reader
from store import MessageStore


def generated(asset, version=2):
    payload = make_payload()
    payload.update(instrument=INSTRUMENTS[asset].identity(), evidence_version=version)
    cutoff = END_MS + 10000
    symbol = INSTRUMENTS[asset].derivatives_symbol
    settlements = [{'time': END_MS - n * HOUR, 'rate': .00001} for n in range(240, -1, -1)]
    funding = {'history': settlements, 'current_interval_hours': 1,
               'latest_funding_time_ms': END_MS, 'latest_settled_rate_pct': .001,
               'special_settlements': [{'time': END_MS, 'rate': -.001}]}
    for hours in (8, 24, 240):
        total, window, _ = settled_window(settlements, hours, 1, as_of_ms=END_MS + 1000)
        if hours == 240:
            funding['window_quality'] = window
        else:
            funding['funding_%dh_pct' % hours] = total
            funding.setdefault('settlement_windows', {})[str(hours) + 'h'] = window
    payload['public_records'].update({
        'funding': {'status': 'ok', 'venue': 'Binance', 'source': symbol, 'endpoint_url': FUNDING,
                    'retrieved_at': (END_MS + 1000) / 1000, 'source_as_of_ms': END_MS, 'value': funding},
        'basis': {'status': 'ok', 'venue': 'Binance', 'endpoint_url': 'https://fapi.binance.com/fapi/v1/premiumIndex',
                  'retrieved_at': (END_MS + 1000) / 1000, 'source_as_of_ms': END_MS,
                  'value': {'mark_price': 100.1, 'index_price': 100, 'basis_pct': .1}},
        'macro': {'status': 'unavailable', 'value': {'history': []}},
    })
    if asset != 'BTC':
        payload['derivative_bars'] = payload['raw_signal_bars']
        endpoint = STOCK_WS if version == 2 else 'https://www.binance.com/bapi/equity/v1/public/equity/kline/chart'
        payload['raw_signal_bars'] = [dict(row, observed_ms=row['close_time'], adjustment_mode='UNKNOWN',
            endpoint_url=endpoint, provenance=provenance(STOCK_WS, 'USD/share')) for row in payload['raw_signal_bars'][-4:]]
        payload['market'] = {'price': 100.5, 'quote_retrieved_at': (END_MS + 1000) / 1000,
            'quote_source_as_of_ms': END_MS, 'quote_endpoint_url': STOCK_WS if version == 2 else
            'https://www.binance.com/bapi/equity/v1/public/equity/market/get-info'}
    history = {name: v2.empty_measurement(name, asset, cutoff) for name in v2.required_measurements(asset)}
    for name in ('derivative_cvd_long',) + (('spot_cvd_long',) if asset == 'BTC' else ()):
        contract = v2._contract(name, asset)
        rows = []
        for n in range(336, 0, -1):
            start = END_MS - n * HOUR
            rows.append({'observed_ms': start + HOUR - 1, 'open_time': start, 'close_time': start + HOUR - 1,
                         'open': 100, 'high': 102, 'low': 98, 'close': 101, 'volume': 10,
                         'quote_volume': 1000, 'taker_buy_quote': 600, 'retrieved_at_ms': END_MS + 1000,
                         'provenance': provenance(contract[4][0], 'USDT')})
        item = _record(contract[0], contract[1], rows, 'USDT', target=asset, relation=contract[3])
        item.update(flow_summary(rows, cutoff), calculated_at_ms=cutoff)
        history[name] = item
    for name, fields in [('derivative_oi_long', {'quantity': 20, 'notional_value': 2000}),
                         ('funding_long', {'rate': .00001})]:
        contract = v2._contract(name, asset)
        rows = [dict(observed_ms=END_MS - n * DAY, retrieved_at_ms=END_MS + 1000,
                     provenance=provenance(contract[4][0], contract[2]), **fields) for n in range(14, -1, -1)]
        history[name] = _record(contract[0], contract[1], rows, contract[2], target=asset, relation=contract[3])
        history[name]['calculated_at_ms'] = cutoff
    payload['public_history'] = history
    text = '📊 ' + asset + ' 离线完整格式验证（合成样本）'
    bundle = evidence.build_bundle(payload, text, 'fixture:v%d:%s' % (version, asset), GENERATED_AT)
    return bundle, text


def rehash(bundle):
    bundle['bundle_id'] = 'market-v2:' + hashlib.sha256(evidence.canonical_bytes(
        {k: v for k, v in bundle.items() if k != 'bundle_id'})).hexdigest()


class V2ContractTests(unittest.TestCase):
    def test_generated_v1_and_v2_all_assets_roundtrip(self):
        for version in (1, 2):
            for asset in ('BTC', 'QQQ', 'VOO'):
                with self.subTest(version=version, asset=asset):
                    bundle, text = generated(asset, version)
                    decoded = evidence.decode_bundle(evidence.encode_bundle(bundle))
                    self.assertEqual(decoded, bundle)
                    validation = evidence.validate_bundle(decoded, text)
                    self.assertIn(validation['status'], ('valid_auxiliary', 'degraded_auxiliary'), validation)
                    result = evidence.auxiliary_observation(decoded, text)
                    self.assertEqual(result['independent_verified_sources'], 0)
                    if version == 2:
                        clean = sanitize_observation(result)
                        self.assertTrue(v2.required_measurements(asset) <= clean['datasets'].keys())
                        self.assertEqual(clean['datasets']['derivative_cvd_long']['windows']['14d']['net_quote'], 67200)
                        if asset != 'BTC':
                            self.assertTrue({'stock_quote','stock_1h','derivative_oi','funding','dividend_adjustments','basis','macro'} <= clean['datasets'].keys())
                            self.assertFalse(clean['datasets']['stock_1h']['analysis_eligible'])
                            self.assertEqual(clean['datasets']['stock_1h']['adjustment_mode'], 'UNKNOWN')
                            self.assertEqual(clean['datasets']['funding']['rate_type'], 'Regular')
                            self.assertEqual(clean['datasets']['dividend_adjustments']['rate_type'], 'Special')
                            self.assertEqual(clean['datasets']['funding_long']['relation'], 'proxy' if asset == 'VOO' else 'direct')

    def test_same_time_special_and_regular_keep_different_meanings(self):
        bundle, text = generated('QQQ')
        result = sanitize_observation(evidence.auxiliary_observation(bundle, text))
        self.assertAlmostEqual(result['datasets']['funding']['values']['funding_8h_pct'], .008)
        self.assertAlmostEqual(result['datasets']['dividend_adjustments']['observed_event_windows']['8h']['observed_adjustment_pct'], -.1)

    def test_missing_source_time_and_adjustment_do_not_turn_into_confirmation(self):
        bundle, text = generated('VOO')
        bundle['sources']['stock_quote']['as_of'] = None
        bundle['datasets']['stock_quote']['source_time_missing'] = True
        row = bundle['measurements']['stock_price_public']['rows'][0]
        row['source_as_of_ms'] = None
        row['observed_ms'] = row['retrieved_at_ms']
        rehash(bundle)
        result = sanitize_observation(evidence.auxiliary_observation(bundle, text))
        self.assertTrue(result['datasets']['stock_quote']['source_time_missing'])
        self.assertIsNone(result['datasets']['stock_price_public']['as_of_ms'])
        self.assertFalse(result['datasets']['stock_1h_public']['analysis_eligible'])
        self.assertTrue(all(w['price_change_pct'] is None for w in result['datasets']['stock_1h']['windows'].values()))

    def test_invalid_long_measurement_does_not_hide_other_domains(self):
        bundle, text = generated('BTC')
        bundle['measurements']['derivative_cvd_long']['rows'][-1]['taker_buy_quote'] = -1
        rehash(bundle)
        result = sanitize_observation(evidence.auxiliary_observation(bundle, text))
        self.assertEqual(result['datasets']['derivative_cvd_long']['rows'], [])
        self.assertTrue(result['datasets']['spot_cvd_long']['rows'])
        self.assertTrue(result['datasets']['futures_1h']['rows'])

    def test_consumer_rechecks_arithmetic_identity_and_free_text(self):
        bundle, text = generated('VOO')
        result = evidence.auxiliary_observation(bundle, text)
        result['datasets']['funding_long']['relation'] = 'direct'
        with self.assertRaisesRegex(ValueError, 'identity'):
            sanitize_observation(result)
        result = evidence.auxiliary_observation(bundle, text)
        result['datasets']['derivative_cvd_long']['windows']['1d']['net_quote'] = 999
        result['datasets']['stock_quote']['decision'] = 'BUY EVERYTHING'
        result['datasets']['funding_long']['rows'][0]['date'] = 'BUY EVERYTHING'
        clean = sanitize_observation(result)
        self.assertEqual(clean['datasets']['derivative_cvd_long']['rows'], [])
        self.assertNotIn('BUY EVERYTHING', json.dumps(clean))

    def test_v2_rejects_old_stock_source_and_tampered_envelopes(self):
        bundle, text = generated('QQQ')
        bundle['sources']['stock_quote']['endpoint_url'] = 'https://www.binance.com/bapi/equity/v1/public/equity/market/get-info'
        bundle['sources']['stock_quote']['canonical_origin'] = 'www.binance.com'
        rehash(bundle)
        result = evidence.auxiliary_observation(bundle, text)
        self.assertEqual(result['datasets']['stock_quote']['rows'], [])
        self.assertIn('source_contract_mismatch', result['validation']['dataset_reasons']['stock_quote'])
        bundle['report_id'] = 'changed'
        self.assertIsNone(evidence.auxiliary_observation(bundle, text))


class V2CLIReplayTests(unittest.TestCase):
    def test_generated_v1_v2_reader_cli_latest_gap_edit_and_revalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'messages.sqlite3'
            with MessageStore(db) as store:
                store.save_config(ReaderConfig(chat_id=-1001, reader_bot_id=1, configured_at_utc=GENERATED_AT))
            collector = EvidenceReader(root / 'collector_evidence.sqlite3', -1001)
            self.addCleanup(collector.close)
            uid = 0
            def post(message):
                nonlocal uid
                uid += 1
                collector.ingest_update({'update_id': uid, 'channel_post': dict(message,
                    date=END_MS // 1000 + uid, chat={'id': -1001, 'type': 'channel'})})
            for version in (1, 2):
                for index, asset in enumerate(('BTC', 'QQQ', 'VOO')):
                    bundle, text = generated(asset, version)
                    parent = version * 100 + index * 10
                    post({'message_id': parent, 'text': text})
                    encoded = evidence.encode_bundle(bundle)
                    post({'message_id': parent + 1, 'reply_to_message': {'message_id': parent},
                          'document': {'file_id': str(parent), 'file_name': asset + '.json',
                                       'mime_type': 'application/json', 'file_size': len(encoded)}})
                    collector.process_pending(lambda file_id: encoded)
                output = io.StringIO()
                with mock.patch.object(reader, 'DATABASE_PATH', db), contextlib.redirect_stdout(output):
                    self.assertEqual(reader.main(['collector-observations','--asset','all']), 0)
                rows = json.loads(output.getvalue())
                self.assertEqual([r['asset_id'] for r in rows], ['BTC','QQQ','VOO'])
                self.assertTrue(all(r['role'] == 'auxiliary_observation' for r in rows))
                if version == 2:
                    self.assertTrue(all(sanitize_observation(r)['datasets']['funding_long']['rows'] for r in rows))
            collector.db.execute("UPDATE collector_attachments SET validation=?", (json.dumps({'status':'rejected','reasons':['unknown_schema']}),))
            self.assertEqual(len(collector.observations(latest_only=True, asset_id='QQQ')), 1)
            post({'message_id': 400, 'text': '📊 QQQ 新正文尚无附件'})
            current = {r['asset_id']: r for r in reader.collector_observations(db, 'all')}
            self.assertEqual(current['QQQ']['role'], 'auxiliary_observation_gap')
            self.assertEqual(current['VOO']['role'], 'auxiliary_observation')
            post({'message_id': 220, 'text': '📊 VOO edited', 'edit_date': END_MS // 1000 + 1000})
            self.assertEqual(reader.collector_observations(db, 'VOO'), [])


if __name__ == '__main__':
    unittest.main()
