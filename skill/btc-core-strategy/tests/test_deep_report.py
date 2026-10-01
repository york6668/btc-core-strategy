"""Authored report provenance, strict host suffix handling and summary reuse."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_cycle_workflow as fixture
import test_report_care as care_fixture
from v4 import cycle, research, storage, reporting, run_flow, mobile_report, report_care
from v4.common import Invalid, digest, read, replace

NOTICE = '\n\n::inbox-item{title="周期候选审阅通过" summary="可由主任务核验采纳"}'


class ReviewEnvelopeTests(unittest.TestCase):
    def test_one_json_or_fenced_json_with_known_notification(self):
        value = {'report_narrative': '中文正文包含 {大括号} 和多段\n解释。', 'approved': True}
        body = json.dumps(value, ensure_ascii=False)
        for wrapped in (body, body + NOTICE, '```json\n' + body + '\n```', '```json\n  ' + body + '\n\n```' + NOTICE):
            with self.subTest(wrapped=wrapped):
                self.assertEqual(cycle.parse_review(wrapped), value)

    def test_unknown_content_multiple_objects_or_ambiguous_keys_fail(self):
        for body in ('{} {}', 'prefix {}', '{}\nall approved', '{}\n::archive{}',
                     '{}' + NOTICE + NOTICE, '```json\n{} {}\n```',
                     '{"approved":true,"approved":false}', '{"x":NaN}', '[]',
                     '```json\n{}', '```json\n{}\n``` arbitrary'):
            with self.subTest(body=body), self.assertRaises(Invalid):
                cycle.parse_review(body)


class AuthoredCycleTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CycleWorkflowTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.f.store
        self.enable()

    def enable(self):
        replace(self.root / 'write-policy.json', dict(read(self.root / 'write-policy.json'), deep_report_required=True))

    def publish(self):
        req, saved, spec = self.f.study(authored=True, wrapper=lambda s: '```json\n' + s + '\n```' + NOTICE)
        value = self.f.d.commit(); self.f.observe()
        return req, saved, spec, value

    def spec(self):
        ctx = reporting.context(self.root)
        basis = run_flow.report_template(ctx)['report_basis']
        basis.update(mode='CYCLE_SUMMARY' if ctx['cycle_baseline']['status'] == 'CURRENT' else 'LIMITED_FEEDBACK',
                     verification='已逐项核对完整报告的周期、仓位与条件，小时观察没有改变其投资期限。')
        return {'schema': 'BTC_REPORT_REVIEW_V1', 'context_id': ctx['context_id'], 'action': 'REPORT',
            'purposes': ['MARKET_SUMMARY'], 'reason': '围绕已采纳的周期报告说明本轮验证结果与仓位安排。',
            'next_observation': '继续检查原报告的条件与必要复核时间，实质反证返回深评。',
            'baseline_review': '核对最后展示的周期报告与当前完整正文，未改变其投资分析期限。',
            'applicability_review': '小时数据用于检验原周期解释，并没有独立生成新的中长期结论。',
            'information': {'cycle': '原周期判断继续验证，短期变化未改变其适用边界。'},
            'provenance': self.f.f.draft['provenance'], 'report_basis': basis,
            'layout': {'format': 'MARKET_MOBILE_V1', 'lead': '原周期判断继续验证，现有安排保留。',
                'blocks': [{'kind': 'judgement', 'text': '本轮承接变化仍属于原报告覆盖的情景，暂不改变周期判断。'}],
                'include_recommendations': False,
                'quality_review': {k: '逐段对照深评正文和嗅探证据，摘要没有改变周期、仓位及修正条件。'
                    for k in mobile_report.QUALITY | cycle.REPORT_CHECKS | cycle.SUMMARY_CHECKS}}}

    def test_actual_full_response_retained_and_authored_report_bound(self):
        req, saved, spec, value = self.publish()
        self.assertTrue(saved['analysis'].endswith(NOTICE))
        basis = cycle.baseline(self.root, value['assessment'])
        self.assertEqual(basis['report_narrative'], value['assessment']['conclusion']['narrative'])
        self.assertEqual(basis['report_hash'], digest(basis['report_narrative']))
        self.assertEqual(run_flow.report_brief(reporting.context(self.root))['cycle_baseline']['report_hash'], basis['report_hash'])
        brief = run_flow.report_brief(reporting.context(self.root))
        self.assertEqual(brief['cycle_report_instructions'], cycle.REPORT_SCOPE_REVIEW)
        self.assertEqual(brief['method_references'], cycle.method_references('report'))
        self.assertNotIn('factor_discovery_instructions', brief)
        self.assertNotIn('spot_risk_review_instructions', brief)
        self.assertEqual(brief['current_strategy']['outlook'], value['assessment']['outlook'])
        self.assertEqual(brief['cycle_baseline']['report_narrative_ref'], '/current_strategy/conclusion/narrative')
        self.assertEqual(brief['current_strategy']['conclusion']['narrative'], value['assessment']['conclusion']['narrative'])
        result = reporting.record(self.root, self.spec())
        self.assertIn('原周期判断', reporting.source(self.root, result['review_id'])['body'])
        disclosed = research._load(self.root, 'candidates', saved['candidate_id'])
        output = cycle.parse_review(saved['analysis'])
        for key, change in [('candidate_hash', 'sha256:' + '0' * 64), ('report_narrative', 'Sol自行改写的新周期正文。')]:
            forged = dict(output, **{key: change})
            with self.subTest(key=key), self.assertRaises(Invalid):
                cycle.review_output(req, disclosed, json.dumps(forged), spec)

    def test_legacy_review_readable_but_cannot_be_new_authored_publication(self):
        self.f.study()
        with self.assertRaisesRegex(Invalid, 'actual Astra authored full report'):
            self.f.d.commit()
        self.assertEqual(storage.current(self.root)['decision_id'], self.f.initial['decision_id'])

    def test_trailing_lf_keeps_real_reply_and_publishes_exact_candidate(self):
        narrative = '已批准的第一段。\n\n第二段保留条件。\n'
        self.f.f.draft['conclusion']['narrative'] = narrative
        def extra_lf(body):
            value = json.loads(body)
            value['report_narrative'] += '\n'
            return json.dumps(value, ensure_ascii=False)
        _, saved, _ = self.f.study(authored=True, wrapper=extra_lf)
        self.assertEqual(cycle.parse_review(saved['analysis'])['report_narrative'], narrative + '\n')
        self.assertEqual(saved['cycle_review']['report_narrative'], narrative)
        published = self.f.d.commit()
        self.assertEqual(published['assessment']['conclusion']['narrative'], narrative)
        basis = cycle.baseline(self.root, published['assessment'])
        self.assertEqual(basis['report_hash'], digest(narrative))

    def test_lf_compatibility_does_not_allow_other_report_changes(self):
        narrative = '第一段。\n\n第二段保留条件。\n'
        self.f.f.draft['conclusion']['narrative'] = narrative
        req, saved, spec = self.f.study(authored=True)
        disclosed = research._load(self.root, 'candidates', saved['candidate_id'])
        output = cycle.parse_review(saved['analysis'])
        for changed in ('\n' + narrative, narrative + ' ',
                        narrative.replace('\n\n', '\n'),
                        narrative.replace('保留', '撤回')):
            with self.subTest(changed=changed), self.assertRaises(Invalid):
                cycle.review_output(req, disclosed, json.dumps(dict(output, report_narrative=changed)), spec)

    def test_v2_preserves_authored_value_and_limits_without_new_analysis(self):
        opening='下跌后的区间震荡延续，上涨切换未确认。既有现货安排仍有价值；新增机会因等待成本增加而待复核，缺少利率路径证据影响兑现时间判断。'
        self.f.f.draft['conclusion']['narrative']=opening
        _,_,_,value=self.publish()
        spec=self.spec()
        spec['layout'].update(format='MARKET_MOBILE_V2',lead='{{/assessment/conclusion/narrative}}',
            blocks=[],include_recommendations=True,
            price_status={'lower_spot':'REVIEW_REQUIRED','upper_short':'REVIEW_REQUIRED'})
        result=reporting.record(self.root,spec)
        source=reporting.source(self.root,result['review_id'])
        self.assertEqual(source['body'].split('\n\n')[0],'**'+opening+'**')
        self.assertEqual(source['report_review']['report_basis']['report_hash'],digest(opening))
        changed=deepcopy(reporting.context(self.root))
        changed['metrics']={'intraday':{'price_change_pct':20,'net_buy':99999999}}
        rendered,_=mobile_report.render(dict(spec,context=changed),value)
        self.assertEqual(rendered,source['body'])

    def test_old_published_cycle_requires_authorship_without_rewriting_history(self):
        replace(self.root / 'write-policy.json', dict(read(self.root / 'write-policy.json'), deep_report_required=False))
        self.f.study(); value = self.f.d.commit()
        self.enable()
        self.assertEqual(cycle.baseline(self.root, value['assessment'])['status'], 'REPORT_AUTHORSHIP_PENDING')
        self.assertEqual(storage.current(self.root)['decision_id'], value['decision_id'])
        self.assertTrue(self.f.observe()['deep_eligible'])

    def test_check_time_does_not_expire_actual_approval_but_advice_expiry_does(self):
        self.f.study(authored=True)
        with mock.patch('v4.cycle.now', return_value='2030-03-02T13:01:00Z'):
            self.f.d.commit()
        # The actual advice validity is independent and cannot be renewed by
        # merely reusing the author's approval.
        until=self.f.f.draft['recommendations']['upper_short']['validity']['until']
        with mock.patch('v4.cycle.now', return_value=until):
            with self.assertRaisesRegex(Invalid, 'executable recommendation expired'):
                cycle.validate_publication(self.root,None,self.f.f.draft)

    def test_summary_binding_and_source_review_are_required(self):
        self.publish()
        spec = self.spec(); spec.pop('report_basis')
        with self.assertRaisesRegex(Invalid, 'report source binding'):
            reporting.record(self.root, spec)
        spec = self.spec(); spec['report_basis']['report_hash'] = digest('a different report')
        with self.assertRaisesRegex(Invalid, 'adopted full cycle report'):
            reporting.record(self.root, spec)
        spec = self.spec(); spec['layout']['quality_review'].pop('deep_report_summary')
        with self.assertRaisesRegex(Invalid, 'expression review'):
            reporting.record(self.root, spec)

    def test_first_pending_feedback_cannot_claim_complete_cycle(self):
        self.f.observe()
        spec = self.spec(); spec['report_basis']['mode'] = 'CYCLE_SUMMARY'
        with self.assertRaisesRegex(Invalid, 'current Astra authored report'):
            reporting.record(self.root, spec)
        spec = self.spec(); result = reporting.record(self.root, spec)
        self.assertEqual(reporting.review(self.root, result['review_id'])['report_basis']['report_hash'], None)
        spec = self.spec(); spec['layout']['include_recommendations'] = True
        with self.assertRaisesRegex(Invalid, 'pending cycle review'):
            reporting.record(self.root, spec)

    def test_sensing_reuses_full_report_and_material_counterevidence_returns_to_deep(self):
        _, _, _, value = self.publish()
        basis = cycle.baseline(self.root, value['assessment'])
        self.assertFalse(self.f.observe('SUPPORT_WEAKENED')['deep_eligible'])
        self.assertEqual(reporting.context(self.root)['cycle_baseline']['report_hash'], basis['report_hash'])
        self.assertTrue(self.f.observe('COUNTEREVIDENCE')['deep_eligible'])
        spec = self.spec()
        self.assertEqual(spec['report_basis']['mode'], 'LIMITED_FEEDBACK')
        result = reporting.record(self.root, spec)
        self.assertEqual(reporting.review(self.root, result['review_id'])['report_basis']['report_hash'], basis['report_hash'])


class DailyCycleSummaryTests(unittest.TestCase):
    def test_daily_unchanged_summary_still_needs_real_delivery(self):
        f = care_fixture.ReportCareTests(); f.setUp(); self.addCleanup(f.doCleanups)
        original = f.context
        narrative = '完整周期判断保留，短期波动只用于检验条件。'
        basis = {'status': 'CURRENT', 'result_id': digest('synthetic-result'),
                 'report_narrative': narrative, 'report_hash': digest(narrative)}
        def context(root):
            return dict(original(root), cycle_policy_revision=1, deep_report_policy_revision=1, cycle_baseline=basis)
        with mock.patch('v4.reporting.context', side_effect=context):
            def spec():
                s = f.spec()
                s['report_basis'] = {'result_id': basis['result_id'], 'report_hash': basis['report_hash'],
                    'mode': 'CYCLE_SUMMARY', 'verification': '每日核对完整周期报告，原判断及仓位含义仍适用，未用短线观察另起主线。'}
                s['layout']['quality_review'].update({k: '实际对照完整深评报告，周期、仓位、条件与不确定性均保持原意。'
                    for k in cycle.REPORT_CHECKS | cycle.SUMMARY_CHECKS})
                return s
            i, source = f.queue(spec()); f.begin(i, source)
            self.assertFalse(report_care.status(f.root)['fulfilled'])
            f.confirm(i); self.assertTrue(report_care.status(f.root)['fulfilled'])
            f.clock = '2030-03-02T01:00:00Z'; f.run = 'SYNTHETIC_NEXT_DAY'
            silent = spec(); silent.update(action='SILENT', layout=None)
            reporting.record(f.root, silent)
            f.clock = '2030-03-02T01:31:00Z'
            j, source = f.queue(spec()); self.assertNotEqual(i, j)
            f.begin(j, source); f.confirm(j)
            self.assertTrue(report_care.status(f.root)['fulfilled'])


if __name__ == '__main__':
    unittest.main()
