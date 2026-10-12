"""Isolated interface regressions; these do not certify model semantics."""
from copy import deepcopy
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest import mock


import test_closed_loop as fixture
import test_workflow_research as native
import test_portfolio_risk as risk_fixture
from v4 import authored_report, closed_loop as loop, cycle, market_state, reporting, research, run_flow
from v4.common import Invalid, fact_inputs, facts, read


class FactAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        for name in ('资产配置.md', '操作记录.md', '纠正记录.md'):
            (self.project / name).write_text('# Synthetic isolated facts\n', encoding='utf-8')

    def test_missing_one_file_preserves_other_versions_without_zero_holdings(self):
        before = facts(self.project); (self.project / '资产配置.md').unlink()
        current = fact_inputs(self.project)
        self.assertIsNone(current['fact_version']['资产配置.md'])
        self.assertEqual(current['files']['资产配置.md']['reason'], 'FileNotFoundError')
        self.assertIsNone(current['files']['资产配置.md']['path'])
        self.assertEqual(current['fact_version']['操作记录.md'], before['操作记录.md'])
        self.assertFalse((self.project / '资产配置.md').exists())

    def test_real_directory_and_invalid_encoding_are_explicit_read_failures(self):
        p = self.project / '资产配置.md'; p.unlink(); p.mkdir()
        self.assertEqual(fact_inputs(self.project)['files']['资产配置.md']['reason'], 'IsADirectoryError')
        p.rmdir(); p.write_bytes(b'\xff\xfe')
        self.assertEqual(fact_inputs(self.project)['files']['资产配置.md']['reason'], 'UnicodeDecodeError')
        self.assertIsNone(facts(self.project)['资产配置.md'])

    @unittest.skipIf(os.geteuid() == 0, 'root bypasses ordinary file permission denial')
    def test_real_permission_denial_is_a_gap_not_a_zero_fact(self):
        p = self.project / '操作记录.md'; p.chmod(0)
        try:
            current = fact_inputs(self.project)
            self.assertEqual(current['files']['操作记录.md']['reason'], 'PermissionError')
            self.assertIsNone(current['fact_version']['操作记录.md'])
        finally:
            p.chmod(0o600)


class PublicContinuityTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.HourlyClosedLoopTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_actual_missing_file_does_not_block_public_prepare_freeze_or_observe(self):
        p, frozen, public = self.f.freeze(); before = read(p['context'])
        (self.f.f.f.project / '资产配置.md').unlink()
        changed = self.f.prepare(self.f.f.f.base / 'missing-personal')
        self.assertEqual(read(changed['context']), before)
        refreshed = run_flow.advance(p['run'], 'market', self.f.f.file(public))
        self.assertEqual(refreshed['market_result_hash'], frozen['market_result_hash'])
        portfolio = read(refreshed['portfolio_context'])
        self.assertIsNone(portfolio['fact_version']['资产配置.md'])
        self.assertNotIn('资产配置.md', portfolio['fact_files'])
        self.assertEqual(portfolio['fact_availability']['资产配置.md']['status'], 'UNAVAILABLE')
        observed = run_flow.advance(p['run'], 'observe', template_path=refreshed['observation_template'],
                                    answers_path=self.f.f.file(self.f.personal(refreshed)))
        self.assertIn('observation_id', observed)
        self.assertFalse((self.f.f.f.project / '资产配置.md').exists())

    def test_actual_unreadable_owner_preserves_public_context_and_current_gap(self):
        p, frozen, public = self.f.freeze(); before = read(p['context'])
        owner = self.f.f.f.project / '纠正记录.md'; owner.unlink(); owner.mkdir()
        prepared = self.f.prepare(self.f.f.f.base / 'unreadable-personal')
        self.assertEqual(read(prepared['context']), before)
        refreshed = run_flow.advance(p['run'], 'market', self.f.f.file(public))
        self.assertEqual(refreshed['market_result_hash'], frozen['market_result_hash'])
        self.assertEqual(read(refreshed['portfolio_context'])['fact_availability']['纠正记录.md']['reason'], 'IsADirectoryError')

    def test_fact_change_after_snapshot_keeps_material_consistent_and_rejects_stale_observation(self):
        p, frozen, public = self.f.freeze()
        owner = self.f.f.f.project / '资产配置.md'
        captured = {}
        def snapshot_then_change(project):
            result = fact_inputs(project)
            captured.update(result)
            owner.write_text('# Changed isolated personal snapshot\n', encoding='utf-8')
            return result
        with mock.patch.object(run_flow, 'fact_inputs', side_effect=snapshot_then_change) as read_once:
            refreshed = run_flow.advance(p['run'], 'market', self.f.f.file(public))
        self.assertEqual(read_once.call_count, 1)
        portfolio = read(refreshed['portfolio_context'])
        template = read(refreshed['observation_template'])
        context = read(template['_binding']['context_file'])
        self.assertEqual(portfolio['fact_version'], captured['fact_version'])
        self.assertEqual(context['identity']['fact_version'], captured['fact_version'])
        self.assertEqual(portfolio['fact_availability'], captured['files'])
        self.assertNotEqual(facts(self.f.f.f.project), captured['fact_version'])
        self.assertEqual(refreshed['market_result_hash'], frozen['market_result_hash'])
        with self.assertRaisesRegex(Invalid, 'facts changed'):
            run_flow.advance(p['run'], 'observe', template_path=refreshed['observation_template'],
                             answers_path=self.f.f.file(self.f.personal(refreshed)))

    def test_public_phase_task_has_no_portfolio_assignment(self):
        p = self.f.prepare(); ctx = read(p['context'])
        self.assertIn(loop.MARKET_INSTRUCTIONS, ctx['instructions'])
        self.assertNotIn(loop.INSTRUCTIONS, ctx['instructions'])
        self.assertNotIn('fact_availability', ctx)


    def test_public_entry_and_actual_candidate_review_receive_full_cycle_thesis(self):
        prepared = self.f.prepare()
        context = read(prepared['context'])
        self.assertIn(market_state.CYCLE_THESIS_REVIEW, context['instructions'])
        # A new public projection also receives the method without an adopted thesis.
        new = market_state.market_projection({'identity': {}, 'cycle_baseline': {}})
        self.assertNotIn('adopted_research', new)
        self.assertIn(market_state.CYCLE_THESIS_REVIEW, new['instructions'])
        checked = run_flow.advance(prepared['run'], 'market',
                                  self.f.f.file(self.f.public(prepared)), check_only=True)
        material = read(checked['review_material'])
        self.assertIn(market_state.CYCLE_THESIS_REVIEW, material['instructions'])
        self.assertEqual(material['market_candidate'], read(checked['market_candidate']))
        self.assertFalse((Path(prepared['run']).parent / 'market-result.json').exists())

    def test_method_update_does_not_rebind_an_existing_public_freeze(self):
        prepared, frozen, public = self.f.freeze()
        original = Path(frozen['market_result']).read_bytes()
        with mock.patch.object(market_state, 'INSTRUCTIONS', 'Synthetic later method revision.'):
            recovered = run_flow.advance(prepared['run'], 'market', self.f.f.file(public))
        self.assertEqual(recovered['market_result_hash'], frozen['market_result_hash'])
        self.assertEqual(Path(recovered['market_result']).read_bytes(), original)


class PublicCycleMethodTransportTests(unittest.TestCase):
    def setUp(self):
        self.f = native.ResearchWorkflowTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_native_cycle_assignment_keeps_method_after_public_replacement(self):
        from v4.common import replace
        replace(self.f.store / 'write-policy.json', {'minimum_assessment_revision': 5})
        spec = deepcopy(self.f.spec); spec.update(issue_key=cycle.ISSUE_KEY, scope=cycle.SCOPE)
        req = self.f.request(spec)
        original = (self.f.store / req['independent_input_path']).read_bytes()
        payload = research.read_input(self.f.store, req['request_id'])['payload']
        self.assertIn(market_state.CYCLE_THESIS_REVIEW, payload['cycle_assignment']['instructions'])
        self.assertIn(market_state.CYCLE_THESIS_REVIEW, payload['market_result_contract']['instructions'])
        self.assertNotIn('portfolio_waiting', payload['cycle_assignment']['required_coverage'])
        self.assertNotIn('fact_files', payload)
        with mock.patch.object(market_state, 'INSTRUCTIONS', 'Synthetic later method revision.'):
            reread = research.read_input(self.f.store, req['request_id'])['payload']
        self.assertEqual(reread, payload)
        self.assertEqual((self.f.store / req['independent_input_path']).read_bytes(), original)

    def test_report_consumer_gets_turning_review_without_new_research_assignment(self):
        ctx = {'market_revision': 7, 'deep_report_policy_revision': 1, 'identity': {'fixed': 'synthetic'}}
        original = deepcopy(ctx)
        actual = reporting.authoring_guidance(ctx)
        self.assertIn(market_state.MOMENTUM_REPORT_REVIEW, actual['result_instructions'])
        self.assertIn(market_state.MOMENTUM_REPORT_REVIEW, actual['cycle_report_instructions'])
        self.assertNotIn(market_state.CYCLE_THESIS_REVIEW, actual['result_instructions'])
        self.assertEqual(ctx, original)


class BoundedPersonalResultTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.FinalReportTests(); self.f.setUp()
        self.addCleanup(mock.patch.stopall); self.addCleanup(self.f.doCleanups)

    def context(self, fresh=False, outcome='EVIDENCE_LIMITED'):
        ctx = self.f.context(self.f.root); ctx['report_contract_revision'] = 3
        ctx['market_revision'] = 7; core = ctx['current_strategy']
        # Only constructors in temporary fixtures; never production account files.
        core['revision'] = 7; portfolio = core['recommendations']['portfolio']
        portfolio.pop('allocation_decision')
        assessed = risk_fixture.risk(core, self.f.stamp)
        portfolio['risk_assessment'] = assessed
        portfolio['strategy_advice'] = []
        ctx['identity']['fact_version'] = dict(core['fact_version'], **{'资产配置.md': None})
        if fresh:
            assessed['fact_version'] = deepcopy(ctx['identity']['fact_version'])
        from v4.common import digest
        ctx['observation']['review']['allocation_review'] = {
            'outcome': outcome, 'portfolio_risk_hash': digest(assessed), 'strategy_advice_hash': digest([]),
            'fact_version': deepcopy(ctx['identity']['fact_version']),
            'explanation': '已完成不依赖缺失配置数量的本轮判断；具体规模仍未决。',
            'limitations': ['Existing independently identified execution limit.']}
        return ctx

    def test_stale_actions_removed_but_fresh_review_and_revise_reason_survive(self):
        ctx = self.context(outcome='REVISE'); before = deepcopy(ctx)
        result = loop.report_inputs(ctx)
        self.assertIsNone(result['portfolio_risk']); self.assertIsNone(result['strategy_advice'])
        current = result['applicability']['portfolio']
        self.assertEqual(current['outcome'], 'REVISE')
        self.assertIn('本轮判断', current['explanation'])
        self.assertIn('Existing independently identified execution limit.', current['limitations'])
        self.assertTrue(any('Local asset facts changed' in x for x in current['limitations']))
        self.assertTrue(any('资产配置.md' in x for x in current['limitations']))
        self.assertTrue(all(not p['tiers'] for p in result['execution_plans'].values()))
        self.assertEqual(ctx, before)

    def test_fresh_bounded_result_is_retained_without_assumed_global_safety(self):
        ctx = self.context(fresh=True)
        result = loop.report_inputs(ctx)
        self.assertEqual(result['portfolio_risk'], ctx['current_strategy']['recommendations']['portfolio']['risk_assessment'])
        self.assertEqual(result['strategy_advice'], [])
        self.assertEqual(result['applicability']['portfolio']['outcome'], 'EVIDENCE_LIMITED')
        self.assertIn('本轮判断', result['applicability']['portfolio']['explanation'])
        risk = result['portfolio_risk']; risk['limitations'] = []
        with self.assertRaisesRegex(Invalid, 'bounded risk limitations'):
            loop.validate_risk(risk, self.f.stamp)


class AuthoredIndependenceContractTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.FinalReportTests(); self.f.setUp()
        self.addCleanup(mock.patch.stopall); self.addCleanup(self.f.doCleanups)

    def spec(self):
        value = self.f.spec(); ctx = self.f.context(self.f.root)
        ctx.update(report_contract_revision=3)
        ctx.pop('cycle_policy_revision', None); ctx.pop('deep_report_policy_revision', None)
        value['context'] = ctx
        value['result_binding'] = loop.report_inputs(ctx)['result_binding']
        paragraphs = value['layout']['body'].split('\n\n')
        # A faithful paraphrase is valid; there is no literal-summary prefix rule.
        paragraphs[0] = '公开结论：短期结构承压；较长周期与反向趋势的支持仍有限。'
        value['layout']['body'] = '\n\n'.join(paragraphs)
        value['layout']['bindings'] = [
            {'text': paragraphs[0], 'scope': 'market', 'refs': ['/market_result/market_assessment/summary']},
            {'text': paragraphs[1], 'scope': 'personal', 'refs': ['/allocation_decision/summary', '/allocation_decision/actions/0/arrangement', '/allocation_decision/actions/1/arrangement']}]
        value['layout']['quality_review']['market_independence'] = '对照公开源命题与完整正文，个人安排只承接市场结果；本测试仅核验接口，语义另行验收。'
        return value

    def test_public_paraphrase_and_downstream_personal_body_preserved_verbatim(self):
        value = self.spec()
        self.assertEqual(authored_report.render(value, self.f.f.f.f.decision)[0], value['layout']['body'])

    def test_non_numeric_unbound_closing_is_not_allowed(self):
        value = self.spec(); value['layout']['body'] += '\n\n因此市场整体应该等待。'
        with self.assertRaisesRegex(Invalid, 'all report prose'):
            authored_report.render(value, self.f.f.f.f.decision)

    def test_personal_choice_cannot_be_the_first_public_conclusion(self):
        value = self.spec(); pieces = value['layout']['body'].split('\n\n')
        value['layout']['body'] = '\n\n'.join(reversed(pieces))
        with self.assertRaisesRegex(Invalid, 'independent public conclusion'):
            authored_report.render(value, self.f.f.f.f.decision)

    def test_mixed_personal_ref_cannot_be_declared_public(self):
        value = self.spec(); value['layout']['bindings'][0]['refs'].append('/allocation_decision/summary')
        with self.assertRaisesRegex(Invalid, 'cannot bind personal'):
            authored_report.render(value, self.f.f.f.f.decision)

    def test_old_frozen_contract_keeps_original_numeric_binding_compatibility(self):
        value = self.spec(); value['context']['report_contract_revision'] = 2
        value['layout']['quality_review'].pop('market_independence')
        value['layout']['body'] += '\n\n旧冻结表达继续原契约回读。'
        self.assertEqual(authored_report.render(value, self.f.f.f.f.decision)[0], value['layout']['body'])


class KnownDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.f = native.ResearchWorkflowTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_known_prior_candidate_read_cannot_be_relabelled_new_independent(self):
        req, saved, spec = self.f.finished()
        path = self.f.trace(req, 'NEW_PUBLIC', '2030-03-01T13:03:11Z', '2030-03-01T13:03:12Z')
        with self.assertRaisesRegex(Invalid, 'already read personal or candidate'):
            research.independent(self.f.store, req['request_id'], 'SYNTHETIC_AGENT', path, 'NEW_PUBLIC')

    def test_future_disclosure_does_not_invalidate_an_earlier_blind_phase(self):
        req, saved, spec = self.f.finished()
        path = self.f.base / 'agent.jsonl'
        turn = research._completed_turn(path, 'SYNTHETIC_AGENT', 'FIRST')
        self.assertEqual(turn['prior_personal_reads'], [])
        result = research.independent(self.f.store, req['request_id'], 'SYNTHETIC_AGENT', path, 'FIRST')
        self.assertEqual(result['input_id'], req['independent_input_id'])


if __name__ == '__main__':
    unittest.main()
