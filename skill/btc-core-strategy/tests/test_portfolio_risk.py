"""Risk assessment may stand alone; advice requires an actual model judgement."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest import mock

import test_closed_loop as legacy
from v4 import closed_loop as loop, run_flow, sensing, storage, reporting, delivery
from v4.common import Invalid, create, digest, instant, read, replace


def risk(core, stamp, response_needed=False):
    return {'summary': '资产风险已评估，当前不需要新增风险应对操作。',
            'basis': '市场短期走弱需要观察；结合持有目的和已确认组合事实，尚不足以推出减仓。',
            'response_needed': response_needed,
            'response_reason': '市场风险迹象与当前组合需要操作分开判断。',
            'actions': [], 'limitations': ['部分账户信息未知，不能声称实时安全。'],
            'market_result_hash': loop.public_hash(core), 'fact_version': core['fact_version'],
            'assessed_at': stamp, 'valid_until': (instant(stamp) + timedelta(days=2)).isoformat(),
            'review_conditions': ['实际风险传导、持有论点或已确认资产情况改变。']}


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.AllocationTests(); self.f.setUp()
        self.value = risk(self.f.core, self.f.stamp)

    def validate(self, value):
        loop.validate_risk(value, self.f.stamp, loop.public_hash(self.f.core), self.f.core['fact_version'])

    def test_no_response_and_unknown_are_complete_without_invented_actions(self):
        for needed in (False, None, True):
            value = deepcopy(self.value); value['response_needed'] = needed
            self.validate(value)
        value['response_needed'] = None; value['limitations'] = []
        with self.assertRaises(Invalid): self.validate(value)

    def test_actions_require_risk_need_and_keep_quantity_coverage_limits(self):
        value = deepcopy(self.value)
        value['actions'] = deepcopy(self.f.value['actions'])
        for needed in (False, None, 1):
            value['response_needed'] = needed
            with self.assertRaises(Invalid): self.validate(value)
        value['response_needed'] = True; self.validate(value)
        value['actions'][1]['decision'] = 'HOLD'
        with self.assertRaises(Invalid): self.validate(value)

    def test_risk_has_its_own_evidence_facts_and_expiry(self):
        for changes in ({'market_result_hash': digest('other')}, {'fact_version': {'changed': 'facts'}},
                        {'valid_until': self.f.stamp}, {'basis': ''}, {'response_reason': ''}):
            value = deepcopy(self.value); value.update(changes)
            with self.assertRaises(Invalid): self.validate(value)
        # The legacy record remains exactly a legacy record, not converted.
        with self.assertRaises(Invalid): self.validate(self.f.value)
        self.f.validate()


class PublicationTests(unittest.TestCase):
    def test_actual_research_mapping_and_publication_with_no_risk_actions(self):
        f = legacy.PublicationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        draft = f.f.draft; draft['revision'] = 7
        draft['recommendations']['portfolio'].pop('allocation_decision')
        draft['recommendations']['portfolio']['risk_assessment'] = risk(draft, draft['temporal_scope']['knowledge_cutoff'])
        draft['recommendations']['portfolio']['strategy_advice'] = []
        policy = read(f.f.store / 'write-policy.json')
        replace(f.f.store / 'write-policy.json', dict(policy, minimum_assessment_revision=7))
        f.c.d.bind()
        request, _, _ = f.c.study(authored=True, independent_output=json.dumps(f.public))
        saved = f.c.d.commit()
        self.assertEqual(saved['assessment']['revision'], 7)
        self.assertEqual(loop.risk(saved['assessment'])['actions'], [])
        self.assertIsNone(loop.allocation(saved['assessment']))
        self.assertEqual(storage.current(f.f.store)['decision_id'], saved['decision_id'])


class HourlyTests(unittest.TestCase):
    def test_new_hour_reads_old_state_without_manufacturing_risk_assessment(self):
        f = legacy.HourlyClosedLoopTests(); f.setUp(); self.addCleanup(f.doCleanups)
        p = run_flow.prepare(f.root, f.f.f.project, 'SYNTHETIC_R7', f.f.d,
                            f.f.raw_file, agent_id='SYNTHETIC_MAIN', workflow_revision=7)
        public = f.public(p)
        frozen = run_flow.advance(p['run'], 'market', f.f.file(public))
        context = read(frozen['portfolio_context'])
        self.assertIn('portfolio_risk', context)
        self.assertIsNone(context['portfolio_risk'])
        form = f.personal(frozen)
        form['allocation_review']['portfolio_risk_hash'] = form['allocation_review'].pop('allocation_decision_hash')
        form['allocation_review']['strategy_advice_hash'] = None
        finished = run_flow.advance(p['run'], 'observe', template_path=frozen['observation_template'], answers_path=f.f.file(form))
        brief = read(finished['report_context'])
        self.assertEqual(brief['market_revision'], 7)
        self.assertIsNone(brief['portfolio_risk'])
        self.assertNotIn('allocation_decision', brief)
        self.assertEqual(brief['applicability']['portfolio']['outcome'], 'EVIDENCE_LIMITED')
        self.assertEqual(storage.current(f.root)['assessment']['revision'], 3)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.FinalReportTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root, self.core, self.stamp = self.f.root, self.f.core, self.f.stamp
        self.core['revision'] = 7
        self.core['recommendations']['portfolio'].pop('allocation_decision')
        self.core['recommendations']['portfolio']['risk_assessment'] = risk(self.core, self.stamp)
        self.core['recommendations']['portfolio']['strategy_advice'] = []
        mock.patch('v4.reporting.context', side_effect=self.context).start()

    def context(self, root):
        ctx = self.f.context(root)
        ctx['market_revision'] = 7
        review = ctx['observation']['review']['allocation_review']
        review['portfolio_risk_hash'] = digest(loop.risk(self.core))
        review['strategy_advice_hash'] = digest(self.core['recommendations']['portfolio']['strategy_advice'])
        review.pop('allocation_decision_hash')
        return ctx

    def spec(self):
        value = self.f.f.spec()
        market = '市场短线风险仍在，长期判断按原证据复核。'
        personal = loop.risk(self.core)['summary']
        value['layout']['body'] = market + '\n\n' + personal
        value['information'] = {'market': market, 'allocation': personal}
        value['layout']['bindings'] = [
            {'text': market, 'scope': 'market', 'refs': ['/market_result/market_assessment/summary']},
            {'text': personal, 'scope': 'personal', 'refs': ['/portfolio_risk/summary']}]
        value['result_binding'] = loop.report_inputs(self.context(self.root))['result_binding']
        return value

    def test_risk_only_report_survives_unknown_delivery_and_real_readback(self):
        spec = self.spec()
        identity, source = self.f.f.queue(spec)
        self.assertEqual(source['body'], spec['layout']['body'])
        self.f.f.begin(identity, source)
        delivery.transition(self.root, identity, 'UNKNOWN')
        self.assertFalse(self.f.f.begin(identity, source)['ready_to_send'])
        confirmed = delivery.transition(self.root, identity, 'CONFIRM', self.f.f.receipt(identity))
        self.assertEqual((confirmed['state'], confirmed['attempts']), ('DELIVERED', 1))
        self.assertEqual(reporting.reported(self.root)['display'], spec['layout']['body'])

    def test_legacy_action_cannot_be_the_source_of_a_new_risk_report(self):
        spec = self.spec()
        spec['layout']['bindings'][1]['refs'] = ['/allocation_decision/summary']
        with self.assertRaises(Invalid): self.f.f.source(spec)

    def test_independent_opportunity_advice_does_not_need_a_risk_trigger(self):
        action = deepcopy(legacy.allocation(self.core, self.stamp)['actions'][0])
        action.update(id='opportunity', decision='INCREASE', scope='Independent equity programme',
                      arrangement='独立策略支持正常分期，实际金额仍受已确认资金限制。', reentry_conditions=[])
        self.core['recommendations']['portfolio']['strategy_advice'] = [action]
        spec = self.spec(); line = action['arrangement']
        spec['layout']['body'] += '\n\n' + line
        spec['layout']['bindings'].append({'text': line, 'scope': 'personal', 'refs': ['/strategy_advice/0/arrangement']})
        _, source = self.f.f.queue(spec)
        self.assertIn(line, source['body'])
        self.assertFalse(loop.risk(self.core)['response_needed'])
        spec = self.spec(); spec['result_binding']['portfolio_risk_hash'] = digest('different')
        with self.assertRaises(Invalid): self.f.f.source(spec)


if __name__ == '__main__':
    unittest.main()
