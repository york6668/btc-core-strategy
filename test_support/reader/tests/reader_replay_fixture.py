"""Replay root fixtures through the real reader and download adapter."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import reader
from bot_client import BotAPI, MAX_DOWNLOAD_BYTES, TELEGRAM_API_ORIGIN
from models import ReaderConfig
from store import MessageStore


FIXTURES = Path(__file__).parent / "fixtures" / "btc_evidence_v1"
CHAT_ID = -1000000000001


def fixture_updates(name="valid"):
    raw = (FIXTURES / (name + ".json")).read_bytes()
    text = (FIXTURES / (name + ".txt")).read_bytes().decode("utf-8")
    return updates_for_document(text, raw), raw


def updates_for_document(text, raw):
    template = json.loads((FIXTURES / "telegram_updates.json").read_text())
    updates = [template["text_update"], template["document_update"]]
    for update in updates:
        update["channel_post"]["chat"]["type"] = "channel"
    updates[0]["channel_post"]["text"] = text
    updates[1]["channel_post"]["document"]["file_size"] = len(raw)
    return updates


def raw_payload_updates(case="valid"):
    import btc_evidence
    from collector_replay_fixture import build_case

    built = build_case(case, btc_evidence)
    return updates_for_document(built["text"], built["encoded"]), built["encoded"]


def fixture_config():
    return ReaderConfig(chat_id=CHAT_ID, reader_bot_id=987,
                        configured_at_utc="2026-09-04T06:00:00+00:00")


class FixtureSource:
    def __init__(self, updates, raw):
        self.updates, self.raw = copy.deepcopy(updates), raw
        self.offsets, self.downloads = [], []

    def get_updates(self, *, offset=None):
        self.offsets.append(offset)
        return copy.deepcopy(self.updates)

    def download_document(self, file_id):
        self.downloads.append(file_id)
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw


class FixtureResponse(io.BytesIO):
    def __init__(self, raw, url, headers=None):
        super().__init__(raw)
        self.url, self.headers, self.read_sizes = url, headers or {}, []

    def geturl(self):
        return self.url

    def getcode(self):
        return 200

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class FixtureTelegramOpener:
    """No socket calls; requests still pass through the production BotAPI methods."""
    def __init__(self, updates, raw):
        self.updates, self.raw = updates, raw
        self.calls, self.download_response = [], None

    def __call__(self, request, *, timeout):
        endpoint = request.full_url.rsplit("/", 1)[-1]
        params = json.loads(request.data) if request.data else {}
        if request.full_url.startswith(TELEGRAM_API_ORIGIN + "/botoffline-fixture-only/"):
            self.calls.append(endpoint)
            if endpoint == "getChat":
                assert params["chat_id"] == CHAT_ID
                result = {"id": CHAT_ID, "type": "channel"}
            elif endpoint == "getUpdates":
                result = self.updates
            elif endpoint == "getFile":
                assert params["file_id"] == "offline-fixture"
                result = {"file_id": "offline-fixture", "file_path": "documents/fixture.json",
                          "file_size": len(self.raw)}
            else:
                raise AssertionError("unexpected_offline_api_method")
            return FixtureResponse(json.dumps({"ok": True, "result": result}).encode(), request.full_url)
        assert request.full_url == TELEGRAM_API_ORIGIN + "/file/botoffline-fixture-only/documents/fixture.json"
        self.calls.append("download_document")
        self.download_response = FixtureResponse(self.raw, request.full_url,
                                                 {"Content-Length": str(len(self.raw))})
        return self.download_response


def export_cli(database_path):
    output = io.StringIO()
    with patch.object(reader, "DATABASE_PATH", database_path), \
         patch.object(reader, "MessageStore", side_effect=AssertionError("export_must_not_open_store")), \
         patch.object(reader, "ProcessLock", side_effect=AssertionError("export_must_not_take_sync_lock")), \
         patch.object(reader, "open_bot_api", side_effect=AssertionError("export_must_not_open_api")), \
         contextlib.redirect_stdout(output):
        result = reader.main(["collector-observations"])
    assert result == 0, "collector_cli_export_failed"
    observations = json.loads(output.getvalue())
    assert isinstance(observations, list)
    return observations


def replay_fixture(directory, name="valid", *, from_payload=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    database_path = directory / "messages.sqlite3"
    with MessageStore(database_path) as store:
        store.save_config(fixture_config())
    updates, raw = raw_payload_updates(name) if from_payload else fixture_updates(name)
    opener = FixtureTelegramOpener(updates, raw)
    api = BotAPI("offline-fixture-only", opener=opener)
    with patch.object(reader, "DATABASE_PATH", database_path), \
         patch.object(reader, "LOCK_PATH", directory / "reader.lock"), \
         patch.object(reader, "EVIDENCE_PATH", directory / "synthetic-evidence.md"), \
         patch.object(reader, "open_bot_api", return_value=api), \
         contextlib.redirect_stdout(io.StringIO()):
        assert reader.main(["sync"]) == 0, "reader_cli_sync_failed"
    observations = export_cli(database_path)
    assert len(observations) == 1
    assert opener.download_response.read_sizes == [MAX_DOWNLOAD_BYTES + 1]
    return observations, {
        "fixture": name, "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "input_kind": "raw_payload_via_actual_build_bundle" if from_payload else "root_encoded_fixture",
        "synthetic_only": True, "api_calls": opener.calls,
        "download_read_sizes": opener.download_response.read_sizes,
        "observation_count": len(observations),
        "validation_status": observations[0]["validation"]["status"],
        "dataset_reasons": observations[0]["validation"]["dataset_reasons"],
        "independent_verified_sources": observations[0]["independent_verified_sources"],
    }
