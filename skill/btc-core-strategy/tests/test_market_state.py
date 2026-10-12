"""Revision 5 public/portfolio separation and continuous risk-report scenarios."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_run_flow as adapter_fixture
import test_mobile_report as mobile_fixture
import test_cycle_workflow as cycle_fixture
from v4 import market_state, run_flow, sensing, reporting, mobile_report, cycle, research, evidence, storage, main_flow, delivery
from v4.common import Invalid, read, replace, digest


class FindingReferenceTests(unittest.TestCase):
    def test_extra_findings_reject_wrong_reference_shape_with_field_path(self):
        for refs in ([{'source_id':'raw','pointer':'/value'}], 'price:current', [], [None]):
            with self.subTest(refs=refs), self.assertRaisesRegex(Invalid, r'extra_findings\[0\].refs: expected'):
                sensing.validate_extra_findings([{'summary':'Generated finding','refs':refs,'material':False}], {'price:current'})
        sensing.validate_extra_findings([{'summary':'Generated finding','refs':['price:current'],'material':False}], {'price:current'})


def assessment(stamp, refs=()):
    markets=[]
    for name in ('SPX','NDX','BTC'):
        horizons=[{'id': period, 'period': label, 'direction': direction,
            'structure':'Synthetic observed structure.', 'change':'Synthetic change from the previous observation.',
            'basis':'Synthetic public evidence with competing interpretation.', 'confirmation': certainty,
            'refs':list(refs), 'counter_refs':list(refs)}
            for period,label,direction,certainty in (
                ('short','Model-selected short horizon','BEARISH','PARTIAL'),
                ('wave','Model-selected wave horizon','BEARISH','PARTIAL'),
                ('medium','Model-selected medium horizon','UNKNOWN','UNKNOWN'))]
        markets.append({'id':name,'as_of':stamp,'horizons':horizons,
            'resonance':{'status':'ALIGNED','periods':['short','wave'],'assessment':'Local alignment does not settle the medium horizon.'},
            'reversal':{'status':'NOT_CONFIRMED','periods':['medium'],'assessment':'Contradicting evidence prevents a larger-cycle reversal conclusion.'},
            'conflicts':'Price acceptance conflicts with part of the observed activity.',
            'next_check':'Verify the retained price response and the competing explanation.'})
    return {'markets':markets,'summary':'Synthetic market conclusion independent of all private assets.'}


def zone(stamp, refs=(), stage='SIGNS'):
    return {'id':'btc-area-a','market':'BTC','quote_unit':'USDT',
        'area':{'lower':'79000','upper':'81000','label':'Synthetic contested area'},
        'horizon':'Model-selected wave','as_of':stamp,'status':'ACTIVE',
        'state':'The area remains contested with meaningful downside signs.',
        'breakout':{'direction':'DOWN','stage':stage,'assessment':'Volume and acceptance have different implications.'},
        'long_risk':'Further decline can enlarge long-side loss.',
        'short_risk':'A failed break can cause a sharp adverse rebound.',
        'response':'Assess entry economics independently of the directional view.',
        'conflicts':'Rising activity is not proof of one-sided selling.',
        'change_reason':'The currently observed evidence changes the breakout assessment.',
        'next_check':'Check continuation versus failure and both adverse paths.',
        'retirement_reason':None,'supersedes':[],'refs':list(refs),'counter_refs':list(refs)}


class MarketStateTests(unittest.TestCase):
    def setUp(self):
        self.f=adapter_fixture.RunFlowTests('test_prepare_never_calls_network_with_frozen_input_or_changes_owners')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.store=self.f.f.store
        policy=read(self.store/'write-policy.json')
        replace(self.store/'write-policy.json',dict(policy,minimum_assessment_revision=5))

    def market(self, directory=None, zones=None, patterns=None):
        prepared=self.f.prep(directory)
        run=read(prepared['run']);ctx=read(Path(run['directory'])/'sensing-context.json')
        authored=self.f.authored(prepared)
        raw=read(run['market']);package=read(Path(run['evidence'])/'evidence.json')
        source=next(s for s in package['sources'] if s['id'].startswith('market_'))
        ref={'source_id':source['id'],'pointer':'/datasets/binance_spot_1h','value_hash':digest(raw['datasets']['binance_spot_1h'])}
        public={k:authored[k] for k in market_state.MARKET_FIELDS if k in authored}
        public.update(market_assessment=assessment(raw['cutoff'],[ref]),
                      dispute_zones=[zone(raw['cutoff'],[ref])] if zones is None else zones)
        if patterns is not None:
            public['market_review']['patterns']=patterns
        result=self.f.freeze_market(prepared['run'],public)
        return prepared,result,authored,public,ctx

    def observe(self, directory=None, zones=None, patterns=None):
        prepared,result,authored,public,ctx=self.market(directory,zones,patterns)
        # Portfolio writer supplies only its own fields. Market fields are read
        # from the immutable public result rather than regenerated or copied.
        private={k:authored[k] for k in ('recommendation_checks','main_evaluation')}
        result=run_flow.advance(prepared['run'],'observe',template_path=result['observation_template'],answers_path=self.f.file(private))
        return prepared,result,public

    def test_market_material_has_no_private_inputs_or_mixed_historical_report(self):
        p=self.f.prep();ctx=read(Path(p['run']).parent/'context.json')
        body=json.dumps(ctx)
        for field in ('confirmed_account_facts','fact_files','portfolio_analysis','recommendations','report_narrative','conclusion'):
            self.assertNotIn('"'+field+'"',body)
        self.assertIsNone(ctx['factor_context']['current']['factor_assessment'])
        before=deepcopy(ctx)
        (self.f.f.project/'资产配置.md').write_text((self.f.f.project/'资产配置.md').read_text()+'\nSynthetic private-only note.\n')
        second=self.f.prep(self.f.f.base/'different-facts')
        after=read(Path(second['run']).parent/'context.json')
        self.assertEqual(before,after)

    def test_portfolio_writer_cannot_change_market_result(self):
        prepared,result,authored,public,ctx=self.market()
        authored['market_assessment']=deepcopy(public['market_assessment'])
        authored['market_assessment']['summary']='Changed by personal position.'
        with self.assertRaisesRegex(Invalid,'portfolio stage cannot edit'):
            run_flow.advance(prepared['run'],'observe',self.f.file(authored))

    def test_open_market_assessment_does_not_require_five_legacy_patterns(self):
        _,result,public=self.observe(patterns=[])
        observed=sensing.observation(self.store,result['observation_id'])
        self.assertEqual(observed['review']['market_review']['patterns'],[])
        self.assertEqual(observed['review']['market_assessment'],public['market_assessment'])

    def test_current_observation_reaches_report_and_next_hour(self):
        prepared,result,public=self.observe()
        ctx=reporting.context(self.store)
        brief=run_flow.report_brief(ctx)
        self.assertEqual(brief['observation']['review']['market_assessment'],public['market_assessment'])
        self.assertEqual(brief['observation']['review']['dispute_zones'],public['dispute_zones'])
        next_run=self.f.prep(self.f.f.base/'second-hour')
        material=read(Path(next_run['run']).parent/'context.json')
        self.assertEqual(material['market_baseline']['dispute_zones'],public['dispute_zones'])
        self.assertEqual(material['previous_market_observation']['market_assessment'],public['market_assessment'])
        package=read(Path(next_run['run']).parent/'evidence/evidence.json')
        for ref in public['dispute_zones'][0]['refs']:
            evidence.resolve(package,Path(next_run['run']).parent/'evidence',ref)

    def test_region_cannot_disappear_during_a_quiet_observation(self):
        self.observe()
        with self.assertRaisesRegex(Invalid,'disappeared without retirement'):
            self.market(self.f.f.base/'missing-zone',zones=[])

    def test_retired_zone_survives_quiet_omission_for_later_reclassification(self):
        _, _, public = self.observe()
        original = public['dispute_zones'][0]
        retired = deepcopy(original)
        retired.update(status='RETIRED', retirement_reason='Synthetic boundary role correction.')
        self.observe(self.f.f.base/'retirement', zones=[retired])
        for i in range(2):
            self.observe(self.f.f.base/('quiet-'+str(i)), zones=[])
            actual = market_state.effective(self.store)
            self.assertEqual(actual['dispute_zones'], [retired])
            # A stale formal ACTIVE copy must not revive the retired identity.
            formal = {'outlook': {'market_assessment': public['market_assessment'], 'dispute_zones': [original]}}
            view = market_state.effective(self.store, formal, storage.current(self.store)['decision_id'])
            self.assertEqual(view['dispute_zones'], [retired])
        replacement = deepcopy(original)
        replacement.update(id='btc-internal-corrected', supersedes=[original['id']], role='INTERNAL_LEVEL')
        market_state.validate_zones([retired, replacement], lambda ref: None, actual['dispute_zones'])

    def test_new_zone_result_on_same_raw_hour_is_not_discarded(self):
        _,first,public=self.observe()
        zones=deepcopy(public['dispute_zones'])
        zones[0]['breakout'].update(stage='FAILED',assessment='Synthetic later interpretation confirms failure rather than continuation.')
        zones[0]['change_reason']='The same frozen evidence now invalidates the previously recorded breakout signs.'
        _,second,_=self.observe(self.f.f.base/'same-hour-revised-zone',zones=zones)
        self.assertNotEqual(first['observation_id'],second['observation_id'])
        observed=sensing.observation(self.store,second['observation_id'])
        self.assertEqual(observed['review']['dispute_zones'][0]['breakout']['stage'],'FAILED')

    def test_breakout_states_and_opposing_evidence_remain_model_owned(self):
        original=zone('2030-03-01T13:00:00Z')
        for stage in ('SIGNS','INITIAL_BREAK','CONFIRMED','FAILED','CONTESTED','UNKNOWN'):
            authored=deepcopy(original);authored['breakout']['stage']=stage
            market_state.validate_zones([authored],lambda ref:None,[original])
            self.assertEqual(authored['breakout']['stage'],stage)
            self.assertIn('not proof',authored['conflicts'])
        retired=deepcopy(original);retired.update(status='RETIRED',retirement_reason='The public evidence no longer supports tracking this area.')
        market_state.validate_zones([retired],lambda ref:None,[original])
        market_state.validate_zones([],lambda ref:None,[retired])

    def test_public_market_update_does_not_require_a_current_personal_plan(self):
        _,_,public=self.observe();ctx=reporting.context(self.store)
        ctx.update(deep_report_policy_revision=1,cycle_baseline={'result_id':'old','report_hash':'old-report','status':'EVIDENCE_LIMITED','report_narrative':'old'})
        spec={'report_basis':{'result_id':'old','report_hash':'old-report','mode':'MARKET_UPDATE',
            'verification':'Actual current public observation confirms the material update.',
            'observation_id':ctx['observation']['observation_id'],'evidence_id':ctx['identity']['factor_evidence_id']}}
        cycle.validate_report_basis(spec,ctx)
        spec['report_basis']['observation_id']='wrong'
        with self.assertRaisesRegex(Invalid,'different observation'):
            cycle.validate_report_basis(spec,ctx)
        spec['report_basis']['observation_id']=ctx['observation']['observation_id']
        ctx['observation']['baseline_decision_id']='replaced formal baseline'
        with self.assertRaisesRegex(Invalid,'current formal baseline'):
            cycle.validate_report_basis(spec,ctx)

    def test_research_market_result_cannot_be_changed_during_personal_mapping(self):
        public={'outlook':{'market_assessment':assessment('2030-03-01T13:00:00Z'),'dispute_zones':[]},
                'factor_assessment':{'items':[],'summary':'Public evidence only.'},'market_report':'Independent public market text.'}
        candidate={'assessment':{'revision':5,**deepcopy(public),'conclusion':{'narrative':public['market_report']+'\nPersonal response.'}}}
        market_state.ensure_research_mapping({'market_result':public},candidate)
        candidate['assessment']['outlook']['market_assessment']['summary']='Changed because of private positions.'
        with self.assertRaisesRegex(Invalid,'changed the independent'):
            market_state.ensure_research_mapping({'market_result':public},candidate)


class RevisionFivePublicationTests(unittest.TestCase):
    def setUp(self):
        self.c=cycle_fixture.CycleWorkflowTests()
        self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.f=self.c.f
        self.f.draft['revision']=5
        refs=self.f.draft['temporal_scope']['current_market_refs']
        stamp=self.f.draft['temporal_scope']['knowledge_cutoff']
        self.f.draft['outlook'].update(market_assessment=assessment(stamp,refs),dispute_zones=[zone(stamp,refs)])
        self.public={'outlook':deepcopy(self.f.draft['outlook']),
                     'factor_assessment':deepcopy(self.f.draft['factor_assessment']),
                     'market_report':'Synthetic independent public conclusion and two-sided loss risk.'}
        self.f.draft['conclusion']['narrative']=self.public['market_report']+'\nSynthetic personal adaptation.'
        replace(self.f.store/'write-policy.json',{'minimum_assessment_revision':5,'cycle_review_required':True,'deep_report_required':True})
        self.c.d.bind()

    def adopted(self):
        req,saved,_=self.c.study(authored=True,independent_output=json.dumps(self.public))
        return req,saved,self.c.d.commit()

    def test_actual_frozen_research_maps_and_publishes_revision_five(self):
        req,saved,published=self.adopted()
        self.assertEqual(published['assessment']['revision'],5)
        self.assertEqual(published['assessment']['outlook'],self.public['outlook'])
        packet=research.read_input(self.f.store,req['request_id'])['payload']
        self.assertNotIn('fact_files',packet)
        self.assertIsNone(packet['historical_anchor'])
        personal=research.read_input(self.f.store,req['request_id'],'portfolio',independent_id=saved['independent_id'])['payload']
        self.assertIn('fact_files',personal)
        self.assertEqual(personal['market_result'],self.public)
        original=cycle.baseline(self.f.store,published['assessment'],as_of='2030-03-01T13:04:00Z')
        changed=cycle.baseline(self.f.store,published['assessment'],fact_version={'synthetic':'private change'},as_of='2030-03-01T13:04:00Z')
        self.assertEqual(original['market_status'],changed['market_status'])
        self.assertEqual(changed['portfolio_status'],'FACTS_CHANGED')
        self.assertEqual(original['market_report'],self.public['market_report'])
        self.assertEqual(original['market_report'],changed['market_report'])
        material=market_state.market_projection({'identity':{},'cycle_baseline':changed})
        self.assertEqual(material['market_report'],self.public['market_report'])
        self.assertNotIn('Synthetic personal adaptation',json.dumps(material))

    def test_new_formal_version_cannot_drop_an_active_zone(self):
        _,_,published=self.adopted()
        self.c.d.bind()
        self.f.draft['outlook']['dispute_zones']=[]
        with self.assertRaisesRegex(Invalid,'disappeared without retirement'):
            main_flow.validate_publication(self.f.store, storage.current(self.f.store), self.f.draft)
        self.assertEqual(storage.current(self.f.store)['decision_id'],published['decision_id'])

    def test_public_evidence_corruption_is_still_rejected(self):
        self.public['outlook']['market_assessment']['markets'][0]['horizons'][0]['refs'][0]['value_hash']=digest('wrong raw data')
        with self.assertRaises(Invalid):
            self.c.study(authored=True,independent_output=json.dumps(self.public))

    def test_public_supplement_continues_original_research_after_disclosure(self):
        req, saved, _ = self.adopted()
        original = research._load(self.f.store, 'independent', saved['independent_id'])
        bundle_spec = {k: deepcopy(v) for k, v in self.f.evidence.items()
                       if k not in ('schema', 'evidence_id')}
        bundle_spec.update(parent_id=None, cutoff='2030-03-01T13:03:04Z')
        raw = self.f.base / 'additional-public.json'
        replace(raw, {'finding': 'Synthetic official financing disclosure.'})
        files = {s['id']: self.f.evidence_dir / 'raw' / (s['raw_hash'][7:] + '.json')
                 for s in bundle_spec['sources']}
        source = {'id': 'additional_public', 'origin': 'FED',
                  'url': 'https://www.federalreserve.gov/synthetic-disclosure', 'trust': 'PUBLIC_RAW',
                  'published_at': '2030-03-01T13:03:04Z', 'retrieved_at': '2030-03-01T13:03:04Z',
                  'start': '2030-03-01T13:03:04Z', 'end': '2030-03-01T13:03:04Z',
                  'raw_hash': digest(raw.read_bytes())}
        bundle_spec['sources'].append(source)
        files[source['id']] = raw
        directory = self.f.base / 'independent-supplement'
        bundle = evidence.freeze(bundle_spec, files, directory)
        public = deepcopy(self.public)
        public['outlook']['refs'].append({'source_id': source['id'], 'pointer': '/finding',
                                        'value_hash': digest(read(raw)['finding'])})
        path = Path(original['rollout_path'])
        proof = research.read_input(self.f.store, req['request_id'])
        with path.open('a') as stream:
            for item in (
                {'type': 'turn_context', 'payload': {'turn_id': 'PUBLIC_SUPPLEMENT', 'model': 'gpt-6-astra', 'effort': 'max'}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'call_id': 'supplement-read',
                    'input': 'python strategy_v4.py research-read --request ' + req['request_id'] + ' --phase independent'}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'call_id': 'supplement-read', 'output': json.dumps(proof)}},
                {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'PUBLIC_SUPPLEMENT',
                    'started_at': original['completed_at'] + 1, 'completed_at': original['completed_at'] + 10,
                    'last_agent_message': json.dumps(public)}}):
                stream.write(json.dumps(item) + '\n')
        original_args = (self.f.store, req['request_id'], original['agent_id'], path, 'PUBLIC_SUPPLEMENT')
        with self.assertRaisesRegex(Invalid, 'already read personal or candidate'):
            research.independent(*original_args)
        def continuation(additional=None):
            return research.amend_market(self.f.store, original['independent_id'], path,
                                         turn_id='PUBLIC_SUPPLEMENT', additional=additional)
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:20Z'):
            with self.assertRaisesRegex(Invalid, 'reference source absent'):
                continuation()
            with self.assertRaisesRegex(Invalid, 'archive supplementary'):
                continuation(additional=directory)
            storage.archive_evidence(self.f.store, directory)
            actual = continuation(additional=directory)
        self.assertEqual(actual['market_result'], public)
        self.assertEqual(actual['input_id'], req['independent_input_id'])
        self.assertEqual(actual['independence_status'], 'CONTINUED_PUBLIC_CORRECTION')
        self.assertEqual(actual['turn_id'], original['turn_id'])
        self.assertEqual(actual['parent_independent_id'], original['independent_id'])
        self.assertEqual(actual['evidence_context']['evidence_id'], bundle['evidence_id'])
        self.assertEqual(actual['evidence_context']['knowledge_cutoff'], bundle['cutoff'])
        self.assertEqual(research._load(self.f.store, 'independent', original['independent_id']), original)
        self.assertNotIn(source['id'], json.dumps(proof))
        late_spec = deepcopy(bundle_spec)
        late_spec['cutoff'] = '2030-03-01T13:03:14Z'
        late_dir = self.f.base / 'late-independent-supplement'
        evidence.freeze(late_spec, files, late_dir)
        storage.archive_evidence(self.f.store, late_dir)
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:20Z'), self.assertRaisesRegex(Invalid, 'after the actual completed answer'):
            continuation(additional=late_dir)
        # A raw-source rewrite cannot acquire approval merely by using --additional.
        (directory / 'raw' / (source['raw_hash'][7:] + '.json')).write_text('{}')
        with mock.patch('v4.research.now', return_value='2030-03-01T13:03:20Z'), self.assertRaisesRegex(Invalid, 'raw evidence'):
            continuation(additional=directory)

    def test_model_selected_driver_does_not_require_legacy_factor_recipe(self):
        self.f.draft['domain']['causal_attribution'].update(driver_regime='MIXED',
            driver_components=['CRYPTO_NATIVE_SPOT','MACRO_BETA'])
        import decision_report
        domain=self.f.draft['domain']
        projection=domain['report_semantics']
        level_ids={v['level_id'] for v in projection['decision_relevant_levels_and_sizes']['semantic']}
        for key,value in decision_report._bundle_semantics(domain,level_ids).items():
            projection[key]['semantic']=value
        self.c.d.bind()
        _,_,published=self.adopted()
        self.assertEqual(published['assessment']['domain']['causal_attribution']['driver_regime'],'MIXED')
        # A later round must read that actual new baseline, not reinstate the
        # legacy recipe while validating its compatibility projection.
        self.c.d.bind()
        self.assertIn(self.c.d.commit()['status'], ('UNCHANGED','CONFIRMED_UNCHANGED'))

    def test_native_public_amendment_preserves_original_independent_authorship(self):
        _,saved,_=self.adopted()
        original=research._load(self.f.store,'independent',saved['independent_id'])
        amended=deepcopy(self.public)
        amended['outlook']['market_assessment']['summary']='Synthetic revised public conclusion based on a competing explanation.'
        path=Path(original['rollout_path'])
        with path.open('a') as stream:
            stream.write(json.dumps({'type':'turn_context','payload':{'turn_id':'PUBLIC_AMENDMENT','model':'gpt-6-astra','effort':'max'}})+'\n')
            stream.write(json.dumps({'type':'event_msg','payload':{'type':'task_complete','turn_id':'PUBLIC_AMENDMENT',
                'started_at':original['completed_at']+20,'completed_at':original['completed_at']+30,
                'last_agent_message':json.dumps(amended)}})+'\n')
        revised=research.amend_market(self.f.store,original['independent_id'],path,turn_id='PUBLIC_AMENDMENT')
        research._verify_market_amendment(revised)
        self.assertEqual(revised['market_result'],amended)
        self.assertEqual(revised['parent_independent_id'],original['independent_id'])
        self.assertEqual(revised['turn_id'],original['turn_id'])
        self.assertEqual(research._load(self.f.store,'independent',original['independent_id'])['market_result'],self.public)


class DisputeZoneDisplayTests(unittest.TestCase):
    def setUp(self):
        self.f=mobile_fixture.MobileReportTests('test_sectioned_market_is_invariant_to_personal_portfolio')
        self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_zone_observation_is_independent_and_has_delivery_constraint(self):
        spec=self.f.sectioned();ctx=self.f.f.context(self.f.f.root)
        ctx.update(market_revision=5,observation={'review':{'dispute_zones':[zone('2030-03-01T13:00:00Z')]}})
        spec['layout']['blocks'].append({'kind':'dispute_zone','text':'{{/observation/review/dispute_zones/0/area/label}}：{{/observation/review/dispute_zones/0/long_risk}} {{/observation/review/dispute_zones/0/short_risk}}'})
        body,constraints=mobile_report.render(dict(spec,context=ctx),self.f.f.f.decision)
        self.assertIn('**多空分歧区与突破迹象**',body)
        self.assertIn('failed break',body)
        self.assertTrue(any(v['kind']=='dispute_zone' and 'failed break' in v['source_text'] for v in constraints))
        self.assertLess(body.index('failed break'),body.index('**个人资产应对**'))

    def test_active_zone_cannot_be_silently_omitted_from_report(self):
        spec=self.f.sectioned();ctx=self.f.f.context(self.f.f.root)
        ctx.update(market_revision=5,observation={'review':{'dispute_zones':[zone('2030-03-01T13:00:00Z')]}})
        with self.assertRaisesRegex(Invalid,'independent report item'):
            mobile_report.render(dict(spec,context=ctx),self.f.f.f.decision)

    def test_public_price_risk_cannot_bind_a_personal_recommendation(self):
        spec=self.f.sectioned();ctx=self.f.f.context(self.f.f.root)
        ctx.update(market_revision=5,observation={'review':{'dispute_zones':[]}})
        spec['layout']['blocks'].append({'kind':'condition',
            'text':'{{/assessment/recommendations/lower_spot/status}}'})
        with self.assertRaisesRegex(Invalid,'pure market references'):
            mobile_report.render(dict(spec,context=ctx),self.f.f.f.decision)

    def test_automatic_personal_prices_stay_in_the_personal_section(self):
        spec=self.f.sectioned();ctx=self.f.f.context(self.f.f.root)
        ctx.update(market_revision=5,observation={'review':{'dispute_zones':[]}})
        body,_=mobile_report.render(dict(spec,context=ctx),self.f.f.f.decision)
        self.assertIn('现货参考',body)
        self.assertLess(body.index('**个人资产应对**'),body.index('现货参考'))
        changed=deepcopy(ctx)
        changed['identity']['fact_version']={'synthetic':'New confirmed private facts.'}
        spec['layout']['price_status']={side:'REVIEW_REQUIRED' for side in ('lower_spot','upper_short')}
        after,_=mobile_report.render(dict(spec,context=changed),self.f.f.f.decision)
        # Private applicability changes must not alter either public section.
        self.assertEqual(body.split('**个人资产应对**')[0],after.split('**个人资产应对**')[0])
        self.assertNotEqual(body.split('**个人资产应对**')[1],after.split('**个人资产应对**')[1])

    def test_zone_meaning_survives_source_and_display_readback(self):
        spec=self.f.sectioned()
        spec['layout']['blocks'].append({'kind':'dispute_zone','text':'当前区域下破迹象增强，但承接形成反证；多头防止延伸损失，空头防止失败回抽，突破尚未确认。'})
        source=self.f.source(spec)
        root=self.f.f.root
        identity=delivery.enqueue(root,self.f.f.f.decision['decision_id'],self.f.f.f.destination,source)['delivery_id']
        self.f.f.begin(identity,source)
        receipt=self.f.f.f.receipt(identity)
        for key in ('phone_readability','decision_usefulness'):
            receipt['review']['checks'][key]={'passed':True,'explanation':'Synthetic display keeps breakout signs, uncertainty and both loss paths separate from personal response.'}
        self.assertTrue(any(v['kind']=='dispute_zone' for v in source['constraints']))
        self.assertEqual(delivery.transition(root,identity,'CONFIRM',receipt)['state'],'DELIVERED')


if __name__=='__main__':
    unittest.main()
