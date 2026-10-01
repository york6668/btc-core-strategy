from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import decision_report as report


def bundle_fixture() -> dict:
    def action(role: str, amount: str = "0") -> dict:
        return {
            "portfolio_role": role, "decision": "MAINTAIN", "amount_u": amount,
            "reason_class": "WAIT_FOR_ABSORPTION", "zero_amount_reason_class": "OPTIONALITY_DOMINATES",
            "formation_conditions": ["CROSS_VENUE_ABSORPTION"],
            "invalidation_conditions": ["PERSISTENT_NATIVE_SELLING"],
            "net_exposure_impact": "UNCHANGED", "capital_pool_impact": "NONE",
        }
    selected = {
        "hypothesis_id": "H_SPOT_ABSORPTION", "thesis": "现货吸收可能解释本轮回升。",
        "counter_evidence": [], "missing_evidence": ["ACCEPTANCE_SEQUENCE"],
        "next_discriminating_evidence": ["CROSS_VENUE_ABSORPTION"],
        "falsification_conditions": ["PERSISTENT_NATIVE_SELLING"],
        "causal_timeline": [{"evidence_dimension": "spot_flow", "evidence_ref": "FLOW_BUY", "event_role": "CAUSE_OBSERVED", "occurred_at": "2026-09-04T01:00:00Z"}],
    }
    alternative = deepcopy(selected)
    alternative.update({"hypothesis_id": "H_LEVERAGE_OVERLAY", "counter_evidence": ["OI_DID_NOT_LEAD"], "missing_evidence": ["LEVERAGED_BUY_SEQUENCE"]})
    return {
        "path_decision": "MAINTAIN", "projection_mode": "LAST_VALID_ANCHOR", "user_input_requirement": "NONE",
        "decision_evidence_status": "EVIDENCE_LIMITED",
        "causal_attribution": {"selected_hypothesis_id": selected["hypothesis_id"], "driver_regime": "CRYPTO_NATIVE_SPOT", "driver_components": [], "hypotheses": [selected, alternative], "rejected_hypothesis_ids": [alternative["hypothesis_id"]]},
        "portfolio_decision": {module: action(role) for module, role in zip(report.MODULES, ("CORE_UPSIDE", "PROTECTIVE_ALPHA", "INCREMENTAL_SPOT", "INCREMENTAL_HEDGE", "CASH_OPTIONALITY"))},
        "evidence_matrix": {
            "spot_flow": {"status": "VERIFIED", "sources": [{"source_id": "PUBLIC_SPOT", "endpoint_url": "https://api.kraken.com/0/public/Trades?pair=XBTUSD", "retrieved_at": "2026-09-04T01:01:00Z"}], "evidence_claims": [{"claim_id": "FLOW_BUY", "claim_type": "NATIVE_SPOT_FLOW", "direction": "BUY", "status": "VERIFIED", "source_ids": ["PUBLIC_SPOT"], "metric": {"periods": 2}}]},
            "cross_exchange_acceptance": {"status": "UNRESOLVED", "sources": [], "evidence_claims": []},
            "account_impact": {"status": "VERIFIED", "sources": [{"source_id": "LOCAL_RECORD"}], "evidence_claims": []},
        },
        "major_risk": {"active": False, "kinds": ["NONE"], "portfolio_effect": "NONE"},
        "value_zones": [{"zone_id": "VALUE_RETEST", "role": "UNRETESTED_VALUE_ZONE", "lifecycle": "RETEST_PENDING", "band": {"lower_u": "81200", "upper_u": "82700"}}],
        "trade_candidates": [{
            "candidate_id": "PREMIUM_SHORT", "kind": "PREMIUM_SHORT", "entries": [{
                "entry_id": "S7", "price_u": "86240", "amount_u": "925.50", "value_role": "UNCONFIRMED_PREMIUM_EXTENSION", "eligibility": "ELIGIBLE_FOR_REVIEW",
                "amount_basis": {"budget_source": "ALPHA_RISK_BUDGET", "net_exposure_constraint": "PORTFOLIO_REMAINS_NET_LONG", "value_gap_rationale": "EXTENSION_WITHOUT_ACCEPTANCE", "risk_assumption": "VOLATILITY_BUDGET_RECONFIRMED"},
                "formation_conditions": ["LEVERAGE_DOMINATES_WITHOUT_SPOT_VALUE"], "invalidation_conditions": ["NATIVE_SPOT_ACCEPTANCE_ABOVE_ZONE"], "evidence_confidence": "CONDITIONAL", "account_precondition": "LOCAL_SAFETY_FACTS_ALLOW", "carried_forward": False,
            }],
        }],
        "observation": {"observed_at": "2026-09-04T01:02:00Z", "raw_prices": {"binance": "83234"}},
        "runtime": {"run_id": "temporary", "pricing_as_of": "2026-09-04T01:02:00Z"},
    }


def paragraph(text: str, subject: str | None = None, *, linked: bool = False) -> dict:
    value = {"text": text, "verification": "EVIDENCE_LINKED" if linked else "UNVERIFIED", "evidence_refs": [{"dimension": "spot_flow", "ref_id": "FLOW_BUY"}] if linked else []}
    if subject is not None:
        value["subject"] = subject
    return value


def narrative_fixture(*, levels: bool = True) -> dict:
    return {
        "overall_user_decision": [paragraph("维持组合的中长期净多角色，把新资金留给具备赔率优势的机会。")],
        "primary_causal_explanation": [paragraph("现货吸收是当前较有解释力的假设，但尚未证明跨交易所留存。", linked=True)],
        "rejected_competing_explanations": [paragraph("暂不把回升主要归因于杠杆追涨，因为持仓量的领先关系尚不完整。", "H_LEVERAGE_OVERLAY")],
        "portfolio_action_by_module": [paragraph("本轮建议调整规模为 {{amount_u}} U。暂留敞口，以免过早牺牲组合的保护作用。", module) for module in report.MODULES],
        "material_uncertainties": [paragraph("关键问题不是报价是否一致，而是承接能否在后续交易时段继续。")],
        "next_decision_changing_evidence": [paragraph("若出现跨所持续吸收且离开后仍能留存，会重评新增现货；持续原生卖出则削弱该解释。")],
        "user_visible_major_risk": [paragraph("尚未识别新的重大风险；账户中未记录的实时安全参数仍未知。")],
        "decision_relevant_levels_and_sizes": [paragraph("延伸至 {{price_u}} U 附近时，才评估名义 {{amount_u}} U 的保护性空头；资金来源是组合保护预算，不能取代现货底仓。", "S7")] if levels else [],
    }


class DecisionReportTests(unittest.TestCase):
    def setUp(self):
        self.bundle = bundle_fixture()
        self.projection = report.build_report_projection(self.bundle, narrative_fixture())

    def test_natural_report_has_dynamic_amount_entry_and_no_control_data(self):
        result = report.render_report(self.projection, self.bundle)
        self.assertIn("86240 U", result["body"])
        self.assertIn("925.5 U", result["body"])
        self.assertIn("现货吸收是当前较有解释力的假设", result["body"])
        self.assertIn("尚未验证", result["body"])
        self.assertNotIn("S7", result["body"])
        self.assertNotIn("H_SPOT", result["body"])
        self.assertNotIn("fingerprint", result["body"])
        self.assertEqual([], report.validate_report_text(result["body"], result["validation_projection"], self.bundle))

    def test_no_prices_are_required_when_irrelevant(self):
        value = report.build_report_projection(self.bundle, narrative_fixture(levels=False), visible_level_ids=[])
        self.assertEqual([], value["decision_relevant_levels_and_sizes"]["semantic"])
        self.assertEqual([], report.validate_report_projection(value))

    def test_positive_new_short_and_arbitrary_spot_amounts_bind_to_modules(self):
        self.bundle["portfolio_decision"]["new_short"].update({"decision": "CONDITIONAL_ADD", "amount_u": "925.5", "net_exposure_impact": "REDUCE_NET_LONG_CONDITIONALLY", "capital_pool_impact": "RESERVE_CONDITIONALLY"})
        self.bundle["portfolio_decision"]["new_spot"].update({"decision": "CONDITIONAL_ADD", "amount_u": "1375"})
        value = report.build_report_projection(self.bundle, narrative_fixture())
        body = report.render_report(value)["body"]
        self.assertIn("新增空头：本轮建议调整规模为 925.5 U", body)
        self.assertIn("新增现货：本轮建议调整规模为 1375 U", body)

    def test_narrative_edits_do_not_change_semantic_fingerprint(self):
        other = deepcopy(self.projection)
        other["primary_causal_explanation"]["narrative"][0]["text"] = "当前证据较支持现货吸收，但结构留存的证明仍不充分。"
        self.assertEqual(report.report_fingerprint(self.projection), report.report_fingerprint(other))
        self.assertNotEqual(report.render_report(self.projection)["report_body_fingerprint"], report.render_report(other)["report_body_fingerprint"])

    def test_snapshot_time_and_internal_projection_mode_do_not_change_fingerprint(self):
        self.bundle["observation"]["raw_prices"]["binance"] = "90123"
        self.bundle["runtime"]["pricing_as_of"] = "2027-01-01T01:02:00Z"
        self.bundle["projection_mode"] = "CURRENT_DERIVATION"
        self.bundle["evidence_matrix"]["spot_flow"]["sources"][0]["retrieved_at"] = "2027-01-01T01:01:00Z"
        other = report.build_report_projection(self.bundle, narrative_fixture())
        self.assertEqual(report.report_fingerprint(self.projection), report.report_fingerprint(other))

    def test_reordering_semantic_sets_and_decimal_spelling_are_stable(self):
        self.bundle["trade_candidates"][0]["entries"][0]["amount_u"] = "925.5000"
        other = report.build_report_projection(self.bundle, narrative_fixture())
        self.assertEqual(report.report_fingerprint(self.projection), report.report_fingerprint(other))

    def test_all_eight_semantic_classes_affect_fingerprint(self):
        changes = {
            "overall_user_decision": lambda s: s.update(path_decision="MOVE_UP"),
            "primary_causal_explanation": lambda s: s["mechanism"][0].update(direction="SELL"),
            "rejected_competing_explanations": lambda s: s[0]["missing_evidence"].append("ALTERNATIVE_SEQUENCE"),
            "portfolio_action_by_module": lambda s: s["new_spot"].update(amount_u="1400"),
            "material_uncertainties": lambda s: s[0].update(status="CONTRADICTED"),
            "next_decision_changing_evidence": lambda s: s["conditions"].append("SELLING_REVERSAL"),
            "user_visible_major_risk": lambda s: s.update(active=True),
            "decision_relevant_levels_and_sizes": lambda s: s[0].update(price_u="87000"),
        }
        for field, change in changes.items():
            with self.subTest(field=field):
                other = deepcopy(self.projection)
                change(other[field]["semantic"])
                self.assertNotEqual(report.report_fingerprint(self.projection), report.report_fingerprint(other))

    def test_each_action_binding_is_checked_against_bundle(self):
        for key, value in {"amount_u": "600", "decision": "EXIT_REVIEW", "portfolio_role": "DIFFERENT_ROLE", "net_exposure_impact": "INCREASE_NET_LONG", "capital_pool_impact": "RESERVE"}.items():
            with self.subTest(key=key):
                other = deepcopy(self.projection)
                other["portfolio_action_by_module"]["semantic"]["new_short"][key] = value
                self.assertTrue(report.validate_report_projection(other, self.bundle))

    def test_numeric_mentions_are_bound_to_subject_not_free_text(self):
        other = deepcopy(self.projection)
        other["portfolio_action_by_module"]["narrative"][0]["text"] = "本轮增加 600U。"
        self.assertTrue(report.validate_report_projection(other))
        other["portfolio_action_by_module"]["narrative"][0]["text"] = "2026 年后续 4 小时观察仍不足以决定中长期仓位，建议调整 {{amount_u}} U。"
        self.assertEqual([], report.validate_report_projection(other))

    def test_wrong_numeric_placeholder_and_missing_module_fail(self):
        other = deepcopy(self.projection)
        other["portfolio_action_by_module"]["narrative"][0]["text"] = "参考 {{price_u}} U。"
        self.assertTrue(report.validate_report_projection(other))
        other = deepcopy(self.projection)
        other["portfolio_action_by_module"]["narrative"].pop()
        self.assertTrue(report.validate_report_projection(other))

    def test_body_is_frozen_exactly_before_delivery(self):
        result = report.render_report(self.projection)
        for body in (result["body"] + "\n", " " + result["body"], result["body"].replace("\n", "\r\n"), result["body"].replace("925.5", "600")):
            with self.subTest(body=body[-20:]):
                self.assertTrue(report.validate_report_text(body, result["validation_projection"]))
        altered = deepcopy(result["validation_projection"])
        altered["rendered_evidence"]["frozen_body"] += "x"
        self.assertTrue(report.validate_report_text(result["body"], altered))

    def test_freezing_does_not_mutate_input_or_authorize_execution(self):
        before = deepcopy(self.projection)
        result = report.render_report(self.projection)
        self.assertEqual(before, self.projection)
        self.assertIn("不代表成交或交易授权", result["body"])
        self.assertEqual(set(result), {"ok", "hard_failure", "errors", "body", "report_body_fingerprint", "validation_projection"})

    def test_evidence_refs_must_exist_in_verified_parent_and_urls_bind(self):
        other = deepcopy(self.projection)
        ref = other["primary_causal_explanation"]["narrative"][0]["evidence_refs"][0]
        ref["ref_id"] = "MISSING"
        self.assertTrue(report.validate_report_projection(other, self.bundle))
        ref["ref_id"] = "FLOW_BUY"
        ref["source_urls"] = ["https://other.example.org/evidence"]
        self.assertTrue(report.validate_report_projection(other, self.bundle))
        self.bundle["evidence_matrix"]["spot_flow"]["status"] = "UNRESOLVED"
        self.assertTrue(report.validate_report_projection(self.projection, self.bundle))

    def test_link_without_evidence_and_internal_controls_are_rejected(self):
        for text in ("gpt-5.6-sol / max 已判断。", "BTC-STATE-20260904-1400", "report_required=true", "H_SPOT_ABSORPTION", "A/D/E", "<!-- 注释 -->"):
            other = deepcopy(self.projection)
            other["overall_user_decision"]["narrative"][0]["text"] = text
            self.assertTrue(report.validate_report_projection(other))
        other = deepcopy(self.projection)
        other["primary_causal_explanation"]["narrative"][0]["evidence_refs"] = []
        self.assertTrue(report.validate_report_projection(other))

    def test_claim_direction_changes_report_semantics_but_alias_does_not(self):
        claim = self.bundle["evidence_matrix"]["spot_flow"]["evidence_claims"][0]
        claim["claim_type"] = "NATIVE_SPOT_BUY_FLOW"
        alias = report.build_report_projection(self.bundle, narrative_fixture())
        self.assertEqual(report.report_fingerprint(self.projection), report.report_fingerprint(alias))
        claim["claim_type"] = "NATIVE_SPOT_FLOW"
        claim["direction"] = "SELL"
        opposite = report.build_report_projection(self.bundle, narrative_fixture())
        self.assertNotEqual(report.report_fingerprint(self.projection), report.report_fingerprint(opposite))

    def test_semantic_prose_is_not_hashed_as_if_stable_identifier(self):
        self.bundle["causal_attribution"]["hypotheses"][0]["next_discriminating_evidence"] = ["等待一段跨所留存证据"]
        with self.assertRaises(report.ReportError):
            report.build_report_projection(self.bundle, narrative_fixture())

    def test_nonzero_action_may_omit_zero_reason_in_bundle(self):
        action = self.bundle["portfolio_decision"]["new_short"]
        action.update({"decision": "CONDITIONAL_ADD", "amount_u": "925.5"})
        action.pop("zero_amount_reason_class")
        projection = report.build_report_projection(self.bundle, narrative_fixture())
        self.assertIsNone(projection["portfolio_action_by_module"]["semantic"]["new_short"]["zero_amount_reason_class"])

    def test_causal_helper_leaves_malformed_shape_to_main_validator(self):
        for value in (None, [], {"causal_attribution": None}, {"causal_attribution": {"hypotheses": None}}, {"evidence_matrix": None}):
            self.assertEqual([], report.causal_mechanism_semantics(value))

    def test_hidden_module_amount_or_level_amount_fails(self):
        for field in ("portfolio_action_by_module", "decision_relevant_levels_and_sizes"):
            other = deepcopy(self.projection)
            other[field]["narrative"][0]["text"] = "维持目前的策略判断。"
            self.assertTrue(report.validate_report_projection(other))

    def test_dynamic_zone_ids_bounds_and_required_relevance(self):
        narratives = narrative_fixture(levels=False)
        narratives["decision_relevant_levels_and_sizes"] = [paragraph("{{lower_u}} U 至 {{upper_u}} U 是当前待回测区间。", "VALUE_RETEST")]
        value = report.build_report_projection(self.bundle, narratives, visible_level_ids=["VALUE_RETEST"])
        self.assertIn("81200 U 至 82700 U", report.render_report(value)["body"])
        self.assertTrue(report.validate_report_projection(value, required_visible_level_ids={"S7"}))
        other = deepcopy(value)
        other["decision_relevant_levels_and_sizes"]["semantic"][0]["price_band_u"]["lower_u"] = "90000"
        self.assertTrue(report.validate_report_projection(other))

    def test_legacy_normalizer_reports_unknowns_without_inventing_new_fingerprint(self):
        path = Path(__file__).parent / "fixtures" / "revision2_report_1346_semantics.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        result = report.normalize_legacy_report_semantics(legacy)
        self.assertFalse(result["complete"])
        self.assertTrue(result["unavailable_fields"])
        self.assertNotIn("report_fingerprint", result)
        self.assertEqual("MAINTAIN", result["comparison_projection"]["portfolio_action_by_module"]["new_short"])


if __name__ == "__main__":
    unittest.main()
