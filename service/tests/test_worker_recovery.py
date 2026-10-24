"""Resume original node attempts using disposable domain artifacts; no models."""
from pathlib import Path
from datetime import datetime,timezone
from contextlib import contextmanager
import hashlib,json,os,unittest
from unittest.mock import patch
import test_scheduler as scheduler_fixtures
Node = scheduler_fixtures.Node
from btc_node import scheduler,tasks
from btc_node.codec import encode
import node_scheduler_worker as worker


class WorkerRecoveryTests(unittest.TestCase):
 @contextmanager
 def case(self,*,native=True,intent=True,revision=2):
  case=scheduler_fixtures.SchedulerTests('test_worker_disabled_has_no_node_side_effect');case.setUp()
  try:
   db=Node(case.root,'ACTIVE');scheduler.observe_reader(db,case.receipt(db));scheduler.tick(db)
   task=scheduler.claim(db,attempt_id='node-'+'1'*32)['task']
   attempts=case.root/'attempts';attempts.mkdir(mode=0o700)
   directory=attempts/task['attempt_id'];directory.mkdir(mode=0o700)
   def write(path,value):worker._write_private(path,json.dumps(value,sort_keys=True).encode())
   claim=dict(task)
   if revision is not None:claim['worker_receipt_revision']=revision
   write(directory/'claim.json',claim)
   if intent:write(directory/'launch-intent.json',{'worker_receipt_revision':2,'task':{k:task[k]for k in ('task_id','attempt_id','kind','issue_id','phase','input_digest','evidence_ref','release_id','started_ms')}})
   root=case.root/'project'/'.strategy/v4';(root/'sensing').mkdir(parents=True)
   value={'created_at':datetime.fromtimestamp(case.clock/1000,timezone.utc).isoformat(),'main_eligible':False,'research_eligible':False}
   identity='sha256:'+hashlib.sha256(encode(value)).hexdigest();value['observation_id']=identity
   write(root/'sensing'/(identity[7:]+'.json'),value);write(root/'sensing-current.json',{'observation_id':identity})
   if native:
    worker._write_private(directory/'events.jsonl',b'{"type":"thread.started","thread_id":"synthetic-native-original"}\n{"type":"turn.started"}\n{"type":"turn.completed"}\n')
    write(directory/'last-message.json',{'task_id':task['task_id'],'attempt_id':task['attempt_id'],'completion_ref':identity})
   class Client:
    def __init__(self):self.calls=[];self.reconciliations=0
    def request(self,method,params=None,operation_id=None):
     self.calls.append((method,params,operation_id))
     if method=='task.status':return tasks.status(db)
     if method=='task.reconcile':self.reconciliations+=1;return tasks.reconcile(db,**params)
     if method=='core.recover':raise RuntimeError('original recovery capability unavailable in this fixture')
     if method=='schedule.window':return scheduler.window(db)
     if method=='schedule.claim':return scheduler.claim(db,**params)
     raise AssertionError(method)
   client=Client();config={'project_dir':str(case.root/'project'),'release_root':str(case.root),'node_scheduler':{'enabled':True,'attempt_dir':str(attempts),'codex_cli':str(case.cli)}}
   yield case,db,task,attempts,directory,client,config,write
  finally:case.doCleanups()

 def never_launch(self,*args):raise AssertionError('original attempt must not relaunch')

 def test_01_native_complete_lost_finish_recovered_without_second_launch(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   result=worker.run_once(config,client,self.never_launch)
   self.assertEqual(result['action'],'RECOVERED_ORIGINAL_ATTEMPT_NO_LAUNCH')
   actual=tasks.status(db,task_id=task['task_id']);self.assertEqual(actual['state'],'COMPLETED');self.assertEqual(actual['attempts'],1)
   self.assertEqual(client.reconciliations,1)
   self.assertNotIn('schedule.claim',[call[0]for call in client.calls])

 def test_02_unknown_native_never_replayed(self):
  with self.case(native=False)as(c,db,task,attempts,d,client,config,write):
   for _ in range(2):worker.run_once(config,client,self.never_launch)
   self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')
   self.assertEqual(client.reconciliations,0)

 def test_03_error_followed_by_complete_is_not_success(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   (d/'events.jsonl').write_bytes(b'{"type":"thread.started","thread_id":"original"}\n{"type":"error","message":"host unavailable"}\n{"type":"turn.completed"}\n')
   self.assertIsNone(worker._native_session(d/'events.jsonl'))
   worker.run_once(config,client,self.never_launch);self.assertEqual(client.reconciliations,0)

 def test_04_claim_saved_before_launch_intent_proves_no_execution(self):
  with self.case(native=False,intent=False)as(c,db,task,attempts,d,client,config,write):
   result=worker.run_once(config,client,self.never_launch)
   self.assertEqual(result['action'],'RECOVERED_ORIGINAL_ATTEMPT_NO_LAUNCH')
   self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'NEEDED')
   self.assertEqual(tasks.status(db,task_id=task['task_id'])['attempts'],1)

 def test_05_legacy_no_intent_without_output_is_unknown(self):
  with self.case(native=False,intent=False,revision=None)as(c,db,task,attempts,d,client,config,write):
   worker.run_once(config,client,self.never_launch)
   self.assertEqual(client.reconciliations,0);self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')

 def test_06_legacy_positive_complete_can_be_consumed(self):
  with self.case(intent=False,revision=None)as(c,db,task,attempts,d,client,config,write):
   self.assertEqual(worker.run_once(config,client,self.never_launch)['action'],'RECOVERED_ORIGINAL_ATTEMPT_NO_LAUNCH')
   self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'COMPLETED')

 def test_07_task_input_drift_rejects_saved_result(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   claim=json.loads((d/'claim.json').read_text());claim['input_digest']='sha256:'+'f'*64;write(d/'new.json',claim);os.replace(d/'new.json',d/'claim.json')
   worker.run_once(config,client,self.never_launch);self.assertEqual(client.reconciliations,0)

 def test_08_message_identity_must_match_original_attempt(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   value=json.loads((d/'last-message.json').read_text());value['attempt_id']='other'
   (d/'last-message.json').unlink();write(d/'last-message.json',value)
   worker.run_once(config,client,self.never_launch);self.assertEqual(client.reconciliations,0)

 def test_09_recovery_precedes_clock_and_executor_availability(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   db.settings['scheduler_mode']='OFF';config['node_scheduler']['codex_cli']='/synthetic/missing'
   self.assertEqual(worker.run_once(config,client,self.never_launch)['action'],'RECOVERED_ORIGINAL_ATTEMPT_NO_LAUNCH')
   self.assertEqual([call[0]for call in client.calls],['task.status','task.reconcile'])

 def test_10_reconcile_transport_unknown_is_not_resent(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   request=client.request
   def lost(method,params=None,operation_id=None):
    if method=='task.reconcile':client.reconciliations+=1;raise RuntimeError('transport outcome unknown')
    return request(method,params,operation_id)
   client.request=lost
   for _ in range(2):worker.run_once(config,client,self.never_launch)
   self.assertEqual(client.reconciliations,1);self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')
   self.assertEqual(len(list(d.glob('reconcile-*.json'))),1)

 def test_11_node_commit_reply_lost_consumes_actual_state_on_next_entry(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   request=client.request
   def lost(method,params=None,operation_id=None):
    result=request(method,params,operation_id)
    if method=='task.reconcile':raise RuntimeError('reply lost after node commit')
    return result
   client.request=lost
   for _ in range(2):worker.run_once(config,client,self.never_launch)
   self.assertEqual(client.reconciliations,1);self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'COMPLETED')

 def test_12_missing_actual_domain_artifact_cannot_complete(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   for p in (c.root/'project'/'.strategy/v4/sensing').iterdir():p.unlink()
   request=client.request
   def boundary(method,params=None,operation_id=None):
    try:return request(method,params,operation_id)
    except Exception as exc:raise RuntimeError(str(exc))from exc
   client.request=boundary
   worker.run_once(config,client,self.never_launch);self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')
   self.assertEqual(client.reconciliations,1)

 def test_13_symlink_artifact_rejected_without_following(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   old=d/'events.jsonl';other=c.root/'outside-events';old.rename(other);old.symlink_to(other)
   worker.run_once(config,client,self.never_launch);self.assertEqual(client.reconciliations,0)

 def test_14_status_unavailable_cannot_admit_new_model(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   def unavailable(*args,**kwargs):raise RuntimeError('node status unavailable')
   client.request=unavailable
   self.assertEqual(worker.run_once(config,client,self.never_launch)['action'],'ORIGINAL_ATTEMPT_STATUS_UNAVAILABLE_NO_LAUNCH')

 def test_15_missing_intent_with_execution_artifacts_cannot_be_not_executed(self):
  with self.case(intent=False)as(c,db,task,attempts,d,client,config,write):
   worker.run_once(config,client,self.never_launch)
   self.assertEqual(client.reconciliations,0)
   self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')

 def test_16_completed_event_with_failure_payload_is_not_terminal_success(self):
  for terminal in ({'type':'turn.completed','status':'failed'},
                   {'type':'turn.completed','turn':{'status':'interrupted'}},
                   {'type':'turn.completed','error':{'message':'failure'}},
                   {'type':'turn.completed','turn':{'error':{'message':'failure'}}}):
   with self.subTest(terminal=terminal),self.case()as(c,db,task,attempts,d,client,config,write):
    (d/'events.jsonl').write_text(json.dumps({'type':'thread.started','thread_id':'original'})+'\n'+json.dumps(terminal)+'\n')
    worker.run_once(config,client,self.never_launch);self.assertEqual(client.reconciliations,0)
    self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')

 def test_17_multiple_completions_are_ambiguous(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   with (d/'events.jsonl').open('a')as stream:stream.write('{"type":"turn.completed"}\n')
   worker.run_once(config,client,self.never_launch);self.assertEqual(client.reconciliations,0)

 def test_18_native_error_item_followed_by_complete_stays_unknown(self):
  # Same event shape as the retained startup failure; synthetic local message.
  with self.case()as(c,db,task,attempts,d,client,config,write):
   events=[{'type':'thread.started','thread_id':'original'},
           {'type':'item.completed','item':{'id':'synthetic-error','type':'error','message':'synthetic host startup failure'}},
           {'type':'turn.completed'}]
   (d/'events.jsonl').write_text(''.join(json.dumps(event)+'\n'for event in events))
   self.assertIsNone(worker._native_session(d/'events.jsonl'))
   worker.run_once(config,client,self.never_launch);self.assertEqual(client.reconciliations,0)

 def test_19_unknown_with_late_event_never_creates_new_reconcile(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   request=client.request
   def unknown(method,params=None,operation_id=None):
    if method=='task.reconcile':client.reconciliations+=1;raise RuntimeError('transport UNKNOWN')
    return request(method,params,operation_id)
   client.request=unknown;worker.run_once(config,client,self.never_launch)
   with(d/'events.jsonl').open('a')as stream:stream.write('{"type":"item.completed","item":{"type":"agent_message","text":"late ordinary event"}}\n')
   worker.run_once(config,client,self.never_launch)
   self.assertEqual(client.reconciliations,1)
   self.assertEqual(len(list(d.glob('reconcile-*.json'))),1)
   self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')

 def test_20_unknown_with_changed_message_never_creates_new_reconcile(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   request=client.request
   def unknown(method,params=None,operation_id=None):
    if method=='task.reconcile':client.reconciliations+=1;raise RuntimeError('transport UNKNOWN')
    return request(method,params,operation_id)
   client.request=unknown;worker.run_once(config,client,self.never_launch)
   value=json.loads((d/'last-message.json').read_text());value['completion_ref']='sha256:'+'e'*64
   (d/'last-message.json').unlink();write(d/'last-message.json',value)
   worker.run_once(config,client,self.never_launch)
   self.assertEqual(client.reconciliations,1)

 def test_21_legacy_per_proof_intent_fences_entire_attempt(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   write(d/('reconcile-'+'f'*64+'.json'),{'outcome':'COMPLETE','original_request_outcome':'UNKNOWN'})
   result=worker.run_once(config,client,self.never_launch)
   self.assertEqual(result['recovery'][0]['original_operation_ids'],['schedule-reconcile-'+task['attempt_id']+'-'+'f'*16])
   self.assertEqual(client.reconciliations,0)
   self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')

 def test_22_completed_then_unfinished_new_turn_cannot_finish(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   with(d/'events.jsonl').open('a')as stream:stream.write('{"type":"turn.started"}\n')
   worker.run_once(config,client,self.never_launch)
   self.assertEqual(client.reconciliations,0)
   self.assertEqual(tasks.status(db,task_id=task['task_id'])['state'],'RUNNING')

 def test_23_turn_identity_and_thread_identity_must_match(self):
  for end in ({'type':'turn.completed','turn_id':'foreign-turn'},
              {'type':'turn.completed','turn_id':'original-turn','thread_id':'foreign-thread'}):
   with self.subTest(end=end),self.case()as(c,db,task,attempts,d,client,config,write):
    events=[{'type':'thread.started','thread_id':'original-thread'},
            {'type':'turn.started','turn_id':'original-turn'},end]
    (d/'events.jsonl').write_text(''.join(json.dumps(event)+'\n'for event in events))
    worker.run_once(config,client,self.never_launch)
    self.assertEqual(client.reconciliations,0)

 def test_24_original_explicit_turn_identity_is_preserved(self):
  with self.case()as(c,db,task,attempts,d,client,config,write):
   events=[{'type':'thread.started','thread_id':'original-thread'},
           {'type':'turn.started','turn_id':'original-turn'},
           {'type':'turn.completed','turn_id':'original-turn','thread_id':'original-thread'}]
   (d/'events.jsonl').write_text(''.join(json.dumps(event)+'\n'for event in events))
   self.assertEqual(worker.run_once(config,client,self.never_launch)['action'],'RECOVERED_ORIGINAL_ATTEMPT_NO_LAUNCH')
   proof=json.loads((d/'reconcile-intent.json').read_text())
   self.assertEqual(proof['evidence']['native_turn_id'],'original-turn')
   self.assertEqual(proof['evidence']['native_thread_id'],'original-thread')

 def test_25_invalid_order_or_missing_start_never_completes(self):
  thread={'type':'thread.started','thread_id':'original'};start={'type':'turn.started'};end={'type':'turn.completed'}
  cases=[[thread,end],[thread,end,start],[thread,start,start,end],
         [thread,thread,start,end],[thread,{'type':'item.completed'},start,end],
         [thread,{'type':'turn.started','turn_id':'original-turn'},end],
         [thread,start,{'type':'item.completed','thread_id':'foreign'},end],
         [thread,start,end,{'type':'turn.cancelled'}],
         [thread,start,{'type':'turn.completed','turn':{'thread_id':'foreign'}}],
         [thread,{'type':'turn.started','status':'failed'},end]]
  for events in cases:
   with self.subTest(events=events):self.assertIsNone(worker._native_terminal(''.join(json.dumps(event)+'\n'for event in events)))

if __name__=='__main__':unittest.main(verbosity=2)
