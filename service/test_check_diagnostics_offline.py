"""Exercise disposable-stack admission details without starting Docker."""
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check


class FailureDiagnosticsTests(unittest.TestCase):
    def test_stage_resource_record_is_scoped_bounded_and_never_changes_verdict(self):
        calls = []
        sample = ('memory.current=1234\nmemory.max=max\nmemory.peak=4567\n'
                  'memory.swap.current=89\nstat.anon=100\nstat.file=200\n'
                  'stat.inactive_file=150\nstat.kernel=20\nvm.MemTotal=8192\n'
                  'vm.MemAvailable=4096\nvm.SwapTotal=1024\nvm.SwapFree=900\n')

        def run(args, **kwargs):
            calls.append((args, kwargs))
            if args[1] == 'ps':
                self.assertIn('label=com.docker.compose.project=btc-test-synthetic', args)
                return subprocess.CompletedProcess(args, 0,
                    'core|aaaaaaaaaaaa\nmysql|bbbbbbbbbbbb\n', '')
            if args[2] == 'bbbbbbbbbbbb':
                raise subprocess.TimeoutExpired(args, kwargs['timeout'])
            return subprocess.CompletedProcess(args, 0, sample, 'SECRET must not print')

        output = io.StringIO()
        result = check.record_validation_resources('docker', 'btc-test-synthetic',
                                                    'before-cleanup', run=run, stream=output)
        self.assertEqual('PARTIAL', result['status'])
        self.assertEqual('OK', result['services']['core']['status'])
        self.assertEqual('UNAVAILABLE', result['services']['mysql']['status'])
        self.assertEqual('TimeoutExpired', result['services']['mysql']['reason'])
        self.assertEqual(4096, result['services']['core']['fields']['vm.MemAvailable'])
        self.assertTrue(all(kwargs['timeout'] == 5 for _, kwargs in calls))
        self.assertEqual(3, len(calls))
        self.assertNotIn('SECRET', output.getvalue())
        self.assertEqual(result, json.loads(output.getvalue().split('=', 1)[1]))

        def failing_run(args, **kwargs):
            raise OSError('synthetic Docker failure')
        output = io.StringIO()
        unavailable = check.record_validation_resources('docker', 'btc-test-synthetic',
            'before-cleanup', run=failing_run, stream=output)
        self.assertEqual('UNAVAILABLE', unavailable['status'])
        self.assertIn('BTC_VALIDATION_RESOURCE=', output.getvalue())

        def no_containers(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, '', '')
        empty = check.capture_validation_resources('docker', 'btc-test-synthetic',
                                                   run=no_containers)
        self.assertEqual('NO_RUNNING_SERVICES', empty['status'])

        def oversized(args, **kwargs):
            output = 'core|aaaaaaaaaaaa\n' if args[1] == 'ps' else 'x' * 4097
            return subprocess.CompletedProcess(args, 0, output, '')
        bounded = check.record_validation_resources('docker', 'btc-test-synthetic',
            'core-started', run=oversized, stream=io.StringIO())
        self.assertEqual('PARTIAL', bounded['status'])
        self.assertEqual('RuntimeError', bounded['services']['core']['reason'])

    def test_mysql_bind_cleanup_is_scoped_to_generated_directory(self):
        with tempfile.TemporaryDirectory(prefix='btc-data-contract-') as root:
            directory = Path(root)
            (directory / 'mysql-data').mkdir()
            with patch.object(check.subprocess, 'run') as run:
                check.cleanup_disposable_mysql_bind('docker', directory, run=run)
            command = run.call_args.args[0]
            self.assertIn('--network', command)
            self.assertIn('none', command)
            self.assertIn('--pull', command)
            self.assertIn('never', command)
            self.assertEqual(str(directory) + ':/fixture', command[command.index('-v') + 1])
            self.assertEqual('import shutil; shutil.rmtree("/fixture/mysql-data")', command[-1])
        with tempfile.TemporaryDirectory(prefix='unrelated-') as root:
            (Path(root) / 'mysql-data').mkdir()
            with patch.object(check.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                    check.cleanup_disposable_mysql_bind('docker', Path(root), run=run)
            run.assert_not_called()

    def test_compose_numeric_byte_strings_preserve_limits(self):
        limits = {'core': check.GIB, 'mysql': check.GIB,
                  'scheduler': check.GIB // 2, 'maintenance': check.GIB // 4}
        services = {name: {'restart': 'no', 'mem_limit': str(limit)}
                    for name, limit in limits.items()}
        for service in ('core', 'scheduler', 'maintenance'):
            services[service]['user'] = '1001:1001'
            services[service]['volumes'] = [{'source':'/tmp/isolated-evidence',
                                            'target':'/fixture/project'}]
        args = ('1001:1001', '/tmp/isolated-evidence', '/fixture/project')
        check.verify_disposable_limits(services, *args)
        services['core']['mem_limit'] = check.GIB
        check.verify_disposable_limits(services, *args)
        services['core']['mem_limit'] = str(check.GIB - 1)
        with self.assertRaisesRegex(AssertionError, 'core'):
            check.verify_disposable_limits(services, *args)
        services['core']['mem_limit'] = str(check.GIB)
        services['core']['restart'] = 'unless-stopped'
        with self.assertRaisesRegex(AssertionError, 'core'):
            check.verify_disposable_limits(services, *args)
        services['core']['restart'] = 'no'
        services['maintenance'].pop('user')
        with self.assertRaisesRegex(AssertionError, 'maintenance'):
            check.verify_disposable_limits(services, *args)
        services['maintenance']['user'] = '1001:1001'
        services['maintenance']['volumes'] = []
        with self.assertRaisesRegex(AssertionError, 'maintenance'):
            check.verify_disposable_limits(services, *args)

    def test_compose_failure_captures_bounded_logs_and_cleans_temp_data(self):
        with tempfile.TemporaryDirectory() as root:
            reader = Path(root) / 'reader'
            reader.mkdir()
            (reader / 'seller_exhaustion.py').write_text('')
            guard = type('Guard', (), {'project_started': False, 'cleaned': False})()
            failure = subprocess.CalledProcessError(1, ['docker', 'compose', 'config'])
            with patch.object(check.subprocess, 'check_output', side_effect=failure), \
                    patch.object(check.subprocess, 'run') as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    check._run('docker', reader, None, guard, 'btc-test-diagnostics')
            calls = [item.args[0] for item in run.call_args_list]
            self.assertTrue(any(command[-2:] == ['ps', '--all'] for command in calls))
            self.assertTrue(any(command[-4:] == ['logs', '--tail', '60', 'core'] for command in calls))
            self.assertTrue(any(command[-4:] == ['logs', '--tail', '60', 'maintenance'] for command in calls))
            self.assertTrue(guard.cleaned)


if __name__ == '__main__':
    unittest.main()
