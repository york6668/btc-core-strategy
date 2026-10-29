"""Identity-bound node admission client tests; no model or production node is called."""
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
from unittest.mock import Mock

import model_daily


class DailyClientTests(unittest.TestCase):
    def test_native_launch_claims_first_and_binds_actual_event_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt, binary, output = (root / n for n in ('prompt.txt','codex','reply.txt'))
            prompt.write_text('frozen synthetic prompt')
            binary.write_text('synthetic binary')
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                kwargs['stdout'].write(b'{"type":"turn.completed"}\n')
                output.write_text('synthetic completed response')
                return Mock(returncode=0)
            admission = {'action':'CLAIMED_NEW','claim':{'claim_id':'btc-deep-original'}}
            with patch.object(model_daily,'claim',return_value=admission) as reserved, \
                 patch.object(model_daily,'finish',return_value={'action':'SETTLED'}) as settled:
                model_daily.launch_native('issue','btc/research','PUBLIC','sha256:'+'a'*64,
                                          prompt,binary,output,runner=runner)
            self.assertEqual(1,len(calls))
            reserved.assert_called_once()
            self.assertEqual('COMPLETE',settled.call_args.args[1])
            self.assertEqual(model_daily.sha256(output.with_suffix('.txt.events.jsonl')),
                             settled.call_args.kwargs['result_ref'])

    def test_claim_only_launches_on_new_shared_slot(self):
        claim = {'action': 'CLAIMED_NEW', 'claim': {'claim_id': 'btc-deep-original'}}
        with patch.object(model_daily, 'request', return_value=claim) as sent:
            self.assertEqual(claim, model_daily.claim('issue', 'btc/research', 'PUBLIC', 'sha256:' + 'a'*64,
                                                     claim_id='btc-deep-original'))
        self.assertEqual('model.deep.claim', sent.call_args.args[0])
        self.assertEqual('deep-claim-btc-deep-original', sent.call_args.args[2])
        with patch.object(model_daily, 'request', return_value={'action': 'REUSE'}):
            with self.assertRaisesRegex(model_daily.Blocked, 'do not launch'):
                model_daily.claim('another-task', 'btc/research', 'PUBLIC', 'sha256:' + 'b'*64,
                                  claim_id='btc-deep-another')

    def test_normal_client_never_relaunches_a_committed_operation_receipt(self):
        from btc_node import api
        from test_operation_recovery_offline import Journal
        for response_lost in (False, True):
            with self.subTest(response_lost=response_lost), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prompt=root/'prompt.txt'; binary=root/'codex'
                prompt.write_text('synthetic original input'); binary.write_text('synthetic native binary')
                journal=Journal(); calls=[]; returned=[]
                def adapter(method, params=None, operation_id=None):
                    result=api.dispatch({}, {'method':method, 'params':params or {}, 'operation_id':operation_id})
                    returned.append(result)
                    if response_lost and len(returned)==1:
                        raise model_daily.Blocked('original claim response lost; inspect original operation')
                    return result
                def runner(command, **kwargs):
                    calls.append(command);kwargs['stdout'].write(b'{"type":"turn.completed"}\n')
                    Path(command[command.index('-o')+1]).write_text('synthetic completed response')
                    return Mock(returncode=0)
                with patch.object(api,'Database',return_value=journal), \
                     patch.object(model_daily,'request',side_effect=adapter), \
                     patch.object(model_daily.uuid,'uuid4',return_value=Mock(hex='fixed-original-identity')):
                    if response_lost:
                        with self.assertRaisesRegex(model_daily.Blocked,'response lost'):
                            model_daily.launch_native('issue','btc/research','PORTFOLIO','sha256:'+'a'*64,
                                prompt,binary,root/'first.txt',runner=runner)
                    else:
                        model_daily.launch_native('issue','btc/research','PORTFOLIO','sha256:'+'a'*64,
                            prompt,binary,root/'first.txt',runner=runner)
                    with self.assertRaisesRegex(model_daily.Blocked,'do not launch'):
                        model_daily.launch_native('issue','btc/research','PORTFOLIO','sha256:'+'a'*64,
                            prompt,binary,root/'second.txt',runner=runner)
                self.assertEqual('CLAIMED_NEW', returned[0]['action'])
                self.assertEqual('RECONCILE_EXISTING_NO_LAUNCH',returned[-1]['action'])
                self.assertEqual(0 if response_lost else 1,len(calls))
                stored=journal.value(journal.operations['deep-claim-btc-deep-fixed-original-identity']['result_digest'])
                self.assertEqual('CLAIMED_NEW',stored['action'])

    def test_registration_reads_new_claims_and_preserves_phase_and_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            proof=Path(directory)/'approval.jsonl';proof.write_text('synthetic actual approval proof')
            record={'claim_id':'original-approval','issue_id':'issue','scope':'btc/research',
                    'phase':'APPROVAL','model':'gpt-6-astra','state':'COMPLETE','result_ref':model_daily.sha256(proof)}
            current={'days':{},'claims':{'original-approval':record},'faults':{}}
            with patch.object(model_daily,'status',return_value=current):
                self.assertEqual(record,model_daily.require_success('issue','btc/research','APPROVAL','original-approval',proof))
                with self.assertRaisesRegex(model_daily.Blocked,'completed original'):
                    model_daily.require_success('issue','btc/research','PORTFOLIO','original-approval',proof)
                proof.write_text('changed bytes')
                with self.assertRaisesRegex(model_daily.Blocked,'completed original'):
                    model_daily.require_success('issue','btc/research','APPROVAL','original-approval',proof)

    def test_registration_requires_same_completed_issue_and_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / 'rollout.jsonl'
            proof.write_text('original completed model turn')
            self._check_registration_proof(proof)

    def _check_registration_proof(self, proof):
        record = {'claim_id': 'btc-deep-original', 'issue_id': 'original-issue',
                  'scope': 'btc/research', 'phase': 'PUBLIC', 'model': 'gpt-6-astra',
                  'state': 'COMPLETE', 'result_ref': model_daily.sha256(proof)}
        state = {'days': {'2026-10-12': record}, 'faults': {}}
        with patch.object(model_daily, 'status', return_value=state):
            self.assertEqual(record, model_daily.require_success('original-issue', 'btc/research',
                                                                 'PUBLIC', 'btc-deep-original', proof))
            for issue, phase, claim in [('new-issue', 'PUBLIC', 'btc-deep-original'),
                                        ('original-issue', 'APPROVAL', 'btc-deep-original'),
                                        ('original-issue', 'PUBLIC', 'wrong-claim')]:
                with self.assertRaisesRegex(model_daily.Blocked, 'completed original'):
                    model_daily.require_success(issue, 'btc/research', phase, claim, proof)
            record['result_ref'] = 'sha256:' + 'b'*64
            with self.assertRaisesRegex(model_daily.Blocked, 'completed original'):
                model_daily.require_success('original-issue', 'btc/research', 'PUBLIC', 'btc-deep-original', proof)
            record['result_ref'] = model_daily.sha256(proof)
            state['faults']['fault'] = {'state': 'OPEN', 'scope': 'btc/research'}
            with self.assertRaisesRegex(model_daily.Blocked, 'open defect'):
                model_daily.require_success('original-issue', 'btc/research', 'PUBLIC', 'btc-deep-original', proof)


if __name__ == '__main__':
    unittest.main()
