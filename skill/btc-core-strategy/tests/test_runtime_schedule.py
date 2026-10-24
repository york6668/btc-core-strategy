"""Only generated project state; native databases/sessions are never test inputs."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import runtime_schedule


class RuntimeScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(runtime_schedule.bind, None, None)
        self.base = Path(self.temp.name)
        self.at = datetime(2030, 3, 1, 13, 5, tzinfo=timezone.utc)
        self.config = {'codex_state_db': str(self.base / 'restricted/state.sqlite'),
                       'codex_automations_db': str(self.base / 'restricted/automation.sqlite')}

    def window(self):
        return {'schema': 'BTC_EXECUTION_WINDOW_V1', 'status': 'AVAILABLE',
                'source': 'BTC_NODE_SCHEDULER_V1', 'checked_at': self.at.isoformat(),
                'next_run_at': '2030-03-01T13:06:00+00:00',
                'execution_margin_seconds': 60,
                'review_not_before': '2030-03-01T13:07:00+00:00'}

    def test_platform_config_is_not_permission_to_read_native_databases(self):
        with mock.patch('sqlite3.connect', side_effect=AssertionError('forbidden synthetic platform database read')):
            got = runtime_schedule.snapshot(self.config, 'CURRENT', self.at)
        self.assertEqual(got['status'], 'UNAVAILABLE')
        self.assertEqual(got['observation_state'], 'NOT_OBSERVED')
        self.assertNotIn('automation_id', got)
        self.assertNotIn('next_run_at', got)
        self.assertNotIn('review_not_before', got)
        self.assertFalse((self.base / 'restricted').exists())

    def test_no_platform_path_resolution_or_session_reads(self):
        with mock.patch.object(Path, 'resolve', side_effect=AssertionError('no platform path resolution')), \
             mock.patch.object(Path, 'read_text', side_effect=AssertionError('no session read')):
            got = runtime_schedule.snapshot(self.config, 'CURRENT', self.at)
        self.assertEqual(got['status'], 'UNAVAILABLE')

    def test_unknown_run_is_unobserved_not_asserted_unscheduled(self):
        for run in (None, 'UNKNOWN', 'codex:CURRENT'):
            got = runtime_schedule.snapshot(self.config, run, self.at)
            self.assertEqual(got['status'], 'UNAVAILABLE')
            self.assertEqual(got['run_ref'], run)
            self.assertEqual(got['checked_at'], self.at.isoformat())

    def test_current_uses_safe_snapshot_when_nothing_is_bound(self):
        with mock.patch('node_config.load', return_value=self.config), \
             mock.patch('sqlite3.connect', side_effect=AssertionError('no native state')):
            got = runtime_schedule.current('CURRENT')
        self.assertEqual(got['status'], 'UNAVAILABLE')
        self.assertEqual(got['run_ref'], 'CURRENT')

    def test_prefixed_run_ref_and_frozen_binding_remain_exact(self):
        got = runtime_schedule.snapshot(self.config, 'codex:CURRENT', self.at)
        path = self.base / 'snapshot.json'
        path.write_text(json.dumps(got))
        runtime_schedule.bind(path, 'codex:CURRENT')
        self.assertEqual(runtime_schedule.current(), got)
        runtime_schedule.bind(path, None)
        self.assertEqual(runtime_schedule.current()['run_ref'], 'codex:CURRENT')
        with self.assertRaisesRegex(ValueError, 'actual run'):
            runtime_schedule.bind(path, 'CURRENT')

    def test_fresh_original_project_window_is_preserved(self):
        path = self.base / 'node-window.json'; window = self.window()
        path.write_text(json.dumps(window))
        config = dict(self.config, node_scheduler={'enabled': True, 'window_file': str(path), 'poll_ms': 60000})
        with mock.patch('sqlite3.connect', side_effect=AssertionError('no native fallback')):
            got = runtime_schedule.snapshot(config, 'node-run', self.at)
        self.assertEqual(got, dict(window, run_ref='node-run'))

    def test_stale_missing_or_invalid_node_window_never_falls_back(self):
        path = self.base / 'node-window.json'
        config = dict(self.config, node_scheduler={'enabled': True, 'window_file': str(path), 'poll_ms': 60000})
        variants = [None, {}, [], dict(self.window(), checked_at='2030-03-01T12:00:00Z'),
                    dict(self.window(), source='OTHER_PLATFORM'), dict(self.window(), status='UNKNOWN'),
                    dict(self.window(), next_run_at='2030-03-01T13:00:00Z'),
                    dict(self.window(), execution_margin_seconds=-1),
                    dict(self.window(), review_not_before=None)]
        with mock.patch('sqlite3.connect', side_effect=AssertionError('no native fallback')):
            for value in variants:
                if value is not None: path.write_text(json.dumps(value))
                got = runtime_schedule.snapshot(config, 'node-run', self.at)
                self.assertEqual(got['status'], 'UNAVAILABLE', value)
                self.assertNotIn('review_not_before', got)
                self.assertNotIn('automation_id', got)

    def test_explicitly_unavailable_project_window_is_not_upgraded(self):
        path = self.base / 'node-window.json'
        window = dict(self.window(), status='UNAVAILABLE', review_not_before=None,
                      reason='Synthetic inactive node, not a completion promise.')
        path.write_text(json.dumps(window))
        got = runtime_schedule.snapshot(dict(self.config, node_scheduler={'enabled': True, 'window_file': str(path)}), 'R', self.at)
        self.assertEqual(got, dict(window, run_ref='R'))

    def test_disabled_node_does_not_read_its_window_or_native_state(self):
        config = dict(self.config, node_scheduler={'enabled': False, 'window_file': 'unused'})
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('no timing file read')):
            self.assertEqual(runtime_schedule.snapshot(config, 'R', self.at)['status'], 'UNAVAILABLE')

    def test_bridge_freezes_unavailable_metadata_through_original_interface(self):
        import node_bridge
        cfg = dict(self.config, project_dir=str(self.base), docker_service={
            'enabled': True, 'fact_bridge_dir': str(self.base / 'facts'),
            'binary': 'SYNTHETIC_DOCKER', 'project': 'SYNTHETIC_PROJECT',
            'env_file': str(self.base / 'env'), 'compose_file': str(self.base / 'compose')})
        response = SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'ok': True, 'result': {'returncode': 0, 'stdout': '{}'}}))
        with mock.patch('node_config.load', return_value=cfg), mock.patch.object(node_bridge, 'sync_facts'), \
             mock.patch('sqlite3.connect', side_effect=AssertionError('no native platform database')), \
             mock.patch.object(node_bridge.subprocess, 'run', return_value=response) as execute, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as done:
                node_bridge.forward_if_enabled(['--project', str(self.base), '--root', str(self.base), 'report-context'])
        self.assertEqual(done.exception.code, 0)
        params = json.loads(execute.call_args.kwargs['input'])['params']
        path = Path(params['argv'][params['argv'].index('--runtime-schedule') + 1])
        window = json.loads(path.read_text())
        self.assertEqual(window['status'], 'UNAVAILABLE')
        self.assertEqual(window['observation_state'], 'NOT_OBSERVED')
        self.assertIn(str(path), params['input_hashes'])


class ReportContextDependencyTests(unittest.TestCase):
    def setUp(self):
        from test_market_reporting import RealContextIntegrationTests
        self.fixture = RealContextIntegrationTests('test_real_context_cli_and_silence_roundtrip_without_mocks')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(runtime_schedule.bind, None, None)
        self.root, self.project = self.fixture.f.store, self.fixture.f.project
        from v4.common import read, replace
        policy = read(self.root / 'write-policy.json')
        policy.update(report_care_revision=1, report_care_activated_at='2030-03-01T13:00:00Z')
        replace(self.root / 'write-policy.json', policy)
        self.restricted = self.project / 'restricted-native'
        self.config = {'codex_state_db': str(self.restricted / 'state.sqlite'),
                       'codex_automations_db': str(self.restricted / 'automations.sqlite')}

    @contextmanager
    def no_platform_reads(self):
        original = sqlite3.connect
        def connect(path, *args, **kwargs):
            if str(self.restricted) in str(path):
                raise AssertionError('forbidden synthetic platform database read')
            return original(path, *args, **kwargs)
        with mock.patch('node_config.load', return_value=self.config), mock.patch('sqlite3.connect', side_effect=connect):
            yield

    def test_real_project_context_keeps_business_clocks_and_unobserved_timing(self):
        from v4 import reporting
        from v4.common import instant
        produced = '2030-03-01T14:40:00Z'
        with self.no_platform_reads(), mock.patch('v4.reporting.now', return_value=produced):
            ctx = reporting.authoring_context(self.root)
        self.assertEqual(ctx['schema'], 'BTC_REPORT_CONTEXT_V1')
        self.assertEqual(ctx['current_strategy']['decision_id'], ctx['identity']['decision_id'])
        self.assertEqual(ctx['observation']['observation_id'], ctx['identity']['observation_id'])
        self.assertEqual(ctx['coverage']['end'], self.fixture.f.raw['cutoff'])
        retrieved = max((r['retrieved_at'] for r in self.fixture.f.raw['retrievals'].values()), key=instant)
        self.assertEqual(ctx['knowledge_cutoff'], retrieved)
        self.assertEqual(ctx['evaluated_at'], produced)
        self.assertLess(instant(ctx['knowledge_cutoff']), instant(ctx['evaluated_at']))
        self.assertEqual(ctx['report_care']['execution_window']['status'], 'UNAVAILABLE')
        self.assertEqual(ctx['report_care']['execution_window']['observation_state'], 'NOT_OBSERVED')
        self.assertFalse(self.restricted.exists())

    def test_missing_observation_still_rejects_context(self):
        from v4 import reporting
        (self.root / 'sensing-current.json').unlink()
        with self.no_platform_reads(), self.assertRaises(FileNotFoundError):
            reporting.authoring_context(self.root)

    def test_observation_identity_time_mismatch_still_rejects_context(self):
        from v4 import reporting
        from v4.common import read, replace, Invalid
        path = self.root / 'sensing-current.json'; pointer = read(path)
        pointer['observed_hour'] = '2030-03-01T10:00:00Z'; replace(path, pointer)
        with self.no_platform_reads(), self.assertRaisesRegex(Invalid, 'sensing pointer mismatch'):
            reporting.authoring_context(self.root)

    def test_real_cli_constructs_context_without_native_platform_access(self):
        import strategy_v4
        output = io.StringIO()
        with self.no_platform_reads(), redirect_stdout(output):
            code = strategy_v4.main(['--project', str(self.project), '--root', str(self.root), 'report-context'])
        self.assertEqual(code, 0)
        ctx = json.loads(output.getvalue())
        self.assertEqual(ctx['report_care']['execution_window']['status'], 'UNAVAILABLE')
        self.assertEqual(ctx['observation']['observation_id'], ctx['identity']['observation_id'])


class TimingGuardTests(unittest.TestCase):
    def setUp(self):
        from test_report_care import ReportCareTests
        self.fixture = ReportCareTests('test_deadline_must_cover_original_next_execution_and_margin')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)

    def test_available_node_window_still_enforces_actual_deadline_floor(self):
        from v4 import reporting
        from v4.common import Invalid
        window = self.fixture.execution_window(); window.pop('automation_id')
        window['source'] = 'BTC_NODE_SCHEDULER_V1'
        with mock.patch('runtime_schedule.current', return_value=window):
            spec = self.fixture.spec('SILENT')
            with self.assertRaisesRegex(Invalid, 'precedes the next executable review'):
                reporting.record(self.fixture.root, spec)
            spec['maintenance']['recheck_at'] = window['review_not_before']
            result = reporting.record(self.fixture.root, spec)
        self.assertEqual(reporting.review(self.fixture.root, result['review_id'])['timing_evidence']['execution_window'], window)

    def test_explicit_unverified_bound_automation_is_not_erased(self):
        from v4 import reporting
        from v4.common import Invalid
        window = dict(self.fixture.execution_window(), status='UNAVAILABLE')
        with mock.patch('runtime_schedule.current', return_value=window):
            with self.assertRaisesRegex(Invalid, 'next execution is unverified'):
                reporting.record(self.fixture.root, self.fixture.spec('SILENT'))
