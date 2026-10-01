from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unix_utc_iso(value: int | float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ReaderConfig:
    chat_id: int
    reader_bot_id: int
    configured_at_utc: str


@dataclass(frozen=True)
class FetchedMessage:
    update_id: int
    chat_id: int
    message_id: int
    sender_id: int | None
    sender_is_bot: bool
    sent_at_utc: str | None
    edited_at_utc: str | None
    text: str
    content_kind: str
    is_bot_command: bool


@dataclass(frozen=True)
class SyncResult:
    scanned_updates: int
    accepted_messages: int
    ignored_commands: int
    ignored_other_channels: int
    changed_messages: int
    last_update_id: int
