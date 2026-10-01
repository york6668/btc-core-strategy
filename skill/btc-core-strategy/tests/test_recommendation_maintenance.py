"""Recommendation/observation contracts against synthetic facts and frozen candles."""
from copy import deepcopy
from pathlib import Path
import unittest
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_v4 as base
from test_v4 import seal
from unittest import mock
import test_market_observation as fixtures
from v4 import assessment, evidence, recommendations, report, sensing, semantics, storage
from v4.common import Invalid, create, digest, facts, read


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        base.V4Tests.setUp(self)
        timer = mock.patch("market_observation.time.time", return_value=1900000000)
        timer.start()
        self.addCleanup(timer.stop)

    def upgrade(self):
        self.raw = fixtures.evidence(1898600400)  # 2030-03-01 13:00Z
        source_time = next(iter(self.raw['retrievals'].values()))['retrieved_at']
        path = self.base / 'market.json'
        create(path, self.raw)
        spec = {k: deepcopy(v) for k, v in self.evidence.items() if k not in ('schema', 'evidence_id')}
        spec['parent_id'] = self.evidence['evidence_id']
        spec['cutoff'] = '2030-03-01T13:02:00Z'
        files = {s['id']: str(self.evidence_dir / 'raw' / (s['raw_hash'][7:] + '.json')) for s in spec['sources']}
        spec['sources'].append({'id': 'hourly', 'origin': 'BINANCE', 'url': 'https://data-api.binance.vision/api/v3/klines', 'trust': 'PUBLIC_RAW', 'published_at': self.raw['cutoff'], 'retrieved_at': source_time, 'start': self.raw['datasets']['binance_spot_1h']['requested_start'], 'end': self.raw['cutoff'], 'raw_hash': digest(path.read_bytes())})
        files['hourly'] = str(path)
        newdir = self.base / 'recommendation-evidence'
        self.evidence = evidence.freeze(spec, files, newdir, self.evidence_dir)
        self.evidence_dir = newdir
        ref = {'source_id': 'hourly', 'pointer': '/datasets', 'value_hash': digest(self.raw['datasets'])}
        self.draft.update(revision=2, evidence_id=self.evidence['evidence_id'])
        self.draft['domain']['runtime']['trigger_type'] = 'MANUAL_REEVALUATION'
        self.draft['temporal_scope'].update(knowledge_cutoff=spec['cutoff'], market_cutoff=self.raw['cutoff'], current_market_refs=[ref])
        for p in self.draft['portfolio_analysis'].values():
            p['semantic'].pop('scenario_budget')
        self.draft['recommendations'] = {}
        for side in recommendations.SIDES:
            self.draft['recommendations'][side] = {'status': 'RECOMMENDED', 'unit': 'SHORT_NOTIONAL_U' if side == 'upper_short' else 'SPOT_CAPITAL_U',
                'tiers': [{'id': side + '_1', 'price_low_usd': '102.5' if side == 'upper_short' else '99', 'price_high_usd': '103' if side == 'upper_short' else '99', 'amount_u': '1000', 'capital_u': '200' if side == 'upper_short' else '1000'}],
                'validity': {'from': '2030-03-01T10:00:00Z', 'until': '2030-03-08T13:02:00Z', 'conditions': ['STRUCTURE_RETAINED'], 'invalidations': ['SUSTAINED_OPPOSING_FLOW']},
                'purpose': ['PORTFOLIO_PROTECTION'], 'amount_basis': ['LIMIT_INCREMENTAL_RISK'], 'allocation_percent': {} if side == 'upper_short' else {'BTC': '60', 'ETH': '20', 'XAUT': '10', 'SOL': '5', 'BNB': '5'},
                'rationale': '建议有跨期原始证据支持，当前维持不调整。', 'impact': '主要解释错误时由Codex及时重新评估；不会据此改变真实仓位。', 'refs': [ref]}
        self.draft['recommendations']['portfolio'] = {'capital_envelope_u': '1200', 'sequence_policy': 'REEVALUATE_AFTER_FIRST', 'existing_exposure': '完整敞口未知，保留既有净多事实。', 'uncertainty': '计划占用不等于账户可用资金。', 'narrative': '两侧顺序发生均计入共同资金池；首项真实成交同步后由Codex重评剩余建议。'}
        self.draft['report']['overview'] = '当前五模块维持既有安排，新增调整暂缓。'
        self.bind()

    def bind(self):
        ctx = sensing.context(self.store, self.project / '.strategy/market_observation.sqlite3', self.project, self.raw)
        self.draft['maintenance_binding'] = {'context_id': ctx['context_id'], 'market_evidence_id': self.raw['evidence_id']}
        self.review = seal(self.draft)
        return ctx

    def commit(self):
        return storage.commit(self.store, self.project, self.draft, self.evidence_dir, self.review, storage.current(self.store)['decision_id'])

    def observe(self, raw=None, finding='NO_MATERIAL_CHANGE', extra=False):
        raw = raw or self.raw
        db = self.project / '.strategy/market_observation.sqlite3'
        ctx = sensing.context(self.store, db, self.project, raw)
        candidate = fixtures.review(raw, {'context_id': ctx['context_id'], 'baselines': {}}, required=False)
        value = {'schema': 'BTC_SENSING_REVIEW_V4', 'context_id': ctx['context_id'], 'market_review': candidate,
                 'recommendation_checks': [{'side': side, 'findings': [finding], 'refs': ['windows.4h.binance_spot_1h'], 'explanation': 'Synthetic same-pattern maintenance finding.'} for side in recommendations.SIDES],
                 'extra_findings': [{'summary': 'Outside taxonomy discovery', 'refs': ['windows.4h.binance_spot_1h'], 'material': True}] if extra else []}
        return sensing.record(self.store, db, self.project, raw, value)

    def test_current_zero_future_prices_and_amounts_are_complete(self):
        self.upgrade()
        self.assertTrue(all(p['amount_u'] == '0' for p in self.draft['domain']['portfolio_decision'].values()))
        value = self.commit()
        self.assertTrue(value['report_required'])
        body = (self.store / 'current/report.md').read_text()
        self.assertIn('1000U名义金额', body)
        self.assertIn('1000U投入金额', body)
        self.assertLess(body.index('上沿空单'), body.index('建议有跨期'))
        self.assertEqual(len(recommendations.watch_targets(value)), 2)
        self.assertNotIn('amount_u', str(recommendations.watch_targets(value)))

    def test_missing_price_amount_and_empty_valid_tiers_rejected(self):
        self.upgrade()
        for key in ('amount_u', 'price_low_usd'):
            bad = deepcopy(self.draft)
            bad['recommendations']['upper_short']['tiers'][0].pop(key)
            with self.assertRaises(Invalid): report.render(bad)
        self.draft['recommendations']['upper_short']['tiers'] = []
        with self.assertRaises(Invalid): report.render(self.draft)

    def test_reasoned_no_recommendation_valid_without_fake_zero(self):
        self.upgrade()
        for side in recommendations.SIDES:
            self.draft['recommendations'][side].update(status='NOT_RECOMMENDED', tiers=[], rationale='当前证据不支持新增此侧建议。')
        self.bind()
        self.commit()
        body = report.render(self.draft)
        self.assertEqual(body.count('当前不建议'), 2)
        self.assertNotIn('0U名义金额', body)

    def test_shared_funding_and_ordered_exposure(self):
        self.upgrade()
        value = self.draft['recommendations']
        seq = recommendations.sequences(value)
        self.assertEqual(seq['spot_then_short'][0]['cumulative_capital_u'], '1000')
        self.assertEqual(seq['short_then_spot'][0]['cumulative_capital_u'], '200')
        self.assertEqual(seq['spot_then_short'][-1]['cumulative_capital_u'], '1200')
        value['portfolio']['capital_envelope_u'] = '1000'
        with self.assertRaisesRegex(Invalid, 'double counted'): report.render(self.draft)

    def test_price_amount_validity_purpose_report_even_same_current_amount(self):
        self.upgrade()
        for path, value in [('price_low_usd', '102.6'), ('amount_u', '1100')]:
            changed = deepcopy(self.draft)
            changed['recommendations']['upper_short']['tiers'][0][path] = value
            self.assertIn('recommendations', semantics.compare(changed, self.draft)['changed'])
        changed = deepcopy(self.draft)
        changed['recommendations']['upper_short'].update(status='NOT_RECOMMENDED', tiers=[])
        self.assertTrue(semantics.compare(changed, self.draft)['report_required'])
        changed = deepcopy(self.draft)
        changed['recommendations']['upper_short']['purpose'] = ['PROTECT_DEEP_DOWNSIDE']
        self.assertTrue(semantics.compare(changed, self.draft)['report_required'])

    def test_prose_format_date_roll_silent_and_real_expiry_extension_reports(self):
        self.upgrade()
        changed = deepcopy(self.draft)
        changed['recommendations']['upper_short']['rationale'] = '同义改写。'
        changed['recommendations']['upper_short']['tiers'][0]['id'] = 'renamed'
        changed['recommendations']['lower_spot']['allocation_percent']['BTC'] = '60.0'
        changed['temporal_scope']['knowledge_cutoff'] = '2030-03-01T14:00:00Z'
        changed['recommendations']['upper_short']['validity']['until'] = '2030-03-08T22:02:00+09:00'
        self.assertFalse(semantics.compare(changed, self.draft)['report_required'])
        changed['recommendations']['upper_short']['validity']['until'] = '2030-03-09T13:02:00Z'
        self.assertTrue(semantics.compare(changed, self.draft)['report_required'])

    def test_legacy_scenario_budget_no_longer_writable(self):
        self.upgrade()
        self.draft['portfolio_analysis']['new_spot']['semantic']['scenario_budget'] = None
        self.review = seal(self.draft)
        with self.assertRaisesRegex(Invalid, 'module semantics'): self.commit()

    def test_basket_and_independent_btc_share_cash_once(self):
        self.upgrade()
        value = self.draft['recommendations']
        value['lower_spot']['tiers'].append({'id':'BTC_DISCOUNT', 'price_low_usd':'90', 'price_high_usd':'90',
            'amount_u':'500', 'capital_u':'500', 'allocation_percent':{'BTC':'100'}})
        value['portfolio']['capital_envelope_u'] = '1700'
        self.bind(); published = self.commit()
        sequence = recommendations.sequences(value)['short_then_spot']
        self.assertEqual(sequence[0]['spot_capital_by_asset_u'], {})
        self.assertEqual(sequence[-1]['cumulative_capital_u'], '1700')
        self.assertEqual(sequence[-1]['spot_capital_by_asset_u'],
                         {'BTC':'1100','ETH':'200','XAUT':'100','SOL':'50','BNB':'50'})
        self.assertEqual(sequence[-1]['short_notional_u'], '1000')
        self.assertIn('独立BTC现货', report.render(published['assessment']))
        self.assertEqual(len(recommendations.watch_targets(published)), 2)
        value['portfolio']['capital_envelope_u'] = '1200'
        with self.assertRaisesRegex(Invalid, 'double counted'):
            recommendations.validate(value, self.draft['temporal_scope']['knowledge_cutoff'], lambda ref: None)

    def test_tier_allocation_is_meaningful_but_numeric_spelling_is_not(self):
        self.upgrade()
        changed = deepcopy(self.draft)
        tier = changed['recommendations']['lower_spot']['tiers'][0]
        tier['allocation_percent'] = deepcopy(changed['recommendations']['lower_spot']['allocation_percent'])
        self.assertFalse(semantics.compare(changed, self.draft)['report_required'])
        tier['allocation_percent'] = {'BTC':'100'}
        self.assertTrue(semantics.compare(changed, self.draft)['report_required'])
        other = deepcopy(changed)
        other['recommendations']['lower_spot']['tiers'][0]['allocation_percent']['BTC'] = '100.0'
        self.assertFalse(semantics.compare(other, changed)['report_required'])

    def test_invalid_or_short_tier_allocation_rejected(self):
        self.upgrade()
        for allocation in ({'BTC':'90'}, {'ETH':'100'}, {'BTC':'100','ETH':'0'}):
            value = deepcopy(self.draft['recommendations'])
            value['lower_spot']['tiers'][0]['allocation_percent'] = allocation
            with self.subTest(allocation=allocation), self.assertRaises(Invalid):
                recommendations.validate(value, self.draft['temporal_scope']['knowledge_cutoff'], lambda ref: None)
        value = deepcopy(self.draft['recommendations'])
        value['upper_short']['tiers'][0]['allocation_percent'] = {'BTC':'100'}
        with self.assertRaisesRegex(Invalid, 'only belongs to spot'):
            recommendations.validate(value, self.draft['temporal_scope']['knowledge_cutoff'], lambda ref: None)

    def test_dynamic_baskets_accept_reviewed_members_and_weights(self):
        self.upgrade()
        for weights in ({'BTC':'65','ETH':'20','XAUT':'10','BNB':'5'},
                        {'BTC':'55','ETH':'20','XAUT':'10','SOL':'5','BNB':'5','LINK':'5'},
                        {'BTC':'50','ETH':'30','XAUT':'10','SOL':'5','BNB':'5'},
                        {'BTC':'95','1INCH':'5'},
                        {'BTC':'100'}):
            with self.subTest(weights=weights):
                value=deepcopy(self.draft['recommendations'])
                spot=value['lower_spot'];spot['allocation_percent']=weights
                recommendations.validate(value,self.draft['temporal_scope']['knowledge_cutoff'],lambda ref: None)
                spot['tiers'][0]['allocation_percent']=deepcopy(weights)
                recommendations.validate(value,self.draft['temporal_scope']['knowledge_cutoff'],lambda ref: None)
                self.assertEqual(sum(float(v) for v in recommendations.spot_capital_by_asset(spot).values()),1000)
        for weights in ({},{'BTC':'99'},{'BTC':'100','ETH':'0'},{'btc':'100'},{'BTC/USDT':'100'},
                        {'BTC':'NaN'},{'BTC':'Infinity'}):
            with self.subTest(weights=weights),self.assertRaises(Invalid):
                recommendations._validate_allocation(weights)

    def test_btc_only_basket_keeps_independent_identity_and_history(self):
        self.upgrade()
        original=self.commit()
        value=self.draft['recommendations'];spot=value['lower_spot']
        spot['allocation_percent']={'BTC':'100'}
        inherited=deepcopy(value)
        spot['tiers'][0]['allocation_percent']={'BTC':'100.0'}
        self.assertNotEqual(recommendations.stable(inherited),recommendations.stable(value))
        self.assertEqual(recommendations.spot_label(inherited['lower_spot']['tiers'][0]),'现货篮子')
        self.assertEqual(recommendations.spot_label(spot['tiers'][0]),'独立BTC现货')
        spot['tiers'].insert(0,dict(deepcopy(inherited['lower_spot']['tiers'][0]),id='BASKET'))
        value['portfolio']['capital_envelope_u']='2200'
        self.bind();published=self.commit()
        self.assertIn('独立BTC现货',report.render(published['assessment']))
        self.assertEqual(recommendations.spot_capital_by_asset(spot),{'BTC':'2000'})
        from v4 import delivery
        restored=delivery._decision(self.store,original['decision_id'])
        self.assertEqual(restored['assessment']['recommendations']['lower_spot']['allocation_percent'],
                         {'BTC':'60','ETH':'20','XAUT':'10','SOL':'5','BNB':'5'})

    def test_intrahour_touch_recognized_after_close_returns(self):
        self.upgrade(); self.commit()
        observed = self.observe()
        self.assertIn('upper_short:PRICE_TOUCH_REVIEW', observed['triggers'])
        value = read(self.store / 'sensing' / (observed['observation_id'][7:] + '.json'))
        sample = value['measurements'][0]['tiers'][0]['samples'][0]
        self.assertTrue(sample['touched']); self.assertEqual(sample['close_relation'], 'BELOW')
        self.assertEqual(observed['maintenance_status'], 'OBSERVATION_ONLY')
        self.assertEqual(storage.current(self.store)['assessment']['recommendations'], self.draft['recommendations'])

    def test_counterevidence_and_budget_basis_open_gate_without_new_pattern(self):
        self.upgrade(); self.commit()
        result = self.observe(finding='COUNTEREVIDENCE', extra=True)
        self.assertIn('upper_short:COUNTEREVIDENCE', result['triggers'])
        self.assertIn('EXTRA_DISCOVERY', result['triggers'])
        self.assertTrue(sensing.gate(self.store, result['observation_id'])['deep_eligible'])

    def test_expiry_opens_gate_without_pattern_change(self):
        self.upgrade(); self.commit()
        later = fixtures.evidence(1899291600)
        observed = self.observe(later)
        self.assertIn('upper_short:EXPIRY', observed['triggers'])

    def test_manual_deep_updates_actual_cumulative_range(self):
        self.upgrade(); self.commit()
        later = fixtures.evidence(1898604000)
        ctx = sensing.context(self.store, self.project / '.strategy/market_observation.sqlite3', self.project, later)
        self.assertEqual(ctx['accumulation']['start'], self.raw['cutoff'])
        self.assertEqual(ctx['accumulation']['windows']['binance_spot_1h']['actual_count'], 1)

    def test_revised_same_hour_retained_and_not_added_as_new_volume(self):
        a, b = fixtures.evidence(), fixtures.evidence(buy_ratio=.55)
        value = sensing.accumulation(a, [b], b)
        self.assertTrue(value['revisions'])
        self.assertIsNone(value['windows']['binance_spot_1h']['measurements'])
        self.assertEqual(value['start'], value['end'])

    def test_missing_candle_not_assumed_untouched_or_successfully_maintained(self):
        self.upgrade(); self.commit()
        missing = fixtures.evidence(1898600400, missing='coinbase')
        ctx = sensing.context(self.store, self.project / '.strategy/market_observation.sqlite3', self.project, missing)
        self.assertTrue(ctx['measurements'][0]['gaps'])
        self.assertEqual(len({s['dataset'] for s in ctx['measurements'][0]['tiers'][0]['samples']}), 3)

    def test_new_observation_blocks_stale_deep_commit(self):
        self.upgrade(); self.commit(); self.bind()
        old = deepcopy(self.draft); review = deepcopy(self.review)
        self.observe()
        with self.assertRaisesRegex(Invalid, 'requires main review'):
            storage.commit(self.store, self.project, old, self.evidence_dir, review, storage.current(self.store)['decision_id'])

    def test_old_gate_rejected_after_new_successful_deep_and_silent_formal_stays(self):
        self.upgrade(); initial = self.commit()
        observed = self.observe()
        self.bind(); self.draft['outlook']['narrative'] = '同义描述。'; self.review = seal(self.draft)
        self.assertEqual(self.commit()['status'], 'UNCHANGED')
        self.assertEqual(storage.current(self.store)['decision_id'], initial['decision_id'])
        with self.assertRaisesRegex(Invalid, 'old sensing'): sensing.gate(self.store, observed['observation_id'])

    def test_sensing_idempotent_and_public_db_has_no_assets_or_recommendations(self):
        import sqlite3
        self.upgrade(); self.commit()
        first = self.observe(); second = self.observe()
        self.assertEqual(first['observation_id'], second['observation_id'])
        db = self.project / '.strategy/market_observation.sqlite3'
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM review').fetchone()[0], 0)
            payload = conn.execute('SELECT payload FROM evidence').fetchone()[0]
            self.assertNotIn('recommendations', payload); self.assertNotIn('fact_version', payload)

    def test_actual_fact_change_opens_gate_without_claiming_new_fact_owner(self):
        self.upgrade(); self.commit()
        (self.project / '纠正记录.md').write_text('合成已确认新事实')
        result = self.observe()
        self.assertIn('FACTS_CHANGED', result['triggers'])

    def test_legacy_context_and_new_legacy_writes_disabled_at_activation(self):
        from v4 import hourly
        self.upgrade(); self.commit()
        create(self.store / 'write-policy.json', {'minimum_assessment_revision': 2})
        with self.assertRaisesRegex(Invalid, 'requires --market'):
            hourly.context(self.store, self.project / '.strategy/market_observation.sqlite3')

    def test_unknown_short_margin_does_not_block_definite_notional_recommendation(self):
        self.upgrade()
        self.draft['recommendations']['upper_short']['tiers'][0]['capital_u'] = None
        self.draft['recommendations']['portfolio']['capital_envelope_u'] = None
        self.bind(); self.commit()
        text = report.render(self.draft)
        self.assertIn('1000U名义金额', text)
        self.assertIn('1000U已知计划占用＋未确定的空单占用', text)
        self.draft['recommendations']['portfolio']['capital_envelope_u'] = '1000'
        with self.assertRaisesRegex(Invalid, 'unknown short capital'): report.render(self.draft)

    def test_unobserved_hours_retained_and_partial_issue_hour_not_exact_touch(self):
        self.upgrade(); self.commit()
        ctx = self.bind()
        ctx['recommendations_issued_at'] = '2030-03-01T11:30:00Z'
        samples = sensing.measure(ctx, self.raw)[0]['tiers'][0]['samples']
        self.assertEqual({s['time'] for s in samples}, {'2030-03-01T11:00:00Z', '2030-03-01T12:00:00Z'})
        self.assertEqual(samples[0]['timing'], 'PARTIAL_ISSUE_HOUR')

    def test_scheduled_completion_needs_real_sensing_and_keeps_current_facts(self):
        self.upgrade(); self.commit()
        self.draft['domain']['runtime']['trigger_type'] = 'SCHEDULED_STRATEGY_BOUNDARY'
        self.bind()
        with self.assertRaisesRegex(Invalid, 'actual sensing observation'): self.commit()
        before = facts(self.project)
        self.observe(finding='AMOUNT_BASIS_CHANGED')
        self.bind()
        self.assertEqual(self.commit()['status'], 'UNCHANGED')
        self.assertEqual(before, facts(self.project))

    def test_pointer_crash_requires_proven_completion_repair(self):
        from v4 import hourly
        self.upgrade()
        def crash(stage):
            if stage == 'after_pointer': raise RuntimeError('simulated crash')
        with self.assertRaises(RuntimeError):
            storage.commit(self.store, self.project, self.draft, self.evidence_dir, self.review, self.prior['decision_id'], crash)
        with self.assertRaisesRegex(Invalid, 'repair-completion'): self.bind()
        path = self.store / 'deep' / (self.draft['draft_id'][7:] + '.json')
        hourly.complete_deep(self.store, path, self.project / '.strategy/market_observation.sqlite3')
        ctx = self.bind()
        self.assertEqual(ctx['identity']['deep_completion_hash'], read(path)['completion_hash'])

    def test_historical_delivery_uses_committed_bytes_after_renderer_changes(self):
        from v4 import delivery
        self.upgrade(); version = self.commit()
        original = (self.store / 'current/report.md').read_text()
        with mock.patch('v4.report.render', side_effect=AssertionError('must not regenerate frozen history')):
            value = delivery.enqueue(self.store, version['decision_id'], {'project_id': 'SYNTHETIC', 'thread_id': 'SYNTHETIC'})
        self.assertEqual(value['artifact']['body'], original)


if __name__ == '__main__': unittest.main()
