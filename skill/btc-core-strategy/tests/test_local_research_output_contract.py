"""Local-output boundaries over original consumers; generated fixtures, no model proof.

These tests use existing fixture constructors, never their old test methods.
No formal context/review/ready, real research or delivery is created.
"""
from copy import deepcopy
import json
import unittest
from unittest import mock
from pathlib import Path

import test_forward_lifecycle as fixtures
from test_forward_lifecycle import T0, T2
from v4 import research, forward_lifecycle as life, closed_loop, authored_report, evidence
from v4.common import Invalid, create, digest, read, replace


class LocalResearchOutputTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ForwardTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.directory, self.bundle = self.package()
        self.public = self.public_result()
        self.public['market_report'] = '合成研究：方向判断仍待后续事实核验；机制原因尚未排他识别；时间条件须按原期限复核。'
        self.public['factor_assessment']['summary'] = '合成来源仅检验结构和传递，不代表真实市场研究。'
        self.packet = {'sources': [], 'historical_hypotheses': {},
                       'evidence_cutoff': self.bundle['cutoff']}
        create(self.root/'input.json', self.packet)
        self.req = research._save(self.root, 'requests', 'request_id', {
            'schema':'BTC_RESEARCH_REQUEST_V4', 'workflow_revision':9,
            'created_at':T2, 'evidence_id':self.bundle['evidence_id'],
            'independent_input_path':'input.json', 'independent_input_id':digest(self.packet)})

    def package(self):
        spec={'parent_id':None,'cutoff':T2,'sources':self.f.bundle['sources'],
              'measurements':[],'verified_facts':[],'candidates':[],'gaps':[],
              'handoff':{'previous_hour':None,'successful_deep':None,'changes_since_hour':[],
                         'changes_since_deep':[],'active_hypotheses':[],'open_questions':[]}}
        files={s['id']:self.f.directory/'raw'/(s['raw_hash'][7:]+'.json') for s in spec['sources']}
        target=self.root/'package';evidence.freeze(spec,files,target)
        return target,read(target/'evidence.json')

    def public_result(self):
        # Generated contract coverage for all required subjects; no market claim.
        markets=[]
        for asset in ('SPX','NDX','BTC'):
            claim=fixtures.proposition('synthetic-'+asset,T2)
            fw=fixtures.forward(claim)
            if asset=='BTC':fw=self.f.derived()
            horizon={'id':'cycle','period':'Synthetic process scale','direction':'UNKNOWN',
              'structure':'Synthetic fixture, no current trend inferred.',
              'change':'No real market change tested.','basis':'Original raw references remain synthetic.',
              'confirmation':'UNKNOWN','refs':[fixtures.anchor()],'counter_refs':[], 'forward':fw,
              'momentum':{'phase':'UNKNOWN','basis':'No real opposite trend assessed.','next_condition':'Obtain actual scoped response.'},
              'price_attractiveness':{'status':'UNDETERMINED','basis':'No defensible valuation in fixture.',
                'benchmark':'Same-horizon comparison required.','limitations':'Fixture has no net return evidence.',
                'change_conditions':'Real value and cost support.','refs':[]},
              'strategy_applicability':[{'id':'synthetic','question':'Contract transport only?',
                'status':'UNDETERMINED','basis':'No actual suitability conclusion.',
                'limitations':'No real research.','review_conditions':'Actual qualified evidence.'}]}
            markets.append({'id':asset,'as_of':T2,'horizons':[horizon],
              'resonance':{'status':'UNKNOWN','periods':['cycle'],'assessment':'Synthetic relation only.'},
              'reversal':{'status':'UNKNOWN','periods':['cycle'],'assessment':'No real reversal confirmation.'},
              'conflicts':'Actual competition remains untested.','next_check':'Qualified independent observations.'})
        scenario={'direction':'UNRESOLVED','horizon':{'unit':'PROCESS','minimum':None,'maximum':None},
                  'mechanism':['SYNTHETIC_RESPONSE'],'formation_conditions':['ACTUAL_RESPONSE'],
                  'invalidation_conditions':['OPPOSING_RESPONSE']}
        factor={'id':'synthetic-source','name':'Synthetic observed value',
                'assessment':'A fixture raw value is not market causation.','change_reason':'No real market revision.',
                'refs':[fixtures.anchor()],'counter_refs':[],
                'decision_review':{'state':'OBSERVING','mechanism':'Fixture only.',
                  'judgement_impact':'No real decision impact.','applicability':'Synthetic scope.',
                  'verification':'No actual market verification.','duplicate_of':[]}}
        return {'outlook':{'semantic':{'status':'ASSESSED','baseline':scenario,'alternatives':[deepcopy(scenario)],
                    'next_evidence':['ACTUAL_NEW_OBSERVATION']},'narrative':'Synthetic bounded outlook.',
                    'refs':[fixtures.anchor()],'market_assessment':{'markets':markets,'summary':'Synthetic market result only.'},'dispute_zones':[]},
                'factor_assessment':{'items':[factor],'summary':'Synthetic factor coverage.'},
                'market_report':'Synthetic result, no business approval.'}

    def accepted(self, value=None):
        with mock.patch.object(life, 'now', return_value=T2):
            return research._public_result(self.root, self.req,
                json.dumps(self.public if value is None else value, ensure_ascii=False), self.directory)

    def context(self):
        plan = {'status':'UNDETERMINED', 'unit':'USDT', 'tiers':[],
                'validity':{'from':T2, 'until':'2024-01-05T00:00:00Z'},
                'allocation_percent':None}
        facts = {name:None for name in ('资产配置.md','操作记录.md','纠正记录.md')}
        strategy = {'revision':9, 'fact_version':facts, 'outlook':deepcopy(self.public['outlook']),
                    'factor_assessment':deepcopy(self.public['factor_assessment']),
                    'evidence_id':self.bundle['evidence_id'], 'knowledge_cutoff':T2,
                    'recommendations':{'portfolio':{}, 'upper_short':deepcopy(plan), 'lower_spot':deepcopy(plan)},
                    'section_provenance':{'outlook':{'evidence_id':self.bundle['evidence_id'], 'assessed_at':T2}}}
        owner = digest('SYNTHETIC_DECISION_NOT_PRODUCTION')
        return {'context_id':digest('SYNTHETIC_CONTEXT_NOT_PRODUCTION'),
                'current_strategy':strategy, 'identity':{'decision_id':owner,'fact_version':facts},
                'observation':{'observation_id':digest('synthetic-observation'), 'baseline_decision_id':owner,
                    'review':{'market_assessment':deepcopy(self.public['outlook']['market_assessment']),
                             'dispute_zones':[], 'market_result_hash':digest(self.public),
                             'applicability_review':{'outcome':'EVIDENCE_LIMITED','limitations':['Synthetic coverage only.']}}},
                'cycle_baseline':{'result_id':digest(self.public),'market_cutoff':T2,'market_report':self.public['market_report']},
                'report_contract_revision':7, 'market_revision':9, 'evaluated_at':T2, 'knowledge_cutoff':T2}

    def report(self, ctx=None):
        ctx = ctx or self.context()
        value = {'context':ctx, 'result_binding':closed_loop.report_inputs(ctx)['result_binding'],
                 'layout':{'format':authored_report.FORMAT, 'body':self.public['market_report'],
                    'bindings':[{'text':self.public['market_report'], 'scope':'market',
                                 'refs':['/market_result/research_basis/market_report']}],
                    'price_status':{'upper_short':'REVIEW_REQUIRED','lower_spot':'REVIEW_REQUIRED'},
                    'quality_review':{key:'合成解释占位仅用于检查原结构边界，不表示已有真实语义审阅或批准。'
                                      for key in authored_report.quality_keys(ctx)},
                    'reviewed_candidate_hash':None}}
        value['layout']['reviewed_candidate_hash'] = authored_report.candidate_hash(value)
        return value, {'assessment':ctx['current_strategy']}

    def test_O01_original_request_read_seal_and_known_candidate_exclusion(self):
        readback = research.read_input(self.root, self.req['request_id'])
        self.assertEqual(readback['payload'], self.packet)
        self.assertEqual(readback['input_id'], digest(self.packet))
        replace(self.root/'input.json', dict(self.packet, evidence_cutoff=T0))
        with self.assertRaisesRegex(Invalid, 'input changed'):
            research.read_input(self.root, self.req['request_id'])
        bad = dict(self.packet, candidates=['SYNTHETIC_CONTAMINATION'])
        replace(self.root/'input.json', bad)
        # Direct original field guard covers named project contamination only;
        # it does not certify all conversation-level information isolation.
        with self.assertRaises(Invalid): research._candidate_free(bad)

    def test_O02_normal_public_result_requires_factors_and_exact_raw_values(self):
        self.assertEqual(self.accepted()['market_result'], self.public)
        for label, change in [('missing_factors', lambda p:p.pop('factor_assessment')),
                              ('missing_summary',lambda p:p['factor_assessment'].pop('summary'))]:
            bad=deepcopy(self.public);change(bad)
            with self.subTest(label=label),self.assertRaises(Invalid):self.accepted(bad)
        source=self.bundle['sources'][0]
        path=self.directory/'raw'/(source['raw_hash'][7:]+'.json')
        original=path.read_bytes();path.write_bytes(original+b' ')
        with self.assertRaisesRegex(Invalid,'raw evidence'):self.accepted()

    def test_O03_original_save_registers_and_roundtrips_exact_public_owner(self):
        accepted=self.accepted()
        with mock.patch.object(life,'now',return_value=T2):
            saved=research._save(self.root,'independent','independent_id',accepted)
        reread=research._load(self.root,'independent',saved['independent_id'])
        self.assertEqual(reread['market_result'],self.public)
        claim=self.public['outlook']['market_assessment']['markets'][0]['horizons'][0]['forward']['lifecycle']['claim']
        registered=life.registration(self.root,digest(claim))
        self.assertEqual(registered['registered_at'],T2)
        self.assertEqual(registered['claim'],claim)
        self.assertEqual(registered['owner']['time_pointer'],'/lifecycle_frozen_at')
        self.assertEqual(research._load(self.root,'independent',saved['independent_id']),saved)

    def test_O04_O05_historical_feedback_and_fixed_claim_survive_report_boundary(self):
        self.accepted()
        ctx=self.context();before=deepcopy(ctx)
        output=closed_loop.report_inputs(ctx)
        basis=output['market_result']['research_basis']
        self.assertEqual(basis['outlook'],self.public['outlook'])
        self.assertEqual(basis['factor_assessment'],self.public['factor_assessment'])
        self.assertEqual(basis['assessed_at'],T2)
        self.assertEqual(ctx,before)
        # Reason/direction/time wording is transported intact, not automatically judged true.
        self.assertEqual(basis['market_report'],self.public['market_report'])
        self.assertEqual(output['market_result']['market_assessment'],ctx['observation']['review']['market_assessment'])

    def test_O06_wrong_binding_or_mismatched_observation_not_promoted(self):
        ctx=self.context();value,_=self.report(ctx)
        closed_loop.validate_report_binding(value,ctx)
        wrong=deepcopy(ctx)
        wrong['current_strategy']['outlook']['narrative']='Changed synthetic research version.'
        with self.assertRaisesRegex(Invalid,'bind the final'):
            closed_loop.validate_report_binding(value,wrong)
        wrong=deepcopy(ctx);wrong['observation']['baseline_decision_id']=digest('another-decision')
        output=closed_loop.report_inputs(wrong)
        self.assertIsNone(output['market_result']['observation_basis']['observation_id'])
        self.assertEqual(output['applicability']['market']['outcome'],'EVIDENCE_LIMITED')
        self.assertIsNone(output['investment_decision'])
        self.assertTrue(all(p['status']=='UNDETERMINED' for p in output['execution_plans'].values()))

    def test_O07_normal_authored_preview_pending_and_render_preserve_body(self):
        value,decision=self.report()
        preview=authored_report.preview(value,decision)
        self.assertEqual(preview['semantic_review'],'PENDING_ACTUAL_FULL_BODY_REVIEW')
        body,_=authored_report.render(value,decision)
        self.assertEqual(body,self.public['market_report'])
        self.assertEqual(value['result_binding'],closed_loop.report_inputs(value['context'])['result_binding'])

    def test_O07_missing_review_changed_body_and_personal_source_rejected(self):
        value,decision=self.report()
        value['layout']['quality_review'][next(iter(value['layout']['quality_review']))]=None
        with self.assertRaises(Invalid):authored_report.render(value,decision)
        value,decision=self.report();value['layout']['body']+=' 合成新增未审文字。'
        with self.assertRaisesRegex(Invalid,'changed or has not been reviewed'):
            authored_report.render(value,decision)
        value,decision=self.report()
        value['layout']['bindings'][0]['refs']=['/applicability/portfolio/limitations/0']
        value['layout']['reviewed_candidate_hash']=authored_report.candidate_hash(value)
        with self.assertRaisesRegex(Invalid,'market conclusion cannot bind personal'):
            authored_report.render(value,decision)
