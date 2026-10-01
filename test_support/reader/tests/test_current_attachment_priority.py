"""A historical burst cannot consume the current three-asset download budget."""
import tempfile
import unittest
from pathlib import Path

from btc_evidence_reader import EvidenceReader
from btc_evidence import encode_bundle
from test_evidence_v2 import generated


class CurrentAttachmentPriorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reader = EvidenceReader(Path(self.temp.name) / 'collector.sqlite3', allowed_chat_id=-100123,
                                     clock=lambda: 2000000000)
        self.addCleanup(self.reader.close)
        self.raw = {}

    def post(self, parent, asset):
        bundle, text = generated(asset)
        self.raw[str(parent)] = encode_bundle(bundle)
        self.reader.ingest_update({'update_id': parent, 'channel_post': {
            'chat': {'id': -100123, 'type': 'channel'}, 'date': parent,
            'message_id': parent, 'text': text}})
        self.reader.ingest_update({'update_id': parent + 1, 'channel_post': {
            'chat': {'id': -100123, 'type': 'channel'}, 'date': parent,
            'message_id': parent + 1, 'reply_to_message': {'message_id': parent},
            'document': {'file_id': str(parent), 'file_name': asset + '.json',
                         'file_size': len(self.raw[str(parent)])}}})

    def test_latest_three_download_before_old_burst_and_pending_survives(self):
        for parent in range(10, 56, 2):
            self.post(parent, 'VOO')
        for parent, asset in ((100, 'BTC'), (102, 'QQQ'), (104, 'VOO')):
            self.post(parent, asset)
        before = self.reader.observations(latest_only=True, asset='all')
        self.assertTrue(all(x['reasons'] == ['attachment_unavailable'] for x in before))
        self.assertEqual([x['transport']['document_message_id'] for x in before], [101, 103, 105])
        calls = []
        self.reader.process_pending(lambda file: calls.append(file) or self.raw[file], limit=3)
        self.assertEqual(set(calls), {'100', '102', '104'})
        current = self.reader.observations(latest_only=True, asset='all')
        self.assertTrue(all(x['role'] == 'auxiliary_observation' for x in current))
        self.assertEqual(self.reader.db.execute("SELECT count(*) FROM collector_attachments WHERE download_status='pending'").fetchone()[0], 23)
        self.reader.process_pending(lambda file: calls.append(file) or self.raw[file], limit=2)
        self.assertEqual(calls[-2:], ['10', '12'])

    def test_retry_time_is_respected_without_history_fallback(self):
        self.post(10, 'BTC'); self.post(100, 'BTC')
        self.reader.db.execute("UPDATE collector_attachments SET download_retry_at=2000000010 WHERE parent_id=100")
        self.reader.db.commit()
        calls = []
        self.reader.process_pending(lambda file: calls.append(file) or self.raw[file], limit=1)
        self.assertEqual(calls, ['10'])
        gap = self.reader.observations(latest_only=True, asset='all')[0]
        self.assertEqual(gap['text_message_id'], 100)
        self.assertEqual(gap['reasons'], ['attachment_unavailable'])
        self.assertEqual(gap['transport']['download_status'], 'pending')

    def test_failed_current_download_keeps_gap_and_pending_identity(self):
        self.post(10, 'BTC'); self.post(100, 'BTC')
        def fail(_file):
            raise ConnectionError('synthetic offline failure')
        self.reader.process_pending(fail, limit=1)
        gap = self.reader.observations(latest_only=True, asset='all')[0]
        self.assertEqual(gap['transport']['document_message_id'], 101)
        self.assertEqual(gap['transport']['download_attempts'], 1)
        self.assertEqual(gap['transport']['download_error'], 'download_failed')
        self.assertEqual(gap['role'], 'auxiliary_observation_gap')


if __name__ == '__main__':
    unittest.main()
