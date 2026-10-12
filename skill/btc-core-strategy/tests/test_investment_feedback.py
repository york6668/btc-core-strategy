"""Generated ledger and validation behavior, not real account or profit evidence."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from v4 import portfolio_ledger as ledger, strategy_validation as validation, evidence, investment
from v4.common import Invalid, create, digest, facts, pointer, read


class LedgerTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='btc-ledger-test-');self.addCleanup(temp.cleanup)
        self.project=Path(temp.name)/'project';self.project.mkdir();self.root=self.project/'.strategy/v4'
        for name in ('资产配置.md','操作记录.md','纠正记录.md'):
            (self.project/name).write_text('Synthetic confirmed source: NAV 100, 155, 170.5; flow 50; complete USD coverage and costs.\n')
        self.version=facts(self.project)
        self.ref={'file':'操作记录.md','line_start':1,'line_end':1,'quote':(self.project/'操作记录.md').read_text().strip()}
        self.start='2030-03-01T00:00:00Z';self.mid='2030-03-02T00:00:00Z';self.end='2030-03-03T00:00:00Z'

    def event(self,key,kind,at,details):
        return {'id':key,'kind':kind,'observed_at':at,'known_at':self.end,'currency':'USD',
                'fact_version':self.version,'source_refs':[self.ref],'details':details}

    def nav(self,key,at,value):
        return self.event(key,'NAV',at,{'value':value,'scope_complete':True,'costs_included':True,'cash_flows_complete':True})

    def items(self):
        return [self.nav('start',self.start,'100'),self.nav('mid',self.mid,'155'),self.nav('end',self.end,'170.5'),
                self.event('flow','EXTERNAL_FLOW',self.mid,{'amount':'50','timing':'BEFORE_NAV'})]

    def test_confirmed_ledger_net_flow_and_time_weighting(self):
        items=self.items()
        with mock.patch('v4.portfolio_ledger.now',return_value=self.end):
            receipt=ledger.append(self.root,self.project,{'items':items,'expected_versions':dict.fromkeys([i['id'] for i in items])})
        self.assertFalse(receipt['position_facts_changed'])
        result=ledger.measure(ledger.records(self.root,self.end),'USD',self.start,self.end)
        self.assertEqual(result['net_gain'],'20.5');self.assertEqual(result['net_time_weighted_return'],'0.155')
        self.assertEqual(result['effectiveness'],'NOT_VALIDATED')
        self.assertEqual(ledger.records(self.root,self.mid),[]) # not archived then
        self.assertEqual(facts(self.project),self.version)

    def test_unknown_cost_coverage_or_cash_flow_never_becomes_zero(self):
        for field in ('scope_complete','costs_included','cash_flows_complete'):
            items=self.items();items[-2]['details'][field]=False
            result=ledger.measure(items,'USD',self.start,self.end)
            self.assertIsNone(result['net_gain']);self.assertIsNone(result['net_time_weighted_return'])
        items=self.items();items[-1]['details']['amount']=None
        self.assertIsNone(ledger.measure(items,'USD',self.start,self.end)['net_gain'])
        items=self.items();items[-1]['observed_at']='2030-03-01T12:00:00Z'
        result=ledger.measure(items,'USD',self.start,self.end)
        self.assertEqual(result['net_gain'],'20.5');self.assertIsNone(result['net_time_weighted_return'])

    def test_unknown_flow_time_cannot_be_excluded_from_a_complete_period(self):
        items=self.items();items[-1]['observed_at']=None
        result=ledger.measure(items,'USD',self.start,self.end)
        self.assertIsNone(result['net_gain'])
        self.assertEqual(result['status'],'DATA_LIMITED')
        self.assertIn('unknown time',result['limitations'][0])

    def test_rejected_batch_and_orphan_views_cannot_change_committed_measurement(self):
        items=self.items()
        with mock.patch('v4.portfolio_ledger.now',return_value=self.end):
            first=ledger.append(self.root,self.project,{'items':items,'expected_versions':dict.fromkeys([i['id'] for i in items])})
        before=(self.root/'investment/ledger/index.json').read_bytes()
        modified=deepcopy(items[-2]);modified['known_at']='2030-03-04T00:00:00Z';modified['details']['value']='999'
        conflicting=self.nav('other',self.start,'100');conflicting['known_at']=modified['known_at']
        with mock.patch('v4.portfolio_ledger.now',return_value=modified['known_at']):
            with self.assertRaisesRegex(Invalid,'changed concurrently'):
                ledger.append(self.root,self.project,{'items':[modified,conflicting],
                    'expected_versions':{'end':first['views']['end'],'other':digest('conflicting')}})
        self.assertEqual((self.root/'investment/ledger/index.json').read_bytes(),before)
        orphan={'schema':'BTC_LEDGER_VIEW_V1','recorded_at':modified['known_at'],'previous_view':first['views']['end'],'item':modified}
        create(self.root/'investment/ledger/versions'/(digest(orphan)[7:]+'.json'),orphan)
        self.assertEqual(ledger.measure(ledger.records(self.root),'USD',self.start,self.end)['net_gain'],'20.5')
        with mock.patch('v4.portfolio_ledger.now',return_value=modified['known_at']):
            ledger.append(self.root,self.project,{'items':[modified],'expected_versions':{'end':first['views']['end']}})
        self.assertEqual(ledger.measure(ledger.records(self.root,self.end),'USD',self.start,self.end)['net_gain'],'20.5')
        self.assertEqual(ledger.measure(ledger.records(self.root),'USD',self.start,self.end)['net_gain'],'849')

    def test_personal_feedback_contains_actual_committed_values_and_source_clocks(self):
        item=self.nav('snapshot',self.start,'100')
        with mock.patch('v4.portfolio_ledger.now',return_value=self.end):
            first=ledger.append(self.root,self.project,{'items':[item],'expected_versions':{'snapshot':None}})
        original=validation.feedback(self.root,None)
        view=original['ledger']['committed_views'][0]
        self.assertEqual(view['view_id'],first['views']['snapshot'])
        self.assertEqual(view['recorded_at'],self.end)
        self.assertEqual(view['item']['details']['value'],'100')
        revised=deepcopy(item);revised['details']['value']='101';revised['known_at']='2030-03-04T00:00:00Z'
        with mock.patch('v4.portfolio_ledger.now',return_value=revised['known_at']):
            ledger.append(self.root,self.project,{'items':[revised],'expected_versions':first['views']})
        current=validation.feedback(self.root,None)
        self.assertNotEqual(original,current)
        self.assertEqual(current['ledger']['committed_views'][0]['item']['details']['value'],'101')
        self.assertEqual(current['ledger']['measurement_interface']['command'],'portfolio-performance')

    def test_idempotent_views_corrections_and_sources_are_not_new_fills(self):
        item=self.nav('snapshot',self.start,'100')
        with mock.patch('v4.portfolio_ledger.now',return_value=self.end):
            first=ledger.append(self.root,self.project,{'items':[item],'expected_versions':{'snapshot':None}})
            retry=ledger.append(self.root,self.project,{'items':[item],'expected_versions':{'snapshot':None}})
        self.assertEqual(first,retry)
        bad=deepcopy(item);bad['details']['value']='99'
        with mock.patch('v4.portfolio_ledger.now',return_value=self.end):
            with self.assertRaises(Invalid):ledger.append(self.root,self.project,{'items':[bad],'expected_versions':{'snapshot':first['views']['snapshot']}})
        bad['source_refs'][0]['quote']='A fabricated confirmed account.'
        with self.assertRaises(Invalid):ledger.validate(bad,self.project)

    def test_profit_contribution_reconciles_but_is_not_causal_or_twr_attribution(self):
        items=self.items()
        items += [self.event('pnl-'+key,'COMPONENT_PNL',self.end,{'component':key,'pnl':value,'period_start':self.start,'period_end':self.end}) for key,value in [('A','15'),('B','5.5')]]
        result=ledger.measure(items,'USD',self.start,self.end)
        self.assertEqual(result['attribution']['residual_pnl'],'0')
        self.assertEqual(result['attribution']['status'],'RECONCILED')
        self.assertIn('NOT_TWR',result['attribution']['basis'])
        items[-1]['details']['pnl']='5'
        self.assertEqual(ledger.measure(items,'USD',self.start,self.end)['attribution']['status'],'INCOMPLETE')

    def test_policy_is_recorded_from_actual_sources_before_a_decision(self):
        with mock.patch('v4.common.now',return_value=self.start):
            result=investment.record_policy(self.root,self.project,{'objectives':['Synthetic principal protection.'],
                'constraints':['No inferred cash or safety.'],'baseline':'Confirmed synthetic purposes, not fills.',
                'unknowns':['Full live quantities.'],'source_refs':[self.ref]})
        self.assertEqual(result['policy']['recorded_at'],self.start)
        self.assertEqual(read(result['record'])['fact_version'],self.version)
        self.assertEqual(result['adoption_status'],'INPUT_ONLY_NOT_A_TRADE_OR_DECISION')


class RuleValidationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='btc-rule-validation-');self.addCleanup(temp.cleanup)
        self.base=Path(temp.name);self.directory=self.base/'evidence'
        costs={'commission':'0.001','spread':'0.002','slippage':'0.001'}
        rows=[{'open_at':'2030-03-%02dT09:00:00Z'%d,'close_at':'2030-03-%02dT16:00:00Z'%d,
               'known_at':'2030-03-%02dT16:00:00Z'%d,'open':str(price),'close':str(price+1)}
              for d,price in enumerate([100,101,102,103,104,102],1)]
        payload={'costs':costs,'rows':rows};raw=self.base/'raw.json';create(raw,payload)
        source={'id':'synthetic-history','origin':evidence.canonical_origin('https://example.invalid/generated-validation'),'url':'https://example.invalid/generated-validation',
                'trust':'PUBLIC_RAW','published_at':'2030-02-28T00:00:00Z','retrieved_at':'2030-03-07T00:00:00Z',
                'start':'2030-02-28T00:00:00Z','end':'2030-03-07T00:00:00Z','raw_hash':digest(raw.read_bytes())}
        spec={'parent_id':None,'cutoff':'2030-03-07T00:00:00Z','sources':[source],'measurements':[], 'verified_facts':[],
              'candidates':[],'gaps':[],'handoff':{'previous_hour':None,'successful_deep':None,'changes_since_hour':[],
                                                'changes_since_deep':[],'active_hypotheses':[],'open_questions':[]}}
        with mock.patch('v4.evidence.now',return_value=spec['cutoff']):
            self.bundle=evidence.freeze(spec,{source['id']:str(raw)},self.directory)
        def ref(path):return {'source_id':source['id'],'pointer':path,'value_hash':digest(pointer(payload,path))}
        self.spec={'rule':{'id':'generated-fixed-rule','kind':'CASH_SPOT_LONG_FLAT_MEAN','recorded_at':'2030-02-28T00:00:00Z',
                           'lookback':2,'capital_fraction':'1','instrument':'SYNTHETIC_SPOT'},
                   'costs':{**costs,'source_refs':{k:ref('/costs/'+k) for k in costs}},
                   'bars':[{'row':r,'ref':ref('/rows/'+str(i))} for i,r in enumerate(rows)],
                   'windows':[{'id':'holdout','train_start':'2030-03-01T09:00:00Z','train_end':'2030-03-02T16:00:00Z',
                               'test_start':'2030-03-03T09:00:00Z','test_end':'2030-03-06T16:00:00Z'}]}

    def run_case(self,spec=None):return validation.backtest(spec or self.spec,self.bundle,self.directory)

    def test_fixed_rule_holdout_cost_sensitivity_and_no_live_profit_claim(self):
        result=self.run_case();self.assertEqual(result['status'],'SIMULATED_NOT_EFFECTIVENESS_VALIDATED')
        self.assertEqual(result['live_performance'],'NOT_VALIDATED')
        points=result['windows'][0]['result']['checkpoints']
        self.assertTrue(all(p['signal_known_at']<p['open_at'] for p in points))
        from decimal import Decimal
        self.assertLess(Decimal(result['sensitivity'][0]['windows'][0]['net_return']),Decimal(result['windows'][0]['result']['net_return']))
        rolling=deepcopy(self.spec)
        rolling['windows'].append({'id':'later','train_start':'2030-03-01T09:00:00Z','train_end':'2030-03-04T16:00:00Z',
                                  'test_start':'2030-03-05T09:00:00Z','test_end':'2030-03-06T16:00:00Z'})
        self.assertEqual(len(self.run_case(rolling)['windows']),2)

    def test_missing_costs_or_unavailable_history_are_data_limited(self):
        spec=deepcopy(self.spec);spec['costs']['commission']=None
        self.assertEqual(self.run_case(spec)['status'],'DATA_LIMITED')
        spec=deepcopy(self.spec);spec['rule']['lookback']=10
        self.assertEqual(self.run_case(spec)['status'],'DATA_LIMITED')

    def test_future_parameters_or_forged_raw_or_costs_are_rejected(self):
        for change in (lambda v:v['rule'].update(recorded_at='2030-03-05T00:00:00Z'),
                       lambda v:v['bars'][0]['row'].update(close='999'),
                       lambda v:v['costs'].update(slippage='0')):
            spec=deepcopy(self.spec);change(spec)
            with self.assertRaises(Invalid):self.run_case(spec)

    def test_stored_cash_simulation_cannot_adopt_leveraged_effectiveness(self):
        import test_investment as decisions
        f=decisions.InvestmentDecisionTests();f.setUp();self.addCleanup(f.doCleanups)
        with mock.patch('v4.strategy_validation.now',return_value=f.stamp):
            receipt=validation.record_backtest(f.f.f.store,self.spec,self.bundle,self.directory)
        record,capabilities=validation.validation_record(f.f.f.store,receipt['validation_id'])
        self.assertEqual(record['result']['adoption_eligibility'],'METHOD_CHECK_ONLY')
        feedback=validation.feedback(f.f.f.store,f.core)
        self.assertEqual(feedback['available_method_validations'][0]['validation_id'],receipt['validation_id'])
        self.assertEqual(feedback['available_method_validations'][0]['result']['live_performance'],'NOT_VALIDATED')
        self.assertNotIn('MARGIN_SURVIVAL',capabilities)
        value=deepcopy(f.value)
        value['candidates'][1]['validation'].update(basis='METHOD_CHECK',record_ids=[receipt['validation_id']])
        with self.assertRaisesRegex(Invalid,'cash spot validation'):
            f.validate(value)
        value['candidates'][1]['validation'].update(products=['CASH_SPOT'],basis='RULE_EFFECTIVENESS',required_capabilities=['RULE_EFFECTIVENESS'])
        with self.assertRaisesRegex(Invalid,'authorize adoption'):
            f.validate(value)


    def test_incomplete_method_cannot_authorize_cash_ready_or_hide_limits(self):
        import test_investment as decisions
        f=decisions.InvestmentDecisionTests();f.setUp();self.addCleanup(f.doCleanups)
        spec=deepcopy(self.spec);spec['costs']['commission']=None
        with mock.patch('v4.strategy_validation.now',return_value=f.stamp):
            failed=validation.record_backtest(f.f.f.store,spec,self.bundle,self.directory)
            completed=validation.record_backtest(f.f.f.store,self.spec,self.bundle,self.directory)
        value=deepcopy(f.value)
        chosen=value['candidates'][1]['validation']
        chosen.update(basis='METHOD_CHECK',products=['CASH_SPOT'],record_ids=[failed['validation_id']],
                      required_capabilities=['CASH_FUNDING','DECLARED_EXECUTION_COSTS'],limitations=[])
        with self.assertRaisesRegex(Invalid,'unfinished'):
            f.validate(value)
        chosen['record_ids']=[completed['validation_id']]
        f.validate(value)
        limits=investment.validation_limits(value)
        self.assertTrue(any(l['basis']=='METHOD_CHECK' and l['limitations'] for l in limits))
        chosen['required_capabilities']=[]
        with self.assertRaisesRegex(Invalid,'funding and cost'):
            f.validate(value)

    def test_judgement_feedback_preserves_original_window_and_cannot_be_returns(self):
        import test_investment as decisions
        f=decisions.InvestmentDecisionTests();f.setUp();self.addCleanup(f.doCleanups)
        owner={'assessment':f.core}
        identity=digest(owner)[7:]
        create(f.f.f.store/'versions'/identity/'decision.json',dict(owner,decision_id=identity))
        window=f.value['evaluation_windows'][0]
        # This generated source is later than the original frozen hypothesis.
        spec={'decision_id':identity,'window_id':window['id'],'outcome':'REALIZED','explanation':'Synthetic later evidence tests the original mechanism; this is not a return.',
              'observed_at':window['end'],'evidence_refs':[self.spec['bars'][0]['ref']]}
        original=read(f.f.f.store/'versions'/identity/'decision.json')
        with mock.patch('v4.strategy_validation.now',return_value=self.bundle['cutoff']):
            result=validation.evaluate_judgement(f.f.f.store,spec,self.bundle,self.directory)
        saved=read(result['record'])
        self.assertEqual(saved['baseline_window'],window)
        self.assertEqual(saved['performance'],'NOT_A_RETURN_MEASUREMENT')
        self.assertEqual(read(f.f.f.store/'versions'/identity/'decision.json'),original)
        self.assertEqual(validation.feedback(f.f.f.store,f.core)['judgement_evaluations'],[saved])
        bad=deepcopy(spec);bad['outcome']='NOT_DUE'
        with self.assertRaisesRegex(Invalid,'cannot remain NOT_DUE'):
            validation.evaluate_judgement(f.f.f.store,bad,self.bundle,self.directory)
        bad['decision_id']='../other'
        with self.assertRaisesRegex(Invalid,'original decision identity'):
            validation.evaluate_judgement(f.f.f.store,bad,self.bundle,self.directory)

    def test_real_rule_recording_is_distinct_from_a_declared_historical_time(self):
        self.assertEqual(self.run_case()['parameter_predeclaration'],'DECLARED_INPUT_NOT_HISTORICALLY_ARCHIVED')
        spec={k:v for k,v in self.spec['rule'].items() if k!='recorded_at'}
        root=self.base/'state'
        with mock.patch('v4.strategy_validation.now',return_value=self.spec['rule']['recorded_at']):
            recorded=validation.record_rule(root,spec)
        self.assertEqual(recorded['rule']['recorded_at'],self.spec['rule']['recorded_at'])
        with mock.patch('v4.strategy_validation.now',return_value=self.bundle['cutoff']):
            result=validation.record_backtest(root,self.spec,self.bundle,self.directory)
        self.assertEqual(result['result']['parameter_predeclaration'],'ACTUALLY_RECORDED_BEFORE_DECLARED_TEST_WINDOWS')
        self.assertEqual(result['result']['live_performance'],'NOT_VALIDATED')
