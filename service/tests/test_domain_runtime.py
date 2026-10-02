"""Replay the actual command executor and existing analysis contracts in Docker."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from btc_node.catalog import Catalog
from btc_node.codec import encode, fingerprint
from btc_node.database import Database
from btc_node.execution import execute, recover
from btc_node.market_store import import_sqlite


class DomainRuntime(unittest.TestCase):
    def test_actual_prepare_observe_and_interrupted_result_recovery(self):
        source = Path(os.environ['BTC_TEST_SOURCE'])
        settings = json.loads(Path(os.environ['BTC_TEST_CONFIG']).read_text())
        self.assertTrue(settings.get('test_only'))
        with tempfile.TemporaryDirectory(prefix='btc-domain-contract-') as folder:
            folder = Path(folder)
            for name in ('资产配置.md','操作记录.md','纠正记录.md'):
                (folder/name).write_text('# SYNTHETIC TEST FACTS\nNo real account or position.\n')
            fixtures = folder/'fixtures'
            subprocess.run([sys.executable,str(source/'scripts/build_synthetic_fixtures.py'),'--output',str(fixtures)],check=True)
            node = folder/'node.json'
            node.write_text(json.dumps({'schema':'BTC_NODE_CONFIG_V1','project_dir':str(folder),
                'legacy_revision2_dir':str(fixtures),'python':sys.executable,'frozen_market_python':sys.executable}))
            sys.path[:0] = [str(source/'skill/btc-core-strategy/scripts'),str(source/'skill/btc-core-strategy/tests')]
            with patch.dict(os.environ, {'BTC_TEST_FIXTURE_DIR':str(fixtures),'BTC_NODE_CONFIG':str(node)}):
                import test_run_flow as fixture
                sample = fixture.RunFlowTests('test_prepare_never_calls_network_with_frozen_input_or_changes_owners')
                try:
                    sample.setUp()
                    # Reuse the domain's dated replay builder. Advance only the
                    # test clock, never relax production validation or fake a
                    # subprocess result; the real CLI still executes its body.
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
                                'runpy.run_path(p,run_name="__main__")')
                            command=[command[0],'-c',bootstrap,*command[1:]]
                        return actual_run(command,**kwargs)
                    launcher=patch('btc_node.execution.subprocess.run',side_effect=replay_process)
                    launcher.start();self.addCleanup(launcher.stop)
                    project = sample.f.project
                    runtime = folder/'releases'; release=runtime/'synthetic-release'
                    release.mkdir(parents=True)
                    shutil.copytree(source/'skill',release/'skill')
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
                    argv=['--project',str(project),'--root',str(sample.f.store),'run-prepare',
                          '--run-ref','SYNTHETIC_CONTAINER_RUN','--agent','SYNTHETIC_MAIN',
                          '--directory',str(sample.d),'--market',str(sample.raw_file)]
                    request={'method':'core.execute','operation_id':folder.name+'-prepare',
                             'params':{'release':str(release),'argv':argv}}
                    result=execute(settings,request)
                    self.assertEqual(result['returncode'],0,result['stderr'])
                    self.assertEqual(execute(settings,request),result)
                    prepared=json.loads(result['stdout'])
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
                            'run-advance','--run',prepared['run'],'--stage','observe','--input',str(answers)]}}
                    observed=execute(settings,observed_request)
                    self.assertEqual(observed['returncode'],0,observed['stderr'])
                    before=(sample.f.store/'sensing-current.json').read_bytes()
                    # Simulate a lost response after the durable EXECUTED journal,
                    # then reconcile state without rerunning an analysis or send.
                    broken={**request,'operation_id':folder.name+'-interrupted'}
                    with patch('btc_node.execution.project_state',side_effect=RuntimeError('injected post-execution failure')):
                        with self.assertRaises(RuntimeError):execute(settings,broken)
                    reconciled=recover(settings,broken['operation_id'])
                    self.assertEqual(reconciled['recovery'],'STATE_PRESERVED_NO_REPLAY')
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
                finally:
                    sample.doCleanups()


if __name__=='__main__':
    unittest.main()
