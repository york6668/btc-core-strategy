"""Protected nodes fail closed without launching business or native work."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_usage


class NativeUsageTests(unittest.TestCase):
    def setUp(self):
        self.config = {'usage_guard_required': True, 'usage_guard': {
            'command': ['/synthetic/codex-framework', 'usage-check'], 'policy': '/synthetic/policy.json'}}

    def test_missing_guard_is_not_silently_disabled_on_adopted_node(self):
        with self.assertRaisesRegex(ValueError, 'binding is required'):
            native_usage.require_admission({'usage_guard_required': True})
        self.assertFalse(native_usage.require_admission({})['enforced'])

    def test_valid_normal_result_uses_only_explicit_binding(self):
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({
            'schema': 'native-usage-guard-v1', 'allowed': True, 'hard_account_spending_cap': False})))
        self.assertTrue(native_usage.require_admission(self.config, runner=run)['enforced'])
        self.assertEqual(json.loads(run.call_args.kwargs['input']), {'policy': '/synthetic/policy.json'})
        self.assertEqual(run.call_args.args[0], self.config['usage_guard']['command'])

    def test_lock_corrupt_reply_and_timeout_block_new_work(self):
        for code, body in ((2, {'schema': 'native-usage-guard-v1', 'allowed': False,
                               'lock': {'reason': 'ANOMALOUS'}}), (0, []), (0, 'invalid')):
            with self.subTest(body=body), self.assertRaises(ValueError):
                native_usage.require_admission(self.config, runner=Mock(return_value=SimpleNamespace(returncode=code, stdout=json.dumps(body))))
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            native_usage.require_admission(self.config, runner=Mock(side_effect=subprocess.TimeoutExpired('guard', 45)))
