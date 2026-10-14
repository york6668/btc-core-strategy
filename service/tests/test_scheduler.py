"""Node scheduling contracts use disposable state and physical release pins."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from btc_node import scheduler, tasks, deep_gate
from btc_node.__main__ import scheduler_pulse
from btc_node.codec import Conflict, encode
sys.path.insert(0, str(Path(os.environ.get('BTC_TEST_SOURCE') or Path(__file__).resolve().parents[2]) /
                       'skill/btc-core-strategy/scripts'))
import node_scheduler_worker


class Node:
    def __init__(self, root, mode):
        self.data, self.blobs = {}, {}
        self.settings = {'scheduler_mode': mode, 'test_only': True,
                         'release_root': str(root / 'releases'), 'project': str(root / 'project')}
        self.projections = {}

    def metadata(self, name, value=None, *, write=False):
        if write: self.data[name] = value
        return self.data.get(name)

    def writer(self): return nullcontext()

    def transaction(self): return nullcontext()

    def close(self): pass

    def put_blob(self, value):
        digest = hashlib.sha256(value).hexdigest()
        self.blobs[digest] = value
        return digest

    def value(self, digest):
        return json.loads(self.blobs[digest])

    def one(self, sql, args=()):
        if 'projection_sources' in sql:
            return self.projections.get(args[0])
        raise AssertionError(sql)

    def rows(self, sql, args=()):
        if 'review_requirements' in sql: return []
        raise AssertionError(sql)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        release = self.root / 'releases' / 'release-a'
        release.mkdir(parents=True)
        (release / 'RELEASE.json').write_text('{}')
        (self.root / 'current').symlink_to(release, target_is_directory=True)
        self.clock = 1893500000000
        patches = [patch.object(module, 'millis', lambda *args: self.clock)
                   for module in (scheduler, tasks, deep_gate)]
        for item in patches: item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])
        self.due = patch.object(scheduler.Entities, 'due', return_value={
            'objects': [], 'truncated': False, 'next': None})
        self.due.start()
        self.addCleanup(self.due.stop)

    def receipt(self, db, inserted=1):
        digest = 'a' * 64
        db.projections['receipt-a'] = {'digest': digest, 'outcome': 'READER_PRICES_V1'}
        return {'changed_receipts': [{'document_key': 'receipt-a', 'digest': digest,
                  'outcome': 'READER_PRICES_V1', 'inserted': inserted}]}

    def test_clock_and_same_value_do_not_start_model(self):
        db = Node(self.root, 'SHADOW')
        self.assertEqual('NO_VALUE_INCREMENT', scheduler.observe_reader(db, self.receipt(db, 0))['status'])
        self.assertEqual([], scheduler.tick(db)['tasks'])
        self.assertEqual('NO_LAUNCH_IN_SHADOW_OR_OFF', scheduler.claim(db, attempt_id='attempt-a')['action'])
        self.assertEqual([], tasks.status(db)['tasks'])

    def test_due_scan_does_not_take_over_research_or_delivery_owners(self):
        db = Node(self.root, 'SHADOW')
        due = {'objects': [{'kind': 'RESEARCH'}, {'kind': 'DELIVERY'}],
               'truncated': False, 'next': None}
        with patch.object(scheduler.Entities, 'due', return_value=due):
            self.assertEqual(0, scheduler.tick(db)['due_registered'])
        self.assertEqual([], tasks.status(db)['tasks'])

    def test_bounded_due_scan_rotates_public_and_personal_cursors(self):
        db = Node(self.root, 'SHADOW')
        state = scheduler._state(db)
        scopes = []
        def due(*, scope, **kwargs):
            scopes.append(scope)
            return {'objects': [{'kind': 'DELIVERY'}], 'truncated': False, 'next': None}
        with patch.object(scheduler.Entities, 'due', side_effect=due):
            scheduler._register_due(db, state, 'release-a', limit=1)
            scheduler._register_due(db, state, 'release-a', limit=1)
        self.assertEqual(['PUBLIC', 'PERSONAL'], scopes)

    def test_real_increment_survives_restart_claims_once_and_holds_other_work(self):
        db = Node(self.root, 'SHADOW')
        queued = scheduler.observe_reader(db, self.receipt(db))
        self.assertEqual('QUEUED', queued['status'])
        self.assertEqual('ALREADY_TRACKED', scheduler.observe_reader(db, self.receipt(db))['status'])
        ticked = scheduler.tick(db)
        self.assertEqual((1, 'READY'), (ticked['reader_registered'], ticked['tasks'][0]['state']))
        self.assertEqual(0, scheduler.tick(db)['reader_registered'])
        # A second process sees the same persisted metadata, not a fresh window.
        restarted = Node(self.root, 'ACTIVE')
        restarted.data, restarted.blobs, restarted.projections = db.data, db.blobs, db.projections
        first = scheduler.claim(restarted, attempt_id='attempt-a')
        self.assertEqual('LAUNCH_ONCE', first['action'])
        self.assertEqual('ORIGINAL_ATTEMPT_RUNNING_NO_LAUNCH',
                         scheduler.claim(restarted, attempt_id='attempt-b')['action'])
        self.assertEqual(1, tasks.status(restarted, task_id=first['task']['task_id'])['attempts'])

    def test_missing_release_blocks_without_rebinding_old_task(self):
        db = Node(self.root, 'SHADOW')
        scheduler.observe_reader(db, self.receipt(db))
        original = scheduler.tick(db)['tasks'][0]['task_id']
        newer = self.root / 'releases' / 'release-b'
        newer.mkdir(); (newer / 'RELEASE.json').write_text('{}')
        (self.root / 'current').unlink()
        (self.root / 'current').symlink_to(newer, target_is_directory=True)
        self.assertEqual([], scheduler.tick(db)['tasks'])
        self.assertEqual('release-a', tasks.status(db, task_id=original)['release_id'])
        (self.root / 'current').unlink()
        self.assertEqual('BLOCKED_RELEASE_UNAVAILABLE', scheduler.tick(db)['action'])

    def test_host_worker_claims_once_and_locks_ambiguous_result_across_restart(self):
        db = Node(self.root, 'ACTIVE')
        scheduler.observe_reader(db, self.receipt(db))
        self.assertEqual('NO_ACTIVE_NODE_SCHEDULE', node_scheduler_worker.run_once(
            {'node_scheduler': {'enabled': True, 'attempt_dir': str(self.root / 'empty-attempts'),
                                'codex_cli': '/no/cli'}},
            type('Idle', (), {'request': lambda _, method: scheduler.window(db)})())['action'])
        scheduler.tick(db)

        class Client:
            def request(self, method, params=None, operation_id=None):
                if method == 'schedule.window': return scheduler.window(db)
                if method == 'schedule.claim': return scheduler.claim(db, **params)
                if method == 'task.finish': return tasks.finish(db, **params)
                raise AssertionError(method)

        attempts = self.root / 'attempts'
        attempts.mkdir(mode=0o700)
        config = {'node_scheduler': {'enabled': True, 'attempt_dir': str(attempts),
                                     'codex_cli': '/no/cli'}}
        calls = []

        def uncertain(config, task, directory):
            calls.append(task['attempt_id'])
            return {'outcome': 'UNKNOWN', 'kind': 'RESULT_UNVERIFIED', 'detail': 'no proof'}

        result = node_scheduler_worker.run_once(config, Client(), uncertain)
        self.assertEqual('BLOCKED', result['action'])
        self.assertEqual(1, len(calls))
        self.assertEqual('NO_READY_TASK', node_scheduler_worker.run_once(config, Client(), uncertain)['action'])
        self.assertEqual(1, len(calls))
        self.assertEqual('UNKNOWN_OUTCOME', tasks.status(db)['tasks'][0]['blocker'])

    def test_worker_disabled_has_no_node_side_effect(self):
        self.assertEqual({'action': 'DISABLED'}, node_scheduler_worker.run_once({}))

    def test_native_session_requires_one_completed_codex_thread(self):
        events=self.root/'events.jsonl'
        events.write_text('{"type":"thread.started","thread_id":"synthetic-thread-1"}\n')
        self.assertIsNone(node_scheduler_worker._native_session(events))
        with events.open('a') as output:
            output.write('{"type":"turn.completed"}\n')
        self.assertEqual('synthetic-thread-1',node_scheduler_worker._native_session(events))

    def test_isolated_worker_cli_replay_requires_actual_verified_sensing_artifact(self):
        db = Node(self.root, 'ACTIVE')
        scheduler.observe_reader(db, self.receipt(db))
        scheduler.tick(db)

        class Client:
            def request(self, method, params=None, operation_id=None):
                if method == 'schedule.window': return scheduler.window(db)
                if method == 'schedule.claim': return scheduler.claim(db, **params)
                if method == 'task.finish': return tasks.finish(db, **params)
                raise AssertionError(method)

        cli = self.root / 'fake-codex-cli'
        cli.write_text('''#!/usr/bin/env python3
import hashlib, json, pathlib, sys
lines = sys.stdin.read().splitlines()
task = next(json.loads(line) for line in lines if line.startswith('{"attempt_id":'))
root = pathlib.Path(sys.argv[sys.argv.index('-C')+1]) / '.strategy/v4'
folder = root / 'sensing'; folder.mkdir(parents=True, exist_ok=True)
value = {'created_at': '2030-01-01T00:00:00+00:00', 'main_eligible': False,
         'research_eligible': False}
raw = (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\\n').encode()
identity = 'sha256:' + hashlib.sha256(raw).hexdigest()
value['observation_id'] = identity
(folder / (identity[7:]+'.json')).write_text(json.dumps(value))
(root / 'sensing-current.json').write_text(json.dumps({'observation_id': identity}))
pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps({
    'task_id':task['task_id'], 'attempt_id':task['attempt_id'], 'completion_ref':identity}))
print(json.dumps({'type':'thread.started','thread_id':'synthetic-thread-1'}))
print(json.dumps({'type':'turn.completed'}))
''')
        cli.chmod(0o700)
        (self.root / 'project').mkdir()
        attempts = self.root / 'attempts'
        attempts.mkdir(mode=0o700)
        config = {'release_root': str(self.root),
                  'project_dir': str(self.root / 'project'),
                  'node_scheduler': {'enabled': True, 'attempt_dir': str(attempts),
                                     'codex_cli': str(cli),
                                     'window_file': str(self.root / 'node-window.json')}}
        result = node_scheduler_worker.run_once(config, Client())
        self.assertEqual('COMPLETED', result['action'])
        self.assertEqual('gpt-5.6-sol', result['model'])
        trace=json.loads(next(attempts.glob('node-*/result.json')).read_text())
        self.assertEqual('synthetic-thread-1',trace['native_thread_id'])
        self.assertEqual('BTC_NODE_SCHEDULER_V1',
                         json.loads((self.root / 'node-window.json').read_text())['source'])
        self.assertEqual('NO_READY_TASK', node_scheduler_worker.run_once(config, Client())['action'])

    def test_two_host_invocations_admit_only_one_original_attempt(self):
        db = Node(self.root, 'ACTIVE')
        scheduler.observe_reader(db, self.receipt(db))
        scheduler.tick(db)
        class Client:
            def request(self, method, params=None, operation_id=None):
                if method == 'schedule.window': return scheduler.window(db)
                if method == 'schedule.claim': return scheduler.claim(db, **params)
                if method == 'task.finish': return tasks.finish(db, **params)
                raise AssertionError(method)
        attempts = self.root / 'attempts'
        attempts.mkdir(mode=0o700)
        config = {'node_scheduler': {'enabled': True, 'attempt_dir': str(attempts),
                                     'codex_cli': '/no/cli'}}
        entered, release = threading.Event(), threading.Event()
        def launcher(config, task, directory):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'outcome': 'UNKNOWN', 'kind': 'RESULT_UNVERIFIED', 'detail': 'no proof'}
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(node_scheduler_worker.run_once, config, Client(), launcher)
            self.assertTrue(entered.wait(5))
            second = pool.submit(node_scheduler_worker.run_once, config, Client(), launcher)
            self.assertEqual('WORKER_ALREADY_RUNNING', second.result(5)['action'])
            release.set()
            self.assertEqual('BLOCKED', first.result(5)['action'])
        self.assertEqual(1, tasks.status(db)['tasks'][0]['attempts'])

    def test_node_pulse_is_the_only_due_clock_and_fault_blocks_worker_window(self):
        db = Node(self.root, 'ACTIVE')
        scheduler.observe_reader(db, self.receipt(db))
        self.assertEqual('TICKED', scheduler_pulse(db.settings, lambda _: db)['action'])
        self.assertEqual('AVAILABLE', scheduler.window(db)['status'])
        signature = 'sha256:' + 'a' * 64
        deep_gate.open_fault(db, scope='btc/scheduler', kind='UNKNOWN',
                             signature=signature, fault_digest=signature,
                             evidence_ref='scheduler-loop/stderr/test')
        self.assertEqual('BLOCKED_SCHEDULER_FAULT',
                         scheduler_pulse(db.settings, lambda _: db)['action'])
        self.assertEqual('UNAVAILABLE', scheduler.window(db)['status'])
        self.assertIn('defect', scheduler.window(db)['reason'])
        self.assertEqual('NODE_CLOCK_UNAVAILABLE_NO_LAUNCH',
                         scheduler.claim(db, attempt_id='blocked-attempt')['action'])
        self.assertEqual(0, tasks.status(db)['tasks'][0]['attempts'])


if __name__ == '__main__': unittest.main()
