"""Frozen auxiliary input retains transport identity without credentials."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

reader_root = Path(os.environ.get('BTC_COLLECTOR_CONTRACT_PATH',
    str(Path(__file__).resolve().parents[3] / 'telegram_reader/btc_evidence.py'))).parent
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'),
               str(reader_root), str(reader_root / 'tests')]
import btc_evidence
from test_evidence_v2 import generated
from v4 import collector, evidence
from v4.common import Invalid, create, digest, read


class CollectorProvenanceTests(unittest.TestCase):
    def test_retained_snapshot_reuses_exact_source_and_rejects_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = {'schema': collector.SCHEMA, 'role': 'auxiliary_observation',
                'captured_at': '2026-09-03T12:00:00Z', 'independent_verified_sources': 0,
                'requires_independent_verification': True, 'requires_current_source_check': True,
                'assets': {asset: {'status': 'UNAVAILABLE'} for asset in collector.ASSETS}}
            path = root / 'snapshot.json'; create(path, snapshot)
            spec = {'sources': []}; files = {}
            collector.add_source(spec, files, path)
            original = deepcopy(spec)
            copy = root / 'same-snapshot.json'; copy.write_bytes(path.read_bytes())
            collector.add_source(spec, files, copy)
            self.assertEqual(spec, original)
            self.assertEqual(files[spec['collector']['source_id']], str(copy))
            for field, value in [('trust', 'PUBLIC_RAW'), ('raw_hash', digest(b'different')),
                                 ('retrieved_at', '2026-09-03T12:01:00Z')]:
                changed = deepcopy(original)
                changed['sources'][0][field] = value
                before = deepcopy(changed); prior_files = dict(files)
                with self.subTest(field=field), self.assertRaisesRegex(Invalid, 'source collision'):
                    collector.add_source(changed, files, copy)
                self.assertEqual(changed, before)
                self.assertEqual(files, prior_files)
            changed = deepcopy(original)
            changed['sources'].append(deepcopy(changed['sources'][0]))
            with self.assertRaisesRegex(Invalid, 'source collision'):
                collector.add_source(changed, files, copy)

    def test_compact_root_reuses_frozen_collector_with_additional_public_evidence(self):
        import test_market_observation as market_fixture
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market = root / 'market.json'; create(market, market_fixture.evidence())
            snapshot = root / 'snapshot.json'
            create(snapshot, {'schema': collector.SCHEMA, 'role': 'auxiliary_observation',
                'captured_at': '2026-09-03T12:00:00Z', 'independent_verified_sources': 0,
                'requires_independent_verification': True, 'requires_current_source_check': True,
                'assets': {asset: {'status': 'UNAVAILABLE'} for asset in collector.ASSETS}})
            raw = root / 'public.json'; create(raw, {'value': 'synthetic public evidence'})
            spec = {'parent_id': None, 'cutoff': '2026-09-03T12:02:00Z',
                'sources': [{'id': 'public', 'origin': evidence.canonical_origin(
                    'https://www.federalreserve.gov/releases/h6/current/default.htm'),
                    'url': 'https://www.federalreserve.gov/releases/h6/current/default.htm',
                    'trust': 'PUBLIC_RAW', 'published_at': '2026-09-03T12:00:00Z',
                    'retrieved_at': '2026-09-03T12:01:00Z', 'start': '2026-09-03T12:00:00Z',
                    'end': '2026-09-03T12:00:00Z', 'raw_hash': digest(raw.read_bytes())}],
                'measurements': [], 'verified_facts': [], 'candidates': [], 'gaps': [],
                'handoff': {'previous_hour': None, 'successful_deep': None, 'changes_since_hour': [],
                    'changes_since_deep': [], 'active_hypotheses': [], 'open_questions': []}}
            base = root / 'base'
            evidence.freeze(spec, {'public': raw}, base)
            first_dir = root / 'first'
            first = evidence.compact_root(base, market, [], first_dir, spec['cutoff'], collector_file=snapshot)
            original = (first_dir / 'evidence.json').read_bytes()
            source = next(s for s in first['sources'] if s['id'] == first['collector']['source_id'])
            ref = {'source_id': source['id'], 'pointer': '/captured_at',
                   'value_hash': digest(read(snapshot)['captured_at'])}
            target = root / 'combined'
            merged = evidence.compact_root(first_dir, market, [ref], target, spec['cutoff'],
                                           additional=base, collector_file=snapshot)
            self.assertEqual(len(merged['sources']), 3)
            self.assertEqual(merged['collector'], first['collector'])
            self.assertEqual(next(s for s in merged['sources'] if s['id'] == source['id']), source)
            self.assertEqual(evidence.resolve(merged, target, ref), read(snapshot)['captured_at'])
            self.assertEqual((first_dir / 'evidence.json').read_bytes(), original)
            with self.assertRaisesRegex(Invalid, 'untrusted lead'):
                evidence.resolve(merged, target, ref, verified=True)
            newer = root / 'newer.json'
            create(newer, dict(read(snapshot), captured_at='2026-09-03T12:01:00Z'))
            changed = evidence.compact_root(first_dir, market, [ref], root / 'changed',
                                           spec['cutoff'], collector_file=newer)
            self.assertNotEqual(changed['collector']['source_id'], source['id'])
            self.assertIn(source, changed['sources'])
            self.assertEqual(evidence.resolve(changed, root / 'changed', ref), read(snapshot)['captured_at'])

    def test_gap_and_valid_identity_reach_frozen_material(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            for asset in ('BTC', 'QQQ', 'VOO'):
                bundle, text = generated(asset)
                item = btc_evidence.auxiliary_observation(bundle, text)
                item['transport'] = {'chat_id': -100123, 'text_message_id': 100,
                    'document_message_id': 101, 'received_at': 1000, 'file_id': 'must-not-propagate'}
                rows.append(item)
            rows[0] = {'asset_id': 'BTC', 'role': 'auxiliary_observation_gap',
                'reasons': ['attachment_unavailable'], 'text_message_id': 200,
                'independent_verified_sources': 0,
                'transport': {'text_message_id': 200, 'document_message_id': 201,
                    'download_status': 'pending', 'download_attempts': 1,
                    'download_error': 'download_failed', 'file_id': 'must-not-propagate'}}
            export = root / 'export.json'; export.write_text(json.dumps(rows))
            captured = collector.capture(root, export)
            self.assertEqual(captured['assets']['BTC']['transport']['document_message_id'], 201)
            self.assertEqual(captured['assets']['QQQ']['report_id'], rows[1]['report_id'])
            self.assertEqual(captured['assets']['VOO']['bundle_id'], rows[2]['bundle_id'])
            self.assertFalse(captured['assets']['VOO']['datasets']['stock_1h']['analysis_eligible'])
            self.assertNotIn('must-not-propagate', json.dumps(captured))
            self.assertNotIn('chat_id', json.dumps(captured))
            path = root / 'snapshot.json'; path.write_text(json.dumps(captured))
            spec = {'sources': []}; files = {}
            collector.add_source(spec, files, path)
            source = spec['sources'][0]
            raw = root / 'raw'; raw.mkdir()
            (raw / (source['raw_hash'][7:] + '.json')).write_bytes(path.read_bytes())
            ctx = collector.context(spec, root)
            self.assertEqual(ctx['assets']['BTC']['transport'], captured['assets']['BTC']['transport'])
            self.assertEqual(source['trust'], 'UNTRUSTED_LEAD')
            self.assertEqual(ctx['independent_verified_sources'], 0)


if __name__ == '__main__':
    unittest.main()
