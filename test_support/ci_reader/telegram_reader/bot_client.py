from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from models import FetchedMessage, unix_utc_iso


KEYCHAIN_SERVICE = "btc.telegram.reader"
KEYCHAIN_ACCOUNT = "bot_token"
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
TELEGRAM_API_ORIGIN = "https://api.telegram.org"


class TelegramReaderError(RuntimeError):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """读取 URL 的路径含凭据，禁止跟随重定向把它带到其他地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TelegramReaderError("Telegram 读取请求拒绝重定向。")


class CredentialVault:
    def __init__(self, backend: Any | None = None) -> None:
        if backend is None:
            try:
                import keyring
            except ImportError:
                raise TelegramReaderError(
                    "缺少 keyring；请先运行 ./install.sh。"
                ) from None
            backend = keyring
        self._backend = backend

    def bot_token(self) -> str:
        token = self._backend.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        if not token:
            raise TelegramReaderError(
                "macOS 钥匙串中没有读取机器人 Token，请先按 README 保存。"
            )
        return token


class BotAPI:
    """只暴露读取所需的 Telegram Bot API 方法。"""

    def __init__(self, token: str, opener: Any | None = None) -> None:
        if not token:
            raise TelegramReaderError("读取机器人 Token 不能为空。")
        self._base_url = f"{TELEGRAM_API_ORIGIN}/bot{token}/"
        self._file_base_url = f"{TELEGRAM_API_ORIGIN}/file/bot{token}/"
        self._opener = opener or urllib.request.build_opener(_RejectRedirects()).open

    def _request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        payload = json.dumps(params or {}, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self._base_url + method,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = max(30, int((params or {}).get("timeout", 0)) + 10)
        try:
            with self._opener(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise TelegramReaderError(
                f"Telegram Bot API 请求失败（HTTP {exc.code}）。"
            ) from None
        except urllib.error.URLError:
            raise TelegramReaderError("无法连接 Telegram Bot API。") from None

        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramReaderError("Telegram Bot API 返回了无效响应。") from None
        if not isinstance(decoded, dict) or not decoded.get("ok"):
            code = decoded.get("error_code", "unknown") if isinstance(decoded, dict) else "unknown"
            if not isinstance(code, int) or isinstance(code, bool):
                code = "unknown"
            raise TelegramReaderError(
                f"Telegram Bot API 拒绝了读取请求（错误码 {code}）。"
            )
        return decoded.get("result")

    def get_me(self) -> dict[str, Any]:
        result = self._request("getMe")
        if not isinstance(result, dict):
            raise TelegramReaderError("getMe 返回格式异常。")
        return result

    def get_webhook_info(self) -> dict[str, Any]:
        result = self._request("getWebhookInfo")
        if not isinstance(result, dict):
            raise TelegramReaderError("getWebhookInfo 返回格式异常。")
        return result

    def get_chat(self, chat_id: int) -> dict[str, Any]:
        result = self._request("getChat", {"chat_id": chat_id})
        if not isinstance(result, dict):
            raise TelegramReaderError("getChat 返回格式异常。")
        return result

    def get_updates(self, *, offset: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": 0,
            "limit": 100,
            "allowed_updates": ALLOWED_UPDATE_TYPES,
        }
        if offset is not None:
            params["offset"] = offset
        result = self._request("getUpdates", params)
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise TelegramReaderError("getUpdates 返回格式异常。")
        return result

    def get_file(self, file_id: str) -> dict[str, Any]:
        """只读取 Telegram 文件元数据，不接受证据载荷中的下载 URL。"""
        if not isinstance(file_id, str) or not file_id.strip():
            raise TelegramReaderError("Telegram 文件标识无效。")
        try:
            result = self._request("getFile", {"file_id": file_id})
        except TelegramReaderError:
            raise
        except Exception as exc:
            raise TelegramReaderError(
                f"Telegram 文件元数据读取失败（{type(exc).__name__}）。"
            ) from None
        if not isinstance(result, dict) or result.get("file_id") != file_id:
            raise TelegramReaderError("getFile 返回的文件标识不一致。")
        return result

    def download_document(self, file_id: str) -> bytes:
        """有界下载官方文件字节；内容与 manifest 的校验由共同模块负责。"""
        try:
            metadata = self.get_file(file_id)
            file_path = metadata.get("file_path")
            if (
                not isinstance(file_path, str)
                or len(file_path) > 2048
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", file_path)
                or any(part in ("", ".", "..") for part in file_path.split("/"))
            ):
                raise TelegramReaderError("getFile 返回的文件路径无效。")

            declared_size = metadata.get("file_size")
            if declared_size is not None and (
                not isinstance(declared_size, int)
                or isinstance(declared_size, bool)
                or not 0 <= declared_size <= MAX_DOWNLOAD_BYTES
            ):
                raise TelegramReaderError("Telegram 文件声明大小无效或超过 5 MiB。")

            url = self._file_base_url + file_path
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/octet-stream", "Accept-Encoding": "identity"},
                method="GET",
            )
            with self._opener(request, timeout=30) as response:
                # 默认 opener 已禁重定向；注入的传输同样不得改变目标 URL。
                if hasattr(response, "geturl") and response.geturl() != url:
                    raise TelegramReaderError("Telegram 文件下载目标不一致。")
                if hasattr(response, "getcode") and response.getcode() != 200:
                    raise TelegramReaderError("Telegram 文件下载响应状态无效。")
                headers = response.headers
                if headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                    raise TelegramReaderError("Telegram 文件下载拒绝内容编码转换。")
                content_length = headers.get("Content-Length")
                if content_length is not None:
                    if not isinstance(content_length, str) or not content_length.isdecimal():
                        raise TelegramReaderError("Telegram 文件响应长度无效。")
                    content_length = int(content_length)
                    if content_length > MAX_DOWNLOAD_BYTES:
                        raise TelegramReaderError("Telegram 文件响应超过 5 MiB。")
                    if declared_size is not None and content_length != declared_size:
                        raise TelegramReaderError("Telegram 文件声明与响应长度不一致。")

                # 声明长度不可信，实际读取独立按上限加一截断。
                content = response.read(MAX_DOWNLOAD_BYTES + 1)
                if not isinstance(content, bytes):
                    raise TelegramReaderError("Telegram 文件响应不是字节流。")
                if len(content) > MAX_DOWNLOAD_BYTES:
                    raise TelegramReaderError("Telegram 文件实际大小超过 5 MiB。")

                if declared_size is not None and len(content) != declared_size:
                    raise TelegramReaderError("Telegram 文件实际大小与声明不一致。")
                if content_length is not None and len(content) != content_length:
                    raise TelegramReaderError("Telegram 文件响应长度与实际大小不一致。")
                return content
        except TelegramReaderError:
            raise
        except urllib.error.HTTPError as exc:
            raise TelegramReaderError(
                f"Telegram 文件下载失败（HTTP {exc.code}）。"
            ) from None
        except Exception as exc:
            # 异常原文可能带含凭据的 URL，只输出稳定异常类型。
            raise TelegramReaderError(
                f"Telegram 文件下载失败（{type(exc).__name__}）。"
            ) from None


@dataclass(frozen=True)
class ChannelCandidate:
    chat_id: int
    chat_type: str
    title: str | None


CHANNEL_UPDATE_TYPES = (
    "channel_post",
    "edited_channel_post",
)

ALLOWED_UPDATE_TYPES = [
    "channel_post",
    "edited_channel_post",
    "my_chat_member",
]


def _channel_post_from_update(update: dict[str, Any]) -> dict[str, Any] | None:
    for update_type in CHANNEL_UPDATE_TYPES:
        value = update.get(update_type)
        if isinstance(value, dict):
            return value
    return None


def discover_channels(updates: list[dict[str, Any]]) -> list[ChannelCandidate]:
    found: dict[int, ChannelCandidate] = {}
    for update in updates:
        raw_message = _channel_post_from_update(update)
        chat = raw_message.get("chat") if raw_message is not None else None
        if not isinstance(chat, dict):
            membership = update.get("my_chat_member")
            if isinstance(membership, dict) and isinstance(membership.get("chat"), dict):
                chat = membership["chat"]
        if not isinstance(chat, dict) or chat.get("type") != "channel":
            continue
        try:
            chat_id = int(chat["id"])
        except (KeyError, TypeError, ValueError):
            continue
        found[chat_id] = ChannelCandidate(
            chat_id=chat_id,
            chat_type=str(chat.get("type")),
            title=str(chat["title"]) if chat.get("title") else None,
        )
    return sorted(found.values(), key=lambda item: item.chat_id)


def _content_kind(message: dict[str, Any]) -> str:
    if "text" in message:
        return "text"
    for key in (
        "animation",
        "audio",
        "document",
        "paid_media",
        "photo",
        "sticker",
        "story",
        "video",
        "video_note",
        "voice",
    ):
        if key in message:
            return key
    return "media"


def _starts_with_bot_command(
    message: dict[str, Any], *, caption: bool, text: str
) -> bool:
    entity_key = "caption_entities" if caption else "entities"
    entities = message.get(entity_key)
    if isinstance(entities, list) and any(
        isinstance(entity, dict)
        and entity.get("type") == "bot_command"
        and entity.get("offset") == 0
        for entity in entities
    ):
        return True
    return bool(re.match(r"^/[A-Za-z0-9_]+(?:@[A-Za-z0-9_]+)?(?:\s|$)", text.lstrip()))


def parse_channel_post_update(update: dict[str, Any]) -> FetchedMessage | None:
    raw_message = _channel_post_from_update(update)
    if raw_message is None:
        return None
    chat = raw_message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "channel":
        return None
    try:
        update_id = int(update["update_id"])
        chat_id = int(chat["id"])
        message_id = int(raw_message["message_id"])
    except (KeyError, TypeError, ValueError):
        return None

    caption = "text" not in raw_message and isinstance(raw_message.get("caption"), str)
    raw_text = raw_message.get("caption") if caption else raw_message.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None

    sender = raw_message.get("from")
    sender_chat = raw_message.get("sender_chat")
    sender_id: int | None = None
    sender_is_bot = False
    if isinstance(sender, dict):
        try:
            sender_id = int(sender["id"])
        except (KeyError, TypeError, ValueError):
            sender_id = None
        sender_is_bot = bool(sender.get("is_bot", False))
    elif isinstance(sender_chat, dict):
        try:
            sender_id = int(sender_chat["id"])
        except (KeyError, TypeError, ValueError):
            sender_id = None

    return FetchedMessage(
        update_id=update_id,
        chat_id=chat_id,
        message_id=message_id,
        sender_id=sender_id,
        sender_is_bot=sender_is_bot,
        sent_at_utc=unix_utc_iso(raw_message.get("date")),
        edited_at_utc=unix_utc_iso(raw_message.get("edit_date")),
        text=raw_text,
        content_kind=_content_kind(raw_message),
        is_bot_command=_starts_with_bot_command(
            raw_message, caption=caption, text=raw_text
        ),
    )
