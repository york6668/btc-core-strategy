"""Synthetic checks for the shared node execution admission boundary."""
import unittest
from unittest.mock import patch

from btc_node import deep_gate, scheduler, tasks
from btc_node.codec import Conflict, encode
import test_scheduler as fixture


class AdmissionBoundaryTests(unittest.TestCase):
    setUp = fixture.SchedulerTests.setUp
    receipt = fixture.SchedulerTests.receipt

    def ready_signal(self):
        db = fixture.Node(self.root, 'ACTIVE')
        scheduler.observe_reader(db, self.receipt(db))
        scheduler.tick(db)
        return db, tasks.status(db)['tasks'][0]

    def test_new_scope_fault_blocks_client_and_scheduler_but_not_other_scope(self):
        db, original = self.ready_signal()
        deep_gate.open_fault(db, scope='btc/market', kind='DETERMINISTIC',
            signature='sha256:'+'e'*64, fault_digest='sha256:'+'f'*64,
            evidence_ref='synthetic-current-defect')
        with self.assertRaisesRegex(Conflict, 'affected task scope'):
            tasks.start(db, task_id=original['task_id'], attempt_id='client-attempt',
                        release_id='release-a')
        self.assertEqual(0, tasks.status(db, task_id=original['task_id'])['attempts'])
        # A distinct ready obligation retains its own scope and source proof.
        tasks.submit(db, task_id='other-scope-signal', issue_id='OTHER_SIGNAL',
            kind='SIGNAL', phase='PUBLIC', input_digest=original['input_digest'],
            release_id='release-a', evidence_ref=original['evidence_ref'],
            reason='Check the synthetic independent report source now.',
            reason_kind='ACTUAL_READER_INCREMENT', scope='btc/report', due_ms=self.clock)
        tasks.assess(db, task_id='other-scope-signal', dependency_ready=True,
                     dependency_ref=original['evidence_ref'])
        claim = scheduler.claim(db, attempt_id='unaffected-attempt')
        self.assertEqual(('LAUNCH_ONCE', 'other-scope-signal'),
                         (claim['action'], claim['task']['task_id']))
        blocked = tasks.status(db, task_id=original['task_id'])
        self.assertEqual(('BLOCKED', 'OPEN_DEFECT', 0),
                         (blocked['state'], blocked['blocker'], blocked['attempts']))

    def test_original_due_subject_superseded_after_assessment_cannot_start(self):
        db = fixture.Node(self.root, 'ACTIVE')
        scheduler.tick(db)
        subject = {'schema': 'BTC_DUE_SUBJECT_V1', 'kind': 'HYPOTHESIS',
                   'object_id': 'original', 'version': 1, 'scope': 'PUBLIC',
                   'due_ms': self.clock, 'open_requirements': []}
        reference = 'sha256:' + db.put_blob(encode(subject))
        tasks.submit(db, task_id='btc/recheck/original', issue_id='HYPOTHESIS/original',
            kind='RECHECK', phase='PUBLIC', input_digest=reference,
            release_id='release-a', evidence_ref=reference,
            reason='Recheck the synthetic original hypothesis version.',
            reason_kind='ORIGINAL_DUE', scope='btc/market', due_ms=self.clock)
        tasks.assess(db, task_id='btc/recheck/original', dependency_ready=True,
                     dependency_ref=reference)
        with patch.object(scheduler.Entities, 'get', return_value={'version': 2}):
            with self.assertRaisesRegex(Conflict, 'dependency changed'):
                tasks.start(db, task_id='btc/recheck/original', attempt_id='client-stale',
                            release_id='release-a')
            self.assertEqual('NO_READY_TASK', scheduler.claim(db, attempt_id='node-stale')['action'])
        task = tasks.status(db, task_id='btc/recheck/original')
        self.assertEqual(('BLOCKED', 'DEPENDENCY_UNREADY', 0, reference),
                         (task['state'], task['blocker'], task['attempts'], task['evidence_ref']))

    def test_missing_original_reader_blob_is_a_blocked_dependency(self):
        db, original = self.ready_signal()
        del db.blobs[original['evidence_ref'][7:]]
        with self.assertRaisesRegex(Conflict, 'dependency changed'):
            tasks.start(db, task_id=original['task_id'], attempt_id='client-missing-source',
                        release_id='release-a')
        self.assertEqual('NO_READY_TASK', scheduler.claim(db, attempt_id='node-missing-source')['action'])
        task = tasks.status(db, task_id=original['task_id'])
        self.assertEqual(('BLOCKED', 'DEPENDENCY_UNREADY', 0),
                         (task['state'], task['blocker'], task['attempts']))

    def test_ordinary_task_cannot_overlap_retest_and_scheduler_fault_still_blocks(self):
        db, first = self.ready_signal()
        tasks.submit(db, task_id='synthetic-retest', issue_id='synthetic-retest',
            kind='RETEST', phase='PUBLIC', input_digest='sha256:'+'a'*64,
            release_id='release-a', evidence_ref='synthetic-retest-input',
            reason='Run an isolated synthetic local retest obligation.',
            reason_kind='ISOLATED_RETEST', scope='btc/retest', due_ms=self.clock,
            namespace='retest')
        tasks.assess(db, task_id='synthetic-retest', dependency_ready=True,
                     dependency_ref='synthetic-retest-proof')
        tasks.start(db, task_id=first['task_id'], attempt_id='formal-original',
                    release_id='release-a')
        with self.assertRaisesRegex(Conflict, 'another original task'):
            tasks.start(db, task_id='synthetic-retest', attempt_id='parallel-retest',
                        release_id='release-a')
        self.assertEqual(0, tasks.status(db, task_id='synthetic-retest')['attempts'])
        self.assertEqual('CLAIMED_NEW', deep_gate.claim(db,
            claim_id='parallel-deep-claim', issue_id='parallel-deep',
            scope='btc/research', phase='PUBLIC', input_digest='sha256:'+'b'*64,
            model='gpt-6-astra')['action'])

        separate, second = self.ready_signal()
        deep_gate.open_fault(separate, scope='btc/scheduler', kind='DETERMINISTIC',
            signature='sha256:'+'d'*64, fault_digest='sha256:'+'c'*64,
            evidence_ref='synthetic-scheduler-defect')
        with self.assertRaisesRegex(Conflict, 'scheduler defect'):
            tasks.start(separate, task_id=second['task_id'], attempt_id='client-bypass',
                        release_id='release-a')
        self.assertEqual(0, tasks.status(separate, task_id=second['task_id'])['attempts'])

    def test_direct_deep_then_sol_claim_and_exact_completed_reuse(self):
        db, original = self.ready_signal()
        params = dict(issue_id='original-research', scope='btc/research',
                      phase='PUBLIC', input_digest='sha256:'+'c'*64,
                      model='gpt-6-astra')
        self.assertEqual('CLAIMED_NEW', deep_gate.claim(
            db, claim_id='deep-first', **params)['action'])
        self.assertEqual('LAUNCH_ONCE', scheduler.claim(
            db, attempt_id='sol-while-deep-in-flight')['action'])
        deep_gate.finish(db, claim_id='deep-first', outcome='COMPLETE',
                         result_ref='original-frozen-research')
        before = deep_gate.status(db)['days']
        self.assertEqual('REUSE', deep_gate.claim(
            db, claim_id='read-existing-result', **params)['action'])
        self.assertEqual(before, deep_gate.status(db)['days'])
        self.assertEqual(1, tasks.status(db, task_id=original['task_id'])['attempts'])

    def test_sol_then_direct_deep_and_only_one_ordinary_attempt(self):
        db, original = self.ready_signal()
        tasks.start(db, task_id=original['task_id'], attempt_id='sol-first',
                    release_id='release-a')
        self.assertEqual('CLAIMED_NEW', deep_gate.claim(db,
            claim_id='deep-while-sol', issue_id='original-research',
            scope='btc/research', phase='PUBLIC', input_digest='sha256:'+'c'*64,
            model='gpt-6-astra')['action'])
        tasks.submit(db, task_id='second-ordinary', issue_id='second-ordinary',
            kind='RECHECK', phase='PUBLIC', input_digest='sha256:'+'d'*64,
            release_id='release-a', evidence_ref='synthetic-original',
            reason='Review a second synthetic ordinary obligation.',
            reason_kind='ORIGINAL_DUE', scope='btc/market', due_ms=self.clock)
        tasks.assess(db, task_id='second-ordinary', dependency_ready=True,
                     dependency_ref='synthetic-proof')
        with self.assertRaisesRegex(Conflict, 'execution lane'):
            tasks.start(db, task_id='second-ordinary', attempt_id='second-sol',
                        release_id='release-a')
        self.assertEqual('ORIGINAL_ATTEMPT_RUNNING_NO_LAUNCH',
                         scheduler.claim(db, attempt_id='second-node-sol')['action'])
        with self.assertRaises(Conflict):
            deep_gate.claim(db, claim_id='second-deep', issue_id='another-research',
                scope='btc/research', phase='PUBLIC', input_digest='sha256:'+'e'*64,
                model='gpt-6-astra')
        self.assertEqual(0, tasks.status(db, task_id='second-ordinary')['attempts'])

    def test_deep_task_and_sol_task_overlap_in_both_orders(self):
        for first_kind in ('DEEP', 'SIGNAL'):
            with self.subTest(first_kind=first_kind):
                db, signal = self.ready_signal()
                tasks.submit(db, task_id='manual-deep', issue_id='manual-deep',
                    kind='DEEP', phase='PUBLIC', input_digest='sha256:'+'e'*64,
                    release_id='release-a', evidence_ref='synthetic-research-request',
                    reason='Review a separate synthetic material research request.',
                    reason_kind='NEW_KEY_EVIDENCE', scope='btc/research', due_ms=self.clock)
                tasks.assess(db, task_id='manual-deep', dependency_ready=True,
                             dependency_ref='synthetic-research-proof')
                def start_deep():
                    return tasks.start(db, task_id='manual-deep',
                        attempt_id='deep-task', release_id='release-a',
                        claim_id='deep-task-claim', model='gpt-6-astra')
                if first_kind == 'DEEP':
                    self.assertEqual('LAUNCH_ONCE', start_deep()['action'])
                    self.assertEqual('LAUNCH_ONCE', scheduler.claim(
                        db, attempt_id='sol-after-deep-task')['action'])
                else:
                    tasks.start(db, task_id=signal['task_id'],
                                attempt_id='sol-before-deep-task', release_id='release-a')
                    self.assertEqual('LAUNCH_ONCE', start_deep()['action'])
                self.assertEqual({'DEEP', 'SIGNAL'}, {
                    row['kind'] for row in tasks.status(db)['tasks'] if row['state'] == 'RUNNING'})
                self.assertEqual(1, len(deep_gate.status(db)['days']))

    def test_deep_request_progress_change_cannot_consume_day_slot(self):
        db = fixture.Node(self.root, 'ACTIVE')
        db.settings['scheduler_deep_enabled'] = True
        scheduler.tick(db)
        request = 'sha256:'+'a'*64
        tasks.submit(db, task_id='btc/research/isolated/independent',
            issue_id='isolated-research', kind='DEEP', phase='PUBLIC',
            input_digest='sha256:'+'b'*64, release_id='release-a',
            evidence_ref=request, reason='Review a synthetic material contradiction.',
            reason_kind='NEW_KEY_EVIDENCE', scope='btc/research', due_ms=self.clock)
        tasks.assess(db, task_id='btc/research/isolated/independent',
                     dependency_ready=True, dependency_ref=request)
        no_longer_requested = {'payload': {'status': 'COMPLETE',
            'latest_request': {'request_id': request,
                               'progress': {'stage': 'COMPLETE'}}}}
        with patch.object(scheduler.Entities, 'get', return_value=no_longer_requested):
            with self.assertRaisesRegex(Conflict, 'dependency changed'):
                tasks.start(db, task_id='btc/research/isolated/independent',
                    attempt_id='client-stale-deep', release_id='release-a',
                    claim_id='client-stale-deep-claim', model='gpt-6-astra')
            self.assertEqual('NO_READY_TASK', scheduler.claim(db, attempt_id='node-stale-deep')['action'])
        self.assertEqual({}, deep_gate.status(db)['days'])

    def test_main_source_replacement_cannot_launch_old_obligation(self):
        db = fixture.Node(self.root, 'ACTIVE')
        scheduler.tick(db)
        tasks.submit(db, task_id='btc/portfolio-synthesis/main',
            issue_id='portfolio-synthesis', kind='MAIN', phase='PERSONAL',
            input_digest='sha256:'+'c'*64, release_id='release-a',
            evidence_ref='old-formal-source',
            reason='Resolve the synthetic unfinished whole-portfolio decision.',
            reason_kind='UNFINISHED_FORMAL_DECISION', scope='btc/research', due_ms=self.clock)
        tasks.assess(db, task_id='btc/portfolio-synthesis/main',
                     dependency_ready=True, dependency_ref='old-formal-source')
        changed = {'source_ref': 'new-formal-source', 'payload': {
            'investment_completion': 'NOT_FORMED_IN_FORMAL_REVISION',
            'market_binding': {'object_id': 'frozen-market'}}}
        with patch.object(scheduler.Entities, 'get', return_value=changed):
            with self.assertRaisesRegex(Conflict, 'dependency changed'):
                tasks.start(db, task_id='btc/portfolio-synthesis/main',
                            attempt_id='client-stale-main', release_id='release-a')
            self.assertEqual('NO_READY_TASK', scheduler.claim(db, attempt_id='node-stale-main')['action'])
        self.assertEqual(0, tasks.status(db, task_id='btc/portfolio-synthesis/main')['attempts'])


if __name__ == '__main__':
    unittest.main()
