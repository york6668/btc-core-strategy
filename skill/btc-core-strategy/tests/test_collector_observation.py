"""Exercise the real observation/fingerprint/decision CLI with local exports.

All generated files are temporary.  This is consumer-boundary coverage, not a
replacement for collector-owned validation or a live market evaluation.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import collector_observation as collector
import strategy_contract as contract
import test_production_strategy as production
import test_strategy_contract as legacy

READER_ROOT = Path(__import__('os').environ['BTC_COLLECTOR_CONTRACT_PATH']).parent
sys.path.insert(0, str(READER_ROOT / "tests"))
from collector_replay_fixture import protocol_fixture_directory
FIXTURES = protocol_fixture_directory()
sys.path.insert(0, str(READER_ROOT))
import btc_evidence as evidence
import reader
from models import ReaderConfig
from store import MessageStore
from sync_engine import synchronize


class CollectorObservationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="btc-aux-cli-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def run_cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = contract.main(arguments)
        return code, json.loads(output.getvalue())

    def real_reader_export(self, *, fixture="valid", mutate=None, edit_parent=False):
        """Actual synchronize -> shared validator -> actual local reader CLI.

        Only the external update/download source and local DB location are
        substituted. The quality result, projection and CLI are not mocked.
        """
        raw = (FIXTURES / (fixture + ".json")).read_bytes()
        if fixture == "valid":
            self.assertEqual(hashlib.sha256(raw).hexdigest(),
                             "e6c2b76452665691b82812f79a300f1375a241e36387ea2c484fbdee4eba38ab")
        if mutate:
            value = evidence.decode_bundle(raw)
            mutate(value)
            body = {key: value for key, value in value.items() if key != "bundle_id"}
            value["bundle_id"] = "btc:" + hashlib.sha256(evidence.canonical_bytes(body)).hexdigest()
            raw = evidence.encode_bundle(value)
        updates = json.loads((FIXTURES / "telegram_updates.json").read_text(encoding="utf-8"))
        text_update, document_update = updates["text_update"], updates["document_update"]
        text_update["channel_post"]["text"] = (FIXTURES / (fixture + ".txt")).read_text(encoding="utf-8")
        for update in (text_update, document_update):
            update["channel_post"]["chat"]["type"] = "channel"
        document_update["channel_post"]["document"]["file_size"] = len(raw)
        batch = [document_update, text_update]  # document before parent
        if edit_parent:
            edit = deepcopy(text_update)
            edit["update_id"] = 103
            edit["edited_channel_post"] = edit.pop("channel_post")
            edit["edited_channel_post"]["edit_date"] = 1788501660
            edit["edited_channel_post"]["text"] += "\nchanged parent body"
            batch.append(edit)

        class LocalSource:
            def get_updates(self, *, offset=None):
                return deepcopy(batch)

            def download_document(self, file_id):
                if file_id != "offline-fixture":
                    raise AssertionError("unexpected offline file reference")
                return raw

        with tempfile.TemporaryDirectory(dir=self.root, prefix="reader-") as directory:
            database = Path(directory) / "messages.sqlite3"
            config = ReaderConfig(chat_id=text_update["channel_post"]["chat"]["id"],
                                  reader_bot_id=987, configured_at_utc="2026-09-04T06:00:00+00:00")
            with MessageStore(database) as store:
                store.save_config(config)
                synchronize(store=store, source=LocalSource(), config=config)
            output = io.StringIO()
            with mock.patch.object(reader, "DATABASE_PATH", database), mock.patch.object(
                reader, "open_bot_api", side_effect=AssertionError("network prohibited")
            ), redirect_stdout(output):
                code = reader.main(["collector-observations"])
            self.assertEqual(code, 0, output.getvalue())
            return json.loads(output.getvalue())

    def test_classify_zone_subprocess_consumes_actual_empty_reader_export(self):
        prices = self.write_json("prices.json", {"binance": "77604", "kraken": "77567"})
        observations = self.write_json("observations.json", [])
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "strategy_contract.py"),
             "classify-zone", "--input", prices, "--lower", "77000", "--upper", "77500",
             "--collector-observations", observations],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["price_relation"], "ABOVE_ZONE")
        self.assertEqual(output["collector_review"]["quality_status"], "EMPTY")
        self.assertFalse(output["collector_review"]["review_candidate"])

    def test_existing_fingerprints_cli_keeps_all_canonical_fingerprints(self):
        value = legacy.make_bundle()
        path = self.write_json("bundle.json", value)
        observations = self.write_json("observations.json", [])
        code, actual = self.run_cli([
            "fingerprints", "--input", path, "--collector-observations", observations,
        ])
        self.assertEqual(code, 0)
        for field, expected in contract.compute_fingerprints(value).items():
            self.assertEqual(actual[field], expected)
        self.assertEqual(actual["collector_review"]["independent_verified_sources"], 0)
        self.assertNotIn("collector_review", value)

    def check_real_decision_cli(self, observations, *, tamper=False, flip_spot=False, previous_export=None):
        with production.frozen_clock(production.events.INITIAL):
            previous, _ = (production.verified_spot_migration() if flip_spot else production.migration_bundle())
        with production.frozen_clock(production.events.BEFORE_FINAL), production.rendered_baseline(previous) as (baseline, _, _):
            bundle = production.daily_bundle(previous, baseline, production.events.BEFORE_FINAL)
            if flip_spot:
                bundle["evidence_matrix"]["spot_flow"]["evidence_claims"][0]["direction"] = "SELL"
                production._report(bundle)
                production._finalize(bundle)
            before = deepcopy(bundle)
            if tamper:
                bundle["fingerprints"]["decision_fingerprint"] = "sha256:" + "0" * 64
            path = self.write_json("bundle.json", bundle)
            exported = self.write_json("observations.json", observations)
            # Bind the unmodified production CLI to isolated actual Owner files;
            # no validator, fingerprint, decision or report gate is mocked.
            with mock.patch.object(contract, "OWNER_STATE_PATH", baseline.state_path), mock.patch.object(contract, "OWNER_PLAN_PATH", baseline.plan_path):
                arguments = [
                    "validate-bundle", "--input", path,
                    "--current-state", str(baseline.state_path),
                    "--current-plan", str(baseline.plan_path),
                    "--collector-observations", exported,
                ]
                if previous_export is not None:
                    arguments += ["--previous-collector-observations", self.write_json("prior-export.json", previous_export)]
                code, output = self.run_cli(arguments)
            return code, output, before

    def test_real_decision_cli_empty_auxiliary_export_remains_silent_a(self):
        code, output, before = self.check_real_decision_cli([])
        self.assertEqual(code, 0, output)
        self.assertEqual(output["fingerprints"], contract.compute_fingerprints(before))
        self.assertEqual(output["derived_control"]["monitor_state"], "A")
        self.assertFalse(output["derived_control"]["project_patch_required"])
        self.assertFalse(output["derived_control"]["report_required"])
        self.assertFalse(output["derived_control"]["attention_planned"])

    def test_auxiliary_input_cannot_bypass_existing_decision_guard(self):
        code, output, _ = self.check_real_decision_cli([], tamper=True)
        self.assertEqual(code, 2)
        self.assertTrue(output["hard_failure"])
        self.assertTrue(any("decision_fingerprint" in error for error in output["errors"]))
        self.assertNotIn("derived_control", output)

    def test_unbound_quality_is_not_treated_as_verified_evidence(self):
        exported = [{"role": "auxiliary_observation", "validation": {"valid": True}}]
        review = collector.review_exports(exported)
        self.assertEqual(review["independent_verified_sources"], 0)
        self.assertEqual(review["accepted_observations"], [])
        self.assertFalse(review["review_candidate"])
        self.assertTrue(review["rejected_observations"][0]["reasons"])

    def test_malformed_export_fails_before_cli_emits_price_output(self):
        prices = self.write_json("prices.json", {"binance": "77604", "kraken": "77567"})
        for raw in ('{}', '[NaN]', '[1e999]', '[{"role":1,"role":2}]', '[1,', 'null'):
            with self.subTest(raw=raw):
                exported = self.root / "bad.json"
                exported.write_text(raw, encoding="utf-8")
                code, output = self.run_cli([
                    "classify-zone", "--input", prices, "--lower", "77000", "--upper", "77500",
                    "--collector-observations", str(exported),
                ])
                self.assertEqual(code, 2)
                self.assertTrue(output["hard_failure"])
                self.assertNotIn("price_relation", output)

    def test_previous_export_without_current_export_is_rejected(self):
        path = self.write_json("bundle.json", legacy.make_bundle())
        old = self.write_json("previous.json", [])
        code, output = self.run_cli([
            "fingerprints", "--input", path, "--previous-collector-observations", old,
        ])
        self.assertEqual(code, 2)
        self.assertTrue(output["hard_failure"])

    def test_export_loader_rejects_nonregular_and_oversize_input(self):
        with self.assertRaises(ValueError):
            collector.load_export(str(self.root))
        path = self.write_json("large.json", ["x" * 100])
        with mock.patch.object(collector, "MAX_EXPORT_BYTES", 20), self.assertRaises(ValueError):
            collector.load_export(path)

    def test_local_io_errors_do_not_echo_paths_or_input(self):
        path = self.root / "untrusted-secret-marker.json"
        with self.assertRaises(ValueError) as error:
            collector.load_export(str(path))
        self.assertNotIn("untrusted-secret-marker", str(error.exception))
        path.write_bytes(b"[\xff]")
        with self.assertRaises(ValueError) as error:
            collector.load_export(str(path))
        self.assertNotIn("\\xff", str(error.exception))

    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        path = self.root / "pipe"
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            collector.load_export(str(path))

    def test_actual_nonempty_reader_export_crosses_all_three_existing_entrypoints(self):
        exported = self.real_reader_export()
        self.assertEqual(len(exported), 1)
        prices = self.write_json("prices.json", {"binance": "77604", "kraken": "77567"})
        path = self.write_json("positive-export.json", exported)
        code, output = self.run_cli(["classify-zone", "--input", prices, "--lower", "77000", "--upper", "77500",
                                     "--collector-observations", path])
        self.assertEqual(code, 0, output)
        self.assertEqual(output["price_relation"], "ABOVE_ZONE")
        self.assertEqual(len(output["collector_review"]["accepted_observations"]), 3)
        bundle = legacy.make_bundle()
        code, output = self.run_cli(["fingerprints", "--input", self.write_json("fingerprints.json", bundle),
                                     "--collector-observations", path])
        self.assertEqual(code, 0, output)
        self.assertEqual({key: output[key] for key in contract.compute_fingerprints(bundle)}, contract.compute_fingerprints(bundle))
        code, output, _ = self.check_real_decision_cli(exported, previous_export=[])
        self.assertEqual(code, 0, output)
        review = output["collector_review"]
        self.assertTrue(review["review_candidate"])
        self.assertFalse(review["deep_review_authorized"])
        self.assertEqual(review["independent_verified_sources"], 0)
        self.assertTrue(all(item["verification"] == "UNVERIFIED" for item in review["accepted_observations"]))
        self.assertEqual(output["derived_control"]["monitor_state"], "A")
        for field in ("project_patch_required", "report_required", "attention_planned"):
            self.assertFalse(output["derived_control"][field])

    def test_raw_time_id_arrival_and_duplicate_changes_do_not_create_candidate_or_report(self):
        prior = self.real_reader_export()
        current = deepcopy(prior)
        current[0]["report_id"] = "changed-id"
        current[0]["bundle_id"] = "changed-id"
        current[0]["generated_at"] = "2026-09-04T07:00:10+00:00"
        current[0]["transport"]["received_at"] += 3600
        current[0]["datasets"]["oi_1h"]["rows"][-1]["quantity_btc"] += 100
        for source in current[0]["sources"].values():
            source["retrieved_at"] = "2026-09-04T07:00:00+00:00"
        current = current + deepcopy(current)
        code, output, before = self.check_real_decision_cli(current, previous_export=prior)
        self.assertEqual(code, 0, output)
        self.assertFalse(output["collector_review"]["review_candidate"])
        self.assertEqual(len(output["collector_review"]["usable_coverage"]), 3)
        self.assertEqual(output["collector_review"]["independent_verified_sources"], 0)
        self.assertEqual(output["fingerprints"], contract.compute_fingerprints(before))
        self.assertEqual(output["derived_control"]["monitor_state"], "A")
        self.assertFalse(output["derived_control"]["report_required"])

    def test_actual_collector_quality_excludes_bad_dataset_but_preserves_other_context(self):
        def damage(bundle):
            bundle["datasets"]["spot_cvd"]["rows"][0]["flow_valid"] = False
        exported = self.real_reader_export(mutate=damage)
        self.assertEqual(exported[0]["validation"]["status"], "degraded_auxiliary")
        review = collector.review_exports(exported)
        self.assertNotIn("spot_cvd", [item["dataset"] for item in review["accepted_observations"]])
        self.assertIn("oi_1h", [item["dataset"] for item in review["accepted_observations"]])
        self.assertTrue(any(item.get("dataset") == "spot_cvd" and item["collector_reason_count"] > 0
                            for item in review["rejected_observations"]))
        self.assertEqual(review["independent_verified_sources"], 0)

    def test_current_parent_edit_removes_observation_and_remains_silent(self):
        previous = self.real_reader_export()
        current = self.real_reader_export(edit_parent=True)
        self.assertEqual(current, [])
        code, output, _ = self.check_real_decision_cli(current, previous_export=previous)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(output["collector_review"]["lost_coverage"]), 3)
        self.assertFalse(output["collector_review"]["review_candidate"])
        self.assertFalse(output["derived_control"]["report_required"])

    def test_collector_cannot_smuggle_claims_account_or_strategy_into_projection(self):
        exported = self.real_reader_export()
        item = exported[0]
        item.update(position_fact="DO_NOT_ECHO", claim_id="DO_NOT_ECHO", event_role="CAUSE")
        item["datasets"]["spot_cvd"]["rows"][0].update(account="DO_NOT_ECHO", claim_id="DO_NOT_ECHO")
        for source in item["sources"].values():
            source["endpoint_url"] += "?secret=DO_NOT_ECHO"
        review = collector.review_exports(exported)
        self.assertTrue(review["accepted_observations"])
        rendered = json.dumps(review)
        for forbidden in ("DO_NOT_ECHO", "claim_id", "position_fact", "CAUSE"):
            self.assertNotIn(forbidden, rendered)

    def test_existing_independently_supported_driver_change_still_reports_d(self):
        exported = self.real_reader_export()
        code, output, _ = self.check_real_decision_cli(exported, flip_spot=True, previous_export=exported)
        self.assertEqual(code, 0, output)
        self.assertFalse(output["collector_review"]["review_candidate"])
        self.assertEqual(output["derived_control"]["monitor_state"], "D")
        self.assertTrue(output["derived_control"]["report_required"])

    def test_unavailable_shared_projection_fails_closed_without_faking_quality(self):
        exported = self.real_reader_export()
        with mock.patch.object(collector, "COLLECTOR_CONTRACT_PATH", self.root / "missing.py"):
            review = collector.review_exports(exported)
        self.assertFalse(review["review_candidate"])
        self.assertEqual(review["accepted_observations"], [])
        self.assertEqual(review["quality_status"], "COLLECTOR_CONTRACT_UNAVAILABLE")

    def test_missing_comparison_export_is_not_misreported_as_new_arrival(self):
        review = collector.review_exports(self.real_reader_export())
        self.assertTrue(review["accepted_observations"])
        self.assertFalse(review["review_candidate"])
        self.assertEqual(review["coverage_baseline_status"], "NOT_PROVIDED")

    def test_missing_empty_and_invalid_baselines_are_distinct(self):
        current = self.real_reader_export()
        invalid = deepcopy(current)
        invalid[0]["requires_current_source_check"] = False
        for previous, status, compared, new_count in (
            (None, "NOT_PROVIDED", False, 0),
            ([], "VALID_EMPTY", True, 3),
            (invalid, "INVALID", False, 0),
        ):
            with self.subTest(status=status):
                review = collector.review_exports(current, previous)
                self.assertEqual(review["coverage_baseline_status"], status)
                self.assertEqual(review["coverage_comparison_performed"], compared)
                self.assertEqual(len(review["review_candidates"]), new_count)
                self.assertEqual(len(review["accepted_observations"]), 3)
                self.assertEqual(review["independent_verified_sources"], 0)
                self.assertFalse(review["deep_review_authorized"])
        self.assertEqual(collector.review_exports([], [])["coverage_baseline_status"], "VALID_EMPTY")
        self.assertEqual(collector.review_exports([])["coverage_baseline_status"], "NOT_PROVIDED")

    def test_current_input_must_be_an_actual_array_not_an_unknown_value(self):
        for current in (None, {}, False, "[]", 0):
            with self.subTest(current=current), self.assertRaises(ValueError):
                collector.review_exports(current, [])

    def test_invalid_previous_reasons_survive_without_new_or_lost_coverage(self):
        valid = self.real_reader_export()
        cases = []
        for field, value, reason in (
            ("role", "not_auxiliary", "INVALID_AUXILIARY_ENVELOPE"),
            ("requires_current_source_check", False, "INVALID_AUXILIARY_TRUST_BOUNDARY"),
            ("datasets", [], "INVALID_COLLECTOR_BUNDLE"),
            ("sources", None, "INVALID_COLLECTOR_BUNDLE"),
        ):
            damaged = deepcopy(valid)
            damaged[0][field] = value
            cases.append((damaged, reason))
        damaged = deepcopy(valid)
        damaged[0]["validation"]["status"] = "rejected"
        cases.append((damaged, "COLLECTOR_QUALITY_NOT_USABLE"))
        damaged = deepcopy(valid)
        damaged[0]["validation"]["reasons"] = ["DO_NOT_ECHO"]
        cases.append((damaged, "INVALID_COLLECTOR_QUALITY_RESULT"))
        damaged = deepcopy(valid)
        damaged[0]["sources"]["public_spot_cvd"]["venue"] = "not_public"
        cases.append((damaged, "INVALID_COLLECTOR_PROJECTION"))
        damaged = deepcopy(valid)
        damaged[0]["datasets"]["DO_NOT_ECHO"] = {}
        cases.append((damaged, "UNSUPPORTED_DATASET"))
        for previous, expected_reason in cases:
            for current in (valid, []):
                with self.subTest(reason=expected_reason, current=bool(current)):
                    review = collector.review_exports(current, previous)
                    self.assertEqual(review["coverage_baseline_status"], "INVALID")
                    self.assertFalse(review["coverage_comparison_performed"])
                    self.assertEqual(review["review_candidates"], [])
                    self.assertEqual(review["lost_coverage"], [])
                    self.assertEqual(len(review["accepted_observations"]), 3 if current else 0)
                    self.assertTrue(any(expected_reason in item["reasons"]
                                        for item in review["previous_rejected_observations"]))
                    self.assertNotIn("DO_NOT_ECHO", json.dumps(review))

    def test_invalid_baseline_is_isolated_at_all_three_actual_entrypoints(self):
        current = self.real_reader_export()
        invalid = deepcopy(current)
        invalid[0]["requires_current_source_check"] = False
        flags = ["--collector-observations", self.write_json("current.json", current),
                 "--previous-collector-observations", self.write_json("previous.json", invalid)]
        price_path = self.write_json("prices.json", {"binance": "77604", "kraken": "77567"})
        code, result = self.run_cli(["classify-zone", "--input", price_path,
                                     "--lower", "77000", "--upper", "77500"] + flags)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["price_relation"], "ABOVE_ZONE")
        self.assertEqual(result["collector_review"]["coverage_baseline_status"], "INVALID")
        self.assertFalse(result["collector_review"]["review_candidate"])
        bundle = legacy.make_bundle()
        code, result = self.run_cli(["fingerprints", "--input", self.write_json("fp.json", bundle)] + flags)
        self.assertEqual(code, 0, result)
        for key, value in contract.compute_fingerprints(bundle).items():
            self.assertEqual(result[key], value)
        self.assertFalse(result["collector_review"]["review_candidate"])
        code, result, before = self.check_real_decision_cli(current, previous_export=invalid)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["fingerprints"], contract.compute_fingerprints(before))
        self.assertEqual(result["derived_control"]["monitor_state"], "A")
        for key in ("project_patch_required", "report_required", "attention_planned"):
            self.assertFalse(result["derived_control"][key])
        self.assertFalse(result["collector_review"]["review_candidate"])
        self.assertTrue(result["collector_review"]["previous_rejected_observations"])

    def test_real_degraded_baseline_can_report_only_recovered_coverage(self):
        current = self.real_reader_export()

        def missing(bundle):
            bundle["datasets"].pop("spot_cvd")

        def invalid_flow(bundle):
            bundle["datasets"]["spot_cvd"]["rows"][0]["flow_valid"] = False

        def all_stale(bundle):
            for dataset in bundle["datasets"].values():
                dataset["quality"]["stale"] = True

        def empty(bundle):
            bundle["datasets"].clear()

        for mutation, recovered_count in ((missing, 1), (invalid_flow, 1), (all_stale, 3), (empty, 3)):
            with self.subTest(mutation=mutation.__name__):
                previous = self.real_reader_export(mutate=mutation)
                self.assertEqual(previous[0]["validation"]["status"], "degraded_auxiliary")
                review = collector.review_exports(current, previous)
                self.assertEqual(review["coverage_baseline_status"], "VALID_DEGRADED")
                self.assertTrue(review["coverage_comparison_performed"])
                self.assertEqual(len(review["review_candidates"]), recovered_count)
                self.assertTrue(review["previous_rejected_observations"])
                self.assertEqual(review["lost_coverage"], [])
                self.assertFalse(review["deep_review_authorized"])
                self.assertEqual(review["independent_verified_sources"], 0)

    def test_real_filtered_source_and_unknown_domain_diagnostics_preserve_baseline(self):
        current = self.real_reader_export()

        def unused_source(bundle):
            bundle["sources"]["unreferenced"] = deepcopy(bundle["sources"]["public_spot_cvd"])

        def unknown_domain(bundle):
            bundle["datasets"]["unrecognized"] = {"source_ids": []}

        for mutation, diagnostic in ((unused_source, "__sources__"), (unknown_domain, "__unsupported__")):
            with self.subTest(mutation=mutation.__name__):
                previous = self.real_reader_export(mutate=mutation)
                self.assertIn(diagnostic, previous[0]["validation"]["dataset_reasons"])
                review = collector.review_exports(current, previous)
                self.assertEqual(review["coverage_baseline_status"], "VALID_DEGRADED")
                self.assertTrue(review["coverage_comparison_performed"])
                self.assertFalse(review["review_candidate"])
                self.assertEqual(review["lost_coverage"], [])

    def test_identical_snapshot_duplicates_are_a_valid_idempotent_baseline(self):
        current = self.real_reader_export()
        previous = current + deepcopy(current)
        review = collector.review_exports(current, previous)
        self.assertEqual(review["coverage_baseline_status"], "VALID")
        self.assertTrue(review["coverage_comparison_performed"])
        self.assertFalse(review["review_candidate"])
        self.assertEqual(review["lost_coverage"], [])
        self.assertTrue(any(item["reasons"] == ["DUPLICATE_AUXILIARY_SNAPSHOT"]
                            for item in review["previous_rejected_observations"]))

    def test_distinct_historical_snapshots_are_not_unioned_as_current_or_baseline(self):
        valid = self.real_reader_export()
        missing = self.real_reader_export(mutate=lambda bundle: bundle["datasets"].pop("spot_cvd"))
        for history in (valid + missing, missing + valid):
            with self.subTest(order=history[0]["validation"]["status"]):
                review = collector.review_exports(history, valid)
                self.assertEqual(review["coverage_current_status"], "INVALID")
                self.assertEqual(review["accepted_observations"], [])
                self.assertEqual(review["usable_coverage"], [])
                self.assertEqual(review["lost_coverage"], [])
                self.assertFalse(review["coverage_comparison_performed"])
                self.assertFalse(review["review_candidate"])
                self.assertTrue(any(item["reasons"] == ["MULTIPLE_AUXILIARY_SNAPSHOTS"]
                                    for item in review["rejected_observations"]))
                review = collector.review_exports(valid, history)
                self.assertEqual(review["coverage_baseline_status"], "INVALID")
                self.assertEqual(len(review["accepted_observations"]), 3)
                self.assertFalse(review["review_candidate"])
                self.assertEqual(review["lost_coverage"], [])

    def test_different_envelopes_cannot_masquerade_as_duplicate_measurements(self):
        valid = self.real_reader_export()
        changed = deepcopy(valid)
        changed[0]["report_id"] = "different-report-same-measurements"
        review = collector.review_exports(valid + changed, [])
        self.assertEqual(review["coverage_current_status"], "INVALID")
        self.assertEqual(review["accepted_observations"], [])
        self.assertFalse(review["review_candidate"])

    def test_distinct_history_is_diagnostic_only_at_actual_decision_entrypoint(self):
        valid = self.real_reader_export()
        missing = self.real_reader_export(mutate=lambda bundle: bundle["datasets"].pop("spot_cvd"))
        code, result, before = self.check_real_decision_cli(valid + missing, previous_export=valid)
        self.assertEqual(code, 0, result)
        review = result["collector_review"]
        self.assertEqual(review["coverage_current_status"], "INVALID")
        self.assertEqual(review["accepted_observations"], [])
        self.assertFalse(review["coverage_comparison_performed"])
        self.assertEqual(review["lost_coverage"], [])
        self.assertEqual(result["fingerprints"], contract.compute_fingerprints(before))
        self.assertEqual(result["derived_control"]["monitor_state"], "A")
        for key in ("project_patch_required", "report_required", "attention_planned"):
            self.assertFalse(result["derived_control"][key])
        review = collector.review_exports(valid, valid + [{}])
        self.assertEqual(review["coverage_baseline_status"], "INVALID")
        self.assertTrue(any(item["reasons"] == ["INVALID_AUXILIARY_ENVELOPE"]
                            for item in review["previous_rejected_observations"]))
        self.assertFalse(review["review_candidate"])

    def test_valid_empty_quality_claim_and_inconsistent_bundle_diagnostic_are_rejected(self):
        valid = self.real_reader_export()
        empty = deepcopy(valid)
        empty[0]["datasets"] = {}
        inconsistent = deepcopy(valid)
        inconsistent[0]["validation"].update(status="degraded_auxiliary", dataset_reasons={"__bundle__": ["empty_datasets"]})
        for previous in (empty, inconsistent):
            review = collector.review_exports(valid, previous)
            self.assertEqual(review["coverage_baseline_status"], "INVALID")
            self.assertFalse(review["coverage_comparison_performed"])
            self.assertFalse(review["review_candidate"])
            self.assertTrue(any(item["reasons"] == ["INVALID_COLLECTOR_QUALITY_RESULT"]
                                for item in review["previous_rejected_observations"]))

    def test_unavailable_projection_cannot_mark_nonempty_baseline_valid(self):
        valid = self.real_reader_export()
        with mock.patch.object(collector, "COLLECTOR_CONTRACT_PATH", self.root / "missing.py"):
            for previous, status in ((valid, "INVALID"), ([], "VALID_EMPTY"), (None, "NOT_PROVIDED")):
                review = collector.review_exports(valid, previous)
                self.assertEqual(review["coverage_baseline_status"], status)
                self.assertFalse(review["coverage_comparison_performed"])
                self.assertEqual(review["review_candidates"], [])
                self.assertEqual(review["lost_coverage"], [])
                if previous:
                    self.assertEqual(review["previous_rejected_observations"][0]["reasons"],
                                     ["COLLECTOR_CONTRACT_UNAVAILABLE"])

    def test_authoritative_stale_and_source_conflict_results_are_filtered(self):
        def stale(bundle):
            bundle["datasets"]["spot_cvd"]["quality"]["stale"] = True

        def conflict(bundle):
            bundle["sources"]["public_spot_cvd"]["venue"] = "Kraken"

        for mutation in (stale, conflict):
            with self.subTest(mutation=mutation.__name__):
                exported = self.real_reader_export(mutate=mutation)
                self.assertIn("spot_cvd", exported[0]["validation"]["dataset_reasons"])
                code, result, _ = self.check_real_decision_cli(exported)
                self.assertEqual(code, 0, result)
                self.assertNotIn("spot_cvd", [item["dataset"] for item in result["collector_review"]["accepted_observations"]])
                self.assertEqual(result["derived_control"]["monitor_state"], "A")
                self.assertFalse(result["derived_control"]["report_required"])

    def test_absent_dataset_retains_collector_missing_reason(self):
        exported = self.real_reader_export(mutate=lambda bundle: bundle["datasets"].pop("spot_cvd"))
        review = collector.review_exports(exported)
        self.assertNotIn("spot_cvd", [item["dataset"] for item in review["accepted_observations"]])
        self.assertTrue(any(item.get("dataset") == "spot_cvd"
                            and item["reasons"] == ["COLLECTOR_DATASET_MISSING"]
                            and item["collector_reason_count"] > 0 for item in review["rejected_observations"]))

    def test_actual_same_origin_shadow_does_not_add_coverage_or_independence(self):
        def with_basis(bundle, shadow=False):
            for name in (("basis", "binance_basis_shadow") if shadow else ("basis",)):
                sid = "public_" + name
                bundle["sources"][sid] = evidence._source(
                    sid, "offline basis", "https://fapi.binance.com/fapi/v1/premiumIndex", "Binance",
                    "2026-09-04T06:00:05+00:00", "2026-09-04T06:00:00+00:00")
                bundle["datasets"][name] = {
                    "source_ids": [sid], "values": {"mark_price": 80000, "index_price": 80000,
                                                    "basis_pct": 0, "received_at_ms": 1788501600000},
                    "quality": {},
                }
        previous = self.real_reader_export(mutate=with_basis)
        current = self.real_reader_export(mutate=lambda bundle: with_basis(bundle, shadow=True))
        self.assertEqual(current[0]["validation"]["status"], "valid_auxiliary")
        review = collector.review_exports(current, previous)
        self.assertEqual(review["independent_verified_sources"], 0)
        self.assertFalse(review["review_candidate"])
        self.assertEqual(len(review["usable_coverage"]), 4)
        self.assertEqual(len(review["accepted_observations"]), 4)
        self.assertTrue(any(item["reasons"] == ["DUPLICATE_AUXILIARY_MEASUREMENT"] for item in review["rejected_observations"]))
        reverse = collector.review_exports(previous, current)
        self.assertEqual(reverse["coverage_baseline_status"], "VALID")
        self.assertTrue(reverse["coverage_comparison_performed"])
        self.assertFalse(reverse["review_candidate"])
        self.assertEqual(reverse["lost_coverage"], [])


if __name__ == "__main__":
    unittest.main()
