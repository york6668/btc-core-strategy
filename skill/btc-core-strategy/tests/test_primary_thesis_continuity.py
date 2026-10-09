"""Synthetic transport/notification contracts; no model-semantic acceptance."""
from copy import deepcopy
import unittest
from unittest import mock

import test_portfolio_risk as risk_fixture
import test_report_care as care_fixture
from v4 import closed_loop, market_state, reporting, report_care, run_flow
from v4.common import Invalid


class ResearchArgumentTests(unittest.TestCase):
    def setUp(self):
        self.f = risk_fixture.ReportTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_adopted_argument_survives_hourly_overlay_without_locking_new_evidence(self):
        ctx = self.f.context(self.f.root)
        formal = deepcopy(self.f.core['outlook']['market_assessment'])
        latest = deepcopy(formal)
        latest['summary'] = 'Synthetic new evidence weakens the old driver; actual review remains necessary.'
        ctx.update(revision=5, current_core={'decision_id':'SYNTHETIC_ADOPTED',
            'market_cutoff':self.f.stamp, 'outlook':deepcopy(self.f.core['outlook'])},
            previous_hour={'review':{'market_assessment':latest,'dispute_zones':[]}})
        view = market_state.market_projection(ctx)
        self.assertEqual(view['adopted_research']['market_assessment'], formal)
        self.assertEqual(view['previous_market_observation']['market_assessment'], latest)
        self.assertNotEqual(formal, latest)
        self.assertEqual(ctx['current_core']['outlook']['market_assessment'], formal)

    def test_report_restores_exact_public_argument_without_mixed_personal_research(self):
        ctx = self.f.context(self.f.root)
        ctx['report_contract_revision'] = 2
        basis = ctx.setdefault('cycle_baseline', {})
        basis.update(result_id='SYNTHETIC_RESEARCH', market_cutoff=self.f.stamp,
            market_report='Verified public structure, pricing rationale and counterevidence.',
            report_narrative='PRIVATE_MIXED_REPORT_SENTINEL')
        ctx['current_strategy']['portfolio_analysis']['private'] = 'PRIVATE_ACCOUNT_SENTINEL'
        actual = run_flow.report_brief(ctx)
        public = actual['market_result']['research_basis']
        self.assertEqual(public['market_report'], basis['market_report'])
        self.assertEqual(public['market_cutoff'], self.f.stamp)
        self.assertEqual(public['factor_assessment'], ctx['current_strategy']['factor_assessment'])
        self.assertNotIn('PRIVATE_MIXED_REPORT_SENTINEL', str(actual))
        self.assertNotIn('PRIVATE_ACCOUNT_SENTINEL', str(actual))
        self.assertNotIn('current_strategy', actual)
        self.assertEqual(ctx['cycle_baseline']['market_report'], public['market_report'])

    def test_direction_cause_and_decided_vs_undetermined_choices_survive_report_consumption(self):
        from v4.common import digest
        from test_closed_loop import allocation
        actions = []
        for scope, choice, gap, arrangement in (
                ('SYNTHETIC_CORE', 'UNDETERMINED', 'ACTION', 'Current coverage prevents choosing which side to change.'),
                ('SYNTHETIC_INCREMENT', 'PAUSE', 'NONE', 'Wait for independently supported value compensation.'),
                ('SYNTHETIC_PROGRAMME', 'HOLD', 'QUANTITY_ONLY', 'Preserve participation purpose; executable size remains limited.')):
            item = deepcopy(allocation(self.f.core, self.f.stamp)['actions'][0])
            item.update(id=scope, scope=scope, decision=choice, gap_impact=gap,
                status='UNDETERMINED' if gap == 'ACTION' else 'DECIDED', arrangement=arrangement,
                execution={'status': 'UNDETERMINED' if gap == 'ACTION' else ('LIMITED' if gap == 'QUANTITY_ONLY' else 'READY'),
                           'limits': [arrangement] if gap != 'NONE' else []},
                reentry_conditions=['Credible value or coverage evidence changes this choice.'] if choice == 'PAUSE' else [])
            actions.append(item)
        portfolio = self.f.core['recommendations']['portfolio']
        portfolio['strategy_advice'] = actions
        portfolio['risk_assessment']['response_needed'] = None
        portfolio['risk_assessment']['valid_until'] = self.f.stamp
        ctx = self.f.context(self.f.root); ctx['report_contract_revision'] = 2
        assessed = deepcopy(self.f.core['outlook']['market_assessment'])
        assessed['summary'] = 'Synthetic downward correction has structural support; its dominant cause remains unidentified.'
        btc = next(m for m in assessed['markets'] if m['id'] == 'BTC')
        for horizon in btc['horizons']:
            horizon.update(direction='BEARISH', confirmation='PARTIAL',
                basis='Several lower-price acceptances persist; the local recovery reduces immediate downside advantage.')
        btc['conflicts'] = 'Flow and leverage causes remain compatible; no dominant cause is identified.'
        ctx['observation']['review']['market_assessment'] = assessed
        review = ctx['observation']['review']['allocation_review']
        review.update(fact_version=ctx['identity']['fact_version'], strategy_advice_hash=digest(actions),
            explanation='Core side changes remain undecided; incremental waiting and the participation purpose remain explicit choices.',
            limitations=['Preserving a purpose does not validate the current whole position.'])
        basis = ctx.setdefault('cycle_baseline', {})
        basis.update(result_id='SYNTHETIC_ORIGINAL_STUDY', market_cutoff=self.f.stamp,
            market_report='Original pure-market structural argument with competing causal explanations.')
        before = deepcopy(ctx)
        brief = run_flow.report_brief(ctx)
        self.assertEqual(brief['market_result']['market_assessment'], assessed)
        self.assertEqual(brief['market_result']['research_basis']['market_report'], basis['market_report'])
        self.assertEqual(brief['strategy_advice'], actions)
        self.assertEqual(brief['strategy_advice'][0]['decision'], 'UNDETERMINED')
        self.assertEqual(brief['applicability']['portfolio']['explanation'], review['explanation'])
        self.assertEqual(brief['applicability']['portfolio']['outcome'], 'REVISE')
        self.assertEqual(brief['portfolio_risk']['valid_until'], self.f.stamp)
        self.assertEqual(ctx, before)

    def test_expiry_preserves_actual_bounded_reassessment_and_keeps_old_advice_unrenewed(self):
        risk = closed_loop.risk(self.f.core)
        risk['valid_until'] = self.f.stamp
        ctx = self.f.context(self.f.root)
        ctx['report_contract_revision'] = 2
        review = ctx['observation']['review']['allocation_review']
        review['fact_version'] = ctx['identity']['fact_version']
        review['explanation'] = 'Public downside path remains assessable; exact hedge resizing needs current coverage.'
        review['limitations'] = ['Exact hedge quantity is unavailable.']
        result = closed_loop.report_inputs(ctx)
        self.assertEqual(result['applicability']['portfolio']['explanation'], review['explanation'])
        self.assertIn(review['limitations'][0], result['applicability']['portfolio']['limitations'])
        self.assertEqual(result['applicability']['portfolio']['outcome'], 'REVISE')
        self.assertEqual(result['portfolio_risk']['valid_until'], self.f.stamp)
        self.assertEqual(risk['valid_until'], self.f.stamp)
        legacy = deepcopy(ctx); legacy.pop('report_contract_revision')
        old = closed_loop.report_inputs(legacy)
        self.assertNotIn('research_basis', old['market_result'])
        self.assertNotIn('explanation', old['applicability']['portfolio'])


    def test_changed_facts_and_expiry_preserve_current_fact_review_without_validating_old_actions(self):
        from v4.common import digest
        risk = closed_loop.risk(self.f.core)
        risk['valid_until'] = self.f.stamp
        ctx = self.f.context(self.f.root); ctx['report_contract_revision'] = 2
        old_risk = deepcopy(risk)
        current_facts = digest({'synthetic_account_facts':'updated'})
        ctx['identity']['fact_version'] = current_facts
        review = ctx['observation']['review']['allocation_review']
        review.update(fact_version=current_facts, outcome='EVIDENCE_LIMITED',
            explanation='Updated public-to-personal assessment is useful; actual hedge sizing remains locally constrained.',
            limitations=['The new facts do not confirm current hedge coverage.'])
        before = deepcopy(ctx)
        result = closed_loop.report_inputs(ctx)
        personal = result['applicability']['portfolio']
        self.assertEqual(personal['explanation'], review['explanation'])
        self.assertEqual(personal['fact_version'], current_facts)
        self.assertIn(review['limitations'][0], personal['limitations'])
        self.assertTrue(any('Local asset facts changed' in item for item in personal['limitations']))
        self.assertTrue(any('Allocation expired' in item for item in personal['limitations']))
        self.assertEqual(personal['outcome'], 'REVISE')
        self.assertEqual(result['portfolio_risk'], old_risk)
        self.assertNotEqual(result['portfolio_risk']['fact_version'], current_facts)
        self.assertEqual(result['portfolio_risk']['valid_until'], self.f.stamp)
        self.assertEqual(result['result_binding']['portfolio_risk_hash'], digest(old_risk))
        self.assertEqual(result['result_binding']['applicability_hash'], digest(result['applicability']))
        self.assertEqual(ctx, before)
        legacy = deepcopy(ctx); legacy.pop('report_contract_revision')
        self.assertNotIn('explanation', closed_loop.report_inputs(legacy)['applicability']['portfolio'])

    def test_wrong_fact_binding_cannot_be_preserved_as_current_reassessment(self):
        from v4.common import digest
        risk = closed_loop.risk(self.f.core); risk['valid_until'] = self.f.stamp
        for facts_changed in (True, False):
            with self.subTest(facts_changed=facts_changed):
                ctx = self.f.context(self.f.root); ctx['report_contract_revision'] = 2
                review = ctx['observation']['review']['allocation_review']
                if facts_changed:
                    ctx['identity']['fact_version'] = digest({'synthetic_account_facts':'new'})
                    review['fact_version'] = risk['fact_version']
                else:
                    review['fact_version'] = digest({'synthetic_account_facts':'another'})
                review.update(outcome='VALID', explanation='WRONG_FACT_REVIEW_SENTINEL', limitations=[])
                result = closed_loop.report_inputs(ctx)
                personal = result['applicability']['portfolio']
                self.assertNotEqual(personal['outcome'], 'VALID')
                self.assertNotIn('WRONG_FACT_REVIEW_SENTINEL', str(personal))
                self.assertNotIn('explanation', personal)
                self.assertEqual(result['portfolio_risk']['valid_until'], self.f.stamp)



class NotificationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.f = care_fixture.ReportCareTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        original = self.f.context
        def context(root):
            ctx = original(root)
            ctx.update(market_revision=6, report_contract_revision=2,
                       current_strategy=self.f.f.f.f.decision['assessment'])
            return ctx
        self.f.context = context
        patch = mock.patch('v4.reporting.context', side_effect=context)
        patch.start(); self.addCleanup(patch.stop)

    def spec(self, outcome='VALID'):
        spec = self.f.spec('SILENT')
        m = spec['maintenance']; m['retired'] = []
        for item in m['triggers']:
            item['scope'] = 'MARKET'
        check = m['checked']
        check.update(outcome=outcome, outcomes={'MARKET':outcome,'PORTFOLIO':'VALID'})
        for item in check['trigger_results']:
            item.update(scope='MARKET', material=False)
        spec['result_binding'] = closed_loop.report_inputs(self.f.context(self.f.root))['result_binding']
        return spec

    def test_internal_revision_can_be_recorded_without_creating_delivery_obligation(self):
        result = reporting.record(self.f.root, self.spec('REVISE'))
        saved = reporting.review(self.f.root, result['review_id'])
        self.assertEqual(saved['maintenance']['checked']['outcome'], 'REVISE')
        self.assertEqual(saved['action'], 'SILENT')
        self.assertEqual(reporting.obligations(self.f.root), [])

    def test_new_material_counterevidence_or_actual_danger_still_requires_feedback(self):
        reporting.record(self.f.root, self.spec())
        for state in ('MET', 'UNKNOWN'):
            with self.subTest(state=state):
                spec = self.spec('REVISE')
                spec['maintenance']['checked']['trigger_results'][0].update(state=state, material=True,
                    evidence='Synthetic independently verified thesis impact or actual portfolio danger, not a price tick.')
                with self.assertRaisesRegex(Invalid, 'material change or unresolved risk'):
                    reporting.record(self.f.root, spec)

    def test_actual_material_personal_danger_still_requires_feedback(self):
        spec = self.spec('REVISE'); ctx = self.f.context(self.f.root)
        ctx['observation'] = {'review':{'allocation_review':{
            'material':True, 'explanation':'Synthetic actual portfolio danger requires a useful response.'}}}
        with self.assertRaisesRegex(Invalid, 'important portfolio review'):
            report_care.validate(self.f.root, spec, ctx)

    def test_old_unscoped_context_does_not_relax_from_a_new_flag(self):
        spec = self.f.spec('SILENT')
        spec['maintenance']['retired'] = []
        spec['maintenance']['checked']['outcome'] = 'REVISE'
        ctx = self.f.context(self.f.root); ctx['market_revision'] = 5
        with self.assertRaisesRegex(Invalid, 'silence must establish'):
            report_care.validate(self.f.root, spec, ctx)

    def test_existing_delivery_duty_survives_a_quiet_internal_revision(self):
        legacy = self.f.context(self.f.root)
        legacy.pop('report_contract_revision'); legacy['market_revision'] = 4
        with mock.patch('v4.reporting.context', return_value=legacy):
            first = reporting.record(self.f.root, self.f.spec('REPORT'))
        before = reporting.obligations(self.f.root)
        self.assertEqual([v['review_id'] for v in before], [first['review_id']])
        reporting.record(self.f.root, self.spec('REVISE'))
        self.assertEqual(reporting.obligations(self.f.root), before)

    def test_daily_feedback_and_legacy_revision_protections_remain(self):
        spec = self.spec('REVISE'); ctx = self.f.context(self.f.root)
        ctx['report_care']['daily_due'] = True
        with self.assertRaisesRegex(Invalid, 'daily feedback is due'):
            report_care.validate(self.f.root, spec, ctx)
        ctx = self.f.context(self.f.root); ctx.pop('report_contract_revision')
        with self.assertRaisesRegex(Invalid, 'silence needs valid market'):
            report_care.validate(self.f.root, spec, ctx)


if __name__ == '__main__':
    unittest.main()
