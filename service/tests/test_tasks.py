"""Execution-state tests use a simulated node authority, never live reports."""
import hashlib
import tempfile
import unittest
from unittest.mock import patch

from btc_node import deep_gate, tasks
from btc_node.codec import Conflict


def digest(text):
    return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()


class Node:
    def __init__(self):
        self.data = {}

    def metadata(self, name, value=None, *, write=False):
        if write:
            self.data[name] = value
        return self.data.get(name)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.db = Node()
        self.clock = 1791940000000
        self.patchers = [patch.object(tasks, 'millis', lambda: self.clock),
                         patch.object(deep_gate, 'millis', lambda: self.clock)]
        for item in self.patchers: item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patchers)])

    def submit(self, **changes):
        fields = dict(task_id='btc-cycle/public', issue_id='BTC_CYCLE', kind='RECHECK',
                      phase='PUBLIC', input_digest=digest('frozen-input'), release_id='a1577cc6',
                      evidence_ref='evidence/original', reason='Examine whether the old thesis still holds.',
                      reason_kind='DUE_VERIFICATION', scope='btc/market', due_ms=self.clock,
                      namespace='formal')
        fields.update(changes)
        return tasks.submit(self.db, **fields)

    def test_no_increment_is_reused_and_need_is_not_ready(self):
        first = self.submit()['task']
        self.assertEqual('NEEDED', first['state'])
        self.assertEqual('ALREADY_TRACKED', self.submit()['action'])
        blocked = tasks.assess(self.db, task_id=first['task_id'], dependency_ready=False,
                               dependency_ref='original-research-incomplete')['task']
        self.assertEqual(('BLOCKED','DEPENDENCY_UNREADY'), (blocked['state'], blocked['blocker']))
        with self.assertRaisesRegex(Conflict, 'not ready'):
            tasks.start(self.db, task_id=first['task_id'], attempt_id='attempt-a', release_id='a1577cc6')
        ready = tasks.assess(self.db, task_id=first['task_id'], dependency_ready=True,
                             dependency_ref='original-result-verified')['task']
        self.assertEqual('READY', ready['state'])
        started = tasks.start(self.db, task_id=first['task_id'], attempt_id='attempt-a',
                              release_id='a1577cc6')['task']
        self.assertEqual(1, started['attempts'])
        with self.assertRaisesRegex(Conflict, 'not ready'):
            tasks.start(self.db, task_id=first['task_id'], attempt_id='attempt-b', release_id='a1577cc6')
        with patch.object(tasks, '_verified_completion'):
            tasks.finish(self.db, task_id=first['task_id'], attempt_id='attempt-a',
                         outcome='COMPLETE', completion_ref='actual-review-artifact')
        self.assertEqual('ALREADY_TRACKED', self.submit()['action'])
        self.assertEqual('COMPLETED', tasks.status(self.db, task_id=first['task_id'])['state'])

    def test_uninstalled_pinned_release_cannot_start(self):
        task = self.submit()['task']
        tasks.assess(self.db, task_id=task['task_id'], dependency_ready=True,
                     dependency_ref='original-result-verified')
        with tempfile.TemporaryDirectory() as directory:
            self.db.settings={'release_root':directory}
            with self.assertRaisesRegex(Conflict, 'installed physical release'):
                tasks.start(self.db, task_id=task['task_id'], attempt_id='attempt-a',
                            release_id='a1577cc6')

    def test_real_change_reopens_same_issue_without_changing_history(self):
        self.submit()
        new = self.submit(input_digest=digest('new-evidence'),
                          evidence_ref='evidence/new-material')['task']
        self.assertEqual(2, new['revision'])
        self.assertEqual('NEEDED', new['state'])
        self.assertEqual(digest('frozen-input'), new['history'][0]['input_digest'])
        with self.assertRaisesRegex(Conflict, 'another problem'):
            self.submit(issue_id='DIFFERENT_ISSUE')

    def test_expiry_alone_cannot_become_deep_and_retest_cannot_deliver(self):
        with self.assertRaisesRegex(ValueError, 'substantive question'):
            self.submit(kind='DEEP', reason_kind='DUE_VERIFICATION')
        with self.assertRaisesRegex(ValueError, 'substantive question'):
            self.submit(kind='DEEP', reason_kind='REOPEN_CONDITION_MET')
        with self.assertRaisesRegex(Conflict, 'retest cannot'):
            self.submit(kind='REPORT', namespace='retest')

    def test_completed_portfolio_does_not_calendar_block_approval_task(self):
        original=deep_gate.claim(self.db,claim_id='prior-portfolio',issue_id='BTC_CYCLE',
            scope='btc/research',phase='PORTFOLIO',input_digest=digest('portfolio input'),model='gpt-6-astra')
        deep_gate.finish(self.db,claim_id='prior-portfolio',outcome='COMPLETE',result_ref=digest('portfolio proof'))
        task=self.submit(kind='DEEP',phase='APPROVAL',reason_kind='MATERIAL_COUNTEREVIDENCE',
            scope='btc/research',task_id='btc-cycle/approval')['task']
        ready=tasks.assess(self.db,task_id=task['task_id'],dependency_ready=True,dependency_ref='actual candidate')
        self.assertEqual('READY',ready['action'])
        started=tasks.start(self.db,task_id=task['task_id'],attempt_id='approval-attempt',
            release_id='a1577cc6',claim_id='approval-call',model='gpt-6-astra')
        self.assertEqual('LAUNCH_ONCE',started['action'])
        self.assertEqual(original['claim']['day'],deep_gate.status(self.db)['claims']['approval-call']['day'])

    def test_deep_claim_identity_and_failure_blocks(self):
        row = self.submit(kind='DEEP', reason_kind='MATERIAL_COUNTEREVIDENCE',
                          scope='btc/research', task_id='btc-cycle/deep')['task']
        tasks.assess(self.db, task_id=row['task_id'], dependency_ready=True,
                     dependency_ref='material-original-evidence')
        tasks.start(self.db, task_id=row['task_id'], attempt_id='deep-attempt',
                    release_id='a1577cc6', claim_id='deep-claim-1', model='gpt-6-astra')
        with self.assertRaisesRegex(Conflict, 'in-flight'):
            self.submit(kind='DEEP', reason_kind='MATERIAL_COUNTEREVIDENCE',
                        scope='btc/research', task_id='btc-cycle/deep',
                        input_digest=digest('another-hour'))
        tasks.finish(self.db, task_id=row['task_id'], attempt_id='deep-attempt',
                     outcome='FAILED', completion_ref='failed-original-call',
                     failure_kind='DETERMINISTIC', failure_ref='original-error')
        self.assertEqual('FAILED', deep_gate.status(self.db)['claims']['deep-claim-1']['state'])
        self.assertEqual('ATTEMPTS_EXHAUSTED', tasks.status(self.db, task_id=row['task_id'])['blocker'])
        self.assertEqual('RECONCILIATION_REQUIRED', tasks.assess(self.db, task_id=row['task_id'],
                         dependency_ready=True, dependency_ref='same-failure')['action'])
        with self.assertRaisesRegex(Conflict, 'verified repair'):
            self.submit(kind='DEEP', reason_kind='MATERIAL_COUNTEREVIDENCE',
                        scope='btc/research', task_id='btc-cycle/deep',
                        input_digest=digest('changed-source'))
        repaired = tasks.repair(self.db, task_id=row['task_id'],
            input_digest=digest('changed-source'), release_id='new-release',
            evidence_ref='isolated-changed-source', verification_ref='verified-fix',
            reason='The original failure was repaired under a new pinned release.')['task']
        self.assertEqual(('NEEDED', 2, 0),
                         (repaired['state'], repaired['revision'], repaired['attempts']))
        self.assertEqual('ATTEMPTS_EXHAUSTED', repaired['history'][-1]['blocker'])
        self.assertEqual('OPEN_DEFECT', tasks.assess(self.db,
            task_id=row['task_id'], dependency_ready=True,
            dependency_ref='verified-fix')['task']['blocker'])

    def test_unknown_delivery_and_restart_do_not_resend(self):
        row = self.submit(kind='REPORT', task_id='daily/report', scope='btc/report')['task']
        tasks.assess(self.db, task_id=row['task_id'], dependency_ready=True,
                     dependency_ref='actual-report-review')
        tasks.start(self.db, task_id=row['task_id'], attempt_id='send-1', release_id='a1577cc6')
        self.assertEqual('RUNNING', tasks.status(self.db, task_id=row['task_id'])['state'])
        # A process restart does not reset persisted RUNNING or authorize resend.
        with self.assertRaisesRegex(Conflict, 'not ready'):
            tasks.start(self.db, task_id=row['task_id'], attempt_id='send-2', release_id='a1577cc6')
        tasks.finish(self.db, task_id=row['task_id'], attempt_id='send-1', outcome='UNKNOWN',
                     completion_ref='unknown-transport', failure_kind='NETWORK', failure_ref='dispatch-lost')
        self.assertEqual('DELIVERY_UNKNOWN', tasks.status(self.db, task_id=row['task_id'])['blocker'])
        with self.assertRaisesRegex(Conflict, 'blocked original attempt'):
            self.submit(kind='REPORT', task_id='daily/report', scope='btc/report',
                        input_digest=digest('new-body'))
        tasks.reconcile(self.db, task_id=row['task_id'], attempt_id='send-1',
                        outcome='COMPLETE', evidence_ref='native-readback',
                        completion_ref='verified-delivery-id')
        self.assertEqual('COMPLETED', tasks.status(self.db, task_id=row['task_id'])['state'])

    def test_delivery_completion_requires_existing_native_receipt(self):
        row = self.submit(kind='REPORT', phase='DELIVERY', task_id='daily/delivery',
                          scope='btc/report')['task']
        tasks.assess(self.db, task_id=row['task_id'], dependency_ready=True,
                     dependency_ref='frozen-report-artifact')
        tasks.start(self.db, task_id=row['task_id'], attempt_id='send-1', release_id='a1577cc6')
        class Missing:
            def get(self, kind, identity): return None
        with patch('btc_node.entities.Entities', return_value=Missing()):
            with self.assertRaisesRegex(Conflict, 'original verified native receipt'):
                tasks.finish(self.db, task_id=row['task_id'], attempt_id='send-1', outcome='COMPLETE',
                             completion_ref='missing-delivery')
        class Delivery:
            def get(self, kind, identity):
                return {'payload': {'status': {'state': 'DELIVERED'}}}
        with patch('btc_node.entities.Entities', return_value=Delivery()):
            result = tasks.finish(self.db, task_id=row['task_id'], attempt_id='send-1',
                                  outcome='COMPLETE', completion_ref='verified-delivery')
        self.assertEqual('COMPLETED', result['task']['state'])


if __name__ == '__main__': unittest.main()
