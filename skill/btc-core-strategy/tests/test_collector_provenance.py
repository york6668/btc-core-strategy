"""Frozen auxiliary input retains transport identity without credentials."""
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
from v4.common import digest


class CollectorProvenanceTests(unittest.TestCase):
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
