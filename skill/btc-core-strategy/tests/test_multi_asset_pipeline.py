"""Synthetic collector samples flow through frozen v4 inputs, never as verified facts."""
import json
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent),
               str(Path(__import__('os').environ['BTC_COLLECTOR_CONTRACT_PATH']).parent)]
import btc_evidence
import collector_observation
import test_market_state as fixture
from v4 import collector, evidence, research, run_flow, reporting, storage
from v4.common import Invalid, create, digest, read

from test_evidence_v2 import generated


def real_export():
    # Historical function name retained for callers; every input is synthetic.
    rows = []
    for asset in ('BTC', 'QQQ', 'VOO'):
        raw, text = generated(asset, version=1)
        if asset == 'QQQ':
            funding = raw['datasets']['funding']
            for sample in funding['rows']:
                sample['rate'] = 0
            for key in ('funding_8h_pct', 'funding_24h_pct', 'latest_settled_rate_pct'):
                funding['values'][key] = 0
            raw['bundle_id'] = 'market:' + hashlib.sha256(btc_evidence.canonical_bytes(
                {key: value for key, value in raw.items() if key != 'bundle_id'})).hexdigest()
        row = btc_evidence.auxiliary_observation(raw, text)
        row.update(asset_id=asset, requires_independent_verification=True, requires_current_source_check=True)
        rows.append(row)
    return rows


class MultiAssetPipelineTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.MarketStateTests('test_market_material_has_no_private_inputs_or_mixed_historical_report')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.adapter = self.f.f
        self.base = self.adapter.f
        self.export = self.base.base / 'real-collector-export.json'
        create(self.export, real_export())
        self.adapter.prep = lambda directory=None: run_flow.prepare(
            self.base.store, self.base.project, 'SYNTHETIC_MULTI_ASSET_RUN', directory or self.adapter.d,
            self.adapter.raw_file, agent_id='SYNTHETIC_MAIN', collector_file=self.export)

    def test_three_assets_reach_market_portfolio_and_report_material(self):
        before = storage.current(self.base.store)['decision_id']
        prepared, observed, _ = self.f.observe(patterns=[])
        public = read(prepared['context'])
        collected = public['collector_observations']
        self.assertEqual(set(collected['assets']), {'BTC','QQQ','VOO'})
        self.assertTrue(all(r['status'] == 'AUXILIARY' for r in collected['assets'].values()))
        self.assertEqual(collected['assets']['VOO']['datasets']['basis']['instrument'], 'SPYUSDT')
        self.assertEqual(collected['assets']['VOO']['datasets']['basis']['relation'], 'proxy')
        self.assertEqual(collected['assets']['QQQ']['datasets']['funding']['values']['funding_24h_pct'], 0)
        self.assertIsNone(collected['assets']['QQQ']['sources']['stock_quote']['as_of'])
        self.assertIn('retrieved_at', collected['assets']['QQQ']['sources']['stock_quote'])
        self.assertNotEqual(collected['assets']['QQQ']['datasets']['funding']['raw_ref'],
                            collected['assets']['VOO']['datasets']['funding']['raw_ref'])
        for key in ('fact_files','confirmed_account_facts','portfolio_analysis','recommendations','mechanical_observation','full_report_text'):
            self.assertNotIn('"'+key+'"', json.dumps(public))
        portfolio = read(self.adapter.d / 'portfolio-context.json')
        self.assertIn('collector_observations', json.dumps(portfolio))
        reported = reporting.context(self.base.store)
        self.assertEqual(reported['collector_observations']['snapshot_id'], collected['snapshot_id'])
        self.assertEqual(reported['report_subjects']['QQQ']['identity_status'], 'UNVERIFIED')
        self.assertEqual(storage.current(self.base.store)['decision_id'], before)

    def test_auxiliary_references_are_frozen_and_cannot_verify_market_facts(self):
        prepared = self.adapter.prep()
        run = read(prepared['run'])
        package = read(Path(run['evidence']) / 'evidence.json')
        context = read(prepared['context'])['collector_observations']
        ref = context['assets']['QQQ']['datasets']['stock_1h']['raw_ref']
        self.assertEqual(evidence.resolve(package, run['evidence'], ref)['instrument'], 'EQ_QQQ')
        with self.assertRaisesRegex(Invalid, 'untrusted lead'):
            evidence.resolve(package, run['evidence'], ref, verified=True)
        source = next(s for s in package['sources'] if s['id'] == ref['source_id'])
        self.assertEqual(source['trust'], 'UNTRUSTED_LEAD')
        index = read(prepared['reference_index'])
        indexed = next(s for s in index if s['id'] == source['id'])
        self.assertIn(ref, indexed['refs'])
        self.assertTrue(any(r['pointer'].startswith('/assets/VOO/') for r in indexed['refs']))
        self.assertFalse(package['verified_facts'])
        self.assertEqual(self.adapter.prep(), prepared)
        self.export.write_text('[]')
        with self.assertRaisesRegex(Invalid, 'collector input changed'):
            self.adapter.prep()

    def test_research_gets_neutral_auxiliary_inputs_with_original_unknowns(self):
        prepared, observed, _ = self.f.observe(patterns=[])
        run = read(prepared['run'])
        package = read(Path(run['evidence']) / 'evidence.json')
        raw = read(run['market'])
        source = next(s for s in package['sources'] if s['id'].startswith('market_'))
        reference = {'source_id':source['id'], 'pointer':'/datasets/binance_spot_1h',
                     'value_hash':digest(raw['datasets']['binance_spot_1h'])}
        request = research.request(self.base.store, self.base.project, {
            'issue_key':'MULTI_ASSET_TEST', 'scope':['/outlook'],
            'question':'Synthetic: can independently checked equity evidence resolve a missing input?',
            'decision_id':storage.current(self.base.store)['decision_id'], 'evidence_id':package['evidence_id'],
            'change':{'kind':'INITIAL_QUESTION','reason':'Synthetic unresolved evidence gap.',
                      'refs':[reference], 'reopen_condition':None},
            'reopen_conditions':['Synthetic independently verified new evidence.']})
        packet = read(self.base.store / request['independent_input_path'])
        self.assertTrue(packet['collector_observations']['assets']['VOO']['datasets']['stock_quote']['source_time_missing'])
        self.assertNotIn('fact_files', packet)
        for source in packet['sources']:
            if source['source']['trust'] == 'UNTRUSTED_LEAD':
                payload = read(source['path'])
                self.assertNotIn('mechanical_observation', json.dumps(payload))
                self.assertEqual(payload['independent_verified_sources'], 0)

    def test_one_invalid_asset_does_not_remove_other_assets(self):
        rows = real_export()
        rows[1]['instrument']['derivatives_symbol'] = 'SPYUSDT'
        self.export.write_text(json.dumps(rows))
        captured = collector.capture(self.base.project, self.export)
        self.assertEqual(captured['assets']['QQQ']['status'], 'UNAVAILABLE')
        self.assertEqual(captured['assets']['VOO']['status'], 'AUXILIARY')
        self.assertEqual(captured['assets']['BTC']['status'], 'AUXILIARY')
        review = collector_observation.review_exports(rows)
        self.assertFalse(review['deep_review_authorized'])

    def test_invalid_frozen_file_fails_and_asset_gaps_keep_their_reason(self):
        self.export.write_text('not JSON')
        with self.assertRaises(ValueError):
            collector.capture(self.base.project, self.export)
        rows = real_export()
        rows[1] = {'asset_id':'QQQ', 'role':'auxiliary_observation_gap',
                   'reasons':['text_hash_mismatch', 'untrusted free prose']}
        self.export.write_text(json.dumps(rows))
        value = collector.capture(self.base.project, self.export)
        self.assertEqual(value['assets']['QQQ']['reasons'], ['text_hash_mismatch'])
        self.assertEqual(value['assets']['VOO']['status'], 'AUXILIARY')

    def test_coverage_comparison_keeps_same_source_ids_in_distinct_assets(self):
        previous = real_export()
        current = real_export()
        current[1]['datasets'].pop('basis')
        review = collector_observation.review_exports(current, previous)
        lost = review['assets']['QQQ']['lost_coverage']
        self.assertEqual([(r['asset_id'], r['dataset']) for r in lost], [('QQQ', 'basis')])
        self.assertEqual(review['assets']['VOO']['lost_coverage'], [])
        self.assertFalse(review['deep_review_authorized'])
        for asset in ('QQQ','VOO'):
            self.assertTrue(all(row['asset_id'] == asset for row in review['assets'][asset]['observation']['usable_coverage']))
        previous[1]['instrument']['derivatives_symbol'] = 'SPYUSDT'
        changed = collector_observation.review_exports(current, previous)
        self.assertEqual(changed['assets']['QQQ']['status'], 'AUXILIARY')
        self.assertFalse(changed['assets']['QQQ']['coverage_comparison_performed'])


if __name__ == '__main__':
    unittest.main()
