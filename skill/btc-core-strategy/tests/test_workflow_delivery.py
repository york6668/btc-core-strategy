"""Isolated delivery tests: synthetic conclusions, no network or real asset records."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
import contextlib
import io
import json
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_workflow_main as main_fixture
from v4 import brief_report, delivery, hourly, report, semantics
from v4.common import Invalid, create, digest, read, replace


class WorkflowDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='btc-delivery-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture = main_fixture.MainTests('test_sol_publishes_complete_strategy_without_astra_or_report')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.template = deepcopy(fixture.draft)
        for side, price, amount in [('upper_short', '82000', '1200'), ('lower_spot', '64000', '1800')]:
            tier = self.template['recommendations'][side]['tiers'][0]
            tier.update(price_low_usd=price, price_high_usd=price, amount_u=amount)
            if side == 'lower_spot':
                tier['capital_u'] = amount
        self.template['recommendations']['portfolio']['capital_envelope_u'] = '2000'
        self.destination = {'project_id': 'SYNTHETIC_PROJECT', 'thread_id': 'SYNTHETIC_THREAD'}
        self.decision = self.publish(1)
        evaluated = deepcopy(self.decision['assessment'])
        self.layout = self.make_layout(evaluated)
        evaluated['report'] = self.layout
        self.body = report.render(evaluated)
        self.items = [
            ('time', 'deadline', self.body.splitlines()[0]),
            ('upper', 'amount', '| 上沿空单 | 82,000 | 1,200U名义额 |'),
            ('lower', 'amount', '| 下沿现货篮子 | 64,000 | 1,800U投入 |'),
            ('condition', 'condition', '仅在价格低于82000USDT且拒绝成立时考虑'),
            ('deadline', 'deadline', '期限不延长'),
            ('unknown', 'uncertainty', '吸收尚未确认，资金来源未知，保证金尚待确认')]
        self.source = self.make_source()

    def publish(self, number, revision=3):
        accepted = deepcopy(self.template)
        accepted['conclusion']['business_meaning']['synthetic_case'] = str(number)
        value = {'schema': 'BTC_DECISION_SNAPSHOT_V4', 'created_at': '2030-03-01T%02d:00:00+00:00' % (12 + number),
                 'assessment': accepted,
                 'view_hashes': {}, 'report_required': True, 'review': {'material_new_outlook': False}}
        value['decision_id'] = digest(value)[7:]
        directory = self.root / 'versions' / value['decision_id']
        create(directory / 'decision.json', value)
        link = self.root / 'current'
        if link.is_symlink():
            link.unlink()
        link.symlink_to(directory.relative_to(self.root))
        return value

    def make_layout(self, accepted):
        return {'format': 'CONCISE_V1',
                'overview': brief_report.sentence('维持两侧建议，继续检验相应条件。', accepted, set(semantics.stable(accepted))),
                'notes': [brief_report.sentence('仅在价格低于{{/recommendations/upper_short/tiers/0/price_low_usd}}USDT且拒绝成立时考虑，期限不延长。', accepted, ['recommendations']),
                          brief_report.sentence('吸收尚未确认，资金来源未知，保证金尚待确认。', accepted, ['outlook', 'robustness'])], 'risk_notes': []}

    def make_source(self, decision=None):
        decision = decision or self.decision
        return {'schema': delivery.SOURCE_SCHEMA, 'decision_id': decision['decision_id'], 'body': self.body,
                'body_hash': digest(self.body.encode()), 'semantic_hash': digest(semantics.stable(decision['assessment'])),
                'layout': self.make_layout(decision['assessment']),
                'constraints': [{'id': identity, 'kind': kind, 'source_text': value} for identity, kind, value in self.items],
                'author_review': {'role': 'MAIN', 'agent_id': 'SYNTHETIC_MAIN', 'source_hash': digest(self.body.encode()),
                                  'coverage_review': '逐项覆盖源稿全部建议、数值、期限与证据限制，无遗漏业务断言。',
                                  'faithfulness_review': '源稿准确反映本次已验证结论，不添加判断或未确认的资产事实。'}}

    def queue(self, decision=None):
        decision = decision or self.decision
        return delivery.enqueue(self.root, decision['decision_id'], self.destination, self.make_source(decision))['delivery_id']

    def begin(self, identity, action='BEGIN', bind=True):
        status = delivery.transition(self.root, identity, action, {'schema': 'BTC_DELIVERY_ATTEMPT_V1', 'destination': self.destination,
                                                                'anchor_ref': 'SYNTHETIC_BEFORE_SEND_CURSOR', 'started_at': '2030-03-01T15:00:00+00:00'})
        if not status['ready_to_send']:
            return status
        if bind:
            status = delivery.transition(self.root, identity, 'BIND_REQUEST', {'schema': 'BTC_SENT_REQUEST_V1', 'attempt_id': status['attempt']['attempt_id'],
                        'destination': self.destination, 'request_ref': 'REQUEST_' + str(status['attempts']), 'record_refs': ['REQUEST_' + str(status['attempts'])],
                        'request_body': '请仅整理以下报告，不新增分析。\n' + self.body, 'anchor_ref': 'SYNTHETIC_BEFORE_SEND_CURSOR',
                        'request_started_at': '2030-03-01T15:00:01+00:00'})
        return status

    def receipt(self, identity, body=None, replacements=None):
        artifact = read(self.root / 'outbox' / identity / 'artifact.json')
        status = read(self.root / 'outbox' / identity / 'status.json')
        body = self.body if body is None else body
        replacements = replacements or {}
        req = status['attempt'].get('request_ref')
        return {'schema': delivery.RECEIPT_SCHEMA, 'destination': self.destination, 'checked_at': '2030-03-01T16:00:00+00:00',
                'attempt_id': status['attempt']['attempt_id'], 'request_ref': req,
                'assistant': {'record_ref': 'REPLY_' + str(status['attempts']), 'parent_request_ref': req,
                              'role': 'assistant', 'body': body, 'completed': True, 'tool_calls': []},
                'record_refs': [req, 'REPLY_' + str(status['attempts'])], 'absence_confirmed': False,
                'review': {'reviewer_role': 'MAIN', 'agent_id': 'SYNTHETIC_MAIN', 'source_hash': artifact['body_hash'], 'display_hash': digest(body.encode()),
                           'bindings': [{'constraint_id': i, 'display_text': replacements.get(i, text)} for i, _, text in self.items],
                           'checks': {key: {'passed': True, 'explanation': '已逐项对照源稿和真实助手展示，当前检查内容完整且业务含义一致。'} for key in delivery.CHECKS},
                           'unresolved_issues': []}}

    def test_reordered_edited_display_persists_source_and_presentation(self):
        identity = self.queue()
        self.assertEqual(identity, self.queue())
        self.begin(identity)
        edited = self.body.replace('评估：', '本次评估：').splitlines()
        edited[1], edited[2] = edited[2], edited[1]
        receipt = self.receipt(identity, '\n\n'.join(edited))
        status = delivery.transition(self.root, identity, 'CONFIRM', receipt)
        self.assertEqual(status['state'], 'DELIVERED')
        artifact, _, saved = delivery.validate_delivered(self.root, identity)
        self.assertEqual(artifact['body'], self.body)
        self.assertNotEqual(saved['assistant']['body'], self.body)
        self.assertTrue((self.root / 'outbox' / identity / status['presentation']).exists())
        with mock.patch('v4.hourly.complete_deep', side_effect=AssertionError('report must not fabricate a specialist completion')):
            self.assertEqual(hourly.complete_report(self.root, identity, self.root / 'unused.sqlite3')['status'], 'V3_REPORTED_CONCLUSION_AUTHORITATIVE')

    def test_cli_confirms_delivery_without_overwriting_input_receipt(self):
        import strategy_v4
        identity=self.queue();self.begin(identity)
        receipt=self.root/'native-readback.json';create(receipt,self.receipt(identity))
        original=receipt.read_bytes();stream=io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
            code=strategy_v4.main(['--project',str(self.root),'--root',str(self.root),'delivery',
                '--identity',identity,'--action','CONFIRM','--receipt',str(receipt)])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(stream.getvalue())['state'],'DELIVERED')
        self.assertEqual(receipt.read_bytes(),original)
        self.assertEqual(delivery.validate_delivered(self.root,identity)[2]['assistant']['body'],self.body)

    def test_swapped_amounts_rejected_even_with_all_positive_semantic_checks(self):
        identity = self.queue(); self.begin(identity)
        body = self.body.replace('1,200', 'TEMP').replace('1,800', '1,200').replace('TEMP', '1,800')
        replacements = {'upper': self.items[1][2].replace('1,200', '1,800'), 'lower': self.items[2][2].replace('1,800', '1,200')}
        status = delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity, body, replacements))
        self.assertEqual(status['state'], 'CORRECTION_REQUIRED')
        self.assertIn('BOUND_FACT_CHANGED:upper', status['presentation_errors'])
        self.assertFalse((self.root / 'reported-current.json').exists())

    def test_new_numbers_and_tool_use_are_rejected(self):
        identity = self.queue(); self.begin(identity)
        receipt = self.receipt(identity, self.body + '\n上涨概率90%。')
        receipt['assistant']['tool_calls'] = [{'name': 'SYNTHETIC_TOOL'}]
        status = delivery.transition(self.root, identity, 'CONFIRM', receipt)
        self.assertIn('DISPLAY_NUMBER_SET_CHANGED', status['presentation_errors'])
        self.assertIn('CLOUD_USED_TOOLS', status['presentation_errors'])

    def test_reversed_condition_and_removed_uncertainty_are_rejected(self):
        identity = self.queue(); self.begin(identity)
        old = self.items[3][2]; new = old.replace('低于', '高于')
        qualifier = '吸收已经确认，资金来源清晰，保证金已经确认'
        body = self.body.replace(old, new).replace(self.items[-1][2], qualifier)
        status = delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity, body, {'condition': new, 'unknown': qualifier}))
        self.assertIn('BOUND_FACT_CHANGED:condition', status['presentation_errors'])
        self.assertIn('QUALIFIER_REMOVED:unknown', status['presentation_errors'])

    def test_direction_change_and_partial_unknown_removal_are_rejected(self):
        identity = self.queue(); self.begin(identity)
        changed = self.items[1][2].replace('空单', '多单')
        qualifier = self.items[-1][2].replace('资金来源未知', '资金来源已知')
        body = self.body.replace(self.items[1][2], changed).replace(self.items[-1][2], qualifier)
        status = delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity, body, {'upper': changed, 'unknown': qualifier}))
        self.assertIn('DISPLAY_DIRECTION_CHANGED', status['presentation_errors'])
        self.assertIn('QUALIFIER_REMOVED:unknown', status['presentation_errors'])

    def test_semantic_distortion_has_one_frozen_source_correction(self):
        identity = self.queue(); self.begin(identity)
        receipt = self.receipt(identity)
        receipt['review']['checks']['no_new_judgement'] = {'passed': False, 'explanation': '本地审阅发现展示稿对一项业务判断表达失真，需要以源稿明确更正。'}
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', receipt)['state'], 'CORRECTION_REQUIRED')
        status = self.begin(identity, 'BEGIN_CORRECTION')
        self.assertEqual(status['attempt']['mode'], 'CORRECTION')
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))['state'], 'DELIVERED')
        self.assertFalse(self.begin(identity, 'BEGIN_CORRECTION')['ready_to_send'])

    def test_failed_correction_cannot_enter_another_edit_loop(self):
        identity = self.queue(); self.begin(identity)
        receipt = self.receipt(identity)
        receipt['review']['checks']['coverage']['passed'] = False
        delivery.transition(self.root, identity, 'CONFIRM', receipt)
        self.begin(identity, 'BEGIN_CORRECTION')
        status = delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity, self.body + '\n额外说明。'))
        self.assertEqual(status['state'], 'CORRECTION_REQUIRED')
        with self.assertRaises(Invalid):
            self.begin(identity, 'BEGIN_CORRECTION')
        self.assertFalse((self.root / 'reported-current.json').exists())

    def test_unknown_blocks_resend_and_accepts_bound_actual_readback(self):
        identity = self.queue(); self.begin(identity)
        delivery.transition(self.root, identity, 'UNKNOWN')
        self.assertFalse(self.begin(identity)['ready_to_send'])
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))['state'], 'DELIVERED')

    def test_user_message_or_unrelated_assistant_cannot_confirm(self):
        identity = self.queue(); self.begin(identity)
        for mutate in (lambda r: r['assistant'].update(role='user'), lambda r: r['assistant'].update(parent_request_ref='OTHER_REQUEST')):
            receipt = self.receipt(identity); mutate(receipt)
            with self.assertRaises(Invalid):
                delivery.transition(self.root, identity, 'CONFIRM', receipt)
        self.assertFalse((self.root / 'reported-current.json').exists())

    def test_unbound_request_or_ack_only_cannot_confirm_new_contract(self):
        identity = self.queue(); self.begin(identity, bind=False)
        with self.assertRaises(Invalid):
            delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))
        legacy = {'schema': 'BTC_CLOUD_READBACK_V4', 'destination': self.destination, 'checked_at': '2030-03-01T16:00:00Z',
                  'visible_report_text': self.body, 'assistant_responses': ['已收录'], 'record_refs': ['SYNTHETIC'], 'absence_confirmed': False}
        with self.assertRaises(Invalid):
            delivery.transition(self.root, identity, 'CONFIRM', legacy)

    def test_pending_old_conclusion_is_superseded_without_send(self):
        identity = self.queue(); self.publish(2)
        status = self.begin(identity)
        self.assertEqual(status['state'], 'SUPERSEDED')
        self.assertEqual(status['attempts'], 0)

    def test_late_confirmation_does_not_roll_reported_baseline_back(self):
        old = self.queue(); self.begin(old)
        delivery.transition(self.root, old, 'CONFIRM', self.receipt(old))
        newer = self.publish(2); new = self.queue(newer); self.begin(new)
        delivery.transition(self.root, new, 'CONFIRM', self.receipt(new))
        late = delivery.transition(self.root, old, 'CONFIRM', self.receipt(old))
        self.assertEqual(late['state'], 'DELIVERED')
        self.assertTrue(late['late_confirmation_review_required'])
        self.assertEqual(read(self.root / 'reported-current.json')['decision_id'], newer['decision_id'])

    def test_other_unknown_delivery_does_not_block_new_important_send(self):
        old = self.queue(); self.begin(old)
        delivery.transition(self.root,old,'UNKNOWN')
        newer=self.publish(2); new=self.queue(newer)
        self.assertEqual(self.begin(new)['state'],'SENDING')
        self.assertEqual(read(self.root/'outbox'/old/'status.json')['state'],'UNKNOWN')
        self.assertEqual(delivery.transition(self.root,new,'CONFIRM',self.receipt(new))['state'],'DELIVERED')
        delivery.transition(self.root,old,'CONFIRM',self.receipt(old))
        self.assertEqual(read(self.root/'reported-current.json')['delivery_id'],new)

    def test_same_current_meaning_is_not_enqueued_after_confirmation(self):
        identity = self.queue(); self.begin(identity)
        delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))
        self.assertEqual(delivery.enqueue(self.root, self.decision['decision_id'], self.destination, self.source)['status'], 'NOT_DUE')

    def test_source_and_display_reviews_must_bind_actual_bytes_and_explain(self):
        bad = deepcopy(self.source); bad['author_review']['source_hash'] = digest(b'other')
        with self.assertRaises(Invalid):
            delivery.enqueue(self.root, self.decision['decision_id'], self.destination, bad)
        identity = self.queue(); self.begin(identity)
        receipt = self.receipt(identity); receipt['review']['checks']['coverage']['explanation'] = 'true'
        with self.assertRaises(Invalid):
            delivery.transition(self.root, identity, 'CONFIRM', receipt)
        self.assertTrue(list((self.root / 'outbox' / identity).glob('observed-readback-*.json')))
        self.assertFalse((self.root / 'reported-current.json').exists())

    def test_missing_assertion_binding_cannot_confirm(self):
        identity = self.queue(); self.begin(identity)
        receipt = self.receipt(identity); receipt['review']['bindings'].pop()
        with self.assertRaises(Invalid):
            delivery.transition(self.root, identity, 'CONFIRM', receipt)

    def test_ordinary_user_request_cannot_be_bound_as_report_send(self):
        identity = self.queue(); status = self.begin(identity, bind=False)
        binding = {'schema': 'BTC_SENT_REQUEST_V1', 'attempt_id': status['attempt']['attempt_id'], 'destination': self.destination,
                   'request_ref': 'OTHER_USER_MESSAGE', 'record_refs': ['OTHER_USER_MESSAGE'], 'request_body': '你有什么建议？',
                   'anchor_ref': 'SYNTHETIC_BEFORE_SEND_CURSOR', 'request_started_at': '2030-03-01T15:00:01+00:00'}
        with self.assertRaises(Invalid):
            delivery.transition(self.root, identity, 'BIND_REQUEST', binding)

    def test_authoritative_absence_can_restore_same_identity(self):
        identity = self.queue(); status = self.begin(identity, bind=False)
        delivery.transition(self.root, identity, 'UNKNOWN')
        receipt = {'schema': delivery.RECEIPT_SCHEMA, 'destination': self.destination, 'checked_at': '2030-03-01T16:00:00+00:00',
                   'attempt_id': status['attempt']['attempt_id'], 'request_ref': None, 'assistant': None, 'record_refs': ['SYNTHETIC_AUTHORITATIVE_ABSENCE'],
                   'absence_confirmed': True, 'review': None,
                   'dispatch_evidence': {'outcome': 'NOT_DISPATCHED', 'record_ref': 'SYNTHETIC_AUTHORITATIVE_ABSENCE',
                                         'detail': '发送工具明确在派发请求之前拒绝操作，因此云端未创建任何消息。'}}
        self.assertEqual(delivery.transition(self.root, identity, 'NOT_SENT', receipt)['state'], 'NOT_SENT_CONFIRMED')
        resumed = self.begin(identity)
        self.assertEqual(resumed['attempts'], 2)
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))['state'], 'DELIVERED')

    def test_empty_readback_does_not_authorize_retry(self):
        identity = self.queue(); status = self.begin(identity, bind=False)
        delivery.transition(self.root, identity, 'UNKNOWN')
        receipt = {'schema': delivery.RECEIPT_SCHEMA, 'destination': self.destination, 'checked_at': '2030-03-01T16:00:00Z',
                   'attempt_id': status['attempt']['attempt_id'], 'request_ref': None, 'assistant': None,
                   'record_refs': ['SYNTHETIC_BEFORE_SEND_CURSOR'], 'absence_confirmed': True, 'review': None}
        with self.assertRaises(Invalid):
            delivery.transition(self.root, identity, 'NOT_SENT', receipt)
        self.assertEqual(read(self.root / 'outbox' / identity / 'status.json')['state'], 'UNKNOWN')

    def test_binding_rejects_message_from_before_attempt(self):
        identity = self.queue(); status = self.begin(identity)
        binding = read(self.root / 'outbox' / identity / status['request_binding'])
        binding['request_started_at'] = '2030-03-01T14:59:59Z'
        with self.assertRaisesRegex(Invalid, 'predates'):
            delivery.transition(self.root, identity, 'BIND_REQUEST', binding)

    def reconciliation_case(self):
        identity = self.queue(); status = self.begin(identity)
        outbox = self.root / 'outbox' / identity
        binding = read(outbox / status['request_binding'])
        receipt = self.receipt(identity)
        # Reproduce a historical journal created before timestamp validation.
        old = deepcopy(binding); old.pop('request_started_at'); old['request_ref'] = 'EARLIER_REQUEST'
        old['record_refs'] = ['EARLIER_REQUEST']
        status['request_binding'] = delivery._save(outbox, 'request', old)
        status['attempt']['request_ref'] = 'EARLIER_REQUEST'; status['state'] = 'UNKNOWN'
        replace(outbox / 'status.json', status)
        evidence = {'schema': 'BTC_REQUEST_RECONCILIATION_V1',
                    'previous_request': {'record_ref': 'EARLIER_REQUEST', 'started_at': '2030-03-01T14:59:00Z', 'body': old['request_body']},
                    'binding': binding, 'readback': receipt,
                    'reason': '实际云端记录证明原绑定属于较早发送，本次消息及助手回复已完整核验。'}
        return identity, status, evidence

    def test_reconcile_confirms_actual_reply_preserving_history_and_is_idempotent(self):
        identity, before, evidence = self.reconciliation_case()
        old_path = self.root / 'outbox' / identity / before['request_binding']; old_bytes = old_path.read_bytes()
        fixed = delivery.transition(self.root, identity, 'RECONCILE_REQUEST', evidence)
        self.assertEqual(fixed['state'], 'DELIVERED'); self.assertEqual(fixed['attempts'], before['attempts'])
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(delivery.validate_delivered(self.root, identity)[2], evidence['readback'])
        again = delivery.transition(self.root, identity, 'RECONCILE_REQUEST', evidence)
        self.assertEqual(again['receipt'], fixed['receipt'])
        new = self.queue(self.publish(2)); self.assertEqual(self.begin(new)['state'], 'SENDING')

    def test_reconcile_rejects_unproven_or_unrelated_or_distorted_reply_atomically(self):
        identity, before, evidence = self.reconciliation_case()
        mutations = [lambda x: x['previous_request'].update(started_at='2030-03-01T15:00:10Z'),
                     lambda x: x['binding'].update(request_started_at='2030-03-01T14:59:00Z'),
                     lambda x: x['readback']['assistant'].update(parent_request_ref='OTHER'),
                     lambda x: x['readback']['review']['checks']['coverage'].update(passed=False)]
        for mutate in mutations:
            value = deepcopy(evidence); mutate(value)
            with self.assertRaises(Invalid):
                delivery.transition(self.root, identity, 'RECONCILE_REQUEST', value)
            self.assertEqual(read(self.root / 'outbox' / identity / 'status.json'), before)
        self.assertFalse((self.root / 'reported-current.json').exists())

    def test_source_numeric_coverage_is_not_only_a_main_boolean(self):
        source = deepcopy(self.source)
        source['constraints'] = [c for c in source['constraints'] if c['id'] != 'upper']
        with self.assertRaises(Invalid):
            delivery.enqueue(self.root, self.decision['decision_id'], self.destination, source)

    def test_source_wrong_amount_is_rejected_even_with_rehashed_author_review(self):
        source = deepcopy(self.source)
        source['body'] = source['body'].replace('1,200', '9,999')
        source['body_hash'] = digest(source['body'].encode())
        source['author_review']['source_hash'] = source['body_hash']
        source['constraints'][1]['source_text'] = source['constraints'][1]['source_text'].replace('1,200', '9,999')
        with self.assertRaisesRegex(Invalid, 'conclusion-bound rendered layout'):
            delivery.enqueue(self.root, self.decision['decision_id'], self.destination, source)

    def test_source_missing_condition_cannot_be_approved_by_rehashing(self):
        source = deepcopy(self.source)
        source['body'] = source['body'].replace(self.items[3][2] + '，', '')
        source['body_hash'] = digest(source['body'].encode())
        source['author_review']['source_hash'] = source['body_hash']
        source['constraints'] = [c for c in source['constraints'] if c['id'] != 'condition']
        with self.assertRaisesRegex(Invalid, 'conclusion-bound rendered layout'):
            delivery.enqueue(self.root, self.decision['decision_id'], self.destination, source)

    def test_source_layout_must_cover_changed_recommendation_meaning(self):
        source = deepcopy(self.source)
        source['layout']['overview']['bindings'].pop('recommendations')
        source['layout']['notes'][0] = brief_report.sentence(source['layout']['notes'][0]['template'], self.decision['assessment'], ['decision'])
        with self.assertRaisesRegex(Invalid, 'omits material change: recommendations'):
            delivery.enqueue(self.root, self.decision['decision_id'], self.destination, source)

    def without_condition_source(self, decision):
        source = self.make_source(decision)
        source['layout']['notes'].pop(0)
        value = deepcopy(decision['assessment']); value['report'] = source['layout']
        source['body'] = report.render(value)
        source['body_hash'] = digest(source['body'].encode())
        source['author_review']['source_hash'] = source['body_hash']
        source['constraints'] = [c for c in source['constraints'] if c['id'] not in ('condition', 'deadline')]
        for item in source['constraints']:
            if item['id'] == 'upper':
                item['source_text'] = next(line for line in source['body'].splitlines() if line.startswith('| 上沿空单 |'))
        return source

    def test_changed_conditions_cannot_be_removed_from_layout_and_assertions(self):
        source = self.without_condition_source(self.decision)
        with self.assertRaisesRegex(Invalid, 'changed support or invalidation conditions'):
            delivery.enqueue(self.root, self.decision['decision_id'], self.destination, source)

    def test_amount_only_change_does_not_force_repetition_of_unchanged_conditions(self):
        identity = self.queue(); self.begin(identity)
        delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))
        self.template['recommendations']['upper_short']['tiers'][0]['amount_u'] = '1400'
        decision = self.publish(2)
        source = self.without_condition_source(decision)
        queued = delivery.enqueue(self.root, decision['decision_id'], self.destination, source)
        self.assertEqual(queued['status']['state'], 'PENDING')

    def test_source_cannot_split_paired_recommendation_into_independent_cells(self):
        source = deepcopy(self.source)
        source['constraints'][1]['source_text'] = '82,000'
        source['constraints'].append({'id': 'isolated_amount', 'kind': 'amount', 'source_text': '1,200U名义额'})
        with self.assertRaisesRegex(Invalid, 'direction, position and amount together'):
            delivery.enqueue(self.root, self.decision['decision_id'], self.destination, source)

    def test_frozen_source_is_not_rerendered_during_delivery_recovery(self):
        identity = self.queue()
        with mock.patch('v4.report.render', side_effect=AssertionError('formatter must not rewrite frozen source')):
            self.begin(identity)
            delivery.transition(self.root, identity, 'UNKNOWN')
            self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', self.receipt(identity))['state'], 'DELIVERED')
            delivery.validate_delivered(self.root, identity)

    def test_legacy_ack_contract_remains_recoverable(self):
        accepted = deepcopy(self.template); accepted['revision'] = 2
        value = {'schema': 'BTC_DECISION_SNAPSHOT_V4', 'created_at': '2030-03-01T13:00:00+00:00',
                 'assessment': accepted, 'report_required': True,
                 'view_hashes': {'report.md': digest(self.body.encode())}}
        value['decision_id'] = digest(value)[7:]
        directory = self.root / 'versions' / value['decision_id']
        create(directory / 'decision.json', value); create(directory / 'report.md', self.body.encode())
        identity = delivery.enqueue(self.root, value['decision_id'], self.destination)['delivery_id']
        delivery.transition(self.root, identity, 'BEGIN')
        receipt = {'schema': 'BTC_CLOUD_READBACK_V4', 'destination': self.destination, 'checked_at': '2030-03-01T16:00:00Z',
                   'visible_report_text': self.body, 'assistant_responses': ['已收录'], 'record_refs': ['SYNTHETIC_REAL_READBACK'], 'absence_confirmed': False}
        self.assertEqual(delivery.transition(self.root, identity, 'CONFIRM', receipt)['state'], 'DELIVERED')
        delivery.validate_delivered(self.root, identity)


if __name__ == '__main__':
    unittest.main()
