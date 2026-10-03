"""V2 stays complete and auxiliary through frozen market and report inputs."""
import json
import math
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent),
               str(Path(__import__('os').environ['BTC_COLLECTOR_CONTRACT_PATH']).parent),
               str(Path(__import__('os').environ['BTC_COLLECTOR_CONTRACT_PATH']).parent / 'tests')]
import btc_evidence
from test_evidence_v2 import generated, rehash
from collector_replay_fixture import END_MS
import seller_exhaustion as sec
from market_history import DAY
import test_multi_asset_pipeline as pipeline_fixture
from v4 import evidence, reporting, storage
from v4.common import Invalid, read


class V2PipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = pipeline_fixture.MultiAssetPipelineTests('test_three_assets_reach_market_portfolio_and_report_material')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        rows = []
        for asset in ('BTC', 'QQQ', 'VOO'):
            bundle, text = generated(asset)
            rows.append(btc_evidence.auxiliary_observation(bundle, text))
        self.fixture.export.write_text(json.dumps(rows))

    def seller_input(self, missing_recent=False):
        bundle, text = generated('BTC')
        cutoff = END_MS + 10000
        last_day = cutoff // DAY * DAY - DAY
        rows = []
        for i in range(70):
            day = last_day - (69-i)*DAY
            inputs = {}
            for kind, (endpoint, _, unit) in sec.INPUTS.items():
                value = {'price': 50000*math.exp(.02*math.sin(i/3)),
                         'profit': 12000000+i*100, 'supply': 20000000}[kind]
                if missing_recent and kind == 'price' and i >= 65:
                    continue
                inputs[kind] = {'value': value, 'source_as_of_ms': day,
                                'retrieved_at_ms': cutoff,
                                'provenance': {'endpoint_url': sec.BASE+endpoint,
                                               'access_scope': 'public', 'unit': unit}}
            rows.append({'period_start_ms': day, 'period_end_ms': day+DAY,
                         'observed_ms': day+DAY-1, 'inputs': inputs})
        item = sec.measurements(None, cutoff)[sec.NAME]
        item.update(rows=sec.derive(rows), input_issues={}, status='ok')
        bundle['measurements'][sec.NAME] = item
        rehash(bundle)
        existing = json.loads(self.fixture.export.read_text())
        existing[0] = btc_evidence.auxiliary_observation(bundle, text)
        self.fixture.export.write_text(json.dumps(existing))

    def test_seller_components_reach_frozen_market_portfolio_and_report(self):
        self.seller_input()
        f = self.fixture
        before = storage.current(f.base.store)['decision_id']
        prepared, _, _ = f.f.observe(patterns=[])
        collected = read(prepared['context'])['collector_observations']
        ds = collected['assets']['BTC']['datasets'][sec.NAME]
        run = read(prepared['run'])
        package = read(Path(run['evidence']) / 'evidence.json')
        raw = evidence.resolve(package, run['evidence'], ds['raw_ref'])
        self.assertEqual(len(raw['rows']), 70)
        self.assertEqual(set(raw['rows'][-1]['inputs']), set(sec.INPUTS))
        self.assertGreater(raw['rows'][-1]['value'], 0)
        self.assertEqual(raw['method'], sec.METHOD)
        self.assertEqual(raw['official_equivalence'], 'unverified')
        self.assertTrue(raw['publication_time_unknown'])
        coverage = ds['temporal_coverage']
        from datetime import datetime, timezone
        last = raw['rows'][-1]
        self.assertEqual(coverage['data_date_utc'], datetime.fromtimestamp(last['period_start_ms']/1000, timezone.utc).date().isoformat())
        self.assertEqual(coverage['period_end_exclusive_utc'], datetime.fromtimestamp(last['period_end_ms']/1000, timezone.utc).isoformat())
        self.assertTrue(coverage['publication_time_unknown'])
        with self.assertRaisesRegex(Invalid, 'untrusted lead'):
            evidence.resolve(package, run['evidence'], ds['raw_ref'], verified=True)
        self.assertIn(sec.NAME, json.dumps(read(f.adapter.d / 'portfolio-context.json')))
        report = reporting.context(f.base.store)['collector_observations']
        self.assertEqual(report['snapshot_id'], collected['snapshot_id'])
        self.assertEqual(storage.current(f.base.store)['decision_id'], before)
        for asset in ('QQQ', 'VOO'):
            self.assertNotIn(sec.NAME, collected['assets'][asset]['datasets'])

    def test_seller_delayed_inputs_keep_old_date_and_gap_in_analysis(self):
        self.seller_input(missing_recent=True)
        prepared = self.fixture.adapter.prep()
        collected = read(prepared['context'])['collector_observations']
        ds = collected['assets']['BTC']['datasets'][sec.NAME]
        self.assertEqual(ds['status'], 'stale')
        self.assertLess(ds['as_of_ms'], END_MS-4*DAY)
        run = read(prepared['run'])
        package = read(Path(run['evidence']) / 'evidence.json')
        raw = evidence.resolve(package, run['evidence'], ds['raw_ref'])
        self.assertIsNone(raw['rows'][-1]['value'])
        self.assertIsNotNone(raw['rows'][-1]['supply_in_profit'])
        self.assertIn('source_partial', raw['quality']['reasons'])
        self.assertFalse(raw['analysis_eligible'])
        self.assertEqual(ds['temporal_coverage']['observed_at_utc'],
                         __import__('datetime').datetime.fromtimestamp(raw['values']['as_of_ms']/1000, __import__('datetime').timezone.utc).isoformat())

    def test_full_v2_domains_reach_market_portfolio_and_report_without_decision_change(self):
        f = self.fixture
        before = storage.current(f.base.store)['decision_id']
        prepared, _, _ = f.f.observe(patterns=[])
        collected = read(prepared['context'])['collector_observations']
        for asset in ('BTC', 'QQQ', 'VOO'):
            data = collected['assets'][asset]
            self.assertEqual(data['status'], 'AUXILIARY')
            self.assertEqual(data['datasets']['derivative_cvd_long']['windows']['14d']['net_quote'], 67200)
            self.assertFalse(data['datasets']['funding_long']['analysis_eligible'])
            if asset != 'BTC':
                self.assertTrue({'stock_1h','stock_quote','derivative_oi','funding','dividend_adjustments','basis','macro'} <= data['datasets'].keys())
                self.assertEqual(data['datasets']['stock_1h']['adjustment_mode'], 'UNKNOWN')
                self.assertFalse(data['datasets']['stock_1h']['analysis_eligible'])
        self.assertIn('collector_observations', json.dumps(read(f.adapter.d / 'portfolio-context.json')))
        self.assertEqual(reporting.context(f.base.store)['collector_observations']['snapshot_id'], collected['snapshot_id'])
        self.assertEqual(storage.current(f.base.store)['decision_id'], before)

    def test_v2_source_rows_freeze_with_proxy_unknowns_and_no_verified_authority(self):
        f = self.fixture
        prepared = f.adapter.prep()
        run = read(prepared['run'])
        bundle = read(Path(run['evidence']) / 'evidence.json')
        collected = read(prepared['context'])['collector_observations']
        for asset in ('BTC', 'QQQ', 'VOO'):
            ref = collected['assets'][asset]['datasets']['derivative_cvd_long']['raw_ref']
            rows = evidence.resolve(bundle, run['evidence'], ref)
            self.assertEqual(len(rows['rows']), 336)
            self.assertEqual(rows['relation'], 'proxy' if asset == 'VOO' else 'direct')
            self.assertTrue(rows['rows'][0]['provenance']['endpoint_url'].startswith('https://'))
            with self.assertRaisesRegex(Invalid, 'untrusted lead'):
                evidence.resolve(bundle, run['evidence'], ref, verified=True)
        self.assertFalse(bundle['verified_facts'])
        self.assertNotIn('mechanical_observation', json.dumps(collected))
        self.assertNotIn('full_report_text', json.dumps(collected))


if __name__ == '__main__':
    unittest.main()
