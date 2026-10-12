"""Real private ledger consumers with generated files; no real account access."""
from copy import deepcopy
import unittest
from unittest import mock
import test_investment as old
from v4 import investment, portfolio_ledger, closed_loop
from v4.common import Invalid, facts, digest, read


class PrivateTests(unittest.TestCase):
    def setUp(self):
        self.f=old.InvestmentDecisionTests()
        # Construct a historical revision8 fixture only. All assertions below
        # use the current, unpatched entry/constraint validator.
        with mock.patch('v4.storage.NEW_WRITE_REVISION',1):self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.project=self.f.f.f.project;self.root=self.f.f.f.store;self.stamp=self.f.stamp
        self.value=deepcopy(self.f.value)
        self.value['account_constraints']=portfolio_ledger.constraints(self.root,self.project,self.stamp)
        for candidate in self.value['candidates']:candidate['execution_role']='INVENTORY_MAINTENANCE' if candidate['is_current'] else 'INVENTORY_ADJUSTMENT'
        for window in self.value['evaluation_windows']:window.update(end=None,process='Track the original recovery mechanism against independently observed facts.')

    def validate(self,value=None):
        investment.validate(value or self.value,self.stamp,closed_loop.public_hash(self.f.core),self.f.core['fact_version'],self.project,9)

    def item(self,identity,kind,details,observed=None):
        line=(self.project/'资产配置.md').read_text().splitlines()[0]
        return {'id':identity,'kind':kind,'observed_at':observed or self.stamp,'known_at':observed or self.stamp,
                'currency':'USD','fact_version':facts(self.project),'source_refs':[{'file':'资产配置.md','line_start':1,'line_end':1,'quote':line}], 'details':details}

    def append(self,item):
        with mock.patch('v4.portfolio_ledger.now',return_value=item['known_at']):
            portfolio_ledger.append(self.root,self.project,{'items':[item],'expected_versions':{item['id']:None}})

    def select(self,role,products=('CASH_SPOT',)):
        v=deepcopy(self.value);candidate=v['candidates'][0]
        candidate.update(execution_role=role,verdict='SUPPORTED',dependencies=[])
        candidate['validation'].update(products=list(products),required_capabilities=[],limitations=[])
        v.update(status='PARTIAL',selected_ids=[candidate['id']]);v['gaps']=[]
        other=v['candidates'][1];other.update(verdict='REJECTED',dependencies=[])
        other['validation'].update(products=['RESEARCH_OR_POLICY'],required_capabilities=[],limitations=[])
        return v

    def test_private_unknown_preserves_market_and_supported_research_without_safe_hold(self):
        self.validate();before=closed_loop.public_hash(self.f.core)
        v=deepcopy(self.value);v['supported_judgements']=['A changed personal priority cannot alter the public claim.']
        self.validate(v);self.assertEqual(before,closed_loop.public_hash(self.f.core))
        for role in ('INVENTORY_MAINTENANCE','INVENTORY_ADJUSTMENT','INCREMENTAL'):
            with self.subTest(role=role),self.assertRaisesRegex(Invalid,'unknown full exposure|deployable funding'):self.validate(self.select(role))

    def test_completeness_cannot_be_self_certified_from_targets(self):
        bad=deepcopy(self.value);bad['account_constraints']['exposure_complete']=True
        with self.assertRaisesRegex(Invalid,'self-certified'):self.validate(bad)

    def test_nav_completeness_does_not_prove_exposure_or_available_funding(self):
        self.append(self.item('nav','NAV',{'value':'100','scope_complete':True,'costs_included':True,'cash_flows_complete':True}))
        c=portfolio_ledger.constraints(self.root,self.project,self.stamp)
        self.assertIsNone(c['exposure_complete']);self.assertIsNone(c['funding_complete']);self.validate()

    def test_confirmed_constraints_are_consumed_and_later_fill_invalidates(self):
        coverage=self.item('coverage','RISK_CONSTRAINTS',dict(exposure_complete=True,cross_margin_complete=True,funding_complete=True))
        self.append(coverage);c=portfolio_ledger.constraints(self.root,self.project,self.stamp)
        self.assertEqual(c['record_id'],digest(coverage));self.assertTrue(c['exposure_complete'])
        v=self.select('INVENTORY_MAINTENANCE');v['account_constraints']=c;self.validate(v)
        from datetime import timedelta
        from v4.common import instant
        later=(instant(self.stamp)+timedelta(hours=1)).isoformat()
        self.append(self.item('later-fill','FILL',{'instrument':'SYNTHETIC','side':'BUY','quantity':'1','price':'1','fee':'0','fee_currency':'USD'},later))
        c=portfolio_ledger.constraints(self.root,self.project,later)
        self.assertIsNone(c['exposure_complete']);self.assertIsNone(c['cross_margin_complete']);self.assertIsNone(c['funding_complete'])

    def test_policy_cannot_disguise_actual_inventory_or_spot_entry(self):
        with self.assertRaisesRegex(Invalid,'policy candidates'):self.validate(self.select('POLICY'))
        v=self.select('POLICY',('RESEARCH_OR_POLICY',));self.validate(v)
        rec={'upper_short':{'status':'UNDETERMINED'},'lower_spot':{'status':'RECOMMENDED','investment_candidates':['current']},'portfolio':{'strategy_advice':[]}}
        with self.assertRaisesRegex(Invalid,'products'):investment.validate_outputs(v,rec)
        rec['lower_spot']['status']='UNDETERMINED'
        for decision in ('HOLD','OPEN','INCREASE','REDUCE','EXIT','ADJUST'):
            rec['portfolio']['strategy_advice']=[{'status':'DECIDED','decision':decision,'investment_candidates':['current']}]
            with self.subTest(decision=decision),self.assertRaisesRegex(Invalid,'policy|HOLD'):investment.validate_outputs(v,rec)


    def test_legal_policy_adjustment_remains_available_without_inventory_coverage(self):
        v=self.select('POLICY',('RESEARCH_OR_POLICY',));self.validate(v)
        action={'id':'policy','scope':'Long-term purpose','status':'DECIDED','decision':'ADJUST',
                'arrangement':'Clarify the independent participation policy.','gap_impact':'NONE',
                'execution':{'status':'NOT_APPLICABLE','limits':[]},'reentry_conditions':[],
                'implementation_products':['RESEARCH_OR_POLICY'],'investment_candidates':['current']}
        closed_loop.validate_actions([action])
        recommendations={'upper_short':{'status':'UNDETERMINED'},'lower_spot':{'status':'UNDETERMINED'},
                         'portfolio':{'strategy_advice':[action],'risk_assessment':{'actions':[]}}}
        investment.validate_outputs(v,recommendations)

    def test_current_cycle_candidate_detects_new_private_contract_with_reused_public8(self):
        from v4 import cycle,market_state
        core=deepcopy(self.f.core);core['revision']=9;core['recommendations']['portfolio']['investment_decision']=self.value
        candidate=cycle.candidate(core)
        self.assertNotIn('revision',candidate['assessment']);self.assertEqual(market_state.revision(candidate['assessment']),9)
        from v4 import evidence
        package=evidence.validate(read(self.f.f.f.evidence_dir/'evidence.json'),self.f.f.f.evidence_dir)
        cycle.validate_candidate(candidate,evidence.forward_resolver(package,self.f.f.f.evidence_dir),self.root,self.project,frozen_public_cutoff=self.stamp)


if __name__=='__main__':unittest.main()
