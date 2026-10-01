from __future__ import annotations

from typing import Any, Callable, Protocol

from bot_client import _channel_post_from_update, parse_channel_post_update
from models import FetchedMessage, ReaderConfig, SyncResult, utc_now_iso
from store import MessageStore


COLLECTOR_DATABASE_NAME = "collector_evidence.sqlite3"


class UpdateSource(Protocol):
    def get_updates(self, *, offset: int | None = None) -> list[dict[str, Any]]:
        ...

    def download_document(self, file_id: str) -> bytes:
        ...


class EvidenceUpdateSink(Protocol):
    def ingest_update(self, update: dict[str, Any]) -> Any:
        ...

    def process_pending(self, download_callback: Callable[[str], bytes]) -> Any:
        ...

    def close(self) -> None:
        ...


def _is_target_channel_update(update: dict[str, Any], chat_id: int) -> bool:
    post = _channel_post_from_update(update)
    chat = post.get("chat") if post is not None else None
    if not isinstance(chat, dict) or chat.get("type") != "channel":
        return False
    try:
        return int(chat["id"]) == chat_id
    except (KeyError, TypeError, ValueError):
        return False


def synchronize(
    *,
    store: MessageStore,
    source: UpdateSource,
    config: ReaderConfig,
    evidence_reader: EvidenceUpdateSink | None = None,
) -> SyncResult:
    owned_evidence_reader = evidence_reader is None
    if evidence_reader is None:
        # 根任务提供共同模块；不在适配层复制协议解析或可信度校验。
        from btc_evidence_reader import EvidenceReader

        evidence_reader = EvidenceReader(
            store.path.parent / COLLECTOR_DATABASE_NAME,
            allowed_chat_id=config.chat_id,
        )
    try:
        return _synchronize(
            store=store,
            source=source,
            config=config,
            evidence_reader=evidence_reader,
        )
    finally:
        if owned_evidence_reader:
            evidence_reader.close()


def _synchronize(
    *,
    store: MessageStore,
    source: UpdateSource,
    config: ReaderConfig,
    evidence_reader: EvidenceUpdateSink,
) -> SyncResult:
    current_update_id = store.get_last_update_id()
    offset = current_update_id + 1 if current_update_id else None
    updates = source.get_updates(offset=offset)
    last_update_id = current_update_id
    accepted: list[FetchedMessage] = []
    ignored_commands = 0
    ignored_other_channels = 0

    for update in updates:
        # 包括无 caption 附件和父消息编辑。先持久化原始同频道更新，
        # 再确认旧正文游标；失败/重放由共同模块的幂等入队语义承接。
        if _is_target_channel_update(update, config.chat_id):
            evidence_reader.ingest_update(update)
        try:
            last_update_id = max(last_update_id, int(update.get("update_id", 0)))
        except (TypeError, ValueError):
            pass
        message = parse_channel_post_update(update)
        if message is None:
            continue
        if message.chat_id != config.chat_id:
            ignored_other_channels += 1
            continue
        if message.is_bot_command:
            ignored_commands += 1
            continue
        accepted.append(message)

    changed = store.apply_sync_batch(
        accepted,
        last_update_id,
        observed_at_utc=utc_now_iso(),
    )
    # 两边持久化均已提交；网络失败不得回滚正文或丢弃待处理附件。
    # 即使本轮 updates 为空，也继续处理此前持久化的失败队列。
    evidence_reader.process_pending(source.download_document)
    return SyncResult(
        scanned_updates=len(updates),
        accepted_messages=len(accepted),
        ignored_commands=ignored_commands,
        ignored_other_channels=ignored_other_channels,
        changed_messages=changed,
        last_update_id=last_update_id,
    )
