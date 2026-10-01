import tempfile
import unittest
from pathlib import Path

from models import ReaderConfig
from store import MessageStore
from sync_engine import synchronize


CHAT_ID = -1001234567890
OTHER_CHAT_ID = -1009999999999


def config():
    return ReaderConfig(
        chat_id=CHAT_ID,
        reader_bot_id=987,
        configured_at_utc="2026-08-23T00:00:00+00:00",
    )


def raw_update(
    update_id,
    *,
    chat_id=CHAT_ID,
    message_id=None,
    text="数据",
    sender_id=123,
    sender_is_bot=False,
    command=False,
    edited=False,
    channel=True,
):
    message = {
        "message_id": message_id or update_id,
        "date": 1787443200,
        "chat": {"id": chat_id, "type": "channel" if channel else "supergroup"},
        "from": {"id": sender_id, "is_bot": sender_is_bot},
        "text": text,
    }
    if command:
        message["entities"] = [
            {"type": "bot_command", "offset": 0, "length": len(text)}
        ]
    if edited:
        message["edit_date"] = 1787443500
    update_type = "edited_message" if edited else "message"
    if channel:
        update_type = "edited_channel_post" if edited else "channel_post"
    return {"update_id": update_id, update_type: message}


class FakeSource:
    def __init__(self, updates=None, error=None):
        self.updates = updates or []
        self.error = error
        self.requested_offset = None

    def get_updates(self, *, offset=None):
        self.requested_offset = offset
        if self.error:
            raise self.error
        return self.updates

    def download_document(self, file_id):
        raise AssertionError("旧正文测试不应下载附件")


class SyncEngineTest(unittest.TestCase):
    def make_store(self):
        directory = tempfile.TemporaryDirectory()
        store = MessageStore(Path(directory.name) / "messages.sqlite3")
        store.save_config(config())
        self.addCleanup(store.close)
        self.addCleanup(directory.cleanup)
        return store

    def test_accepts_human_and_bot_but_ignores_command(self):
        store = self.make_store()
        source = FakeSource(
            [
                raw_update(1, sender_id=1, sender_is_bot=False),
                raw_update(2, sender_id=2, sender_is_bot=True),
                raw_update(3, text="/btc", command=True),
            ]
        )
        result = synchronize(store=store, source=source, config=config())
        self.assertEqual(result.accepted_messages, 2)
        self.assertEqual(result.ignored_commands, 1)
        self.assertEqual(store.status()["total"], 2)
        self.assertEqual(store.get_last_update_id(), 3)

    def test_ignores_other_channel_and_advances_cursor(self):
        store = self.make_store()
        result = synchronize(
            store=store,
            source=FakeSource([raw_update(7, chat_id=OTHER_CHAT_ID)]),
            config=config(),
        )
        self.assertEqual(result.ignored_other_channels, 1)
        self.assertEqual(store.get_last_update_id(), 7)
        self.assertEqual(store.status()["total"], 0)

    def test_next_request_uses_saved_update_offset(self):
        store = self.make_store()
        synchronize(
            store=store,
            source=FakeSource([raw_update(10)]),
            config=config(),
        )
        source = FakeSource([])
        synchronize(store=store, source=source, config=config())
        self.assertEqual(source.requested_offset, 11)

    def test_fetch_failure_does_not_advance_cursor(self):
        store = self.make_store()
        with self.assertRaises(ConnectionError):
            synchronize(
                store=store,
                source=FakeSource(error=ConnectionError("simulated")),
                config=config(),
            )
        self.assertEqual(store.get_last_update_id(), 0)

    def test_edited_message_creates_revision(self):
        store = self.make_store()
        synchronize(
            store=store,
            source=FakeSource([raw_update(1, message_id=50, text="初始")]),
            config=config(),
        )
        synchronize(
            store=store,
            source=FakeSource(
                [raw_update(2, message_id=50, text="修改", edited=True)]
            ),
            config=config(),
        )
        self.assertEqual(store.status()["total"], 1)
        self.assertEqual(store.status()["revisions"], 2)

    def test_channel_post_is_stored(self):
        store = self.make_store()
        result = synchronize(
            store=store,
            source=FakeSource([raw_update(1, channel=True, sender_is_bot=True)]),
            config=config(),
        )
        self.assertEqual(result.accepted_messages, 1)
        self.assertEqual(store.status()["total"], 1)

    def test_group_message_is_rejected_even_when_id_matches(self):
        store = self.make_store()
        result = synchronize(
            store=store,
            source=FakeSource([raw_update(1, channel=False)]),
            config=config(),
        )
        self.assertEqual(result.accepted_messages, 0)
        self.assertEqual(store.status()["total"], 0)
        self.assertEqual(store.get_last_update_id(), 1)


if __name__ == "__main__":
    unittest.main()
