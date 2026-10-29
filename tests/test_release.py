"""Version switching must not reset state, research or delivery recovery."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import plistlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('btc_release', ROOT / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ExportManifestTests(unittest.TestCase):
    def test_strategy_help_does_not_load_node_configuration_or_forward(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, BTC_NODE_CONFIG=str(Path(directory) / 'absent-node.json'))
            environment.pop('BTC_IN_CONTAINER', None)
            for arguments in (['--help'], ['--project', directory, '--root', directory,
                                          'report-care', '--help']):
                result = subprocess.run([sys.executable, '-B',
                    str(ROOT / 'skill/btc-core-strategy/scripts/strategy_v4.py'), *arguments],
                    env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_exported_validation_entrypoint_imports_without_repository_fallback(self):
        files = json.loads((ROOT / 'export-manifest.json').read_text())['files']
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in files:
                output = target / name
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, output)
            result = subprocess.run([sys.executable, '-E', '-s', '-B',
                                     str(target / 'service/check.py'), '--help'],
                                    cwd=target, check=True, capture_output=True, text=True)
            self.assertIn('--reader-image', result.stdout)

    def test_node_launchagent_template_is_disabled_and_has_one_calendar_wake(self):
        template = plistlib.loads((ROOT / 'config/local.btc.node-scheduler.plist.example').read_bytes())
        self.assertEqual('local.btc.node-scheduler', template['Label'])
        self.assertTrue(template['Disabled'])
        self.assertEqual('Aqua', template['LimitLoadToSessionType'])
        self.assertTrue(template['RunAtLoad'])
        self.assertEqual({}, template['StartCalendarInterval'])
        self.assertNotIn('StartInterval', template)
        self.assertNotIn('KeepAlive', template)
        self.assertEqual(63, template['Umask'])
        self.assertEqual(1, sum('node_scheduler_worker.py' in arg
                                for arg in template['ProgramArguments']))

    def test_prepared_node_keeps_strategy_and_cas_on_one_project_mount(self):
        spec = importlib.util.spec_from_file_location('btc_setup', ROOT/'service/setup_node.py')
        setup = importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();project=root/'project';project.mkdir()
            for name in ('资产配置.md','操作记录.md','纠正记录.md'):
                (project/name).write_text('# SYNTHETIC FACTS\n',encoding='utf-8')
            node=root/'node.json';node.write_text(json.dumps({'schema':'BTC_NODE_CONFIG_V1',
                'project_dir':str(project),'release_root':str(root/'runtime'),
                'reader_code_dir':str(root/'reader-code'),'reader_data_dir':str(root/'reader-data'),
                'observation_db':str(project/'.strategy/market.sqlite3')}))
            data=root/'data';setup.prepare(node,data,Path('/synthetic/docker'),'synthetic',ROOT)
            settings=json.loads((data/'settings.json').read_text())
            self.assertEqual(settings['objects'],str(project))
            self.assertTrue(settings['require_shared_evidence'])
            self.assertEqual(settings['compatibility_root'],str(data/'objects/.strategy'))
            self.assertEqual(settings['backup_directory'],str(project/'.backups'))
            volumes=json.loads((data/'compose.active.json').read_text())['services']['core']['volumes']
            self.assertIn(str(data/'objects')+':'+str(project),volumes)
            self.assertFalse(any(v.split(':')[1]==str(project/'.strategy') for v in volumes))
            self.assertTrue((data/'objects/.strategy').is_dir())
            self.assertTrue(all((data/'objects'/name).is_symlink() for name in ('资产配置.md','操作记录.md','纠正记录.md')))

    def test_service_export_includes_every_source_module(self):
        files = set(json.loads((ROOT / 'export-manifest.json').read_text())['files'])
        modules = {path.relative_to(ROOT).as_posix()
                   for path in (ROOT / 'service/btc_node').glob('*.py')}
        self.assertTrue(modules)
        self.assertEqual(sorted(modules - files), [], 'service source modules missing from export')

    def test_exported_failure_receipt_module_imports(self):
        files = json.loads((ROOT / 'export-manifest.json').read_text())['files']
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in files:
                if name.startswith('service/btc_node/') and name.endswith('.py'):
                    output = target / name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / name, output)
            subprocess.run([sys.executable, '-I', '-B', '-c',
                'import sys; sys.path.insert(0, sys.argv[1]); '
                'from btc_node.failures import is_receipt; assert callable(is_receipt)',
                str(target / 'service')], check=True, capture_output=True, text=True)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / 'data'; self.project.mkdir()
        self.state = self.project / 'UNKNOWN-delivery.json'
        self.state.write_text('{"attempt":"existing","status":"UNKNOWN"}\n')
        self.before = self.state.read_bytes()
        self.config = {'schema': 'BTC_NODE_CONFIG_V1', 'project_dir': str(self.project),
                       'release_root': str(self.base / 'runtime'), 'python': sys.executable,
                       '_path': str(self.base / 'node.json')}
        reader = self.base / 'reader'; reader.mkdir()
        (reader / 'reader.py').write_text('# synthetic installed reader\n')
        self.config['reader_code_dir'] = str(reader)
        self.source = self.base / 'source'; self.source.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.git('config', 'user.name', 'Synthetic release test')
        (self.source / 'scripts').mkdir()
        (self.source / 'scripts/run_tests.py').write_text(
            'import sys\nfrom pathlib import Path\n'
            'assert sys.argv[1] == "--reader" and (Path(sys.argv[2]) / "reader.py").is_file()\n')
        entries = ['scripts/release.py', 'skill/btc-core-strategy/SKILL.md',
                   'project-methods/AGENTS.md', 'project-methods/价值因子.md', 'tasks/hourly.md']
        for name in entries:
            path = self.source / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Synthetic release entry\n', encoding='utf-8')
        (self.source / 'export-manifest.json').write_text(json.dumps({'files': ['scripts/run_tests.py', *entries]}))
        self.first = self.commit('first')
        release.install(self.config, self.source)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], text=True, stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git('add', '.'); self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def test_missing_method_stops_entry_without_creating_an_active_run(self):
        root = Path(self.config['release_root'])
        installed = release.version_dir(root, self.first)
        for name in ('skill/btc-core-strategy/SKILL.md', 'project-methods/价值因子.md', 'tasks/hourly.md'):
            with self.subTest(name=name):
                path = installed / name; content = path.read_bytes(); path.unlink()
                try:
                    with self.assertRaisesRegex(ValueError, 'release source'):
                        release.enter(self.config, 'missing-method')
                    self.assertFalse(release.record_path(root, 'runs', 'missing-method').exists())
                    with self.assertRaisesRegex(ValueError, 'release source'):
                        release.activate(self.config, self.first)
                finally:
                    path.write_bytes(content)
        self.assertEqual(release.enter(self.config, 'restored-method')['commit'], self.first)
        self.assertEqual(self.state.read_bytes(), self.before)

    def test_duplicate_finished_trigger_preserves_terminal_record_and_original_version(self):
        root = Path(self.config['release_root'])
        first = release.enter(self.config, 'same-trigger')
        self.assertEqual(first['entry_action'], 'START_NEW_RUN')
        self.assertEqual(Path(first['task']).read_text(), '# Synthetic release entry\n')
        self.assertEqual(release.enter(self.config, 'same-trigger')['entry_action'], 'RESUME_ORIGINAL_RUN')
        done = release.finish(self.config, 'same-trigger')
        path = release.record_path(root, 'runs', 'same-trigger')
        original = path.read_bytes()
        (self.source / 'change.txt').write_text('next version')
        second = self.commit('second'); release.install(self.config, self.source)
        repeated = release.enter(self.config, 'same-trigger')
        self.assertEqual(repeated['status'], 'FINISHED')
        self.assertEqual(repeated['entry_action'], 'RETURN_FINISHED_RUN')
        self.assertEqual(repeated['commit'], self.first)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(release.finish(self.config, 'same-trigger'), done)
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'active pinned run'):
            release.bind(self.config, 'same-trigger', 'no-new-context-after-completion')
        self.assertEqual(release.status(self.config)['active_runs'], [])
        self.assertEqual(release.status(self.config)['current'], str(release.version_dir(root, second).resolve()))
        self.assertEqual(self.state.read_bytes(), self.before)

    def test_status_discovers_original_active_material_without_starting_or_rebinding(self):
        root = Path(self.config['release_root'])
        release.enter(self.config, 'interrupted')
        release.bind(self.config, 'interrupted', 'unknown-attempt')
        material = self.project / '.strategy/v4/runs/interrupted/run.json'
        material.parent.mkdir(parents=True)
        payload = {'schema': 'BTC_RUN_MATERIAL_V1', 'run_ref': 'interrupted',
                   'directory': str(material.parent), 'root': str(material.parents[2]),
                   'project': str(self.project)}
        material.write_text(json.dumps(payload))
        path = release.record_path(root, 'runs', 'interrupted')
        original = path.read_bytes(); context = release.record_path(root, 'contexts', 'unknown-attempt').read_bytes()
        got = release.status(self.config)
        self.assertEqual(len(got['active_runs']), 1)
        row = got['active_runs'][0]
        self.assertEqual(row['run_id'], 'interrupted')
        self.assertEqual(row['commit'], self.first)
        self.assertEqual(row['material_status'], 'AVAILABLE')
        self.assertEqual(row['run_material'], str(material))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(release.record_path(root, 'contexts', 'unknown-attempt').read_bytes(), context)
        self.assertEqual(self.state.read_bytes(), self.before)
        payload['run_ref'] = 'different-execution'; material.write_text(json.dumps(payload))
        mismatched = release.status(self.config)['active_runs'][0]
        self.assertEqual(mismatched['material_status'], 'IDENTITY_MISMATCH_REQUIRES_REVIEW')
        self.assertIsNone(mismatched['run_material'])
        foreign = self.base / 'foreign.txt'; foreign.write_text('Not a run and not JSON')
        material.unlink(); material.symlink_to(foreign)
        self.assertEqual(release.status(self.config)['active_runs'][0]['material_status'],
                         'IDENTITY_MISMATCH_REQUIRES_REVIEW')

    def test_legacy_finished_record_is_not_reopened_or_given_a_fabricated_finish_time(self):
        root = Path(self.config['release_root'])
        run = release.enter(self.config, 'legacy-finished')
        path = release.record_path(root, 'runs', 'legacy-finished')
        run.pop('entry_action'); run['status'] = 'FINISHED'
        release.write(path, run)
        original = path.read_bytes()
        self.assertIsNone(release.finish(self.config, 'legacy-finished')['finished_at'])
        self.assertEqual(release.enter(self.config, 'legacy-finished')['status'], 'FINISHED')
        self.assertEqual(path.read_bytes(), original)

    def test_status_preserves_logical_material_path_for_native_and_container_consumers(self):
        from v4 import evidence, run_flow
        from v4.common import Invalid, digest
        self.project = self.project.resolve()
        self.config['project_dir'] = str(self.project)
        release.enter(self.config, 'mounted-run')
        state = self.base.resolve() / 'objects/.strategy'; state.mkdir(parents=True)
        alias = self.project / '.strategy'; alias.symlink_to(state)
        directory = alias / 'v4/runs/mounted-run'; directory.mkdir(parents=True)
        raw = directory / 'market.json'; raw.write_text('{}\n')
        stamp = '2020-01-01T00:00:00Z'
        bundle = evidence.freeze({'parent_id': None, 'cutoff': stamp,
            'sources': [{'id': 'market', 'origin': 'BINANCE', 'url': 'https://data-api.binance.vision/api/v3/klines',
                         'trust': 'PUBLIC_RAW', 'published_at': None, 'retrieved_at': stamp,
                         'start': stamp, 'end': stamp, 'raw_hash': digest(raw.read_bytes())}],
            'measurements': [], 'verified_facts': [], 'candidates': [], 'gaps': [],
            'handoff': {'previous_hour': None, 'successful_deep': None, 'changes_since_hour': [],
                        'changes_since_deep': [], 'active_hypotheses': [], 'open_questions': []}},
            {'market': raw}, directory / 'evidence')
        payload = {'schema': 'BTC_RUN_MATERIAL_V1', 'run_ref': 'mounted-run',
                   'project': str(self.project), 'root': str(alias / 'v4'), 'directory': str(directory),
                   'market': str(raw), 'market_hash': digest({}),
                   'evidence': str(directory / 'evidence'), 'evidence_id': bundle['evidence_id']}
        material = directory / 'run.json'; release.write(material, payload)
        run_record = release.record_path(Path(self.config['release_root']), 'runs', 'mounted-run')
        original = run_record.read_bytes(); material_bytes = material.read_bytes()
        row = release.status(self.config)['active_runs'][0]
        self.assertEqual(row['material_status'], 'AVAILABLE')
        self.assertEqual(row['run_material'], str(material))
        self.assertEqual(run_flow.read_run(row['run_material'], alias / 'v4', self.project,
                                          resolve_aliases=True), payload)
        # The node mounts the same state at the logical project path. Preserve
        # its stored identity rather than handing the container a host alias.
        alias.unlink(); shutil.copytree(state, alias)
        self.assertEqual(run_flow.read_run(row['run_material'], alias / 'v4', self.project), payload)
        with self.assertRaisesRegex(Invalid, 'run directory binding differs'):
            run_flow.read_run(state / 'v4/runs/mounted-run/run.json', alias / 'v4', self.project)
        self.assertEqual(run_record.read_bytes(), original)
        self.assertEqual(material.read_bytes(), material_bytes)
        self.assertEqual(self.state.read_bytes(), self.before)

    def test_repeat_activation_preserves_rollback_and_pending_upgrade(self):
        root = Path(self.config['release_root'])
        (self.source / 'change.txt').write_text('second')
        second = self.commit('second'); release.install(self.config, self.source)
        before = (root / 'activation.json').read_bytes()
        release.enter(self.config, 'running-second')
        (self.source / 'change.txt').write_text('third')
        third = self.commit('third'); release.install(self.config, self.source)
        pending = (root / 'pending.json').read_bytes()
        result = release.activate(self.config, second)
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertEqual(result['previous'], self.first)
        self.assertEqual((root / 'activation.json').read_bytes(), before)
        self.assertEqual((root / 'pending.json').read_bytes(), pending)
        release.finish(self.config, 'running-second')
        release.activate(self.config, third)
        self.assertEqual(release.read(root / 'activation.json')['previous'], second)

    def test_complete_release_can_replace_damaged_current_without_repairing_history(self):
        root = Path(self.config['release_root'])
        (self.source / 'change.txt').write_text('replacement')
        replacement = self.commit('replacement')
        release.install(self.config, self.source, activate_after=False)
        missing = release.version_dir(root, self.first) / 'project-methods/价值因子.md'
        missing.unlink()
        result = release.activate(self.config, replacement)
        self.assertEqual(result['previous'], self.first)
        self.assertEqual(release.enter(self.config, 'replacement')['commit'], replacement)
        self.assertFalse(missing.exists())
        self.assertEqual(self.state.read_bytes(), self.before)

    def test_update_waits_for_run_gap_and_resumes_unknown_on_original_version(self):
        original = release.enter(self.config, 'run-a')
        release.bind(self.config, 'run-a', 'unknown-attempt')
        (self.source / 'change.txt').write_text('new code')
        second = self.commit('second')
        update = release.install(self.config, self.source)
        self.assertEqual(update['status'], 'WAITING_FOR_RUN_GAP')
        self.assertEqual(release.current(Path(self.config['release_root'])).name, self.first)
        self.assertEqual(release.enter(self.config, 'run-a')['commit'], original['commit'])
        release.finish(self.config, 'run-a')
        release.activate(self.config, second)
        resumed = release.enter(self.config, 'run-b', 'unknown-attempt')
        self.assertEqual(resumed['commit'], self.first)
        self.assertEqual(release.bind(self.config, 'run-b', 'unknown-attempt')['commit'], self.first)
        self.assertEqual(self.state.read_bytes(), self.before)

    def test_failed_install_and_code_rollback_preserve_newer_data(self):
        (self.source / 'scripts/run_tests.py').write_text('raise SystemExit(1)\n')
        broken = self.commit('broken')
        with self.assertRaises(subprocess.CalledProcessError):
            release.install(self.config, self.source)
        self.assertEqual(release.current(Path(self.config['release_root'])).name, self.first)
        self.assertFalse(release.version_dir(Path(self.config['release_root']), broken).exists())
        (self.source / 'scripts/run_tests.py').write_text('raise SystemExit(0)\n# changed\n')
        second = self.commit('fixed'); release.install(self.config, self.source)
        self.state.write_text('{"attempt":"existing","status":"CONFIRMED"}\n')
        after = self.state.read_bytes()
        release.activate(self.config, self.first)
        self.assertEqual(self.state.read_bytes(), after)
        self.assertEqual(release.current(Path(self.config['release_root'])).name, self.first)

    def test_context_is_never_rebound_by_new_code(self):
        release.enter(self.config, 'original'); release.bind(self.config, 'original', 'research-request')
        release.finish(self.config, 'original')
        (self.source / 'change.txt').write_text('new code')
        self.commit('second'); release.install(self.config, self.source)
        release.enter(self.config, 'new')
        with self.assertRaisesRegex(ValueError, 'different version'):
            release.bind(self.config, 'new', 'research-request')

    def test_explicit_research_repair_preserves_origin_and_existing_runs(self):
        request = {'schema':'BTC_RESEARCH_REQUEST_V4','workflow_revision':6,'issue_id':'synthetic'}
        raw = (json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
        context = 'sha256:'+hashlib.sha256(raw).hexdigest()
        request['request_id'] = context
        path = self.project/'.strategy/v4/research/requests'/(context[7:]+'.json')
        release.write(path,request); before = path.read_bytes()
        release.enter(self.config,'origin'); release.bind(self.config,'origin',context)
        release.finish(self.config,'origin')
        release.enter(self.config,'old-recovery',context)
        manifest = release.read(self.source / 'export-manifest.json')
        manifest['content_contract'] = {'read_revisions':[1,2,3,4,5,6]}
        (self.source/'export-manifest.json').write_text(json.dumps(manifest))
        target=self.commit('compatible repair');release.install(self.config,self.source,activate_after=False)
        with self.assertRaisesRegex(ValueError,'active recovery'):
            release.repair_context(self.config,context,self.first,target,'fix revision forwarding')
        release.finish(self.config,'old-recovery')
        repaired=release.repair_context(self.config,context,self.first,target,'fix revision forwarding')
        self.assertEqual(repaired['commit'],self.first)
        self.assertEqual(release.repair_context(self.config,context,self.first,target,'same retry'),repaired)
        self.assertEqual(release.enter(self.config,'old-recovery',context)['commit'],self.first)
        resumed=release.enter(self.config,'new-recovery',context)
        self.assertEqual(resumed['commit'],target)
        self.assertEqual(release.bind(self.config,'new-recovery',context),repaired)
        with self.assertRaisesRegex(ValueError,'version changed'):
            release.repair_context(self.config,context,'f'*40,target,'stale repair')
        with self.assertRaisesRegex(ValueError,'cannot read'):
            release.repair_context(self.config,context,target,self.first,'incompatible target')
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.state.read_bytes(),self.before)

    def test_repair_rejects_delivery_context_and_incompatible_content(self):
        context='sha256:'+'d'*64
        release.enter(self.config,'run');release.bind(self.config,'run',context)
        with self.assertRaisesRegex(ValueError,'stored research'):
            release.repair_context(self.config,context,self.first,self.first,'not a research request')

    def test_native_adapter_bootstraps_enter_before_pinned_environment_exists(self):
        node=Path(self.config['_path'])
        node.write_text(json.dumps({**self.config,'native_python':sys.executable}))
        environment=dict(os.environ)
        environment.pop('BTC_NODE_CONFIG',None)
        output=subprocess.check_output([sys.executable,str(ROOT/'service/host_python.py'),
            str(ROOT/'scripts/release.py'),'--node-config',str(node),'enter','--run-id','adapter-entry'],
            env=environment,text=True)
        self.assertEqual(json.loads(output)['commit'],self.first)
        self.assertEqual(self.state.read_bytes(),self.before)

    def test_native_log_rotation_preserves_live_writer_and_other_project(self):
        module_spec=importlib.util.spec_from_file_location('btc_test_capacity',ROOT/'service/node_capacity.py')
        capacity=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(capacity)
        owned=self.base/'reader.log';owned.write_text('previous run\n')
        other=self.base/'other-project.log';other.write_text('keep\n')
        entry={'path':str(owned),'launchd_label':'test-reader','max_bytes':2,'keep_files':2}
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='state = running\n')):
            self.assertFalse(capacity.native_logs([entry])[0]['rotated'])
        self.assertEqual(owned.read_text(),'previous run\n')
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='state = not running\n')):
            self.assertTrue(capacity.native_logs([entry])[0]['rotated'])
            self.assertEqual(owned.read_text(),'')
            self.assertEqual(Path(str(owned)+'.1').read_text(),'previous run\n')
            owned.write_text('next run\n');capacity.native_logs([entry])
            self.assertEqual(Path(str(owned)+'.2').read_text(),'previous run\n')
            self.assertEqual(Path(str(owned)+'.1').read_text(),'next run\n')
        self.assertEqual(other.read_text(),'keep\n')
        with patch('os.scandir',side_effect=PermissionError('generated denied directory')):
            with self.assertRaises(PermissionError):capacity.allocated([self.base])
        first=self.base/'capacity-first'; first.write_bytes(b'first')
        alias=self.base/'capacity-alias';os.link(first,alias)
        second=self.base/'capacity-second';second.write_bytes(b'second')
        with patch.object(capacity,'MAX_INODE_KEYS',2):
            expected=first.stat().st_blocks*512+second.stat().st_blocks*512
            self.assertEqual(capacity.allocated([first,alias,second,first]),expected)

    def test_native_transport_preserves_template_binding_and_freezes_authored_answers(self):
        module_spec=importlib.util.spec_from_file_location('btc_test_bridge',ROOT/'skill/btc-core-strategy/scripts/node_bridge.py')
        bridge=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(bridge)
        for name in ('资产配置.md','操作记录.md','纠正记录.md'):(self.project/name).write_text('SYNTHETIC UNKNOWN\n')
        state=self.base/'compatibility';state.mkdir();(self.project/'.strategy').symlink_to(state)
        template=self.project/'.strategy/template.json';template.write_text('{"binding":"original"}\n')
        answers=self.base/'answers.json';answers.write_text('{"judgement":"synthetic"}\n')
        config={**self.config,'docker_service':{'enabled':True,'binary':'docker','project':'synthetic',
            'env_file':'synthetic.env','compose_file':'synthetic.yaml','fact_bridge_dir':str(self.base/'facts')}}
        sent=[]
        def capture(command,**kwargs):
            sent.append(json.loads(kwargs['input']))
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'ok':True,'result':{'returncode':0,'stdout':'{}','stderr':''}}), stderr='')
        with patch.dict(sys.modules,{'node_config':SimpleNamespace(load=lambda:config)}),patch('subprocess.run',side_effect=capture):
            with self.assertRaises(SystemExit) as ended:
                bridge.forward_if_enabled(['--template',str(template),'--answers',str(answers)],requested_release=self.source)
            self.assertEqual(ended.exception.code,0)
        params=sent[0]['params'];argv=params['argv']
        self.assertEqual(argv[1],str(template))
        self.assertIn(str(template),params['input_hashes'])
        self.assertNotEqual(argv[3],str(answers))
        self.assertEqual(Path(argv[3]).read_bytes(),answers.read_bytes())

    def test_explicit_test_reader_does_not_reconfigure_production_reader(self):
        dependency = self.base / 'test-reader'; dependency.mkdir()
        (dependency / 'reader.py').write_text('# synthetic test dependency\n')
        production = self.config['reader_code_dir']
        self.config['reader_code_dir'] = str(self.base / 'production-without-test-sources')
        (self.source / 'change.txt').write_text('test-reader override')
        revision = self.commit('test reader')
        result = release.install(self.config, self.source, test_reader=dependency)
        self.assertEqual(result['commit'], revision)
        self.assertEqual(self.config['reader_code_dir'], str(self.base / 'production-without-test-sources'))
        self.assertTrue(Path(production, 'reader.py').is_file())
        self.assertEqual(self.state.read_bytes(), self.before)

    def test_interrupted_stage_cannot_become_current(self):
        root = Path(self.config['release_root'])
        orphan = root / 'releases/.install-interrupted'; orphan.mkdir()
        (orphan / 'partial').write_text('incomplete')
        self.assertEqual(release.enter(self.config, 'after-interruption')['commit'], self.first)
        self.assertEqual(self.state.read_bytes(), self.before)

    def test_unsafe_archive_rejected_before_activation(self):
        (self.source / 'outside').symlink_to(self.state)
        self.commit('unsafe link')
        with self.assertRaisesRegex(ValueError, 'relative regular sources'):
            release.install(self.config, self.source)
        self.assertEqual(self.state.read_bytes(), self.before)

    def test_code_rollback_cannot_make_new_content_unreadable(self):
        formal = self.project / '.strategy/v4/current/decision.json'
        formal.parent.mkdir(parents=True)
        formal.write_text(json.dumps({'assessment': {'revision': 6}}))
        with self.assertRaisesRegex(ValueError, 'cannot read current assessment'):
            release.activate(self.config, self.first)
        self.assertEqual(json.loads(formal.read_text())['assessment']['revision'], 6)
        # Original UNKNOWN context still resumes its pinned older implementation.
        metadata = release.enter(self.config, 'old-recovery')
        self.assertEqual(metadata['commit'], self.first)
