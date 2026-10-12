"""Decision-path regressions with generated data, not investment performance claims."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest

import test_closed_loop as legacy
from test_portfolio_risk import risk
from v4 import investment, closed_loop, run_flow, storage, market_state, reporting
from v4.common import Invalid, digest, instant, read, replace, facts


def public_research(outlook):
    for market in outlook['market_assessment']['markets']:
        for h in market['horizons']:
            h['momentum']={'phase':'WEAKENING','basis':'Synthetic original impulse weakens; opposite demand has not persisted.',
                           'next_condition':'Independent opposing demand and durable price acceptance.'}
            h['price_attractiveness']={'status':'UNDETERMINED','basis':'Direction alone does not determine a profitable entry.',
                                      'benchmark':'Comparable-horizon waiting opportunity.', 'limitations':'No defensible terminal value and net cost.',
                                      'change_conditions':'Value and costs become supportable.','refs':[]}
            h['strategy_applicability']=[{'id':'synthetic-participation','question':'Does phased participation suit this mechanism?',
                                         'status':'CONDITIONAL','basis':'A recovery mechanism must persist independently of account facts.',
                                         'limitations':'This is not an unconditional value or personal size decision.',
                                         'review_conditions':'Mechanism fails or net compensation becomes measurable.'}]


def formed(core, stamp, project):
    version = facts(project)
    line = (project / '资产配置.md').read_text().splitlines()[0]
    policy = {'recorded_at': stamp, 'fact_version':version,
              'objectives':['Preserve principal while retaining independently supported participation.'],
              'constraints':['Unknown coverage cannot imply current safety.'],
              'baseline':'Synthetic long-term purposes; no inferred balance or fills.',
              'unknowns':['Current total exposure.'],
              'source_refs':[{'file':'资产配置.md','line_start':1,'line_end':1,'quote':line}]}
    from v4.common import create
    policy['version'] = digest(policy)
    path=project/'.strategy/v4/investment/policies'/(policy['version'][7:]+'.json')
    create(path,policy)
    comparison = {'return_sources':'Recovery participation depends on the public thesis.',
                  'risk_overlap':'Spot and protection must be assessed jointly.',
                  'capital':'Missing free cash is not zero.', 'liquidity':'No immediate liquidity need has been asserted.',
                  'net_cost':'Costs remain unconfirmed.', 'opportunity_cost':'Waiting retains flexibility but can miss recovery.',
                  'rationale':'The current evidence supports a role distinction, not a forced net trade.'}
    old = storage.current(project / '.strategy/v4')['assessment']
    issues = investment.open_issues(old)
    for item in issues:
        if item['id'] == 'legacy:portfolio-synthesis':
            item.update(disposition='RESOLVED', explanation='The actual policy and relevant candidate comparison now form the bounded joint decision.', next_evidence=None)
    return {'policy':policy, 'market_result_hash':closed_loop.public_hash(core), 'fact_version':version,
            'decided_at':stamp, 'valid_until':(instant(stamp)+timedelta(days=2)).isoformat(),
            'status':'UNDETERMINED', 'summary':'The recovery-dependent sleeve is the first review priority; a net spot/protection change remains dependent on actual coverage.',
            'candidates':[{'id':key, 'scope':'Combined spot and protective positions', 'purpose':'PROTECTION',
                           'is_current':key=='current', 'arrangement':arrangement, 'verdict':'UNDETERMINED',
                           'comparison':deepcopy(comparison), 'dependencies':['coverage'],
                           'validation':{'basis':'JUDGEMENT','products':['CASH_SPOT','CROSS_MARGIN_DERIVATIVE'],'record_ids':[],
                                         'required_capabilities':['MARGIN_SURVIVAL','CROSS_MARGIN','FUNDING_COSTS'],
                                         'limitations':['Cash spot method checks cannot establish derivative protection safety.']}}
                          for key,arrangement in [('current','Continue the current joint positions.'),('adjust','Change spot and protection together.')]],
            'selected_ids':[], 'priorities':['Review the recovery-dependent sleeve before assuming all purposes fail together.'],
            'supported_judgements':['Principal protection and long-term equity participation retain distinct purposes.'],
            'gaps':[{'id':'coverage','fact':'Current joint exposure and margin capacity.',
                     'affected_candidates':['current','adjust'],'impact':'ACTION',
                     'consequence':'Cannot determine the net direction or safety of the joint change.',
                     'required_evidence':'Confirmed current joint quantities, cross-margin capacity and funding costs.',
                     'capabilities':['MARGIN_SURVIVAL','CROSS_MARGIN','FUNDING_COSTS']}],
            'issues':issues, 'evaluation_windows':[{'id':'synthetic-forward','question':'Does the recovery premise persist?',
                     'start':stamp,'end':(instant(stamp)+timedelta(days=1)).isoformat(),
                     'success_evidence':'Independent demand and price acceptance persist.',
                     'invalidation_evidence':'Demand exits and price acceptance fails.'}],
            'review_conditions':['Actual coverage, public mechanism or product costs change.']}


class InvestmentDecisionTests(unittest.TestCase):
    def setUp(self):
        self.f=legacy.PublicationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.core=self.f.f.draft
        self.core['revision']=8
        public_research(self.core['outlook'])
        for item in self.core['factor_assessment']['items']:
            item['decision_review']={'state':'ADOPTED','mechanism':'Synthetic independent mechanism.',
                'judgement_impact':'Supports the stated public thesis, not a personal trade.',
                'applicability':'Synthetic declared regime.', 'verification':'Source-bound evidence remains subject to counterevidence.', 'duplicate_of':[]}
        self.f.public={'outlook':deepcopy(self.core['outlook']), 'factor_assessment':deepcopy(self.core['factor_assessment']), 'market_report':self.f.public['market_report']}
        portfolio=self.core['recommendations']['portfolio'];portfolio.pop('allocation_decision')
        self.stamp=self.core['temporal_scope']['knowledge_cutoff']
        portfolio['risk_assessment']=risk(self.core,self.stamp,response_needed=None)
        portfolio['strategy_advice']=[]
        for side in ('upper_short','lower_spot'):
            self.core['recommendations'][side].update(status='UNDETERMINED',tiers=[])
        portfolio['capital_envelope_u']=None
        self.value=formed(self.core,self.stamp,self.f.f.project)
        portfolio['investment_decision']=self.value

    def validate(self, value=None):
        investment.validate(value or self.value,self.stamp,closed_loop.public_hash(self.core),self.core['fact_version'],self.f.f.project)

    def test_unknown_net_action_keeps_supported_priority_and_policy(self):
        self.validate()
        self.assertTrue(investment.finished(self.value)['priorities'])
        self.assertEqual(self.value['selected_ids'],[])
        before=closed_loop.public_hash(self.core)
        other=deepcopy(self.value);other['supported_judgements']=['A different confirmed holding purpose changes only the personal choice.']
        self.validate(other)
        self.assertEqual(before,closed_loop.public_hash(self.core))

    def test_no_compare_no_synthesis_and_unknown_action_no_assumed_hold(self):
        for change in (lambda v:v.update(candidates=v['candidates'][:1]),
                       lambda v:v.update(status='DECIDED',selected_ids=['current']),
                       lambda v:v.update(priorities=[]),lambda v:v['gaps'][0].update(affected_candidates=['current'])):
            bad=deepcopy(self.value);change(bad)
            with self.assertRaises(Invalid):self.validate(bad)

    def test_policy_source_and_market_facts_cannot_be_relabelled(self):
        for change in (lambda v:v.update(market_result_hash=digest('other')),
                       lambda v:v['policy'].update(baseline='Changed without a new policy version.'),
                       lambda v:v['policy']['source_refs'][0].update(quote='Invented account fact.')):
            bad=deepcopy(self.value);change(bad)
            if bad['policy']['source_refs'][0]['quote']=='Invented account fact.':
                bad['policy']['version']=digest({k:x for k,x in bad['policy'].items() if k!='version'})
            with self.assertRaises(Invalid):self.validate(bad)

    def test_ended_review_does_not_close_legacy_unknown_or_missing_analysis(self):
        old=storage.current(self.f.f.store)['assessment']
        issues=investment.open_issues(old)
        with self.assertRaisesRegex(Invalid,'upstream completion'):
            investment.validate_issue_review(issues,old,False)
        investment.validate_issue_review(issues,old,True)
        with self.assertRaisesRegex(Invalid,'every prior'):
            investment.validate_issue_review([],old,False)

    def test_publication_report_consumes_completed_choice_not_full_research(self):
        policy=read(self.f.f.store/'write-policy.json')
        replace(self.f.f.store/'write-policy.json',dict(policy,minimum_assessment_revision=8))
        self.f.c.d.bind()
        self.f.c.study(authored=True,independent_output=json.dumps(self.f.public))
        saved=self.f.c.d.commit()
        self.assertEqual(saved['assessment']['revision'],8)
        self.assertEqual(market_state.revision(saved['assessment']),8)
        # A finished-result interface carries the whole choice independently
        # of risk actions, while excluding candidate research from the body.
        context={'current_strategy':saved['assessment'], 'identity':{'decision_id':saved['decision_id'],'fact_version':self.core['fact_version']},
                 'evaluated_at':self.stamp,'market_revision':8,'report_contract_revision':3}
        result=closed_loop.report_inputs(context)
        self.assertEqual(result['investment_decision']['summary'],self.value['summary'])
        self.assertEqual(result['result_binding']['investment_decision_hash'],digest(self.value))
        self.assertNotIn('candidates',result['investment_decision'])
        self.assertEqual(result['portfolio_narrative'],saved['assessment']['recommendations']['portfolio']['narrative'])
        self.assertEqual(result['result_binding']['portfolio_narrative_hash'],digest(result['portfolio_narrative']))
        self.assertEqual(result['portfolio_risk']['actions'],[])
        self.assertEqual(storage.current(self.f.f.store)['decision_id'],saved['decision_id'])

    def test_expiry_retains_current_review_and_open_issues_without_renewal(self):
        core=deepcopy(self.core)
        old=core['recommendations']['portfolio']['investment_decision']
        old['valid_until']=self.stamp
        context={'current_strategy':core,'identity':{'decision_id':'synthetic','fact_version':self.core['fact_version']},
                 'evaluated_at':self.stamp,'market_revision':8,'report_contract_revision':3}
        result=closed_loop.report_inputs(context)
        self.assertEqual(result['applicability']['portfolio']['outcome'],'REVISE')
        self.assertEqual(result['investment_decision']['valid_until'],self.stamp)
        self.assertEqual(result['investment_decision']['issues'],old['issues'])
        self.assertIsNone(result['portfolio_narrative'])
        self.assertIsNone(result['result_binding']['portfolio_narrative_hash'])

    def test_expired_joint_choice_blocks_adoption_and_longer_lived_price_tiers(self):
        from unittest import mock
        from v4 import authored_report
        policy=read(self.f.f.store/'write-policy.json')
        replace(self.f.f.store/'write-policy.json',dict(policy,minimum_assessment_revision=8))
        self.value['valid_until']=(instant(self.stamp)+timedelta(minutes=1)).isoformat()
        self.f.c.d.bind();self.f.c.study(authored=True,independent_output=json.dumps(self.f.public))
        later=(instant(self.stamp)+timedelta(minutes=2)).isoformat()
        with mock.patch('v4.storage.now',return_value=later),mock.patch('v4.cycle.now',return_value=later):
            with self.assertRaisesRegex(Invalid,'investment decision expired'):
                self.f.c.d.commit()
        clock_calls=[]
        def crossing_clock():
            moment=self.stamp if not clock_calls else later
            clock_calls.append(moment)
            return moment
        before=storage.current(self.f.f.store)['decision_id']
        with mock.patch('v4.storage.now',side_effect=crossing_clock),mock.patch('v4.cycle.now',return_value=self.stamp):
            with self.assertRaisesRegex(Invalid,'investment decision expired before main completion'):
                self.f.c.d.commit()
        self.assertGreaterEqual(len(clock_calls),2)
        self.assertEqual(storage.current(self.f.f.store)['decision_id'],before)
        core=deepcopy(self.core)
        for side in ('upper_short','lower_spot'):
            core['recommendations'][side].update(status='RECOMMENDED',tiers=[{'id':'synthetic','price_low_usd':'90000',
                'price_high_usd':'90000','amount_u':'100','capital_u':'100'}])
            core['recommendations'][side]['validity']['until']=(instant(later)+timedelta(days=2)).isoformat()
        ctx={'current_strategy':core,'identity':{'decision_id':'synthetic','fact_version':core['fact_version']},
             'evaluated_at':later,'knowledge_cutoff':self.stamp,'market_revision':8,'report_contract_revision':3}
        results=closed_loop.report_inputs(ctx)
        self.assertEqual(results['applicability']['portfolio']['outcome'],'REVISE')
        self.assertTrue(all(p['tiers']==[] for p in results['execution_plans'].values()))
        self.assertIsNone(results['strategy_advice'])
        with self.assertRaisesRegex(Invalid,'expired advice'):
            authored_report.price_status({'context':ctx,'layout':{'price_status':dict.fromkeys(('lower_spot','upper_short'),'VALID')}},{'assessment':core})
        authored_report.price_status({'context':ctx,'layout':{'price_status':dict.fromkeys(('lower_spot','upper_short'),'REVIEW_REQUIRED')}},{'assessment':core})

    def test_legacy_risk_clock_and_limited_sides_preserve_problem_identities(self):
        old=storage.current(self.f.f.store)['assessment']
        old=deepcopy(old)
        old['recommendations']['portfolio']['risk_assessment']=deepcopy(self.core['recommendations']['portfolio']['risk_assessment'])
        for side in ('upper_short','lower_spot'):
            old['recommendations'][side].update(status='EXECUTION_LIMITED',tiers=[])
        a=investment.open_issues(old)
        old['recommendations']['portfolio']['risk_assessment']['assessed_at']=(instant(self.stamp)+timedelta(hours=1)).isoformat()
        b=investment.open_issues(old)
        self.assertEqual({i['id'] for i in a},{i['id'] for i in b})
        self.assertTrue({'legacy:side:upper_short','legacy:side:lower_spot'}<={i['id'] for i in b})
        portfolio=old['recommendations']['portfolio']
        portfolio['risk_assessment']['actions']=[{'id':'old-risk','scope':'Portfolio protection','status':'UNDETERMINED',
            'arrangement':'Choose the actual protective response.','execution':{'status':'UNDETERMINED','limits':['Current cover is unconfirmed.']}}]
        portfolio['strategy_advice']=[{'id':'old-sized','scope':'Tactical spot','status':'DECIDED',
            'arrangement':'Reduce the recovery-dependent sleeve.','execution':{'status':'LIMITED','limits':['Current quantity is unconfirmed.']}}]
        retained=investment.open_issues(old)
        self.assertTrue({'legacy:risk-action:old-risk','legacy:advice:old-sized'}<={i['id'] for i in retained})

    def test_direction_momentum_price_and_strategy_are_separate(self):
        outlook=deepcopy(self.core['outlook'])
        for m in outlook['market_assessment']['markets']:
            for h in m['horizons']:
                market_state.validate_investment_research(h,lambda ref:None)
                self.assertEqual(h['price_attractiveness']['status'],'UNDETERMINED')
                bad=deepcopy(h);bad.pop('price_attractiveness')
                with self.assertRaises((KeyError,Invalid)):
                    market_state.validate_investment_research(bad,lambda ref:None)

    def test_normal_public_freeze_then_personal_review_returns_missing_synthesis_upstream(self):
        f=legacy.HourlyClosedLoopTests();f.setUp();self.addCleanup(f.doCleanups)
        prepared=run_flow.prepare(f.root,f.f.f.project,'SYNTHETIC_R8',f.f.d,f.f.raw_file,agent_id='SYNTHETIC_MAIN',workflow_revision=8)
        public=f.public(prepared);public_research({'market_assessment':public['market_assessment']})
        frozen=f.f.freeze_market(prepared['run'],public)
        packet=read(frozen['portfolio_context'])
        private=f.personal(frozen)
        private['allocation_review'].pop('allocation_decision_hash')
        private['allocation_review'].update(portfolio_risk_hash=None,strategy_advice_hash=None,investment_decision_hash=None,
                                           issue_reviews=deepcopy(packet['open_investment_issues']))
        with self.assertRaisesRegex(Invalid,'upstream completion'):
            run_flow.advance(prepared['run'],'observe',template_path=frozen['observation_template'],answers_path=f.f.file(private))
        private['main_evaluation']={'required':True,'reasons':['Missing whole-portfolio choice needs the original research/adoption path.']}
        result=run_flow.advance(prepared['run'],'observe',template_path=frozen['observation_template'],answers_path=f.f.file(private))
        self.assertTrue(result['main_eligible'])
        context=read(result['report_context'])
        self.assertIsNone(context['investment_decision'])
        self.assertEqual(context['applicability']['portfolio']['outcome'],'REVISE')

    def test_implementation_gaps_constrain_advice_and_complete_report_consumer(self):
        from v4 import authored_report
        from v4.mobile_report import QUALITY
        value=deepcopy(self.value)
        value.update(status='PARTIAL',selected_ids=['adjust'])
        value['candidates'][1]['verdict']='CONDITIONAL'
        value['gaps'][0]['impact']='QUANTITY_ONLY'
        self.validate(value)
        rec=deepcopy(self.core['recommendations'])
        rec['upper_short'].update(status='RECOMMENDED',investment_candidates=['adjust'])
        with self.assertRaisesRegex(Invalid,'execution-ready'):
            investment.validate_outputs(value,rec)
        rec['upper_short']['status']='EXECUTION_LIMITED'
        investment.validate_outputs(value,rec)
        rec['upper_short'].pop('investment_candidates')
        with self.assertRaisesRegex(Invalid,'selected whole'):
            investment.validate_outputs(value,rec)
        ctx={'current_strategy':self.core,'identity':{'decision_id':'synthetic','fact_version':self.core['fact_version']},
             'evaluated_at':self.stamp,'knowledge_cutoff':self.stamp,'market_revision':8,'report_contract_revision':3}
        results=closed_loop.report_inputs(ctx)
        market=results['market_result']['market_assessment']['summary']
        personal=results['portfolio_risk']['summary']
        layout={'format':authored_report.FORMAT,'body':market+'\n\n'+personal,
                'bindings':[{'scope':'market','text':market,'refs':['/market_result/market_assessment/summary']},
                            {'scope':'personal','text':personal,'refs':['/portfolio_risk/summary']}],
                'price_status':dict.fromkeys(('lower_spot','upper_short'),'REVIEW_REQUIRED'),
                'quality_review':{k:'Concrete synthetic expression review for this negative control.' for k in QUALITY|{'market_independence','overall_choice','upstream_completeness'}}}
        spec={'layout':layout,'context':ctx,'result_binding':results['result_binding']}
        with self.assertRaisesRegex(Invalid,'consume the formed overall'):
            authored_report.render(spec,{'assessment':self.core})
        summary=results['investment_decision']['summary']
        layout['body']+='\n\n'+summary
        layout['bindings'].append({'scope':'personal','text':summary,'refs':['/investment_decision/summary']})
        with self.assertRaisesRegex(Invalid,'capabilities must reach'):
            authored_report.render(spec,{'assessment':self.core})
        identity=results['investment_decision']['validation_limits'][0]['candidate_id']
        layout['body']+='\n\n'+identity
        layout['bindings'].append({'scope':'personal','text':identity,'refs':['/investment_decision/validation_limits/0/candidate_id']})
        with self.assertRaisesRegex(Invalid,'concrete limitations'):
            authored_report.render(spec,{'assessment':self.core})
        layout['body']=layout['body'].rsplit('\n\n',1)[0]
        layout['bindings'].pop()
        limitation=results['investment_decision']['validation_limits'][0]['limitations'][0]
        layout['body']+='\n\n'+limitation
        layout['bindings'].append({'scope':'personal','text':limitation,'refs':['/investment_decision/validation_limits/0/limitations/0']})
        self.assertEqual(authored_report.render(spec,{'assessment':self.core})[0],layout['body'])

    def test_unreviewed_margin_financing_cannot_be_silently_omitted(self):
        for change in (lambda v:v['candidates'][0]['validation'].update(required_capabilities=[]),
                       lambda v:v['gaps'][0].pop('capabilities'),
                       lambda v:v['evaluation_windows'].append(deepcopy(v['evaluation_windows'][0]))):
            bad=deepcopy(self.value);change(bad)
            with self.assertRaises(Invalid):self.validate(bad)


    def test_decided_outputs_match_actual_products_and_capability_gaps(self):
        value=deepcopy(self.value)
        value.update(status='PARTIAL',selected_ids=['adjust'])
        chosen=value['candidates'][1]
        chosen.update(verdict='SUPPORTED',dependencies=[])
        chosen['validation'].update(products=['CASH_SPOT'],required_capabilities=[],limitations=[])
        value['candidates'][0]['dependencies']=[]
        value['candidates'][0]['verdict']='REJECTED'
        value['candidates'][0]['validation'].update(products=['CASH_SPOT'],required_capabilities=[],limitations=[])
        value['gaps']=[]
        self.validate(value)
        rec=deepcopy(self.core['recommendations'])
        rec['upper_short'].update(status='RECOMMENDED',investment_candidates=['adjust'])
        with self.assertRaisesRegex(Invalid,'derivative candidate'):
            investment.validate_outputs(value,rec)
        rec['upper_short']['status']='UNDETERMINED'
        rec['portfolio']['strategy_advice']=[{'status':'DECIDED','execution':{'status':'READY'},
            'investment_candidates':['adjust'],'implementation_products':['CROSS_MARGIN_DERIVATIVE']}]
        with self.assertRaisesRegex(Invalid,'actual advice products'):
            investment.validate_outputs(value,rec)
        rec['portfolio']['strategy_advice'][0]['implementation_products']=['CASH_SPOT']
        investment.validate_outputs(value,rec)
        chosen['validation'].update(products=['RESEARCH_OR_POLICY'])
        action=rec['portfolio']['strategy_advice'][0]
        action.update(implementation_products=['RESEARCH_OR_POLICY'],decision='OPEN')
        with self.assertRaisesRegex(Invalid,'trading action'):
            investment.validate_outputs(value,rec)
        action.update(decision='ADJUST',execution={'status':'NOT_APPLICABLE'})
        investment.validate_outputs(value,rec)
        bounded=deepcopy(self.value)
        bounded.update(status='PARTIAL',selected_ids=['adjust'])
        bounded['candidates'][1]['verdict']='CONDITIONAL'
        bounded['gaps'][0]['impact']='VALUE'
        self.validate(bounded)
        rec['portfolio']['strategy_advice']=[]
        rec['upper_short'].update(status='RECOMMENDED',investment_candidates=['adjust'])
        with self.assertRaisesRegex(Invalid,'execution-ready'):
            investment.validate_outputs(bounded,rec)

    def test_replacement_issue_is_real_acyclic_and_legacy_allocation_is_retained(self):
        issue=deepcopy(self.value['issues'][0])
        issue.update(disposition='REPLACED',explanation='Replace the specific question.',next_evidence=None,successor='missing')
        with self.assertRaisesRegex(Invalid,'actual successor'):
            investment.validate_issues([issue])
        successor=deepcopy(issue);successor.update(id='missing',successor=issue['id'])
        with self.assertRaisesRegex(Invalid,'cannot cycle'):
            investment.validate_issues([issue,successor])
        successor.update(disposition='PENDING_FACTS',successor=None,next_evidence='Confirmed implementation facts.')
        investment.validate_issues([issue,successor])
        old={'recommendations':{'portfolio':{'allocation_decision':{'actions':[
            {'id':'old-net','scope':'Combined portfolio','status':'UNDETERMINED','arrangement':'Choose the joint net adjustment.',
             'execution':{'status':'UNDETERMINED','limits':['Actual exposure is missing.']}},
            {'id':'old-size','scope':'Tactical sleeve','status':'DECIDED','arrangement':'Reduce the unsupported sleeve.',
             'execution':{'status':'LIMITED','limits':['Actual quantity is missing.']}}]}}}}
        retained=investment.open_issues(old)
        self.assertEqual({i['id'] for i in retained},{'legacy:portfolio-synthesis','legacy:allocation:old-net','legacy:allocation:old-size'})
        with self.assertRaisesRegex(Invalid,'every prior'):
            investment.validate_issue_review(retained[:1],old,True)

    def test_changed_confirmed_facts_do_not_reuse_old_personal_synthesis(self):
        changed=dict(self.core['fact_version']);changed['资产配置.md']=digest('changed confirmed allocation')
        ctx={'current_strategy':self.core,'identity':{'decision_id':'synthetic','fact_version':changed},
             'evaluated_at':self.stamp,'market_revision':8,'report_contract_revision':3}
        results=closed_loop.report_inputs(ctx)
        self.assertIsNone(results['investment_decision'])
        self.assertIsNone(results['portfolio_narrative'])
        self.assertEqual(results['applicability']['portfolio']['outcome'],'REVISE')
        self.assertEqual(results['market_result']['market_assessment'],self.core['outlook']['market_assessment'])

    def test_personal_revision8_can_carry_original_public_revision7_without_rewriting(self):
        from v4 import assessment,cycle
        mixed=deepcopy(self.core)
        for market in mixed['outlook']['market_assessment']['markets']:
            for horizon in market['horizons']:
                for key in ('momentum','price_attractiveness','strategy_applicability'):horizon.pop(key)
        for factor in mixed['factor_assessment']['items']:factor.pop('decision_review')
        frozen=deepcopy(mixed['outlook'])
        mixed['recommendations']['portfolio']['risk_assessment']['market_result_hash']=closed_loop.public_hash(mixed)
        mixed['recommendations']['portfolio']['investment_decision']['market_result_hash']=closed_loop.public_hash(mixed)
        mixed['section_provenance']['outlook']['mode']='REUSED'
        assessment.validate_analysis_sections(mixed,8,lambda ref:None)
        cycle.validate_candidate(cycle.candidate(mixed))
        self.assertEqual(mixed['outlook'],frozen)
        mixed['section_provenance']['outlook']['mode']='ASSESSED'
        with self.assertRaisesRegex(Invalid,'horizon'):
            assessment.validate_analysis_sections(mixed,8,lambda ref:None)

    def test_personal_policy_ledger_and_issues_never_enter_public_whitelist(self):
        ctx={'revision':8,'identity':{'market_evidence_id':digest('public'),
             'fact_version':self.core['fact_version'],'decision_id':'synthetic'},
             'current_core':deepcopy(self.core),'factor_context':{},'recorded_policy_inputs':[self.value['policy']],
             'investment_feedback':{'ledger':{'cash':'unknown'},'private_execution':'not a public fact'},
             'investment_review':{'open_issues':self.value['issues']}}
        original=market_state.market_projection(ctx)
        changed=deepcopy(ctx)
        changed['identity']['fact_version']={'资产配置.md':digest('a different confirmed position')}
        changed['recorded_policy_inputs']=[{'objective':'Different personal priority.'}]
        changed['investment_feedback']={'ledger':{'cash':'different'},'private_execution':'changed personal execution'}
        changed['investment_review']={'open_issues':[]}
        changed['current_core']['recommendations']['portfolio']['investment_decision']['summary']='Changed personal choice.'
        self.assertEqual(market_state.market_projection(changed),original)
        for private in ('recorded_policy_inputs','investment_feedback','investment_review'):
            self.assertNotIn(private,original)
