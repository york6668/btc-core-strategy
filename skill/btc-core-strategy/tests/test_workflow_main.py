"""Main strategy publication is independent from research and reporting."""
from copy import deepcopy
from datetime import timezone
from pathlib import Path
import sys, unittest
from unittest import mock
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).parent)]
import test_recommendation_maintenance as base
from v4 import assessment, main_flow, sensing, semantics, storage
from v4.common import Invalid, create, digest, read


def seal(value):
    value['draft_id'] = digest({k:v for k,v in value.items() if k != 'draft_id'})
    return {'schema':'BTC_MAIN_REVIEW_V4','draft_id':value['draft_id'],'evidence_id':value['evidence_id'],
            'reviewer_role':'MAIN','checks':dict.fromkeys(main_flow.CHECKS.split(), 'Synthetic evidence-backed main review.'),
            'unresolved_issues':[], 'material_new_outlook':True}


class MainTests(unittest.TestCase):
    def setUp(self):
        base.RecommendationTests.setUp(self)
        # Persist constructed Owners under the same simulated wall clock as
        # their events. A real 2026 timestamp cannot own a 2030 test plan.
        import strategy_contract as legacy
        clock = mock.patch('v4.storage.now', side_effect=lambda: legacy.datetime.now(timezone.utc).isoformat())
        clock.start(); self.addCleanup(clock.stop)
        base.RecommendationTests.upgrade(self)
        self.draft.update(revision=3, provenance={'role':'MAIN','model':'gpt-6.1-sol','effort':'high','agent_id':'SYNTHETIC_MAIN','run_ref':'SYNTHETIC_TRACE'},
                          conclusion={'business_meaning':{'value':'合成价值结构保持，超卖与溢价方案分别受组合预算约束。'},'narrative':'合成当前解释。'}, research_absorptions=[])
        self.draft.pop('report')
        self.draft['section_provenance'] = {key:{'mode':'ASSESSED','decision_id':None,'assessed_at':self.draft['temporal_scope']['knowledge_cutoff'],
                                                 'evidence_id':self.draft['evidence_id'],'explanation':'Synthetic assessed section.'} for key in main_flow.SECTIONS}
        create(self.store/'write-policy.json', {'minimum_assessment_revision':3, 'required_report_format':'CONCISE_V1'})
        self.bind()

    def bind(self):
        # Compatible with the inherited fixture builder before revision 3 activation.
        ctx = sensing.context(self.store, self.project/'.strategy/market_observation.sqlite3',self.project,self.raw)
        self.draft['maintenance_binding']={'context_id':ctx['context_id'],'market_evidence_id':self.raw['evidence_id']}
        self.review=seal(self.draft) if self.draft.get('revision')==3 else base.seal(self.draft)
        return ctx

    def commit(self):
        self.review=seal(self.draft)
        return storage.commit(self.store,self.project,self.draft,self.evidence_dir,self.review,storage.current(self.store)['decision_id'])

    def report_baseline(self, value):
        create(self.store/'reported-current.json',{'decision_id':value['decision_id'],'delivery_id':'SYNTHETIC_ACCEPTED_REPORT'})

    def test_sol_publishes_complete_strategy_without_astra_or_report(self):
        value=self.commit()
        self.assertEqual(value['assessment']['provenance']['model'],'gpt-6.1-sol')
        self.assertFalse((self.store/'deep-current.json').exists())
        self.assertFalse((self.store/'current/report.md').exists())
        self.assertIn('1000U名义额',(self.store/'current/plan.md').read_text())
        self.assertIn('1000U投入',(self.store/'current/plan.md').read_text())
        ctx=self.bind()
        self.assertEqual(ctx['current_conclusion'],value['assessment']['conclusion'])

    def test_expired_no_entry_review_does_not_require_expiry_extension(self):
        for side in ('upper_short', 'lower_spot'):
            proposal = self.draft['recommendations'][side]
            proposal['status'] = 'NOT_RECOMMENDED'
            proposal['tiers'] = []
            proposal['validity']['until'] = '2030-03-01T12:00:00Z'
        with mock.patch('v4.storage.now', return_value='2030-03-02T13:00:00Z'):
            value = self.commit()
        for side in ('upper_short', 'lower_spot'):
            self.assertEqual(value['assessment']['recommendations'][side]['validity']['until'],
                             '2030-03-01T12:00:00Z')
            self.assertEqual(value['assessment']['recommendations'][side]['tiers'], [])

    def test_expired_executable_entry_still_blocks_main_publication(self):
        before = storage.current(self.store)
        self.draft['recommendations']['upper_short']['validity']['until'] = '2030-03-01T12:00:00Z'
        with mock.patch('v4.storage.now', return_value='2030-03-02T13:00:00Z'):
            with self.assertRaisesRegex(Invalid, 'upper_short_expiry'):
                self.commit()
        self.assertEqual(storage.current(self.store), before)

    def test_canonical_domain_submission_keeps_original_candidate_through_normalization(self):
        from v4 import domain
        for key in ('previous_projection_baseline', 'previous_fingerprints'):
            self.draft['domain'].pop(key, None)
        original = deepcopy(self.draft)
        value = self.commit()
        receipt_path = self.store / read(self.store / 'main-current.json')['completion']
        receipt = read(receipt_path)
        self.assertEqual(receipt['submitted_draft']['domain'], original['domain'])
        self.assertIn('previous_projection_baseline', value['assessment']['domain'])
        main_flow.validate_completion(receipt, receipt_path)
        tampered = deepcopy(value['assessment'])
        tampered['conclusion']['narrative'] = 'Unreviewed replacement.'
        with self.assertRaisesRegex(Invalid, 'changed reviewed content'):
            domain.validate_normalization(self.draft, tampered)

    def test_model_cycle_review_preserves_expired_event_as_exact_history(self):
        from datetime import datetime, timedelta
        import test_production_strategy as production
        from v4 import domain
        current = self.commit()
        prior = storage.domain_baseline(self.store, current)['canonical_domain']
        submitted = domain.synthetic(prior)
        submitted['runtime']['trigger_type'] = 'SCHEDULED_STRATEGY_BOUNDARY'
        original_submission = deepcopy(submitted)
        original_event = deepcopy(prior['event'])
        due = datetime.fromisoformat(original_event['cycle']['events'][-1]['due_at'].replace('Z', '+00:00'))
        clock = due + timedelta(hours=1)
        review_at = (clock + timedelta(hours=1)).isoformat()
        with production.frozen_clock(clock.isoformat()):
            accepted = domain.validate(submitted, prior, self.project,
                                       cycle_recheck_at=review_at, model_market=True)
            self.assertEqual(accepted['event'], original_event)
            self.assertFalse(accepted['event']['final_event_reached'])
            self.assertIsNone(accepted['event']['trigger']['completed_previous_event'])
            # The model-market adapter requires its existing cycle-review
            # context. Ordinary callers still have to consume the due event.
            for options in ({'model_market': True}, {'cycle_recheck_at': review_at}):
                with self.subTest(options=options), self.assertRaisesRegex(Invalid, 'final trigger'):
                    domain.validate(submitted, prior, self.project, **options)
        self.assertEqual(submitted, original_submission)
        self.assertEqual(storage.current(self.store), current)

    def test_expired_history_adapter_cannot_change_events_or_omit_valid_review_time(self):
        from datetime import datetime, timedelta
        import test_production_strategy as production
        from v4 import domain
        prior = storage.domain_baseline(self.store, self.commit())['canonical_domain']
        due = datetime.fromisoformat(prior['event']['cycle']['events'][-1]['due_at'].replace('Z', '+00:00'))
        with production.frozen_clock((due + timedelta(hours=1)).isoformat()):
            submitted = domain.synthetic(prior)
            submitted['runtime']['trigger_type'] = 'SCHEDULED_STRATEGY_BOUNDARY'
            with self.assertRaisesRegex(Invalid, 'approved cycle review deadline'):
                domain.validate(submitted, prior, self.project,
                                cycle_recheck_at='invalid', model_market=True)
            for field in ('due_at', 'opened_at'):
                changed = deepcopy(submitted)
                if field == 'due_at':
                    changed['event']['cycle']['events'][-1][field] = (due + timedelta(minutes=1)).isoformat()
                else:
                    changed['event']['cycle'][field] = (due - timedelta(hours=1)).isoformat()
                with self.subTest(field=field), self.assertRaises(Invalid):
                    domain.validate(changed, prior, self.project,
                                    cycle_recheck_at=(due + timedelta(hours=2)).isoformat(), model_market=True)

    def test_old_gpt6_1_cannot_publish_a_new_main_assessment(self):
        before = storage.current(self.store)
        self.draft['provenance']['model'] = 'gpt-5.6-sol'
        with self.assertRaisesRegex(Invalid, 'gpt-6.1-sol/high'):
            self.commit()
        self.assertEqual(storage.current(self.store), before)
        self.assertFalse((self.store / 'main-current.json').exists())

    def test_previous_gpt6_provenance_is_readable_but_not_a_new_writer(self):
        self.draft['provenance']['model']='gpt-6-sol'
        main_flow.validate_contract(self.draft,historical=True)
        with self.assertRaisesRegex(Invalid,'gpt-6.1-sol/high'):
            self.commit()

    def test_gpt6_1_baseline_remains_readable_after_current_sol_publication(self):
        # Build a genuinely old-shaped, hashed fixture under the former write rule.
        self.draft['provenance']['model'] = 'gpt-5.6-sol'
        validate = main_flow.validate_contract
        with mock.patch('v4.main_flow.validate_contract',
                        side_effect=lambda draft, **kw: validate(draft, historical=True)):
            old = self.commit()
        receipt = self.store / read(self.store / 'main-current.json')['completion']
        version = self.store / 'versions' / old['decision_id'] / 'decision.json'
        original = {p: p.read_bytes() for p in (receipt, version)}
        main_flow.validate_completion(read(receipt), receipt)
        self.draft['provenance']['model'] = 'gpt-6.1-sol'
        self.bind()  # Reads and verifies the actual old-model completion baseline.
        self.draft['conclusion']['narrative'] = '合成新模型复核后的说明，原有本金安排保持。'
        new = self.commit()
        self.assertEqual(new['assessment']['provenance']['model'], 'gpt-6.1-sol')
        self.assertEqual(new['parent_id'], old['decision_id'])
        for path, body in original.items():
            self.assertEqual(path.read_bytes(), body)
        main_flow.validate_completion(read(receipt), receipt)

    def test_explanation_supplement_publishes_without_report(self):
        initial=self.commit();self.report_baseline(initial);self.bind()
        self.draft['conclusion']['narrative']='补充支持范围及反证，已报告业务含义不变。'
        value=self.commit()
        self.assertNotEqual(value['decision_id'],initial['decision_id'])
        self.assertFalse(value['report_required'])
        self.assertFalse((self.store/'deep-current.json').exists())

    def test_important_explanation_reports_without_amount_change(self):
        initial=self.commit();self.report_baseline(initial);self.bind()
        self.draft['conclusion']['business_meaning']['value']='合成：承接证据减弱，未来路径下行风险上升。'
        value=self.commit()
        self.assertEqual(value['assessment']['recommendations'],initial['assessment']['recommendations'])
        self.assertEqual(value['comparison']['changed'],['conclusion'])
        self.assertTrue(value['report_required'])

    def test_report_compares_last_delivered_not_previous_current(self):
        initial=self.commit();self.report_baseline(initial);self.bind()
        self.draft['conclusion']['business_meaning']['value']='新风险已完成判断但尚未交付。'
        second=self.commit();self.bind()
        self.draft['conclusion']['narrative']='再补充解释而保留新风险判断。'
        third=self.commit()
        self.assertTrue(third['report_required'])
        self.assertNotEqual(third['decision_id'],second['decision_id'])
        self.assertEqual(read(self.store/'reported-current.json')['decision_id'],initial['decision_id'])

    def test_raw_only_revision_does_not_publish_main_or_deep(self):
        initial=self.commit();self.report_baseline(initial);self.bind()
        self.draft['provenance']['run_ref']='another synthetic run'
        result=self.commit()
        self.assertEqual(result['status'],'UNCHANGED')
        self.assertEqual(storage.current(self.store)['decision_id'],initial['decision_id'])
        self.assertFalse((self.store/'deep-current.json').exists())

    def test_false_reuse_and_forged_astra_owner_rejected(self):
        initial=self.commit();self.bind()
        p=self.draft['section_provenance']['outlook']
        p.update(mode='REUSED',decision_id=initial['decision_id'])
        self.draft['outlook']['narrative']='changed while claiming reused'
        with self.assertRaisesRegex(Invalid,'reused section changed'):self.commit()
        self.draft['provenance']['model']='gpt-6-astra'
        with self.assertRaisesRegex(Invalid,'Sol/high'):self.commit()

    def test_fact_race_or_orphan_does_not_create_successful_main(self):
        with mock.patch('v4.storage.facts',return_value={'changed':'fact'}):
            with self.assertRaises(Invalid):self.commit()
        self.assertFalse((self.store/'main-current.json').exists())
        def crash(stage):
            if stage=='before_pointer':raise RuntimeError('synthetic interruption')
        with self.assertRaises(RuntimeError):
            storage.commit(self.store,self.project,self.draft,self.evidence_dir,seal(self.draft),self.prior['decision_id'],crash)
        self.assertEqual(storage.current(self.store)['decision_id'],self.prior['decision_id'])
        self.assertFalse((self.store/'main-current.json').exists())

    def test_incremental_assembly_preserves_original_section_provenance(self):
        initial=self.commit()
        components={'updates':{'conclusion':{'business_meaning':deepcopy(self.draft['conclusion']['business_meaning']),'narrative':'补充有效解释。'}},
                    'section_reviews':dict.fromkeys(main_flow.SECTIONS,'Synthetic prior section remains applicable to this raw evidence.'),
                    'evidence_bindings':deepcopy(self.draft['evidence_bindings']),
                    'domain_market_cutoff':self.draft['temporal_scope']['domain_market_cutoff'],'stop_reason':self.draft['stop_reason']}
        identity=dict(self.draft['provenance'],run_ref='SYNTHETIC_INCREMENTAL_RUN')
        checks={k:self.review[k] for k in ('checks','unresolved_issues','material_new_outlook')}
        draft,review=main_flow.assemble(self.store,self.project,components,self.evidence_dir,self.raw,identity,checks)
        self.assertEqual(draft['section_provenance']['outlook']['mode'],'REUSED')
        self.assertEqual(draft['section_provenance']['outlook']['assessed_at'],initial['assessment']['temporal_scope']['knowledge_cutoff'])
        result=storage.commit(self.store,self.project,draft,self.evidence_dir,review,initial['decision_id'])
        self.assertEqual(result['assessment']['conclusion']['narrative'],'补充有效解释。')
        self.assertFalse((self.store/'deep-current.json').exists())

    def test_frozen_main_review_after_newer_hours_keeps_sensing_and_rejects_races(self):
        import test_market_observation as fixture
        initial = self.commit()
        db = self.project / '.strategy/market_observation.sqlite3'
        def record_newer(stamp):
            raw = fixture.evidence(stamp)
            ctx = sensing.context(self.store, db, self.project, raw)
            review = {'schema': 'BTC_SENSING_REVIEW_V4', 'revision': 3, 'context_id': ctx['context_id'],
                      'market_review': fixture.review(raw, {'context_id': ctx['context_id'], 'baselines': {}}, required=False),
                      'recommendation_checks': [{'side': side, 'findings': ['NO_MATERIAL_CHANGE'],
                          'refs': ['windows.4h.binance_spot_1h'], 'explanation': 'Synthetic later-hour applicability review.'}
                          for side in ('upper_short', 'lower_spot')],
                      'extra_findings': [], 'main_evaluation': {'required': False, 'reasons': []}, 'research_findings': []}
            return sensing.record(self.store, db, self.project, raw, review)
        record_newer(1898604000)
        with self.assertRaisesRegex(Invalid, 'late older-hour observation'):
            sensing.context(self.store, db, self.project, self.raw)
        ctx = sensing.context(self.store, db, self.project, self.raw, frozen_review=True)
        self.assertIsNone(ctx['previous_hour'])
        self.assertEqual(ctx['since_deep'], [])
        self.assertEqual(ctx['post_evidence_observation']['observed_hour'], '2030-03-01T13:00:00Z')
        self.assertEqual(ctx['accumulation']['end'], self.raw['cutoff'])
        components = {'updates': {'conclusion': {'business_meaning': deepcopy(self.draft['conclusion']['business_meaning']),
                                                 'narrative': 'Synthetic completed study with separately reviewed later observations.'}},
                      'section_reviews': dict.fromkeys(main_flow.SECTIONS, 'Synthetic frozen-study applicability reviewed.'),
                      'evidence_bindings': deepcopy(self.draft['evidence_bindings']),
                      'domain_market_cutoff': self.draft['temporal_scope']['domain_market_cutoff'], 'stop_reason': self.draft['stop_reason']}
        checks = {k: self.review[k] for k in ('checks', 'unresolved_issues', 'material_new_outlook')}
        def assemble():
            return main_flow.assemble(self.store, self.project, components, self.evidence_dir, self.raw,
                                      self.draft['provenance'], checks)
        draft, review = assemble()
        record_newer(1898607600)
        with self.assertRaisesRegex(Invalid, 'new observation'):
            storage.commit(self.store, self.project, draft, self.evidence_dir, review, initial['decision_id'])
        pointer = (self.store / 'sensing-current.json').read_bytes()
        draft, review = assemble()
        result = storage.commit(self.store, self.project, draft, self.evidence_dir, review, initial['decision_id'])
        self.assertEqual(result['assessment']['temporal_scope']['market_cutoff'], self.raw['cutoff'])
        self.assertEqual((self.store / 'sensing-current.json').read_bytes(), pointer)

    def test_compact_root_and_archive_do_not_publish_strategy(self):
        from v4 import evidence
        raw=self.base/'raw-current.json';create(raw,self.raw)
        directory=self.base/'compact-evidence'
        result=evidence.compact_root(self.evidence_dir,raw,[],directory,self.draft['temporal_scope']['knowledge_cutoff'])
        self.assertEqual(len(result['sources']),1)
        self.assertIsNone(result['parent_id'])
        archived=storage.archive_evidence(self.store,directory)
        self.assertEqual(archived['status'],'EVIDENCE_ARCHIVED_ONLY')
        self.assertEqual(storage.current(self.store)['decision_id'],self.prior['decision_id'])
        self.assertFalse((self.store/'main-current.json').exists())

    def test_new_main_fact_baseline_does_not_depend_on_legacy_deep_facts(self):
        import test_market_observation as fixture
        self.commit();ctx=self.bind()
        candidate=fixture.review(self.raw,{'context_id':ctx['context_id'],'baselines':{}},required=False)
        review={'schema':'BTC_SENSING_REVIEW_V4','revision':3,'context_id':ctx['context_id'],'market_review':candidate,
                'recommendation_checks':[{'side':side,'findings':['NO_MATERIAL_CHANGE'],'refs':['windows.4h.binance_spot_1h'],'explanation':'Same confirmed facts and current meaning.'} for side in ('upper_short','lower_spot')],
                'extra_findings':[],'main_evaluation':{'required':False,'reasons':[]},'research_findings':[]}
        result=sensing.record(self.store,self.project/'.strategy/market_observation.sqlite3',self.project,self.raw,review)
        self.assertNotIn('FACTS_CHANGED',result['triggers'])
        self.assertFalse(result['research_eligible'])

    def test_post_pointer_interrupt_keeps_readable_main(self):
        def crash(stage):
            if stage=='after_pointer':raise RuntimeError('synthetic interruption')
        with self.assertRaises(RuntimeError):
            storage.commit(self.store,self.project,self.draft,self.evidence_dir,seal(self.draft),self.prior['decision_id'],crash)
        self.assertEqual(storage.current(self.store)['assessment']['revision'],3)
        self.bind()  # No dependency on a fabricated new deep completion.

    def historical_final_origin(self):
        """Publish one real synthetic E transition before exercising immutable reuse."""
        import test_production_strategy as production
        import test_event_contract as events
        from v4 import domain, evidence
        clock = production.frozen_clock('2030-03-01T15:07:00Z')
        clock.__enter__(); self.addCleanup(clock.__exit__, None, None, None)
        timer = mock.patch('v4.evidence.now', return_value='2030-03-01T15:10:00Z')
        timer.start(); self.addCleanup(timer.stop)
        with production.rendered_baseline(domain.synthetic(self.prior['canonical_domain'])) as (baseline, _, __):
            value = production.daily_bundle(self.draft['domain'], baseline, events.AFTER_FINAL, final=True)
        value['runtime']['trigger_type'] = 'MANUAL_REEVALUATION'
        self.draft['domain'] = value
        spec = {k:deepcopy(v) for k,v in self.evidence.items() if k not in ('schema','evidence_id')}
        spec.update(parent_id=self.evidence['evidence_id'], cutoff='2030-03-01T15:06:00Z')
        files = {s['id']:str(self.evidence_dir/'raw'/(s['raw_hash'][7:]+'.json')) for s in spec['sources']}
        directory = self.base/'final-event-evidence'
        self.evidence = evidence.freeze(spec, files, directory, self.evidence_dir)
        self.evidence_dir = directory
        self.draft['evidence_id'] = self.evidence['evidence_id']
        self.draft['temporal_scope']['knowledge_cutoff'] = spec['cutoff']
        for p in self.draft['section_provenance'].values():
            p.update(evidence_id=self.evidence['evidence_id'], assessed_at=spec['cutoff'])
        self.bind()
        value = self.commit()
        self.assertTrue(value['assessment']['domain']['event']['final_event_reached'])
        self.assertEqual(value['assessment']['domain']['event']['trigger']['kind'], 'PREVIOUS_FINAL_COMPLETED')
        return value

    def reused_assembly(self, narrative=None):
        current = storage.current(self.store)
        previous = current['assessment']
        updates = {} if narrative is None else {'conclusion':dict(deepcopy(previous['conclusion']), narrative=narrative)}
        components = {'updates':updates, 'section_reviews':dict.fromkeys(main_flow.SECTIONS,'Synthetic immutable section reviewed against its current applicability.'),
            'evidence_bindings':deepcopy(previous['evidence_bindings']), 'domain_market_cutoff':previous['temporal_scope']['domain_market_cutoff'], 'stop_reason':previous['stop_reason']}
        identity = dict(previous['provenance'], run_ref='SYNTHETIC_REUSE')
        checks = {k:self.review[k] for k in ('checks','unresolved_issues','material_new_outlook')}
        return main_flow.assemble(self.store,self.project,components,self.evidence_dir,self.raw,identity,checks)

    def test_historical_final_transition_replays_original_parent_once(self):
        initial = self.historical_final_origin()
        draft, review = self.reused_assembly('New main explanation retaining the prior event transition as history.')
        baseline = main_flow.domain_validation_baseline(self.store, initial, draft)
        self.assertEqual(baseline['decision_id'], self.prior['decision_id'])
        self.assertEqual(baseline['_verified_domain_replay_clock'].isoformat(), initial['created_at'])
        self.assertEqual(initial['created_at'], '2030-03-01T15:07:00+00:00')
        result = storage.commit(self.store,self.project,draft,self.evidence_dir,review,initial['decision_id'])
        self.assertTrue(result['assessment']['domain']['event']['final_event_reached'])
        self.assertEqual(result['assessment']['domain'], initial['assessment']['domain'])
        self.assertFalse((self.store/'deep-current.json').exists())

    def test_current_final_deadline_cannot_be_hidden_by_historical_replay_clock(self):
        initial = self.historical_final_origin()
        draft, _ = self.reused_assembly()
        with mock.patch('v4.common.now', return_value='2030-03-01T17:00:00Z'):
            with self.assertRaisesRegex(Invalid,'current final event requires'):
                main_flow.domain_validation_baseline(self.store, initial, draft)
        draft['temporal_scope']['knowledge_cutoff'] = '2030-03-01T17:00:00Z'
        with self.assertRaisesRegex(Invalid,'current final event requires'):
            main_flow.domain_validation_baseline(self.store, initial, draft)

    def test_repeated_reuse_does_not_republish_historical_final_transition(self):
        initial = self.historical_final_origin()
        draft, review = self.reused_assembly('An initially useful main explanation supplement.')
        second = storage.commit(self.store,self.project,draft,self.evidence_dir,review,initial['decision_id'])
        generations = len(list((self.store/'versions').glob('*/decision.json')))
        for _ in range(2):
            draft, review = self.reused_assembly()
            result = storage.commit(self.store,self.project,draft,self.evidence_dir,review,second['decision_id'])
            self.assertEqual(result['status'],'UNCHANGED')
            self.assertEqual(result['decision_id'],second['decision_id'])
        self.assertEqual(len(list((self.store/'versions').glob('*/decision.json'))),generations)

if __name__=='__main__':unittest.main()
