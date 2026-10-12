"""Synthetic consumer tests; they do not judge or prove market semantics."""
import unittest
from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from v4 import closed_loop as c
from v4.common import digest

def context(contract=6):
    stamp='2026-09-03T12:00:00Z'
    fact={'资产配置.md':None,'操作记录.md':None,'纠正记录.md':None}
    old={'semantic':{'baseline':{'direction':'DOWNSIDE_SKEW'}},
         'narrative':'Synthetic adopted future path with decisive counterevidence.', 'refs':[],
         'market_assessment':{'summary':'Synthetic adopted main-scale downward correction'},'dispute_zones':[]}
    strategy={'outlook':old,'revision':6,'factor_assessment':{'items':[],'summary':'Synthetic selected mechanism'},
      'recommendations':{'portfolio':{},'upper_short':{},'lower_spot':{}},
      'section_provenance':{'outlook':{'mode':'REUSED','decision_id':'D0','evidence_id':'OLD_EVIDENCE','assessed_at':stamp}}}
    for side in ('upper_short','lower_spot'):
        strategy['recommendations'][side]={'status':'UNDETERMINED','unit':'USD','tiers':[], 'validity':{},'allocation_percent':{}}
    return {'identity':{'decision_id':'D1','fact_version':fact},'current_strategy':strategy,
       'report_contract_revision':contract,'market_revision':6,'evaluated_at':stamp,
       'cycle_baseline':{'result_id':'R1','market_cutoff':stamp,'market_report':'Synthetic pure market report.'},
       'observation':{'observation_id':'O1','baseline_decision_id':'D1','review':{
           'market_assessment':{'summary':'Synthetic recovery has changed current evidence'},'dispute_zones':[],
           'market_result_hash':'H1','applicability_review':{'outcome':'REVISE','limitations':['Original forward ranking requires renewed adjudication.']}}}}

class ResultInterfaceTests(unittest.TestCase):
    def test_adopted_full_outlook_is_verbatim_and_dated_alongside_current_observation(self):
        ctx=context();before=deepcopy(ctx);view=c.report_inputs(ctx)['market_result']
        self.assertEqual(view['research_basis']['outlook'],ctx['current_strategy']['outlook'])
        self.assertEqual(view['research_basis']['decision_id'],'D1')
        self.assertEqual(view['research_basis']['evidence_id'],'OLD_EVIDENCE')
        self.assertEqual(view['research_basis']['section_provenance'],ctx['current_strategy']['section_provenance']['outlook'])
        self.assertEqual(view['research_basis']['section_provenance']['decision_id'],'D0')
        self.assertEqual(view['research_basis']['assessed_at'],'2026-09-03T12:00:00Z')
        self.assertEqual(view['market_assessment'],ctx['observation']['review']['market_assessment'])
        self.assertEqual(view['observation_basis']['applicability_review']['outcome'],'REVISE')
        self.assertEqual(ctx,before)

    def test_nonmatching_observation_does_not_author_old_outlook_as_current(self):
        ctx=context();ctx['observation']['baseline_decision_id']='OTHER'
        view=c.report_inputs(ctx)['market_result']
        self.assertEqual(view['market_assessment'],ctx['current_strategy']['outlook']['market_assessment'])
        self.assertIsNone(view['observation_basis']['observation_id'])
        self.assertIsNone(view['observation_basis']['applicability_review'])
        self.assertEqual(view['research_basis']['outlook'],ctx['current_strategy']['outlook'])

    def test_frozen_old_contract_has_exact_previous_projection(self):
        for revision in (1,2,3,4,5):
            ctx=context(revision);actual=c.report_inputs(ctx)['market_result']
            expected={k:deepcopy(ctx['observation']['review'][k]) for k in ('market_assessment','dispute_zones')}
            if revision>=2:
                expected['research_basis']={**ctx['cycle_baseline'], 'factor_assessment':deepcopy(ctx['current_strategy']['factor_assessment'])}
            self.assertEqual(actual,expected)
            self.assertNotIn('observation_basis',actual)

    def test_market_binding_changes_on_semantic_or_observation_revision(self):
        ctx=context();old=c.report_inputs(ctx)['result_binding']['market_result_hash']
        ctx['current_strategy']['outlook']['semantic']['baseline']['direction']='RANGE'
        semantic=c.report_inputs(ctx)['result_binding']['market_result_hash'];self.assertNotEqual(old,semantic)
        ctx['observation']['review']['applicability_review']['outcome']='EVIDENCE_LIMITED'
        self.assertNotEqual(semantic,c.report_inputs(ctx)['result_binding']['market_result_hash'])

if __name__=='__main__':unittest.main()
