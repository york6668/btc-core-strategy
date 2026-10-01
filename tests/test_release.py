"""Version switching must not reset state, research or delivery recovery."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('btc_release', ROOT / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


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
        (self.source / 'export-manifest.json').write_text(json.dumps({'files': ['scripts/run_tests.py']}))
        self.first = self.commit('first')
        release.install(self.config, self.source)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], text=True, stderr=subprocess.PIPE).strip()

    def commit(self, message):
        self.git('add', '.'); self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

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
