"""Resource tracing uses synthetic requests and processes, never node data."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from btc_node import request_trace as trace
from btc_node import __main__ as node


class TimeoutReader:
    def readline(self, limit):
        raise TimeoutError('synthetic timeout')


class TimeoutWriter:
    def write(self, data):
        raise TimeoutError('synthetic write timeout')


class RequestTraceOfflineTest(unittest.TestCase):
    def setUp(self):
        self.stderr = io.StringIO()
        self.stderr_patch = patch.object(trace.sys, 'stderr', self.stderr)
        self.stderr_patch.start()
        self.addCleanup(self.stderr_patch.stop)
        trace._LAST_STATUS_NS = 0

    def records(self):
        return [json.loads(line) for line in self.stderr.getvalue().splitlines()]

    def test_success_error_and_sensitive_payload_are_separated(self):
        secret = 'PRIVATE_POSITION_AND_PASSWORD_MARKER'
        payload = {'method': 'prices.query', 'params': {'secret': secret}}
        output = io.BytesIO()
        with patch.object(node, 'answer', return_value={'ok': True, 'result': {'count': 2}}):
            node.handle_stream({}, io.BytesIO(json.dumps(payload).encode() + b'\n'), output)
        self.assertEqual({'ok': True, 'result': {'count': 2}}, json.loads(output.getvalue()))
        records = self.records()
        self.assertEqual(['START', 'END'], [row['event'] for row in records])
        self.assertEqual('prices.query', records[0]['method'])
        self.assertEqual('OK', records[1]['outcome'])
        self.assertEqual(records[0]['request_id'], records[1]['request_id'])
        self.assertNotIn(secret, self.stderr.getvalue())
        with patch.object(node, 'answer', return_value={'ok': False, 'error': 'synthetic'}):
            node.handle_stream({}, io.BytesIO(b'{"method":"object.get"}\n'), io.BytesIO())
        self.assertEqual('APP_ERROR', self.records()[-1]['outcome'])

    def test_invalid_input_and_timeout_paths_emit_fixed_outcomes(self):
        node.handle_stream({}, TimeoutReader(), io.BytesIO())
        node.handle_stream({}, io.BytesIO(b'not-json\n'), io.BytesIO())
        with patch.object(node, 'MAX_REQUEST', 8):
            node.handle_stream({}, io.BytesIO(b'0123456789\n'), io.BytesIO())
        node.handle_stream({}, io.BytesIO(b'{}'), io.BytesIO())
        self.assertEqual(['READ_TIMEOUT', 'INVALID_JSON', 'OVERSIZE', 'INVALID_FRAME'],
                         [row['outcome'] for row in self.records()])
        with patch.object(node, 'answer', return_value={'ok': True, 'result': {}}):
            with self.assertRaises(TimeoutError):
                node.handle_stream({}, io.BytesIO(b'{"method":"status"}\n'), TimeoutWriter())
        self.assertEqual('TIMEOUT', self.records()[-1]['outcome'])

    def test_status_rate_limit_and_unknown_method_sanitization(self):
        with patch.object(node, 'answer', return_value={'ok': True, 'result': {}}):
            for _ in range(2):
                node.handle_stream({}, io.BytesIO(b'{"method":"status"}\n'), io.BytesIO())
            node.handle_stream({}, io.BytesIO(b'{"method":"private-secret"}\n'), io.BytesIO())
        records = self.records()
        self.assertEqual(['START', 'END', 'START', 'END'], [r['event'] for r in records])
        self.assertEqual('UNKNOWN', records[-1]['method'])
        self.assertNotIn('private-secret', self.stderr.getvalue())

    def test_resource_fields_are_readable_or_explicitly_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(trace, '_CGROUP', root), patch.object(trace, '_STATUS', root / 'missing'):
                missing = trace.sample()
                self.assertEqual('UNKNOWN', missing['cgroup_state'])
                self.assertIsNone(missing['process_rss_bytes'])
                (root / 'memory.current').write_text('1234\n')
                (root / 'memory.peak').write_text('4567\n')
                (root / 'memory.swap.current').write_text('89\n')
                (root / 'status').write_text('Name:\ttest\nVmRSS:\t20 kB\n')
            with patch.object(trace, '_CGROUP', root), patch.object(trace, '_STATUS', root / 'status'):
                measured = trace.sample()
            self.assertEqual('READABLE', measured['cgroup_state'])
            self.assertEqual(20 * 1024, measured['process_rss_bytes'])
            self.assertEqual(1234, measured['cgroup_memory_current_bytes'])
            self.assertEqual(4567, measured['cgroup_memory_peak_bytes_cumulative'])

    def test_child_pid_and_result_are_correlated_without_command_logging(self):
        with trace.RequestTrace('core.execute') as request:
            result = trace.run_child([sys.executable, '-c', 'print("SENSITIVE_CHILD_OUTPUT")'],
                                     cwd='.', env=None)
            request.outcome = 'OK'
        self.assertEqual(0, result.returncode)
        self.assertIn('SENSITIVE_CHILD_OUTPUT', result.stdout)
        records = self.records()
        self.assertEqual(['START', 'CHILD_START', 'CHILD_END', 'END'], [r['event'] for r in records])
        self.assertEqual(1, len({r['request_id'] for r in records}))
        self.assertEqual(records[1]['child_pid'], records[2]['child_pid'])
        self.assertNotIn('SENSITIVE_CHILD_OUTPUT', self.stderr.getvalue())
        with patch.object(trace.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as original:
            trace.run_child(['unused'], cwd='.', env=None)
            original.assert_called_once()

    def test_interrupted_child_is_killed_and_recorded_without_argv(self):
        class InterruptedProcess:
            pid = 54321
            calls = 0
            killed = False

            def communicate(self):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError('synthetic child interruption')
                return '', ''

            def kill(self):
                self.killed = True

        child = InterruptedProcess()
        with patch.object(trace.subprocess, 'Popen', return_value=child):
            with self.assertRaises(TimeoutError):
                with trace.RequestTrace('core.execute'):
                    trace.run_child(['SECRET_ARGUMENT'], cwd='.', env=None)
        self.assertTrue(child.killed)
        self.assertEqual(['START', 'CHILD_START', 'CHILD_END', 'END'],
                         [row['event'] for row in self.records()])
        self.assertEqual('INTERRUPTED', self.records()[2]['outcome'])
        self.assertEqual('TIMEOUT', self.records()[3]['outcome'])
        self.assertNotIn('SECRET_ARGUMENT', self.stderr.getvalue())

    def test_spawn_and_dispatch_exceptions_still_close_trace(self):
        with patch.object(trace.subprocess, 'Popen', side_effect=OSError('synthetic spawn failure')):
            with self.assertRaises(OSError):
                with trace.RequestTrace('core.execute'):
                    trace.run_child(['SECRET_ARGUMENT'], cwd='.', env=None)
        self.assertEqual(['START', 'CHILD_END', 'END'],
                         [row['event'] for row in self.records()])
        self.assertEqual('SPAWN_ERROR', self.records()[1]['outcome'])
        self.assertEqual('EXCEPTION', self.records()[2]['outcome'])
        self.assertIsNone(self.records()[1]['child_pid'])
        self.assertNotIn('SECRET_ARGUMENT', self.stderr.getvalue())
        with patch.object(node, 'answer', side_effect=RuntimeError('synthetic dispatch failure')):
            with self.assertRaises(RuntimeError):
                node.handle_stream({}, io.BytesIO(b'{"method":"status"}\n'), io.BytesIO())
        self.assertEqual('EXCEPTION', self.records()[-1]['outcome'])

    def test_isolated_process_logs_request_without_payload(self):
        source = Path(__file__).resolve().parents[1]
        program = ('import sys\nfrom btc_node import __main__ as node\n'
                   'node.answer=lambda settings,request:{"ok":True,"result":{"fixed":True}}\n'
                   'node.handle_stream({},sys.stdin.buffer,sys.stdout.buffer)\n')
        process = subprocess.run([sys.executable, '-c', program],
                                 input=b'{"method":"prices.query","private_position":"TOKEN_NEVER_LOG"}\n',
                                 env={**os.environ, 'PYTHONPATH': str(source)},
                                 capture_output=True, timeout=3)
        self.assertEqual(0, process.returncode, process.stderr.decode())
        self.assertEqual({'ok': True, 'result': {'fixed': True}}, json.loads(process.stdout))
        stderr = process.stderr.decode()
        records = [json.loads(line) for line in stderr.splitlines() if line.startswith('{')]
        self.assertEqual(['START', 'END'], [row['event'] for row in records])
        self.assertEqual(1, len({row['pid'] for row in records}))
        self.assertNotIn('TOKEN_NEVER_LOG', stderr)


if __name__ == '__main__':
    unittest.main()
