import sqlite3
import tempfile
import unittest
from pathlib import Path

from models import FetchedMessage, ReaderConfig
from store import AlreadyRunningError, MessageStore, ProcessLock, StoreError


CHAT_ID = -1001234567890
BOT_ID = 123456789


def config(chat_id=CHAT_ID):
    return ReaderConfig(
        chat_id=chat_id,
        reader_bot_id=BOT_ID,
        configured_at_utc="2026-08-23T00:00:00+00:00",
    )


def message(
    message_id=1,
    text="初始内容",
    edited_at=None,
    sender_id=321,
    sender_is_bot=False,
    content_kind="text",
):
    return FetchedMessage(
        update_id=message_id,
        chat_id=CHAT_ID,
        message_id=message_id,
        sender_id=sender_id,
        sender_is_bot=sender_is_bot,
        sent_at_utc="2026-08-23T00:00:00+00:00",
        edited_at_utc=edited_at,
        text=text,
        content_kind=content_kind,
        is_bot_command=False,
    )


class MessageStoreTest(unittest.TestCase):
    def make_store(self):
        directory = tempfile.TemporaryDirectory()
        store = MessageStore(Path(directory.name) / "messages.sqlite3")
        self.addCleanup(store.close)
        self.addCleanup(directory.cleanup)
        return store

    def test_create_duplicate_and_edit_revisions(self):
        store = self.make_store()
        store.save_config(config())
        self.assertTrue(store.observe_message(message()))
        self.assertFalse(store.observe_message(message()))
        self.assertTrue(
            store.observe_message(
                message(text="修改内容", edited_at="2026-08-23T00:05:00+00:00")
            )
        )
        self.assertEqual(store.status()["total"], 1)
        self.assertEqual(store.status()["revisions"], 2)

    def test_human_and_bot_counts_are_separate(self):
        store = self.make_store()
        store.save_config(config())
        store.observe_message(message(message_id=1, sender_is_bot=False))
        store.observe_message(message(message_id=2, sender_is_bot=True))
        status = store.status()
        self.assertEqual(status["human_total"], 1)
        self.assertEqual(status["bot_total"], 1)

    def test_refuses_switching_chat_when_messages_exist(self):
        store = self.make_store()
        store.save_config(config())
        store.observe_message(message())
        with self.assertRaises(StoreError):
            store.save_config(config(chat_id=-100999))

    def test_batch_failure_rolls_back_messages_and_cursor(self):
        store = self.make_store()
        store.save_config(config())
        invalid = message(message_id=2, content_kind=None)
        with self.assertRaises(sqlite3.IntegrityError):
            store.apply_sync_batch([message(message_id=1), invalid], 2)
        self.assertEqual(store.status()["total"], 0)
        self.assertEqual(store.get_last_update_id(), 0)

    def test_empty_v1_database_migrates_to_v3(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                "CREATE TABLE messages(message_id INTEGER); PRAGMA user_version=1;"
            )
            connection.close()
            with MessageStore(path) as store:
                version = store._connection.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, 3)

    def test_v2_group_views_migrate_to_channel_views(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.sqlite3"
            with MessageStore(path) as store:
                store._connection.execute("PRAGMA user_version = 2")
                store._connection.execute(
                    "CREATE VIEW current_group_messages AS SELECT * FROM messages"
                )
                store._connection.execute(
                    "CREATE VIEW group_message_audit AS SELECT * FROM message_revisions"
                )
                store._connection.commit()
            with MessageStore(path) as migrated:
                names = {
                    row[0]
                    for row in migrated._connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'view'"
                    )
                }
                self.assertNotIn("current_group_messages", names)
                self.assertNotIn("group_message_audit", names)
                self.assertIn("current_channel_posts", names)
                self.assertIn("channel_post_audit", names)
                self.assertEqual(
                    migrated._connection.execute("PRAGMA user_version").fetchone()[0],
                    3,
                )

    def test_nonempty_v1_database_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE messages(message_id INTEGER);
                INSERT INTO messages VALUES(1);
                PRAGMA user_version=1;
                """
            )
            connection.close()
            with self.assertRaises(StoreError):
                MessageStore(path)

    def test_process_lock_blocks_concurrent_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reader.lock"
            with ProcessLock(path):
                with self.assertRaises(AlreadyRunningError):
                    with ProcessLock(path):
                        pass


if __name__ == "__main__":
    unittest.main()
