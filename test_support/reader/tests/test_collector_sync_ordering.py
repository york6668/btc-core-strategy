import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from store import MessageStore
from sync_engine import synchronize
from test_sync_engine import FakeSource, config, raw_update


class SinkSpy:
    """仅验证适配调用顺序；不实现或替代共同模块的证据校验。"""

    def __init__(self, store, *, fail_ingest=False):
        self.store = store
        self.fail_ingest = fail_ingest
        self.ingested = []
        self.cursor_when_ingested = []
        self.cursor_when_processed = []
        self.closed = False

    def ingest_update(self, update):
        self.cursor_when_ingested.append(self.store.get_last_update_id())
        if self.fail_ingest:
            raise OSError("simulated pre-write failure")
        self.ingested.append(update)

    def process_pending(self, download_callback):
        self.cursor_when_processed.append(self.store.get_last_update_id())

    def close(self):
        self.closed = True


class CollectorSyncOrderingTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = MessageStore(Path(self.directory.name) / "messages.sqlite3")
        self.store.save_config(config())
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.store.close)

    def test_persists_raw_captionless_document_before_body_cursor(self):
        document = raw_update(2)
        post = document["channel_post"]
        del post["text"]
        post["document"] = {"file_id": "synthetic-json-file", "mime_type": "application/json"}
        sink = SinkSpy(self.store)
        result = synchronize(
            store=self.store,
            source=FakeSource([raw_update(1), document]),
            config=config(),
            evidence_reader=sink,
        )
        self.assertEqual(result.accepted_messages, 1)
        self.assertEqual(sink.ingested[-1], document)
        self.assertEqual(sink.cursor_when_ingested, [0, 0])
        self.assertEqual(sink.cursor_when_processed, [2])
        self.assertEqual(self.store.get_last_update_id(), 2)
        self.assertFalse(sink.closed)

    def test_prewrite_failure_never_advances_body_cursor(self):
        sink = SinkSpy(self.store, fail_ingest=True)
        with self.assertRaises(OSError):
            synchronize(store=self.store, source=FakeSource([raw_update(4)]), config=config(), evidence_reader=sink)
        self.assertEqual(self.store.get_last_update_id(), 0)
        self.assertEqual(self.store.status()["total"], 0)
        self.assertEqual(sink.cursor_when_processed, [])

    def test_body_transaction_failure_keeps_raw_intake_and_skips_download(self):
        sink = SinkSpy(self.store)
        with patch.object(self.store, "apply_sync_batch", side_effect=OSError("simulated body failure")):
            with self.assertRaises(OSError):
                synchronize(store=self.store, source=FakeSource([raw_update(5)]), config=config(), evidence_reader=sink)
        self.assertEqual(len(sink.ingested), 1)
        self.assertEqual(self.store.get_last_update_id(), 0)
        self.assertEqual(sink.cursor_when_processed, [])

    def test_replay_continues_body_sync_when_evidence_owner_reports_duplicate(self):
        sink = SinkSpy(self.store)
        update = raw_update(5)
        with patch.object(self.store, "apply_sync_batch", side_effect=OSError("simulated body failure")):
            with self.assertRaises(OSError):
                synchronize(store=self.store, source=FakeSource([update]), config=config(), evidence_reader=sink)
        # 这里只断言适配层不因公共 Owner 的重复返回跳过正文；
        # 真正跨进程的幂等性另由到齐后的公共模块集成测试证明。
        with patch.object(sink, "ingest_update", return_value=False) as replay:
            result = synchronize(store=self.store, source=FakeSource([update]), config=config(), evidence_reader=sink)
        replay.assert_called_once_with(update)
        self.assertEqual(result.accepted_messages, 1)
        self.assertEqual(self.store.get_last_update_id(), 5)
        self.assertEqual(self.store.status()["total"], 1)
        self.assertEqual(sink.cursor_when_processed, [5])

    def test_empty_update_batch_still_processes_pending_queue(self):
        sink = SinkSpy(self.store)
        result = synchronize(store=self.store, source=FakeSource([]), config=config(), evidence_reader=sink)
        self.assertEqual(result.scanned_updates, 0)
        self.assertEqual(sink.cursor_when_processed, [0])

    def test_other_channels_and_groups_do_not_reach_raw_intake(self):
        sink = SinkSpy(self.store)
        result = synchronize(
            store=self.store,
            source=FakeSource([raw_update(1, channel=False), raw_update(2, chat_id=999)]),
            config=config(),
            evidence_reader=sink,
        )
        self.assertEqual(sink.ingested, [])
        self.assertEqual(result.accepted_messages, 0)
        self.assertEqual(self.store.get_last_update_id(), 2)

    def test_parent_edit_reaches_common_owner_even_when_legacy_command_is_ignored(self):
        edited = raw_update(8, message_id=1, text="/btc", command=True, edited=True)
        sink = SinkSpy(self.store)
        result = synchronize(store=self.store, source=FakeSource([edited]), config=config(), evidence_reader=sink)
        self.assertEqual(sink.ingested, [edited])
        self.assertEqual(result.ignored_commands, 1)
        self.assertEqual(self.store.status()["total"], 0)

    def test_duplicate_raw_updates_are_not_silently_dropped_by_adapter(self):
        update = raw_update(9)
        sink = SinkSpy(self.store)
        synchronize(store=self.store, source=FakeSource([update, update]), config=config(), evidence_reader=sink)
        self.assertEqual(sink.ingested, [update, update])
        self.assertEqual(self.store.status()["total"], 1)
        self.assertEqual(self.store.status()["revisions"], 1)


if __name__ == "__main__":
    unittest.main()
