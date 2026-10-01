from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import decision_report as report
import revision2_report_bridge as bridge


def same_meaning_bundle() -> dict:
    """Independent new-format expression of only the recoverable old meaning."""
    roles = ("LEGACY_MODULE_ROLE_NOT_STATED", "LEGACY_MODULE_ROLE_NOT_STATED", "INCREMENTAL_SPOT", "LEGACY_SHORT_PURPOSE_NOT_STATED", "LEGACY_OPTIONALITY_ROLE_NOT_STATED")
    actions = {module: {
        "decision": "MAINTAIN", "amount_u": "0.00", "portfolio_role": role,
        "net_exposure_impact": "UNCHANGED", "capital_pool_impact": "NONE",
        "reason_class": "WAIT_FOR_DECISIONAL_EVIDENCE",
        "zero_amount_reason_class": "LEGACY_OPTIONALITY_COMPARISON_NOT_STATED",
        "formation_conditions": ["DECISION_EVIDENCE_CHANGES"],
        "invalidation_conditions": ["LEGACY_MODULE_INVALIDATION_NOT_STATED"],
    } for module, role in zip(report.MODULES, roles)}
    hypotheses = [{
        "hypothesis_id": key, "thesis": "测试用的自然中文表述，不参与指纹。",
        "counter_evidence": ["NO_STRUCTURAL_CONFIRMATION"],
        "missing_evidence": ["SPOT_FLOW_AND_OI_SEQUENCE"],
        "next_discriminating_evidence": ["CROSS_VENUE_SPOT_AND_OI_TIMELINE"],
        "falsification_conditions": ["OPPOSING_DRIVER_SEQUENCE_VERIFIED"],
        "causal_timeline": [],
    } for key in ("H_UNRESOLVED", "H_SPOT", "H_LEVERAGE", "H_MACRO")]
    dimensions = ("price_quotes", "cross_exchange_acceptance", "spot_flow", "derivatives_flow", "oi_timeline", "funding_basis", "capital_source", "macro", "relative_strength", "account_impact")
    return {
        "path_decision": "MAINTAIN", "projection_mode": "LAST_VALID_ANCHOR",
        "decision_evidence_status": "EVIDENCE_LIMITED", "user_input_requirement": "NONE",
        "causal_attribution": {"driver_regime": "UNRESOLVED", "driver_components": [], "selected_hypothesis_id": "H_UNRESOLVED", "rejected_hypothesis_ids": ["H_MACRO", "H_SPOT", "H_LEVERAGE"], "hypotheses": hypotheses},
        "portfolio_decision": actions,
        "evidence_matrix": {dimension: {"status": "VERIFIED" if dimension in {"price_quotes", "account_impact"} else "UNRESOLVED", "sources": [], "evidence_claims": []} for dimension in dimensions},
        "major_risk": {"active": False, "kinds": ["NONE"], "portfolio_effect": "NONE"},
        "value_zones": [
            {"zone_id": "V0", "role": "PERSISTENT_VALUE_DISPUTE_ZONE", "lifecycle": "RETEST_PENDING", "band": {"lower_u": "77000.00", "upper_u": "77500.0"}},
            {"zone_id": "V1", "role": "UNRETESTED_VALUE_ZONE", "lifecycle": "RETEST_PENDING", "band": {"lower_u": "78300", "upper_u": "80000"}},
        ],
        "trade_candidates": [], "runtime": {"pricing_as_of": "2030-01-01T00:00:00Z"},
        "observation": {"raw_prices": {"sample": "90000"}},
    }


def paragraphs() -> dict:
    def paragraph(text, subject=None):
        result = {"text": text, "verification": "UNVERIFIED", "evidence_refs": []}
        if subject:
            result["subject"] = subject
        return result
    return {
        "overall_user_decision": [paragraph("旧判断仍是等待结构证据，不把回升直接当成调整仓位的依据。")],
        "primary_causal_explanation": [paragraph("旧资料不足以确定主要驱动，资金成本只能作为观察背景。")],
        "rejected_competing_explanations": [paragraph("历史资料尚未形成这项解释所需的完整证据链。", subject) for subject in ("H_SPOT", "H_LEVERAGE", "H_MACRO")],
        "portfolio_action_by_module": [paragraph("本轮建议增量为 {{amount_u}} U，既有事实保持分账；未记录的角色细节仍未知。", module) for module in report.MODULES],
        "material_uncertainties": [paragraph("报价与本地记录可用，不代表完整决策证据链或实时账户安全已经核验。")],
        "next_decision_changing_evidence": [paragraph("需要跨所留存与原生现货、持仓量的时序形成一致解释；相反驱动可以推翻等待论点。")],
        "user_visible_major_risk": [paragraph("旧报告没有提出新的重大风险；这不是实时账户安全结论。")],
        "decision_relevant_levels_and_sizes": [paragraph("{{lower_u}} U 至 {{upper_u}} U 的下层结构仍等待回测。", "V0")],
    }


def fingerprint(semantics: dict) -> str:
    wrappers = {field: {"semantic": semantics[field], "narrative": []} for field in report.FIELDS}
    return report.report_fingerprint(wrappers)


class Revision2ReportBridgeTests(unittest.TestCase):
    def completed_legacy_details(self):
        current = bridge.bridge_semantics()
        for module, action in current["portfolio_action_by_module"].items():
            if module != "new_spot":
                action["portfolio_role"] = "REVIEWED_MODULE_ROLE"
            action["zero_amount_reason_class"] = "OPTIONALITY_DOMINATES_IMMEDIATE_CHANGE"
            action["invalidation_conditions"] = ["ACCOUNT_SAFETY_CONFLICT"]
        return current

    def test_same_business_meaning_has_same_fingerprint_across_format(self):
        old = bridge.bridge_semantics()
        new = report.build_report_projection(same_meaning_bundle(), paragraphs(), visible_level_ids={"V0"})
        self.assertEqual(old, report.semantic_projection(new))
        self.assertEqual(fingerprint(old), report.report_fingerprint(new))

    def test_meaningful_amount_direction_and_next_evidence_change_fingerprint(self):
        old_hash = fingerprint(bridge.bridge_semantics())
        for change in ("amount", "direction", "next_evidence"):
            with self.subTest(change=change):
                current = same_meaning_bundle()
                if change == "amount":
                    current["portfolio_decision"]["new_spot"]["amount_u"] = "725"
                elif change == "direction":
                    current["portfolio_decision"]["new_short"]["net_exposure_impact"] = "REDUCE_NET_LONG_CONDITIONALLY"
                else:
                    current["causal_attribution"]["hypotheses"][0]["next_discriminating_evidence"] = ["NATIVE_SELLING_LED_ACCEPTANCE"]
                new = report.build_report_projection(current, paragraphs(), visible_level_ids={"V0"})
                self.assertNotEqual(old_hash, report.report_fingerprint(new))

    def test_claim_buy_and_sell_remain_distinct_from_legacy_and_each_other(self):
        buy = bridge.bridge_semantics()
        buy["primary_causal_explanation"]["mechanism"] = [{"dimension": "spot_flow", "event_role": "CAUSE_OBSERVED", "claim_type": "NATIVE_SPOT_FLOW", "direction": "BUY"}]
        sell = deepcopy(buy)
        sell["primary_causal_explanation"]["mechanism"][0]["direction"] = "SELL"
        self.assertEqual(3, len({fingerprint(bridge.bridge_semantics()), fingerprint(buy), fingerprint(sell)}))

    def test_every_artifact_is_byte_bound_before_recovery(self):
        original = Path.read_bytes
        for filename, _ in bridge.ARTIFACTS.values():
            with self.subTest(filename=filename):
                def tamper(path):
                    data = original(path)
                    return data + b"\n" if path.name == filename else data
                with mock.patch.object(Path, "read_bytes", tamper):
                    with self.assertRaises(bridge.BridgeError):
                        bridge.bridge_semantics()

    def test_audit_has_exact_source_spans_for_all_eight_sections(self):
        artifact = bridge.bridge_artifact()
        self.assertEqual(set(report.FIELDS), set(artifact["audit_witness"]))
        for witness in artifact["audit_witness"].values():
            self.assertTrue(witness["interpretation"])
            self.assertTrue(witness["sources"])
            for source in witness["sources"]:
                lines = (bridge.FIXTURE_DIRECTORY / source["artifact"]).read_text(encoding="utf-8").splitlines()
                span = "\n".join(lines[source["line_start"] - 1:source["line_end"]])
                self.assertIn(source["quote"], span)

    def test_zero_means_proposed_increment_never_actual_cash_or_position(self):
        artifact = bridge.bridge_artifact()
        for item in artifact["semantic_projection"]["portfolio_action_by_module"].values():
            self.assertEqual(("0", "UNCHANGED", "NONE"), (item["amount_u"], item["net_exposure_impact"], item["capital_pool_impact"]))
        audit = artifact["audit_witness"]["portfolio_action_by_module"]
        self.assertEqual("DIRECT_PROPOSED_INCREMENT_NOT_ACCOUNT_FACT", audit["inference_kind"])
        self.assertIn("绝非账户Delta或现金", audit["interpretation"])
        self.assertTrue(artifact["explicit_unknowns"])

    def test_unrecoverable_roles_are_not_replaced_by_current_decision(self):
        artifact = bridge.bridge_artifact()
        role = artifact["semantic_projection"]["portfolio_action_by_module"]["existing_alpha_short"]["portfolio_role"]
        self.assertEqual("LEGACY_MODULE_ROLE_NOT_STATED", role)
        with self.assertRaises(TypeError):
            bridge.bridge_semantics(same_meaning_bundle())

    def test_metadata_and_wording_do_not_create_a_migration_report(self):
        current = same_meaning_bundle()
        current["projection_mode"] = "CURRENT_DERIVATION"
        current["user_input_requirement"] = "FACT_SYNC"
        text = paragraphs()
        text["overall_user_decision"][0]["text"] = "仍保留现有配置，暂不新增风险，继续观察结构留存。"
        new = report.build_report_projection(current, text, visible_level_ids={"V0"})
        self.assertEqual(fingerprint(bridge.bridge_semantics()), report.report_fingerprint(new))

    def test_comparison_v0_only_does_not_claim_v1_was_absent_from_old_report(self):
        artifact = bridge.bridge_artifact()
        self.assertEqual(["V0"], [item["level_id"] for item in artifact["semantic_projection"]["decision_relevant_levels_and_sizes"]])
        audit = artifact["audit_witness"]["decision_relevant_levels_and_sizes"]["interpretation"]
        self.assertIn("旧报告正文确实包含V1及候选阶梯", audit)

    def test_audit_and_codec_are_not_report_semantics_and_results_are_fresh(self):
        first = bridge.bridge_semantics()
        self.assertEqual(set(report.FIELDS), set(first))
        first["portfolio_action_by_module"]["new_spot"]["amount_u"] = "99999"
        self.assertEqual("0", bridge.bridge_semantics()["portfolio_action_by_module"]["new_spot"]["amount_u"])

    def test_migration_masks_only_fixed_historical_unknowns_on_both_sides(self):
        current = self.completed_legacy_details()
        result = bridge.migration_compare(current)
        self.assertFalse(result["changed"])
        self.assertEqual([], result["known_differences"])
        self.assertEqual(list(bridge.HISTORICAL_UNKNOWN_PATHS), result["historical_unknown_paths"])
        self.assertEqual(result["previous_report_fingerprint"], result["current_report_fingerprint"])
        self.assertNotEqual(fingerprint(current), fingerprint(bridge.bridge_semantics()))

    def test_previous_comparison_hash_never_depends_on_current_values(self):
        first = bridge.migration_compare(self.completed_legacy_details())
        second_values = self.completed_legacy_details()
        second_values["portfolio_action_by_module"]["new_short"]["portfolio_role"] = "ANOTHER_CURRENT_ROLE"
        second_values["portfolio_action_by_module"]["new_short"]["amount_u"] = "1500"
        second = bridge.migration_compare(second_values)
        self.assertEqual(first["previous_report_fingerprint"], second["previous_report_fingerprint"])
        self.assertNotEqual(first["current_report_fingerprint"], second["current_report_fingerprint"])
        self.assertTrue(second["changed"])
        self.assertEqual("LEGACY_MODULE_ROLE_NOT_STATED", bridge.bridge_semantics()["portfolio_action_by_module"]["existing_alpha_short"]["portfolio_role"])

    def test_known_decision_amount_direction_condition_and_risk_changes_survive_mask(self):
        for name in ("amount", "direction", "decision", "known_role", "condition", "risk", "next"):
            with self.subTest(name=name):
                current = self.completed_legacy_details()
                action = current["portfolio_action_by_module"]["new_spot"]
                if name == "amount":
                    action["amount_u"] = "625"
                elif name == "direction":
                    action["net_exposure_impact"] = "INCREASE_NET_LONG_CONDITIONALLY"
                elif name == "decision":
                    action["decision"] = "CONDITIONAL_ADD"
                    current["overall_user_decision"]["module_decisions"]["new_spot"] = "CONDITIONAL_ADD"
                elif name == "known_role":
                    action["portfolio_role"] = "REDUCED_SPOT_EXPOSURE"
                elif name == "condition":
                    action["formation_conditions"] = ["NATIVE_SPOT_ACCEPTANCE"]
                elif name == "risk":
                    current["user_visible_major_risk"] = {"active": True, "kinds": ["ACCOUNT_SAFETY"], "portfolio_effect": "FREEZE_INCREMENTAL_RISK"}
                else:
                    current["next_decision_changing_evidence"]["conditions"] = ["PERSISTENT_NATIVE_SELLING"]
                result = bridge.migration_compare(current)
                self.assertTrue(result["changed"])
                self.assertTrue(result["known_differences"])
                self.assertNotEqual(result["previous_report_fingerprint"], result["current_report_fingerprint"])

    def test_mask_cannot_be_expanded_by_putting_legacy_prefix_on_known_field(self):
        current = self.completed_legacy_details()
        current["portfolio_action_by_module"]["new_spot"]["capital_pool_impact"] = "LEGACY_CASH_NOT_STATED"
        result = bridge.migration_compare(current)
        self.assertTrue(result["changed"])
        self.assertEqual("portfolio_action_by_module.new_spot.capital_pool_impact", result["known_differences"][0]["path"])

    def test_migration_comparison_does_not_mutate_input(self):
        current = self.completed_legacy_details()
        before = deepcopy(current)
        bridge.migration_compare(current)
        self.assertEqual(before, current)

    def test_migration_comparison_rejects_missing_or_malformed_known_data(self):
        current = self.completed_legacy_details()
        current["portfolio_action_by_module"]["new_spot"].pop("amount_u")
        with self.assertRaises(bridge.BridgeError):
            bridge.migration_compare(current)
        with self.assertRaises(bridge.BridgeError):
            bridge.migration_compare({"runtime": "not pure semantics"})

    def test_new_codec_spelling_is_not_silently_treated_as_equal_meaning(self):
        current = self.completed_legacy_details()
        current["next_decision_changing_evidence"]["conditions"] = ["CROSS_VENUE_ACCEPTANCE_WITH_CAUSAL_TIMELINE"]
        result = bridge.migration_compare(current)
        self.assertTrue(result["changed"])
        self.assertEqual("next_decision_changing_evidence.conditions", result["known_differences"][0]["path"])
        self.assertTrue(any("命名" in warning for warning in bridge.CODEC_COMPARISON_LIMITATIONS))

    def test_masked_hash_is_exactly_the_explicitly_reduced_eight_dimensional_view(self):
        current = self.completed_legacy_details()
        reduced = deepcopy(current)
        for path in bridge.HISTORICAL_UNKNOWN_PATHS:
            section, module, key = path.split(".")
            del reduced[section][module][key]
        result = bridge.migration_compare(current)
        self.assertEqual(fingerprint(reduced), result["current_report_fingerprint"])


if __name__ == "__main__":
    unittest.main()
