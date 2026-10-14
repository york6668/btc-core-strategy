"""Isolated daily admission using one shared simulated node authority."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from btc_node import deep_gate
from btc_node.api import invoke
from btc_node.codec import Conflict
from btc_node.execution import validate_deep_result_admission


def at(value):
    return int(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)


def digest(value):
    return 'sha256:' + hashlib.sha256(value.encode()).hexdigest()


class SharedNode:
    def __init__(self):
        self.data={}
        self.writer=threading.Lock()

    def client(self):
        owner=self
        class Client:
            def metadata(self,name,value=None,*,write=False):
                if write: owner.data[name]=value
                return owner.data.get(name)
        return Client()


class DeepGateTests(unittest.TestCase):
    def setUp(self):
        self.node=SharedNode()
        self.input=digest('locked real input represented by synthetic bytes')
        self.clock=at('2026-10-12T14:59:59Z')

    def call(self,claim='original-claim',issue='original-issue',model='gpt-6-astra',scope='btc/research',phase='PUBLIC'):
        with self.node.writer,patch.object(deep_gate,'millis',return_value=self.clock):
            return deep_gate.claim(self.node.client(),claim_id=claim,issue_id=issue,
                                   scope=scope,phase=phase,input_digest=self.input,model=model)

    def settle(self,claim,outcome,**fields):
        with self.node.writer,patch.object(deep_gate,'millis',return_value=self.clock):
            return deep_gate.finish(self.node.client(),claim_id=claim,outcome=outcome,**fields)

    def test_tokyo_day_and_cross_midnight_in_flight(self):
        self.assertEqual('2026-10-12',deep_gate.tokyo_day(self.clock))
        self.assertEqual('2026-10-13',deep_gate.tokyo_day(self.clock+1000))
        first=self.call()
        self.assertEqual('2026-10-12',first['day'])
        self.clock+=1000
        with self.assertRaisesRegex(Conflict,'in-flight'):
            self.call('tomorrow-claim')
        settled=self.settle('original-claim','COMPLETE',result_ref='actual-original-report')
        self.assertEqual('2026-10-12',settled['claim']['day'])
        self.assertEqual('CLAIMED_NEW',self.call('tomorrow-claim')['action'])

    def test_same_day_tasks_models_and_node_clients_share_one_slot(self):
        def try_claim(index):
            try:return self.call('claim-id-'+str(index),'split-task-'+str(index),
                                 'gpt-6-astra' if index%2 else 'gpt-6-other-high')
            except Conflict as exc:return str(exc)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(try_claim,range(8)))
        self.assertEqual(1,sum(isinstance(v,dict) and v['action']=='CLAIMED_NEW' for v in results))
        original=next(v['claim']['claim_id'] for v in results if isinstance(v,dict))
        self.settle(original,'COMPLETE',result_ref='frozen-deep-research')
        after=self.call('same-original-request','split-task-'+original.split('-')[-1],
                        'gpt-6-astra' if int(original.split('-')[-1])%2 else 'gpt-6-other-high')
        self.assertEqual('REUSE',after['action'])
        self.assertEqual(original,after['claim']['claim_id'])
        with self.assertRaisesRegex(Conflict,'another issue or input'):
            self.call('another-task','renamed-task','gpt-6-other-high')

    def test_failed_capacity_call_spends_day_and_requires_recovery(self):
        self.call()
        ended=self.settle('original-claim','FAILED',failure_kind='CAPACITY',failure_ref='actual-error-log')
        self.assertEqual('FAILED',ended['claim']['state'])
        with self.assertRaisesRegex(Conflict,'known defect'):
            self.call('retry-same-day')
        fault=next(iter(self.node.data[deep_gate.STATE_NAME]['faults'].values()))
        with self.assertRaisesRegex(Conflict,'service recovery'):
            deep_gate.resolve_fault(self.node.client(),fault_id=fault['fault_id'],
                repair_digest=digest('service-alive'),verification_ref='provider-health',verification_status='PASS')
        deep_gate.resolve_fault(self.node.client(),fault_id=fault['fault_id'],
            repair_digest=digest('service-alive'),verification_ref='provider-health',verification_status='RECOVERED')
        with self.assertRaisesRegex(Conflict,'today'):
            self.call('retry-same-day')
        self.clock=at('2026-10-12T15:00:00Z')
        self.assertEqual('CLAIMED_NEW',self.call('next-natural-day')['action'])

    def test_unknown_and_cancellation_do_not_refill_or_move_original_day(self):
        self.call()
        self.clock=at('2026-10-12T15:00:00Z')
        self.settle('original-claim','UNKNOWN',failure_kind='NETWORK',failure_ref='transport-lost')
        with self.assertRaisesRegex(Conflict,'known defect'):
            self.call('new-day-before-reconcile')
        fault=next(iter(self.node.data[deep_gate.STATE_NAME]['faults'].values()))
        with self.node.writer:
            deep_gate.reconcile_unknown(self.node.client(),claim_id='original-claim',
                outcome='CANCELED',evidence_ref='verified-original-process-exit')
            deep_gate.resolve_fault(self.node.client(),fault_id=fault['fault_id'],
                repair_digest=digest('actual-transport-recovered'),
                verification_ref='service-recovery-proof',verification_status='RECOVERED')
        self.assertEqual('CLAIMED_NEW',self.call('next-natural-day')['action'])
        with self.assertRaisesRegex(Conflict,'today'):
            self.call('new-day-again')

    def test_known_defect_requires_changed_repair_and_keeps_unrelated_scope(self):
        fault=deep_gate.open_fault(self.node.client(),scope='btc/report',kind='DETERMINISTIC',
            signature=digest('report-contract-bug'),fault_digest=digest('bad-code'),evidence_ref='actual-trace')
        self.assertEqual('CLAIMED_NEW',self.call(scope='btc/research')['action'])
        with self.assertRaisesRegex(Conflict,'changed repair'):
            deep_gate.resolve_fault(self.node.client(),fault_id=fault['fault_id'],
                repair_digest=digest('bad-code'),verification_ref='synthetic-offline-test',verification_status='PASS')
        fixed=deep_gate.resolve_fault(self.node.client(),fault_id=fault['fault_id'],
            repair_digest=digest('fixed-code'),verification_ref='synthetic-offline-test',verification_status='PASS')
        self.assertEqual('RESOLVED',fixed['state'])

    def test_ordinary_scope_check_blocks_only_open_affected_stage(self):
        fault=deep_gate.open_fault(self.node.client(),scope='btc/market',kind='DETERMINISTIC',
            signature=digest('revision9 stored forward'),fault_digest=digest('locked failed candidate'),
            evidence_ref='actual natural candidate')
        blocked=deep_gate.check_scope(self.node.client(),scope='btc/market/public')
        self.assertFalse(blocked['allowed'])
        self.assertEqual(blocked,invoke(self.node.client(),'model.defect.check',{'scope':'btc/market/public'}))
        with self.assertRaisesRegex(Conflict,'known public-market defect'):
            invoke(self.node.client(),'market.freeze',{'spec':{},'expected_version':0})
        self.assertEqual([fault['fault_id']],[item['fault_id'] for item in blocked['faults']])
        self.assertTrue(deep_gate.check_scope(self.node.client(),scope='btc/report')['allowed'])
        self.assertEqual({},self.node.data[deep_gate.STATE_NAME]['days'])
        deep_gate.resolve_fault(self.node.client(),fault_id=fault['fault_id'],
            repair_digest=digest('changed verified candidate'),verification_ref='locked isolated replay',
            verification_status='PASS')
        self.assertTrue(deep_gate.check_scope(self.node.client(),scope='btc/market')['allowed'])

    def test_child_fault_blocks_broader_affected_operation(self):
        deep_gate.open_fault(self.node.client(),scope='btc/research/public',kind='SEMANTIC',
            signature=digest('public claim'),fault_digest=digest('unsupported assertion'),
            evidence_ref='actual-public-review')
        with self.assertRaisesRegex(Conflict,'known defect'):
            self.call(scope='btc/research')
        self.assertEqual('CLAIMED_NEW',self.call(scope='btc/report')['action'])

    def test_old_pinned_registration_cannot_bypass_shared_node(self):
        with tempfile.TemporaryDirectory() as directory:
            release=Path(directory)
            argv=['--project','/isolated','--root','/isolated/.strategy/v4','--full',
                  'research-independent','--request','synthetic',
                  '--guard-attempt','original-claim']
            with self.assertRaisesRegex(Conflict,'pinned release lacks'):
                validate_deep_result_admission(self.node.client(),argv,release)
            script=release/'skill/btc-core-strategy/scripts/model_daily.py'
            script.parent.mkdir(parents=True)
            script.write_text('# synthetic release marker\n')
            with self.assertRaisesRegex(Conflict,'completed Astra claim'):
                validate_deep_result_admission(self.node.client(),argv,release)
            self.call()
            with self.assertRaisesRegex(Conflict,'completed Astra claim'):
                validate_deep_result_admission(self.node.client(),argv,release)
            self.settle('original-claim','COMPLETE',result_ref=digest('synthetic trace'))
            validate_deep_result_admission(self.node.client(),argv,release)


if __name__=='__main__': unittest.main()
