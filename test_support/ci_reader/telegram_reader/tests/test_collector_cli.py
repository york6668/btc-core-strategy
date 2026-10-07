import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import reader
from store import MessageStore
from sync_engine import COLLECTOR_DATABASE_NAME
from test_sync_engine import config, raw_update


class LocalOnlyAPI:
    def get_chat(self, chat_id):
        return {"id": chat_id, "type": "channel"}

    def get_updates(self, *, offset=None):
        return [raw_update(1, text="普通正文")]

    def download_document(self, file_id):
        raise AssertionError("普通正文不得触发网络下载")


class CollectorCliTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database_path = self.root / "messages.sqlite3"
        self.addCleanup(self.directory.cleanup)

    def test_missing_sidecar_returns_empty_without_opening_config_or_network(self):
        output = io.StringIO()
        with patch.object(reader, "DATABASE_PATH", self.database_path):
            with patch.object(reader, "MessageStore", side_effect=AssertionError("must not open MessageStore")):
                with patch.object(reader, "open_bot_api", side_effect=AssertionError("must not open Telegram")):
                    with contextlib.redirect_stdout(output):
                        result = reader.main(["collector-observations"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_sidecar_exports_via_common_module_without_changing_original(self):
        from btc_evidence_reader import EvidenceReader

        with MessageStore(self.database_path) as store:
            store.save_config(config())
        sidecar = self.root / COLLECTOR_DATABASE_NAME
        evidence = EvidenceReader(sidecar, allowed_chat_id=config().chat_id)
        expected = evidence.observations()
        evidence.close()
        before = {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (self.database_path, sidecar)
        }
        output = io.StringIO()
        with patch.object(reader, "DATABASE_PATH", self.database_path):
            with patch.object(reader, "MessageStore", side_effect=AssertionError("must not migrate MessageStore")):
                with patch.object(reader, "open_bot_api", side_effect=AssertionError("must not open Telegram")):
                    with contextlib.redirect_stdout(output):
                        result = reader.main(["collector-observations"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before})

    def test_run_sync_uses_common_intake_and_preserves_legacy_text_projection(self):
        with MessageStore(self.database_path) as store:
            store.save_config(config())
            with patch.object(reader, "open_bot_api", return_value=LocalOnlyAPI()):
                with patch.object(reader, "EVIDENCE_PATH", self.root / "strategy-evidence.md"):
                    with contextlib.redirect_stdout(io.StringIO()):
                        reader.run_sync(store, requested_full=False)
            self.assertEqual(store.get_last_update_id(), 1)
            self.assertEqual(store.status()["total"], 1)
        self.assertTrue((self.root / COLLECTOR_DATABASE_NAME).is_file())
        self.assertTrue((self.root / "strategy-evidence.md").is_file())


if __name__ == "__main__":
    unittest.main()
