"""V2 stays complete and auxiliary through frozen market and report inputs."""
import json
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent),
               str(Path(__import__('os').environ['BTC_COLLECTOR_CONTRACT_PATH']).parent),
               str(Path(__import__('os').environ['BTC_COLLECTOR_CONTRACT_PATH']).parent / 'tests')]
import btc_evidence
from test_evidence_v2 import generated
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
