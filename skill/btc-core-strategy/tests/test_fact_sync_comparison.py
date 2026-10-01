"""Offline production-path checks using synthetic facts and temporary Owners only."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import strategy_contract as c
import test_production_strategy as production
import test_event_contract as events


CURRENT = "2030-03-01T14:31:00+00:00"


def finalize(bundle, baseline):
    bundle["fingerprints"] = c.compute_fingerprints(bundle)
    bundle["control"] = c._derive_control_unchecked(bundle, baseline)
    return bundle


def receipt(bundle):
    control = bundle["control"]
    return {
        "schema": c.RUN_RECEIPT_SCHEMA, "receipt_phase": "FINAL",
        "bundle_identity": c.compute_bundle_identity(bundle),
        **{key: control[key] for key in (
            "decision_class", "attention_level", "monitor_state", "write_transition",
            "project_patch_required", "report_required", "attention_planned",
        )},
        **c.compute_fingerprints(bundle),
        "local_readback_state": "NOT_REQUIRED",
        "report_validation_state": "NOT_REQUIRED", "cloud_delivery_state": "NOT_REQUIRED",
        "report_visible": False,
    }


class FactSyncComparisonTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="btc-sync-compat-")))
        self.fact_paths = {owner: self.directory / (owner + ".txt") for owner in c.FACT_OWNER_PATHS}
        for owner, path in self.fact_paths.items():
            path.write_text("synthetic fact " + owner + " version one\n", encoding="utf-8")
        self.stack.enter_context(mock.patch.object(c, "FACT_OWNER_PATHS", self.fact_paths))
        self.stack.enter_context(mock.patch.object(c, "OWNER_STATE_PATH", self.directory / "state.md"))
        self.stack.enter_context(mock.patch.object(c, "OWNER_PLAN_PATH", self.directory / "plan.md"))
        self.stack.enter_context(production.frozen_clock(CURRENT))
        initial, migration = production.migration_bundle()
        with production.frozen_clock(events.INITIAL):
            self.assertEqual([], c.validate_bundle(initial, migration, fact_owner_snapshot=c.load_fact_owner_snapshot()))
        with production.rendered_baseline(initial) as (first, _, _):
            self.fact_paths["ASSET_CONFIGURATION"].write_text("synthetic confirmed version two\n", encoding="utf-8")
            prior = production.daily_bundle(initial, first, events.BEFORE_FINAL)
            prior["account_facts"].update(
                account_fact_changed_known=True,
                fact_owner_fingerprints=dict(c.load_fact_owner_snapshot().fingerprints),
                known_change_evidence=[{
                    "confirmation_type": "USER_CONFIRMED_POSITION_CHANGE", "owner_id": "ASSET_CONFIGURATION",
                }], local_record_status="FACT_SYNC_PENDING",
            )
            prior["user_input_requirement"] = "FACT_SYNC"
            prior["portfolio_decision"]["existing_alpha_short"]["reason_class"] = "CONFIRMED_POSITION_REVIEW"
            prior["runtime"].update(state_id="BTC-STATE-20300301-2330", plan_id="BTC-20300301-2330")
            production._report(prior)
            finalize(prior, first)
            self.assertEqual([], c.validate_bundle(
                prior, previous_baseline=first, fact_owner_snapshot=c.load_fact_owner_snapshot(),
                require_owner_baseline=False,
            ))
            self.assertEqual("D", prior["control"]["monitor_state"])
        self.prior = prior
        c.OWNER_STATE_PATH.write_text(c.render_projection_document(prior, "STATE"), encoding="utf-8")
        c.OWNER_PLAN_PATH.write_text(c.render_projection_document(prior, "PLAN"), encoding="utf-8")
        self.baseline = c.load_revision3_baseline(str(c.OWNER_STATE_PATH), str(c.OWNER_PLAN_PATH), require_owner_paths=True)
        self.before = {path: path.read_bytes() for path in (c.OWNER_STATE_PATH, c.OWNER_PLAN_PATH, *self.fact_paths.values())}
        self.current = production.daily_bundle(prior, self.baseline, CURRENT)
        self.current["account_facts"] = {
            "account_fact_changed_known": False,
            "fact_owner_fingerprints": dict(c.load_fact_owner_snapshot().fingerprints),
        }
        self.current["user_input_requirement"] = "NONE"
        finalize(self.current, self.baseline)

    def validate(self, value=None):
        return c.validate_bundle(
            value or self.current, previous_baseline=self.baseline,
            fact_owner_snapshot=c.load_fact_owner_snapshot(),
        )

    def derive(self, value=None, snapshot=None):
        return c.derive_control(
            value or self.current, previous_baseline=self.baseline,
            fact_owner_snapshot=snapshot or c.load_fact_owner_snapshot(),
        )

    def run_cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = c.main(list(args))
        return result, json.loads(output.getvalue())

    def write_json(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def assert_unchanged(self):
        self.assertEqual(self.before, {path: path.read_bytes() for path in self.before})

    def assert_cli_write_round_trip(self, bundle):
        """Exercise D/E CLI artifact selection against synthetic fixed Owners."""
        bundle_path = self.write_json("write-bundle.json", bundle)
        current = ("--current-state", str(c.OWNER_STATE_PATH), "--current-plan", str(c.OWNER_PLAN_PATH))
        code, output = self.run_cli("validate-bundle", "--input", bundle_path, *current)
        self.assertEqual(0, code, output)
        snapshots = []
        candidates = []
        for role, owner in (("STATE", c.OWNER_STATE_PATH), ("PLAN", c.OWNER_PLAN_PATH)):
            path = self.directory / (role + "-before.md")
            path.write_bytes(owner.read_bytes())
            snapshots.extend(("--previous-" + role.lower() + "-snapshot", str(path)))
        for role, owner in (("STATE", c.OWNER_STATE_PATH), ("PLAN", c.OWNER_PLAN_PATH)):
            code, output = self.run_cli("render-projection-document", "--bundle", bundle_path, "--role", role, *snapshots)
            self.assertEqual(0, code, output)
            path = self.directory / (role + "-candidate.md")
            path.write_text(output["document"], encoding="utf-8")
            candidates.extend(("--" + role.lower(), str(path)))
        code, output = self.run_cli("validate-projection", "--bundle", bundle_path, *candidates, *snapshots)
        self.assertEqual(0, code, output)
        result = receipt(bundle)
        result["local_readback_state"] = "READBACK_VERIFIED"
        report_args = []
        if bundle["control"]["report_required"]:
            report = production.report.render_report(bundle["report_semantics"])
            self.assertTrue(report["ok"], report)
            path = self.directory / "report.txt"
            path.write_text(report["body"], encoding="utf-8")
            result.update(
                report_validation_state="VALIDATED", cloud_delivery_state="PENDING_RETRY",
                report_body_fingerprint=c.content_fingerprint(report["body"]),
            )
            report_args = ["--report", str(path)]
        result_path = self.write_json("write-receipt.json", result)
        common = ("validate-receipt", "--input", result_path, "--bundle", bundle_path)
        code, output = self.run_cli(*common, *snapshots, *candidates, *report_args)
        self.assertNotEqual(0, code, output)  # candidates cannot be FINAL Owner readback
        for candidate, owner in zip(candidates[1::2], (c.OWNER_STATE_PATH, c.OWNER_PLAN_PATH)):
            owner.write_bytes(Path(candidate).read_bytes())  # only synthetic temp Owners
        readback = ("--state", str(c.OWNER_STATE_PATH), "--plan", str(c.OWNER_PLAN_PATH))
        code, output = self.run_cli("validate-projection", "--bundle", bundle_path, *readback, *snapshots)
        self.assertEqual(0, code, output)
        code, output = self.run_cli(*common, *snapshots, *readback, *report_args)
        self.assertEqual(0, code, output)
        code, output = self.run_cli(*common, *current, *readback, *report_args)
        self.assertNotEqual(0, code, output)  # post-write Owners cannot become write-before
        self.assertEqual(
            {path: self.before[path] for path in self.fact_paths.values()},
            {path: path.read_bytes() for path in self.fact_paths.values()},
        )

    def test_consumed_markers_are_a_without_changing_raw_fingerprints_or_owners(self):
        original = deepcopy(self.current)
        self.assertNotEqual(self.prior["fingerprints"]["decision_fingerprint"], self.current["fingerprints"]["decision_fingerprint"])
        self.assertEqual([], self.validate())
        self.assertEqual("A", self.derive()["monitor_state"])
        comparison = c.decision_comparison(self.current, self.baseline)
        self.assertEqual("BOUND_REVISION3_FACT_SYNC_COMPAT", comparison["mode"])
        self.assertEqual(comparison["previous_decision_comparison_fingerprint"], comparison["current_decision_comparison_fingerprint"])
        self.assertFalse(self.current["control"]["project_patch_required"])
        self.assertFalse(self.current["control"]["report_required"])
        self.assertEqual(original, self.current)
        self.assert_unchanged()

    def test_repeated_a_keeps_binding_original_owner_fingerprints(self):
        for _ in range(2):
            self.assertEqual([], self.validate())
            self.assertEqual(self.prior["fingerprints"]["decision_fingerprint"], self.current["previous_fingerprints"]["decision_fingerprint"])
            self.assertEqual([], c.validate_receipt(receipt(self.current), self.current, previous_baseline=self.baseline, fact_owner_snapshot=c.load_fact_owner_snapshot()))
        self.assert_unchanged()

    def test_legacy_private_raw_comparison_remains_explicitly_different(self):
        self.assertEqual("D", c._derive_control_unchecked(self.current)["monitor_state"])
        self.assertEqual("RAW_FINGERPRINT", c.decision_comparison(self.current)["mode"])
        self.assertEqual("A", self.current["control"]["monitor_state"])

    def test_actual_fact_version_change_still_requires_d_and_report_semantics(self):
        self.fact_paths["ASSET_CONFIGURATION"].write_text("synthetic confirmed version three\n", encoding="utf-8")
        changed = deepcopy(self.current)
        changed["account_facts"] = {
            "account_fact_changed_known": True,
            "fact_owner_fingerprints": dict(c.load_fact_owner_snapshot().fingerprints),
            "known_change_evidence": [{"confirmation_type": "USER_CONFIRMED_POSITION_CHANGE", "owner_id": "ASSET_CONFIGURATION"}],
            "local_record_status": "FACT_SYNC_PENDING",
        }
        changed["user_input_requirement"] = "FACT_SYNC"
        finalize(changed, self.baseline)
        self.assertEqual("D", self.derive(changed)["monitor_state"])
        self.assertTrue(any("report_fingerprint" in error for error in self.validate(changed)))
        changed["portfolio_decision"]["existing_alpha_short"]["reason_class"] = "SECOND_CONFIRMED_POSITION_REVIEW"
        production._report(changed)
        finalize(changed, self.baseline)
        self.assertEqual([], self.validate(changed))
        self.assertTrue(changed["control"]["report_required"])

    def test_changed_version_cannot_be_hidden_as_none(self):
        self.fact_paths["ASSET_CONFIGURATION"].write_text("synthetic changed version\n", encoding="utf-8")
        self.current["account_facts"]["fact_owner_fingerprints"] = dict(c.load_fact_owner_snapshot().fingerprints)
        finalize(self.current, self.baseline)
        with self.assertRaises(c.GuardError):
            self.derive()
        self.assertTrue(self.validate())

    def recorded_preference(self):
        self.fact_paths["ASSET_CONFIGURATION"].write_text("synthetic preference revision; positions unchanged\n", encoding="utf-8")
        changed = deepcopy(self.current)
        changed["account_facts"].update(
            fact_owner_fingerprints=dict(c.load_fact_owner_snapshot().fingerprints),
            known_change_evidence=[{"confirmation_type": c.PREFERENCE_CONFIRMATION_TYPE,
                                    "owner_id": "ASSET_CONFIGURATION"}],
            local_record_status="PREFERENCE_RECORDED",
        )
        changed["portfolio_decision"]["existing_alpha_short"]["reason_class"] = "UPDATED_PROTECTION_PREFERENCE"
        production._report(changed)
        return finalize(changed, self.baseline)

    def test_recorded_preference_change_publishes_without_claiming_account_sync(self):
        changed = self.recorded_preference()
        self.assertEqual([], self.validate(changed))
        self.assertEqual("D", self.derive(changed)["monitor_state"])
        self.assertEqual("NONE", changed["user_input_requirement"])
        self.assertFalse(changed["account_facts"]["account_fact_changed_known"])
        before = deepcopy(self.prior)
        with production.rendered_baseline(changed) as (next_baseline, _, _):
            next_round = production.daily_bundle(changed, next_baseline, CURRENT)
            next_round["account_facts"] = {
                "account_fact_changed_known": False,
                "fact_owner_fingerprints": dict(c.load_fact_owner_snapshot().fingerprints),
            }
            finalize(next_round, next_baseline)
            self.assertFalse(c.decision_comparison(next_round, next_baseline)["decision_changed"])
        self.assertEqual(before, self.prior)

    def test_preference_marker_cannot_hide_other_owner_change_or_claim_account_sync(self):
        changed = self.recorded_preference()
        self.fact_paths["OPERATION_LOG"].write_text("synthetic recorded execution\n", encoding="utf-8")
        changed["account_facts"]["fact_owner_fingerprints"] = dict(c.load_fact_owner_snapshot().fingerprints)
        with self.assertRaisesRegex(c.GuardError, "only ASSET_CONFIGURATION"):
            self.derive(changed)
        changed["account_facts"]["account_fact_changed_known"] = True
        changed["account_facts"]["local_record_status"] = "FACT_SYNC_PENDING"
        changed["user_input_requirement"] = "FACT_SYNC"
        with self.assertRaises(c.GuardError):
            self.derive(changed)

    def test_preference_marker_requires_actual_change_and_correct_record_status(self):
        changed = self.recorded_preference()
        self.fact_paths["ASSET_CONFIGURATION"].write_bytes(self.before[self.fact_paths["ASSET_CONFIGURATION"]])
        changed["account_facts"]["fact_owner_fingerprints"] = dict(c.load_fact_owner_snapshot().fingerprints)
        with self.assertRaisesRegex(c.GuardError, "actually change"):
            self.derive(changed)
        changed = self.recorded_preference()
        changed["account_facts"]["local_record_status"] = "FACT_SYNC_PENDING"
        with self.assertRaisesRegex(c.GuardError, "recorded ASSET_CONFIGURATION preference"):
            self.derive(changed)

    def test_forged_fact_sync_is_rejected_by_public_derivation_bundle_and_receipt(self):
        bad = deepcopy(self.current)
        bad["account_facts"] = deepcopy(self.prior["account_facts"])
        bad["user_input_requirement"] = "FACT_SYNC"
        finalize(bad, self.baseline)
        with self.assertRaises(c.GuardError):
            self.derive(bad)
        self.assertTrue(self.validate(bad))
        self.assertTrue(c.validate_receipt(receipt(bad), bad, previous_baseline=self.baseline, fact_owner_snapshot=c.load_fact_owner_snapshot()))

    def test_confirmation_shape_and_owner_must_be_valid(self):
        for confirmation in (None, [], [{"owner_id": "OPERATION_LOG", "confirmation_type": "PUBLIC_PRICE"}], [{"owner_id": "ASSET_CONFIGURATION", "confirmation_type": "USER_CONFIRMED_POSITION_CHANGE", "override": True}]):
            with self.subTest(confirmation=confirmation):
                bad = deepcopy(self.current)
                bad["account_facts"] = deepcopy(self.prior["account_facts"])
                bad["account_facts"]["known_change_evidence"] = confirmation
                bad["user_input_requirement"] = "FACT_SYNC"
                with self.assertRaises(c.GuardError):
                    self.derive(bad)

    def test_forged_or_stale_fact_snapshot_is_rejected(self):
        bad = deepcopy(self.current)
        bad["account_facts"]["fact_owner_fingerprints"]["ASSET_CONFIGURATION"] = "sha256:" + "0" * 64
        with self.assertRaises(c.GuardError):
            self.derive(bad)
        snapshot = c.load_fact_owner_snapshot()
        self.fact_paths["OPERATION_LOG"].write_text("changed after capture\n", encoding="utf-8")
        with self.assertRaises(c.GuardError):
            self.derive(snapshot=snapshot)

    def test_real_portfolio_changes_are_not_masked(self):
        for key, value in ("portfolio_role", "REVISED_PROTECTION_ROLE"), ("reason_class", "REVISED_PROTECTION_REASON"), ("formation_conditions", ["REVISED_FORMATION"]):
            with self.subTest(key=key):
                changed = deepcopy(self.current)
                changed["portfolio_decision"]["existing_alpha_short"][key] = value
                production._report(changed)
                finalize(changed, self.baseline)
                self.assertEqual("D", self.derive(changed)["monitor_state"])
                self.assertTrue(changed["control"]["report_required"])

    def test_comparison_preserves_nontransient_strategy_fields(self):
        cases = [
            (("path_decision",), "MOVE_DOWN"),
            (("value_zones", 0, "lifecycle"), "INVALIDATED"),
            (("trade_candidates", 0, "entries", 0, "amount_u"), "251"),
            (("trade_candidates", 0, "entries", 0, "formation_conditions"), ["REVISED_FORMATION"]),
            (("major_risk", "active"), True),
            (("causal_attribution", "selected_hypothesis_id"), "H_SPOT"),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                changed = deepcopy(self.current)
                node = changed
                for key in path[:-1]:
                    node = node[key]
                self.assertIn(path[-1], node)
                node[path[-1]] = value
                self.assertTrue(c.decision_comparison(changed, self.baseline)["decision_changed"])
                # These are fingerprint probes, not evidence-qualified PRE_WRITE decisions.
        self.assert_unchanged()

    def test_semantic_d_uses_snapshot_projection_and_pending_report_cli_branch(self):
        changed = deepcopy(self.current)
        changed["portfolio_decision"]["existing_alpha_short"]["reason_class"] = "REVISED_PROTECTION_REASON"
        changed["runtime"].update(state_id="BTC-STATE-20300301-2331", plan_id="BTC-20300301-2331")
        production._report(changed)
        finalize(changed, self.baseline)
        self.assertEqual("D", changed["control"]["monitor_state"])
        self.assertTrue(changed["control"]["report_required"])
        self.assert_cli_write_round_trip(changed)

    def test_final_event_still_creates_e_without_forced_report(self):
        final = production.daily_bundle(self.prior, self.baseline, events.AFTER_FINAL, final=True)
        final["account_facts"] = deepcopy(self.current["account_facts"])
        final["user_input_requirement"] = "NONE"
        finalize(final, self.baseline)
        with production.frozen_clock(events.AFTER_FINAL):
            self.assertEqual([], self.validate(final))
            self.assertEqual("E", self.derive(final)["monitor_state"])
            self.assertTrue(final["control"]["project_patch_required"])
            self.assertFalse(final["control"]["report_required"])

    def test_e_cleanup_uses_snapshot_projection_and_silent_receipt_cli_branch(self):
        final = production.daily_bundle(self.prior, self.baseline, events.AFTER_FINAL, final=True)
        final["account_facts"] = deepcopy(self.current["account_facts"])
        final["user_input_requirement"] = "NONE"
        finalize(final, self.baseline)
        self.assertEqual("E", final["control"]["monitor_state"])
        self.assertFalse(final["control"]["report_required"])
        with production.frozen_clock(events.AFTER_FINAL):
            self.assert_cli_write_round_trip(final)

    def test_forged_previous_hash_or_changed_baseline_is_rejected(self):
        bad = deepcopy(self.current)
        bad["previous_fingerprints"]["decision_fingerprint"] = c.decision_comparison(bad, self.baseline)["previous_decision_comparison_fingerprint"]
        with self.assertRaises(c.GuardError):
            self.derive(bad)
        c.OWNER_STATE_PATH.write_text(c.OWNER_STATE_PATH.read_text() + "\n", encoding="utf-8")
        with self.assertRaises(c.GuardError):
            self.derive()

    def test_three_cli_paths_accept_a_and_preserve_owner_bytes(self):
        bundle_path = self.write_json("bundle.json", self.current)
        receipt_path = self.write_json("receipt.json", receipt(self.current))
        pair = ("--current-state", str(c.OWNER_STATE_PATH), "--current-plan", str(c.OWNER_PLAN_PATH))
        code, value = self.run_cli("fingerprints", "--input", bundle_path)
        self.assertEqual(0, code, value)
        code, value = self.run_cli("validate-bundle", "--input", bundle_path, *pair)
        self.assertEqual(0, code, value)
        self.assertEqual("A", value["derived_control"]["monitor_state"])
        self.assertFalse(value["decision_comparison"]["decision_changed"])
        code, value = self.run_cli("validate-receipt", "--input", receipt_path, "--bundle", bundle_path, *pair)
        self.assertEqual(0, code, value)
        self.assert_unchanged()

    def test_a_cli_rejects_snapshot_pair_missing_pair_and_readback_artifacts(self):
        bundle_path = self.write_json("bundle.json", self.current)
        receipt_path = self.write_json("receipt.json", receipt(self.current))
        correct = ("--current-state", str(c.OWNER_STATE_PATH), "--current-plan", str(c.OWNER_PLAN_PATH))
        invalid = [(), ("--current-state", str(c.OWNER_STATE_PATH)), ("--previous-state-snapshot", str(c.OWNER_STATE_PATH), "--previous-plan-snapshot", str(c.OWNER_PLAN_PATH)), (*correct, "--state", str(c.OWNER_STATE_PATH), "--plan", str(c.OWNER_PLAN_PATH))]
        for args in invalid:
            with self.subTest(args=args):
                code, value = self.run_cli("validate-receipt", "--input", receipt_path, "--bundle", bundle_path, *args)
                self.assertNotEqual(0, code, value)
        self.assert_unchanged()

    def test_a_receipt_rejects_report_cloud_and_forged_fingerprints(self):
        for extra in ({"report_text": "not a report"}, {"cloud_readback": {"schema": c.CLOUD_READBACK_SCHEMA, "visible_report_text": "", "assistant_responses": ["已收录"]}}):
            self.assertTrue(c.validate_receipt(receipt(self.current), self.current, previous_baseline=self.baseline, fact_owner_snapshot=c.load_fact_owner_snapshot(), **extra))
        forged = receipt(self.current)
        forged["decision_fingerprint"] = self.current["previous_fingerprints"]["decision_fingerprint"]
        self.assertTrue(c.validate_receipt(forged, self.current, previous_baseline=self.baseline, fact_owner_snapshot=c.load_fact_owner_snapshot()))


if __name__ == "__main__":
    unittest.main()
