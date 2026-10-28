"""Current entrypoints with real frozen bytes/owners; synthetic engineering only."""
from copy import deepcopy
import json, unittest
from pathlib import Path
from unittest import mock
import test_forward_lifecycle as lifecycle_fixture
from test_forward_lifecycle import proposition, forward, register_public, T0, T2, BASE
from v4 import assessment, closed_loop, evidence, market_state, research, storage, sensing, cycle
from v4.common import Invalid, create, digest


class EntryTests(unittest.TestCase):
    setUp = lifecycle_fixture.ForwardTests.setUp
    derived = lifecycle_fixture.ForwardTests.derived
    check = lifecycle_fixture.ForwardTests.check
    def result(self):
        import test_investment as fixture
        from test_closed_loop import market
        m=market(T2);fixture.public_research({'market_assessment':m})
        for item in m['markets']:
            for h in item['horizons']:h['forward']=forward(proposition(item['id']+'-'+h['id'],T2))
        m['markets'][0]['horizons'][0]['forward']=self.derived()
        scenario={'direction':'RANGE','horizon':{'unit':'PROCESS','minimum':None,'maximum':None},
                  'mechanism':['SYNTHETIC_RESPONSE'],'formation_conditions':['ACTUAL_LATER_RESPONSE'],
                  'invalidation_conditions':['OPPOSING_RESPONSE']}
        return {'outlook':{'semantic':{'status':'ASSESSED','baseline':scenario,'alternatives':[deepcopy(scenario)],
                                      'next_evidence':['ACTUAL_NEW_OBSERVATION']},
                          'narrative':'Synthetic bounded outlook.','refs':[], 'market_assessment':m,'dispute_zones':[]},
                'factor_assessment':{'items':[],'summary':'No additional synthetic factors.'},
                'market_report':'Synthetic engineering result; no effectiveness evidence.'}

    def package(self):
        spec={'parent_id':None,'cutoff':T2,'sources':self.bundle['sources'], 'measurements':[],
              'verified_facts':[],'candidates':[],'gaps':[],
              'handoff':{'previous_hour':None,'successful_deep':None,'changes_since_hour':[],
                         'changes_since_deep':[],'active_hypotheses':[],'open_questions':[]}}
        files={s['id']:str(self.directory/'raw'/(s['raw_hash'][7:]+'.json')) for s in spec['sources']}
        target=self.root/'valid-package';evidence.freeze(spec,files,target)
        from v4.common import read
        return target,read(target/'evidence.json')

    def test_actual_public_result_consumer_passes_resolver_owner_and_registration(self):
        directory,package=self.package();create(self.root/'packet.json',{'historical_hypotheses':{}})
        req={'workflow_revision':9,'evidence_id':package['evidence_id'],'independent_input_path':'packet.json'}
        result=self.result()
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):
            accepted=research._public_result(self.root,req,json.dumps(result),directory)
        self.assertEqual(accepted['market_result'],result)
        bad=deepcopy(result);bad['outlook']['market_assessment']['markets'][0]['horizons'][0]['forward']['lifecycle']['reviews'][0]['samples']=[]
        with mock.patch('v4.forward_lifecycle.now',return_value=T2), self.assertRaisesRegex(Invalid,'support label'):
            research._public_result(self.root,req,json.dumps(bad),directory)

    def test_research_legacy_review_needs_no_registration_and_cannot_supply_calendar_proof(self):
        directory,package=self.package()
        registrations=list((self.root/'market-lifecycle/claims').glob('*.json'))
        for mixed in (False,True):
            with self.subTest(mixed=mixed):
                result=self.result();market=result['outlook']['market_assessment']
                horizons=[h for m in market['markets'] for h in m['horizons']]
                for h in horizons[:1] if mixed else horizons:
                    h['forward'].pop('lifecycle')
                    h['forward']['prior_check']['outcome']='EVIDENCE_LIMITED'
                prior=deepcopy(market)
                for m in prior['markets']:
                    m['horizons']=[h for h in m['horizons'] if 'lifecycle' not in h['forward']]
                packet='legacy-packet-'+str(mixed)+'.json'
                create(self.root/packet,{'historical_hypotheses':{'historical_anchor':{
                    'outlook':{'market_assessment':prior}}}})
                req={'workflow_revision':9,'evidence_id':package['evidence_id'],'independent_input_path':packet}
                with mock.patch('v4.forward_lifecycle.now',return_value=T2):
                    accepted=research._public_result(self.root,req,json.dumps(result),directory)
                    self.assertEqual(accepted['market_result'],result)
                    calendar=deepcopy(result)
                    calendar['outlook']['semantic']['baseline']['horizon']={'unit':'DAYS','minimum':1,'maximum':2}
                    with self.assertRaisesRegex(Invalid,'cannot mechanically inherit long calendar forecasts'):
                        research._public_result(self.root,req,json.dumps(calendar),directory)
        self.assertEqual(list((self.root/'market-lifecycle/claims').glob('*.json')),registrations)

    def test_text_and_amendment_readback_recognize_revision9_without_fake_resolver(self):
        result=self.result()
        self.assertEqual(market_state.parse_result(json.dumps(result)),result)
        self.assertEqual(market_state.public_schema_revision(result['outlook']),9)

    def test_unrelated_registered_claim_cannot_realize_current_prior(self):
        other=proposition('unrelated',T0)
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):
            from v4 import forward_lifecycle as life
            register_public(self.root,{'markets':[{'horizons':[{'forward':forward(other)}]}]})
        r=deepcopy(self.review);r['prior_claim_hash']=digest(other)
        new=forward(proposition('independent',T2),[r]);new['prior_check']['outcome']='REALIZED'
        with self.assertRaisesRegex(Invalid,'partial review'):self.check(new)

    def test_original_falsifier_requires_corrected_identity_and_failed_review(self):
        old=deepcopy(self.old);old['expectations'][0]['support']['op']='LT';old['expectations'][0]['falsification']['op']='GT'
        from v4 import forward_lifecycle as life
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old);r['outcomes'][0].update(verdict='FALSIFIED',supported_scope={})
        unchanged=forward(old,[r]);unchanged['prior_check']['outcome']='PENDING'
        with self.assertRaisesRegex(Invalid,'withdrawn|corrected'):self.check(unchanged,{'forward':forward(old)})
        corrected=forward(proposition('corrected',T2,'FACT_CORRECTION'),[r]);corrected['prior_check']['outcome']='PENDING'
        with self.assertRaisesRegex(Invalid,'not remain pending'):self.check(corrected,{'forward':forward(old)})
        corrected['prior_check']['outcome']='REVISE';self.check(corrected,{'forward':forward(old)})

    def test_timestamp_is_not_a_price_measurement(self):
        old=deepcopy(self.old);old['expectations'][0]['support']['left']['path']='/0';old['expectations'][0]['falsification']['left']['path']='/0'
        from v4 import forward_lifecycle as life
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old)
        with self.assertRaisesRegex(Invalid,'unclassified'):self.check(forward(proposition('clock-as-price',T2),[r]),{'forward':forward(old)})

    def test_preformation_open_cannot_borrow_later_candle_close_clock(self):
        old=deepcopy(self.old);old['expectations'][0]['support']['left']['path']='/1';old['expectations'][0]['falsification']['left']['path']='/1'
        from v4 import forward_lifecycle as life
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        row=deepcopy(self.raw[1]);row[0]=1704067199999;row[1]='110'
        path=self.directory/'borrowed-clock.json';create(path,[row]);h=digest(path.read_bytes());create(self.directory/'raw'/(h[7:]+'.json'),path.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][0],id='borrowed',raw_hash=h))
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old);r['samples'][0]['ref']={'source_id':'borrowed','pointer':'/0','value_hash':digest(row)}
        with self.assertRaisesRegex(Invalid,'field predates'):self.check(forward(proposition('borrowed-time',T2),[r]),{'forward':forward(old)})

    def test_current_new_write_entry_refuses_legacy_even_with_old_policy(self):
        create(self.root/'write-policy.json',{'minimum_assessment_revision':8})
        with self.assertRaisesRegex(Invalid,'replay-only'):
            storage.enforce_write_policy(self.root,{'revision':8},{'assessment':{'revision':8}})
        with self.assertRaisesRegex(Invalid,'read-only'):
            sensing.record(self.root,self.root/'unused.sqlite',self.root,{}, {'revision':8})
        storage.enforce_write_policy(self.root,{'revision':9},{'assessment':{'revision':8}})

    def test_reused_unchanged_public_outlook_does_not_invent_another_prior(self):
        outlook=self.result()['outlook']
        # Reuse the exact originally frozen body at its original cutoff. Its
        # original registered predecessor remains available, without reusing
        # today's current market result as a new initial-state check.
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):
            assessment.validate_outlook(outlook,9,self.resolve,T2,lifecycle_root=self.root)
        frozen={'assessment':{'revision':9,'outlook':outlook,'temporal_scope':{'knowledge_cutoff':T2},'evidence_id':digest('original-evidence')},'created_at':T2,'parent_id':None}
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):saved=storage._publish(self.root,frozen,{},None)
        origin={'mode':'REUSED','assessed_at':T2,'decision_id':saved['decision_id'],'evidence_id':digest('original-evidence')}
        draft={'outlook':outlook,'section_provenance':{'outlook':origin},
               'temporal_scope':{'knowledge_cutoff':T2}}
        with mock.patch('v4.market_state.effective',side_effect=AssertionError('reused public body is not a fresh initial review')), \
             mock.patch('v4.forward_lifecycle.now',return_value=T2):
            # Other section errors may still be raised; outlook must pass first.
            with self.assertRaises(KeyError) as missing:assessment.validate_analysis_sections(draft,9,self.resolve,self.root)
        self.assertEqual(missing.exception.args[0],'robustness')


    def test_partial_original_scope_cannot_be_declared_wholly_realized(self):
        old=deepcopy(self.old);old['scope']['venue']=['BINANCE','COINBASE']
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old)
        current=forward(old,[r]);current['prior_check']['outcome']='REALIZED'
        with self.assertRaisesRegex(Invalid,'partial review'):self.check(current,{'forward':forward(old)})

    def test_unknown_component_does_not_delete_another_components_fact(self):
        old=deepcopy(self.old);extra=deepcopy(old['expectations'][0]);extra['id']='unclassified'
        extra['support']['left']['path']='/0';extra['falsification']['left']['path']='/0';old['expectations'].append(extra)
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old)
        unknown={'expectation_id':'unclassified','verdict':'UNKNOWN','supported_scope':{},'explanation':'A timestamp cannot measure a price response.'}
        for order in ([unknown,r['outcomes'][0]],[r['outcomes'][0],unknown]):
            r['outcomes']=order;current=forward(proposition('independent-order',T2),[r])
            result=self.check(current,{'forward':forward(old)})
            self.assertEqual({x['verdict'] for x in result['verification']},{'UNKNOWN','SUPPORTED'})

    def test_unknown_old_observation_tracks_without_grant_or_blocking_correction(self):
        r=deepcopy(self.review);r['samples'][0]['ref']={'source_id':'raw','pointer':'/0','value_hash':digest(self.raw[0])}
        r['outcomes'][0].update(verdict='UNKNOWN',supported_scope={})
        result=self.check(forward(proposition('independent-correction',T2,'FACT_CORRECTION'),[r]))
        self.assertEqual(result['granted_dependencies'],0)

    def test_canonical_market_rows_keep_actual_instrument_measure_clock_and_quote(self):
        raw={'datasets':{'oi':{'group':'BINANCE','market':'PERPETUAL','instrument':'BTCUSDT','quote_unit':'USDT',
                              'kind':'OI','rows':[{'time':'2024-01-02T00:00:00Z','quantity_btc':12,'notional_usdt':1320}]}}}
        path=self.directory/'canonical.json';create(path,raw);h=digest(path.read_bytes());create(self.directory/'raw'/(h[7:]+'.json'),path.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][0],id='canonical',raw_hash=h))
        ref={'source_id':'canonical','pointer':'/datasets/oi/rows/0','value_hash':digest(raw['datasets']['oi']['rows'][0])}
        fact=self.resolve.fact(ref,'/time')
        self.assertEqual(fact['field_scopes']['/quantity_btc']['measure'],'OPEN_INTEREST_QUANTITY')
        self.assertEqual(fact['field_scopes']['/notional_usdt']['measure'],'OPEN_INTEREST_NOTIONAL')
        self.assertEqual(fact['field_scopes']['/quantity_btc']['subject'],'BTC')
        self.assertEqual(fact['field_times']['/quantity_btc'],'2024-01-02T00:00:00+00:00')


    def test_external_freeze_cleanup_and_root_restore_keep_original_registration_clock(self):
        import tempfile,shutil
        from pathlib import Path
        from v4 import forward_lifecycle as life
        with tempfile.TemporaryDirectory() as directory:
            original=Path(directory)/'market-result.json';c=proposition('external-freeze',T2)
            value={'lifecycle_frozen_at':T2,'market_assessment':{'markets':[{'horizons':[{'forward':forward(c)}]}]}}
            create(original,value)
            with mock.patch('v4.forward_lifecycle.now',return_value=T2):life.register(self.root,value['market_assessment'],original,'/lifecycle_frozen_at')
            record=life.registration(self.root,digest(c));self.assertFalse(Path(record['owner']['path']).is_absolute())
        self.assertEqual(life.registration(self.root,digest(c))['registered_at'],T2)
        restored=self.root.parent/(self.root.name+'-restore');shutil.copytree(self.root,restored);self.addCleanup(shutil.rmtree,restored)
        self.assertEqual(life.registration(restored,digest(c))['registered_at'],T2)

    def test_registration_backdate_or_original_freeze_change_cannot_create_proof(self):
        from v4 import forward_lifecycle as life
        from v4.common import replace
        record=life.registration(self.root,digest(self.old));record['registered_at']='2023-12-01T00:00:00Z'
        replace(self.root/'market-lifecycle/claims'/(digest(self.old)[7:]+'.json'),record)
        with self.assertRaisesRegex(Invalid,'clock differs'):life.registration(self.root,digest(self.old))


    def test_current_run_prepare_freeze_reentry_and_private_context_use_real9(self):
        import test_run_flow as historical
        import test_investment as fixture
        from test_closed_loop import market
        from v4 import run_flow,forward_lifecycle as lifecycle
        from v4.common import read
        f=historical.RunFlowTests()
        with mock.patch('v4.storage.NEW_WRITE_REVISION',1):f.setUp()
        self.addCleanup(f.doCleanups)
        stamp=f.f.draft['temporal_scope']['knowledge_cutoff']
        with mock.patch('v4.forward_lifecycle.now',return_value=stamp),mock.patch('v4.run_flow.now',return_value=stamp),mock.patch('v4.sensing.now',return_value=stamp):
            prepared=f.prep();run=read(prepared['run']);ctx=read(f.d/'sensing-context.json')
            self.assertEqual(ctx['revision'],9);self.assertEqual(run['workflow_revision'],9)
            import io,strategy_v4
            from contextlib import redirect_stdout
            window=f.f.base/'execution-window.json'
            create(window,{'schema':'BTC_EXECUTION_WINDOW_V1','status':'UNAVAILABLE','run_ref':'SYNTHETIC_RUN','reason':'Generated test timing; no real scheduler.'})
            stream=io.StringIO()
            with redirect_stdout(stream):
                code=strategy_v4.main(['--runtime-schedule',str(window),'--project',str(f.f.project),'--root',str(f.f.store),
                    'run-prepare','--run-ref','SYNTHETIC_RUN','--agent','SYNTHETIC_MAIN','--directory',str(f.d),'--market',str(f.raw_file)])
            self.assertEqual(code,0);self.assertEqual(json.loads(stream.getvalue())['run'],prepared['run'])
            self.assertTrue(all(ctx['account_constraints'][k] is None for k in ('exposure_complete','cross_margin_complete','funding_complete')))
            directory=run['evidence'];package=read(Path(directory)/'evidence.json')
            raw=read(run['market']);rows=raw['datasets']['binance_spot_1h']['rows']
            source=next(s for s in package['sources'] if read(Path(directory)/'raw'/(s['raw_hash'][7:]+'.json'))==raw)
            ref={'source_id':source['id'],'pointer':'/datasets/binance_spot_1h/rows/'+str(len(rows)-1)+'/close','value_hash':digest(rows[-1]['close'])}
            value=f.authored(prepared);value={k:v for k,v in value.items() if k in market_state.MARKET_FIELDS}
            public=market(package['cutoff']);fixture.public_research({'market_assessment':public})
            for item in public['markets']:
                for h in item['horizons']:
                    c=proposition('current-run-'+item['id']+'-'+h['id'],package['cutoff']);c['basis_refs']=[ref]
                    for condition in ('support','falsification'):c['expectations'][0][condition]['right']['anchor']=ref
                    h['forward']=forward(c)
            value.update(market_assessment=public,dispute_zones=[])
            if 'applicability_review' in value:
                value['applicability_review'].update(outcome='EVIDENCE_LIMITED',explanation='Legacy public initial conclusion lacks registered tests.',limitations='No adopted prospective mechanism has been verified.')
            candidate=run_flow.advance(prepared['run'],'market',f.file(value),check_only=True)
            frozen=run_flow.advance(prepared['run'],'market',candidate['market_candidate'])
            original=(f.d/'market-result.json').read_bytes()
            again=run_flow.advance(prepared['run'],'market',candidate['market_candidate'])
            self.assertEqual(original,(f.d/'market-result.json').read_bytes())
            self.assertEqual(frozen['market_result_hash'],again['market_result_hash'])
            recorded=lifecycle.registration(f.f.store,digest(public['markets'][0]['horizons'][0]['forward']['lifecycle']['claim']))
            self.assertEqual(recorded['registered_at'],stamp)
            portfolio=read(frozen['portfolio_context']);self.assertEqual(portfolio['account_constraints'],ctx['account_constraints'])


    def test_tracking_and_realized_original_bodies_keep_their_actual_owner_on_reuse(self):
        outlook=self.result()['outlook'];first=outlook['market_assessment']['markets'][0]
        h=first['horizons'][0];h['forward']=forward(self.old,[self.review]);h['forward']['prior_check']['outcome']='REALIZED'
        original_prior={'markets':[{'id':first['id'],'horizons':[dict(h,forward=self.prior['forward'])]}]}
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):
            assessment.validate_outlook(outlook,9,self.resolve,T2,original_prior,self.root)
            frozen={'assessment':{'revision':9,'outlook':outlook,'temporal_scope':{'knowledge_cutoff':T2},'evidence_id':digest('original-evidence')},'created_at':T2,'parent_id':None}
            saved=storage._publish(self.root,frozen,{},None)
        origin={'mode':'REUSED','assessed_at':T2,'decision_id':saved['decision_id'],'evidence_id':digest('original-evidence')}
        draft={'outlook':outlook,'section_provenance':{'outlook':origin},'temporal_scope':{'knowledge_cutoff':T2}}
        with self.assertRaises(KeyError) as missing:assessment.validate_analysis_sections(draft,9,self.resolve,self.root)
        self.assertEqual(missing.exception.args[0],'robustness')
        changed=deepcopy(draft);changed['outlook']['narrative']='Forged replacement disguised as reused.'
        with self.assertRaisesRegex(Invalid,'changed its original content'):assessment.validate_analysis_sections(changed,9,self.resolve,self.root)


    def test_equal_numeric_strings_cannot_become_a_changed_price_response(self):
        old=deepcopy(self.old);old['expectations'][0]['support']['op']='NE'
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        row=deepcopy(self.raw[1]);row[4]='100.0'
        path=self.directory/'same-price.json';create(path,[row]);h=digest(path.read_bytes());create(self.directory/'raw'/(h[7:]+'.json'),path.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][0],id='same-price',raw_hash=h))
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old);r['samples'][0]['ref']={'source_id':'same-price','pointer':'/0','value_hash':digest(row)}
        with self.assertRaisesRegex(Invalid,'support label'):self.check(forward(proposition('format-only',T2),[r]),{'forward':forward(old)})

    def test_next_claim_with_new_anchor_retains_registered_predecessor_raw_refs(self):
        from v4 import run_flow
        current=self.derived();new_ref={'source_id':'raw','pointer':'/1/4','value_hash':digest(self.raw[1][4])}
        current['lifecycle']['claim']['basis_refs']=[new_ref]
        for name in ('support','falsification'):current['lifecycle']['claim']['expectations'][0][name]['right']['anchor']=new_ref
        self.check(current)
        direct={r['source_id'] for r in run_flow.refs_in(current)}
        retained={r['source_id'] for r in run_flow.refs_in(current,self.root)}
        self.assertEqual(direct,{'raw'});self.assertEqual(retained,{'before','raw'})


    def test_nonfinite_numeric_equality_cannot_grant_real_price_support(self):
        old=deepcopy(self.old);old['expectations'][0]['support']['op']='NE';old['expectations'][0]['falsification']['op']='EQ'
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        for value in ('NaN','Infinity','-Infinity'):
            row=deepcopy(self.raw[1]);row[4]=value
            path=self.directory/('nonfinite-'+value+'.json');create(path,[row]);h=digest(path.read_bytes());create(self.directory/'raw'/(h[7:]+'.json'),path.read_bytes())
            self.bundle['sources'].append(dict(self.bundle['sources'][0],id='nonfinite-'+value,raw_hash=h))
            r=deepcopy(self.review);r['prior_claim_hash']=digest(old);r['samples'][0]['ref']={'source_id':'nonfinite-'+value,'pointer':'/0','value_hash':digest(row)}
            with self.subTest(value=value),self.assertRaisesRegex(Invalid,'nonfinite'):self.check(forward(proposition('not-real-price',T2),[r]),{'forward':forward(old)})


    def test_numeric_placeholder_does_not_grant_support_and_can_remain_unknown(self):
        old=deepcopy(self.old);old['expectations'][0]['support']['op']='NE';old['expectations'][0]['falsification']['op']='EQ'
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        row=deepcopy(self.raw[1]);row[4]='N/A'
        path=self.directory/'unavailable-price.json';create(path,[row]);h=digest(path.read_bytes());create(self.directory/'raw'/(h[7:]+'.json'),path.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][0],id='unavailable',raw_hash=h))
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old);r['samples'][0]['ref']={'source_id':'unavailable','pointer':'/0','value_hash':digest(row)}
        with self.assertRaisesRegex(Invalid,'numeric observation is unavailable'):self.check(forward(proposition('not-a-number',T2),[r]),{'forward':forward(old)})
        r['outcomes'][0].update(verdict='UNKNOWN',supported_scope={})
        checked=self.check(forward(proposition('independent-correction',T2,'FACT_CORRECTION'),[r]),{'forward':forward(old)})
        self.assertEqual(checked['verification'][0]['verdict'],'UNKNOWN');self.assertEqual(checked['granted_dependencies'],0)


    def test_unknown_support_field_does_not_hide_known_price_falsification(self):
        old=deepcopy(self.old);old['expectations'][0]['support']['left']['path']='/0'
        with mock.patch('v4.forward_lifecycle.now',return_value=T0):register_public(self.root,{'markets':[{'horizons':[{'forward':forward(old)}]}]})
        row=deepcopy(self.raw[1]);row[4]='90'
        path=self.directory/'known-falsifier.json';create(path,[row]);h=digest(path.read_bytes());create(self.directory/'raw'/(h[7:]+'.json'),path.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][0],id='known-falsifier',raw_hash=h))
        r=deepcopy(self.review);r['prior_claim_hash']=digest(old);r['samples'][0]['ref']={'source_id':'known-falsifier','pointer':'/0','value_hash':digest(row)}
        r['outcomes'][0].update(verdict='UNKNOWN',supported_scope={})
        current=forward(proposition('independent-risk',T2,'RISK_RESPONSE'),[r]);current['prior_check']['outcome']='FAILED'
        with self.assertRaisesRegex(Invalid,'falsified expectation'):self.check(current,{'forward':forward(old)})
        r['outcomes'][0]['verdict']='FALSIFIED';current['lifecycle']['reviews']=[r]
        result=self.check(current,{'forward':forward(old)})
        self.assertTrue(result['falsified_prior']);self.assertEqual(result['granted_dependencies'],0)


    def test_new_process_cannot_hide_inherited_date_in_realization_window(self):
        current=self.derived();current['realization_window']={'start':T0,'end':'2024-11-25T00:00:00Z'}
        with self.assertRaisesRegex(Invalid,'inherited forecast date'):self.check(current)

    def test_realization_calendar_requires_its_own_actual_bound_reference(self):
        end='2024-01-04T00:00:00Z';raw={'announced_end':end}
        path=self.directory/'calendar.json';create(path,raw);h=digest(path.read_bytes());create(self.directory/'raw'/(h[7:]+'.json'),path.read_bytes())
        self.bundle['sources'].append(dict(self.bundle['sources'][0],id='calendar',raw_hash=h))
        ref={'source_id':'calendar','pointer':'/announced_end','value_hash':digest(end)}
        current=self.derived();current['lifecycle']['claim']['scale'].update(end=end,end_ref=ref)
        current['judgement_window']['end']=end;current['realization_window']={'start':T2,'end':end}
        self.check(current)


if __name__=='__main__':unittest.main()
