"""Frozen adapter scenarios; all stores and account text are synthetic and isolated."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import unittest
from unittest import mock
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_workflow_main as base
import test_market_observation as fixture
from v4 import run_flow, storage, sensing, reporting, report_care
from v4.common import Invalid, create, read, digest, facts


class RunFlowTests(unittest.TestCase):
    def test_resolved_market_correction_is_frozen_in_next_normal_observe_read_set(self):
        correction={'fault_id':'original-fault','scope':'btc/market','kind':'SEMANTIC',
            'evidence_ref':'original-output','impact_ref':'affected-judgement',
            'cause_ref':'reviewed-cause','correction_ref':'changed-release',
            'verification_ref':'isolated-replay'}
        settings={'docker_service':{'enabled':True}}
        with mock.patch('node_config.load',return_value=settings), \
             mock.patch('model_daily.request',return_value={
                 'scope':'btc/market','allowed':True,'faults':[],
                 'accepted_corrections':[correction]}):
            prepared=self.prep()
        self.assertEqual([correction],read(prepared['context'])['accepted_corrections'])
        run=read(prepared['run'])
        read_set=read(run['read_set']['observe'])
        self.assertEqual(prepared['context'],read_set['material'])
        self.assertEqual([correction],read(self.d/'preparation.json')['accepted_corrections'])

    def test_open_authoritative_market_defect_blocks_new_run_before_collection(self):
        settings={'docker_service':{'enabled':True}}
        with mock.patch('node_config.load',return_value=settings), \
             mock.patch('model_daily.request',return_value={'scope':'btc/market','allowed':False,'faults':[{'fault_id':'original'}]}) as gate, \
             mock.patch('market_observation.collect',side_effect=AssertionError('collection must not start')):
            with self.assertRaisesRegex(Invalid,'known public-market defect'):
                self.prep()
        gate.assert_called_once_with('model.defect.check',{'scope':'btc/market'})
        self.assertFalse((self.d/'run.json').exists())
        with mock.patch('node_config.load',return_value=settings), \
             mock.patch('model_daily.request',side_effect=RuntimeError('node unavailable')):
            with self.assertRaisesRegex(RuntimeError,'node unavailable'):
                self.prep()
        self.assertFalse((self.d/'run.json').exists())
        with mock.patch('node_config.load',return_value=settings), \
             mock.patch('model_daily.request',return_value={'scope':'btc/market','allowed':True,'faults':[]}):
            with self.assertRaisesRegex(Invalid,'lacks correction receipt contract'):
                self.prep()
        self.assertFalse((self.d/'run.json').exists())
        with mock.patch('node_config.load',return_value=settings), \
             mock.patch('model_daily.request',return_value={'scope':'btc/market','allowed':True,'faults':[],
                                                            'accepted_corrections':[]}):
            prepared=self.prep()
        self.assertTrue(Path(prepared['run']).is_file())
        # Recovery of that pinned run remains possible while a later defect
        # is open; only new repeated market work is stopped.
        with mock.patch('node_config.load',return_value=settings), \
             mock.patch('model_daily.request',side_effect=AssertionError('existing run should not be readmitted')):
            self.assertEqual(prepared,self.prep())

    def test_waiting_attempt_is_history_only_through_prepare_and_report_material(self):
        waiting = {'delivery_id':'HISTORICAL_DELIVERY', 'attempt_id':'HISTORICAL_ATTEMPT',
            'state':'UNKNOWN', 'next_action':'WAIT_FOR_NEW_EVIDENCE', 'blocks_new_reports':False}
        active = dict(waiting, delivery_id='NEW_DELIVERY', attempt_id='NEW_ATTEMPT', next_action='READ_BACK_THIS_ATTEMPT')
        with mock.patch('v4.delivery.attempt_recovery', return_value=[waiting, active]):
            prepared = self.prep()
            brief = read(prepared['context'])
            self.assertEqual(brief['pending_delivery'], [active])
            self.assertEqual(brief['attempt_recovery'], [active])
            self.assertEqual(read(self.d/'recovery.json')['attempt_recovery'], [waiting, active])
            output, _ = self.observe()
            self.assertEqual(output['delivery_recovery'], [active])
            report = read(output['report_context'])
            self.assertEqual(report['pending_delivery'], [active])
            self.assertNotIn('HISTORICAL_ATTEMPT', str(report))

    def test_partial_prepare_explicitly_resumes_original_snapshot_without_collection(self):
        from v4 import collector
        snapshot = {'schema':collector.SCHEMA,'role':'auxiliary_observation',
                    'captured_at':'2030-03-01T13:00:00Z','independent_verified_sources':0,
                    'requires_independent_verification':True,'requires_current_source_check':True,
                    'assets':{asset:{'status':'UNAVAILABLE','reasons':['SYNTHETIC']} for asset in collector.ASSETS}}
        before = run_flow.anchors(self.f.store, self.f.project)
        with mock.patch('v4.collector.capture', return_value=snapshot), \
             mock.patch('v4.evidence.compact_root', side_effect=Invalid('retained evidence source collision')):
            with self.assertRaisesRegex(Invalid, 'source collision'):
                self.prep()
        frozen = {name:(self.d/name).read_bytes() for name in ('market.json','collector.json')}
        with mock.patch('v4.collector.capture', side_effect=AssertionError('must not capture again')), \
             mock.patch('market_observation.collect', side_effect=AssertionError('must not recollect')):
            with self.assertRaisesRegex(Invalid, '--resume-preparation'):
                self.prep()
            result = run_flow.prepare(self.f.store,self.f.project,'SYNTHETIC_RUN',self.d,
                self.raw_file,agent_id='SYNTHETIC_MAIN',resume_preparation=True)
        self.assertEqual(before, run_flow.anchors(self.f.store,self.f.project))
        self.assertEqual(frozen, {name:(self.d/name).read_bytes() for name in frozen})
        self.assertTrue(Path(result['run']).is_file())
        with self.assertRaisesRegex(Invalid, 'different execution'):
            run_flow.prepare(self.f.store,self.f.project,'OTHER_RUN',self.d,
                self.raw_file,agent_id='SYNTHETIC_MAIN',resume_preparation=True)

    def test_legacy_partial_directory_requires_new_directory_and_explicit_frozen_inputs(self):
        from v4 import collector
        create(self.d/'market.json',self.f.raw)
        with self.assertRaisesRegex(Invalid, '--resume-preparation'):
            self.prep()
        with self.assertRaisesRegex(Invalid, 'legacy incomplete preparation'):
            run_flow.prepare(self.f.store,self.f.project,'SYNTHETIC_RUN',self.d,
                self.raw_file,agent_id='SYNTHETIC_MAIN',resume_preparation=True)
        snapshot = {'schema':collector.SCHEMA,'role':'auxiliary_observation',
                    'captured_at':'2030-03-01T13:00:00Z','independent_verified_sources':0,
                    'requires_independent_verification':True,'requires_current_source_check':True,
                    'assets':{asset:{'status':'UNAVAILABLE'} for asset in collector.ASSETS}}
        create(self.d/'collector.json', snapshot)
        original=(self.d/'collector.json').read_bytes()
        with mock.patch('v4.collector.capture', side_effect=AssertionError('no reader or new capture')):
            result=run_flow.prepare(self.f.store,self.f.project,'SYNTHETIC_RUN',self.f.base/'continued',
                self.d/'market.json',agent_id='SYNTHETIC_MAIN',collector_snapshot=self.d/'collector.json')
        self.assertEqual((Path(result['run']).parent/'collector.json').read_bytes(), original)
        self.assertEqual((self.d/'collector.json').read_bytes(),original)

    def _partial_prepare_after_reference_index(self):
        from v4 import collector
        raw = deepcopy(self.f.raw)
        for name in ('datasets', 'windows'):
            raw[name] = {key: raw[name][key] for key in sorted(raw[name], reverse=True)}
        snapshot = {'schema': collector.SCHEMA, 'role': 'auxiliary_observation',
                    'captured_at': '2030-03-01T13:00:00Z', 'independent_verified_sources': 0,
                    'requires_independent_verification': True, 'requires_current_source_check': True,
                    'assets': {asset: {'status': 'UNAVAILABLE', 'reasons': ['SYNTHETIC']}
                               for asset in collector.ASSETS}}
        original_save = run_flow.save
        def fail_after_index(path, value):
            if Path(path).name == 'recovery.json':
                raise Invalid('interrupted after reference index')
            return original_save(path, value)
        with mock.patch('market_observation.collect', return_value=raw), \
             mock.patch('v4.collector.capture', return_value=snapshot), \
             mock.patch('v4.run_flow.save', side_effect=fail_after_index):
            with self.assertRaisesRegex(Invalid, 'interrupted after reference index'):
                run_flow.prepare(self.f.store, self.f.project, 'SYNTHETIC_RUN', self.d,
                                 agent_id='SYNTHETIC_MAIN')
        self.assertFalse((self.d/'run.json').exists())
        return self.d/'reference-index.json'

    def test_late_partial_prepare_resumes_after_json_key_order_changes(self):
        index_path = self._partial_prepare_after_reference_index()
        frozen = {name: (self.d/name).read_bytes() for name in
                  ('market.json', 'collector.json', 'reference-index.json', 'sensing-context.json')}
        before = run_flow.anchors(self.f.store, self.f.project)
        with mock.patch('market_observation.collect', side_effect=AssertionError('no recollection')), \
             mock.patch('v4.collector.capture', side_effect=AssertionError('no recapture')):
            result = run_flow.prepare(self.f.store, self.f.project, 'SYNTHETIC_RUN', self.d,
                                      agent_id='SYNTHETIC_MAIN', resume_preparation=True)
        self.assertTrue(Path(result['run']).is_file())
        self.assertEqual(frozen, {name: (self.d/name).read_bytes() for name in frozen})
        self.assertEqual(before, run_flow.anchors(self.f.store, self.f.project))
        self.assertEqual(str(index_path.resolve()), result['reference_index'])

    def test_legacy_reference_order_is_reused_but_changed_content_is_rejected(self):
        index_path = self._partial_prepare_after_reference_index()
        legacy = read(index_path)
        for source in legacy:
            source['refs'].reverse()
        index_path.write_text(json.dumps(legacy), encoding='utf-8')
        original = index_path.read_bytes()
        with mock.patch('market_observation.collect', side_effect=AssertionError('no recollection')), \
             mock.patch('v4.collector.capture', side_effect=AssertionError('no recapture')):
            result = run_flow.prepare(self.f.store, self.f.project, 'SYNTHETIC_RUN', self.d,
                                      agent_id='SYNTHETIC_MAIN', resume_preparation=True)
        self.assertTrue(Path(result['run']).is_file())
        self.assertEqual(original, index_path.read_bytes())
        expected = run_flow.ref_index(read(self.d/'evidence/evidence.json'),
                                      (self.d/'evidence').resolve(), read(self.d/'market.json'))
        run_flow.save_ref_index(index_path, expected)
        self.assertEqual(original, index_path.read_bytes())
        for change in ('value_hash', 'origin'):
            with self.subTest(changed=change):
                corrupted = deepcopy(legacy)
                if change == 'value_hash':
                    next(source for source in corrupted if source['refs'])['refs'][0]['value_hash'] = digest('changed value')
                else:
                    corrupted[0]['origin'] = 'changed source metadata'
                index_path.write_text(json.dumps(corrupted), encoding='utf-8')
                rejected = index_path.read_bytes()
                with self.assertRaisesRegex(Invalid, 'reference-index.json'):
                    run_flow.save_ref_index(index_path, expected)
                self.assertEqual(rejected, index_path.read_bytes())

    def setUp(self):
        self.f = base.MainTests('test_sol_publishes_complete_strategy_without_astra_or_report')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.commit()
        self.d = self.f.base / 'run'
        self.raw_file = self.f.base / 'adapter-raw.json'; create(self.raw_file, self.f.raw)
        self.seq = 0

    def prep(self, directory=None):
        return run_flow.prepare(self.f.store,self.f.project,'SYNTHETIC_RUN',directory or self.d,
                                self.raw_file,agent_id='SYNTHETIC_MAIN')

    def file(self, value):
        self.seq += 1; p = self.f.base / ('authored-'+str(self.seq)+'.json');create(p,value);return p

    def freeze_market(self, run, public):
        # Synthetic authored results exercise the same candidate binding as
        # production; this helper does not simulate model semantic review.
        candidate = run_flow.advance(run, 'market', self.file(public), check_only=True)
        return run_flow.advance(run, 'market', candidate['market_candidate'])

    def authored(self, output):
        value = read(output['observation_template'])
        ctx = read(self.d / 'sensing-context.json')
        mr = fixture.review(self.f.raw,{'context_id':ctx['context_id'],'baselines':{}},required=False)
        value['market_review']={k:v for k,v in mr.items() if k not in ('schema','context_id','evidence_id','observed_hour','model')}
        value.update(recommendation_checks=[{'side':side,'findings':['NO_MATERIAL_CHANGE'],
            'refs':['windows.4h.binance_spot_1h'],'explanation':'Synthetic evidence leaves the current advice applicable.'} for side in ('upper_short','lower_spot')],
            main_evaluation={'required':False,'reasons':[]},factor_checks={'items':[], 'summary':'Synthetic open factor selection, evidence remains limited.'})
        return value

    def test_run_read_cli_returns_content_and_next_cursor_without_completing_a_stage(self):
        import io
        from contextlib import redirect_stdout
        import strategy_v4
        prepared = self.prep()
        before = run_flow.anchors(self.f.store, self.f.project)
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = strategy_v4.main(['--project', str(self.f.project), '--root', str(self.f.store),
                'run-read', '--run', str(prepared['run']), '--read-set', prepared['read_set']['observe'],
                '--max-characters', '29'])
        self.assertEqual(code, 0)
        packet = json.loads(stream.getvalue())
        self.assertEqual(sum(len(v['content']) for v in packet['segments']), 29)
        self.assertEqual(packet['next_cursor'], 29)
        self.assertFalse(packet['complete'])
        self.assertEqual(before, run_flow.anchors(self.f.store, self.f.project))
        self.assertFalse((self.d / 'market-result.json').exists())

    def test_native_reread_resolves_same_store_alias_and_rejects_another_run(self):
        import io
        from contextlib import redirect_stdout
        import strategy_v4
        prepared = self.prep()
        alias = self.f.base / 'logical-store'
        alias.symlink_to(self.f.base, target_is_directory=True)
        run = read(prepared['run'])
        for name in ('directory', 'root', 'project'):
            run[name] = str(alias / Path(run[name]).resolve().relative_to(self.f.base.resolve()))
        Path(prepared['run']).write_text(json.dumps(run))
        original = Path(prepared['run']).read_bytes()
        before = run_flow.anchors(self.f.store, self.f.project)
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = strategy_v4.main(['--project', str(self.f.project), '--root', str(self.f.store),
                'run-read', '--run', prepared['run'], '--read-set', prepared['read_set']['observe'],
                '--max-characters', '29'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stream.getvalue())['next_cursor'], 29)
        self.assertEqual(Path(prepared['run']).read_bytes(), original)
        self.assertEqual(before, run_flow.anchors(self.f.store, self.f.project))
        # Mutation consumers retain their original logical binding contract.
        with self.assertRaisesRegex(Invalid, 'run directory binding differs'):
            run_flow.read_run(prepared['run'], self.f.store, self.f.project)
        other = self.f.base / 'other-run'; other.mkdir()
        (other / 'run.json').write_bytes(original)
        with self.assertRaisesRegex(Invalid, 'run directory binding differs'):
            run_flow.read_run(other / 'run.json', self.f.store, self.f.project, resolve_aliases=True)

    def observe(self):
        prepared=self.prep();p=self.file(self.authored(prepared))
        return run_flow.advance(prepared['run'],'observe',p),p

    def test_prepare_never_calls_network_with_frozen_input_or_changes_owners(self):
        before=run_flow.anchors(self.f.store,self.f.project)
        with mock.patch('market_observation.collect',side_effect=AssertionError('no network')):
            result=self.prep()
        self.assertEqual(before,run_flow.anchors(self.f.store,self.f.project))
        self.assertIsNone(read(result['observation_template'])['main_evaluation']['required'])
        self.assertIn('next_evidence',read(result['context']))
        self.assertEqual(result,self.prep())

    def test_live_prepare_uses_existing_anonymous_collector(self):
        with mock.patch('market_observation.collect',return_value=self.f.raw) as collect:
            run_flow.prepare(self.f.store,self.f.project,'SYNTHETIC_RUN',self.d,agent_id='SYNTHETIC_MAIN')
        collect.assert_called_once_with(deep=False)

    def test_compact_context_retains_factor_guide_sources_and_original_time(self):
        result = self.prep()
        brief = read(result['context'])
        full = read(self.d / 'sensing-context.json')
        self.assertEqual(brief['factor_context'], full['factor_context'])
        self.assertIn('temporal_scope', brief['factor_context']['current'])
        self.assertIn('section_provenance', brief['factor_context']['current'])
        self.assertTrue(brief['factor_context']['evidence']['sources'])
        for source in read(result['reference_index']):
            self.assertIn('url', source)
            self.assertIn('origin', source)

    def test_prepare_keeps_public_fact_from_last_display_without_its_portfolio_prose(self):
        from v4 import evidence
        spec=read(self.f.evidence_dir/'evidence.json')
        value={'rows':{'completed_day':'Previously verified completed flow'}}
        raw_path=self.f.base/'reported-public.json';create(raw_path,value)
        source=deepcopy(next(s for s in spec['sources'] if s['trust']=='PUBLIC_RAW'))
        source.update(id='LATER_REPORTED_PUBLIC',raw_hash=digest(raw_path.read_bytes()))
        files={s['id']:str(self.f.evidence_dir/'raw'/(s['raw_hash'][7:]+'.json')) for s in spec['sources']}
        files[source['id']]=str(raw_path)
        spec.update(parent_id=spec['evidence_id']);spec['sources'].append(source)
        folder=self.f.base/'reported-evidence'
        bundle=evidence.freeze(spec,files,folder,parent=self.f.evidence_dir)
        storage.archive_evidence(self.f.store,folder)
        ref={'source_id':source['id'],'pointer':'/rows','value_hash':digest(value['rows'])}
        artifact={'source':{'report_review':{'context':{
            'identity':{'factor_evidence_id':bundle['evidence_id']},
            'observation':{'review':{'factor_checks':{'refs':[ref]},'portfolio_analysis':'PRIVATE_SENTINEL'}},
            'mixed_report':'PRIVATE_SENTINEL'}}}}
        create(self.f.store/'last-display-current.json',{'delivery_id':'SYNTHETIC_DISPLAY'})
        with mock.patch('v4.delivery.validate_delivered',return_value=(artifact,{},{})):
            result=self.prep()
        saved=read(read(result['run'])['evidence']+'/evidence.json')
        self.assertIn(source,saved['sources'])
        self.assertNotIn('PRIVATE_SENTINEL',Path(result['context']).read_text())

    def test_unfilled_template_cannot_record_an_observation(self):
        prepared=self.prep()
        with self.assertRaises(ValueError):run_flow.advance(prepared['run'],'observe',prepared['observation_template'])
        self.assertFalse((self.f.store/'sensing-current.json').exists())

    def test_unchanged_observation_returns_report_context_and_exact_retry(self):
        before=facts(self.f.project);out,p=self.observe()
        n=len(list((self.f.store/'sensing').glob('*.json')))
        retry=run_flow.advance(self.d/'run.json','observe',p)
        self.assertTrue(retry['replayed']);self.assertEqual(out['observation_id'],retry['observation_id'])
        self.assertEqual(n,len(list((self.f.store/'sensing').glob('*.json'))))
        self.assertTrue(Path(out['report_template']).exists());self.assertEqual(before,facts(self.f.project))
        self.assertNotIn('main_template', out)
        self.assertNotIn('layout_template', out)
        self.assertFalse(list(self.d.glob('*main-context.json')))
        self.assertFalse(list(self.d.glob('*main-template.json')))
        self.assertFalse(list(self.d.glob('*layout-template.json')))
        saved = sensing.observation(self.f.store, out['observation_id'])
        self.assertEqual(saved['review']['market_review']['model'],
                         {'name': 'gpt-6.1-sol', 'reasoning_effort': 'high'})

    def test_old_completed_observation_retry_and_native_recovery_keep_provenance(self):
        import market_observation as market
        with mock.patch.object(market, 'CURRENT_HOURLY_MODEL',
                               {'name': 'gpt-5.6-sol', 'reasoning_effort': 'high'}):
            out, path = self.observe()
        saved = sensing.observation(self.f.store, out['observation_id'])
        self.assertEqual(saved['review']['market_review']['model']['name'], 'gpt-5.6-sol')
        original = {p: p.read_bytes() for p in (self.f.store / 'sensing').glob('*.json')}
        with mock.patch('v4.run_flow.sensing.record', side_effect=AssertionError('must reuse completed observation')):
            self.assertTrue(run_flow.advance(self.d / 'run.json', 'observe', path)['replayed'])
            next(self.d.glob('observe-*-receipt.json')).unlink()
            recovered = run_flow.advance(self.d / 'run.json', 'observe', path)
        self.assertEqual(recovered['status'], 'RECOVERED_OBSERVATION')
        self.assertEqual(recovered['observation_id'], out['observation_id'])
        self.assertEqual({p: p.read_bytes() for p in (self.f.store / 'sensing').glob('*.json')}, original)

    def test_uncompleted_old_observation_intent_is_rejected_without_relabelling(self):
        import market_observation as market
        with mock.patch.object(market, 'CURRENT_HOURLY_MODEL',
                               {'name': 'gpt-5.6-sol', 'reasoning_effort': 'high'}):
            prepared = self.prep(); path = self.file(self.authored(prepared))
            with mock.patch('v4.run_flow.sensing.record', side_effect=RuntimeError('before observation')):
                with self.assertRaises(RuntimeError):
                    run_flow.advance(prepared['run'], 'observe', path)
        intent = next(self.d.glob('observe-*-intent.json'))
        original = intent.read_bytes()
        with self.assertRaisesRegex(Invalid, 'gpt-6.1-sol/high'):
            run_flow.advance(prepared['run'], 'observe', path)
        self.assertEqual(intent.read_bytes(), original)
        self.assertFalse((self.f.store / 'sensing-current.json').exists())

    def test_native_observation_completion_survives_adapter_receipt_interruption(self):
        prepared=self.prep();p=self.file(self.authored(prepared))
        with mock.patch('v4.run_flow.phase_material',side_effect=RuntimeError('adapter interrupted')):
            with self.assertRaises(RuntimeError):run_flow.advance(prepared['run'],'observe',p)
        n=len(list((self.f.store/'sensing').glob('*.json')))
        result=run_flow.advance(prepared['run'],'observe',p)
        self.assertEqual(result['status'],'RECOVERED_OBSERVATION')
        self.assertEqual(n,len(list((self.f.store/'sensing').glob('*.json'))))

    def test_changed_facts_or_raw_are_not_rebound_automatically(self):
        prepared=self.prep();p=self.file(self.authored(prepared))
        (self.f.project/'纠正记录.md').write_text('Changed synthetic confirmed facts.')
        with self.assertRaisesRegex(Invalid,'facts changed'):run_flow.advance(prepared['run'],'observe',p)
        raw=read(self.d/'market.json');raw['cutoff']='2031-01-01T00:00:00Z';(self.d/'market.json').write_text(__import__('json').dumps(raw))
        with self.assertRaisesRegex(Invalid,'raw material changed'):run_flow.advance(prepared['run'],'observe',p)

    def test_other_observation_invalidates_authored_context(self):
        prepared=self.prep();p=self.file(self.authored(prepared))
        second=self.f.base/'other-run';other=self.prep(second)
        run_flow.advance(other['run'],'observe',p)
        with self.assertRaisesRegex(Invalid,'changed_bindings.*observation_id'):run_flow.advance(prepared['run'],'observe',p)

    def test_context_diff_preserves_known_research_stages(self):
        before={'identity':{'decision_id':'same','observation_id':'old'}}
        after={'identity':{'decision_id':'same','observation_id':'new'},'research':{'issues':[
            {'issue_key':'BTC_CYCLE','latest_request':{'request_id':'existing','progress':{'stage':'CANDIDATE_REVEALED','candidate_id':'approved-input'}}}]}}
        delta=run_flow.context_changes(before,after)
        self.assertEqual(delta['changed_bindings'],['observation_id'])
        self.assertEqual(delta['research_progress'][0]['progress']['candidate_id'],'approved-input')

    def test_brief_contains_full_report_once_without_losing_content(self):
        ctx={'cycle_baseline':{'report_narrative':'Full original authored report.'},
             'current_strategy':{'conclusion':{'narrative':'Full original authored report.'}}}
        brief=run_flow.report_brief(ctx)
        self.assertNotIn('report_narrative',brief['cycle_baseline'])
        self.assertEqual(brief['current_strategy'],ctx['current_strategy'])
        self.assertIn('report_narrative',ctx['cycle_baseline'])

    def test_main_reuses_true_sections_without_changing_amounts(self):
        self.observe();out=run_flow.phase_material(read(self.d/'run.json'),'main-test','main')
        auth=read(out['main_template']);prior=storage.current(self.f.store)
        auth['components']['section_reviews']={k:'Synthetic actual review: prior evidence and investment horizon remain applicable.' for k in auth['components']['section_reviews']}
        auth['components']['updates']={'factor_assessment':{'items':[],'summary':'Synthetic first current factor analysis; no fixed factor list.'},
            'conclusion':deepcopy(prior['assessment']['conclusion'])}
        auth['components']['updates']['conclusion']['narrative']='Synthetic value and waiting-cost clarification; original amounts remain applicable.'
        auth['checks']={'checks':{k:'Synthetic actual review of original evidence, uncertainty and portfolio timing.' for k in auth['checks']['checks']},'unresolved_issues':[],'material_new_outlook':False}
        p=self.file(auth)
        answers=self.file({k:v for k,v in auth.items() if k != '_binding'})
        before = {str(p): p.read_bytes() for p in self.f.base.rglob('*') if p.is_file()}
        checked=run_flow.advance(self.d/'run.json','main',template_path=out['main_template'],answers_path=answers,check_only=True)
        self.assertTrue(checked['valid'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.f.base.rglob('*') if p.is_file()})
        result=run_flow.advance(self.d/'run.json','main',template_path=out['main_template'],answers_path=answers)
        current=storage.current(self.f.store)
        self.assertEqual(current['assessment']['provenance']['model'], 'gpt-6.1-sol')
        self.assertEqual(current['assessment']['recommendations'],prior['assessment']['recommendations'])
        self.assertEqual(current['assessment']['section_provenance']['outlook']['mode'],'REUSED')
        self.assertIn('report_template',result)
        self.assertTrue(run_flow.advance(self.d/'run.json','main',p)['replayed'])

    def test_answer_references_keep_exact_content_and_do_not_repair_wrong_hash(self):
        prepared=self.prep();run=read(prepared['run'])
        source=self.d/'author-source.json'
        original={'narrative':'Complete authored thesis, unresolved condition and original deadline.'}
        create(source, original)
        ref={'$value_ref':{'file':str(source),'pointer':'/narrative','value_hash':digest(original['narrative'])}}
        self.assertEqual(run_flow.resolve_answers(run,ref),original['narrative'])
        raw=next(v['refs'][0] for v in read(prepared['reference_index']) if v['refs'])
        self.assertEqual(run_flow.resolve_answers(run,{k:v for k,v in raw.items() if k!='value_hash'}),raw)
        with self.assertRaisesRegex(Invalid,'mismatch'):
            run_flow.resolve_answers(run,dict(raw,value_hash=digest('wrong')))
        with self.assertRaisesRegex(Invalid,'changed'):
            run_flow.resolve_answers(run,{'$value_ref':dict(ref['$value_ref'],value_hash=digest('wrong'))})
        with self.assertRaisesRegex(Invalid,'escapes'):
            run_flow.resolve_answers(run,{'$value_ref':dict(ref['$value_ref'],file=str(self.f.project/'资产配置.md'))})

    def test_main_preflight_collects_independent_failures_without_writing(self):
        from v4 import storage
        candidate=deepcopy(storage.current(self.f.store)['assessment'])
        candidate['fact_version']={}
        for side in ('upper_short','lower_spot'):
            candidate['recommendations'][side]['validity']['until']='2000-01-01T00:00:00Z'
        prior=storage.current(self.f.store)
        before={str(p):p.read_bytes() for p in self.f.store.rglob('*') if p.is_file()}
        with self.assertRaises(Invalid) as failed:
            storage.validate_main(self.f.store,self.f.project,candidate,self.f.evidence_dir,prior['review'])
        for check in ('facts','upper_short_expiry','lower_spot_expiry'):
            self.assertIn(check,str(failed.exception))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.f.store.rglob('*') if p.is_file()})

    def test_initial_question_is_not_suppressed_by_no_research_history(self):
        prepared=self.prep();value=self.authored(prepared)
        index=read(prepared['reference_index']);ref=next(i['refs'][0] for i in index if i['refs'])
        value['research_findings']=[{'issue_key':'MATERIAL_FIRST_QUESTION','scope':['/outlook'],
            'question':'Could a newly observed transmission change the conditional value path?',
            'material_change':True,'change_kind':'INITIAL_QUESTION','change_reason':'Synthetic first important question with verifiable raw evidence.',
            'refs':[ref],'reopen_condition':None}]
        result=run_flow.advance(prepared['run'],'observe',self.file(value))
        self.assertTrue(result['gate']['research_eligible'])

    def test_cli_dispatch_prepares_and_advances_the_bound_project(self):
        import io,json,contextlib,strategy_v4
        common=['--project',str(self.f.project),'--root',str(self.f.store)]
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):
            code=strategy_v4.main(common+['run-prepare','--run-ref','SYNTHETIC_RUN','--agent','SYNTHETIC_MAIN','--directory',str(self.d),'--market',str(self.raw_file)])
        self.assertEqual(code,0);prepared=json.loads(stream.getvalue());p=self.file(self.authored(prepared));stream=io.StringIO()
        with contextlib.redirect_stdout(stream):code=strategy_v4.main(common+['run-advance','--run',prepared['run'],'--stage','observe','--input',str(p)])
        self.assertEqual(code,0);self.assertIn('report_template',json.loads(stream.getvalue()))

    def test_same_hour_new_retrieval_keeps_old_references_and_new_provenance(self):
        from v4 import evidence
        from v4.common import pointer
        import market_observation as market
        first=self.prep();run=read(first['run']);old=read(Path(run['evidence'])/'evidence.json')
        source=next(x for x in old['sources'] if read(Path(run['evidence'])/'raw'/(x['raw_hash'][7:]+'.json'))==self.f.raw)
        ref={'source_id':source['id'],'pointer':'/windows','value_hash':digest(self.f.raw['windows'])}
        newer=deepcopy(self.f.raw)
        for v in newer['retrievals'].values():v['retrieved_at']='2030-03-01T13:02:30Z'
        newer['retrieval_id']=market.digest({'evidence_id':newer['evidence_id'],'retrievals':newer['retrievals']})
        path=self.file(newer);target=self.f.base/'retrieved-again'
        bundle=evidence.compact_root(run['evidence'],path,[ref],target,'2030-03-01T13:03:00Z')
        self.assertIn(source,bundle['sources']);self.assertEqual(evidence.resolve(bundle,target,ref),self.f.raw['windows'])
        copies=[s for s in bundle['sources'] if read(target/'raw'/(s['raw_hash'][7:]+'.json'))==newer]
        self.assertEqual(len(copies),1);self.assertNotEqual(copies[0]['id'],source['id'])
        self.assertEqual(copies[0]['retrieved_at'],'2030-03-01T13:02:30Z')
        self.assertEqual(newer['evidence_id'],self.f.raw['evidence_id'])

    def test_unavailable_window_measurements_remain_unknown(self):
        raw=deepcopy(self.f.raw);span=next(iter(raw['windows']));key=next(iter(raw['windows'][span]));raw['windows'][span][key]['measurements']=None
        self.assertIsNone(run_flow.market_brief(raw)['windows'][span][key]['measurements'])

    def test_window_row_details_are_addressable_without_duplicating_context(self):
        from v4.common import pointer
        original=deepcopy(self.f.raw);brief=run_flow.market_brief(original)
        for span,datasets in brief['windows'].items():
            for key,window in datasets.items():
                if 'raw_rows' in window:
                    self.assertEqual(len(pointer(original,window['raw_rows']['pointer'])),window['raw_rows']['count'])
                    self.assertNotIn('rows',window['measurements'])
                    self.assertEqual(window['measurements'],{k:v for k,v in original['windows'][span][key]['measurements'].items() if k!='rows'})
        self.assertEqual(original,self.f.raw)

    def test_prepare_exposes_candidate_issue_scope_and_retained_price_use(self):
        import market_observation as market
        original=deepcopy(self.f.raw)
        for name in ('coinbase_spot_1h','kraken_spot_1h'):
            dataset=original['datasets'][name]
            self.assertIn('UNCLOSED_OR_FUTURE',dataset['issues'])
            self.assertTrue(dataset['coverage']['complete'])
        prepared=self.prep()
        brief=read(self.d/'market-summary.json')
        self.assertIn('prior_proposition_roles',read(prepared['context']))
        required={row['role'] for row in read(prepared['read_set']['observe'])['required']}
        self.assertIn('market_summary',required)
        for name in ('coinbase_spot_1h','kraken_spot_1h'):
            row=brief['datasets'][name]
            self.assertEqual(row['issue_scope']['UNCLOSED_OR_FUTURE'],
                             'EXCLUDED_CANDIDATE_NOT_RETAINED_ROW')
            self.assertTrue(row['retained_row_use']['all_price_rows_closed_at_cutoff'])
            self.assertTrue(row['retained_row_use']['latest_price_closed_at_cutoff'])
            self.assertTrue(row['retained_row_use']['own_instrument_open_close_usable'])
            self.assertFalse(row['retained_row_use']['quote_taker_balance_usable'])
            self.assertTrue(row['retained_row_use']['requested_window_complete'])
            self.assertLessEqual(market.timestamp(row['latest']['end']),
                                 market.timestamp(brief['cutoff']))
            self.assertEqual(row['quote_unit'],'USD')
        binance=brief['datasets']['binance_spot_1h']
        self.assertTrue(binance['retained_row_use']['quote_taker_balance_usable'])
        self.assertEqual(binance['quote_unit'],'USDT')
        self.assertEqual(original,self.f.raw)

    def test_replay_row_keeps_available_binance_flow_and_peer_limits(self):
        raw=deepcopy(self.f.raw)
        for name in ('binance_spot_1h','coinbase_spot_1h'):
            source=raw['datasets'][name]['rows'][-1]
            replay=run_flow.replay_market_row(raw,name,source['time'])
            self.assertEqual(source,replay['row'])
            self.assertEqual(raw['datasets'][name]['quote_unit'],replay['quote_unit'])
            self.assertEqual(raw['retrievals'][name]['retrieved_at'],replay['retrieved_at'])
            self.assertTrue(replay['coverage_complete'])
            if name=='binance_spot_1h':
                self.assertIsNotNone(replay['row']['quote_volume'])
                self.assertIsNotNone(replay['row']['taker_buy_quote'])
                self.assertTrue(replay['quote_taker_balance_usable'])
            else:
                self.assertFalse(replay['quote_taker_balance_usable'])
                self.assertEqual('EXCLUDED_CANDIDATE_NOT_RETAINED_ROW',
                                 replay['issue_scope']['UNCLOSED_OR_FUTURE'])
        with self.assertRaisesRegex(Invalid,'one complete retained'):
            run_flow.replay_market_row(raw,'binance_spot_1h','1900-01-01T00:00:00Z')
        raw['datasets']['binance_spot_1h']['rows'][-1]['end']='2031-01-01T00:00:00Z'
        with self.assertRaisesRegex(Invalid,'unclosed price'):
            run_flow.replay_market_row(raw,'binance_spot_1h',source['time'])

    def test_prior_proposition_roles_keep_future_condition_out_of_original_assertion(self):
        core={'outlook':{'market_assessment':{'markets':[{'id':'BTC','horizons':[
            {'id':'BTC_SWING','direction':'BEARISH','confirmation':'PARTIAL',
             'structure':'Observed purchases slowed; continued decline is unconfirmed',
             'basis':'Observed 334 BTC purchases',
             'forward':{'conditions':['Historical purchase slowdown supports the limited claim'],
                        'invalidations':['If future purchases stop, downside risk increases']}}
        ]}]}}}
        roles=run_flow.prior_proposition_roles(core)
        self.assertEqual(1,len(roles))
        self.assertEqual('Observed 334 BTC purchases',roles[0]['asserted_state']['basis'])
        self.assertNotIn('If future purchases stop, downside risk increases',
                         str(roles[0]['asserted_state']))
        self.assertIn('If future purchases stop, downside risk increases',
                      roles[0]['future_invalidations'])
        self.assertEqual([],run_flow.prior_proposition_roles({'outlook':{}}))

    def test_frozen_raw_bytes_survive_packaging(self):
        import json
        original=json.dumps(self.f.raw,indent=2).encode()
        self.raw_file.write_bytes(original)
        self.prep()
        self.assertEqual((self.d/'market.json').read_bytes(),original)

    def test_cli_root_and_run_project_must_match(self):
        prepared=self.prep()
        with self.assertRaisesRegex(Invalid,'root does not match'):
            run_flow.read_run(prepared['run'],self.f.base/'unrelated')

    def test_answers_submission_matches_legacy_input_and_recovers(self):
        prepared = self.prep(); authored = self.authored(prepared); path = self.file(authored)
        with mock.patch('v4.run_flow.phase_material', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                run_flow.advance(prepared['run'], 'observe', template_path=prepared['observation_template'], answers_path=path)
        result = run_flow.advance(prepared['run'], 'observe', template_path=prepared['observation_template'], answers_path=path)
        self.assertEqual(result['status'], 'RECOVERED_OBSERVATION')
        self.assertEqual(run_flow.advance(prepared['run'], 'observe', path)['observation_id'], result['observation_id'])
        self.assertIn('observe', prepared['read_set']); self.assertIn('report', result['read_set'])

    def test_answers_do_not_fill_missing_judgments_or_accept_edited_templates(self):
        prepared = self.prep()
        with self.assertRaises(ValueError):
            run_flow.advance(prepared['run'], 'observe', template_path=prepared['observation_template'], answers_path=self.file({}))
        self.assertFalse((self.f.store / 'sensing-current.json').exists())
        Path(prepared['observation_template']).write_text(__import__('json').dumps(self.authored(prepared)))
        with self.assertRaisesRegex(Invalid, 'template was edited'):
            run_flow.advance(prepared['run'], 'observe', template_path=prepared['observation_template'], answers_path=self.file({}))

    def test_cli_accepts_answers_and_template_without_input(self):
        import io, json, contextlib, strategy_v4
        prepared = self.prep(); answers = self.file(self.authored(prepared)); stream = io.StringIO()
        args = ['--project', str(self.f.project), '--root', str(self.f.store), 'run-advance',
                '--run', prepared['run'], '--stage', 'observe', '--template', prepared['observation_template'], '--answers', str(answers)]
        with contextlib.redirect_stdout(stream):
            code = strategy_v4.main(args)
        self.assertEqual(code, 0); self.assertIn('observation_id', json.loads(stream.getvalue()))



class ReportStageTests(RunFlowTests):
    # Inherit setup only; scenarios below exercise the real observation/report owners.
    def setUp(self):
        super().setUp()
        self.clock='2030-03-01T13:05:00Z'
        for target in ('v4.reporting.now','v4.report_care.now'):
            patch=mock.patch(target,side_effect=lambda:self.clock);patch.start();self.addCleanup(patch.stop)
        policy=read(self.f.store/'write-policy.json')
        policy.update(report_care_revision=1,report_care_activated_at=self.clock)
        from v4.common import replace
        replace(self.f.store/'write-policy.json',policy)

    def report_input(self,out,action='SILENT'):
        from datetime import timedelta
        from v4.common import instant
        a=read(out['report_template'])
        a.update(action=action,purposes=['MARKET_SUMMARY'] if action=='REPORT' else [],
            reason='合成测试中已核对当前观察与实际展示，判断是否有需要更新的信息。',
            next_observation='根据当前解释的反证和投资周期继续核对有效信息。',
            baseline_review='历史实际展示缺失保持未知，不补造历史上已经完成的分析。',
            applicability_review='本轮原始引用支持保留现有组合安排，资金与仓位事实没有改变。',
            information={'market':'合成独立解释变化需要更新展示'} if action=='REPORT' else {})
        m=a['maintenance'];future=(instant(self.clock)+timedelta(minutes=30)).isoformat()
        m.update(thesis='当前变化尚未改变中期价值解释，现有安排仍需接受反证检查。',
            support='当前匿名公开证据不足以确认资金机制改变，保留竞争解释。',
            unresolved='合成证据没有识别最终资金来源，不能强造兑现日期。',
            triggers=[{'id':'STRUCTURE','condition':'若多周期证据否定当前承接机制，应重新评估安排。',
                'reason':'这一反证将改变未来价值解释及等待的依据，必须实际检查。','refs':['/metrics/binance_spot_1h/latest/close']}],recheck_at=future)
        m['daily']['report_by']=m['daily']['report_by'] or future
        m['daily']['reason']='根据本次复核选择交付期限，并留出实际发送与回读时间。'
        m['checked'].update(basis_review='已逐项核对上一报告全部条件，当前证据尚未满足反证。',outcome='EVIDENCE_LIMITED' if action=='EVIDENCE_LIMITED' else 'VALID')
        for item in m['checked']['trigger_results']:item.update(state='NOT_MET',evidence='本轮实际公开观察尚未满足所述机制反证，继续核对。')
        if action=='REPORT':
            body_material=run_flow.phase_material(read(self.d/'run.json'),'body-test','report-body')
            a['layout']=read(body_material['layout_template'])
            a['layout'].update(body='币安VOO底层波段区间未破；币安QQQ短线承压，中期估值证据不足。\n\n'
                'BTC短线下跌延续，中期向上涨切换尚未确认。\n\n'
                '产品报价缺失，观察区域不能当作有效建议。\n\n已有现货和保护按确认记录适配，覆盖未知。',
                price_status={'lower_spot':'VALID','upper_short':'VALID'})
            a['information']={'market':'BTC短线下跌延续，中期向上涨切换尚未确认。'}
            a['layout']['quality_review']={k:'已核对核心市场评级、结构及确认程度、价格用途和个人应对，未因手机简短而删除重要限制。' for k in a['layout']['quality_review']}
        return a

    def test_body_stage_supplies_reading_method_and_matching_review_template(self):
        from v4 import authored_report
        self.observe()
        context = reporting.context(self.f.store)
        context['report_contract_revision'] = authored_report.REPORT_CONTRACT_REVISION
        context['identity']['report_contract_revision'] = context['report_contract_revision']
        with mock.patch('v4.reporting.context', return_value=context):
            out = run_flow.phase_material(read(self.d/'run.json'), 'reading-review', 'report-body')
        brief = read(out['report_context'])
        layout = read(out['layout_template'])
        self.assertEqual(set(layout['quality_review']), authored_report.quality_keys(context))
        self.assertEqual(brief['reading_review_instructions'], authored_report.READING_REVIEW)
        self.assertEqual(brief['body_authoring_instructions'], authored_report.BODY_AUTHORING_INSTRUCTIONS)
        self.assertIsNone(layout['body'])
        # Guidance is backstage material, never automatically added to the body.
        legacy = dict(context, report_contract_revision=3)
        self.assertNotIn('reading_review_instructions', reporting.authoring_guidance(legacy))

    def test_report_silence_retry_does_not_advance_delivered_baseline(self):
        out,_=self.observe();p=self.file(self.report_input(out));r=run_flow.advance(self.d/'run.json','report',p)
        self.assertEqual(r['action'],'SILENT');self.assertTrue(run_flow.advance(self.d/'run.json','report',p)['replayed'])
        self.assertFalse((self.f.store/'reported-current.json').exists())
        self.assertEqual(reporting.review(self.f.store, r['review_id'])['provenance']['model'], 'gpt-6.1-sol')

    def test_report_due_without_new_hour_requires_report_and_freezes_source(self):
        out,_=self.observe();run_flow.advance(self.d/'run.json','report',self.file(self.report_input(out)))
        obs=read(self.f.store/'sensing-current.json');self.clock='2030-03-01T13:36:00Z'
        fresh=run_flow.phase_material(read(self.d/'run.json'),'same-hour-due')
        with self.assertRaisesRegex(Invalid,'daily feedback is due'):
            run_flow.advance(self.d/'run.json','report',self.file(self.report_input(fresh)))
        result=run_flow.advance(self.d/'run.json','report',self.file(self.report_input(fresh,'REPORT')))
        self.assertTrue(Path(result['source']).exists());self.assertNotIn('最迟',result['body'])
        self.assertEqual(read(result['source'])['layout']['format'],'AUTHORED_REPORT_V1')
        self.assertEqual(len(read(result['source'])['report_review']['maintenance']['checked']['trigger_results']),1)
        self.assertEqual(obs,read(self.f.store/'sensing-current.json'));self.assertFalse((self.f.store/'reported-current.json').exists())

    def test_all_previous_report_conditions_survive_the_template(self):
        out,_=self.observe();run_flow.advance(self.d/'run.json','report',self.file(self.report_input(out)))
        fresh=run_flow.phase_material(read(self.d/'run.json'),'follow-up');a=self.report_input(fresh)
        self.assertEqual([i['id'] for i in a['maintenance']['checked']['trigger_results']],['STRUCTURE'])
        a['maintenance']['checked']['trigger_results']=[]
        with self.assertRaisesRegex(Invalid,'all prior'):run_flow.advance(self.d/'run.json','report',self.file(a))

    def test_limited_evidence_is_not_silence(self):
        out,_=self.observe();result=run_flow.advance(self.d/'run.json','report',self.file(self.report_input(out,'EVIDENCE_LIMITED')))
        self.assertEqual(result['action'],'EVIDENCE_LIMITED');self.assertNotIn('source',result)

    def test_report_native_completion_is_recovered_without_new_source_identity(self):
        out,_=self.observe();p=self.file(self.report_input(out,'REPORT'))
        with mock.patch('v4.run_flow.reporting.source',side_effect=RuntimeError('interrupted before source')):
            with self.assertRaises(RuntimeError):run_flow.advance(self.d/'run.json','report',p)
        review=read(self.f.store/'report-review-current.json')['review_id']
        result=run_flow.advance(self.d/'run.json','report',p)
        self.assertEqual(result['review_id'],review);self.assertTrue(Path(result['source']).exists())

    def test_answers_report_retains_binding_and_rejects_changed_context(self):
        out, _ = self.observe(); authored = self.report_input(out, 'EVIDENCE_LIMITED')
        authored.pop('_binding'); answers = self.file(authored)
        result = run_flow.advance(self.d / 'run.json', 'report', template_path=out['report_template'], answers_path=answers)
        self.assertEqual(result['action'], 'EVIDENCE_LIMITED')
        self.assertTrue(run_flow.advance(self.d / 'run.json', 'report', template_path=out['report_template'], answers_path=answers)['replayed'])
        authored['reason'] += ' Another actual review.'
        with self.assertRaisesRegex(Invalid, 'stage context changed|stale report maintenance review'):
            run_flow.advance(self.d / 'run.json', 'report', template_path=out['report_template'], answers_path=self.file(authored))

    def test_report_preflight_checks_bound_disclosure_and_rendered_index_before_export(self):
        from v4 import authored_report
        self.observe()
        ctx = reporting.context(self.f.store)
        ctx.update(market_revision=5, report_contract_revision=5)
        ctx['identity']['fact_version'] = dict(ctx['identity']['fact_version'])
        key = next(iter(ctx['identity']['fact_version']))
        ctx['identity']['fact_version'][key] = digest(b'synthetic new generation')
        ctx['context_id'] = digest(ctx['identity'])
        with mock.patch('v4.reporting.context', return_value=ctx):
            out = run_flow.phase_material(read(self.d/'run.json'), 'disclosure-review', 'report-body')
            value = self.report_input(out, 'REPORT')
            quote = '当前公开小时材料提供有限反馈，保留未完成的个人适用性。'
            notice = '个人事实已改变，旧个人建议未完成适配，不作可执行安排。'
            value['layout'].update(body=quote+'\n\n'+notice,
                bindings=[{'text':quote+'\n\n'+notice,'scope':'market',
                           'refs':['/observation/review/factor_checks/summary']}],
                price_status=dict(lower_spot='REVIEW_REQUIRED', upper_short='REVIEW_REQUIRED'),
                quality_review={key:None for key in authored_report.quality_keys(ctx)}, reviewed_candidate_hash=None)
            value['information'] = {'market':quote}
            before = run_flow.anchors(self.f.store,self.f.project)
            def preflight():
                return run_flow.advance(self.d/'run.json','report',self.file(value),check_only=True)
            del value['information']
            with self.assertRaisesRegex(Invalid, 'reviewed information must be a mapping'):
                preflight()
            value['information'] = {'market':quote}
            with self.assertRaisesRegex(Invalid, 'personal response must explain'):
                preflight()
            self.assertEqual(list(self.d.glob('report-review-*.json')), [])
            value['information']['fact_limitations'] = 'Not present in the completed body.'
            with self.assertRaisesRegex(Invalid, 'quote actual rendered content'):
                preflight()
            self.assertEqual(list(self.d.glob('report-review-*.json')), [])
            value['information']['fact_limitations'] = notice
            checked = preflight()
            self.assertEqual(read(checked['review_material'])['candidate']['layout']['body'], value['layout']['body'])
            self.assertEqual(checked['semantic_review'], 'PENDING_ACTUAL_FULL_BODY_REVIEW')
            self.assertEqual(before, run_flow.anchors(self.f.store,self.f.project))
            self.assertFalse((self.f.store/'report-review-current.json').exists())
            self.assertFalse((self.f.store/'outbox').exists())

    def test_report_preflight_binds_full_text_without_recording_or_sending(self):
        from v4 import authored_report
        self.observe()
        ctx = reporting.context(self.f.store)
        ctx['report_contract_revision'] = 5
        ctx['identity']['report_contract_revision'] = 5
        ctx['context_id'] = digest(ctx['identity'])
        with mock.patch('v4.reporting.context', return_value=ctx):
            out = run_flow.phase_material(read(self.d/'run.json'), 'new-review', 'report-body')
            value = self.report_input(out, 'REPORT')
            quote = '当前公开小时材料只提供有限反馈，不补造未完成的判断。'
            value['layout'].update(body=quote,
                bindings=[{'text':quote,'scope':'market','refs':['/observation/review/factor_checks/summary']}],
                quality_review={key:None for key in authored_report.quality_keys(ctx)}, reviewed_candidate_hash=None)
            value['information'] = {'market':quote}
            before = run_flow.anchors(self.f.store,self.f.project)
            checked = run_flow.advance(self.d/'run.json','report',self.file(value),check_only=True)
            self.assertEqual(checked['semantic_review'], 'PENDING_ACTUAL_FULL_BODY_REVIEW')
            candidate = read(checked['review_material'])['candidate']
            self.assertEqual(candidate['layout']['body'],quote)
            self.assertEqual(candidate['layout']['bindings'],value['layout']['bindings'])
            self.assertEqual(before,run_flow.anchors(self.f.store,self.f.project))
            self.assertFalse((self.f.store/'report-review-current.json').exists())
            self.assertFalse((self.f.store/'reported-current.json').exists())
            value['layout']['quality_review'] = {key:'Synthetic actual-candidate record; semantic correctness is independently reviewed.'
                                               for key in authored_report.quality_keys(ctx)}
            reviewed = run_flow.advance(self.d/'run.json','report',self.file(value),check_only=True)
            self.assertEqual(reviewed['reviewed_candidate_hash'], checked['reviewed_candidate_hash'])
            self.assertNotEqual(reviewed['review_material'], checked['review_material'])
            self.assertIsNone(read(checked['review_material'])['candidate']['layout']['quality_review']['evidence_priority'])
            self.assertEqual(run_flow.advance(self.d/'run.json','report',self.file(value),check_only=True)['review_material'], reviewed['review_material'])
            # A rolling clock creates new immutable evidence while keeping the same text identity.
            ctx['evaluated_at'] = '2030-03-01T13:06:00Z'
            refreshed = run_flow.phase_material(read(self.d/'run.json'), 'new-review-clock', 'report-body')
            current = deepcopy(value)
            current['_binding'] = read(refreshed['report_template'])['_binding']
            rechecked = run_flow.advance(self.d/'run.json','report',self.file(current),check_only=True)
            self.assertEqual(rechecked['reviewed_candidate_hash'], checked['reviewed_candidate_hash'])
            self.assertNotEqual(rechecked['review_material'], reviewed['review_material'])
            self.assertEqual(before,run_flow.anchors(self.f.store,self.f.project))
            with self.assertRaisesRegex(Invalid,'has not been reviewed'):
                run_flow.advance(self.d/'run.json','report',self.file(value))
            value['layout']['reviewed_candidate_hash'] = checked['reviewed_candidate_hash']
            final = run_flow.advance(self.d/'run.json','report',self.file(value))
            self.assertEqual(final['body'],quote)
            self.assertFalse((self.f.store/'reported-current.json').exists())
            self.assertFalse((self.f.store/'outbox').exists())

if __name__=='__main__':unittest.main()


# Do not run the parent scenarios twice through inheritance.
for _name in tuple(RunFlowTests.__dict__):
    if _name.startswith('test_'):setattr(ReportStageTests,_name,None)
