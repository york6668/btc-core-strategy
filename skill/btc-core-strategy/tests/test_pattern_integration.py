"""Offline real-SQLite integration; all market data and asset Owners synthetic."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import io
import json
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests")]
import strategy_contract as c
import decision_report as report
import pattern_semantics as pattern
import market_observation as market
import test_production_strategy as production
import test_strategy_contract as legacy
import test_event_contract as events

NOW = "2030-03-01T14:31:00+00:00"


class SyntheticClient:
    """Shape-faithful providers; no network call is made."""
    def get(self, endpoint, params):
        if "coinbase" in endpoint:
            start, end = market.timestamp(params["start"]), market.timestamp(params["end"])
            return [[at, 99, 101, 100, 100, 10] for at in range(int(start), int(end), 3600)]
        if "kraken" in endpoint:
            start = params["since"]
            return {"error": [], "result": {"XXBTZUSD": [[at, 100, 101, 99, 100, 100, 10, 1] for at in range(start, start + 240 * 3600, 3600)], "last": start + 240 * 3600}}
        start = params["startTime"] // 1000
        end = params["endTime"] // 1000
        if "openInterest" in endpoint:
            return [{"symbol": "BTCUSDT", "timestamp": at * 1000, "sumOpenInterest": 1000, "sumOpenInterestValue": 100000} for at in range(start, end + 1, 3600)]
        if "fundingRate" in endpoint:
            return [{"symbol": "BTCUSDT", "fundingTime": at * 1000, "fundingRate": "0.0001"} for at in range(start, end, 8 * 3600)]
        return [[at * 1000, 100, 101, 99, 100, 10, (at + 3600) * 1000 - 1, 1000, 100, 4, 400, 0] for at in range(start, end, 3600)]


def authored_report(bundle):
    narratives = production.narratives(bundle)
    for item in bundle.get("causal_attribution", {}).get("material_pattern", {}).get("hypotheses", []):
        kind = item["kind"]
        narratives["primary_causal_explanation"].append({
            "subject": kind,
            "text": ("复核后的连续证据不支持原先承接解释，本轮撤回该解释；这不能证明参与者意图，组合规模暂时保持。"
                     if item["stage"] == "FALSIFIED" else "连续主动卖出与价格抗跌同时出现，承接解释的权重提高；这仍不能证明吸筹意图，组合规模暂时保持。"),
            "verification": "EVIDENCE_LINKED", "evidence_refs": deepcopy(item["support_refs"]),
        })
        if item["uncertainties"]:
            narratives["material_uncertainties"].append({
                "subject": kind, "text": "其他场所的主动成交与参与者意图仍不确定，单所关系不能证明跨所资金驱动。",
                "verification": "UNVERIFIED", "evidence_refs": [],
            })
        narratives["next_decision_changing_evidence"].append({
            "subject": kind, "text": "继续比较卖压下的回测留存和其他场所成交；若抗跌失效，将撤回承接解释。",
            "verification": "UNVERIFIED", "evidence_refs": [],
        })
    bundle["report_semantics"] = report.build_report_projection(bundle, narratives, visible_level_ids={"V0"})
    bundle["fingerprints"] = c.compute_fingerprints(bundle)


def final_receipt(bundle):
    control = bundle["control"]
    return {
        "schema": c.RUN_RECEIPT_SCHEMA, "receipt_phase": "FINAL",
        "bundle_identity": c.compute_bundle_identity(bundle),
        **{key: control[key] for key in ("decision_class", "attention_level", "monitor_state", "write_transition", "project_patch_required", "report_required", "attention_planned")},
        **c.compute_fingerprints(bundle), "local_readback_state": "NOT_REQUIRED",
        "report_validation_state": "NOT_REQUIRED", "cloud_delivery_state": "NOT_REQUIRED", "report_visible": False,
    }


class PatternIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="btc-pattern-contract-")))
        self.db = self.directory / "observation.sqlite3"
        self.stack.enter_context(mock.patch.object(market, "DEFAULT_DB", self.db))
        self.stack.enter_context(mock.patch.object(market.time, "time", return_value=datetime.fromisoformat(NOW).timestamp()))
        self.stack.enter_context(production.frozen_clock(NOW))
        self.facts = {owner: self.directory / (owner + ".txt") for owner in c.FACT_OWNER_PATHS}
        for owner, path in self.facts.items():
            path.write_text("synthetic unchanged " + owner + "\n", encoding="utf-8")
        self.stack.enter_context(mock.patch.object(c, "FACT_OWNER_PATHS", self.facts))
        self.stack.enter_context(mock.patch.object(c, "OWNER_STATE_PATH", self.directory / "state.md"))
        self.stack.enter_context(mock.patch.object(c, "OWNER_PLAN_PATH", self.directory / "plan.md"))
        prior, migration = production.migration_bundle()
        with production.frozen_clock(events.INITIAL):
            self.assertEqual([], c.validate_bundle(prior, migration, fact_owner_snapshot=c.load_fact_owner_snapshot()))
        c.OWNER_STATE_PATH.write_text(c.render_projection_document(prior, "STATE"), encoding="utf-8")
        c.OWNER_PLAN_PATH.write_text(c.render_projection_document(prior, "PLAN"), encoding="utf-8")
        self.baseline = c.load_revision3_baseline(str(c.OWNER_STATE_PATH), str(c.OWNER_PLAN_PATH), require_owner_paths=True)
        self.prior = prior
        self.current = production.daily_bundle(prior, self.baseline, NOW)
        self.before = {path: path.read_bytes() for path in [c.OWNER_STATE_PATH, c.OWNER_PLAN_PATH, *self.facts.values()]}

    def hourly(self, required=True, as_of=NOW, absorption_stage="UNRESOLVED"):
        evidence = market.collect(as_of=as_of, client=SyntheticClient())
        context = market.read_context(self.db)
        review = {
            "schema": market.REVIEW_SCHEMA, "evidence_id": evidence["evidence_id"],
            "observed_hour": evidence["observed_hour"], "context_id": context["context_id"],
            "model": {"name": "gpt-6.1-sol", "reasoning_effort": "high"},
            "assessment_summary": "完整小时序列出现主动卖出和抗跌的组合，需要比较承接和去杠杆解释。",
            "patterns": [{"kind": kind, "stage": absorption_stage if kind == "ABSORPTION" else "UNRESOLVED", "confidence": "LOW",
                          "support_refs": ["windows.4h.binance_spot_1h"], "counter_refs": [],
                          "interpretation": "观测支持竞争解释复核，但尚不能确认意图。", "next_evidence": ["跨所留存与主动流独立验证"]}
                         for kind in sorted(market.PATTERN_KINDS)],
            "changes_since_hour": [], "changes_since_deep": [],
            "deep_review": {"required": required, "reasons": ["多期卖压与抗跌需要鉴别，不能由小时入口确认意图"] if required else []},
        }
        return market.record(evidence, review, self.db)["binding"]

    def add_material(self, entry_stage="UNRESOLVED"):
        binding = self.hourly(absorption_stage=entry_stage)
        self.current["observation"]["hourly_review_binding"] = binding
        self.current["observation"]["event_discriminator_status"] = "DEEP_REVIEW_CANDIDATE"
        legacy.verify_dimensions(self.current, "spot_flow", "cross_exchange_acceptance")
        self.current = production._shift_evidence(self.current)
        # Negative taker flow can support an absorption interpretation without
        # any BUY claim, selected spot cause, or candidate qualification.
        for dimension in ("spot_flow", "cross_exchange_acceptance"):
            self.current["evidence_matrix"][dimension].pop("evidence_claims")
        self.current["causal_attribution"]["material_pattern"] = {
            "schema": pattern.SCHEMA, "review_binding": binding,
            "hypotheses": [{"kind": "ABSORPTION", "stage": "FORMING", "confidence": "LOW",
                            "support_refs": [{"dimension": dimension, "ref_id": self.current["evidence_matrix"][dimension]["sources"][0]["source_id"]}
                                             for dimension in ("spot_flow", "cross_exchange_acceptance")],
                            "counter_refs": [], "uncertainties": ["CROSS_VENUE_FLOW_UNRESOLVED", "PARTICIPANT_INTENT_UNKNOWN"],
                            "next_evidence": ["SELL_PRESSURE_RETEST_RETENTION"]}],
            "impact": ["CAUSAL_EXPLANATION"],
        }
        self.current["runtime"].update(state_id="BTC-STATE-20300301-2331", plan_id="BTC-20300301-2331")
        authored_report(self.current)
        self.current["control"] = c._derive_control_unchecked(self.current, self.baseline)

    def validate(self):
        return c.validate_bundle(self.current, previous_baseline=self.baseline, fact_owner_snapshot=c.load_fact_owner_snapshot())

    def test_legacy_absence_has_no_db_reads_no_new_fields_and_exact_owner_bytes(self):
        expected = c.compute_fingerprints(self.current)
        with mock.patch.object(pattern, "load_hourly_review", side_effect=AssertionError("legacy path read DB")):
            self.assertEqual([], self.validate())
            self.assertEqual(expected, c.compute_fingerprints(self.current))
            self.assertEqual(self.before[c.OWNER_STATE_PATH].decode(), c.render_projection_document(self.prior, "STATE"))
            self.assertEqual(self.before[c.OWNER_PLAN_PATH].decode(), c.render_projection_document(self.prior, "PLAN"))
        self.assertNotIn("material_pattern", c.decision_semantics(self.current)["primary_causal_hypothesis"])
        self.assertEqual(self.before, {path: path.read_bytes() for path in self.before})

    def test_actual_sqlite_review_can_deep_without_quote_range_change(self):
        before = c.observation_semantics(self.current)
        self.current["observation"]["hourly_review_binding"] = self.hourly()
        self.current["fingerprints"] = c.compute_fingerprints(self.current)
        self.assertEqual([], self.validate())
        after = c.observation_semantics(self.current)
        after.pop("hourly_review_binding")
        self.assertEqual(before, after)
        self.assertEqual("A", c._derive_control_unchecked(self.current, self.baseline)["monitor_state"])
        self.assertNotIn("DEEP", market.read_context(self.db)["baselines"])

    def test_fake_or_nondeep_review_cannot_be_decision_binding(self):
        first = self.hourly(as_of="2030-03-01T13:31:00+00:00")
        market.record_completion(self.db, "DEEP", first, "sha256:" + "a" * 64, NOW, accepted_market_assessment=c.accepted_market_assessment(self.current))
        binding = self.hourly(required=False)
        self.current["observation"]["hourly_review_binding"] = binding
        self.current["fingerprints"] = c.compute_fingerprints(self.current)
        self.assertTrue(any("real bound hourly" in error for error in self.validate()))
        binding["review_id"] = "sha256:" + "0" * 64
        self.assertTrue(any("hourly review binding" in error for error in self.validate()))

    def test_hour_start_cannot_admit_evidence_from_later_in_that_hour(self):
        binding = self.hourly()
        self.current["observation"].update(hourly_review_binding=binding, observed_at="2030-03-01T13:30:00+00:00")
        self.current["fingerprints"] = c.compute_fingerprints(self.current)
        self.assertTrue(any("complete hourly evidence cutoff" in error for error in self.validate()))
        result, observation = production.unavailable_pair(self.prior, self.baseline, NOW)
        observation["observation"].update(hourly_review_binding=binding, observed_at="2030-03-01T13:30:00+00:00")
        errors = c.validate_unavailable_receipt(result, self.baseline, observation, c.load_fact_owner_snapshot())
        self.assertTrue(any("complete hourly evidence cutoff" in error for error in errors))

    def test_absorption_interpretation_changes_d_and_report_without_buy_or_add(self):
        self.add_material()
        self.assertEqual([], self.validate())
        self.assertEqual("D", self.current["control"]["monitor_state"])
        self.assertTrue(self.current["control"]["report_required"])
        self.assertEqual(self.prior["portfolio_decision"], self.current["portfolio_decision"])
        self.assertEqual(self.prior["trade_candidates"], self.current["trade_candidates"])
        self.assertEqual("UNRESOLVED", self.current["causal_attribution"]["driver_regime"])
        self.assertNotIn("evidence_claims", self.current["evidence_matrix"]["spot_flow"])
        rendered = report.render_report(self.current["report_semantics"], self.current)
        self.assertTrue(rendered["ok"], rendered)
        self.assertIn("承接解释", rendered["body"])
        self.assertNotIn("ABSORPTION", rendered["body"])

    def test_ref_timestamps_and_wording_are_not_semantic_changes(self):
        self.add_material()
        old = c.compute_fingerprints(self.current)
        material = self.current["causal_attribution"]["material_pattern"]
        material["review_binding"]["review_id"] = "sha256:" + "1" * 64
        material["hypotheses"][0]["support_refs"].reverse()
        self.current["report_semantics"]["primary_causal_explanation"]["narrative"][-1]["text"] = "承接的竞争解释仍有依据，但需要继续验证。"
        new = c.compute_fingerprints(self.current)
        self.assertEqual(old["decision_fingerprint"], new["decision_fingerprint"])
        self.assertEqual(old["report_fingerprint"], new["report_fingerprint"])

    def test_unbound_or_point_only_support_and_promotion_are_rejected(self):
        self.add_material()
        value = self.current["causal_attribution"]["material_pattern"]
        value["hypotheses"][0]["support_refs"][0]["ref_id"] = "MADE_UP"
        self.assertTrue(any("absent or unverified" in error for error in self.validate()))
        self.addCleanup(lambda: None)
        self.current["trade_candidates"][1]["entries"][0]["eligibility"] = "ELIGIBLE_FOR_REVIEW"
        self.assertTrue(any("candidate" in error or "eligibility" in error for error in self.validate()))

    def test_important_pattern_cannot_be_silently_deleted(self):
        self.add_material()
        with production.rendered_baseline(self.current) as (baseline, _, _):
            without_pattern = deepcopy(self.current)
            without_pattern["causal_attribution"].pop("material_pattern")
            next_bundle = production.daily_bundle(without_pattern, baseline, NOW)
            production._report(next_bundle)
            next_bundle["fingerprints"] = c.compute_fingerprints(next_bundle)
            next_bundle["control"] = c._derive_control_unchecked(next_bundle, baseline)
            errors = c.validate_bundle(next_bundle, previous_baseline=baseline, fact_owner_snapshot=c.load_fact_owner_snapshot(), require_owner_baseline=False)
            self.assertTrue(any("FALSIFIED" in error for error in errors))

    def test_v4_review_can_retire_only_the_whole_legacy_pattern_projection(self):
        self.add_material()
        with production.rendered_baseline(self.current) as (baseline, _, _):
            retired = deepcopy(self.current)
            retired["causal_attribution"].pop("material_pattern")
            retired["observation"].pop("hourly_review_binding")
            errors = []
            c._validate_hourly_integration(retired, baseline, errors)
            self.assertTrue(any("FALSIFIED" in error for error in errors))
            errors = []
            c._validate_hourly_integration(retired, baseline, errors, allow_pattern_retirement=True)
            self.assertEqual([], errors)
            partial = deepcopy(self.current)
            partial["causal_attribution"]["material_pattern"]["hypotheses"] = []
            errors = []
            c._validate_hourly_integration(partial, baseline, errors, allow_pattern_retirement=True)
            self.assertTrue(any("FALSIFIED" in error for error in errors))
            invalid = deepcopy(self.current)
            invalid["observation"]["hourly_review_binding"]["review_id"] = "sha256:" + "0" * 64
            errors = []
            c._validate_hourly_integration(invalid, baseline, errors, allow_pattern_retirement=True)
            self.assertTrue(any("hourly review binding" in error for error in errors))

    def test_material_report_must_explain_each_pattern_and_retain_uncertainty(self):
        self.add_material()
        projection = deepcopy(self.current["report_semantics"])
        projection["primary_causal_explanation"]["narrative"] = projection["primary_causal_explanation"]["narrative"][:1]
        self.assertTrue(any("material pattern subject" in error for error in report.validate_report_projection(projection, self.current)))
        projection = deepcopy(self.current["report_semantics"])
        projection["material_uncertainties"]["semantic"] = [item for item in projection["material_uncertainties"]["semantic"] if "pattern_kind" not in item]
        self.assertTrue(any("uncertainties" in error for error in report.validate_report_projection(projection)))

    def test_unavailable_uses_real_hourly_candidate_when_quote_relations_unchanged(self):
        result, observation = production.unavailable_pair(self.prior, self.baseline, NOW)
        observation["observation"] = deepcopy(self.prior["observation"])
        observation["observation"].update(observed_at=NOW, event_discriminator_status="DEEP_REVIEW_CANDIDATE", hourly_review_binding=self.hourly())
        errors = []
        fingerprint = c._validate_unavailable_observation(observation, errors, self.prior, c.load_fact_owner_snapshot().fingerprints)
        self.assertEqual([], errors)
        result["observation_fingerprint"] = fingerprint
        self.assertEqual([], c.validate_unavailable_receipt(result, self.baseline, observation, c.load_fact_owner_snapshot()))
        self.assertNotIn("DEEP", market.read_context(self.db)["baselines"])
        observation["observation"]["hourly_review_binding"]["review_id"] = "sha256:" + "f" * 64
        self.assertTrue(c.validate_unavailable_receipt(result, self.baseline, observation, c.load_fact_owner_snapshot()))

    def test_deep_completion_requires_valid_final_artifacts_and_never_advances_report(self):
        self.current["observation"]["hourly_review_binding"] = self.hourly()
        self.current["fingerprints"] = c.compute_fingerprints(self.current)
        self.current["control"] = c._derive_control_unchecked(self.current, self.baseline)
        receipt = final_receipt(self.current)
        artifacts = {"previous_baseline": self.baseline, "fact_owner_snapshot": c.load_fact_owner_snapshot()}
        invalid = deepcopy(receipt)
        invalid["bundle_identity"] = "sha256:" + "0" * 64
        with self.assertRaises(c.GuardError):
            c.record_validated_hourly_completion(invalid, self.current, "DEEP", **artifacts)
        with self.assertRaises(c.GuardError):
            c.record_validated_hourly_completion(receipt, self.current, "DEEP", require_owner_readback=False, **artifacts)
        self.assertNotIn("DEEP", market.read_context(self.db)["baselines"])
        result = c.record_validated_hourly_completion(receipt, self.current, "DEEP", **artifacts)
        self.assertIn("DEEP", result["completion"])
        before = market.read_context(self.db)["baselines"]
        self.assertIn("DEEP", before)
        self.assertNotIn("REPORT", before)
        bundle_file, receipt_file = self.directory / "bundle.json", self.directory / "receipt.json"
        bundle_file.write_text(json.dumps(self.current), encoding="utf-8")
        receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = c.main(["validate-receipt", "--input", str(receipt_file), "--bundle", str(bundle_file),
                                "--current-state", str(c.OWNER_STATE_PATH), "--current-plan", str(c.OWNER_PLAN_PATH),
                                "--record-hourly-completion", "DEEP"])
        self.assertEqual(0, exit_code, output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertEqual(before, market.read_context(self.db)["baselines"])
        with self.assertRaises(c.GuardError):
            c.record_validated_hourly_completion(receipt, self.current, "REPORT", **artifacts)
        self.assertEqual(before, market.read_context(self.db)["baselines"])
        self.assertEqual(self.before, {path: path.read_bytes() for path in self.before})

    def test_pending_delivery_completes_deep_but_report_requires_exact_cloud_readback(self):
        self.add_material(entry_stage="FORMING")
        self.current["causal_attribution"]["material_pattern"]["hypotheses"][0]["stage"] = "FALSIFIED"
        authored_report(self.current)
        self.current["control"] = c._derive_control_unchecked(self.current, self.baseline)
        with production.rendered_baseline(self.prior) as (snapshot, _, _):
            state = c.render_projection_document(self.current, "STATE")
            plan = c.render_projection_document(self.current, "PLAN")
            c.OWNER_STATE_PATH.write_text(state, encoding="utf-8")
            c.OWNER_PLAN_PATH.write_text(plan, encoding="utf-8")
            body = report.render_report(self.current["report_semantics"], self.current)["body"]
            receipt = final_receipt(self.current)
            receipt.update(local_readback_state="READBACK_VERIFIED", report_validation_state="VALIDATED", cloud_delivery_state="PENDING_RETRY", report_body_fingerprint=c.content_fingerprint(body))
            artifacts = {"previous_baseline": snapshot, "fact_owner_snapshot": c.load_fact_owner_snapshot(),
                         "state_text": state, "plan_text": plan, "state_artifact_path": c.OWNER_STATE_PATH,
                         "plan_artifact_path": c.OWNER_PLAN_PATH, "report_text": body}
            self.assertEqual([], c.validate_receipt(receipt, self.current, **artifacts))
            c.record_validated_hourly_completion(receipt, self.current, "DEEP", **artifacts)
            deep = market.read_context(self.db)["successful_deep"]
            self.assertEqual("FALSIFIED", deep["accepted_market_assessment"]["material_pattern"]["hypotheses"][0]["stage"])
            entry = next(item for item in deep["original_hourly_candidate"]["patterns"] if item["kind"] == "ABSORPTION")
            self.assertEqual("FORMING", entry["stage"])
            self.assertNotIn("review", deep)
            accepted_text = json.dumps(deep["accepted_market_assessment"])
            for forbidden in ("account_impact", "portfolio_decision", "account_facts", "report_semantics", "amount_u", "reasoning_summary"):
                self.assertNotIn(forbidden, accepted_text)
            with self.assertRaises(c.GuardError):
                c.record_validated_hourly_completion(receipt, self.current, "REPORT", **artifacts)
            self.assertNotIn("REPORT", market.read_context(self.db)["baselines"])
            readback = {"schema": c.CLOUD_READBACK_SCHEMA, "visible_report_text": body, "assistant_responses": ["已收录"]}
            receipt.update(cloud_delivery_state="DELIVERED", report_visible=True, cloud_visible_body_fingerprint=c.content_fingerprint(body), cloud_readback_fingerprint=c._fingerprint(readback))
            c.record_validated_hourly_completion(receipt, self.current, "REPORT", cloud_readback=readback, **artifacts)
            self.assertIn("REPORT", market.read_context(self.db)["baselines"])
            reported = market.read_context(self.db)["reported_conclusion"]
            self.assertEqual(deep["accepted_market_assessment"], reported["accepted_market_assessment"])
            self.assertEqual(self.current["fingerprints"]["report_fingerprint"], reported["report_fingerprint"])

    def test_accepted_market_projection_rejects_account_or_full_report_payloads(self):
        self.add_material()
        accepted = c.accepted_market_assessment(self.current)
        self.assertEqual([], pattern.validate_accepted_market_assessment(accepted))
        bad = deepcopy(accepted)
        bad["portfolio_decision"] = self.current["portfolio_decision"]
        self.assertTrue(pattern.validate_accepted_market_assessment(bad))
        bad = deepcopy(accepted)
        bad["evidence_refs"]["account_impact"] = {"status": "VERIFIED", "references": []}
        self.assertTrue(pattern.validate_accepted_market_assessment(bad))
        bad = deepcopy(accepted)
        bad["causal_semantics"]["reasoning_summary"] = "原始思维链不得入库"
        self.assertTrue(pattern.validate_accepted_market_assessment(bad))


if __name__ == "__main__":
    unittest.main()
