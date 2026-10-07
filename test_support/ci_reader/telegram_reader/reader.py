#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import closing
from pathlib import Path
from typing import Sequence

from bot_client import (
    BotAPI,
    CredentialVault,
    TelegramReaderError,
    discover_channels,
)
from evidence import refresh_strategy_evidence
from models import ReaderConfig, utc_now_iso
from store import AlreadyRunningError, MessageStore, ProcessLock, StoreError
from sync_engine import COLLECTOR_DATABASE_NAME, synchronize


ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("BTC_READER_DATA_DIR", str(ROOT / "data"))).expanduser()
DATABASE_PATH = DATA_ROOT / "messages.sqlite3"
LOCK_PATH = DATA_ROOT / "reader.lock"
EVIDENCE_PATH = Path(os.environ.get("BTC_READER_EVIDENCE_PATH", str(ROOT.parent / "策略证据.md"))).expanduser()
TARGET_CHANNEL_TITLE = "数据采集频道"


class ReaderCommandError(RuntimeError):
    pass


def require_config(store: MessageStore) -> ReaderConfig:
    config = store.get_config()
    if config is None:
        raise ReaderCommandError("尚未选择目标频道，请先运行 configure。")
    return config


def open_bot_api() -> BotAPI:
    if os.environ.get('BTC_READER_TOKEN_STDIN') == '1':
        # Native Keychain adapter writes one token to the pipe. It never enters
        # the image, command line, environment, source export or a log file.
        token = sys.stdin.readline().strip()
        if not token or len(token) > 512:
            raise ReaderCommandError('native credential bridge supplied no valid token')
        return BotAPI(token)
    return BotAPI(CredentialVault().bot_token())


def run_configure(store: MessageStore) -> None:
    api = open_bot_api()
    bot = api.get_me()
    if not bot.get("is_bot"):
        raise ReaderCommandError("钥匙串凭据不是 Telegram 机器人 Token。")
    webhook = api.get_webhook_info()
    if webhook.get("url"):
        raise ReaderCommandError(
            "该机器人已配置 Webhook，getUpdates 无法同时读取；未自动修改远端配置。"
        )

    updates = api.get_updates()
    candidates = [
        candidate
        for candidate in discover_channels(updates)
    ]
    exact_matches = [
        candidate for candidate in candidates if candidate.title == TARGET_CHANNEL_TITLE
    ]
    if len(exact_matches) == 1:
        selected_chat_id = exact_matches[0].chat_id
    elif not candidates:
        raise ReaderCommandError(
            "尚未发现频道更新。请把读取机器人添加到“数据采集频道”后重新运行 configure。"
        )
    elif len(candidates) == 1:
        selected_chat_id = candidates[0].chat_id
    else:
        print("发现多个频道，请按数字 ID 选择目标频道（不会显示消息正文）：")
        for index, candidate in enumerate(candidates, start=1):
            print(f"  {index}. {candidate.chat_type}，ID={candidate.chat_id}")
        raw_choice = input("频道序号: ").strip()
        try:
            selected_chat_id = candidates[int(raw_choice) - 1].chat_id
        except (ValueError, IndexError):
            raise ReaderCommandError("频道序号无效。") from None

    store.save_config(
        ReaderConfig(
            chat_id=selected_chat_id,
            reader_bot_id=int(bot["id"]),
            configured_at_utc=utc_now_iso(),
        )
    )
    print("配置完成：使用 macOS 钥匙串中的读取机器人 Token。")
    print(f"目标频道 ID：{selected_chat_id}")
    print("本次配置未显示消息正文，也未推进本地同步游标。")


def run_sync(store: MessageStore, *, requested_full: bool) -> None:
    config = require_config(store)
    api = open_bot_api()
    target = api.get_chat(config.chat_id)
    if target.get("type") != "channel":
        raise ReaderCommandError(
            "已配置目标不是 Telegram 频道，拒绝读取；请重新运行 configure。"
        )
    try:
        result = synchronize(store=store, source=api, config=config)
    except Exception as exc:
        store.record_error(type(exc).__name__)
        raise

    if requested_full:
        print("说明：Bot API 不提供历史回溯，full-sync 按可用更新队列执行。")
    print(
        f"同步完成：收到更新 {result.scanned_updates} 条，"
        f"有效数据 {result.accepted_messages} 条，"
        f"忽略指令 {result.ignored_commands} 条，"
        f"忽略其他频道 {result.ignored_other_channels} 条，"
        f"新增或变更 {result.changed_messages} 条。"
    )
    evidence = refresh_strategy_evidence(store, EVIDENCE_PATH)
    print(
        f"策略证据快照：来源消息 {evidence.source_message_id or '无'}，"
        f"明细 {evidence.item_count} 项，时效 {evidence.freshness}；"
        "来源默认不可信，仅作交叉验证一环。"
    )


def run_evidence(store: MessageStore) -> None:
    evidence = refresh_strategy_evidence(store, EVIDENCE_PATH)
    print(
        f"策略证据快照已刷新：来源消息 {evidence.source_message_id or '无'}，"
        f"明细 {evidence.item_count} 项，时效 {evidence.freshness}。"
    )
    print("信任状态：采集机器人默认不可信，只能作为交叉验证的一环。")


def collector_observations(database_path: Path, asset: str = "BTC") -> list:
    """只读导出辅助观察，不打开 Bot API、不创建或迁移正式数据。"""
    sidecar = database_path.parent / COLLECTOR_DATABASE_NAME
    if not sidecar.exists():
        if asset == "all":
            return [{"role": "auxiliary_observation_gap", "asset_id": symbol,
                     "reasons": ["missing_database"], "text_message_id": None,
                     "independent_verified_sources": 0} for symbol in ("BTC", "QQQ", "VOO")]
        return []
    if not sidecar.is_file():
        raise ReaderCommandError("辅助观察路径不是有效数据库文件。")

    # MessageStore 构造器会设置 WAL 并检查迁移，导出路径不能使用它。
    try:
        from btc_evidence_reader import EvidenceReader, open_readonly_snapshot

        with closing(open_readonly_snapshot(database_path)) as config_db:
            row = config_db.execute(
                "SELECT chat_id FROM reader_config WHERE singleton_id = 1"
            ).fetchone()
        if row is None or not isinstance(row[0], int):
            raise ReaderCommandError("辅助观察导出缺少有效的本地频道配置。")

        evidence_reader = EvidenceReader(sidecar, allowed_chat_id=row[0], read_only=True)
        try:
            # 最新缺口或无效附件不能被较旧的有效观察遮盖。
            observations = evidence_reader.observations(latest_only=True, asset=asset)
            if not isinstance(observations, list):
                raise ReaderCommandError("辅助观察导出必须是 JSON 数组。")
            return observations
        finally:
            evidence_reader.close()
    except ReaderCommandError:
        raise
    except Exception as exc:
        raise ReaderCommandError(
            f"辅助观察导出失败（{type(exc).__name__}）。"
        ) from None


def print_status(store: MessageStore) -> None:
    status = store.status()
    config = status["config"]
    state = status["state"] or {}
    if config is None:
        print("状态：尚未配置")
        print(f"数据库：{store.path}")
        return
    print("状态：已配置")
    print(f"数据库：{store.path}")
    print(f"目标频道 ID：{config.chat_id}")
    print(f"读取机器人 ID：{config.reader_bot_id}")
    print(
        f"入库内容：总计 {status['total']}，审计版本 {status['revisions']}"
    )
    print(f"最后 Update ID：{state.get('last_update_id', 0)}")
    print(f"最近成功同步：{state.get('last_success_at_utc') or '尚无'}")
    print(f"最近错误类型：{state.get('last_error_code') or '无'}")
    print(f"策略证据快照：{'已生成' if EVIDENCE_PATH.exists() else '尚无'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用独立 Telegram 机器人读取目标频道内容，忽略 /btc 等指令。"
    )
    parser.add_argument(
        "command",
        choices=("configure", "sync", "full-sync", "status", "evidence", "collector-observations"),
    )
    parser.add_argument("--asset", choices=("BTC", "QQQ", "VOO", "all"), default="BTC",
                        help="collector-observations: latest snapshot per asset; default preserves BTC compatibility")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = build_parser().parse_args(argv)
    try:
        if args.command == "collector-observations":
            print(json.dumps(collector_observations(DATABASE_PATH, args.asset), ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "status":
            with MessageStore(DATABASE_PATH) as store:
                print_status(store)
            return 0

        with ProcessLock(LOCK_PATH):
            with MessageStore(DATABASE_PATH) as store:
                if args.command == "configure":
                    run_configure(store)
                elif args.command == "evidence":
                    run_evidence(store)
                else:
                    run_sync(store, requested_full=args.command == "full-sync")
        return 0
    except AlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 0
    except (ReaderCommandError, StoreError, TelegramReaderError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"同步失败：{type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
