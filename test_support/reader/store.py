from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from models import FetchedMessage, ReaderConfig, utc_now_iso


SCHEMA_VERSION = 3


class StoreError(RuntimeError):
    pass


class AlreadyRunningError(StoreError):
    pass


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: Any | None = None

    def __enter__(self) -> "ProcessLock":
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._handle = self._path.open("a+", encoding="utf-8")
        try:
            os.chmod(self._path, 0o600)
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._handle.close()
            self._handle = None
            raise AlreadyRunningError("另一个同步任务仍在运行。") from None
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class MessageStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._connection = sqlite3.connect(path, timeout=10)
        self._connection.row_factory = sqlite3.Row
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._prepare_schema()

    def _prepare_schema(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise StoreError("数据库版本高于当前程序，拒绝打开。")
        if version == 1:
            self._migrate_empty_legacy_schema()
            version = 0
        if version == 2:
            self._migrate_channel_views()
            version = 3
        if version == 0:
            self._create_schema()

    def _migrate_channel_views(self) -> None:
        with self._connection:
            self._connection.execute("DROP VIEW IF EXISTS current_group_messages")
            self._connection.execute("DROP VIEW IF EXISTS group_message_audit")
            self._create_channel_views()
            self._connection.execute("PRAGMA user_version = 3")

    def _create_channel_views(self) -> None:
        self._connection.executescript(
            """
            CREATE VIEW IF NOT EXISTS current_channel_posts AS
            SELECT
                chat_id AS channel_id,
                message_id,
                sender_id,
                sender_is_bot,
                sent_at_utc,
                edited_at_utc,
                current_text,
                content_kind,
                first_seen_at_utc,
                last_seen_at_utc
            FROM messages;

            CREATE VIEW IF NOT EXISTS channel_post_audit AS
            SELECT
                revision_id,
                chat_id AS channel_id,
                message_id,
                event_type,
                observed_at_utc,
                sender_id,
                sender_is_bot,
                telegram_edited_at_utc,
                text,
                content_kind
            FROM message_revisions;
            """
        )

    def _migrate_empty_legacy_schema(self) -> None:
        count = 0
        table = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()
        if table is not None:
            count = int(self._connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        if count:
            raise StoreError(
                "旧版用户账号读取库中已有消息；请先备份，程序不会自动覆盖。"
            )
        with self._connection:
            self._connection.execute("DROP VIEW IF EXISTS current_bot_messages")
            self._connection.execute("DROP VIEW IF EXISTS bot_message_audit")
            self._connection.execute("DROP TABLE IF EXISTS message_revisions")
            self._connection.execute("DROP TABLE IF EXISTS messages")
            self._connection.execute("DROP TABLE IF EXISTS sync_state")
            self._connection.execute("DROP TABLE IF EXISTS reader_config")
            self._connection.execute("PRAGMA user_version = 0")

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS reader_config (
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                chat_id INTEGER NOT NULL,
                reader_bot_id INTEGER NOT NULL,
                configured_at_utc TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                sender_id INTEGER,
                sender_is_bot INTEGER NOT NULL CHECK(sender_is_bot IN (0, 1)),
                sent_at_utc TEXT,
                edited_at_utc TEXT,
                current_text TEXT NOT NULL,
                content_kind TEXT NOT NULL,
                first_seen_at_utc TEXT NOT NULL,
                last_seen_at_utc TEXT NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS message_revisions (
                revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                event_type TEXT NOT NULL CHECK(event_type IN ('created', 'edited')),
                observed_at_utc TEXT NOT NULL,
                sender_id INTEGER,
                sender_is_bot INTEGER NOT NULL CHECK(sender_is_bot IN (0, 1)),
                telegram_edited_at_utc TEXT,
                text TEXT NOT NULL,
                content_kind TEXT NOT NULL,
                content_fingerprint TEXT NOT NULL,
                UNIQUE(chat_id, message_id, event_type, content_fingerprint),
                FOREIGN KEY(chat_id, message_id)
                    REFERENCES messages(chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                last_update_id INTEGER NOT NULL DEFAULT 0,
                last_success_at_utc TEXT,
                last_error_code TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_sent
            ON messages(chat_id, sent_at_utc);

            CREATE INDEX IF NOT EXISTS idx_revisions_message
            ON message_revisions(chat_id, message_id, revision_id);

            CREATE VIEW IF NOT EXISTS current_channel_posts AS
            SELECT
                chat_id AS channel_id,
                message_id,
                sender_id,
                sender_is_bot,
                sent_at_utc,
                edited_at_utc,
                current_text,
                content_kind,
                first_seen_at_utc,
                last_seen_at_utc
            FROM messages;

            CREATE VIEW IF NOT EXISTS channel_post_audit AS
            SELECT
                revision_id,
                chat_id AS channel_id,
                message_id,
                event_type,
                observed_at_utc,
                sender_id,
                sender_is_bot,
                telegram_edited_at_utc,
                text,
                content_kind
            FROM message_revisions;

            INSERT OR IGNORE INTO sync_state(singleton_id, last_update_id)
            VALUES(1, 0);

            PRAGMA user_version = 3;
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "MessageStore":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

    def get_config(self) -> ReaderConfig | None:
        row = self._connection.execute(
            "SELECT * FROM reader_config WHERE singleton_id = 1"
        ).fetchone()
        if row is None:
            return None
        return ReaderConfig(
            chat_id=int(row["chat_id"]),
            reader_bot_id=int(row["reader_bot_id"]),
            configured_at_utc=str(row["configured_at_utc"]),
        )

    def save_config(self, config: ReaderConfig) -> None:
        existing = self.get_config()
        switching_chat = existing is not None and existing.chat_id != config.chat_id
        if switching_chat:
            count = int(self._connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            if count:
                raise StoreError("数据库已有其他频道的内容，拒绝静默切换目标频道。")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO reader_config(
                    singleton_id, chat_id, reader_bot_id, configured_at_utc
                ) VALUES(1, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    reader_bot_id = excluded.reader_bot_id,
                    configured_at_utc = excluded.configured_at_utc
                """,
                (config.chat_id, config.reader_bot_id, config.configured_at_utc),
            )
            if switching_chat:
                self._connection.execute(
                    """
                    UPDATE sync_state SET
                        last_update_id = 0,
                        last_success_at_utc = NULL,
                        last_error_code = NULL
                    WHERE singleton_id = 1
                    """
                )

    def get_last_update_id(self) -> int:
        row = self._connection.execute(
            "SELECT last_update_id FROM sync_state WHERE singleton_id = 1"
        ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _fingerprint(message: FetchedMessage, event_type: str) -> str:
        payload = json.dumps(
            [
                event_type,
                message.sender_id,
                message.sender_is_bot,
                message.text,
                message.content_kind,
                message.edited_at_utc,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _insert_revision(
        self,
        message: FetchedMessage,
        event_type: str,
        observed_at_utc: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO message_revisions(
                chat_id, message_id, event_type, observed_at_utc,
                sender_id, sender_is_bot, telegram_edited_at_utc,
                text, content_kind, content_fingerprint
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.chat_id,
                message.message_id,
                event_type,
                observed_at_utc,
                message.sender_id,
                int(message.sender_is_bot),
                message.edited_at_utc,
                message.text,
                message.content_kind,
                self._fingerprint(message, event_type),
            ),
        )

    def _observe_message(self, message: FetchedMessage, observed_at_utc: str) -> bool:
        row = self._connection.execute(
            "SELECT * FROM messages WHERE chat_id = ? AND message_id = ?",
            (message.chat_id, message.message_id),
        ).fetchone()
        if row is None:
            self._connection.execute(
                """
                INSERT INTO messages(
                    chat_id, message_id, sender_id, sender_is_bot,
                    sent_at_utc, edited_at_utc, current_text, content_kind,
                    first_seen_at_utc, last_seen_at_utc
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.chat_id,
                    message.message_id,
                    message.sender_id,
                    int(message.sender_is_bot),
                    message.sent_at_utc,
                    message.edited_at_utc,
                    message.text,
                    message.content_kind,
                    observed_at_utc,
                    observed_at_utc,
                ),
            )
            self._insert_revision(message, "created", observed_at_utc)
            return True

        changed = any(
            (
                row["sender_id"] != message.sender_id,
                bool(row["sender_is_bot"]) != message.sender_is_bot,
                row["edited_at_utc"] != message.edited_at_utc,
                row["current_text"] != message.text,
                row["content_kind"] != message.content_kind,
            )
        )
        self._connection.execute(
            """
            UPDATE messages SET
                sender_id = ?,
                sender_is_bot = ?,
                sent_at_utc = ?,
                edited_at_utc = ?,
                current_text = ?,
                content_kind = ?,
                last_seen_at_utc = ?
            WHERE chat_id = ? AND message_id = ?
            """,
            (
                message.sender_id,
                int(message.sender_is_bot),
                message.sent_at_utc,
                message.edited_at_utc,
                message.text,
                message.content_kind,
                observed_at_utc,
                message.chat_id,
                message.message_id,
            ),
        )
        if changed:
            self._insert_revision(message, "edited", observed_at_utc)
        return changed

    def observe_message(
        self, message: FetchedMessage, observed_at_utc: str | None = None
    ) -> bool:
        with self._connection:
            return self._observe_message(message, observed_at_utc or utc_now_iso())

    def apply_sync_batch(
        self,
        messages: Iterable[FetchedMessage],
        last_update_id: int,
        observed_at_utc: str | None = None,
    ) -> int:
        observed_at_utc = observed_at_utc or utc_now_iso()
        changed = 0
        with self._connection:
            for message in messages:
                changed += int(self._observe_message(message, observed_at_utc))
            self._connection.execute(
                """
                UPDATE sync_state SET
                    last_update_id = MAX(last_update_id, ?),
                    last_success_at_utc = ?,
                    last_error_code = NULL
                WHERE singleton_id = 1
                """,
                (last_update_id, observed_at_utc),
            )
        return changed

    def record_error(self, error_code: str) -> None:
        safe_code = "".join(
            character
            for character in error_code[:100]
            if character.isalnum() or character in "._-"
        ) or "UnknownError"
        with self._connection:
            self._connection.execute(
                "UPDATE sync_state SET last_error_code = ? WHERE singleton_id = 1",
                (safe_code,),
            )

    def status(self) -> dict[str, Any]:
        config = self.get_config()
        state = self._connection.execute(
            "SELECT * FROM sync_state WHERE singleton_id = 1"
        ).fetchone()
        totals = self._connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN sender_is_bot = 1 THEN 1 ELSE 0 END) AS bot_total,
                SUM(CASE WHEN sender_is_bot = 0 THEN 1 ELSE 0 END) AS human_total
            FROM messages
            """
        ).fetchone()
        revisions = int(
            self._connection.execute("SELECT COUNT(*) FROM message_revisions").fetchone()[0]
        )
        return {
            "config": config,
            "state": dict(state) if state is not None else None,
            "total": int(totals["total"] or 0),
            "bot_total": int(totals["bot_total"] or 0),
            "human_total": int(totals["human_total"] or 0),
            "revisions": revisions,
        }

    def latest_strategy_report(self, asset: str = "BTC") -> dict[str, Any] | None:
        if asset not in ("BTC", "QQQ", "VOO"):
            raise ValueError("unsupported report asset")
        row = self._connection.execute(
            """
            SELECT
                messages.chat_id,
                messages.message_id,
                messages.sent_at_utc,
                messages.edited_at_utc,
                messages.current_text,
                messages.content_kind,
                COUNT(message_revisions.revision_id) AS revision_count
            FROM messages
            LEFT JOIN message_revisions
                ON message_revisions.chat_id = messages.chat_id
                AND message_revisions.message_id = messages.message_id
            WHERE messages.current_text LIKE ?
            GROUP BY messages.chat_id, messages.message_id
            ORDER BY messages.sent_at_utc DESC, messages.message_id DESC
            LIMIT 1
            """, ("%📊 " + asset + " %",)
        ).fetchone()
        return dict(row) if row is not None else None
