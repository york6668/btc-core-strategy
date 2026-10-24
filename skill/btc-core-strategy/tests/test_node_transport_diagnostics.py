"""Original transport functions with strict synthetic runner/config; no real IPC."""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import model_daily
import node_bridge
import node_config
import node_scheduler_worker


class NodeTransportDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='btc-transport-synthetic-')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.operation = 'SYNTHETIC_ORIGINAL_OPERATION'
        self.docker = {'enabled': True, 'binary': 'SYNTHETIC_DOCKER_NOT_EXECUTABLE',
                       'project': 'SYNTHETIC_PROJECT', 'env_file': str(self.project / 'not-a-real.env'),
                       'compose_file': str(self.project / 'not-a-real-compose.json'), 'override_files': []}
        settings = {'project_dir': str(self.project), 'docker_service': self.docker}
        self.docker['fact_bridge_dir'] = str(self.project / 'synthetic-facts')
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {'BTC_IN_CONTAINER': '0', 'BTC_OPERATION_ID': self.operation}))
        self.stack.enter_context(mock.patch.object(node_config, 'load', return_value=settings))
        self.sync = self.stack.enter_context(mock.patch.object(node_bridge, 'sync_facts'))
        self.runner = self.stack.enter_context(mock.patch.object(node_bridge.subprocess, 'run'))
        self.stack.enter_context(mock.patch('socket.socket', side_effect=AssertionError('no network')))

    def response(self, code=0, stdout=None, stderr=''):
        self.runner.return_value = SimpleNamespace(returncode=code,
            stdout=json.dumps({'ok': True, 'result': {'synthetic': 'accepted'}}) if stdout is None else stdout,
            stderr=stderr)

    def request(self):
        return model_daily.request('SYNTHETIC_OFFLINE_TEST_ONLY', {'synthetic': True}, self.operation)

    def worker_request(self):
        return node_scheduler_worker.NodeClient({'docker_service': self.docker}).request(
            'SYNTHETIC_OFFLINE_TEST_ONLY', {'synthetic': True}, self.operation)

    def bridge(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as exit:
            node_bridge.forward_if_enabled(['--project', str(self.project), '--root', str(self.project / '.strategy/v4'), 'pin'])
        return exit.exception.code, stdout.getvalue(), stderr.getvalue()

    def assert_one_original_request(self):
        self.runner.assert_called_once()
        argv = self.runner.call_args.args[0]
        payload = json.loads(self.runner.call_args.kwargs['input'])
        self.assertEqual(argv[0], self.docker['binary'])
        self.assertEqual(payload['operation_id'], self.operation)
        self.assertEqual(argv[-7:], ['exec', '-T', 'core', 'python', '-m', 'btc_node', 'request'])
        return payload

    def test_nonzero_success_json_is_not_a_successful_request(self):
        self.response(code=1)
        with self.assertRaises(model_daily.Blocked):
            self.request()
        self.assert_one_original_request()

    def test_empty_stdout_preserves_safe_failure_evidence_and_original_identity(self):
        text = ('permission denied while trying to connect to the docker API at '
                'unix:///synthetic/docker.sock\nauthorization: Bearer SYNTHETIC_SECRET\n')
        self.response(code=1, stdout='', stderr=text)
        with self.assertRaises(model_daily.Blocked) as failed:
            self.request()
        diagnostic = failed.exception.diagnostic
        self.assertEqual(diagnostic['child_exit_code'], 1)
        self.assertEqual(diagnostic['operation_id'], self.operation)
        self.assertEqual(diagnostic['effect'], 'UNKNOWN')
        self.assertEqual(diagnostic['stderr_class'], 'DOCKER_UNIX_SOCKET_PERMISSION_DIAGNOSTIC')
        self.assertEqual(diagnostic['stderr_bytes'], len(text.encode()))
        self.assertEqual(len(diagnostic['stderr_sha256']), 64)
        self.assertNotIn('SYNTHETIC_SECRET', str(failed.exception) + json.dumps(diagnostic))
        self.assert_one_original_request()

    def test_nonzero_success_json_cannot_escape_through_bridge(self):
        self.response(code=1, stdout=json.dumps({'ok': True, 'result': {'returncode': 0, 'stdout': '{"synthetic": true}'}}))
        code, stdout, stderr = self.bridge()
        self.assertEqual(code, 75)
        self.assertEqual(stdout, '')
        self.assertIn(self.operation, stderr)
        self.assert_one_original_request()

    def test_execution_error_is_not_an_approval_denial_or_an_automatic_retry(self):
        self.runner.side_effect = PermissionError(errno.EACCES, 'SYNTHETIC_EXEC_FAILURE')
        with self.assertRaises(model_daily.Blocked) as failed:
            self.request()
        self.assertEqual(failed.exception.diagnostic['stage'], 'PROCESS_START')
        self.assertEqual(failed.exception.diagnostic['errno'], errno.EACCES)
        self.assertEqual(failed.exception.diagnostic['effect'], 'UNKNOWN')
        self.assert_one_original_request()

    def test_bridge_execution_error_stops_the_original_route(self):
        self.runner.side_effect = FileNotFoundError(errno.ENOENT, 'SYNTHETIC_MISSING_BINARY')
        code, stdout, stderr = self.bridge()
        self.assertEqual(code, 75)
        self.assertEqual(stdout, '')
        self.assertIn(self.operation, stderr)
        self.assert_one_original_request()

    def test_truthy_ok_is_not_a_protocol_boolean(self):
        for value in ('false', 1, None):
            with self.subTest(value=value):
                self.runner.reset_mock()
                self.response(stdout=json.dumps({'ok': value, 'result': {'synthetic': True}}))
                with self.assertRaises(model_daily.Blocked):
                    self.request()
                self.assert_one_original_request()

    def test_success_without_result_or_nonobject_json_is_unknown(self):
        for value in ({'ok': True}, [], None):
            with self.subTest(value=value):
                self.runner.reset_mock()
                self.response(stdout=json.dumps(value))
                with self.assertRaises(model_daily.Blocked):
                    self.request()
                self.assert_one_original_request()

    def test_valid_node_rejection_preserves_application_reason(self):
        self.response(code=1, stdout=json.dumps({'ok': False, 'message': 'SYNTHETIC_APPLICATION_REJECTED'}))
        with self.assertRaisesRegex(model_daily.Blocked, 'SYNTHETIC_APPLICATION_REJECTED'):
            self.request()
        self.assert_one_original_request()

    def test_zero_success_json_retains_original_result_and_route(self):
        self.response()
        self.assertEqual(self.request(), {'synthetic': 'accepted'})
        self.assert_one_original_request()

    def test_bridge_does_not_print_malformed_success_result(self):
        for result in ({'stdout': 'SYNTHETIC_MUST_NOT_PRINT'},
                       {'returncode': '0', 'stdout': 'SYNTHETIC_MUST_NOT_PRINT'},
                       {'returncode': 0, 'stdout': None}, None):
            with self.subTest(result=result):
                self.runner.reset_mock()
                self.response(stdout=json.dumps({'ok': True, 'result': result}))
                code, stdout, stderr = self.bridge()
                self.assertEqual(code, 75)
                self.assertEqual(stdout, '')
                self.assertIn('INVALID_CORE_RESULT', stderr)
                self.assert_one_original_request()

    def test_bridge_preserves_business_exit_and_application_rejection(self):
        self.response(stdout=json.dumps({'ok': True, 'result': {'returncode': 2,
                                                              'stdout': '', 'stderr': 'SYNTHETIC_BUSINESS_ERROR'}}))
        code, stdout, stderr = self.bridge()
        self.assertEqual((code, stdout, stderr), (2, '', 'SYNTHETIC_BUSINESS_ERROR'))
        self.assert_one_original_request()
        self.runner.reset_mock()
        self.response(code=1, stdout=json.dumps({'ok': False, 'message': 'SYNTHETIC_APPLICATION_REJECTED'}))
        code, stdout, stderr = self.bridge()
        self.assertEqual((code, stdout), (75, ''))
        self.assertIn('SYNTHETIC_APPLICATION_REJECTED', stderr)
        self.assert_one_original_request()

    def test_model_cli_keeps_safe_diagnostic_without_claiming_root_cause(self):
        self.response(code=1, stdout='', stderr='SYNTHETIC_UNCLASSIFIED_SECRET')
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = model_daily.main(['status'])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), '')
        failure = json.loads(stderr.getvalue())
        self.assertEqual(failure['diagnostic']['child_exit_code'], 1)
        self.assertEqual(failure['diagnostic']['stderr_class'], 'CLIENT_DIAGNOSTIC_PRESENT')
        self.assertIsNone(failure['diagnostic']['operation_id'])
        self.assertNotIn('SYNTHETIC_UNCLASSIFIED_SECRET', stderr.getvalue())
        self.runner.assert_called_once()

    def test_client_output_decode_error_remains_unknown_without_raw_bytes(self):
        for route in ('request', 'bridge'):
            with self.subTest(route=route):
                self.runner.reset_mock()
                self.runner.side_effect = UnicodeDecodeError('utf-8', b'SYNTHETIC_SECRET\xff', 16, 17, 'invalid')
                if route == 'request':
                    with self.assertRaises(model_daily.Blocked) as failed:
                        self.request()
                    diagnostic = failed.exception.diagnostic
                    self.assertEqual(diagnostic['stage'], 'CLIENT_RESPONSE')
                    self.assertEqual(diagnostic['reason'], 'CLIENT_OUTPUT_DECODE_FAILED')
                    self.assertNotIn('SYNTHETIC_SECRET', str(failed.exception))
                else:
                    code, stdout, stderr = self.bridge()
                    self.assertEqual((code, stdout), (75, ''))
                    self.assertIn('CLIENT_OUTPUT_DECODE_FAILED', stderr)
                    self.assertNotIn('SYNTHETIC_SECRET', stderr)
                self.assert_one_original_request()

    def test_worker_rejects_client_nonzero_even_with_success_json(self):
        self.response(code=1)
        with self.assertRaises(RuntimeError) as failed:
            self.worker_request()
        self.assertEqual(failed.exception.diagnostic['reason'], 'CLIENT_EXIT_NOT_SUCCESSFUL')
        self.assert_one_original_request()

    def test_worker_empty_or_start_failure_keeps_unknown_original_operation(self):
        self.response(code=1, stdout='', stderr='SYNTHETIC_SECRET')
        with self.assertRaises(RuntimeError) as failed:
            self.worker_request()
        self.assertEqual(failed.exception.diagnostic['child_exit_code'], 1)
        self.assertNotIn('SYNTHETIC_SECRET', str(failed.exception))
        self.assert_one_original_request()
        self.runner.reset_mock()
        self.runner.side_effect = PermissionError(errno.EACCES, 'SYNTHETIC_SECRET')
        with self.assertRaises(RuntimeError) as failed:
            self.worker_request()
        self.assertEqual(failed.exception.diagnostic['stage'], 'PROCESS_START')
        self.assertEqual(failed.exception.diagnostic['effect'], 'UNKNOWN')
        self.assert_one_original_request()

    def test_worker_timeout_cannot_prove_no_remote_effect_or_authorize_replay(self):
        self.runner.side_effect = node_bridge.subprocess.TimeoutExpired(['SYNTHETIC_SECRET'], 120)
        with self.assertRaises(RuntimeError) as failed:
            self.worker_request()
        diagnostic = failed.exception.diagnostic
        self.assertEqual(diagnostic['stage'], 'CLIENT_WAIT')
        self.assertEqual(diagnostic['effect'], 'UNKNOWN')
        self.assertEqual(diagnostic['operation_id'], self.operation)
        self.assertNotIn('SYNTHETIC_SECRET', str(failed.exception))
        self.assert_one_original_request()

    def test_worker_retains_success_and_application_rejection(self):
        self.response()
        self.assertEqual(self.worker_request(), {'synthetic': 'accepted'})
        self.assert_one_original_request()
        self.runner.reset_mock()
        self.response(code=1, stdout=json.dumps({'ok': False, 'message': 'SYNTHETIC_APPLICATION_REJECTED'}))
        with self.assertRaisesRegex(RuntimeError, 'SYNTHETIC_APPLICATION_REJECTED'):
            self.worker_request()
        self.assert_one_original_request()

    def test_transport_full_trace_suppresses_raw_exception_details_at_each_boundary(self):
        sentinel = 'SYNTHETIC_' + 'PRIVATE_TRACE_MARKER_R3'
        cases = [('request', PermissionError(errno.EACCES, 'denied', '/' + sentinel)),
                 ('worker', PermissionError(errno.EACCES, 'denied', '/' + sentinel)),
                 ('worker', node_bridge.subprocess.TimeoutExpired([sentinel], 120)),
                 ('bridge', PermissionError(errno.EACCES, 'denied', '/' + sentinel))]
        for route, original in cases:
            with self.subTest(route=route, error=type(original).__name__):
                self.runner.reset_mock()
                self.runner.side_effect = original
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    try:
                        if route == 'request': self.request()
                        elif route == 'worker': self.worker_request()
                        else:
                            node_bridge.forward_if_enabled(['--project', str(self.project),
                                '--root', str(self.project / '.strategy/v4'), 'pin'])
                    except (RuntimeError, SystemExit) as failed:
                        trace = ''.join(traceback.format_exception(type(failed), failed, failed.__traceback__))
                        self.assertNotIn(sentinel, trace + stderr.getvalue())
                        self.assertTrue(failed.__suppress_context__)
                        if route != 'bridge':
                            self.assertEqual(failed.diagnostic['effect'], 'UNKNOWN')
                            self.assertEqual(failed.diagnostic['operation_id'], self.operation)
                    else:
                        self.fail('failed transport must not return successfully')
                self.assertEqual(stdout.getvalue(), '')
                self.assert_one_original_request()

    def test_original_worker_main_does_not_reexpose_caught_transport_chain(self):
        sentinel = 'SYNTHETIC_' + 'PRIVATE_TRACE_MARKER_R3'
        errors = [node_bridge.subprocess.TimeoutExpired([sentinel], 120),
                  PermissionError(errno.EACCES, 'denied', '/' + sentinel)]
        for original in errors:
            with self.subTest(error=type(original).__name__):
                self.runner.reset_mock()
                self.runner.side_effect = original
                try:
                    self.worker_request()
                except RuntimeError as failed:
                    with mock.patch.object(sys, 'argv', ['SYNTHETIC_WORKER']), \
                         mock.patch.dict(os.environ, {'BTC_NODE_CONFIG': str(self.project / 'NOT_READ_CONFIG')}), \
                         mock.patch.object(node_scheduler_worker, 'run_once', side_effect=failed) as once:
                        try:
                            node_scheduler_worker.main()
                        except RuntimeError as surfaced:
                            trace = ''.join(traceback.format_exception(type(surfaced), surfaced, surfaced.__traceback__))
                            self.assertNotIn(sentinel, trace)
                            self.assertEqual(surfaced.diagnostic['effect'], 'UNKNOWN')
                            self.assertIn(self.operation, trace)
                        else:
                            self.fail('expected original main failure boundary')
                        once.assert_called_once()
                else:
                    self.fail('failed transport must not return successfully')
                self.assert_one_original_request()

    def test_canonical_three_entry_failures_keep_one_unknown_operation(self):
        cases = (
            ('nonzero_ok', 1, '{"ok":true,"result":{}}', None, 'CLIENT_EXIT_NOT_SUCCESSFUL'),
            ('empty', 0, '', None, 'INVALID_JSON'),
            ('malformed', 0, '{', None, 'INVALID_JSON'),
            ('array', 0, '[]', None, 'INVALID_ENVELOPE'),
            ('null', 0, 'null', None, 'INVALID_ENVELOPE'),
            ('truthy_ok', 0, '{"ok":1,"result":{}}', None, 'INVALID_ENVELOPE'),
            ('no_result', 0, '{"ok":true}', None, 'MISSING_RESULT'),
            ('start', 0, '', PermissionError(errno.EACCES, 'SYNTHETIC_RAW_SECRET'), 'CLIENT_START_FAILED'),
            ('decode', 0, '', UnicodeDecodeError('utf-8', b'SYNTHETIC_RAW_SECRET\xff', 20, 21, 'invalid'), 'CLIENT_OUTPUT_DECODE_FAILED'),
        )
        for route in ('model', 'bridge', 'worker'):
            for label, code, output, error, reason in cases:
                with self.subTest(route=route, case=label):
                    self.runner.reset_mock()
                    self.runner.side_effect = error
                    self.response(code=code, stdout=output, stderr='SYNTHETIC_RAW_SECRET')
                    if route == 'bridge':
                        actual_code, stdout, text = self.bridge()
                        self.assertEqual((actual_code, stdout), (75, ''))
                        self.assertIn('"effect": "UNKNOWN"', text)
                        self.assertIn(reason, text)
                    else:
                        with self.assertRaises(model_daily.Blocked if route == 'model' else RuntimeError) as caught:
                            self.request() if route == 'model' else self.worker_request()
                        self.assertEqual(caught.exception.diagnostic['effect'], 'UNKNOWN')
                        self.assertEqual(caught.exception.diagnostic['reason'], reason)
                        self.assertEqual(caught.exception.diagnostic['operation_id'], self.operation)
                        text = ''.join(traceback.format_exception(type(caught.exception), caught.exception,
                                                                  caught.exception.__traceback__))
                    self.assertNotIn('SYNTHETIC_RAW_SECRET', text)
                    self.assertIn(self.operation, text)
                    self.assert_one_original_request()

    def test_canonical_three_entry_success_and_explicit_rejection_stay_distinct(self):
        result = {'returncode': 0, 'stdout': '{"synthetic":"accepted"}', 'stderr': ''}
        for route in ('model', 'bridge', 'worker'):
            with self.subTest(route=route, outcome='success'):
                self.runner.reset_mock()
                self.runner.side_effect = None
                self.response(stdout=json.dumps({'ok': True, 'result': result}))
                if route == 'bridge':
                    code, stdout, stderr = self.bridge()
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(stdout), {'synthetic': 'accepted'})
                    self.assertEqual(stderr, '')
                else:
                    actual = self.request() if route == 'model' else self.worker_request()
                    self.assertEqual(actual, result)
                self.assert_one_original_request()
            with self.subTest(route=route, outcome='application_rejection'):
                self.runner.reset_mock()
                self.response(code=1, stdout=json.dumps({'ok': False, 'message': 'SYNTHETIC_APPLICATION_REJECTED'}))
                if route == 'bridge':
                    code, stdout, text = self.bridge()
                    self.assertEqual((code, stdout), (75, ''))
                    self.assertNotIn('"effect": "UNKNOWN"', text)
                else:
                    with self.assertRaises(model_daily.Blocked if route == 'model' else RuntimeError) as caught:
                        self.request() if route == 'model' else self.worker_request()
                    self.assertIsNone(getattr(caught.exception, 'diagnostic', None))
                    text = str(caught.exception)
                self.assertIn('SYNTHETIC_APPLICATION_REJECTED', text)
                self.assert_one_original_request()


if __name__ == '__main__':
    unittest.main()
