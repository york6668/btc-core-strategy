"""Original node transaction recovery with synthetic journals, no native DB/model."""
from contextlib import contextmanager
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,threading,unittest
from unittest.mock import patch
from btc_node import api,execution,tasks
from btc_node.codec import Conflict,encode
import test_worker_recovery as worker_fixtures
import node_scheduler_worker as worker


class Journal:
 def __init__(self,domain=None):
  self.domain=domain;self.data=domain.data if domain else {'writer':{'owner':'DOCKER'}}
  self.data['writer']={'owner':'DOCKER'}
  self.settings=domain.settings if domain else {}
  self.operations={};self.blobs={};self.lock=threading.RLock();self.depth=0
 @contextmanager
 def writer(self):
  with self.lock:
   self.depth+=1
   try:yield
   finally:self.depth-=1
 @contextmanager
 def transaction(self):
  old=deepcopy((self.data,self.operations,self.blobs))
  try:yield
  except BaseException:
   self.data.clear();self.data.update(old[0]);self.operations,self.blobs=old[1:];raise
 def metadata(self,name,value=None,*,write=False):
  if write:self.data[name]=deepcopy(value)
  return deepcopy(self.data.get(name))
 def one(self,sql,args=()):
  if "status<>'COMMITTED'"in sql:return next((v for v in self.operations.values()if v['status']!='COMMITTED'),None)
  if 'FROM operations WHERE operation_id='in sql:return deepcopy(self.operations.get(args[0]))
  raise AssertionError(sql)
 def put_blob(self,value):
  digest=hashlib.sha256(value).hexdigest();self.blobs[digest]=value;return digest
 def value(self,digest):return json.loads(self.blobs[digest])
 def execute(self,sql,args=()):
  assert self.depth,'writer barrier required'
  if sql.startswith('INSERT INTO operations'):
   keys=('operation_id','request_digest','status','started_ms','finished_ms','result_digest');record=dict(zip(keys,args))
   assert record['operation_id']not in self.operations
   self.operations[record['operation_id']]=record;return 1
  raise AssertionError(sql)
 def close(self):pass


class OperationRecoveryTests(unittest.TestCase):
 def request(self,**changes):
  params={'task_id':'original-task','attempt_id':'original-attempt','outcome':'COMPLETE','evidence_ref':'node-worker/original-attempt/reconcile-intent.json','completion_ref':'original-result'};params.update(changes)
  return {'method':'task.reconcile','operation_id':'schedule-reconcile-original-attempt','params':params}
 def recover(self,request):
  return {'method':'core.recover','params':{'operation_id':request['operation_id'],'original_request':request}}
 @contextmanager
 def database(self,journal):
  with patch.object(api,'Database',return_value=journal),patch.object(execution,'Database',return_value=journal):yield
 def test_01_absent_operation_is_settled_once_and_delayed_original_reuses_commit(self):
  j=Journal();request=self.request();calls=[]
  def invoke(db,method,params):self.assertGreater(db.depth,0);calls.append((method,params));return {'action':'COMPLETED'}
  with self.database(j),patch.object(api,'invoke',side_effect=invoke):
   recovered=api.dispatch({'journal':'/synthetic-unused'},self.recover(request))
   self.assertEqual('ORIGINAL_NODE_TRANSACTION_RECONCILED',recovered['recovery'])
   self.assertEqual({'action':'COMPLETED'},api.dispatch({},request))
   self.assertEqual(recovered,api.dispatch({},self.recover(request)))
  self.assertEqual(1,len(calls));self.assertEqual(1,len(j.operations))
 def test_02_committed_operation_does_not_run_domain_again(self):
  j=Journal();request=self.request()
  with self.database(j),patch.object(api,'invoke',return_value={'action':'COMPLETED'})as invoke:
   api.dispatch({},request);api.dispatch({},self.recover(request));self.assertEqual(1,invoke.call_count)
 def test_03_changed_original_bytes_cannot_rebind_same_operation(self):
  j=Journal();request=self.request()
  with self.database(j),patch.object(api,'invoke',return_value={'action':'COMPLETED'})as invoke:
   api.dispatch({},request)
   with self.assertRaisesRegex(Conflict,'another request'):api.dispatch({},self.recover(self.request(completion_ref='later-result')))
   self.assertEqual(1,invoke.call_count)
 def test_04_other_methods_and_ids_are_rejected_before_db(self):
  for change in ({'method':'core.execute'},{'operation_id':'fresh-id'},{'params':{}}):
   request=self.request();request.update(change)
   with self.subTest(change=change),patch.object(api,'Database',side_effect=AssertionError('must reject before DB')):
    with self.assertRaises(ValueError):api.dispatch({},self.recover(request))
 def test_05_pending_core_or_non_docker_writer_still_blocks(self):
  for pending in (True,False):
   j=Journal()
   if pending:j.operations['old']={'status':'EXECUTING'}
   else:j.data['writer']={'owner':'HOST'}
   with self.database(j),patch.object(api,'invoke',side_effect=AssertionError('no domain write')):
    with self.assertRaises(Conflict):api.dispatch({},self.recover(self.request()))
 def test_06_domain_failure_rolls_back_operation_and_state(self):
  j=Journal()
  def fail(db,*args):db.metadata('partial','must rollback',write=True);raise Conflict('actual artifact absent')
  with self.database(j),patch.object(api,'invoke',side_effect=fail):
   with self.assertRaisesRegex(Conflict,'actual artifact absent'):api.dispatch({},self.recover(self.request()))
  self.assertNotIn('partial',j.data);self.assertEqual({},j.operations)
 def test_07_concurrent_late_dispatch_and_recovery_invoke_domain_once(self):
  j=Journal();request=self.request();calls=[]
  def invoke(*args):calls.append(1);return {'action':'COMPLETED'}
  with self.database(j),patch.object(api,'invoke',side_effect=invoke),ThreadPoolExecutor(max_workers=8)as pool:
   results=list(pool.map(lambda i:api.dispatch({},request if i%2 else self.recover(request)),range(16)))
  self.assertEqual(1,len(calls));self.assertEqual(1,len(j.operations));self.assertEqual(16,len(results))
 def test_08_absence_without_exact_request_keeps_original_unknown(self):
  j=Journal()
  with self.database(j):
   with self.assertRaisesRegex(ValueError,'unknown operation'):api.dispatch({'journal':'/synthetic-unused'},{'method':'core.recover','params':{'operation_id':'unknown-original'}})
  self.assertEqual({},j.operations)
 def test_09_worker_missing_server_record_recovers_saved_request_despite_late_event(self):
  fixture=worker_fixtures.WorkerRecoveryTests()
  with fixture.case()as(c,domain,task,attempts,d,client,config,write):
   j=Journal(domain);original=client.request;sent=[]
   def request(method,params=None,operation_id=None):
    if method=='task.reconcile':client.reconciliations+=1;raise RuntimeError('original transport UNKNOWN before server commit')
    if method=='core.recover':sent.append(deepcopy(params));return api.dispatch({}, {'method':method,'params':params})
    return original(method,params,operation_id)
   client.request=request
   with self.database(j):
    worker.run_once(config,client,fixture.never_launch)
    with(d/'events.jsonl').open('a')as out:out.write('{"type":"item.completed","item":{"type":"agent_message","text":"late"}}\n')
    result=worker.run_once(config,client,fixture.never_launch)
   self.assertEqual('RECOVERED_ORIGINAL_ATTEMPT_NO_LAUNCH',result['action'])
   self.assertEqual(1,client.reconciliations);self.assertEqual(1,len(sent));self.assertEqual(1,len(j.operations))
   self.assertEqual('COMPLETED',tasks.status(domain,task_id=task['task_id'])['state'])
 def test_10_old_fence_without_saved_envelope_holds_no_recovery(self):
  fixture=worker_fixtures.WorkerRecoveryTests()
  with fixture.case()as(c,domain,task,attempts,d,client,config,write):
   write(d/'reconcile-intent.json',{'task':worker._binding(task),'operation_id':'schedule-reconcile-'+task['attempt_id'],'outcome':'COMPLETE','completion_ref':'old'})
   result=worker.run_once(config,client,fixture.never_launch)
   self.assertEqual('UNKNOWN_NO_REPLAY',result['recovery'][0]['action']);self.assertEqual(0,client.reconciliations)
 def test_11_lost_recovery_response_keeps_one_server_commit(self):
  fixture=worker_fixtures.WorkerRecoveryTests()
  with fixture.case()as(c,domain,task,attempts,d,client,config,write):
   j=Journal(domain);original=client.request
   def request(method,params=None,operation_id=None):
    if method=='task.reconcile':raise RuntimeError('unknown before original commit')
    if method=='core.recover':api.dispatch({}, {'method':method,'params':params});raise RuntimeError('recovery response UNKNOWN after commit')
    return original(method,params,operation_id)
   client.request=request
   with self.database(j):
    worker.recover_pending(client,attempts);worker.recover_pending(client,attempts);worker.recover_pending(client,attempts)
   self.assertEqual(1,len(j.operations));self.assertEqual([],worker.recover_pending(client,attempts))

 def test_12_changed_intent_and_envelope_with_stale_checksum_must_hold(self):
  self._drift_intent(False)
 def test_13_recomputed_nonexecution_proof_cannot_ignore_completed_artifacts(self):
  self._drift_intent(True)
 def _drift_intent(self,recompute):
  fixture=worker_fixtures.WorkerRecoveryTests()
  with fixture.case()as(c,domain,task,attempts,d,client,config,write):
   original=client.request;recovered=[]
   def request(method,params=None,operation_id=None):
    if method=='task.reconcile':raise RuntimeError('original transport UNKNOWN')
    if method=='core.recover':recovered.append(params);raise AssertionError('damaged intent must HOLD')
    return original(method,params,operation_id)
   client.request=request;worker.run_once(config,client,fixture.never_launch)
   p=d/'reconcile-intent.json';saved=json.loads(p.read_text());saved['outcome']='NOT_EXECUTED';saved['completion_ref']=None
   saved['original_request']['params']['outcome']='NOT_EXECUTED';saved['original_request']['params'].pop('completion_ref')
   if recompute:
    saved['evidence']={'claim_sha256':hashlib.sha256((d/'claim.json').read_bytes()).hexdigest(),'launch_intent_absent_under_original_worker_lock':True}
    proof={k:saved[k]for k in ('worker_receipt_revision','task','outcome','completion_ref','evidence')}
    saved['proof_sha256']=hashlib.sha256(json.dumps(proof,sort_keys=True).encode()).hexdigest()
   p.write_text(json.dumps(saved));worker.run_once(config,client,fixture.never_launch)
   self.assertEqual([],recovered);self.assertEqual('RUNNING',tasks.status(domain,task_id=task['task_id'])['state'])
 def test_14_valid_nonexecution_receipt_recovers_only_original_transaction(self):
  fixture=worker_fixtures.WorkerRecoveryTests()
  with fixture.case(native=False,intent=False)as(c,domain,task,attempts,d,client,config,write):
   j=Journal(domain);original=client.request
   def request(method,params=None,operation_id=None):
    if method=='task.reconcile':raise RuntimeError('original transport UNKNOWN')
    if method=='core.recover':return api.dispatch({}, {'method':method,'params':params})
    return original(method,params,operation_id)
   client.request=request
   with self.database(j):
    worker.run_once(config,client,fixture.never_launch);result=worker.run_once(config,client,fixture.never_launch)
   self.assertEqual('RECOVERED_ORIGINAL_ATTEMPT_NO_LAUNCH',result['action']);self.assertEqual(1,len(j.operations))
   self.assertEqual('NEEDED',tasks.status(domain,task_id=task['task_id'])['state'])
