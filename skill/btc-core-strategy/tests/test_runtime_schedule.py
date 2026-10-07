"""Native timing reads use isolated metadata and synthetic lifecycle events."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import runtime_schedule


class RuntimeScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(runtime_schedule.bind, None, None)
        self.base = Path(self.tmp.name)
        self.state = self.base / 'state.sqlite'
        self.db = self.base / 'sqlite/codex-dev.db'
        self.db.parent.mkdir()
        self.config = {'codex_state_db': str(self.state)}
        self.at = datetime(2030, 3, 1, 13, 5, tzinfo=timezone.utc)
        self.next_ms = int(datetime(2030, 3, 1, 13, 40, tzinfo=timezone.utc).timestamp() * 1000)
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE automations(id,status,rrule,next_run_at,next_run_nominal_at)')
            c.execute('CREATE TABLE automation_runs(thread_id,automation_id,created_at)')
            c.execute('INSERT INTO automations VALUES(?,?,?,?,?)', ('SYNTHETIC_TASK','ACTIVE','RRULE:FREQ=HOURLY',self.next_ms,self.next_ms+60000))
            c.executemany('INSERT INTO automation_runs VALUES(?,?,?)', [('CURRENT','SYNTHETIC_TASK',3),('PRIOR','SYNTHETIC_TASK',2),('UNRELATED','OTHER_TASK',4)])
        self.rollout = self.base / 'native.jsonl'
        self.rollout.write_text('\n'.join(json.dumps(x) for x in [
            {'timestamp':'2030-03-01T12:00:00Z','payload':{'type':'task_started'}},
            {'timestamp':'2030-03-01T12:01:00Z','payload':{'type':'message','text':'SYNTHETIC_PRIVATE_CONTENT_NOT_FOR_EXPORT'}},
            {'timestamp':'2030-03-01T12:18:30Z','payload':{'type':'task_complete'}}])+'\n')
        with sqlite3.connect(self.state) as c:
            c.execute('CREATE TABLE threads(id,rollout_path)')
            c.execute('INSERT INTO threads VALUES(?,?)', ('PRIOR',str(self.rollout)))

    def test_original_task_next_start_and_native_duration_are_read_only(self):
        before = {p: p.read_bytes() for p in (self.db,self.state,self.rollout)}
        got = runtime_schedule.snapshot(self.config, 'CURRENT', self.at)
        self.assertEqual(got['status'], 'AVAILABLE')
        self.assertEqual(got['execution_margin_seconds'], 1170)
        self.assertEqual(got['review_not_before'], '2030-03-01T13:59:30+00:00')
        self.assertEqual([x['run_ref'] for x in got['recent_completed_runs']], ['PRIOR'])
        self.assertNotIn('SYNTHETIC_PRIVATE_CONTENT_NOT_FOR_EXPORT', json.dumps(got))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_unknown_run_and_missing_database_do_not_invent_schedule(self):
        self.assertEqual(runtime_schedule.snapshot(self.config, 'UNKNOWN', self.at)['status'], 'NOT_SCHEDULED')
        target = self.base / 'absent.db'
        got = runtime_schedule.snapshot(dict(self.config, codex_automations_db=str(target)), 'CURRENT', self.at)
        self.assertEqual(got['status'], 'UNAVAILABLE')
        self.assertNotIn('review_not_before', got)
        self.assertFalse(target.exists())

    def test_prefixed_reference_finds_owner_preserves_binding_and_excludes_current(self):
        # A resumed current thread may already contain a completed turn. It
        # must not become its own execution-margin sample after normalization.
        current_rollout = self.base / 'current.jsonl'
        current_rollout.write_text('\n'.join(json.dumps(x) for x in [
            {'timestamp':'2030-03-01T10:00:00Z','payload':{'type':'task_started'}},
            {'timestamp':'2030-03-01T12:00:00Z','payload':{'type':'task_complete'}}])+'\n')
        with sqlite3.connect(self.state) as c:
            c.execute('INSERT INTO threads VALUES(?,?)', ('CURRENT',str(current_rollout)))
        before = {p: p.read_bytes() for p in (self.db,self.state,self.rollout,current_rollout)}
        native = runtime_schedule.snapshot(self.config, 'CURRENT', self.at)
        got = runtime_schedule.snapshot(self.config, 'codex:CURRENT', self.at)
        self.assertEqual(got, dict(native, run_ref='codex:CURRENT'))
        self.assertEqual(got['status'], 'AVAILABLE')
        self.assertEqual(got['automation_id'], 'SYNTHETIC_TASK')
        self.assertEqual([x['run_ref'] for x in got['recent_completed_runs']], ['PRIOR'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        path = self.base / 'prefixed-snapshot.json'
        path.write_text(json.dumps(got))
        runtime_schedule.bind(path, 'codex:CURRENT')
        self.assertEqual(runtime_schedule.current(), got)
        with self.assertRaisesRegex(ValueError, 'actual run'):
            runtime_schedule.bind(path, 'CURRENT')
        self.assertEqual(runtime_schedule.snapshot(self.config, 'codex:UNKNOWN', self.at)['status'], 'NOT_SCHEDULED')

    def test_due_or_paused_start_is_not_a_future_completion_promise(self):
        with sqlite3.connect(self.db) as c:
            c.execute('UPDATE automations SET next_run_at=0')
        got = runtime_schedule.snapshot(self.config,'CURRENT',self.at)
        self.assertEqual(got['status'],'UNAVAILABLE')
        self.assertNotIn('review_not_before',got)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE automations SET status='PAUSED'")
        self.assertEqual(runtime_schedule.snapshot(self.config,'CURRENT',self.at)['status'],'UNAVAILABLE')

    def test_incomplete_turn_is_not_a_duration_sample(self):
        self.rollout.write_text(json.dumps({'timestamp':'2030-03-01T12:00:00Z','payload':{'type':'task_started'}})+'\n')
        got = runtime_schedule.snapshot(self.config,'CURRENT',self.at)
        self.assertEqual(got['status'],'UNAVAILABLE')
        self.assertEqual(got['recent_completed_runs'],[])

    def test_older_scheduler_without_nominal_column_uses_actual_plan(self):
        with sqlite3.connect(self.db) as c:
            c.execute('ALTER TABLE automations RENAME TO old_automations')
            c.execute('CREATE TABLE automations AS SELECT id,status,rrule,next_run_at FROM old_automations')
        got=runtime_schedule.snapshot(self.config,'CURRENT',self.at)
        self.assertEqual(got['status'],'AVAILABLE')
        self.assertIsNone(got['next_run_nominal_at'])

    def test_frozen_bridge_snapshot_keeps_actual_run_identity(self):
        path=self.base/'snapshot.json'
        value=runtime_schedule.snapshot(self.config,'CURRENT',self.at)
        path.write_text(json.dumps(value))
        runtime_schedule.bind(path,'CURRENT')
        self.assertEqual(runtime_schedule.current(),value)
        runtime_schedule.bind(path,None)  # Context reads have no authored provenance yet.
        self.assertEqual(runtime_schedule.current()['run_ref'],'CURRENT')
        with self.assertRaisesRegex(ValueError,'actual run'):
            runtime_schedule.bind(path,'OTHER')
