"""Synthetic assets, frozen raw evidence, fault injection; no live Owners or delivery."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile
import unittest
from urllib.parse import urlencode
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import strategy_contract as legacy
import test_production_strategy as production
from v4 import assessment, delivery, domain, evidence, migration, report, semantics, storage
from v4.common import Invalid, create, digest, facts, read


def seal(draft):
    draft['draft_id'] = digest({k: v for k, v in draft.items() if k != 'draft_id'})
    return {'schema': 'BTC_MAIN_REVIEW_V4', 'draft_id': draft['draft_id'], 'evidence_id': draft['evidence_id'],
            'reviewer_role': 'MAIN', 'checks': dict.fromkeys(('raw_evidence', 'competing_explanations', 'portfolio_completeness', 'report_fidelity', 'semantic_continuity'), 'Synthetic source and claim audited.'), 'unresolved_issues': [], 'material_new_outlook': True}


def scenario(direction='RANGE'):
    return {'direction': direction, 'horizon': {'unit': 'WEEKS', 'minimum': 2, 'maximum': 8},
            'mechanism': ['DRIVER_UNRESOLVED'], 'formation_conditions': ['CROSS_VENUE_RETENTION'], 'invalidation_conditions': ['OPPOSING_FLOW_SEQUENCE']}


class V4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='btc-v4-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'project'
        self.project.mkdir()
        for name in ('资产配置.md', '操作记录.md', '纠正记录.md'):
            (self.project / name).write_text('合成事实：总方向净多，未记录账户字段未知；非真实账户。\n')
        self.store = self.project / '.strategy/v4'
        self.clock = production.frozen_clock('2030-03-01T22:00:00+09:00')
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        evidence_clock = mock.patch('v4.evidence.now', return_value='2030-03-01T23:00:00+09:00')
        evidence_clock.start()
        self.addCleanup(evidence_clock.stop)
        prior, _ = production.migration_bundle()
        prior['account_facts']['fact_owner_fingerprints'] = dict(zip(legacy.FACT_OWNER_PATHS, facts(self.project).values()))
        prior['fingerprints'] = legacy.compute_fingerprints(prior)
        prior['control'] = legacy._derive_control_unchecked(prior)
        state, plan = self.project / '策略状态.md', self.project / '当前预期计划.md'
        state.write_text(legacy.render_projection_document(prior, 'STATE'))
        plan.write_text(legacy.render_projection_document(prior, 'PLAN'))
        self.prior = migration.migrate(self.store, self.project, state, plan)
        with production.rendered_baseline(prior) as (baseline, _, __):
            value = production.daily_bundle(prior, baseline, '2030-03-01T22:00:00+09:00')
        self.evidence_dir = self.base / 'evidence'
        sources, files, bindings = [], {}, {}
        for dim, entry in value['evidence_matrix'].items():
            if dim == 'account_impact' or entry['status'] != 'VERIFIED':
                continue
            for i, source in enumerate(entry['sources']):
                sid = dim + str(i)
                payload = {'value': {'price': value['observation']['raw_prices'][source['venue']], 'observed': source['as_of']}}
                raw = self.base / (sid + '.json')
                create(raw, payload)
                files[sid] = str(raw)
                sources.append({'id': sid, 'origin': source['canonical_origin'], 'url': source['endpoint_url'], 'trust': 'PUBLIC_RAW',
                                'published_at': source['as_of'], 'retrieved_at': source['retrieved_at'], 'start': source['as_of'], 'end': source['as_of'], 'raw_hash': digest(raw.read_bytes())})
                bindings['/evidence_matrix/' + dim + '/sources/' + str(i)] = [{'source_id': sid, 'pointer': '/value', 'value_hash': digest(payload['value'])}]
                bindings['/observation/raw_prices/' + source['venue']] = [{'source_id': sid, 'pointer': '/value/price', 'value_hash': digest(payload['value']['price'])}]
        spec = {'parent_id': None, 'cutoff': '2030-03-01T22:00:00+09:00', 'sources': sources, 'measurements': [], 'verified_facts': [], 'candidates': [], 'gaps': ['causal flow unavailable'],
                'handoff': {'previous_hour': None, 'successful_deep': None, 'changes_since_hour': [], 'changes_since_deep': [], 'active_hypotheses': [], 'open_questions': ['driver']}}
        self.evidence = evidence.freeze(spec, files, self.evidence_dir)
        self.draft = {'schema': 'BTC_ASSESSMENT_DRAFT_V4', 'evidence_id': self.evidence['evidence_id'], 'evidence_bindings': bindings,
                      'fact_version': facts(self.project), 'domain': value,
                      'temporal_scope': {'market_cutoff': sources[0]['end'], 'knowledge_cutoff': spec['cutoff'], 'domain_market_cutoff': sources[0]['end'], 'current_market_refs': [next(iter(bindings.values()))[0]]},
                      'outlook': {'semantic': {'status': 'ASSESSED', 'baseline': scenario(), 'alternatives': [scenario('DOWNSIDE_SKEW')], 'next_evidence': ['SPOT_OI_SEQUENCE']}, 'narrative': '未来数周以区间为基准，等待独立资金时序，失效则转向下行情景。', 'refs': []},
                      'robustness': {'semantic': {'failed_primary': ['DOWNSIDE_PERSISTS'], 'module_impacts': dict.fromkeys(legacy.PORTFOLIO_MODULES, ['EXPOSURE_REVIEW']), 'adjustment_conditions': ['CONFIRMED_DEMAND_LOSS']}, 'narrative': '主要解释失效时现有多头承压，现有保护空头缓冲部分风险，新增模块等待，保留现金。', 'refs': []},
                      'portfolio_analysis': {m: {'semantic': {'protection_purpose': ['PRESERVE_OPTIONALITY'], 'amount_basis': ['LOCAL_CONFIRMED_BUDGET'], 'scenario_budget': None}, 'narrative': '维持现有角色，等待独立证据；资金占用来自本地确认事实。'} for m in legacy.PORTFOLIO_MODULES},
                      'provenance': {'model': 'gpt-6-astra', 'effort': 'max', 'agent_id': 'SYNTHETIC_TEST', 'independent_review': 'Synthetic independent pass', 'candidate_review': 'Synthetic candidate rejected'},
                      'report': {'blocks': [{'template': '{{/outlook/narrative}}', 'coverage': ['outlook', 'causes', 'alternatives', 'uncertainty', 'next_evidence']}, {'template': '{{/robustness/narrative}}', 'coverage': ['risk']}] + [{'template': '{{/portfolio_analysis/' + m + '/narrative}}', 'coverage': [m]} for m in legacy.PORTFOLIO_MODULES]}, 'stop_reason': 'INSUFFICIENT_CONSERVATIVE_COMPLETE'}
        self.review = seal(self.draft)

    def commit(self):
        self.review = seal(self.draft)
        return storage.commit(self.store, self.project, self.draft, self.evidence_dir, self.review, storage.current(self.store)['decision_id'])

    def test_full_flow_and_exact_delivery(self):
        value = self.commit()
        self.assertTrue(value['report_required'])
        self.assertEqual(storage.current(self.store)['decision_id'], value['decision_id'])
        queued = delivery.enqueue(self.store, value['decision_id'], {'project_id': 'SYNTHETIC', 'thread_id': 'SYNTHETIC'})
        identity = queued['delivery_id']
        delivery.transition(self.store, identity, 'BEGIN')
        delivery.transition(self.store, identity, 'UNKNOWN')
        with self.assertRaises(Invalid):
            delivery.transition(self.store, identity, 'BEGIN')
        receipt = {'schema': 'BTC_CLOUD_READBACK_V4', 'destination': queued['artifact']['destination'], 'checked_at': '2030-03-01T13:10:00Z', 'visible_report_text': queued['artifact']['body'], 'assistant_responses': ['已收录'], 'record_refs': ['SYNTHETIC_READBACK'], 'absence_confirmed': False}
        bad = dict(receipt, visible_report_text=receipt['visible_report_text'] + '\n')
        with self.assertRaises(Invalid):
            delivery.transition(self.store, identity, 'CONFIRM', bad)
        self.assertEqual(delivery.transition(self.store, identity, 'CONFIRM', receipt)['state'], 'DELIVERED')
        self.assertEqual(storage.recover(self.store)['outbox'][0]['delivery_id'], identity)
        self.assertEqual(storage.recover(self.store)['outbox'][0]['state'], 'DELIVERED')

    def test_format_migration_can_establish_baseline_without_report(self):
        self.review['material_new_outlook'] = False
        value = storage.commit(self.store, self.project, self.draft, self.evidence_dir, self.review, self.prior['decision_id'])
        self.assertFalse(value['report_required'])
        self.assertIsNotNone(value['assessment'])
        self.assertNotEqual(value['decision_id'], self.prior['decision_id'])
        with self.assertRaises(Invalid):
            delivery.enqueue(self.store, value['decision_id'], {'project_id': 'SYNTHETIC', 'thread_id': 'SYNTHETIC'})

    def test_silent_rewrite_and_date_roll(self):
        first = self.commit()
        self.draft['outlook']['narrative'] = '未来几周仍观察区间，等待可辨别的资金驱动。'
        second = self.commit()
        self.assertEqual(second['status'], 'UNCHANGED')
        self.assertEqual(first['decision_id'], storage.current(self.store)['decision_id'])
        self.assertTrue((self.store / 'deep-current.json').exists())

    def test_outlook_horizon_direction_and_purpose_change_report(self):
        old = deepcopy(self.draft)
        self.draft['outlook']['semantic']['baseline']['horizon']['unit'] = 'MONTHS'
        self.assertIn('outlook', semantics.compare(self.draft, old)['changed'])
        self.draft = deepcopy(old)
        self.draft['outlook']['semantic']['baseline']['direction'] = 'DOWNSIDE_SKEW'
        self.assertTrue(semantics.compare(self.draft, old)['report_required'])
        self.draft = deepcopy(old)
        self.draft['portfolio_analysis']['new_short']['semantic']['protection_purpose'] = ['DOWNSIDE_SURVIVAL']
        self.assertIn('portfolio', semantics.compare(self.draft, old)['changed'])

    def test_facts_cannot_change_under_review(self):
        (self.project / '操作记录.md').write_text('合成已确认事实已变化')
        with self.assertRaisesRegex(Invalid, 'facts changed'):
            self.commit()

    def test_crash_before_pointer_keeps_prior(self):
        expected = storage.current(self.store)['decision_id']
        def fault(stage):
            if stage == 'before_pointer':
                raise RuntimeError('simulated power loss')
        with self.assertRaises(RuntimeError):
            storage.commit(self.store, self.project, self.draft, self.evidence_dir, self.review, expected, fault)
        self.assertEqual(storage.current(self.store)['decision_id'], expected)
        self.assertNotEqual(self.commit()['decision_id'], expected)

    def test_stale_concurrent_write_rejected(self):
        expected = storage.current(self.store)['decision_id']
        self.commit()
        with self.assertRaisesRegex(Invalid, 'stale'):
            storage.commit(self.store, self.project, self.draft, self.evidence_dir, self.review, expected)

    def test_unpublished_completion_cannot_claim_success(self):
        from v4.hourly import complete_deep
        def fault(stage):
            if stage == 'before_pointer':
                raise RuntimeError('crash')
        with self.assertRaises(RuntimeError):
            storage.commit(self.store, self.project, self.draft, self.evidence_dir, self.review, self.prior['decision_id'], fault)
        path = self.store / 'deep' / (self.draft['draft_id'][7:] + '.json')
        with self.assertRaisesRegex(Invalid, 'unpublished'):
            complete_deep(self.store, path, self.project / '.strategy/market_observation.sqlite3')

    def test_after_pointer_crash_exposes_original_report_for_recovery(self):
        from v4.hourly import complete_deep
        def fault(stage):
            if stage == 'after_pointer':
                raise RuntimeError('crash')
        with self.assertRaises(RuntimeError):
            storage.commit(self.store, self.project, self.draft, self.evidence_dir, self.review, self.prior['decision_id'], fault)
        pinned = storage.current(self.store)
        self.assertIn(pinned['decision_id'], storage.recover(self.store)['reports_not_enqueued'])
        path = self.store / 'deep' / (self.draft['draft_id'][7:] + '.json')
        self.assertEqual(complete_deep(self.store, path, self.project / '.strategy/market_observation.sqlite3')['status'], 'MANUAL_NO_HOURLY_BINDING')

    def test_supplement_preserves_old_version_and_requires_new_cutoff(self):
        spec = {k: deepcopy(v) for k, v in self.evidence.items() if k not in ('schema', 'evidence_id')}
        spec['parent_id'] = self.evidence['evidence_id']
        files = {s['id']: str(self.evidence_dir / 'raw' / (s['raw_hash'][7:] + '.json')) for s in spec['sources']}
        spec['cutoff'] = '2030-03-01T21:00:00+09:00'
        with self.assertRaisesRegex(Invalid, 'backdate'):
            evidence.freeze(spec, files, self.base / 'bad-supplement', self.evidence_dir)
        spec['cutoff'] = '2030-03-01T22:20:00+09:00'
        spec['candidates'] = ['Original candidate weakened by new data']
        new = evidence.freeze(spec, files, self.base / 'supplement', self.evidence_dir)
        self.assertNotEqual(new['evidence_id'], self.evidence['evidence_id'])
        self.assertEqual(read(self.evidence_dir / 'evidence.json'), self.evidence)

    def test_supplement_cannot_overwrite_original_source(self):
        spec = {k: deepcopy(v) for k, v in self.evidence.items() if k not in ('schema', 'evidence_id')}
        spec['parent_id'] = self.evidence['evidence_id']
        spec['sources'][0]['origin'] = 'OTHER'
        with self.assertRaisesRegex(Invalid, 'retain original'):
            evidence.freeze(spec, {}, self.base / 'supplement', self.evidence_dir)

    def test_tampered_raw_and_future_source_rejected(self):
        source = self.evidence['sources'][0]
        path = self.evidence_dir / 'raw' / (source['raw_hash'][7:] + '.json')
        original = path.read_bytes()
        path.write_text('{}')
        with self.assertRaisesRegex(Invalid, 'changed raw'):
            self.commit()
        path.write_bytes(original)
        altered = deepcopy(self.evidence)
        altered['sources'][0]['retrieved_at'] = '2030-03-02T00:00:00Z'
        altered['evidence_id'] = digest({k: v for k, v in altered.items() if k != 'evidence_id'})
        with self.assertRaisesRegex(Invalid, 'time boundary'):
            evidence.validate(altered, self.evidence_dir)

    def candle_sources(self):
        requests = {
            'binance': ('https://data-api.binance.vision/api/v3/klines', {'symbol': 'BTCUSDT', 'interval': '1h', 'limit': 240}),
            'kraken': ('https://api.kraken.com/0/public/OHLC', {'pair': 'XBTUSD', 'interval': 60}),
            'coinbase': ('https://api.exchange.coinbase.com/products/BTC-USD/candles',
                         {'granularity': 3600, 'start': '2030-02-28T11:59:00Z', 'end': '2030-03-01T11:59:00Z'}),
        }
        spec = {k: deepcopy(v) for k, v in self.evidence.items() if k not in ('schema', 'evidence_id')}
        files = {}
        for raw, source in zip(spec['sources'], self.draft['domain']['evidence_matrix']['price_quotes']['sources']):
            endpoint, params = requests[source['venue']]
            payload = read(self.evidence_dir / 'raw' / (raw['raw_hash'][7:] + '.json'))
            payload['retrieval'] = {'endpoint': endpoint, 'parameters': params}
            path = self.base / (raw['id'] + '-candle.json')
            create(path, payload)
            files[raw['id']] = str(path)
            raw.update(url=endpoint, raw_hash=digest(path.read_bytes()))
            source.update(endpoint_url=endpoint + '?' + urlencode(list(params.items())[::-1]), payload_kind='BTC_SPOT_CANDLE_CLOSE')
        self.evidence_dir = self.base / 'candles'
        self.evidence = evidence.freeze(spec, files, self.evidence_dir)
        self.draft['evidence_id'] = self.evidence['evidence_id']

    def test_completed_candle_request_params_bind_both_source_and_price(self):
        self.candle_sources()
        published = self.commit()
        self.assertEqual(published['assessment']['domain']['observation']['raw_prices'], self.draft['domain']['observation']['raw_prices'])

    def test_candle_binding_rejects_different_request_and_wrong_venue(self):
        self.candle_sources()
        source = self.draft['domain']['evidence_matrix']['price_quotes']['sources'][0]
        original = source['endpoint_url']
        for wrong in (original.replace('interval=1h', 'interval=1d'), original.replace('BTCUSDT', 'ETHUSDT'),
                      original + '&interval=1h', original + '&unexpected=1'):
            source['endpoint_url'] = wrong
            with self.assertRaisesRegex(Invalid, 'unrelated raw source'):
                self.commit()
        source['endpoint_url'] = original
        self.draft['evidence_bindings']['/observation/raw_prices/binance'] = self.draft['evidence_bindings']['/observation/raw_prices/kraken']
        self.draft['domain']['observation']['raw_prices']['binance'] = self.draft['domain']['observation']['raw_prices']['kraken']
        with self.assertRaisesRegex(Invalid, 'wrong venue'):
            self.commit()

    def test_ticker_binding_does_not_use_candle_request_fallback(self):
        self.candle_sources()
        self.draft['domain']['evidence_matrix']['price_quotes']['sources'][0]['payload_kind'] = 'BTC_SPOT_TICKER'
        with self.assertRaisesRegex(Invalid, 'unrelated raw source'):
            self.commit()

    def test_same_origin_not_independent(self):
        package = deepcopy(self.evidence)
        refs = [refs[0] for refs in self.draft['evidence_bindings'].values()]
        for source in package['sources']:
            source['origin'] = 'BINANCE'
            source['url'] = 'https://data-api.binance.vision/api/v3/klines'
        self.assertEqual(len(evidence.origins(package, refs)), 1)

    def test_origin_alias_cannot_forge_independence(self):
        package = deepcopy(self.evidence)
        package['sources'][0]['origin'] = 'OTHER_PROVIDER'
        package['evidence_id'] = digest({k: v for k, v in package.items() if k != 'evidence_id'})
        with self.assertRaisesRegex(Invalid, 'self-declare'):
            evidence.validate(package, self.evidence_dir)

    def test_numeric_reference_cannot_launder_different_price(self):
        self.draft['domain']['observation']['raw_prices']['binance'] = '77300'
        with self.assertRaisesRegex(Invalid, 'differs from frozen'):
            self.commit()

    def test_money_inside_authored_narrative_still_requires_binding(self):
        self.draft['outlook']['narrative'] = '建议投入900U'
        with self.assertRaisesRegex(Invalid, 'monetary'):
            report.render(self.draft)

    def test_stable_concept_order_is_not_a_change(self):
        self.draft['outlook']['semantic']['next_evidence'] = ['SPOT_OI_SEQUENCE', 'CROSS_VENUE_RETENTION']
        old = deepcopy(self.draft)
        self.draft['outlook']['semantic']['next_evidence'].reverse()
        self.assertFalse(semantics.compare(self.draft, old)['report_required'])

    def test_equivalent_horizon_units_are_silent(self):
        old = deepcopy(self.draft)
        self.draft['outlook']['semantic']['baseline']['horizon'] = {'unit': 'DAYS', 'minimum': 14, 'maximum': 56}
        self.assertFalse(semantics.compare(self.draft, old)['report_required'])

    def test_budget_changes_report_and_decimal_reformat_does_not(self):
        budget = {'amount_u': '2400', 'unit': 'SPOT_CAPITAL_U', 'eligibility': 'THESIS_ONLY', 'conditions': ['VALUE_REVIEW'], 'invalidations': ['VALUE_MIGRATION'], 'rationale': 'Synthetic risk budget'}
        self.draft['portfolio_analysis']['new_spot']['semantic']['scenario_budget'] = budget
        old = deepcopy(self.draft)
        budget['amount_u'] = '2400.00'
        budget['rationale'] = 'Same budget with new wording'
        self.assertFalse(semantics.compare(self.draft, old)['report_required'])
        budget['amount_u'] = '2500'
        self.assertTrue(semantics.compare(self.draft, old)['report_required'])

    def test_scenario_budget_cannot_become_entry_authorization(self):
        self.draft['portfolio_analysis']['new_spot']['semantic']['scenario_budget'] = {'amount_u': '2400', 'unit': 'SPOT_CAPITAL_U', 'eligibility': 'ELIGIBLE_FOR_REVIEW', 'conditions': ['VALUE_REVIEW'], 'invalidations': ['VALUE_MIGRATION'], 'rationale': 'Synthetic risk budget'}
        with self.assertRaisesRegex(Invalid, 'cannot grant'):
            self.commit()

    def test_prepare_domain_ceil_does_not_put_observation_after_pricing(self):
        from v4.draft import prepare_domain
        value = prepare_domain(self.prior, '2030-03-01T22:00:01+09:00', 'MANUAL_REEVALUATION', {})
        self.assertEqual(value['runtime']['pricing_as_of'], '2030-03-01 22:01 JST')

    def test_current_market_window_cannot_relabel_old_raw(self):
        self.draft['temporal_scope']['market_cutoff'] = '2030-03-01T22:01:00+09:00'
        with self.assertRaises(Invalid):
            self.commit()

    def test_historical_domain_is_explicit_in_current_deep_context(self):
        from v4.hourly import context
        self.draft['temporal_scope']['domain_market_cutoff'] = '2030-03-01T20:00:00+09:00'
        self.commit()
        result = context(self.store, self.project / '.strategy/market_observation.sqlite3')
        deep = result['v4_successful_deep']
        self.assertEqual(deep['domain_baseline']['role'], 'HISTORICAL_INVARIANT_ANCHOR')
        self.assertEqual(deep['current_outlook'], self.draft['outlook'])
        self.assertNotIn('accepted_market_assessment', deep)

    def test_v4_clock_preserves_periodic_and_candidate_gates(self):
        from v4.hourly import review_gate
        review = {'deep_review': {'required': False}}
        old = {'datasets': {'spot': {'rows': [{'time': '2030-03-01T12:00:00Z', 'close': '100'}]}}}
        market = deepcopy(old)
        market['retrievals'] = {'spot': {'retrieved_at': '2030-03-02T12:00:00Z'}}
        self.assertFalse(review_gate(review, market, '2030-03-01T11:00:00Z', old)['deep_eligible'])
        market['datasets']['spot']['rows'].append({'time': '2030-03-02T11:00:00Z', 'close': '101'})
        self.assertTrue(review_gate(review, market, '2030-03-01T11:00:00Z', old)['periodic_review_due'])
        self.assertFalse(review_gate(review, market, '2030-03-02T11:00:00Z', old)['deep_eligible'])
        review['deep_review']['required'] = True
        self.assertTrue(review_gate(review, market, '2030-03-02T11:00:00Z', old)['deep_eligible'])

    def test_older_deep_cannot_replace_newer_silent_baseline(self):
        self.commit()
        self.draft['temporal_scope']['knowledge_cutoff'] = '2030-03-01T20:00:00+09:00'
        with self.assertRaisesRegex(Invalid, 'older deep'):
            self.commit()

    def test_untrusted_source_cannot_verify(self):
        package = deepcopy(self.evidence)
        source = package['sources'][0]
        source['trust'] = 'UNTRUSTED_LEAD'
        ref = next(v[0] for v in self.draft['evidence_bindings'].values() if v[0]['source_id'] == source['id'])
        with self.assertRaisesRegex(Invalid, 'untrusted'):
            evidence.resolve(package, self.evidence_dir, ref, verified=True)

    def test_unavailable_deep_does_not_advance(self):
        expected = storage.current(self.store)['decision_id']
        self.draft['provenance']['agent_id'] = ''
        with self.assertRaises(Invalid):
            self.commit()
        self.assertEqual(storage.current(self.store)['decision_id'], expected)
        self.assertFalse((self.store / 'deep-current.json').exists())

    def test_raw_report_money_and_control_leaks_rejected(self):
        for text in ('建议投入 900U', 'GPT-6/max 的结果', '{{/fact_version/资产配置.md}}'):
            draft = deepcopy(self.draft)
            draft['report']['blocks'][0]['template'] = text
            with self.assertRaises(Invalid):
                report.render(draft)

    def test_compatibility_views_resolve_same_generation(self):
        self.commit()
        expected = {n: digest((self.project / n).read_bytes()) for n in ('策略状态.md', '当前预期计划.md')}
        storage.install_views(self.store, self.project, expected)
        self.assertTrue((self.project / '策略状态.md').is_symlink())
        self.assertEqual((self.project / '策略状态.md').resolve().parent, (self.project / '当前预期计划.md').resolve().parent)


if __name__ == '__main__':
    unittest.main()
