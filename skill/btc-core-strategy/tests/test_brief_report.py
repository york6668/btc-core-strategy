"""Concise presentation with full internal validation; isolated synthetic facts only."""
from copy import deepcopy
import unittest
from unittest import mock

import test_recommendation_maintenance as fixtures
from test_v4 import seal
from v4 import assessment, brief_report, delivery, report, semantics, storage
from v4.common import Invalid, create, digest, read


class BriefReportTests(unittest.TestCase):
    upgrade = fixtures.RecommendationTests.upgrade
    bind = fixtures.RecommendationTests.bind
    commit = fixtures.RecommendationTests.commit

    def setUp(self):
        fixtures.RecommendationTests.setUp(self)
        self.upgrade()
        self.legacy = self.commit()
        self.old_draft = deepcopy(self.draft)
        self.bind()
        self.compact()

    def compact(self):
        block = lambda text, topics: brief_report.sentence(text, self.draft, topics)
        self.draft['report'] = {
            'format': brief_report.FORMAT,
            'overview': block('现有现货和保护空单维持，暂不新增。', ['decision', 'portfolio']),
            'notes': [block('预期以区间为主，上沿保护、下沿分批投入。', ['outlook', 'recommendations']),
                      block('现货仍承担下跌风险，保留现金。', ['robustness', 'facts'])],
            'risk_notes': [],
        }
        self.review = seal(self.draft)

    def test_complete_internal_assessment_accepts_short_report(self):
        self.draft['outlook']['narrative'] = '内部详细论证不进入简报。' * 40
        self.review = seal(self.draft)
        assessment.validate(self.draft, self.evidence_dir, storage.domain_baseline(self.store, self.legacy), self.project, self.review)
        body = report.render(self.draft)
        self.assertLess(len(body), 450)
        self.assertEqual(sum(line.startswith('|---') for line in body.splitlines()), 1)
        self.assertIn('1,000U名义额', body)
        self.assertIn('1,000U投入', body)
        self.assertNotIn('内部详细论证', body)
        self.assertNotIn('仅现货', body)
        self.assertNotIn('复核期限', body)

    def test_missing_internal_analysis_still_fails(self):
        original = deepcopy(self.draft)
        for field in ('outlook', 'robustness', 'module', 'alternatives', 'recommendation_reason'):
            with self.subTest(field=field):
                self.draft = deepcopy(original)
                if field in ('outlook', 'robustness'):
                    self.draft[field]['narrative'] = ''
                elif field == 'module':
                    self.draft['portfolio_analysis'].pop('new_short')
                elif field == 'alternatives':
                    self.draft['outlook']['semantic']['alternatives'] = []
                else:
                    self.draft['recommendations']['upper_short']['rationale'] = ''
                self.review = seal(self.draft)
                with self.assertRaises(Invalid): self.commit()

    def test_missing_recommendation_price_or_amount_rejected(self):
        for key in ('price_low_usd', 'amount_u'):
            value = deepcopy(self.draft)
            value['recommendations']['upper_short']['tiers'][0].pop(key)
            with self.assertRaises(Invalid): report.render(value)

    def test_withdrawal_is_visible_without_fake_zero_amount(self):
        self.draft['recommendations']['upper_short'].update(status='NOT_RECOMMENDED', tiers=[], rationale='新反证使保护依据失效。')
        self.compact()
        self.draft['report']['notes'][0] = brief_report.sentence('撤回上沿新增空单，保护依据已失效，减少上涨拖累。', self.draft, ['outlook', 'recommendations'])
        self.review = seal(self.draft)
        result = self.commit()
        self.assertTrue(result['report_required'])
        body = (self.store / 'current/report.md').read_text()
        self.assertIn('| 上沿空单 | 当前不建议 | — |', body)
        self.assertIn('撤回上沿新增空单', body)
        self.assertNotIn('0U名义额', body)

    def test_changed_outlook_cannot_reuse_old_sentence_binding(self):
        self.draft['outlook']['semantic']['baseline']['direction'] = 'DOWNSIDE_SKEW'
        self.review = seal(self.draft)
        with self.assertRaisesRegex(Invalid, 'stale concise sentence binding: outlook'): self.commit()

    def test_same_amount_changed_outlook_must_be_covered(self):
        self.draft['outlook']['semantic']['baseline']['direction'] = 'DOWNSIDE_SKEW'
        self.compact()
        self.draft['report']['notes'][0]['bindings'].pop('outlook')
        self.review = seal(self.draft)
        with self.assertRaisesRegex(Invalid, 'omits material change: outlook'): self.commit()
        self.draft['report']['notes'][0] = brief_report.sentence('预期转为下行倾向，买盘承接减弱，现货回撤风险上升。', self.draft, ['outlook', 'recommendations'])
        self.review = seal(self.draft)
        result = self.commit()
        self.assertEqual(result['comparison']['changed'], ['outlook'])
        self.assertIn('预期转为下行倾向', (self.store / 'current/report.md').read_text())

    def test_risk_horizon_and_purpose_deltas_require_short_explanation(self):
        original = deepcopy(self.draft)
        for topic in ('robustness', 'outlook', 'recommendations'):
            with self.subTest(topic=topic):
                self.draft = deepcopy(original)
                if topic == 'robustness':
                    self.draft[topic]['semantic']['adjustment_conditions'] = ['NEW_MATERIAL_RISK']
                elif topic == 'outlook':
                    self.draft[topic]['semantic']['baseline']['horizon']['maximum'] = 12
                else:
                    self.draft[topic]['upper_short']['purpose'] = ['PROTECT_DEEP_DOWNSIDE']
                self.compact()
                comparison = semantics.compare(self.draft, self.old_draft)
                self.assertIn(topic, comparison['changed'])
                for block in self.draft['report']['notes']:
                    block['bindings'].pop(topic, None)
                with self.assertRaisesRegex(Invalid, 'omits material change'):
                    report.validate_changes(self.draft, comparison)

    def test_format_only_commits_silently_without_new_version_or_outbox(self):
        before = {str(p.relative_to(self.store)): digest(p.read_bytes()) for part in ('versions', 'outbox') for p in (self.store / part).rglob('*') if p.is_file()}
        result = self.commit()
        self.assertEqual(result['status'], 'UNCHANGED')
        self.assertEqual(storage.current(self.store)['decision_id'], self.legacy['decision_id'])
        after = {str(p.relative_to(self.store)): digest(p.read_bytes()) for part in ('versions', 'outbox') for p in (self.store / part).rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_paraphrase_and_equivalent_date_do_not_report(self):
        self.draft['report']['overview']['template'] = '现货和空单保持，当前不增减。'
        self.draft['recommendations']['upper_short']['validity']['until'] = '2030-03-08T22:02:00+09:00'
        self.review = seal(self.draft)
        self.assertEqual(self.commit()['status'], 'UNCHANGED')

    def test_legacy_frozen_report_and_delivery_identity_remain_exact(self):
        destination = {'project_id': 'SYNTHETIC', 'thread_id': 'SYNTHETIC'}
        first = delivery.enqueue(self.store, self.legacy['decision_id'], destination)
        before = (self.store / 'current/report.md').read_bytes()
        self.commit()
        with mock.patch('v4.report.render', side_effect=AssertionError('historical delivery must not re-render')):
            again = delivery.enqueue(self.store, self.legacy['decision_id'], destination)
        self.assertEqual(first, again)
        self.assertEqual(before, first['artifact']['body'].encode())

    def test_unbound_price_or_money_cannot_enter_authored_sentence(self):
        for prose in ('考虑在82200做空。', '建议增加1200U名义额。'):
            self.draft['report']['notes'][0]['template'] = prose
            with self.assertRaisesRegex(Invalid, 'literal numbers'): report.render(self.draft)

    def test_full_narrative_and_extra_normal_sentences_are_rejected(self):
        original = deepcopy(self.draft)
        self.draft['report']['notes'] *= 2
        with self.assertRaisesRegex(Invalid, 'at most two'): report.render(self.draft)
        self.draft = deepcopy(original)
        self.draft['report']['notes'][0]['template'] = '第一句。第二句。'
        with self.assertRaisesRegex(Invalid, 'one sentence'): report.render(self.draft)
        self.draft['report']['notes'][0]['template'] = '完整分析继续留在内部，' * 30
        with self.assertRaisesRegex(Invalid, 'too long'): report.render(self.draft)

    def test_nonzero_current_action_requires_exact_amount_binding(self):
        self.draft['domain']['portfolio_decision']['new_spot']['amount_u'] = '500'
        with self.assertRaisesRegex(Invalid, 'nonzero formal amount'): report.render(self.draft)
        self.draft['report']['overview']['template'] = '当前新增现货{{/domain/portfolio_decision/new_spot/amount_u}}U，其他安排维持。'
        self.assertIn('新增现货500U', report.render(self.draft))

    def test_exceptional_risk_can_add_a_short_bound_note(self):
        self.draft['report']['risk_notes'] = [brief_report.sentence('风险集中程度上升，现有保护不能完整抵消篮子损失。', self.draft, ['robustness'])]
        self.assertIn('重要风险：风险集中程度上升', report.render(self.draft))
        self.draft['report']['risk_notes'][0]['bindings'] = self.draft['report']['overview']['bindings']
        with self.assertRaisesRegex(Invalid, 'risk binding'): report.render(self.draft)

    def test_unknown_margin_keeps_internal_limit_without_expanding_report(self):
        self.draft['recommendations']['upper_short']['tiers'][0]['capital_u'] = None
        self.draft['recommendations']['portfolio']['capital_envelope_u'] = None
        self.compact()
        self.draft['report']['notes'][1] = brief_report.sentence('空单保证金尚未确定。', self.draft, ['robustness', 'facts', 'recommendations'])
        self.review = seal(self.draft)
        self.assertTrue(self.commit()['report_required'])
        body = report.render(self.draft)
        self.assertIn('1,000U名义额', body)
        self.assertIn('空单保证金尚未确定', body)
        self.assertNotIn('累计资金占用', body)

    def test_production_policy_rejects_legacy_new_writes_but_reads_history(self):
        create(self.store / 'write-policy.json', {'minimum_assessment_revision': 2, 'required_report_format': brief_report.FORMAT})
        self.assertEqual(storage.current(self.store)['decision_id'], self.legacy['decision_id'])
        with self.assertRaisesRegex(Invalid, 'requires concise report'):
            storage.enforce_write_policy(self.store, self.old_draft, self.legacy)
        self.assertEqual(self.commit()['status'], 'UNCHANGED')

    def test_missing_overview_binding_and_unknown_format_fail(self):
        self.draft['report']['overview']['bindings'].pop('portfolio')
        with self.assertRaisesRegex(Invalid, 'overview must bind'): report.render(self.draft)
        self.compact()
        self.draft['report']['format'] = 'UNKNOWN'
        with self.assertRaisesRegex(Invalid, 'unsupported report format'): report.render(self.draft)

    def test_cli_validate_applies_format_and_changed_meaning_checks(self):
        import contextlib
        import io
        import strategy_v4
        from v4.common import replace
        draft_path, review_path = self.base / 'brief-draft.json', self.base / 'brief-review.json'
        create(self.store / 'write-policy.json', {'minimum_assessment_revision': 2, 'required_report_format': brief_report.FORMAT})
        args = ['--project', str(self.project), '--root', str(self.store), 'validate', '--draft', str(draft_path), '--review', str(review_path), '--evidence', str(self.evidence_dir)]
        def run(value):
            review = seal(value)
            replace(draft_path, value); replace(review_path, review)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return strategy_v4.main(args)
        self.assertEqual(run(self.old_draft), 2)
        self.assertEqual(run(self.draft), 0)
        self.draft['outlook']['semantic']['baseline']['direction'] = 'DOWNSIDE_SKEW'
        self.compact()
        self.draft['report']['notes'][0]['bindings'].pop('outlook')
        self.assertEqual(run(self.draft), 2)


if __name__ == '__main__':
    unittest.main()
