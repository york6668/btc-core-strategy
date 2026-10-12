"""Replay the actual command executor and existing analysis contracts in Docker."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from btc_node.catalog import Catalog
from btc_node.api import dispatch
from btc_node.codec import Conflict, encode, fingerprint
from btc_node.database import Database
from btc_node.execution import execute, recover, value_reference_only, help_only
from btc_node.market_store import import_sqlite


class DomainRuntime(unittest.TestCase):
    def test_reference_command_is_not_inferred_from_an_argument_value(self):
        self.assertTrue(value_reference_only(['--project','/p','--root','/p/.strategy/v4','--full','run-value-ref']))
        for argv in (['--project','run-value-ref','--root','/r','run-advance'],
                     ['--project','/p','--root','/r','run-advance','--input','run-value-ref'],
                     ['--project','/p','--root','/r','--unknown','run-value-ref'],
                     ['--project']):
            self.assertFalse(value_reference_only(argv))
        self.assertTrue(help_only(['--project','/p','--root','/p/.strategy/v4','freeze-evidence','--help']))
        for argv in (['--project','--help','--root','/r','run-advance','--input','file'],
                     ['--project','/p','--root','/r','run-advance','--input','--help'],
                     ['--project','/p','--root','/r','--unknown','--help']):
            self.assertFalse(help_only(argv))

    def test_expired_joint_decision_reaches_allocation_due_and_formal_plan_limit(self):
        from unittest import mock
        source=Path(os.environ['BTC_TEST_SOURCE'])
        sys.path[:0]=[str(source/'skill/btc-core-strategy/scripts')]
        from v4.common import facts
        from btc_node.projection import Projection
        from btc_node.codec import millis
        with tempfile.TemporaryDirectory(prefix='btc-expiry-projection-') as directory:
            project=Path(directory);root=project/'.strategy/v4'
            for name in ('资产配置.md','操作记录.md','纠正记录.md'):
                (project/name).write_text('SYNTHETIC CONFIRMED FACTS ONLY\n')
            version=facts(project)
            deadline='2030-03-01T12:00:00Z';checked='2030-03-05T00:00:00Z'
            whole={'valid_until':deadline,'evaluation_windows':[],'policy':{'version':'synthetic-policy'}}
            assessment={'revision':8,'fact_version':version,'recommendations':{
                'portfolio':{'investment_decision':whole},
                'upper_short':{'status':'RECOMMENDED','validity':{'until':'2030-03-08T00:00:00Z'}},
                'lower_spot':{'status':'RECOMMENDED','validity':{'until':'2030-03-08T00:00:00Z'}}}}
            original={'decision_id':'b'*64,'created_at':'2030-03-01T00:00:00Z','fact_version':version,'assessment':assessment}
            path=root/'versions'/original['decision_id']/'decision.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps(original))
            db=mock.Mock();db.put_blob.side_effect=fingerprint
            projection=Projection(db,'synthetic-candidate')
            projection.objects=mock.Mock()
            projection.objects.get.side_effect=lambda kind,object_id: {'known_ms':millis(checked)} if kind=='FACT_PROJECTION' else None
            projection.objects.list.return_value={'objects':[],'next':None}
            calls=[]
            def capture(kind,object_id,payload,source,known,**kwargs):
                calls.append((kind,object_id,payload,kwargs))
                return {'object_id':object_id,'version':1,'digest':fingerprint(encode(payload)),'known_ms':millis(known)}
            projection.put=capture
            public={'outlook':{'market_assessment':None,'dispute_zones':[]},'factor_assessment':{'items':[]}}
            with mock.patch('v4.storage.current',return_value=original), mock.patch('v4.market_state.public_content',return_value=public), \
                 mock.patch('v4.market_state.effective',return_value={}), mock.patch('v4.research.context',return_value={'issues':[]}), \
                 mock.patch('v4.delivery.attempt_recovery',return_value=[]), mock.patch('v4.delivery.recovery_actions',return_value=[]), \
                 mock.patch('btc_node.reader_prices.project',return_value={}), mock.patch('btc_node.history.project_history',return_value={}), \
                 mock.patch('btc_node.projection.stamp',return_value=checked):
                projection.refresh(project,root)
            allocation=next(c for c in calls if c[0]=='ALLOCATION')
            self.assertEqual(allocation[2]['recommendation_applicability'],'REVISE')
            self.assertIsNone(allocation[2]['portfolio'])
            self.assertEqual(allocation[3]['expires'],deadline)
            self.assertEqual(allocation[3]['due'],deadline)
            plans=[c for c in calls if c[0]=='PLAN']
            self.assertEqual(len(plans),2)
            for plan in plans:
                self.assertEqual(plan[3]['expires'],deadline)
                self.assertIsNone(plan[2]['recommendation'])
                self.assertEqual(plan[2]['recommendation_applicability'],'REVISE')
                self.assertEqual(plan[2]['formal_recommendation']['validity']['until'],'2030-03-08T00:00:00Z')

    def test_actual_prepare_observe_and_interrupted_result_recovery(self):
        source = Path(os.environ['BTC_TEST_SOURCE'])
        settings = json.loads(Path(os.environ['BTC_TEST_CONFIG']).read_text())
        self.assertTrue(settings.get('test_only'))
        with tempfile.TemporaryDirectory(prefix='btc-domain-contract-', dir=settings['objects']) as folder:
            folder = Path(folder)
            for name in ('资产配置.md','操作记录.md','纠正记录.md'):
                (folder/name).write_text('# SYNTHETIC TEST FACTS\nNo real account or position.\n')
            fixtures = folder/'fixtures'
            subprocess.run([sys.executable,str(source/'scripts/build_synthetic_fixtures.py'),'--output',str(fixtures)],check=True)
            node = folder/'node.json'
            node.write_text(json.dumps({'schema':'BTC_NODE_CONFIG_V1','project_dir':str(folder),
                'legacy_revision2_dir':str(fixtures),'python':sys.executable,'frozen_market_python':sys.executable}))
            sys.path[:0] = [str(source/'skill/btc-core-strategy/scripts'),str(source/'skill/btc-core-strategy/tests')]
            # Generated analysis directories use the prepared evidence mount,
            # just as the production project and CAS share one mount.
            with patch.object(tempfile, 'tempdir', settings['objects']), patch.dict(os.environ,
                    {'BTC_TEST_FIXTURE_DIR':str(fixtures),'BTC_NODE_CONFIG':str(node), 'TMPDIR':settings['objects']}):
                import test_run_flow as fixture
                sample = fixture.RunFlowTests('test_prepare_never_calls_network_with_frozen_input_or_changes_owners')
                try:
                    with patch('v4.storage.NEW_WRITE_REVISION',1):sample.setUp()
                    # Reuse the domain's dated replay builder. Advance only the
                    # test clock and original historical contract floor in
                    # the disposable test release; the real CLI executes its
                    # body and every original storage/recovery guard. Current9
                    # lifecycle writes are covered by the unmocked core suite.
                    from btc_node import codec
                    actual_utc = codec.utc
                    clock = patch.object(codec, 'utc', side_effect=lambda value=None:
                        actual_utc('2030-03-02T00:00:00+00:00') if value is None else actual_utc(value))
                    clock.start(); self.addCleanup(clock.stop)
                    actual_run = subprocess.run
                    def replay_process(command, **kwargs):
                        if len(command)>1 and command[1].endswith('/strategy_v4.py'):
                            bootstrap = ('import runpy,sys,time; time.time=lambda:1900000000; p=sys.argv.pop(1); '
                                'sys.argv[0]=p; sys.path.insert(0,str(__import__("pathlib").Path(p).parent)); '
                                'import v4.common as c; c.now=lambda:"2030-03-01T14:00:00+00:00"; '
                                'import v4.storage as historical; historical.NEW_WRITE_REVISION=1; '
                                'runpy.run_path(p,run_name="__main__")')
                            command=[command[0],'-c',bootstrap,*command[1:]]
                        return actual_run(command,**kwargs)
                    launcher=patch('btc_node.execution.subprocess.run',side_effect=replay_process)
                    launcher.start();self.addCleanup(launcher.stop)
                    project = sample.f.project
                    runtime = folder/'releases'; release=runtime/'synthetic-release'
                    release.mkdir(parents=True)
                    shutil.copytree(source/'skill',release/'skill',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
                    shutil.copytree(source/'project-methods',release/'project-methods')
                    (release/'RELEASE.json').write_text('{"synthetic":true}\n')
                    config=json.loads(node.read_text());config['project_dir']=str(project);node.write_text(json.dumps(config))
                    settings.update(project=str(project),release_root=str(runtime),container_node_config=str(node),
                        journal=str(folder/'journal'),observation_db=str(project/'.strategy/market_observation.sqlite3'))
                    service_config=folder/'service.json';settings['_path']=str(service_config)
                    service_config.write_text(json.dumps(settings))
                    db=Database(settings)
                    try:
                        with db.writer(),db.transaction():
                            # The fixture and this database are explicitly disposable.
                            for table in ('mo_completion_attempt','mo_baseline','mo_review','mo_retrieval','mo_evidence'):
                                db.execute('DELETE FROM '+table)
                            original=project/'.strategy/market_observation.sqlite3'
                            # The migrated production protocol already has a
                            # SQLite schema; build the same empty schema here.
                            import market_observation
                            with market_observation._connect(original):pass
                            import_sqlite(db,original)
                            Catalog(db).sync('strategy',project/'.strategy')
                            db.metadata('writer',{'owner':'DOCKER'},write=True)
                    finally:db.close()
                    timing=folder/'execution-window.json'
                    timing.write_text(json.dumps({'schema':'BTC_EXECUTION_WINDOW_V1','status':'UNAVAILABLE',
                        'run_ref':'SYNTHETIC_CONTAINER_RUN','reason':'Synthetic timing bridge transport; no real automation.'}))
                    argv=['--runtime-schedule',str(timing),'--project',str(project),'--root',str(sample.f.store),'run-prepare',
                          '--run-ref','SYNTHETIC_CONTAINER_RUN','--agent','SYNTHETIC_MAIN',
                          '--directory',os.path.relpath(sample.d,project),'--market',str(sample.raw_file)]
                    request={'method':'core.execute','operation_id':folder.name+'-prepare',
                             'params':{'release':str(release),'argv':argv,
                                       'input_hashes':{str(timing):fingerprint(timing.read_bytes())}}}
                    result=execute(settings,request)
                    self.assertEqual(result['returncode'],0,result['stderr'])
                    self.assertEqual(execute(settings,request),result)
                    prepared=json.loads(result['stdout'])
                    reference_request={'method':'core.execute','operation_id':folder.name+'-value-ref',
                        'params':{'release':str(release),'argv':['--project',str(project),'--root',str(sample.f.store),
                            'run-value-ref','--run',os.path.relpath(prepared['run'],project),
                            '--file',str(sample.d/'context.json'),'--pointer','/factor_context/evidence']}}
                    # The real pinned CLI checks the frozen run and computes the
                    # reference. It must not archive/project unchanged strategy
                    # state, even while a real reader checkpoint uses MySQL.
                    import sqlite3
                    reader=folder/'reader';reader.mkdir()
                    with sqlite3.connect(reader/'cursor.sqlite3') as cursor:
                        cursor.execute('CREATE TABLE cursor(next_offset INTEGER)')
                        cursor.execute('INSERT INTO cursor VALUES(43)')
                    settings['reader_data']=str(reader)
                    frozen_before={p:p.read_bytes() for p in sample.d.rglob('*') if p.is_file()}
                    actual_sync=Catalog.sync
                    def reader_sync(catalog,namespace,*args,**kwargs):
                        self.assertNotEqual(namespace,'strategy','scalar reference must not recatalog strategy')
                        return actual_sync(catalog,namespace,*args,**kwargs)
                    with patch('btc_node.execution.Catalog.sync',side_effect=reader_sync,autospec=True), \
                         patch('btc_node.execution.project_state',side_effect=AssertionError('scalar reference must not project')):
                        with ThreadPoolExecutor(max_workers=3) as workers:
                            references=[]
                            for index in range(6):
                                ref=deepcopy(reference_request);ref['operation_id']+='-'+str(index)
                                references.append(workers.submit(execute,settings,ref))
                            checkpoint=workers.submit(dispatch,settings,{'method':'reader.checkpoint'})
                            reference_result=references[0].result(timeout=30)
                            for future in references:
                                self.assertEqual(future.result(timeout=30),reference_result)
                            self.assertTrue(checkpoint.result(timeout=30)['checkpointed'])
                        replay=deepcopy(reference_request);replay['operation_id']+='-0'
                        self.assertEqual(execute(settings,replay),reference_result)
                        changed=deepcopy(replay);changed['params']['argv'][-1]='/changed'
                        with self.assertRaisesRegex(Conflict,'different inputs'):execute(settings,changed)
                        # A lost response after EXECUTED must reconcile the
                        # saved result without replay or full-state projection.
                        from btc_node import execution
                        actual_atomic=execution.atomic
                        def interrupt(path,value):
                            actual_atomic(path,value)
                            if value.get('status')=='EXECUTED':raise RuntimeError('lost scalar response')
                        lost=deepcopy(reference_request);lost['operation_id']+='-lost'
                        with patch('btc_node.execution.atomic',side_effect=interrupt):
                            with self.assertRaisesRegex(RuntimeError,'lost scalar'):execute(settings,lost)
                        with patch('btc_node.execution.subprocess.run',side_effect=AssertionError('must not replay')):
                            restored=recover(settings,lost['operation_id'])
                        self.assertEqual(restored['stdout'],reference_result['stdout'])
                        self.assertEqual(restored['recovery'],'STATE_PRESERVED_NO_REPLAY')
                        self.assertEqual(execute(settings,lost),restored)
                        bad=deepcopy(reference_request);bad['operation_id']+='-bad'
                        bad['params']['argv'][-1]='/missing'
                        self.assertNotEqual(execute(settings,bad)['returncode'],0)
                    self.assertEqual({p:p.read_bytes() for p in sample.d.rglob('*') if p.is_file()},frozen_before)
                    help_request=deepcopy(reference_request)
                    help_request['operation_id']=folder.name+'-help'
                    help_request['params']['argv']=['--project',str(project),'--root',str(sample.f.store),
                                                   'freeze-evidence','--help']
                    with patch('btc_node.execution.Catalog.sync',side_effect=AssertionError('help must not archive')), \
                         patch('btc_node.execution.project_state',side_effect=AssertionError('help must not project')):
                        help_result=execute(settings,help_request)
                        self.assertEqual(help_result['returncode'],0,help_result['stderr'])
                        self.assertIn('usage:',help_result['stdout'])
                        self.assertNotIn('continuity',help_result)
                        self.assertEqual(execute(settings,help_request),help_result)
                        lost_help=deepcopy(help_request);lost_help['operation_id']+='-lost'
                        with patch('btc_node.execution.atomic',side_effect=interrupt):
                            with self.assertRaisesRegex(RuntimeError,'lost scalar'):execute(settings,lost_help)
                        with patch('btc_node.execution.subprocess.run',side_effect=AssertionError('must not replay')):
                            restored_help=recover(settings,lost_help['operation_id'])
                        self.assertEqual(restored_help['stdout'],help_result['stdout'])
                        self.assertEqual(restored_help['recovery'],'STATE_PRESERVED_NO_REPLAY')
                    self.assertEqual({p:p.read_bytes() for p in sample.d.rglob('*') if p.is_file()},frozen_before)
                    self.assertNotIn('continuity',reference_result)
                    self.assertEqual(reference_result['returncode'],0,reference_result['stderr'])
                    from v4.common import digest
                    expected=digest(json.loads((sample.d/'context.json').read_text())['factor_context']['evidence'])
                    self.assertEqual(json.loads(reference_result['stdout'])['$value_ref']['value_hash'],expected)
                    context=json.loads(Path(result['continuity']['public_context']).read_bytes())
                    self.assertTrue(context['objects'])
                    for entry in context['objects']:
                        self.assertNotIn('payload',entry)
                        raw=Path(entry['content_path']).read_bytes()
                        self.assertEqual(fingerprint(raw),entry['digest'])
                        self.assertIn('payload',json.loads(raw))
                    answers=sample.file(sample.authored(prepared))
                    observed_request={'method':'core.execute','operation_id':folder.name+'-observe',
                        'params':{'release':str(release),'argv':['--project',str(project),'--root',str(sample.f.store),
                            'run-advance','--run',os.path.relpath(prepared['run'],project),'--stage','observe','--input',str(answers)]}}
                    observed=execute(settings,observed_request)
                    self.assertEqual(observed['returncode'],0,observed['stderr'])
                    # A later database judgement must not become evidence for
                    # an older prepared round, including through real CLI reads.
                    from btc_node.entities import Entities
                    frozen_public=Path(result['continuity']['public_context']).read_bytes()
                    db=Database(settings)
                    try:
                        with db.writer(),db.transaction():
                            future_id=folder.name+'-later-public'
                            Entities(db).put({'kind':'CYCLE','object_id':future_id,'scope':'PUBLIC',
                                'observed_at':'2030-03-01T14:01:00Z','known_at':'2030-03-01T14:10:00Z',
                                'method_version':'SYNTHETIC_ONLY','source_ref':'generated-later-evidence',
                                'change_reason':'Later information must not enter the original round',
                                'payload':{'direction':'BEARISH','synthetic':True}},expected_version=0)
                    finally:db.close()
                    material_request={'method':'core.execute','operation_id':folder.name+'-old-round-material',
                        'params':{'release':str(release),'argv':['--project',str(project),'--root',str(sample.f.store),
                            'run-material','--run',prepared['run']]}}
                    material=execute(settings,material_request)
                    self.assertEqual(material['returncode'],0,material['stderr'])
                    self.assertEqual(Path(material['continuity']['public_context']).read_bytes(),frozen_public)
                    self.assertNotIn(future_id,{v['object_id'] for v in json.loads(frozen_public)['objects']})
                    self.assertEqual(material['component_binding']['core_release'],release.name)
                    self.assertEqual(set(material['component_binding']['service_sources']),
                        {'execution.py','projection.py','entities.py','series.py','history.py'})
                    print('BTC_OLD_ROUND_FROZEN_PUBLIC='+json.dumps({'later_object_withheld':True,
                        'snapshot_unchanged':True,'real_cli_returncode':0,'component_receipt':True}),flush=True)
                    before=(sample.f.store/'sensing-current.json').read_bytes()
                    # Simulate a lost response after the durable EXECUTED journal,
                    # then reconcile state without rerunning an analysis or send.
                    broken={**request,'operation_id':folder.name+'-interrupted'}
                    with patch('btc_node.execution.project_state',side_effect=RuntimeError('injected post-execution failure')):
                        with self.assertRaises(RuntimeError):execute(settings,broken)
                    reconciled=recover(settings,broken['operation_id'])
                    self.assertEqual(reconciled['recovery'],'STATE_PRESERVED_NO_REPLAY')
                    self.assertEqual(Path(reconciled['continuity']['public_context']).read_bytes(),frozen_public)
                    self.assertEqual(reconciled['component_binding'],result['component_binding'])
                    self.assertIn('recovery_component_binding',reconciled)
                    self.assertEqual((sample.f.store/'sensing-current.json').read_bytes(),before)
                    self.assertEqual(execute(settings,broken),reconciled)
                    # Resume an immutable pre-Docker release. Its real SQLite
                    # consumer must see the newest MySQL observation, and the
                    # native protocol state must survive the round trip.
                    legacy=runtime/'synthetic-pre-docker';shutil.copytree(release,legacy)
                    adapter=legacy/'skill/btc-core-strategy/scripts/market_observation.py'
                    text=adapter.read_text()
                    begin=text.index("    import os\n",text.index('def _connect('))
                    end=text.index('    path = Path(db_path)',begin)
                    adapter.write_text(text[:begin]+text[end:])
                    self.assertNotIn('connect_if_configured',adapter.read_text())
                    resumed={'method':'core.execute','operation_id':folder.name+'-old-release',
                        'params':{'release':str(legacy),'argv':['--project',str(project),'--root',str(sample.f.store),
                            'run-material','--run',prepared['run']]}}
                    old_result=execute(settings,resumed)
                    self.assertEqual(old_result['returncode'],0,old_result['stderr'])
                    self.assertEqual((sample.f.store/'sensing-current.json').read_bytes(),before)
                    import sqlite3
                    with sqlite3.connect(original) as copied:
                        latest=copied.execute('SELECT COUNT(*) FROM review').fetchone()[0]
                    db=Database(settings)
                    try:self.assertEqual(latest,db.one('SELECT COUNT(*) AS n FROM mo_review')['n'])
                    finally:db.close()
                    # The real service wrapper must commit public work even if
                    # personal source files cannot be read. The isolated core
                    # drops DAC override/read-search, so uid 0 cannot bypass 000.
                    from btc_node.entities import Entities
                    for mode, name in [('missing', '资产配置.md'), ('unreadable', '操作记录.md')]:
                        path=project/name
                        original_bytes=path.read_bytes()
                        original_mode=path.stat().st_mode & 0o777
                        if mode=='missing':
                            path.unlink()
                            with self.assertRaises(FileNotFoundError):path.read_bytes()
                        else:
                            path.chmod(0)
                            with self.assertRaises(PermissionError):path.read_bytes()
                        probe=deepcopy(request)
                        probe['operation_id']=folder.name+'-'+mode
                        probe['params']['argv'][probe['params']['argv'].index('--directory')+1]=str(sample.d.parent/(sample.d.name+'-'+mode))
                        try:
                            committed=execute(settings,probe)
                            self.assertEqual(committed['returncode'],0,committed['stderr'])
                            self.assertEqual(execute(settings,probe),committed)
                            db=Database(settings)
                            try:
                                fact=Entities(db).get('FACT_PROJECTION',name)
                                self.assertIsNone(fact['payload']['file_hash'])
                                self.assertEqual(fact['payload']['authority'],'UNAVAILABLE_LOCAL_FILE')
                                self.assertEqual(fact['payload']['availability'],{'status':'UNAVAILABLE',
                                    'reason':'FileNotFoundError' if mode=='missing' else 'PermissionError'})
                                self.assertIsNone(fact['payload']['structured_view'])
                                self.assertIn('AVAILABILITY_CHECK',{d['relation'] for d in fact['dependencies']})
                                portfolio=Entities(db).get('ALLOCATION','portfolio')['payload']
                                self.assertIn('investment_decision',portfolio)
                                self.assertIn('investment_feedback',portfolio)
                                if portfolio['investment_feedback'] is not None:
                                    self.assertEqual(portfolio['investment_feedback']['actual_performance'],'REQUIRES_COMPLETE_CONFIRMED_LEDGER')
                                    self.assertIn('available_method_validations',portfolio['investment_feedback'])
                                self.assertEqual(portfolio['fact_review'],'REQUIRED')
                                self.assertEqual(portfolio['recommendation_applicability'],'EVIDENCE_LIMITED')
                                self.assertIsNone(portfolio['portfolio_analysis'])
                                self.assertIsNone(portfolio['portfolio'])
                                self.assertEqual(db.one('SELECT status FROM operations WHERE operation_id=%s',
                                    (probe['operation_id'],))['status'],'COMMITTED')
                            finally:db.close()
                            public=json.loads(Path(committed['continuity']['public_context']).read_bytes())
                            self.assertTrue(public['objects'])
                            self.assertTrue(all(v['scope']=='PUBLIC' for v in public['objects']))
                            print('BTC_FACT_WRAPPER_FIXED='+json.dumps({'case':mode,
                                'actual_read_denied':True,'core_returncode':0,'wrapper_committed':True,
                                'personal_value_unknown':True,'stale_current_portfolio_withheld':True}),flush=True)
                        finally:
                            path.chmod(0o600) if path.exists() else None
                            path.write_bytes(original_bytes)
                            path.chmod(original_mode)
                finally:
                    sample.doCleanups()


if __name__=='__main__':
    unittest.main()
