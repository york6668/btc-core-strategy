"""Revision 4 publication/reuse/report isolation with synthetic assets and raw data."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_workflow_main as base
from v4 import brief_report, main_flow, sensing, semantics, storage
from v4.common import Invalid, create, digest, read


class DynamicMainTests(unittest.TestCase):
    def setUp(self):
        self.f = base.MainTests('test_sol_publishes_complete_strategy_without_astra_or_report')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def upgrade(self, items=None):
        f = self.f
        f.draft['revision'] = 4
        if items is None:
            items = [{'id': 'synthetic_unlisted_mechanism', 'name': '目录外合成机制',
                      'assessment': '样本仅支持新的候选解释；尚不足以改变现有建议。',
                      'change_reason': '合成新增证据与既有解释存在偏差，需要持续检验。',
                      'refs': deepcopy(f.draft['temporal_scope']['current_market_refs']), 'counter_refs': []}]
        f.draft['factor_assessment'] = {'items': items, 'summary': '本轮按证据选择候选解释，不设固定数量或权重。'}
        f.draft['section_provenance']['factor_assessment'] = {
            'mode': 'ASSESSED', 'decision_id': None,
            'assessed_at': f.draft['temporal_scope']['knowledge_cutoff'],
            'evidence_id': f.draft['evidence_id'], 'explanation': 'Synthetic model-authored factor review.'}
        self.bind()

    def bind(self):
        f = self.f
        kwargs = {'evidence_dir': f.evidence_dir} if f.draft.get('revision', 1) >= 4 else {}
        ctx = sensing.context(f.store, f.project/'.strategy/market_observation.sqlite3', f.project, f.raw, **kwargs)
        f.draft['maintenance_binding'] = {'context_id': ctx['context_id'], 'market_evidence_id': f.raw['evidence_id']}
        f.review = base.seal(f.draft)
        return ctx

    def commit(self, draft=None, review=None):
        f = self.f
        draft = f.draft if draft is None else draft
        review = base.seal(draft) if review is None else review
        return storage.commit(f.store, f.project, draft, f.evidence_dir, review, storage.current(f.store)['decision_id'])

    def assembly(self, updates):
        f = self.f
        previous = storage.current(f.store)['assessment']
        revision = 4 if previous['revision'] == 4 or 'factor_assessment' in updates else 3
        components = {'updates': updates, 'section_reviews': dict.fromkeys(main_flow.sections_for_revision(revision), 'Synthetic evidence applicability reviewed.'),
                      'evidence_bindings': deepcopy(previous['evidence_bindings']),
                      'domain_market_cutoff': previous['temporal_scope']['domain_market_cutoff'], 'stop_reason': previous['stop_reason']}
        checks = {k: f.review[k] for k in ('checks', 'unresolved_issues', 'material_new_outlook')}
        return main_flow.assemble(f.store, f.project, components, f.evidence_dir, f.raw,
                                  dict(previous['provenance'], run_ref='SYNTHETIC_REVISION4_ASSEMBLY'), checks)

    def test_first_revision4_requires_explicit_model_factor_content(self):
        self.upgrade()
        del self.f.draft['factor_assessment']
        with self.assertRaises(Invalid):
            self.commit()
        self.assertEqual(storage.current(self.f.store)['decision_id'], self.f.prior['decision_id'])

    def test_empty_dynamic_selection_is_valid_without_fixed_coverage(self):
        self.upgrade(items=[])
        value = self.commit()
        self.assertEqual(value['assessment']['factor_assessment']['items'], [])
        self.assertEqual(value['assessment']['provenance']['model'], 'gpt-5.6-sol')
        self.assertFalse((self.f.store/'deep-current.json').exists())

    def test_unlisted_factor_changes_publish_internal_state_without_report(self):
        self.upgrade()
        first = self.commit()
        self.f.report_baseline(first)
        self.bind()
        item = self.f.draft['factor_assessment']['items'][0]
        item['assessment'] = '新的反证削弱该候选解释；保持已采纳的整体价值结论。'
        item['change_reason'] = '竞争解释比原候选更能说明合成市场序列。'
        second = self.commit()
        self.assertNotEqual(first['decision_id'], second['decision_id'])
        self.assertFalse(second['report_required'])
        self.assertEqual(second['comparison']['changed'], [])
        self.assertEqual(storage.recover(self.f.store)['reports_not_enqueued'], [])
        self.assertEqual(second['assessment']['recommendations'], first['assessment']['recommendations'])

    def test_raw_factor_binding_changes_do_not_force_publication(self):
        self.upgrade()
        first = self.commit()
        self.f.report_baseline(first)
        self.bind()
        self.f.draft['factor_assessment']['items'][0]['counter_refs'] = deepcopy(self.f.draft['temporal_scope']['current_market_refs'])
        result = self.commit()
        self.assertEqual(result['status'], 'UNCHANGED')
        self.assertEqual(result['decision_id'], first['decision_id'])
        self.assertFalse(result['report_required'])

    def test_retired_factors_need_no_catalog_mutation(self):
        self.upgrade()
        first = self.commit()
        self.f.report_baseline(first)
        self.bind()
        self.f.draft['factor_assessment'] = {'items': [], 'summary': '前期候选已被反证排除，当前无需采用它；整体价值含义维持。'}
        result = self.commit()
        self.assertNotEqual(result['decision_id'], first['decision_id'])
        self.assertFalse(result['report_required'])
        self.assertEqual(result['assessment']['factor_assessment']['items'], [])

    def test_factor_reference_corruption_prevents_publication(self):
        self.upgrade()
        self.f.draft['factor_assessment']['items'][0]['refs'][0]['value_hash'] = digest('unrelated synthetic value')
        with self.assertRaises(Invalid):
            self.commit()
        self.assertFalse((self.f.store/'main-current.json').exists())

    def test_actual_factor_reuse_keeps_provenance_and_rejects_edited_payload(self):
        self.upgrade()
        first = self.commit()
        draft, review = self.assembly({})
        p = draft['section_provenance']['factor_assessment']
        self.assertEqual(p['mode'], 'REUSED')
        self.assertEqual(p['decision_id'], first['decision_id'])
        self.assertEqual(p['assessed_at'], first['assessment']['temporal_scope']['knowledge_cutoff'])
        self.assertEqual(draft['revision'], 4)
        self.assertEqual(self.commit(draft, review)['status'], 'UNCHANGED')
        draft['factor_assessment']['summary'] = 'A different assessment cannot claim historical authorship.'
        with self.assertRaisesRegex(Invalid, 'reused section changed'):
            self.commit(draft)

    def test_revision3_assembly_migrates_only_with_authored_factors(self):
        first = self.f.commit()
        draft, _ = self.assembly({})
        self.assertEqual(draft['revision'], 3)
        self.assertNotIn('factor_assessment', draft)
        factor = {'items': [], 'summary': '首次完成因子评估，没有足够证据采用新的候选。'}
        draft, review = self.assembly({'factor_assessment': factor})
        self.assertEqual(draft['revision'], 4)
        self.assertEqual(draft['section_provenance']['factor_assessment']['mode'], 'ASSESSED')
        self.assertIsNone(draft['section_provenance']['factor_assessment']['decision_id'])
        result = self.commit(draft, review)
        self.assertNotEqual(result['decision_id'], first['decision_id'])
        self.assertEqual(result['assessment']['factor_assessment'], factor)

    def test_revision4_cannot_downgrade_or_assign_specialist_as_owner(self):
        self.upgrade()
        first = self.commit()
        self.bind()
        self.f.draft['provenance']['model'] = 'gpt-6-astra'
        with self.assertRaisesRegex(Invalid, 'Sol/high'):
            self.commit()
        self.f.draft['revision'] = 3
        with self.assertRaisesRegex(Invalid, 'replay-only'):
            self.commit()
        self.assertEqual(storage.current(self.f.store)['decision_id'], first['decision_id'])

    def test_adopted_meaning_still_reports_and_latest_revision4_is_recovered(self):
        self.upgrade()
        first = self.commit()
        self.f.report_baseline(first)
        self.bind()
        self.f.draft['conclusion']['business_meaning']['value'] = '合成机制变化已影响未来价值路径，风险安排需要更新。'
        second = self.commit()
        self.assertEqual(second['comparison']['changed'], ['conclusion'])
        self.assertTrue(second['report_required'])
        self.bind()
        self.f.draft['factor_assessment']['summary'] = '补充候选解释，已采纳业务含义与上一版相同。'
        third = self.commit()
        self.assertEqual(storage.recover(self.f.store)['reports_not_enqueued'], [third['decision_id']])
        accepted = third['assessment']
        layout = {'format': 'CONCISE_V1',
                  'overview': brief_report.sentence('合成风险判断已更新，继续按条件维护两侧建议。', accepted, set(semantics.stable(accepted))),
                  'notes': [], 'risk_notes': []}
        source = main_flow.report_source(self.f.store, third['decision_id'], layout, [], {})
        self.assertEqual(source['semantic_hash'], digest(semantics.stable(accepted)))
        self.assertNotIn('目录外合成机制', source['body'])
        self.assertEqual(second['assessment']['recommendations'], first['assessment']['recommendations'])

    def test_equity_reentry_and_hedge_resize_change_meaning_without_btc_repricing(self):
        self.upgrade(); first = self.commit(); self.f.report_baseline(first)
        self.bind()
        meaning = self.f.draft['conclusion']['business_meaning']
        meaning['equity'] = '合成盈利反证支持减持已有纳指风险仓，盈利修复且估值恢复后分批回补；标普定投另行核验。'
        meaning['hedge_after_reduction'] = '现货减少后先核对保护空单是否过量；未取得实际数量前不推算净敞口或本金安全。'
        meaning['allocation'] = '调整目标比例为待采纳配置建议，当期占用与目标资金分别核算。'
        second = self.commit()
        self.assertTrue(second['report_required'])
        self.assertEqual(second['comparison']['changed'], ['conclusion'])
        self.assertEqual(second['assessment']['recommendations'], first['assessment']['recommendations'])
        self.assertEqual(second['assessment']['fact_version'], first['assessment']['fact_version'])
        self.assertEqual(second['assessment']['domain']['portfolio_decision'], first['assessment']['domain']['portfolio_decision'])
        self.bind()
        self.assertEqual(self.commit()['status'], 'UNCHANGED')


if __name__ == '__main__':
    unittest.main()
