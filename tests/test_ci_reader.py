"""CI dependency identity, export exclusion and real-check invocation contracts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_reader', ROOT / 'scripts/ci_reader.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class CIReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / 'snapshot'
        self.snapshot.mkdir()
        sources = {
            'telegram_reader/reader.py': 'pass\n',
            'telegram_reader/seller_exhaustion.py': 'pass\n',
            'requirements.txt': '',
            'roles/containers/Dockerfile': 'FROM scratch\n',
            'roles/containers/entrypoint.py': 'pass\n',
        }
        manifest = {'schema': 1, 'source_repository': 'https://example.invalid/roles.git',
                    'source_commit': '1' * 40, 'files': []}
        for name, content in sources.items():
            path = self.snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            path.chmod(0o644)
            raw = content.encode()
            manifest['files'].append({'path': name, 'mode': '100644',
                'sha256': hashlib.sha256(raw).hexdigest(),
                'git_blob_sha': ci.object_id(b'blob', raw).hex()})
        (self.snapshot / 'SNAPSHOT.json').write_text(json.dumps(manifest))
        (self.snapshot / '.dockerignore').write_text('.git\n')
        files = {p.relative_to(self.snapshot).as_posix()
                 for p in self.snapshot.rglob('*') if p.is_file()}
        self.lock = {'repository': manifest['source_repository'],
                     'commit': manifest['source_commit'],
                     'ci_snapshot': {'path': 'snapshot',
                                     'tree': ci.source_tree(self.snapshot, files)}}
        (self.root / 'test_support').mkdir()
        self.write_lock()

    def write_lock(self):
        (self.root / 'test_support/reader.lock.json').write_text(json.dumps(self.lock))

    def test_actual_dependency_is_verified_and_excluded_from_exports(self):
        lock = ci.read_lock(ROOT)
        ci.verify_snapshot(ROOT / lock['ci_snapshot']['path'], lock)
        exported = json.loads((ROOT / 'export-manifest.json').read_text())['files']
        self.assertFalse(any(p.startswith('test_support/ci_reader/') for p in exported))
        self.assertNotIn('scripts/ci_reader.py', exported)
        self.assertNotIn('tests/test_ci_reader.py', exported)

    def test_missing_tree_is_rejected(self):
        self.lock['ci_snapshot']['tree'] = None
        self.write_lock()
        with self.assertRaisesRegex(ValueError, 'snapshot tree'):
            ci.read_lock(self.root)

    def test_tree_and_upstream_identity_must_both_match(self):
        for field in ('tree', 'upstream'):
            lock = json.loads(json.dumps(self.lock))
            if field == 'upstream':
                lock['commit'] = '2' * 40
            else:
                lock['ci_snapshot']['tree'] = '2' * 40
            with self.subTest(field=field), self.assertRaises(ValueError):
                ci.verify_snapshot(self.snapshot, lock)
        self.assertEqual(ci.verify_snapshot(self.snapshot, self.lock), self.snapshot.resolve())

    def test_modified_source_is_rejected(self):
        (self.snapshot / 'telegram_reader/reader.py').write_text('raise RuntimeError\n')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            ci.verify_snapshot(self.snapshot, self.lock)

    def test_extra_runtime_state_is_rejected(self):
        (self.snapshot / 'telegram_reader/data').mkdir()
        (self.snapshot / 'telegram_reader/data/state.sqlite').write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'extra files'):
            ci.verify_snapshot(self.snapshot, self.lock)

    def test_source_symlink_is_rejected(self):
        path = self.snapshot / 'telegram_reader/reader.py'
        path.unlink()
        path.symlink_to(self.snapshot / 'telegram_reader/seller_exhaustion.py')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            ci.verify_snapshot(self.snapshot, self.lock)

    def test_real_entrypoint_receives_both_reader_inputs(self):
        plan = ci.commands(self.root, self.snapshot, '/test/docker', 'btc-reader-ci:test')
        def inspect_build(command, **kwargs):
            if command[:2] == ['/test/docker', 'build']:
                context = Path(command[-1])
                self.assertTrue((context / 'telegram_reader/data').is_dir())
                self.assertEqual(list((context / 'telegram_reader/data').iterdir()), [])
                self.assertEqual(Path(command[command.index('--file') + 1]),
                                 context / 'roles/containers/Dockerfile')
                self.assertFalse((self.snapshot / 'telegram_reader/data').exists())
        with patch.object(ci.subprocess, 'run', side_effect=inspect_build) as run:
            ci.execute(plan, '/test/docker', 'btc-reader-ci:test')
        self.assertEqual(run.call_args_list[0].args[0][0:4],
                         ['/test/docker', 'build', '--tag', 'btc-reader-ci:test'])
        actual = run.call_args_list[1].args[0]
        self.assertEqual(actual[2], str(self.root / 'service/check.py'))
        self.assertEqual(actual[actual.index('--reader') + 1],
                         str(self.snapshot / 'telegram_reader'))
        self.assertEqual(actual[actual.index('--reader-image') + 1], 'btc-reader-ci:test')
        self.assertTrue(run.call_args_list[1].kwargs['check'])
        self.assertEqual(run.call_args_list[2].args[0],
                         ['/test/docker', 'image', 'rm', 'btc-reader-ci:test'])

    def test_docker_failure_propagates_and_cleans_only_its_image(self):
        plan = ci.commands(self.root, self.snapshot, '/test/docker', 'btc-reader-ci:test')
        with patch.object(ci.subprocess, 'run',
                          side_effect=[None, subprocess.CalledProcessError(1, ['contract']), None]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                ci.execute(plan, '/test/docker', 'btc-reader-ci:test')
        self.assertEqual(run.call_args_list[-1].args[0],
                         ['/test/docker', 'image', 'rm', 'btc-reader-ci:test'])


if __name__ == '__main__':
    unittest.main()
