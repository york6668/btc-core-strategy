"""Isolated production-profile integration tests; never write actual Owners.

The market evidence is synthetic and the local clock is explicit.  Real local
fact files are read only for their existing binding, never copied into output.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
import decision_report as report  # noqa: E402
import strategy_contract as contract  # noqa: E402
import test_event_contract as events  # noqa: E402
import test_strategy_contract as legacy  # noqa: E402


@contextmanager
def frozen_clock(instant: str):
    current = datetime.fromisoformat(instant.replace('Z', '+00:00'))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    with mock.patch.object(contract, "datetime", Clock):
        yield


def _shift_evidence(value):
    if isinstance(value, dict):
        return {key: _shift_evidence(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shift_evidence(item) for item in value]
    if isinstance(value, str) and value.startswith("2026-09-02T"):
        delta = datetime.fromisoformat(events.INITIAL) - datetime.fromisoformat("2026-09-02T04:41:00+09:00")
        return (datetime.fromisoformat(value) + delta).isoformat()
    return value


def _paragraph(text: str, subject=None) -> dict:
    value = {"text": text, "verification": "UNVERIFIED", "evidence_refs": []}
    if subject is not None:
        value["subject"] = subject
    return value


def narratives(bundle: dict) -> dict:
    rejected_text = {
        "H_UNRESOLVED": "当前已有可辨认的领先时序，完全未解决的解释不再优先。",
        "H_SPOT": "原生现货解释缺少跨所接受与持续承接。",
        "H_LEVERAGE": "杠杆解释缺少可验证的持仓量变化时序。",
        "H_MACRO": "宏观解释缺少数据公布与资产反应的领先关系。",
    }
    result = {
        "overall_user_decision": [_paragraph("现有资料尚不足以改变组合决定，保留结构锚并继续观察。")],
        "primary_causal_explanation": [_paragraph("报价位置已知，但现货、杠杆与宏观的领先关系仍缺少足够证据。")],
        "rejected_competing_explanations": [
            _paragraph(rejected_text.get(identity, "该资金来源解释仍缺少独立的领先证据，暂不作为主要解释。"), identity)
            for identity in bundle["causal_attribution"]["rejected_hypothesis_ids"]
        ],
        "portfolio_action_by_module": [
            _paragraph("保持当前组合角色，本轮建议新增占用 {{amount_u}}U；等待能够鉴别驱动的证据。", module)
            for module in contract.PORTFOLIO_MODULES
        ],
        "material_uncertainties": [_paragraph("现货资金来源、持仓量时序及跨所留存仍未充分建立。")],
        "next_decision_changing_evidence": [_paragraph("下一步比较官方公布后的宏观资产、原生现货与价格反应时序。")],
        "user_visible_major_risk": [_paragraph("本轮资料未建立新的重大风险结论，实时账户安全参数仍未知。")],
        "decision_relevant_levels_and_sizes": [
            _paragraph("当前仍有解释意义的价值带为 {{lower_u}} 至 {{upper_u}}U；它是观察锚，不是成交。", "V0")
        ],
    }
    if bundle["causal_attribution"]["driver_regime"] == "CRYPTO_NATIVE_SPOT":
        claim = bundle["evidence_matrix"]["spot_flow"]["evidence_claims"][0]
        direction = "买入" if claim["direction"] == "BUY" else "卖出"
        result["primary_causal_explanation"] = [{
            "text": f"原生现货{direction}先于价格响应，现有跨所时序支持该方向的资金驱动解释。",
            "verification": "EVIDENCE_LINKED",
            "evidence_refs": [{"dimension": "spot_flow", "ref_id": claim["claim_id"]}],
        }]
    for index, module in enumerate(contract.PORTFOLIO_MODULES):
        if bundle["portfolio_decision"][module]["decision"] == "CONDITIONAL_ADD":
            direction = "现货增量" if module == "new_spot" else "保护性空头名义敞口"
            result["portfolio_action_by_module"][index] = _paragraph(
                f"将{direction}纳入条件评估，建议规模 {{{{amount_u}}}}U；只有既定形成条件成立才继续复核，不代表下单。", module,
            )
    for candidate in bundle["trade_candidates"]:
        for entry in candidate["entries"]:
            if entry["eligibility"] == "ELIGIBLE_FOR_REVIEW":
                direction = "折价现货" if candidate["kind"] == "DISCOUNT_SPOT" else "溢价保护性空头"
                result["decision_relevant_levels_and_sizes"].append(_paragraph(
                    f"{direction}候选价位 {{{{price_u}}}}U，对应评估规模 {{{{amount_u}}}}U；形成条件与失效条件仍需持续核验。", entry["entry_id"],
                ))
    return result


def _report(bundle: dict) -> None:
    visible = {"V0"} | {
        entry["entry_id"] for candidate in bundle["trade_candidates"]
        for entry in candidate["entries"] if entry["eligibility"] == "ELIGIBLE_FOR_REVIEW"
    }
    bundle["report_semantics"] = report.build_report_projection(
        bundle, narratives(bundle), visible_level_ids=visible,
    )


def _finalize(bundle: dict) -> dict:
    if bundle.get("write_transition") == "MIGRATION":
        bundle["previous_fingerprints"]["report_fingerprint"] = contract.previous_report_fingerprint_from_revision2(bundle)
    bundle["fingerprints"] = contract.compute_fingerprints(bundle)
    bundle["control"] = contract._derive_control_unchecked(bundle)
    return bundle


def migration_bundle() -> tuple[dict, contract.MigrationBaseline]:
    value = _shift_evidence(legacy.make_bundle(include_baseline=False))
    value["runtime"].update(
        run_id="BTC-CYCLE-20300301-2100", state_id="BTC-STATE-20300301-2100",
        plan_id="BTC-20300301-2100", pricing_as_of="2030-03-01 21:00 JST",
        trigger_type="IMPLEMENTATION_MIGRATION",
    )
    value["event"] = events.first_cycle()
    value["event"]["cycle"]["cycle_id"] = value["runtime"]["run_id"]
    value["event"]["trigger"]["kind"] = "MIGRATION"
    for item in value["event"]["cycle"]["events"]:
        item["hypothesis_refs"] = ["H_SPOT", "H_MACRO"]
        item["falsification_outcomes"][0]["hypothesis_id"] = "H_SPOT"
    value["write_transition"] = "MIGRATION"
    value["previous_strategy_revision"] = 2
    legacy.bind_revision2_artifacts(value)
    _report(value)
    return _finalize(value), contract.load_immutable_migration_baseline()


def verified_spot_migration() -> tuple[dict, contract.MigrationBaseline]:
    value, migration = migration_bundle()
    legacy.verify_dimensions(value, "spot_flow", "cross_exchange_acceptance", "capital_source", "oi_timeline")
    legacy.select_driver(value, "CRYPTO_NATIVE_SPOT")
    value["decision_evidence_status"] = "RESOLVED"
    value["projection_mode"] = "CURRENT_DERIVATION"
    value = _shift_evidence(value)
    _report(value)
    return _finalize(value), migration


def candidate_migration(module: str, amount: str) -> tuple[dict, contract.MigrationBaseline]:
    value, migration = migration_bundle()
    candidate_index = 1 if module == "new_spot" else 0
    # Do not use the legacy report helper: only reuse its causal qualification.
    legacy.set_candidate_eligible(value, candidate_index, report_relevant=False)
    candidate = value["trade_candidates"][candidate_index]
    candidate["report_relevant"] = True
    candidate["entries"][0]["amount_u"] = amount
    proposal = value["portfolio_decision"][module]
    proposal.update(
        decision="CONDITIONAL_ADD", amount_u=amount,
        reason_class=candidate["entries"][0]["formation_conditions"][0],
        net_exposure_impact="INCREASE_NET_LONG_CONDITIONALLY" if module == "new_spot" else "REDUCE_NET_LONG_CONDITIONALLY",
        capital_pool_impact="RESERVE_SHARED_CAPITAL_IF_ACTIVATED" if module == "new_spot" else "ALPHA_NOTIONAL_ALLOCATION_IF_ACTIVATED",
    )
    proposal.pop("zero_amount_reason_class", None)
    value["projection_mode"] = "CURRENT_DERIVATION"
    value = _shift_evidence(value)
    _report(value)
    return _finalize(value), migration


@contextmanager
def rendered_baseline(bundle: dict):
    state = contract.render_projection_document(bundle, "STATE")
    plan = contract.render_projection_document(bundle, "PLAN")
    with tempfile.TemporaryDirectory(prefix="btc-production-contract-") as directory:
        state_path = Path(directory) / "state.md"
        plan_path = Path(directory) / "plan.md"
        state_path.write_text(state, encoding="utf-8")
        plan_path.write_text(plan, encoding="utf-8")
        baseline = contract.load_revision3_baseline(
            str(state_path), str(plan_path), require_owner_paths=False,
        )
        yield baseline, state, plan


def daily_bundle(prior: dict, baseline, at: str, *, final: bool = False) -> dict:
    value = deepcopy(prior)
    for key in tuple(value):
        if key.startswith("previous_") or key == "write_transition":
            value.pop(key)
    value["previous_projection_baseline"] = deepcopy(baseline.binding)
    value["previous_fingerprints"] = {
        name: baseline.binding[name] for name in ("decision_fingerprint", "report_fingerprint")
    }
    instant = datetime.fromisoformat(at)
    value["runtime"].update(
        pricing_as_of=instant.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST"),
        trigger_type="SCHEDULED_STRATEGY_BOUNDARY",
    )
    value["observation"]["observed_at"] = at
    value["event"] = events.next_cycle(prior["event"]) if final else events.checked_cycle(prior["event"])
    if final:
        value["runtime"].update(
            run_id="BTC-CYCLE-20300302-0005", state_id="BTC-STATE-20300302-0005", plan_id="BTC-20300302-0005",
        )
        value["event"]["cycle"]["cycle_id"] = value["runtime"]["run_id"]
        for item in value["event"]["cycle"]["events"]:
            item["hypothesis_refs"] = ["H_SPOT", "H_MACRO"]
            item["falsification_outcomes"][0]["hypothesis_id"] = "H_SPOT"
    value["observation"]["final_expiry_status"] = "COMPLETED" if final else "PENDING"
    value["observation"]["event_discriminator_status"] = "FINAL_EVENT_REACHED" if final else "NO_DISCRIMINATING_EVENT"
    _report(value)
    return _finalize(value)


def unavailable_pair(prior: dict, baseline, at: str, *, completed=False):
    observation = legacy.unavailable_observation_from(prior)
    observation["evaluation_as_of"] = at
    observation["observation"]["observed_at"] = at
    observation["observation"]["final_expiry_status"] = "COMPLETED" if completed else "PENDING"
    instant = datetime.fromisoformat(at)
    quote_at = (instant - timedelta(minutes=1)).isoformat()
    retrieved_at = (instant - timedelta(seconds=30)).isoformat()
    quote = observation["price_quote_evidence"]
    quote["time_range"] = {"kind": "POINT_IN_TIME", "start": quote_at, "end": quote_at}
    for source in quote["sources"]:
        source.update(as_of=quote_at, retrieved_at=retrieved_at)
    receipt = legacy.unavailable_receipt_for(prior, observation)
    for key in ("decision_fingerprint", "report_fingerprint"):
        receipt["reused_" + key] = baseline.binding[key]
    return receipt, observation


class ProductionEventIntegrationTests(unittest.TestCase):
    def validate(self, bundle, *, migration=None, baseline=None):
        return contract.validate_bundle(
            bundle, migration_baseline=migration, previous_baseline=baseline,
            fact_owner_snapshot=contract.load_fact_owner_snapshot(), require_owner_baseline=False,
        )

    def test_production_migration_renders_loadable_event_baseline(self):
        with frozen_clock(events.INITIAL):
            bundle, migration = migration_bundle()
            self.assertEqual(contract._production_contract_errors(bundle), [])
            self.assertEqual(self.validate(bundle, migration=migration), [])
            with rendered_baseline(bundle) as (baseline, state, plan):
                self.assertEqual(contract.extract_projection_sidecar(state)["canonical_semantics"]["event"], bundle["event"])
                self.assertEqual(contract.extract_projection_sidecar(plan)["canonical_semantics"]["event"], bundle["event"])
                self.assertEqual(baseline.binding["cycle_id"], bundle["event"]["cycle"]["cycle_id"])

    def test_next_a_after_migration_has_no_patch_or_report(self):
        with frozen_clock(events.INITIAL):
            previous, _ = migration_bundle()
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            bundle = daily_bundle(previous, baseline, events.BEFORE_FINAL)
            self.assertEqual(self.validate(bundle, baseline=baseline), [])
            self.assertEqual(bundle["control"]["monitor_state"], "A")
            self.assertFalse(bundle["control"]["project_patch_required"])
            self.assertFalse(bundle["control"]["report_required"])

    def test_final_event_e_rolls_over_without_forcing_report(self):
        with frozen_clock(events.INITIAL):
            previous, _ = migration_bundle()
        with frozen_clock(events.AFTER_FINAL), rendered_baseline(previous) as (baseline, _, _):
            bundle = daily_bundle(previous, baseline, events.AFTER_FINAL, final=True)
            self.assertEqual(self.validate(bundle, baseline=baseline), [])
            self.assertEqual(bundle["control"]["monitor_state"], "E")
            self.assertTrue(bundle["control"]["project_patch_required"])
            self.assertFalse(bundle["control"]["report_required"])
            with rendered_baseline(bundle) as (next_baseline, state, plan):
                followup = daily_bundle(bundle, next_baseline, events.AFTER_FINAL)
                self.assertEqual(self.validate(followup, baseline=next_baseline), [])
                self.assertEqual(followup["control"]["monitor_state"], "A")
                self.assertFalse(followup["control"]["project_patch_required"])
                self.assertFalse(followup["control"]["report_required"])
                self.assertEqual(contract.validate_projection_texts(state, plan, bundle, baseline, contract.load_fact_owner_snapshot()), [])

    def test_production_cannot_repeat_e_against_new_persisted_cycle(self):
        with frozen_clock(events.INITIAL):
            previous, _ = migration_bundle()
        with frozen_clock(events.AFTER_FINAL), rendered_baseline(previous) as (baseline, _, _):
            bundle = daily_bundle(previous, baseline, events.AFTER_FINAL, final=True)
            with rendered_baseline(bundle) as (next_baseline, _, _):
                repeated = deepcopy(bundle)
                repeated["previous_projection_baseline"] = deepcopy(next_baseline.binding)
                repeated["previous_fingerprints"] = {key: next_baseline.binding[key] for key in ("decision_fingerprint", "report_fingerprint")}
                _finalize(repeated)
                errors = self.validate(repeated, baseline=next_baseline)
                self.assertTrue(any("event" in error for error in errors))

    def test_semantic_d_preserves_event_cycle_then_next_run_returns_to_a(self):
        with frozen_clock(events.INITIAL):
            previous, _ = migration_bundle()
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            value = daily_bundle(previous, baseline, events.BEFORE_FINAL)
            value["runtime"].update(state_id="BTC-STATE-20300301-2330", plan_id="BTC-20300301-2330")
            value["portfolio_decision"]["existing_alpha_short"]["decision"] = "REDUCE_REVIEW"
            reviewed_narratives = narratives(value)
            reviewed_narratives["overall_user_decision"] = [_paragraph("将保护仓位转入减仓复核，其余组合角色继续保留；复核不代表执行。")]
            reviewed_narratives["portfolio_action_by_module"][1] = _paragraph(
                "复核是否降低既有保护敞口；本轮新增占用 {{amount_u}}U，尚未执行仓位调整。", "existing_alpha_short",
            )
            value["report_semantics"] = report.build_report_projection(value, reviewed_narratives, visible_level_ids={"V0"})
            _finalize(value)
            self.assertEqual(self.validate(value, baseline=baseline), [])
            self.assertEqual(value["control"]["monitor_state"], "D")
            self.assertTrue(value["control"]["report_required"])
            self.assertEqual(value["event"]["cycle"], previous["event"]["cycle"])
            with rendered_baseline(value) as (next_baseline, _, _):
                followup = daily_bundle(value, next_baseline, events.BEFORE_FINAL)
                # Narrative wording is not event or decision state. Preserve
                # the reviewed prose while testing the subsequent A branch.
                followup["report_semantics"] = deepcopy(value["report_semantics"])
                _finalize(followup)
                self.assertEqual(self.validate(followup, baseline=next_baseline), [])
                self.assertEqual(followup["control"]["monitor_state"], "A")
                self.assertFalse(followup["control"]["project_patch_required"])
                self.assertFalse(followup["control"]["report_required"])

    def test_due_final_cannot_be_hidden_as_a(self):
        with frozen_clock(events.INITIAL):
            previous, _ = migration_bundle()
        with frozen_clock(events.AFTER_FINAL), rendered_baseline(previous) as (baseline, _, _):
            value = daily_bundle(previous, baseline, events.AFTER_FINAL)
            self.assertTrue(any("deadline" in error for error in self.validate(value, baseline=baseline)))

    def test_event_cycle_id_must_match_projection_runtime(self):
        with frozen_clock(events.INITIAL):
            value, migration = migration_bundle()
            value["event"]["cycle"]["cycle_id"] = "UNRELATED_CYCLE"
            _finalize(value)
            self.assertTrue(any("cycle_id" in error for error in self.validate(value, migration=migration)))

    def test_production_cli_rejects_legacy_profile_before_output(self):
        # Only the path loader is substituted: argparse, production-profile
        # gate, JSON input and hard-failure result all run through main().
        with frozen_clock(events.INITIAL):
            value, migration = migration_bundle()
            value["event"] = {"final_event_reached": False}
            value["report_semantics"] = legacy.make_bundle(False)["report_semantics"]
            _finalize(value)
            with tempfile.TemporaryDirectory(prefix="btc-cli-profile-") as directory:
                input_path = Path(directory) / "input.json"
                input_path.write_text(json.dumps(value), encoding="utf-8")
                stdout = io.StringIO()
                with mock.patch.object(contract, "load_migration_baseline", return_value=migration), redirect_stdout(stdout):
                    code = contract.main([
                        "validate-bundle", "--input", str(input_path),
                        "--previous-state", str(migration.state_path), "--previous-plan", str(migration.plan_path),
                        "--previous-report", str(migration.report_path), "--previous-report-projection", str(migration.report_projection_path),
                    ])
                result = json.loads(stdout.getvalue())
                self.assertEqual(code, 2)
                self.assertTrue(result["hard_failure"])
                self.assertTrue(any("boolean-only" in error for error in result["errors"]))
                self.assertTrue(any("fixed report codes" in error for error in result["errors"]))

    def test_generic_native_flow_buy_to_sell_changes_decision_and_report_without_price_or_action_change(self):
        with frozen_clock(events.INITIAL):
            previous, migration = verified_spot_migration()
            self.assertEqual(self.validate(previous, migration=migration), [])
            old_body = report.render_report(previous["report_semantics"], previous)["body"]
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            current = daily_bundle(previous, baseline, events.BEFORE_FINAL)
            claim = current["evidence_matrix"]["spot_flow"]["evidence_claims"][0]
            self.assertEqual(claim["claim_type"], "NATIVE_SPOT_FLOW")
            claim["direction"] = "SELL"
            _report(current)
            _finalize(current)
            self.assertEqual(self.validate(current, baseline=baseline), [])
            self.assertEqual(current["observation"]["raw_prices"], previous["observation"]["raw_prices"])
            self.assertEqual(current["value_zones"], previous["value_zones"])
            self.assertEqual(current["anchor_map_semantics"], previous["anchor_map_semantics"])
            self.assertEqual(current["portfolio_decision"], previous["portfolio_decision"])
            self.assertEqual(current["account_facts"], previous["account_facts"])
            self.assertEqual(current["control"]["monitor_state"], "D")
            self.assertTrue(current["control"]["report_required"])
            self.assertNotEqual(current["fingerprints"]["decision_fingerprint"], previous["fingerprints"]["decision_fingerprint"])
            self.assertNotEqual(current["fingerprints"]["report_fingerprint"], previous["fingerprints"]["report_fingerprint"])
            envelope = report.render_report(current["report_semantics"], current)
            self.assertTrue(envelope["ok"])
            self.assertIn("原生现货买入先于价格响应", old_body)
            self.assertIn("原生现货卖出先于价格响应", envelope["body"])
            self.assertNotIn("原生现货买入先于价格响应", envelope["body"])

    def test_equivalent_claim_alias_rewording_and_retrieval_time_do_not_create_d_or_report(self):
        with frozen_clock(events.INITIAL):
            previous, migration = verified_spot_migration()
            self.assertEqual(self.validate(previous, migration=migration), [])
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            for alias in ("NATIVE_SPOT_BUY", "NATIVE_SPOT_BUY_FLOW"):
                with self.subTest(alias=alias):
                    current = daily_bundle(previous, baseline, events.BEFORE_FINAL)
                    current["evidence_matrix"]["spot_flow"]["evidence_claims"][0]["claim_type"] = alias
                    for item in current["evidence_matrix"].values():
                        for source in item["sources"]:
                            source["retrieved_at"] = "2030-03-01T14:29:00+00:00"
                    current["causal_attribution"]["reasoning_summary"] = "资金与价格的领先证据仍一致；此次仅用另一种表述复述。"
                    revised = narratives(current)
                    revised["primary_causal_explanation"][0]["text"] = "跨所证据继续显示，原生现货买方流先出现，随后价格作出响应。"
                    current["report_semantics"] = report.build_report_projection(current, revised, visible_level_ids={"V0"})
                    _finalize(current)
                    self.assertEqual(self.validate(current, baseline=baseline), [])
                    self.assertEqual(current["fingerprints"]["decision_fingerprint"], previous["fingerprints"]["decision_fingerprint"])
                    self.assertEqual(current["fingerprints"]["report_fingerprint"], previous["fingerprints"]["report_fingerprint"])
                    self.assertEqual(current["control"]["monitor_state"], "A")
                    self.assertFalse(current["control"]["project_patch_required"])
                    self.assertFalse(current["control"]["report_required"])

    def test_nonfixed_conditional_amounts_bind_same_direction_candidate_and_render_actual_amount(self):
        for module, amount, kind in (
            ("new_spot", "750", "DISCOUNT_SPOT"),
            ("new_short", "250", "PREMIUM_SHORT"),
        ):
            with self.subTest(module=module), frozen_clock(events.INITIAL):
                value, migration = candidate_migration(module, amount)
                self.assertEqual(self.validate(value, migration=migration), [])
                candidate = next(c for c in value["trade_candidates"] if c["kind"] == kind)
                self.assertEqual(candidate["eligibility"], "ELIGIBLE_FOR_REVIEW")
                self.assertEqual(candidate["entries"][0]["amount_u"], amount)
                self.assertEqual(value["portfolio_decision"][module]["amount_u"], amount)
                envelope = report.render_report(value["report_semantics"], value)
                self.assertTrue(envelope["ok"])
                self.assertIn(amount + "U", envelope["body"])
                self.assertNotIn("600U", envelope["body"])
                self.assertEqual(report.validate_report_text(envelope["body"], envelope["validation_projection"], value), [])

    def test_nonfixed_conditional_amount_and_direction_mismatches_are_rejected(self):
        for module, amount in (("new_spot", "750"), ("new_short", "250")):
            with self.subTest(module=module), frozen_clock(events.INITIAL):
                value, migration = candidate_migration(module, amount)
                value["portfolio_decision"][module]["amount_u"] = str(int(amount) + 1)
                _report(value)
                _finalize(value)
                self.assertTrue(any("amount must equal same-direction" in error for error in self.validate(value, migration=migration)))
                value, migration = candidate_migration(module, amount)
                other = "new_short" if module == "new_spot" else "new_spot"
                role = value["portfolio_decision"][other]["portfolio_role"]
                value["portfolio_decision"][other] = deepcopy(value["portfolio_decision"][module])
                value["portfolio_decision"][other]["portfolio_role"] = role
                value["portfolio_decision"][module] = legacy.portfolio_item("INCREMENTAL_SPOT" if module == "new_spot" else "INCREMENTAL_HEDGE")
                _report(value)
                _finalize(value)
                self.assertTrue(any("same-direction eligible" in error for error in self.validate(value, migration=migration)))

    def test_unavailable_fallback_cannot_hide_actual_or_reported_final_deadline(self):
        with frozen_clock(events.INITIAL):
            previous, _ = migration_bundle()
        cases = (
            (events.BEFORE_FINAL, events.BEFORE_FINAL, False, None),
            ("2030-03-01T15:00:00+00:00", "2030-03-01T15:00:00+00:00", False, "actual baseline final deadline"),
            (events.AFTER_FINAL, events.BEFORE_FINAL, False, "actual baseline final deadline"),
            (events.BEFORE_FINAL, events.BEFORE_FINAL, True, "completed final event"),
            ("2030-03-01T14:59:30+00:00", "2030-03-01T15:00:00+00:00", False, "actual baseline final deadline"),
        )
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            for now, evaluated, completed, expected in cases:
                with self.subTest(now=now, evaluated=evaluated, completed=completed), frozen_clock(now):
                    receipt, observation = unavailable_pair(previous, baseline, evaluated, completed=completed)
                    errors = contract.validate_unavailable_receipt(
                        receipt, baseline, observation, contract.load_fact_owner_snapshot(),
                        require_owner_baseline=False,
                    )
                    if expected is None:
                        self.assertEqual(errors, [])
                    else:
                        self.assertTrue(any(expected in error for error in errors), errors)

    def test_same_driver_distinct_hypotheses_can_compete_and_selection_changes_semantics(self):
        with frozen_clock(events.INITIAL):
            previous, migration = verified_spot_migration()
            causal = previous["causal_attribution"]
            selected = next(item for item in causal["hypotheses"] if item["hypothesis_id"] == "H_SPOT")
            selected.update(hypothesis_id="H_NATIVE_ETF", thesis="ETF 相关原生现货买入领先于价格，企业财库资金仍是竞争解释。")
            competitor = deepcopy(selected)
            competitor.update(hypothesis_id="H_NATIVE_TREASURY", thesis="企业财库原生现货买入可能解释资金持续性，ETF 申赎时序可加以鉴别。")
            causal["hypotheses"].append(competitor)
            causal["selected_hypothesis_id"] = "H_NATIVE_ETF"
            causal["rejected_hypothesis_ids"] = [item["hypothesis_id"] for item in causal["hypotheses"] if item is not selected]
            for event in previous["event"]["cycle"]["events"]:
                event["hypothesis_refs"] = ["H_NATIVE_ETF", "H_NATIVE_TREASURY", "H_MACRO"]
                event["falsification_outcomes"][0]["hypothesis_id"] = "H_NATIVE_ETF"
                treasury_outcome = deepcopy(event["falsification_outcomes"][0])
                treasury_outcome["hypothesis_id"] = "H_NATIVE_TREASURY"
                treasury_outcome["observable_outcome"] = "企业财库买入公开时间晚于价格响应，ETF 申赎资金则先行"
                event["falsification_outcomes"].append(treasury_outcome)
            initial_narrative = narratives(previous)
            initial_narrative["primary_causal_explanation"][0]["text"] = "当前优先考虑 ETF 相关现货买入解释；企业财库资金仍须独立鉴别。"
            previous["report_semantics"] = report.build_report_projection(previous, initial_narrative, visible_level_ids={"V0"})
            _finalize(previous)
            self.assertEqual(self.validate(previous, migration=migration), [])
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            current = daily_bundle(previous, baseline, events.BEFORE_FINAL)
            causal = current["causal_attribution"]
            causal["selected_hypothesis_id"] = "H_NATIVE_TREASURY"
            causal["rejected_hypothesis_ids"] = [item["hypothesis_id"] for item in causal["hypotheses"] if item["hypothesis_id"] != "H_NATIVE_TREASURY"]
            revised = narratives(current)
            revised["primary_causal_explanation"][0]["text"] = "当前优先考虑企业财库现货买入解释；ETF 相关资金尚不足以解释持续性。"
            current["report_semantics"] = report.build_report_projection(current, revised, visible_level_ids={"V0"})
            _finalize(current)
            self.assertEqual(self.validate(current, baseline=baseline), [])
            self.assertEqual(current["causal_attribution"]["driver_regime"], previous["causal_attribution"]["driver_regime"])
            self.assertEqual(current["portfolio_decision"], previous["portfolio_decision"])
            self.assertEqual(current["observation"]["raw_prices"], previous["observation"]["raw_prices"])
            self.assertEqual(current["control"]["monitor_state"], "D")
            self.assertTrue(current["control"]["report_required"])
            for key in ("decision_fingerprint", "report_fingerprint"):
                self.assertNotEqual(current["fingerprints"][key], previous["fingerprints"][key])

    def test_generic_buy_alias_preserves_eligible_candidate_and_silent_a(self):
        with frozen_clock(events.INITIAL):
            previous, migration = candidate_migration("new_spot", "750")
            self.assertEqual(self.validate(previous, migration=migration), [])
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            current = daily_bundle(previous, baseline, events.BEFORE_FINAL)
            claim = current["evidence_matrix"]["spot_flow"]["evidence_claims"][0]
            self.assertEqual(claim["direction"], "BUY")
            claim["claim_type"] = "NATIVE_SPOT_FLOW"
            _report(current)
            _finalize(current)
            self.assertEqual(self.validate(current, baseline=baseline), [])
            for key in ("decision_fingerprint", "report_fingerprint"):
                self.assertEqual(current["fingerprints"][key], previous["fingerprints"][key])
            self.assertEqual(current["control"]["monitor_state"], "A")
            self.assertFalse(current["control"]["project_patch_required"])
            self.assertFalse(current["control"]["report_required"])

    def test_sell_aliases_preserve_causal_meaning_and_silent_a(self):
        with frozen_clock(events.INITIAL):
            previous, migration = verified_spot_migration()
            previous["evidence_matrix"]["spot_flow"]["evidence_claims"][0]["direction"] = "SELL"
            _report(previous)
            _finalize(previous)
            self.assertEqual(self.validate(previous, migration=migration), [])
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            for alias in ("NATIVE_SPOT_SELL", "NATIVE_SPOT_SELL_FLOW"):
                with self.subTest(alias=alias):
                    current = daily_bundle(previous, baseline, events.BEFORE_FINAL)
                    current["evidence_matrix"]["spot_flow"]["evidence_claims"][0]["claim_type"] = alias
                    _report(current)
                    _finalize(current)
                    self.assertEqual(self.validate(current, baseline=baseline), [])
                    for key in ("decision_fingerprint", "report_fingerprint"):
                        self.assertEqual(current["fingerprints"][key], previous["fingerprints"][key])
                    self.assertEqual(current["control"]["monitor_state"], "A")
                    self.assertFalse(current["control"]["project_patch_required"])
                    self.assertFalse(current["control"]["report_required"])

    def test_portfolio_purpose_and_conditions_change_decision_without_amount_change(self):
        with frozen_clock(events.INITIAL):
            previous, migration = migration_bundle()
            self.assertEqual(self.validate(previous, migration=migration), [])
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            for key, updated, text in (
                ("portfolio_role", "LONG_TERM_RISK_BUFFER", "将现有核心现货定位为长期风险缓冲，本轮新增占用 {{amount_u}}U。"),
                ("formation_conditions", ["SPOT_RETENTION_AFTER_RETEST"], "保持现有核心现货，改以回测后的现货留存作为复核条件；本轮新增占用 {{amount_u}}U。"),
                ("invalidation_conditions", ["SPOT_RETENTION_FAILURE"], "保持现有核心现货，现货留存失败时重新复核；本轮新增占用 {{amount_u}}U。"),
            ):
                with self.subTest(field=key):
                    current = daily_bundle(previous, baseline, events.BEFORE_FINAL)
                    module = contract.PORTFOLIO_MODULES[0]
                    current["portfolio_decision"][module][key] = updated
                    revised = narratives(current)
                    revised["portfolio_action_by_module"][0] = _paragraph(text, module)
                    current["report_semantics"] = report.build_report_projection(current, revised, visible_level_ids={"V0"})
                    _finalize(current)
                    self.assertEqual(self.validate(current, baseline=baseline), [])
                    self.assertEqual(current["portfolio_decision"][module]["amount_u"], previous["portfolio_decision"][module]["amount_u"])
                    self.assertEqual(current["control"]["monitor_state"], "D")
                    self.assertTrue(current["control"]["report_required"])
                    for fingerprint in ("decision_fingerprint", "report_fingerprint"):
                        self.assertNotEqual(current["fingerprints"][fingerprint], previous["fingerprints"][fingerprint])

    def test_local_fact_explanation_rewording_preserves_silent_a(self):
        with frozen_clock(events.INITIAL):
            previous, _ = migration_bundle()
        with frozen_clock(events.BEFORE_FINAL), rendered_baseline(previous) as (baseline, _, _):
            current = daily_bundle(previous, baseline, events.BEFORE_FINAL)
            current["causal_attribution"]["reasoning_summary"] = "组合事实继续以本地已确认记录为依据，资料不足以建立新的因果顺序。"
            revised = narratives(current)
            revised["overall_user_decision"] = [_paragraph("依照本地已确认记录继续保留组合角色；若实际仓位已变化请同步，未变化无需操作。")]
            revised["portfolio_action_by_module"][0] = _paragraph("本地已确认的核心现货继续保留；新增占用 {{amount_u}}U，等待有鉴别力的公开证据。", contract.PORTFOLIO_MODULES[0])
            current["report_semantics"] = report.build_report_projection(current, revised, visible_level_ids={"V0"})
            _finalize(current)
            self.assertEqual(self.validate(current, baseline=baseline), [])
            self.assertEqual(current["account_facts"], previous["account_facts"])
            for key in ("decision_fingerprint", "report_fingerprint"):
                self.assertEqual(current["fingerprints"][key], previous["fingerprints"][key])
            self.assertEqual(current["control"]["monitor_state"], "A")
            self.assertFalse(current["control"]["project_patch_required"])
            self.assertFalse(current["control"]["report_required"])

    def test_direct_fixed_report_cli_requires_explicit_replay_only(self):
        old_projection = legacy.make_bundle(False)["report_semantics"]
        for replay in (False, True):
            output = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(old_projection))), redirect_stdout(output):
                arguments = ["render-report", "--projection", "-"]
                if replay:
                    arguments.append("--replay-only")
                code = contract.main(arguments)
            result = json.loads(output.getvalue())
            if replay:
                self.assertEqual(code, 0)
                self.assertTrue(result["ok"])
                self.assertTrue(result["body"])
            else:
                self.assertEqual(code, 2)
                self.assertTrue(result["hard_failure"])
                self.assertNotIn("body", result)
                self.assertTrue(any("replay-only" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
