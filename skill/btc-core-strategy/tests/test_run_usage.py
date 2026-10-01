from pathlib import Path
import json,sys,tempfile,unittest,sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_usage

class UsageTests(unittest.TestCase):
    def test_elapsed_time_and_error_quantiles_keep_missing_values_unknown(self):
        summary=run_usage.summarize([{'elapsed_seconds':n,'repeated_errors':0} for n in (10,20,30,None)])
        self.assertEqual(summary['elapsed_seconds'],{'known_runs':3,'median':20,'p95':30,'sum':60})
        self.assertIsNone(summary['model_roundtrips']['median'])
    def test_response_dedup_and_cache_are_not_double_counted(self):
        events=[{'type':'event_msg','payload':{'type':'task_started','turn_id':'t'}},
            {'type':'token_usage_record','payload':{'response_id':'a','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':10}}},
            {'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':1000}}}},
            {'type':'response_item','payload':{'type':'function_call','call_id':'x','name':'read'}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':'t','completed_at':'2030-03-01T01:00:00Z'}}]
        events.append(events[1]);events.append(events[3])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'rollout.jsonl';p.write_text(''.join(json.dumps(e)+'\n' for e in events));v=run_usage.rollout(p)
        self.assertEqual((v['input_tokens'],v['cached_input_tokens'],v['model_roundtrips'],v['tool_calls']),(100,80,1,1));self.assertTrue(v['completed'])
    def test_interrupted_followup_and_missing_usage_remain_unknown(self):
        events=[{'type':'event_msg','payload':{'type':'task_complete','turn_id':'old'}},{'type':'event_msg','payload':{'type':'task_started','turn_id':'new'}}]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r';p.write_text(''.join(json.dumps(e)+'\n' for e in events));v=run_usage.rollout(p)
        self.assertFalse(v['completed']);self.assertIsNone(v['input_tokens']);self.assertIsNone(v['model_roundtrips'])

    def test_window_descendants_and_failed_costs_are_counted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);db=base/'state.sqlite'
            def write(name,events):
                p=base/name;p.write_text(''.join(json.dumps(e)+'\n' for e in events));return str(p)
            def event(kind,payload,hour='01'):return {'timestamp':'2030-03-01T'+hour+':00:00Z','type':kind,'payload':payload}
            usage=lambda rid,tid: {'response_id':rid,'thread_id':tid,'usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':10}}
            parent=write('p',[event('token_usage_record',usage('outside','p'),'00'),
                event('event_msg',{'type':'task_started','turn_id':'p'}),event('token_usage_record',usage('p1','p')),
                event('event_msg',{'type':'task_complete','turn_id':'p'})])
            child=write('c',[event('token_usage_record',usage('p1','p')),
                event('event_msg',{'type':'task_started','turn_id':'c'}),event('token_usage_record',usage('c1','c')),
                event('token_usage_record',usage('c1','c')),event('event_msg',{'type':'turn_aborted','turn_id':'c'})])
            with sqlite3.connect(db) as con:
                con.execute('create table threads(id,rollout_path,created_at,updated_at,model,reasoning_effort,thread_source,name,source)')
                con.execute('create table thread_spawn_edges(parent_thread_id,child_thread_id)')
                t=run_usage.timestamp('2030-03-01T01:00:00Z')
                con.executemany('insert into threads values(?,?,?,?,?,?,?,?,?)',[
                    ('p',parent,t,t,'gpt-5.6-sol','high','automation','BTC核心策略自动重评','vscode'),
                    ('c',child,t,t,'gpt-6-astra','max','subagent',None,'{}')])
                con.execute("insert into thread_spawn_edges values('p','c')")
            result=run_usage.audit(db,'2030-03-01T01:00:00Z','2030-03-01T02:00:00Z')
            self.assertEqual(result['summary']['completed_runs'],0)
            self.assertEqual(result['cost_totals']['all']['model_roundtrips']['sum'],2)
            self.assertEqual(result['cost_totals']['all']['input_tokens']['sum'],200)
            self.assertEqual(result['cost_totals']['incomplete_or_failed']['model_roundtrips']['sum'],1)
            self.assertEqual(next(v for v in result['thread_costs'] if v['thread_id']=='c')['root_thread_id'],'p')
            with open(child,'a') as stream:
                stream.write(json.dumps(event('event_msg',{'type':'task_complete','turn_id':'c'}))+'\n')
            recovered=run_usage.audit(db,'2030-03-01T01:00:00Z','2030-03-01T02:00:00Z')
            self.assertEqual(recovered['summary']['completed_runs'],1)
            self.assertEqual(recovered['groups']['gpt-5.6-sol/high/research']['model_roundtrips']['sum'],2)

    def test_usage_target_never_closes_quality_acceptance(self):
        def audit(n):return {'groups':{'gpt-5.6-sol/high/ordinary':{'model_roundtrips':{'median':n}}}}
        result=run_usage.compare(audit(17),audit(29))['gpt-5.6-sol/high/ordinary']
        self.assertEqual(result['target_median_at_most'],14)
        self.assertFalse(result['usage_target_met'])
        self.assertEqual(result['natural_quality_and_daily_delivery_acceptance'],'SEPARATE_REQUIRED')

    def test_passing_usage_cannot_close_major_defect_and_failure_reopens_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'defect.json';path.write_text(json.dumps({'status':'OPEN_IMPLEMENTING'}))
            value={'since':'2030-03-01T00:00:00Z','until':'2030-03-02T00:00:00Z',
                'comparison':{'ordinary':{'usage_target_met':True}},
                'recurrence_signals':{'repeated_errors':0,'wait_calls':0},'cost_totals':{'incomplete_or_failed':{}}}
            result=run_usage.record_defect_check(path,value,Path(directory)/'usage.json')
            self.assertTrue(result['defect_status'].startswith('OPEN'))
            value['until']='2030-03-03T00:00:00Z';value['recurrence_signals']['repeated_errors']=2
            result=run_usage.record_defect_check(path,value,Path(directory)/'usage.json')
            self.assertEqual(result['defect_status'],'OPEN_RECURRENCE_OR_USAGE_TARGET_FAILED')
            self.assertEqual(len(json.loads(path.read_text())['natural_acceptance']['usage_checks']),2)

    def test_model_switch_keeps_relative_unknown_and_checks_absolute_target(self):
        def audit(model, median):return {'groups':{model+'/high/ordinary':{'model_roundtrips':{'median':median}}}}
        current=audit('gpt-6.1-sol',25);baseline=audit('gpt-5.6-sol',28)
        result=run_usage.compare(current,baseline)['gpt-6.1-sol/high/ordinary']
        self.assertIsNone(result['relative_target_met']);self.assertFalse(result['absolute_target_met'])
        self.assertFalse(result['usage_target_met'])
        result=run_usage.compare(audit('gpt-6.1-sol',12),baseline)['gpt-6.1-sol/high/ordinary']
        self.assertTrue(result['absolute_target_met']);self.assertIsNone(result['usage_target_met'])
        self.assertIsNone(run_usage.absolute_target(audit('gpt-6.1-sol',None))['met'])

    def test_missing_comparison_or_later_success_never_erases_unresolved_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'defect.json';p.write_text(json.dumps({'status':'OPEN_RECURRENCE_OR_USAGE_TARGET_FAILED'}))
            value={'since':'a','until':'b','comparison':{},'recurrence_signals':{'repeated_errors':0,'wait_calls':0},
                   'cost_totals':{'incomplete_or_failed':{}}}
            for comparison,target in [({},None),({'ordinary':{'usage_target_met':True}},True)]:
                value.update(comparison=comparison,target_check={'met':target})
                result=run_usage.record_defect_check(p,value,Path(d)/'usage.json')
                self.assertEqual(result['defect_status'],'OPEN_RECURRENCE_OR_USAGE_TARGET_FAILED')

    def test_nested_execution_failure_is_counted_once_per_call_with_location(self):
        failure={'exit_code':1,'output':'Traceback (most recent call last):\n  File "<stdin>", line 1\nSyntaxError: invalid syntax'}
        output=[{'type':'text','text':json.dumps({'value':failure})}]
        events=[{'type':'response_item','payload':{'type':'function_call_output','call_id':identity,'output':output}}
                for identity in ('a','a','b')]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'rollout.jsonl';p.write_text(''.join(json.dumps(e)+'\n' for e in events));r=run_usage.rollout(p)
        self.assertEqual(r['error_counts'],{'SyntaxError: invalid syntax':2});self.assertEqual(r['repeated_errors'],1)
        self.assertEqual(len(r['error_occurrences']),2);self.assertEqual(r['error_occurrences'][0]['line'],1)

    def test_successful_output_and_quoted_examples_are_not_failures(self):
        self.assertEqual(run_usage.execution_errors({'exit_code':0,'output':'Traceback (most recent call last):\nSyntaxError: example\n{"error":"sample"}'}),[])
        self.assertEqual(run_usage.execution_errors('Example:\n```\n{"error":"sample"}\nSyntaxError: example\n```'),[])
        self.assertEqual(run_usage.execution_errors({'exit_code':2,'output':'command failed'})[0]['signature'],'ProcessExit: 2')

    def test_daily_status_is_read_only_and_separates_usage_from_quality(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'defect.json';p.write_text(json.dumps({'status':'OPEN_RECURRENCE_OR_USAGE_TARGET_FAILED',
                'natural_acceptance':{'usage_checks':{'2030-03-02T00:00:00+09:00':{}},'reviewed_days':[]}}))
            original=p.read_bytes();result=run_usage.daily_status(p,'2030-03-02T03:00:00+09:00')
            self.assertEqual(result['day_jst'],'2030-03-01');self.assertFalse(result['usage_due'])
            self.assertTrue(result['quality_and_delivery_review_due']);self.assertEqual(p.read_bytes(),original)

    def test_native_execution_and_forwarded_output_are_one_failure(self):
        failure='SyntaxError: Non-UTF-8 code in file <stdin> on line 15'
        events=[{'type':'response_item','payload':{'type':'custom_tool_call','call_id':'outer','name':'functions.exec'}},
            {'type':'event_msg','payload':{'type':'item_completed','item':{'type':'CommandExecution','id':'exec-1',
                'status':'failed','exit_code':1,'aggregated_output':failure}}},
            {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'outer',
                'output':[{'type':'input_text','text':failure},{'type':'input_text','text':json.dumps({'exit_code':1,'output':failure})}]}}]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r';p.write_text(''.join(json.dumps(e)+'\n' for e in events));v=run_usage.rollout(p)
        self.assertEqual(v['error_counts'],{failure:1});self.assertEqual(v['repeated_errors'],0)
        self.assertEqual(v['error_occurrences'][0]['execution_id'],'exec-1')
        self.assertEqual(v['error_occurrences'][0]['exit_code'],1)
