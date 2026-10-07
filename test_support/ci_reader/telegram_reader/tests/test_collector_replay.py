import contextlib
import copy
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import reader
import btc_evidence_reader
from btc_evidence_reader import EvidenceReader
from reader_replay_fixture import (
    CHAT_ID, FixtureSource, export_cli, fixture_config, fixture_updates, replay_fixture,
)
from store import MessageStore
from sync_engine import COLLECTOR_DATABASE_NAME, synchronize


class CollectorReplayTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database_path = self.root / "messages.sqlite3"
        self.sidecar = self.root / COLLECTOR_DATABASE_NAME
        self.addCleanup(self.directory.cleanup)
        self.store = MessageStore(self.database_path)
        self.store.save_config(fixture_config())
        self.addCleanup(self.store.close)
        self.updates, self.raw = fixture_updates()

    def sync(self, updates, raw=None):
        source = FixtureSource(updates, self.raw if raw is None else raw)
        result = synchronize(store=self.store, source=source, config=fixture_config())
        return result, source

    def evidence_row(self):
        with contextlib.closing(sqlite3.connect(self.sidecar)) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM collector_attachments").fetchone())

    def restart(self):
        self.store.close()
        self.store = MessageStore(self.database_path)
        self.addCleanup(self.store.close)

    def test_real_cli_botapi_intake_download_and_nonempty_export(self):
        observations, receipt = replay_fixture(self.root / "cli")
        self.assertEqual(receipt["api_calls"], ["getChat", "getUpdates", "getFile", "download_document"])
        self.assertEqual(receipt["validation_status"], "valid_auxiliary")
        self.assertEqual(observations[0]["independent_verified_sources"], 0)
        self.assertTrue(observations[0]["requires_independent_verification"])
        self.assertTrue(observations[0]["requires_current_source_check"])
        self.assertNotIn("decision", observations[0])
        self.assertNotIn("position_fact", observations[0])

    def test_no_caption_is_ingested_before_legacy_parser_drops_media_body(self):
        self.assertNotIn("caption", self.updates[1]["channel_post"])
        result, source = self.sync(self.updates)
        self.assertEqual(result.accepted_messages, 1)
        self.assertEqual(self.store.status()["total"], 1)
        self.assertEqual(source.downloads, ["offline-fixture"])
        self.assertEqual(len(export_cli(self.database_path)), 1)

    def test_body_commit_precedes_download(self):
        source = FixtureSource(self.updates, self.raw)
        download = source.download_document

        def checked_download(file_id):
            self.assertEqual(self.store.get_last_update_id(), 102)
            self.assertEqual(self.store.status()["total"], 1)
            self.assertEqual(self.evidence_row()["download_status"], "pending")
            return download(file_id)

        source.download_document = checked_download
        synchronize(store=self.store, source=source, config=fixture_config())
        self.assertEqual(self.evidence_row()["download_status"], "received")

    def test_intake_failure_never_confirms_text_or_cursor(self):
        original = EvidenceReader.ingest_update

        def fail_second(evidence, update):
            if update["update_id"] == 102:
                raise sqlite3.OperationalError("injected_intake_failure")
            return original(evidence, update)

        with patch.object(EvidenceReader, "ingest_update", new=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.sync(self.updates)
        self.assertEqual(self.store.get_last_update_id(), 0)
        self.assertEqual(self.store.status()["total"], 0)
        self.restart()
        self.sync(self.updates)
        self.assertEqual(self.store.get_last_update_id(), 102)
        self.assertEqual(len(export_cli(self.database_path)), 1)

    def test_body_failure_replays_duplicate_intake_after_restart(self):
        source = FixtureSource(self.updates, self.raw)
        with patch.object(self.store, "apply_sync_batch", side_effect=sqlite3.OperationalError("injected_body_failure")):
            with self.assertRaises(sqlite3.OperationalError):
                synchronize(store=self.store, source=source, config=fixture_config())
        self.assertEqual(self.store.get_last_update_id(), 0)
        self.assertEqual(source.downloads, [])
        self.assertEqual(self.evidence_row()["download_status"], "pending")
        self.restart()
        result, replay = self.sync(self.updates)
        self.assertIsNone(replay.offsets[0])
        self.assertEqual(result.accepted_messages, 1)
        self.assertEqual(self.store.get_last_update_id(), 102)
        self.assertEqual(len(export_cli(self.database_path)), 1)

    def test_download_failure_recovers_on_empty_update_after_restart(self):
        self.sync(self.updates, raw=ConnectionError("offline_fixture_failure"))
        self.assertEqual(self.store.get_last_update_id(), 102)
        self.assertEqual(self.evidence_row()["download_status"], "pending")
        self.assertEqual(self.evidence_row()["download_attempts"], 1)
        self.assertEqual(export_cli(self.database_path), [])
        self.restart()
        # Advance only the persisted synthetic retry deadline; no wall-clock sleep.
        with contextlib.closing(sqlite3.connect(self.sidecar)) as db:
            db.execute("UPDATE collector_attachments SET download_retry_at=0")
            db.commit()
        result, source = self.sync([])
        self.assertEqual(result.scanned_updates, 0)
        self.assertEqual(source.offsets, [103])
        self.assertEqual(source.downloads, ["offline-fixture"])
        self.assertEqual(len(export_cli(self.database_path)), 1)

    def test_attachment_first_waits_for_parent_across_restart(self):
        self.sync([self.updates[1]])
        self.assertEqual(json.loads(self.evidence_row()["validation"])["status"], "pending_text")
        self.assertEqual(export_cli(self.database_path), [])
        self.restart()
        parent = copy.deepcopy(self.updates[0])
        parent["update_id"] = 103
        _, source = self.sync([parent])
        self.assertEqual(source.downloads, [])
        self.assertEqual(len(export_cli(self.database_path)), 1)

    def test_same_bundle_new_document_is_deduplicated_without_second_download(self):
        self.sync(self.updates)
        duplicate = copy.deepcopy(self.updates[1])
        duplicate["update_id"] = 103
        duplicate["channel_post"]["message_id"] = 203
        duplicate["channel_post"]["date"] += 1
        _, source = self.sync([duplicate])
        self.assertEqual(source.downloads, [])
        self.assertEqual(len(export_cli(self.database_path)), 1)

    def test_parent_edit_to_command_invalidates_evidence_but_preserves_legacy_ignore(self):
        self.sync(self.updates)
        post = copy.deepcopy(self.updates[0]["channel_post"])
        post["text"] = "/btc"
        post["edit_date"] = post["date"] + 10
        result, source = self.sync([{"update_id": 103, "edited_channel_post": post}])
        self.assertEqual(result.ignored_commands, 1)
        self.assertEqual(self.store.status()["revisions"], 1)
        self.assertEqual(source.downloads, [])
        self.assertEqual(export_cli(self.database_path), [])
        self.assertIn("text_hash_mismatch", json.loads(self.evidence_row()["validation"])["reasons"])

    def test_unknown_schema_does_not_block_later_text(self):
        bundle = json.loads(self.raw)
        bundle["schema"] = "UNKNOWN_FUTURE_SCHEMA"
        raw = json.dumps(bundle).encode()
        updates = copy.deepcopy(self.updates)
        updates[1]["channel_post"]["document"]["file_size"] = len(raw)
        later = copy.deepcopy(updates[0])
        later["update_id"] = 103
        later["channel_post"]["message_id"] = 203
        later["channel_post"]["text"] = "later independent text"
        self.sync(updates + [later], raw=raw)
        self.assertEqual(self.store.get_last_update_id(), 103)
        self.assertEqual(self.store.status()["total"], 2)
        self.assertEqual(export_cli(self.database_path), [])
        self.assertIn("unknown_schema", json.loads(self.evidence_row()["validation"])["reasons"])

    def test_degraded_fixture_exports_quality_receipt_without_verified_upgrade(self):
        observations, receipt = replay_fixture(self.root / "missing", name="missing")
        self.assertEqual(receipt["validation_status"], "degraded_auxiliary")
        self.assertEqual(observations[0]["independent_verified_sources"], 0)
        self.assertTrue(observations[0]["validation"]["dataset_reasons"])

    def test_other_chat_and_group_never_reach_download_or_observations(self):
        for kind in ("other_channel", "same_id_group"):
            with self.subTest(kind=kind):
                updates = copy.deepcopy(self.updates)
                for update in updates:
                    chat = update["channel_post"]["chat"]
                    if kind == "other_channel":
                        chat["id"] = CHAT_ID - 1
                    else:
                        chat["type"] = "supergroup"
                _, source = self.sync(updates)
                self.assertEqual(source.downloads, [])
                self.assertEqual(export_cli(self.database_path), [])

    def test_export_is_nonmutating_readonly_even_with_live_wal(self):
        wrong_config = fixture_config()
        wrong_config = type(wrong_config)(chat_id=CHAT_ID - 1, reader_bot_id=987,
                                          configured_at_utc=wrong_config.configured_at_utc)
        self.store.save_config(wrong_config)
        with contextlib.closing(sqlite3.connect(self.database_path)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        self.store.save_config(fixture_config())
        writer = EvidenceReader(self.sidecar, CHAT_ID)
        self.addCleanup(writer.close)
        writer.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.sync(self.updates)
        self.assertGreater(Path(str(self.sidecar) + "-wal").stat().st_size, 32)
        self.assertGreater(Path(str(self.database_path) + "-wal").stat().st_size, 32)
        before = "\n".join(writer.db.iterdump())
        file_before = self.database_files()
        output = export_cli(self.database_path)
        self.assertEqual(len(output), 1)
        self.assertEqual(before, "\n".join(writer.db.iterdump()))
        self.assertEqual(file_before, self.database_files())
        with patch.object(btc_evidence_reader.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            export_cli(self.database_path)
        self.assertGreaterEqual(len(connect.call_args_list), 4)
        for call in connect.call_args_list:
            path = str(call.args[0])
            self.assertNotIn(str(self.database_path), path)
            self.assertNotIn(str(self.sidecar), path)

    def database_files(self, directory=None):
        return {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in (directory or self.root).glob("*.sqlite3*")}

    def test_closed_wal_mode_databases_export_without_creating_side_files(self):
        self.sync(self.updates)
        self.store.close()
        closed = self.root / "closed-data"
        closed.mkdir()
        for path in (self.database_path, self.sidecar):
            with contextlib.closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            shutil.copyfile(path, closed / path.name)
        before = self.database_files(closed)
        self.assertEqual(set(before), {self.database_path.name, self.sidecar.name})
        self.assertEqual(len(export_cli(closed / self.database_path.name)), 1)
        self.assertEqual(before, self.database_files(closed))

    def test_raw_collector_valid_payload_reaches_actual_reader(self):
        _, receipt = replay_fixture(self.root / "raw-valid", from_payload=True)
        self.assertEqual(receipt["input_kind"], "raw_payload_via_actual_build_bundle")
        self.assertEqual(receipt["validation_status"], "valid_auxiliary")
        self.assertEqual(receipt["dataset_reasons"], {})

    def test_raw_collector_missing_spot_is_preserved_as_degraded(self):
        _, receipt = replay_fixture(self.root / "raw-missing", "missing_spot", from_payload=True)
        self.assertEqual(receipt["validation_status"], "degraded_auxiliary")
        self.assertIn("missing_dataset", receipt["dataset_reasons"]["spot_cvd"])

    def test_raw_collector_half_hour_spot_is_preserved_as_degraded(self):
        _, receipt = replay_fixture(self.root / "raw-half-hour", "half_hour_spot", from_payload=True)
        self.assertEqual(receipt["validation_status"], "degraded_auxiliary")
        self.assertIn("invalid_utc_hour_boundary", receipt["dataset_reasons"]["spot_cvd"])

    def test_raw_collector_misaligned_spot_is_preserved_as_degraded(self):
        _, receipt = replay_fixture(self.root / "raw-misaligned", "misaligned_spot", from_payload=True)
        self.assertEqual(receipt["validation_status"], "degraded_auxiliary")
        self.assertIn("core_hour_alignment_mismatch", receipt["dataset_reasons"]["spot_cvd"])

    def test_export_existing_sidecar_missing_config_never_creates_message_database(self):
        directory = self.root / "unconfigured"
        directory.mkdir()
        evidence = EvidenceReader(directory / COLLECTOR_DATABASE_NAME, CHAT_ID)
        evidence.close()
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
        with patch.object(reader, "DATABASE_PATH", directory / "messages.sqlite3"), \
             contextlib.redirect_stdout(io.StringIO()) as output, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(reader.main(["collector-observations"]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertFalse((directory / "messages.sqlite3").exists())
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()})


if __name__ == "__main__":
    unittest.main()
