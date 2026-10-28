"""Check validation prerequisites without starting Docker or MySQL."""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check


class PreflightTests(unittest.TestCase):
    def test_staged_script_needs_explicit_package_root(self):
        # File-path execution searches the script directory first, even when
        # the process working directory is the package's parent.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root/'source/path_contract_pkg'
            package.mkdir(parents=True)
            (package/'__init__.py').write_text('VALUE = "resolved"\n')
            script = root/'staged/probe.py'
            script.parent.mkdir()
            script.write_text('from path_contract_pkg import VALUE\nprint(VALUE)\n')
            env = dict(os.environ, PYTHONPATH='', PYTHONDONTWRITEBYTECODE='1')
            missing = subprocess.run([os.sys.executable, '-B', str(script)],
                                     cwd=root/'source', env=env, capture_output=True, text=True)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn('ModuleNotFoundError', missing.stderr)
            resolved = subprocess.run([os.sys.executable, '-B', str(script)],
                                      cwd=root/'source', env=dict(env, PYTHONPATH=str(root/'source')),
                                      capture_output=True, text=True)
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertEqual(resolved.stdout.strip(), 'resolved')

    def test_fingerprint_command_copies_current_script_and_resolves_core_package(self):
        compose = ['docker', 'compose', '-p', 'btc-test-synthetic']
        calls = []
        def output(command, **kwargs):
            calls.append(('output', command, kwargs))
            return 'synthetic-core-id\n' if command[-3:] == ['ps', '-q', 'core'] else b'synthetic-digest\n'
        def run(command, **kwargs):
            calls.append(('run', command, kwargs))
        digest = check.fingerprint_disposable_state(compose, 'docker',
            run=run, check_output=output)
        self.assertEqual(digest, b'synthetic-digest\n')
        self.assertEqual(calls[0][1], compose + ['ps', '-q', 'core'])
        self.assertEqual(calls[1][1], ['docker', 'cp',
            str(check.ROOT/'service/check_fingerprint.py'),
            'synthetic-core-id:/tmp/check_fingerprint.py'])
        self.assertEqual(calls[2][1], compose + ['exec', '-T', '-e',
            'PYTHONPATH=/opt/btc', 'core', 'python', '/tmp/check_fingerprint.py'])
        with self.assertRaisesRegex(ValueError, 'staged /tmp Python file'):
            check.core_python_script_command(compose, '/opt/btc/unknown.py')
        with self.assertRaisesRegex(AssertionError, 'core unavailable'):
            check.fingerprint_disposable_state(compose, 'docker', run=run,
                check_output=lambda *args, **kwargs: '')
        self.assertEqual(len(calls), 3)

    def test_reader_mount_source_has_only_code_and_empty_data_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'reader-source'
            source.mkdir()
            (source/'reader.py').write_text('pass\n')
            (source/'data').mkdir()
            (source/'data/state.sqlite').write_bytes(b'private state')
            staged = check.stage_reader_source_mount(source, root/'reader-export')
            self.assertEqual((staged/'reader.py').read_text(), 'pass\n')
            self.assertTrue((staged/'data').is_dir())
            self.assertEqual(list((staged/'data').iterdir()), [])
            self.assertFalse((staged/'data/state.sqlite').exists())

    def test_missing_input_never_claims_the_disposable_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(check, 'ROOT', Path(tmp)), patch.object(check, 'ValidationGuard') as guard:
                with self.assertRaisesRegex(ValueError, 'missing source/export'):
                    check.run('docker', Path(tmp)/'reader')
            guard.assert_not_called()

    def test_actual_field_run_requires_immutable_reader_image_before_guard(self):
        with patch.object(check, 'preflight_test_sources', return_value='role_34c'), \
                patch.object(check, 'ValidationGuard') as guard:
            for image in (None, 'reader:latest'):
                with self.assertRaisesRegex(ValueError, 'immutable image ID/digest'):
                    check.run('docker', '/synthetic/reader', reader_image=image,
                              require_actual_reader=True)
            guard.assert_not_called()

    def test_missing_fixture_and_wrong_container_layout_stop_before_a_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, reader = Path(tmp)/'source', Path(tmp)/'reader'
            paths = (
                'service/Dockerfile', 'service/compose.yaml',
                'service/btc_node/__main__.py',
                'service/tests/test_data_contract.py',
                'service/tests/test_domain_runtime.py',
                'scripts/build_synthetic_fixtures.py',
                'skill/btc-core-strategy/scripts/strategy_v4.py',
                'skill/btc-core-strategy/tests/test_run_flow.py',
            )
            for relative in paths:
                path = root/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('')
            for name in ('seller_exhaustion.py', 'btc_evidence.py'):
                reader.mkdir(exist_ok=True)
                (reader/name).write_text('')
            (reader/'tests').mkdir()
            (reader/'tests/test_evidence_v2.py').write_text('')
            with self.assertRaisesRegex(ValueError, 'btc_evidence_reader.py'):
                check.preflight_test_sources(root, reader)
            (reader/'btc_evidence_reader.py').write_text('')
            with self.assertRaisesRegex(ValueError, 'version mismatch'):
                check.preflight_test_sources(root, reader)
            fake_common = {name: hashlib.sha256(b'').hexdigest()
                           for name in check.READER_COMMON}
            fake_variants = {'ci_snapshot_c58': hashlib.sha256(b'').hexdigest()}
            fake_tree = {'ci_snapshot_c58': check.reader_tree_digest(reader)}
            with patch.object(check, 'READER_COMMON', fake_common), \
                    patch.object(check, 'READER_VARIANTS', fake_variants), \
                    patch.object(check, 'READER_TREE_VARIANTS', fake_tree), \
                    self.assertRaisesRegex(ValueError, '/opt/btc'):
                check.preflight_test_sources(root, reader)
            (root/'service/Dockerfile').write_text(
                'WORKDIR /opt/btc\nCOPY service/btc_node /opt/btc/btc_node\n')
            with patch.object(check, 'READER_COMMON', fake_common), \
                    patch.object(check, 'READER_VARIANTS', fake_variants), \
                    patch.object(check, 'READER_TREE_VARIANTS', fake_tree):
                self.assertEqual(check.preflight_test_sources(root, reader), 'ci_snapshot_c58')
                with self.assertRaisesRegex(ValueError, 'version mismatch'):
                    check.preflight_test_sources(root, reader, require_actual=True)

    def test_reviewed_role_contract_and_source_only_export(self):
        reader = os.environ.get('BTC_ACTUAL_READER')
        if not reader:
            self.skipTest('set BTC_ACTUAL_READER to the reviewed role source')
        variant = check.reader_source_variant(reader, require_actual=True)
        self.assertTrue(variant.startswith('role_'))
        check.probe_actual_reader_contract(Path(reader))
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)/'reader'
            check.export_reader_sources(reader, staged)
            self.assertTrue((staged/'tests/test_evidence_v2.py').is_file())
            self.assertTrue((staged/'btc_evidence.py').is_file())
            self.assertTrue((staged/'seller_exhaustion.py').is_file())
            self.assertFalse((staged/'data').exists())
            self.assertFalse((staged/'__pycache__').exists())
            self.assertTrue(all(path.suffix == '.py' for path in staged.rglob('*') if path.is_file()))
            self.assertEqual(check.reader_tree_digest(staged), check.READER_TREE_VARIANTS[variant])
            check.probe_actual_reader_contract(staged)

    def test_unique_logs_preserve_a_failed_attempt(self):
        roots = []
        def failed(command, *, stdout, stderr, timeout):
            stdout.write('first failure\n')
            return subprocess.CompletedProcess(command, 1)
        def passed(command, *, stdout, stderr, timeout):
            stdout.write('second pass\n')
            return subprocess.CompletedProcess(command, 0)
        try:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                check.run_contract_tests(['synthetic'], 'standalone-domain', run=failed)
            first = Path(raised.exception.output.removeprefix('log='))
            roots.append(first.parent)
            second = check.run_contract_tests(['synthetic'], 'standalone-domain', run=passed)
            roots.append(second.parent)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_text(), 'first failure\n')
            self.assertEqual(second.read_text(), 'second pass\n')
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
        finally:
            for root in roots:
                shutil.rmtree(root)

    def test_rendered_core_must_serve_the_configured_socket(self):
        services = {'core': {'command': ['serve'],
            'environment': {'BTC_SERVICE_CONFIG': '/run/btc/settings.json'},
            'volumes': [{'target': '/run/btc/settings.json', 'read_only': True}],
            'healthcheck': {'test': ['CMD', 'python', '-c',
                "socket.connect('/run/btc/btc.sock')"]}}}
        check.verify_core_service_contract(services, '/run/btc/btc.sock')
        services['core']['command'] = ['request']
        with self.assertRaisesRegex(AssertionError, 'Unix socket service'):
            check.verify_core_service_contract(services, '/run/btc/btc.sock')
        services['core']['command'] = ['serve']
        services['core']['healthcheck']['test'] = ['CMD', 'true']
        with self.assertRaisesRegex(AssertionError, 'configured Unix socket'):
            check.verify_core_service_contract(services, '/run/btc/btc.sock')


if __name__ == '__main__':
    unittest.main()
