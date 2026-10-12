"""Generated native-transport contract tests, never actual author/read proof."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json,unittest
from unittest import mock


import test_lifecycle_entrypoints as fixtures
from test_forward_lifecycle import T2
from v4 import research,storage
from v4.common import Invalid,create,digest,instant,read,replace


class NativeResearchTransportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EntryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root
        self.directory,self.package=self.f.package()
        storage.archive_evidence(self.root,self.directory)
        self.public=self.f.result()
        packet={'sources':[],'historical_hypotheses':{}}
        create(self.root/'transport-test-input.json',packet)
        self.req=research._save(self.root,'requests','request_id',{
            'schema':'BTC_RESEARCH_REQUEST_V4','workflow_revision':9,
            'created_at':T2,'evidence_id':self.package['evidence_id'],
            'independent_input_path':'transport-test-input.json','independent_input_id':digest(packet)})
        self.expected=research.read_input(self.root,self.req['request_id'])
        self.agent='GENERATED_TRACE_NOT_A_REAL_AUTHOR';self.turn='SYNTHETIC_PUBLIC_TURN'
        self.reference=self.output(self.public)

    def output(self,value=None,body=None):
        body=body if body is not None else json.dumps(value or self.public,ensure_ascii=False).encode()
        identity=digest(body)
        path='research/author_outputs/'+identity[7:]+'.json'
        destination=self.root/path
        if destination.exists():self.assertEqual(destination.read_bytes(),body)
        else:create(destination,body)
        return {'schema':'BTC_RESEARCH_PUBLIC_RESULT_REF_V1','path':path,'content_hash':identity}

    def wrapped(self,proof=None):
        return [{'type':'text','text':json.dumps({'i':0,'status':'fulfilled',
            'value':{'exit_code':0,'output':json.dumps(proof or self.expected)}})}]

    def trace(self,output=None,answer=None,model='gpt-6-astra',start=None,private=False,complete=True):
        start=start if start is not None else instant(T2).timestamp()+1
        events=[{'type':'session_meta','payload':{'id':self.agent}}]
        if private:
            prior=deepcopy(self.expected);prior['phase']='portfolio'
            events.extend([{'type':'turn_context','payload':{'turn_id':'SYNTHETIC_PRIVATE','model':'gpt-6-astra','effort':'max'}},
                {'type':'response_item','payload':{'type':'custom_tool_call','call_id':'private-read',
                    'input':'python strategy_v4.py research-read --request '+self.req['request_id']+' --phase portfolio'}},
                {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'private-read','output':self.wrapped(prior)}}])
        events.extend([{'type':'turn_context','payload':{'turn_id':self.turn,'model':model,'effort':'max'}},
            {'type':'response_item','payload':{'type':'custom_tool_call','call_id':'public-read',
                'input':'const r=await tools.exec_command({cmd:"python strategy_v4.py research-read --request '+self.req['request_id']+' --phase independent"});text(r);'}},
            {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'public-read',
                'output':output if output is not None else self.wrapped()}}])
        if complete:events.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':self.turn,
            'started_at':start,'completed_at':start+1,
            'last_agent_message':answer if answer is not None else json.dumps(self.reference)}})
        path=self.root/'generated-runtime-trace.jsonl'
        path.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
        return path

    def independent(self,**options):
        path=self.trace(**options)
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):
            return research.independent(self.root,self.req['request_id'],self.agent,path,self.turn)

    def public_result(self,reference=None):
        with mock.patch('v4.forward_lifecycle.now',return_value=T2):
            return research._public_result(self.root,self.req,json.dumps(reference or self.reference))

    def test_nested_exec_stdout_retains_whole_exact_input(self):
        proof=deepcopy(self.expected);proof['continuity']={'live_status':'SYNTHETIC_EXTERNAL_BRIDGE_STATUS'}
        self.assertEqual(list(research._read_proofs(self.wrapped(proof))),[self.expected])
        multiple=json.dumps(json.dumps({'stdout':json.dumps(self.expected)}))
        self.assertEqual(list(research._read_proofs(multiple)),[self.expected])
        header='Chunk ID: GENERATED\nExit code: 0\nOutput:\n'+json.dumps(self.expected)
        self.assertEqual(list(research._read_proofs(header)),[self.expected])

    def test_truncated_or_metadata_only_output_cannot_become_exact_input(self):
        for output in (self.wrapped({'schema':self.expected['schema'],'request_id':self.req['request_id'],
                                    'phase':'independent','input_id':self.expected['input_id']}),
                       self.wrapped({'stdout_bytes':300000,'input_id':self.expected['input_id']}),
                       [{'type':'text','text':json.dumps(self.expected)[:-30]}],
                       '{\"stdout\":'+json.dumps(self.expected),
                       {'truncated':True,'output':json.dumps(self.expected)},
                       'Warning: truncated output\n'+json.dumps(self.expected)):
            with self.subTest(output_kind=type(output).__name__),self.assertRaisesRegex(Invalid,'did not read this frozen request input'):
                self.independent(output=output)

    def test_wrong_payload_or_identity_is_rejected_after_unwrapping(self):
        for change in (lambda p:p['payload'].update(extra='Changed frozen input.'),
                       lambda p:p.update(input_id=digest('different')),
                       lambda p:p.update(phase='candidate')):
            proof=deepcopy(self.expected);change(proof)
            with self.subTest(change=change),self.assertRaisesRegex(Invalid,'did not read this frozen request input'):
                self.independent(output=self.wrapped(proof))

    def test_transport_does_not_relax_actual_model_completion_clock_or_private_gate(self):
        for options,message in (({'model':'gpt-6.1-sol'},'actually run Astra/max'),
                                ({'complete':False},'actual completed agent turn'),
                                ({'start':instant(T2).timestamp()-1},'predates this request'),
                                ({'private':True},'already read personal or candidate')):
            with self.subTest(options=options),self.assertRaisesRegex(Invalid,message):self.independent(**options)

    def test_transport_depth_is_bounded(self):
        output=json.dumps(self.expected)
        for _ in range(20):output=json.dumps({'output':output})
        with self.assertRaisesRegex(Invalid,'transport limit exceeded'):list(research._read_proofs(output))

    def test_actual_current9_consumer_archives_complete_result_and_final_reference(self):
        saved=self.independent()
        self.assertEqual(saved['summary'],json.dumps(self.reference))
        self.assertEqual(saved['market_result'],self.public)
        self.assertEqual(saved['market_result_hash'],digest(self.public))
        self.assertEqual(saved['public_result_reference'],self.reference)
        self.assertEqual(saved['verification_scope'],'NATIVE_RUNTIME_PHASE_INPUT_READ')
        (self.root/self.reference['path']).unlink()
        archived=research._load(self.root,'independent',saved['independent_id'])
        self.assertEqual(archived['market_result'],self.public)
        self.assertEqual(archived['public_result_reference'],self.reference)

    def test_reference_cannot_change_bytes_hash_filename_or_owner_directory(self):
        path=self.root/self.reference['path']
        original=path.read_bytes();path.write_bytes(original+b' ')
        with self.assertRaisesRegex(Invalid,'bytes changed'):self.public_result()
        path.write_bytes(original)
        for name in ('../escape.json',str(path),'research/author_outputs/not-the-hash.json'):
            ref=dict(self.reference,path=name)
            with self.subTest(name=name),self.assertRaisesRegex(Invalid,'root-relative hash filename'):self.public_result(ref)
        ref=dict(self.reference,content_hash='sha256:invalid')
        with self.assertRaisesRegex(Invalid,'actual byte hash'):self.public_result(ref)

    def test_reference_symlinks_are_rejected(self):
        path=self.root/self.reference['path']
        copied=self.root/'generated-external-file.json';create(copied,path.read_bytes())
        path.unlink();path.symlink_to(copied)
        with self.assertRaisesRegex(Invalid,'file escapes its owner'):self.public_result()
        path.unlink();create(path,copied.read_bytes())
        directory=path.parent;elsewhere=self.root/'generated-other-output-directory';directory.rename(elsewhere)
        directory.symlink_to(elsewhere,target_is_directory=True)
        with self.assertRaisesRegex(Invalid,'directory escapes its owner'):self.public_result()

    def test_reference_shape_and_self_declared_author_do_not_bypass_full_contract(self):
        ref=dict(self.reference,author='gpt-6-astra/max')
        with self.assertRaisesRegex(Invalid,'fields'):self.public_result(ref)
        for value in ({'market_report':'Missing all original structured public fields.'},
                      dict(self.public,author='gpt-6-astra/max')):
            with self.subTest(shape=list(value)),self.assertRaisesRegex(Invalid,'fields'):self.public_result(self.output(value))
        old=dict(self.req,workflow_revision=8)
        with self.assertRaisesRegex(Invalid,'require revision 9'):
            research._public_result(self.root,old,json.dumps(self.reference))

    def test_referenced_result_still_runs_factor_raw_and_lifecycle_validation(self):
        bad=deepcopy(self.public);bad['factor_assessment']={'macro_transmission':'Not the original factor contract.'}
        with self.assertRaisesRegex(Invalid,'fields'):self.public_result(self.output(bad))
        bad=deepcopy(self.public)
        claim=bad['outlook']['market_assessment']['markets'][0]['horizons'][0]['forward']['lifecycle']['claim']
        claim['basis_refs'][0]['value_hash']=digest('Forged original source value.')
        with self.assertRaisesRegex(Invalid,'raw value reference mismatch'):self.public_result(self.output(bad))
        bad=deepcopy(self.public)
        claim=bad['outlook']['market_assessment']['markets'][1]['horizons'][0]['forward']['lifecycle']['claim']
        claim['formed_at']='2023-12-01T00:00:00Z'
        with self.assertRaisesRegex(Invalid,'formation|predates|cutoff'):self.public_result(self.output(bad))

    def test_reference_utf8_and_size_limits_are_checked_before_parsing(self):
        ref=self.output(body=b'\xff')
        with self.assertRaisesRegex(Invalid,'UTF-8'):self.public_result(ref)
        ref=self.output(body=b' '*(16*1024*1024+1))
        with self.assertRaisesRegex(Invalid,'too large'):self.public_result(ref)

    def test_amendment_readback_binds_archived_final_without_reopening_mutable_file(self):
        saved=self.independent()
        path=self.trace()
        phase=deepcopy(saved);phase['market_amendment']={'rollout_path':str(path),'turn_id':self.turn,
            'analysis_hash':digest(json.dumps(self.reference))}
        (self.root/self.reference['path']).unlink()
        research._verify_market_amendment(phase)
        phase['public_result_reference']=dict(self.reference,content_hash=digest('different'))
        with self.assertRaisesRegex(Invalid,'result reference changed'):research._verify_market_amendment(phase)


if __name__=='__main__':unittest.main()
