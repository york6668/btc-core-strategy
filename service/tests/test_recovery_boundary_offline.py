"""Exercise node command/replay disclosure without real business data or Docker."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from btc_node import api, execution
from btc_node.codec import encode, fingerprint
from test_operation_recovery_offline import Journal


class CommandJournal(Journal):
    @contextmanager
    def writer(self, **kwargs):
        with super().writer():
            yield

    def rows(self, sql, args=()):
        return [deepcopy(v) for v in self.operations.values() if v['status'] != 'COMMITTED']

    def execute(self, sql, args=()):
        if sql.startswith('INSERT INTO operations'):
            keys = ('operation_id', 'request_digest', 'status', 'started_ms', 'release_id')
            value = dict(zip(keys, args)); self.operations[value['operation_id']] = value
            return 1
        if sql.startswith('UPDATE operations'):
            finished, digest, identity = args
            self.operations[identity].update(status='COMMITTED', finished_ms=finished, result_digest=digest)
            return 1
        return super().execute(sql, args)


class NodeRecoveryBoundaryTests(unittest.TestCase):
    def test_real_dispatch_sanitizes_pinned_cli_and_committed_operation_without_replay(self):
        source = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); project = base / 'project'; project.mkdir()
            release = base / 'releases' / 'synthetic-pinned'; release.mkdir(parents=True)
            (release / 'RELEASE.json').write_text('{}')
            (release / 'skill').symlink_to(source / 'skill', target_is_directory=True)
            settings = {'project':str(project), 'release_root':str(release.parent),
                'journal':str(base/'journal'), 'observation_db':str(base/'unused.sqlite'),
                'container_node_config':str(base/'unused-node.json'), '_path':str(base/'unused-settings.json')}
            request = {'method':'core.execute', 'operation_id':'original-recover', 'params':{
                'release':str(release), 'argv':['--project',str(project),'--root',str(project/'.strategy/v4'),'recover']}}
            private = {'current':'old-current', 'report_reviews_not_enqueued':[{'reason':'PRIVATE_REPORT_REASON'}],
                       'report_care':{'active_maintenance':{'thesis':'PRIVATE_THESIS'}}}
            continuity = {'personal_context':'PRIVATE_PERSONAL_PATH', 'market_binding':{'digest':'old-formal'},
                          'public_context':'public-only-path'}
            db = CommandJournal()
            with patch.object(execution,'Database',return_value=db), \
                 patch.object(execution.Catalog,'sync',return_value={'immutable_files_reused':0}), \
                 patch.object(execution,'project_state',return_value=continuity), \
                 patch('btc_node.request_trace.run_child',return_value=subprocess.CompletedProcess([],0,json.dumps(private),'')) as child:
                result = api.dispatch(settings,request)
                self.assertNotIn('PRIVATE_', json.dumps(result))
                self.assertEqual('PUBLIC',json.loads(result['stdout'])['scope'])
                self.assertIsNone(result['continuity']['market_binding'])
                original_digest = db.operations['original-recover']['result_digest']
                original = deepcopy(db.value(original_digest))
                self.assertIn('PRIVATE_',json.dumps(original))
                self.assertEqual(result,api.dispatch(settings,request))
                self.assertEqual(1,child.call_count)
                recovered = api.dispatch(settings,{'method':'core.recover','params':{'operation_id':'original-recover'}})
                self.assertEqual('PUBLIC',recovered['scope'])
                self.assertNotIn('PRIVATE_',json.dumps(recovered))
                self.assertNotIn('stdout',recovered)
                self.assertEqual(original,db.value(original_digest))
                # Labels, a directory, and the old global binding cannot unlock a body.
                with self.assertRaises((ValueError,OSError,KeyError)):
                    api.dispatch(settings,{'method':'core.recover','params':{
                        'operation_id':'original-recover','run':str(project/'unprepared/run.json'),'run_ref':'new-run'}})

    def test_arbitrary_result_file_never_creates_personal_continuity(self):
        source = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            project=Path(folder); d=project/'run'; d.mkdir()
            (d/'run.json').write_text(json.dumps({'created_at':'2030-01-01T00:00:00Z','run_ref':'invented'}))
            (d/'market-result.json').write_text(json.dumps({'result_hash':'invented'}))
            settings={'project':str(project),'release_root':str(source.parent)}
            with patch.object(execution.Projection,'refresh',return_value={'market_binding':{'object_id':'old-global'}}), \
                 patch.object(execution,'full_context',return_value={'scope':'PUBLIC','objects':[]}), \
                 patch.object(execution,'boundary_methods',side_effect=ValueError('no genuine frozen run')):
                result=execution.project_state(CommandJournal(),settings,source,['--run',str(d/'run.json')])
            self.assertIsNone(result['market_binding'])
            self.assertNotIn('personal_context',result)
            self.assertFalse((d/'continuous-personal.json').exists())


if __name__ == '__main__':
    unittest.main()
