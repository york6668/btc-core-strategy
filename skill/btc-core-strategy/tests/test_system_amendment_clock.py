"""Generated amendment-clock regressions; no actual author/runtime evidence."""
from copy import deepcopy
from datetime import timedelta
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock


import test_system_research_transport as transport_fixture
from test_forward_lifecycle import T2
from v4 import forward_lifecycle as life, research
from v4.common import Invalid, digest, instant, read


class AmendmentRegistrationClockTests(unittest.TestCase):
    def setUp(self):
        self.fixture = transport_fixture.NativeResearchTransportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        with mock.patch('v4.forward_lifecycle.now', return_value=T2):
            validated = self.fixture.public_result()
            self.initial = research._save(self.root, 'independent', 'independent_id', {
                'schema': 'BTC_RESEARCH_INDEPENDENT_V4',
                'request_id': self.fixture.req['request_id'],
                'agent_id': 'GENERATED_CLOCK_TEST_NOT_ACTUAL_AUTHOR',
                'turn_id': 'SYNTHETIC_INITIAL', 'input_id': self.fixture.req['independent_input_id'],
                'rollout_path': str(self.root / 'not-an-actual-runtime.jsonl'),
                'started_at': instant(T2).timestamp() - 1,
                'completed_at': instant(T2).timestamp(), **validated,
            })
        self.old_records = {path: path.read_bytes()
                            for path in (self.root / 'market-lifecycle/claims').glob('*.json')}
        self.later = instant(T2) + timedelta(days=1)
        self.freeze_at = (self.later + timedelta(seconds=11)).isoformat()

    def amend(self, public):
        for market in public['outlook']['market_assessment']['markets']:
            for horizon in market['horizons']:
                horizon['forward']['prior_check'] = {'outcome':'EVIDENCE_LIMITED','explanation':'Generated continuing review with no new realization evidence.','refs':[]}
        reference = self.fixture.output(public)
        proof = {'started_at': self.later.timestamp() - 1,
                 'completed_at': self.later.timestamp() + 10,
                 'last_agent_message': json.dumps(reference)}
        with mock.patch('v4.research._completed_turn', return_value=proof), \
                mock.patch('v4.forward_lifecycle.now', return_value=self.freeze_at):
            # Only native author execution is simulated. The original output
            # resolver, full public validators, immutable saving and claim
            # registration execute against generated source-bound fixtures.
            return research.amend_market(self.root, self.initial['independent_id'],
                self.initial['rollout_path'], turn_id='SYNTHETIC_LATER_AMENDMENT')

    def assert_old_registrations_unchanged(self):
        for path, body in self.old_records.items():
            self.assertEqual(path.read_bytes(), body)

    def test_new_claim_first_clock_is_later_amendment_freeze(self):
        public = deepcopy(self.fixture.public)
        for market in public['outlook']['market_assessment']['markets']:
            for horizon in market['horizons']:
                forward = horizon['forward']
                claim = forward['lifecycle']['claim']
                claim['id'] += '_GENERATED_LATER_CORRECTION'
                claim['formed_at'] = self.later.isoformat()
                forward['judgement_window']['start'] = claim['formed_at']
        amended = self.amend(public)
        self.assertEqual(amended['lifecycle_frozen_at'], self.freeze_at)
        self.assertNotEqual(amended['lifecycle_frozen_at'], self.initial['lifecycle_frozen_at'])
        self.assertEqual(amended['parent_independent_id'], self.initial['independent_id'])
        self.assertEqual(amended['independence_status'], 'CONTINUED_PUBLIC_CORRECTION')
        for market in public['outlook']['market_assessment']['markets']:
            for horizon in market['horizons']:
                claim = horizon['forward']['lifecycle']['claim']
                record = life.registration(self.root, digest(claim))
                self.assertEqual(record['registered_at'], self.freeze_at)
                self.assertGreater(instant(record['registered_at']), instant(claim['formed_at']))
        self.assert_old_registrations_unchanged()

    def test_unchanged_claim_keeps_its_original_first_clock(self):
        public = deepcopy(self.fixture.public)
        public['market_report'] += '\nGenerated public wording correction, without changing claims.'
        amended = self.amend(public)
        self.assertEqual(amended['lifecycle_frozen_at'], self.freeze_at)
        self.assertEqual(len(list((self.root / 'market-lifecycle/claims').glob('*.json'))),
                         len(self.old_records))
        for path in self.old_records:
            self.assertEqual(read(path)['registered_at'], json.loads(self.old_records[path])['registered_at'])
        self.assert_old_registrations_unchanged()


class PortfolioPublicManifestTests(unittest.TestCase):
    def setUp(self):
        self.base = AmendmentRegistrationClockTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.root = self.base.root
        outside = tempfile.TemporaryDirectory(prefix='btc-author-artifact-outside-')
        self.addCleanup(outside.cleanup)
        self.outside = Path(outside.name)
        self.fixture = self.base.fixture
        self.public = deepcopy(self.fixture.public)
        self.public['market_report'] += '\nLater disclosed public wording, not candidate approval.'
        for market in self.public['outlook']['market_assessment']['markets']:
            for horizon in market['horizons']:
                horizon['forward']['prior_check'] = {'outcome':'EVIDENCE_LIMITED','explanation':'Generated continuing review; no new realized claim.','refs':[]}
        with mock.patch('v4.forward_lifecycle.now', return_value=T2):
            initial = {k:v for k,v in self.base.initial.items() if k != 'independent_id'}
            initial['agent_id'] = '/root/generated_original_author'
            self.phase = research._save(self.root,'independent','independent_id',initial)
        self.author = self.phase['agent_id']
        self.later = self.base.later
        self.folder = self.root/'generated-author-artifacts'
        self.folder.mkdir()
        self.trace = self.root/'generated-creator-trace.jsonl'
        self.public_path = self.folder/'public-proposed-market-result.v4.json'
        self.review_path = self.folder/'public-supplement-review.json'
        self.manifest_path = self.folder/'deliverable-manifest.json'
        self.public_path.write_text(json.dumps(self.public))
        self.review = {'original':{'request_id':self.phase['request_id'],'independent_id':self.phase['independent_id']},
                       'formed_at':(self.later+timedelta(seconds=1)).isoformat(),
                       'later_scope':{'knowledge_cutoff':self.later.isoformat(),
                                      'source_manifest_frozen_at':self.later.isoformat(),
                                      'evidence_ids':[self.fixture.package['evidence_id']]}}
        self.manifest = {'schema':'BTC_AUTHOR_DELIVERABLE_MANIFEST_V1',
                         'status':'AUTHOR_WORK_COMPLETED_READY_FOR_SOL_MAPPING',
                         'completed_at':(self.later+timedelta(seconds=5)).isoformat(),
                         'active_public_proposal':self.public_path.name}
        self.final = {'status':self.manifest['status'], 'formal_writes_or_delivery':False,
                      'public_correction':{'needed':True,'original_public_modified':False,'proposal_formally_registered':False}}
        self.seal()

    def reference(self,path):
        return {'path':str(path),'sha256':digest(path.read_bytes())}

    def seal(self):
        self.review_path.write_text(json.dumps(self.review))
        self.manifest['artifacts'] = [dict(self.reference(p),name=p.name,bytes=p.stat().st_size)
                                      for p in (self.public_path,self.review_path)]
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.final.update(manifest=self.reference(self.manifest_path),
                          artifacts={'public_proposal':self.reference(self.public_path)})
        self.write_trace()

    def write_trace(self,author=None):
        author = author or self.author
        events = [
            {'type':'response_item','timestamp':(self.later-timedelta(seconds=1)).isoformat(),
             'payload':{'type':'function_call','namespace':'collaboration','name':'followup_task','call_id':'generated-followup',
                        'arguments':json.dumps({'target':self.author,'message':'Generated complete work request.'})}},
            {'type':'response_item','timestamp':self.later.isoformat(),
             'payload':{'type':'function_call_output','call_id':'generated-followup','output':json.dumps({'task_name':self.author})}},
            {'type':'response_item','timestamp':(self.later+timedelta(seconds=10)).isoformat(),
             'payload':{'type':'agent_message','id':'generated-final','author':author,
                        'content':[{'type':'input_text','text':'Message Type: FINAL_ANSWER\nSender: '+author+'\nPayload:\n'+json.dumps(self.final)}]}}]
        self.trace.write_text('\n'.join(json.dumps(x) for x in events)+'\n')

    def amend(self,**kwargs):
        with mock.patch('v4.research._guard_completed') as guard, \
                mock.patch('v4.forward_lifecycle.now',return_value=self.base.freeze_at):
            saved = research.amend_market(self.root,self.phase['independent_id'],self.trace,
                        'generated-followup','generated-final',guard_attempt='generated-portfolio-complete',
                        source_phase='PORTFOLIO',**kwargs)
        guard.assert_called_once_with(self.root,self.fixture.req,'PORTFOLIO','generated-portfolio-complete',self.trace)
        return saved

    def test_real_manifest_consumer_preserves_original_owner_claims_and_phase(self):
        saved = self.amend()
        self.assertEqual(saved['market_result'],self.public)
        self.assertEqual(saved['independence_status'],'DISCLOSED_SAME_AUTHOR_PUBLIC_UPDATE_FROM_PORTFOLIO')
        self.assertEqual(saved['market_amendment']['source_phase'],'PORTFOLIO')
        self.assertEqual(saved['market_amendment']['guard_attempt'],'generated-portfolio-complete')
        self.assertEqual(saved['market_amendment']['guard_proof_hash'],digest(self.trace.read_bytes()))
        self.assertEqual(saved['parent_independent_id'],self.phase['independent_id'])
        self.assertEqual(saved['public_evidence_inputs']['knowledge_cutoff'],self.later.isoformat())
        self.assertNotIn('cycle_review',saved)
        self.assertEqual(saved['verification_scope'],'ORCHESTRATED_PORTFOLIO_PUBLIC_ARTIFACT_BINDING')
        self.assertFalse(saved['inner_tool_trace_verified'])
        self.assertFalse(saved['os_input_isolation_verified'])
        self.base.assert_old_registrations_unchanged()
        self.assertEqual(research._load(self.root,'independent',self.phase['independent_id']),self.phase)
        # Retention may remove mutable author paths. The exact checked copies remain.
        for path in (self.public_path,self.review_path,self.manifest_path): path.unlink()
        research._verify_market_amendment(saved,self.root)
        copied = self.root/saved['public_result_reference']['path']
        self.assertEqual(json.loads(copied.read_text()),self.public)

    def test_cli_preserves_explicit_source_phase_and_original_default(self):
        import io
        from contextlib import redirect_stdout
        import strategy_v4
        base=['--project',str(self.root.parent.parent),'--root',str(self.root),
              'research-market-amendment','--independent',self.phase['independent_id'],
              '--rollout',str(self.trace),'--dispatch','generated-followup','--returned','generated-final']
        for extra,expected in (([],'PUBLIC_CORRECTION'),(['--source-phase','PORTFOLIO'],'PORTFOLIO')):
            with mock.patch('runtime_schedule.bind'),mock.patch('v4.research.amend_market',return_value={}) as amend,redirect_stdout(io.StringIO()):
                self.assertEqual(strategy_v4.main(base+extra),0)
            self.assertEqual(amend.call_args.kwargs['source_phase'],expected)

    def test_guard_rejection_does_not_fallback_or_archive(self):
        before = set((self.root/'research/author_outputs').glob('*'))
        with mock.patch('v4.research._guard_completed',side_effect=Invalid('wrong original phase')) as guard, \
                self.assertRaisesRegex(Invalid,'wrong original phase'):
            research.amend_market(self.root,self.phase['independent_id'],self.trace,
                                  'generated-followup','generated-final',source_phase='PORTFOLIO')
        self.assertEqual(guard.call_count,1)
        self.assertEqual(set((self.root/'research/author_outputs').glob('*')),before)

    def test_actual_author_final_active_artifact_and_hash_are_required(self):
        self.write_trace(author='/root/different_author')
        with self.assertRaisesRegex(Invalid,'same agent'): self.amend()
        self.write_trace()
        self.public_path.write_text('{}')
        with self.assertRaisesRegex(Invalid,'bytes changed'): self.amend()
        self.public_path.write_text(json.dumps(self.public));self.seal()
        self.manifest['active_public_proposal']='not-the-active-file.json';self.seal()
        with self.assertRaisesRegex(Invalid,'active manifest entry'): self.amend()

    def test_no_approval_fallback_or_late_sol_merge(self):
        with self.assertRaisesRegex(Invalid,'original input bundles'): self.amend(additional=self.root/'later-sol-merge')
        self.final['status']='APPROVED';self.write_trace()
        with self.assertRaisesRegex(Invalid,'manifest FINAL'): self.amend()
        self.final['status']=self.manifest['status'];self.write_trace()
        self.review['later_scope']['knowledge_cutoff']=(self.later+timedelta(seconds=20)).isoformat()
        self.review['later_scope']['source_manifest_frozen_at']=self.review['later_scope']['knowledge_cutoff'];self.seal()
        with self.assertRaisesRegex(Invalid,'clocks exceed'): self.amend()

    def test_root_escape_symlink_and_wrong_original_owner_rejected(self):
        outside = self.outside/'outside-public.json';outside.write_text(json.dumps(self.public))
        self.manifest['artifacts'][0].update(self.reference(outside),bytes=outside.stat().st_size)
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.final.update(manifest=self.reference(self.manifest_path),artifacts={'public_proposal':self.reference(outside)})
        self.write_trace()
        with self.assertRaisesRegex(Invalid,'active manifest entry|escapes research root'): self.amend()
        self.seal();self.public_path.unlink();self.public_path.symlink_to(outside)
        with self.assertRaisesRegex(Invalid,'escapes research root|child symlinks'): self.amend()
        self.public_path.unlink();self.public_path.write_text(json.dumps(self.public))
        self.review['original']['independent_id']=digest('other');self.seal()
        with self.assertRaisesRegex(Invalid,'another request or original public owner'): self.amend()

    def test_malformed_final_and_nonobject_artifacts_are_explicit_invalid(self):
        original=deepcopy(self.final)
        for key in ('public_correction','manifest','artifacts','public_proposal'):
            for bad in (None,[]):
                self.final=deepcopy(original)
                if key=='public_proposal': self.final['artifacts'][key]=bad
                else: self.final[key]=bad
                self.write_trace()
                with self.subTest(field=key,value=bad),self.assertRaises(Invalid): self.amend()
        self.final=deepcopy(original)
        for bad in (None,[]):
            self.manifest_path.write_text(json.dumps(bad))
            self.final['manifest']=self.reference(self.manifest_path);self.write_trace()
            with self.subTest(manifest=bad),self.assertRaisesRegex(Invalid,'JSON object'): self.amend()
        for bad in (None,[]):
            self.review=bad;self.seal()
            with self.subTest(public_input=bad),self.assertRaisesRegex(Invalid,'JSON object'): self.amend()

    def test_external_file_symlink_is_not_a_logical_root_alias(self):
        alias=self.outside/'alias-public.json';alias.symlink_to(self.public_path)
        ref=self.reference(alias)
        with self.assertRaisesRegex(Invalid,'escapes research root'):
            research._author_artifact(self.root,ref)
        nested=self.folder/'alias-child.json';nested.symlink_to(self.public_path)
        with self.assertRaisesRegex(Invalid,'child symlinks'):
            research._author_artifact(self.root,self.reference(nested))

    def test_logical_and_physical_root_spellings_are_supported(self):
        logical=self.outside/'logical-root';logical.symlink_to(self.root,target_is_directory=True)
        expected=self.public_path.read_bytes()
        alias_file=logical/self.public_path.relative_to(self.root)
        for root,path in ((self.root,alias_file),(logical,self.public_path),(logical,alias_file)):
            with self.subTest(root_spelling=str(root),path_spelling=str(path)):
                self.assertEqual(research._author_artifact(root,self.reference(path)),expected)
        traversal=self.folder/'..'/self.folder.name/self.public_path.name
        with self.assertRaisesRegex(Invalid,'parent paths'):
            research._author_artifact(self.root,{'path':str(traversal),'sha256':digest(expected)})

    def test_readback_cannot_substitute_completion_proof(self):
        saved=self.amend()
        saved['market_amendment']['guard_proof_hash']=digest('a different creator proof')
        with self.assertRaisesRegex(Invalid,'completion proof binding changed'):
            research._verify_market_amendment(saved,self.root)

    def test_input_bundles_cannot_be_missing_late_or_rewrite_original_sources(self):
        from v4 import evidence, storage
        self.review['later_scope']['evidence_ids']=[digest('missing-bundle')];self.seal()
        with self.assertRaisesRegex(Invalid,'bundle missing'): self.amend()
        original=self.fixture.package
        for late in (True,False):
            spec={k:deepcopy(v) for k,v in original.items() if k not in ('schema','evidence_id')}
            spec['parent_id']=None
            spec['cutoff']=(self.later+timedelta(seconds=2)).isoformat() if late else (instant(T2)+timedelta(seconds=2)).isoformat()
            if not late: spec['sources'][0]['retrieved_at']=(instant(T2)+timedelta(seconds=1)).isoformat()
            files={x['id']:str(self.fixture.directory/'raw'/(x['raw_hash'][7:]+'.json')) for x in spec['sources']}
            directory=self.root/('generated-late' if late else 'generated-rewrite')
            package=evidence.freeze(spec,files,directory);storage.archive_evidence(self.root,directory)
            self.review['later_scope']['evidence_ids']=[package['evidence_id']];self.seal()
            with self.subTest(late=late),self.assertRaisesRegex(Invalid,'frozen after|rewrite an original'):
                self.amend()

    def test_archive_cannot_write_through_a_child_symlink(self):
        directory=self.root/'research/author_outputs'
        directory.rename(self.root/'original-generated-outputs')
        outside=self.outside/'outside-generated-outputs';outside.mkdir()
        directory.symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(Invalid,'archive escapes research root'): self.amend()
        self.assertEqual(list(outside.iterdir()),[])

    def test_full_public_validator_still_rejects_mutated_original_claim(self):
        claim=self.public['outlook']['market_assessment']['markets'][0]['horizons'][0]['forward']['lifecycle']['claim']
        claim['scale']['process'] += ' Changed after original public registration.'
        self.public_path.write_text(json.dumps(self.public));self.seal()
        with self.assertRaisesRegex(Invalid,'immutable|fields|must be'): self.amend()


if __name__ == '__main__':
    unittest.main()
