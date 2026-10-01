"""Synthetic reports exercise the real sync, shared reader and export CLI."""
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import btc_evidence
import reader
from btc_evidence_reader import EvidenceReader
from collector_replay_fixture import GENERATED_AT, build_case
from reader_replay_fixture import CHAT_ID, FixtureSource, fixture_config, updates_for_document
from store import MessageStore
from sync_engine import COLLECTOR_DATABASE_NAME, synchronize


OUTPUT_LIMIT = 4 * 1024 * 1024


class CollectorLatestCliTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database_path = self.root / "messages.sqlite3"
        self.sidecar = self.root / COLLECTOR_DATABASE_NAME
        self.store = MessageStore(self.database_path)
        self.addCleanup(self.store.close)
        self.store.save_config(fixture_config())
        self.update_id = 0
        self.now = 1788501600

    def report(self, parent, case="valid", *, document=None, report_id=None,
               corrupt=None):
        built = build_case(case, btc_evidence)
        text = built["text"] + "\nsynthetic-parent=" + str(parent)
        bundle = btc_evidence.build_bundle(
            built["payload"], text, str(parent) if report_id is None else report_id,
            GENERATED_AT)
        self.assertEqual(built["validation"]["status"],
                         btc_evidence.validate_bundle(bundle, text)["status"])
        if corrupt is not None:
            corrupt(bundle)
        raw = btc_evidence.encode_bundle(bundle)
        updates = updates_for_document(text, raw)
        updates[0]["channel_post"]["message_id"] = parent
        post = updates[1]["channel_post"]
        post["message_id"] = parent + 1 if document is None else document
        post["reply_to_message"]["message_id"] = parent
        digest = hashlib.sha256(raw).hexdigest()
        post["document"].update(file_id=digest, file_unique_id=digest)
        return updates, raw

    def sync(self, updates, raw, *, failure=None):
        updates = copy.deepcopy(updates)
        for update in updates:
            self.update_id += 1
            update["update_id"] = self.update_id
        source = FixtureSource(updates, raw if failure is None else failure)
        # 真实共享实现使用固定合成时钟，使字节边界与重试无需等待真实时间。
        with contextlib.closing(EvidenceReader(
                self.sidecar, allowed_chat_id=CHAT_ID, clock=lambda: self.now)) as evidence:
            synchronize(store=self.store, source=source, config=fixture_config(),
                        evidence_reader=evidence)
        return source

    def publish(self, parent, case="valid", **kwargs):
        updates, raw = self.report(parent, case, **kwargs)
        self.sync(updates, raw)
        return updates, raw

    def history(self):
        with contextlib.closing(EvidenceReader(
                self.sidecar, allowed_chat_id=CHAT_ID, read_only=True)) as evidence:
            return evidence.observations()

    def file_state(self):
        return {path.name: (path.stat().st_size, path.stat().st_mtime_ns,
                            hashlib.sha256(path.read_bytes()).hexdigest())
                for path in self.root.iterdir() if path.is_file()}

    def cli(self, expected_exit=0):
        before = self.file_state()
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(reader, "DATABASE_PATH", self.database_path), \
             patch.object(reader, "MessageStore", side_effect=AssertionError("export_must_not_open_store")), \
             patch.object(reader, "ProcessLock", side_effect=AssertionError("export_must_not_take_sync_lock")), \
             patch.object(reader, "open_bot_api", side_effect=AssertionError("export_must_not_open_api")), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            exit_code = reader.main(["collector-observations"])
        self.assertEqual(expected_exit, exit_code, errors.getvalue())
        self.assertEqual(before, self.file_state())
        if expected_exit == 0:
            self.assertEqual("", errors.getvalue())
            self.assertTrue(output.getvalue().endswith("\n"))
            observations = json.loads(output.getvalue())
            self.assertIsInstance(observations, list)
            self.assertLessEqual(len(observations), 1)
            self.assertLessEqual(len(output.getvalue().encode("utf-8")), OUTPUT_LIMIT)
        else:
            self.assertEqual("", output.getvalue())
            self.assertIn("辅助观察导出失败（ValueError）", errors.getvalue())
            self.assertLess(len(errors.getvalue().encode("utf-8")), 200)
            observations = None
        return observations, output.getvalue(), errors.getvalue()

    @staticmethod
    def edit(update):
        post = copy.deepcopy(update["channel_post"])
        post["edit_date"] = post["date"] + 10
        return {"edited_channel_post": post}

    def test_current_missing_spot_is_not_filled_by_valid_history(self):
        self.publish(1)
        self.publish(3, "missing_spot")
        observations, _, _ = self.cli()
        latest = observations[0]
        self.assertEqual("3", latest["report_id"])
        self.assertEqual("degraded_auxiliary", latest["validation"]["status"])
        self.assertNotIn("spot_cvd", latest["datasets"])
        self.assertIn("missing_dataset", latest["validation"]["dataset_reasons"]["spot_cvd"])
        for name in ("futures_1h", "oi_1h"):
            self.assertEqual(240, len(latest["datasets"][name]["rows"]))
        self.assertEqual(0, latest["independent_verified_sources"])
        self.assertEqual(2, len(self.history()))

    def test_late_old_attachment_does_not_outrank_newer_parent(self):
        self.publish(10)
        self.publish(30, "missing_spot")
        updates, raw = self.report(10, document=99)
        source = self.sync([updates[1]], raw)
        self.assertEqual([], source.downloads)
        observations, _, _ = self.cli()
        self.assertEqual(30, observations[0]["transport"]["text_message_id"])
        self.assertEqual("degraded_auxiliary", observations[0]["validation"]["status"])

    def test_latest_download_pending_returns_empty_without_history_fallback(self):
        self.publish(1)
        updates, raw = self.report(3)
        self.sync(updates, raw, failure=ConnectionError("synthetic_download_failure"))
        self.assertEqual([], self.cli()[0])
        self.assertEqual(["1"], [item["report_id"] for item in self.history()])
        self.now += 61
        self.sync([], raw)
        self.assertEqual("3", self.cli()[0][0]["report_id"])

    def test_latest_pending_parent_returns_empty_then_recovers_without_redownload(self):
        self.publish(1)
        updates, raw = self.report(100)
        self.sync([updates[1]], raw)
        self.assertEqual([], self.cli()[0])
        self.assertEqual(1, len(self.history()))
        source = self.sync([updates[0]], raw)
        self.assertEqual([], source.downloads)
        self.assertEqual("100", self.cli()[0][0]["report_id"])

    def test_unknown_schema_and_invalid_digest_do_not_fall_back(self):
        self.publish(1)
        corruptions = (
            lambda bundle: bundle.update(schema="FUTURE_UNSUPPORTED"),
            lambda bundle: bundle.update(bundle_id="btc:" + "0" * 64),
        )
        for parent, corruption in zip((3, 5), corruptions):
            with self.subTest(parent=parent):
                self.publish(parent, corrupt=corruption)
                self.assertEqual([], self.cli()[0])
                self.assertEqual(["1"], [item["report_id"] for item in self.history()])

    def test_latest_parent_edit_invalidates_current_without_filling_old_value(self):
        self.publish(1)
        updates, raw = self.publish(3)
        edited = self.edit(updates[0])
        edited["edited_channel_post"]["text"] = "/btc"
        self.sync([edited], raw)
        self.assertEqual([], self.cli()[0])
        self.assertEqual(["1"], [item["report_id"] for item in self.history()])

    def test_newest_attachment_and_its_current_revision_win(self):
        self.publish(1)
        updates, raw = self.report(1, "missing_spot", document=3)
        # 同一正文的更新附件优先，即使它尚未下载，也不能回退已下载的旧附件。
        self.sync([updates[1]], raw, failure=ConnectionError("synthetic_download_failure"))
        self.assertEqual([], self.cli()[0])
        self.now += 61
        self.sync([], raw)
        # 不同正文哈希仍不允许通过，随后修复成与新附件绑定的正文。
        self.assertEqual([], self.cli()[0])
        self.sync([self.edit(updates[0])], raw)
        latest = self.cli()[0][0]
        self.assertEqual(3, latest["transport"]["document_message_id"])
        self.assertEqual("degraded_auxiliary", latest["validation"]["status"])
        invalid, bad_raw = self.report(
            1, "missing_spot", document=3,
            corrupt=lambda bundle: bundle.update(schema="FUTURE_UNSUPPORTED"))
        self.sync([self.edit(invalid[1])], bad_raw)
        self.assertEqual([], self.cli()[0])

    def test_forty_reports_export_one_complete_snapshot_under_four_mib(self):
        for parent in range(1, 81, 2):
            self.publish(parent)
        history = self.history()
        self.assertEqual(40, len(history))
        history_bytes = len((json.dumps(history, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        self.assertGreater(history_bytes, 8 * 1024 * 1024)
        observations, output, _ = self.cli()
        self.assertEqual("79", observations[0]["report_id"])
        for name in ("futures_1h", "oi_1h", "spot_cvd"):
            self.assertEqual(240, len(observations[0]["datasets"][name]["rows"]))
        self.assertLess(len(output.encode("utf-8")), OUTPUT_LIMIT)

    def test_actual_four_mib_boundary_includes_newline_then_fails_one_extra_byte(self):
        self.publish(1, report_id="r")
        baseline_size = len(self.cli()[1].encode("utf-8"))
        report_id = "r" + "x" * (OUTPUT_LIMIT - baseline_size)
        updates, raw = self.report(1, report_id=report_id)
        self.assertLess(len(raw), 5 * 1024 * 1024)
        self.sync([self.edit(updates[1])], raw)
        _, output, _ = self.cli()
        self.assertEqual(OUTPUT_LIMIT, len(output.encode("utf-8")))
        updates, raw = self.report(1, report_id=report_id + "x")
        self.sync([self.edit(updates[1])], raw)
        self.cli(expected_exit=2)

    def test_utf8_oversized_snapshot_fails_without_partial_json_or_original_writes(self):
        self.publish(1, report_id="超" * (OUTPUT_LIMIT // 3))
        self.assertEqual(1, len(self.history()))
        _, _, errors = self.cli(expected_exit=2)
        self.assertNotIn("超", errors)


if __name__ == "__main__":
    unittest.main()
