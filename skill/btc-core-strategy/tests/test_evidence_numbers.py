"""Exact public JSON numeric bindings; no market or account access."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from v4 import evidence
from v4.common import Invalid, digest, numbered


class EvidenceNumberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'raw').mkdir()

    def bind(self, token):
        payload = ('{"price":'+token+'}').encode()
        raw_hash = digest(payload)
        path = self.root/'raw'/(raw_hash[7:]+'.json')
        path.write_bytes(payload)
        package = {'sources': [{'id': 'SYNTHETIC_QUOTE', 'trust': 'PUBLIC_RAW', 'raw_hash': raw_hash}]}
        ref = {'source_id': 'SYNTHETIC_QUOTE', 'pointer': '/price',
               'value_hash': digest(json.loads(payload)['price'])}
        return package, ref, path

    def test_public_numbers_use_the_exact_frozen_token(self):
        for token, expected in [('81100.16', '81100.16'), ('8.110016e4', '81100.16'),
                                ('0.123456789012345678901', '0.123456789012345678901'),
                                ('"81100.160"', '81100.16'), ('81100', '81100')]:
            with self.subTest(token=token):
                package, ref, _ = self.bind(token)
                self.assertEqual(evidence.resolve_number(package, self.root, ref, verified=True), expected)
        with self.assertRaises(Invalid):
            numbered(81100.16)  # Authored amounts/quotes still require exact strings.

    def test_rounding_cannot_substitute_for_the_original_decimal(self):
        package, ref, _ = self.bind('0.123456789012345678901')
        rounded = str(evidence.resolve(package, self.root, ref, verified=True))
        self.assertNotEqual(evidence.resolve_number(package, self.root, ref, verified=True), numbered(rounded))

    def test_unchanged_float_hash_cannot_hide_changed_raw_bytes(self):
        package, ref, path = self.bind('0.123456789012345678901')
        path.write_text('{"price":0.123456789012345678902}')
        with self.assertRaisesRegex(Invalid, 'changed raw evidence'):
            evidence.resolve_number(package, self.root, ref, verified=True)

    def test_boolean_untrusted_or_wrong_binding_is_rejected(self):
        package, ref, _ = self.bind('true')
        with self.assertRaises(Invalid):
            evidence.resolve_number(package, self.root, ref, verified=True)
        package, ref, _ = self.bind('81100.16')
        ref['value_hash'] = digest('wrong quote')
        with self.assertRaisesRegex(Invalid, 'reference mismatch'):
            evidence.resolve_number(package, self.root, ref, verified=True)
        ref['value_hash'] = digest(81100.16)
        package['sources'][0]['trust'] = 'UNTRUSTED_LEAD'
        with self.assertRaisesRegex(Invalid, 'untrusted lead'):
            evidence.resolve_number(package, self.root, ref, verified=True)
