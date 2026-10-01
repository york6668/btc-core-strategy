from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from decimal import InvalidOperation
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__import__('os').environ['BTC_TEST_FIXTURE_DIR']).resolve()
sys.path.insert(0, str(SCRIPTS))

import strategy_contract as contract  # noqa: E402


def time_range(kind: str = "POINT_IN_TIME") -> dict:
    if kind == "NOT_ESTABLISHED":
        return {"kind": kind}
    start = "2026-09-02T04:40:00+09:00" if kind == "POINT_IN_TIME" else "2026-09-02T03:40:00+09:00"
    end = start if kind == "POINT_IN_TIME" else "2026-09-02T04:40:00+09:00"
    return {"kind": kind, "start": start, "end": end}


def metric(metric_id: str = "PUBLIC_MEASURED_SEQUENCE", periods: int = 2) -> dict:
    return {
        "metric_id": metric_id,
        "unit": "NORMALIZED_INDEX",
        "aggregation": "WINDOW_SEQUENCE",
        "periods": periods,
    }


def evidence(
    status: str, source: str | None = None, dimension: str = "spot_flow"
) -> dict:
    verified = status == "VERIFIED"
    source_id = source or "PUBLIC_SOURCE"
    if dimension == "account_impact":
        sources = [{
            "source_id": source_id,
            "payload_kind": contract.EVIDENCE_PAYLOAD_KINDS[dimension],
            "access_scope": "LOCAL_CONFIRMED_RECORD",
            "as_of": "2026-09-02T04:40:00+09:00",
            "retrieved_at": "2026-09-02T04:40:30+09:00",
        }] if verified else []
    else:
        sources = [{
            "source_id": source_id,
            "endpoint_url": "https://data.example.com/" + source_id.lower(),
            "payload_kind": contract.EVIDENCE_PAYLOAD_KINDS[dimension],
            "canonical_origin": "data.example.com",
            "venue": "venue_a" if dimension == "cross_exchange_acceptance" else "NOT_APPLICABLE",
            "access_scope": "PUBLIC_UNAUTHENTICATED",
            "as_of": "2026-09-02T04:40:00+09:00",
            "retrieved_at": "2026-09-02T04:40:30+09:00",
        }] if verified else []
    return {
        "status": status,
        "sources": sources,
        "time_range": time_range("POINT_IN_TIME" if verified else "NOT_ESTABLISHED"),
        "supporting": ["OBSERVED"] if verified else [],
        "conflicts": [],
        "missing": [] if verified else ["DECISIONAL_SEQUENCE_NOT_OBSERVED"],
    }


def hypothesis(hypothesis_id: str, thesis: str) -> dict:
    return {
        "hypothesis_id": hypothesis_id,
        "thesis": thesis,
        "supporting_evidence": ["QUOTE_RELATION_ONLY"],
        "counter_evidence": ["NO_STRUCTURAL_CONFIRMATION"],
        "causal_timeline": [{
            "sequence": 1,
            "occurred_at": "2026-09-02T04:40:00+09:00",
            "evidence_dimension": "price_quotes",
            "evidence_ref": "BINANCE_SPOT_PUBLIC",
            "event_role": "UNRESOLVED_PRICE_OBSERVATION",
        }],
        "missing_evidence": ["SPOT_FLOW_AND_OI_SEQUENCE"],
        "falsification_conditions": ["OPPOSING_DRIVER_SEQUENCE_VERIFIED"],
        "next_discriminating_evidence": ["CROSS_VENUE_SPOT_AND_OI_TIMELINE"],
    }


def portfolio_item(role: str, decision: str = "MAINTAIN") -> dict:
    return {
        "local_fact_basis": "LOCAL_CONFIRMED_RECORDS",
        "portfolio_role": role,
        "decision": decision,
        "amount_u": "0",
        "reason_class": "WAIT_FOR_DECISIONAL_EVIDENCE",
        "zero_amount_reason_class": "OPTIONALITY_DOMINATES_IMMEDIATE_CHANGE",
        "formation_conditions": ["DECISION_EVIDENCE_CHANGES"],
        "invalidation_conditions": ["ACCOUNT_SAFETY_CONFLICT"],
        "net_exposure_impact": "UNCHANGED",
        "capital_pool_impact": "NONE",
        "evidence_considered": ["LOCAL_FACTS", "EVIDENCE_MATRIX"],
    }


def amount_basis(budget: str) -> dict:
    return {
        "budget_source": budget,
        "net_exposure_constraint": "PORTFOLIO_REMAINS_NET_LONG",
        "value_gap_rationale": "STRUCTURAL_MISPRICING_ONLY",
        "risk_assumption": "NO_ACCOUNT_SAFETY_CONFLICT",
        "continuation_reason": "ANCHOR_AND_PORTFOLIO_CONSTRAINTS_UNCHANGED",
    }


def _apply_control(bundle: dict) -> dict:
    bundle["fingerprints"] = contract.compute_fingerprints(bundle)
    derived = contract._derive_control_unchecked(bundle)
    bundle["control"] = dict(derived)
    return bundle


def make_bundle(include_baseline: bool = True) -> dict:
    matrix = {
        dimension: evidence("UNRESOLVED", dimension=dimension)
        for dimension in contract.REQUIRED_EVIDENCE_DIMENSIONS
    }
    matrix["price_quotes"] = {
        "status": "VERIFIED",
        "sources": [
            {
                "source_id": "BINANCE_SPOT_PUBLIC",
                "endpoint_url": "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
                "payload_kind": "BTC_SPOT_TICKER",
                "canonical_origin": "BINANCE",
                "venue": "binance",
                "access_scope": "PUBLIC_UNAUTHENTICATED",
                "as_of": "2026-09-02T04:40:00+09:00",
                "retrieved_at": "2026-09-02T04:40:30+09:00",
            },
            {
                "source_id": "KRAKEN_SPOT_PUBLIC",
                "endpoint_url": "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
                "payload_kind": "BTC_SPOT_TICKER",
                "canonical_origin": "KRAKEN",
                "venue": "kraken",
                "access_scope": "PUBLIC_UNAUTHENTICATED",
                "as_of": "2026-09-02T04:40:00+09:00",
                "retrieved_at": "2026-09-02T04:40:30+09:00",
            },
            {
                "source_id": "COINBASE_SPOT_PUBLIC",
                "endpoint_url": "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
                "payload_kind": "BTC_SPOT_TICKER",
                "canonical_origin": "COINBASE",
                "venue": "coinbase",
                "access_scope": "PUBLIC_UNAUTHENTICATED",
                "as_of": "2026-09-02T04:40:00+09:00",
                "retrieved_at": "2026-09-02T04:40:30+09:00",
            },
        ],
        "independence_groups": ["BINANCE", "KRAKEN", "COINBASE"],
        "access_scope": "PUBLIC_UNAUTHENTICATED",
        "time_range": time_range(),
        "supporting": ["THREE_VENUE_QUOTE_RELATION"],
        "conflicts": [],
        "missing": [],
    }
    matrix["account_impact"] = evidence(
        "VERIFIED", "LOCAL_CONFIRMED_RECORDS", "account_impact"
    )
    bundle = {
        "schema": contract.SCHEMA_VERSION,
        "bundle_phase": "PRE_WRITE",
        "strategy_revision": 3,
        "observation": {
            "normalization_revision": "BTC_OBSERVATION_V3",
            "source_health_class": "PUBLIC_QUOTES_HEALTHY_DECISION_DATA_LIMITED",
            "quote_relation_by_venue": {
                "binance": "INSIDE_ZONE",
                "coinbase": "INSIDE_ZONE",
                "kraken": "INSIDE_ZONE",
            },
            "closed_bar_relation_sequence": "NO_CLOSED_BAR_EVIDENCE",
            "cross_venue_divergence_class": "NONE",
            "evidence_availability_by_dimension": {
                dimension: item["status"] for dimension, item in matrix.items()
            },
            "event_discriminator_status": "NO_DISCRIMINATING_EVENT",
            "final_expiry_status": "PENDING",
            "price_relation": "INSIDE_ZONE",
            "prior_price_relation": "INSIDE_ZONE",
            "relation_transition": "UNCHANGED",
            "raw_prices": {
                "binance": "77320.00",
                "coinbase": "77312.00",
                "kraken": "77318.00",
            },
            "observed_at": "2026-09-02T04:41:00+09:00",
        },
        "quote_verification_status": "VERIFIED",
        "decision_evidence_status": "EVIDENCE_LIMITED",
        "evidence_matrix": matrix,
        "causal_attribution": {
            "driver_regime": "UNRESOLVED",
            "driver_components": [],
            "selected_hypothesis_id": "H_UNRESOLVED",
            "rejected_hypothesis_ids": ["H_SPOT", "H_LEVERAGE", "H_MACRO"],
            "reasoning_summary": "报价关系已知，但因果时序不足，维持最近有效锚。",
            "hypotheses": [
                hypothesis("H_UNRESOLVED", "价格结果尚无充分因果解释"),
                hypothesis("H_SPOT", "原生现货形成结构接受"),
                hypothesis("H_LEVERAGE", "衍生品杠杆推动价格"),
                hypothesis("H_MACRO", "宏观风险偏好推动价格"),
            ],
        },
        "value_zones": [
            {
                "zone_id": "V0",
                "role": "PERSISTENT_VALUE_DISPUTE_ZONE",
                "band": {"lower_u": "77000", "upper_u": "77500"},
                "lifecycle": "RETEST_PENDING",
            },
            {
                "zone_id": "V1",
                "role": "UNRETESTED_VALUE_ZONE",
                "band": {"lower_u": "78300", "upper_u": "80000"},
                "lifecycle": "RETEST_PENDING",
            },
        ],
        "anchor_map_semantics": {
            "persistent_anchor_ids": ["V0"],
            "unretested_anchor_ids": ["V1"],
        },
        "path_decision": "MAINTAIN",
        "forward_value_band_semantics": {
            "primary_zone_ids": ["V0", "V1"],
            "migration_state": "NO_MIGRATION",
        },
        "portfolio_decision": {
            "existing_digital_beta": portfolio_item("CORE_UPSIDE_EXPOSURE"),
            "existing_alpha_short": portfolio_item("PROTECTIVE_ALPHA"),
            "new_spot": portfolio_item("INCREMENTAL_SPOT"),
            "new_short": portfolio_item("INCREMENTAL_HEDGE"),
            "optionality": portfolio_item("CASH_AND_DECISION_OPTIONALITY"),
        },
        "trade_candidates": [
            {
                "candidate_id": "PREMIUM_SHORT",
                "kind": "PREMIUM_SHORT",
                "eligibility": "CONDITIONAL_INACTIVE",
                "report_relevant": False,
                "entries": [
                    {
                        "entry_id": "S1",
                        "price_u": "81800",
                        "amount_u": "250",
                        "amount_basis": amount_basis("ALPHA_RISK_BUDGET"),
                        "value_role": "UNCONFIRMED_PREMIUM_EXTENSION",
                        "eligibility": "INACTIVE",
                        "formation_conditions": ["LEVERAGE_DOMINATES_WITHOUT_SPOT_VALUE"],
                        "evidence_confidence": "CONDITIONAL",
                        "account_precondition": "LOCAL_SAFETY_FACTS_ALLOW",
                        "invalidation_conditions": ["SPOT_VALUE_ACCEPTED_ABOVE_80000"],
                        "carried_forward": True,
                    }
                ],
            },
            {
                "candidate_id": "DISCOUNT_SPOT",
                "kind": "DISCOUNT_SPOT",
                "eligibility": "CONDITIONAL_INACTIVE",
                "report_relevant": False,
                "entries": [
                    {
                        "entry_id": "D1",
                        "price_u": "76200",
                        "amount_u": "600",
                        "amount_basis": amount_basis("SHARED_CAPITAL_POOL"),
                        "value_role": "DELEVERAGING_DISCOUNT",
                        "eligibility": "INACTIVE",
                        "formation_conditions": ["DELEVERAGING_WITH_SPOT_ABSORPTION"],
                        "evidence_confidence": "CONDITIONAL",
                        "account_precondition": "USER_CONFIRMED_CASH_AVAILABLE",
                        "invalidation_conditions": ["NATIVE_SPOT_SELLING_DOMINATES"],
                        "carried_forward": True,
                    }
                ],
            },
        ],
        "major_risk": {
            "active": False,
            "kinds": ["NONE"],
            "portfolio_effect": "NONE",
        },
        "account_facts": {
            "account_fact_changed_known": False,
            "fact_owner_fingerprints": dict(
                contract.load_fact_owner_snapshot().fingerprints
            ),
        },
        "user_input_requirement": "NONE",
        "projection_mode": "LAST_VALID_ANCHOR",
        "event": {"final_event_reached": False},
        "report_semantics": {
            "overall_user_decision": "NO_INCREMENTAL_POSITION_CHANGE",
            "primary_causal_explanation": "CAUSE_UNRESOLVED_KEEP_LAST_VALID_ANCHOR",
            "rejected_competing_explanations": [
                {"hypothesis_id": "H_SPOT", "reason_class": "NO_ACCEPTANCE_SEQUENCE"},
                {"hypothesis_id": "H_LEVERAGE", "reason_class": "NO_OI_SEQUENCE"},
                {"hypothesis_id": "H_MACRO", "reason_class": "NO_MACRO_TIMELINE"},
            ],
            "portfolio_action_by_module": {
                module: "MAINTAIN" for module in contract.PORTFOLIO_MODULES
            },
            "decision_relevant_levels_and_sizes": [
                {
                    "level_id": "V0",
                    "price_band_u": {"lower_u": "77000", "upper_u": "77500"},
                    "amount_u": "0",
                }
            ],
            "material_uncertainties": ["SPOT_FLOW", "OI_TIMELINE", "CAPITAL_SOURCE"],
            "next_decision_changing_evidence": [
                "CROSS_VENUE_ACCEPTANCE_WITH_CAUSAL_TIMELINE"
            ],
            "user_visible_major_risk": "NONE",
        },
        "runtime": {
            "run_id": "BTC-CYCLE-20260902-0441",
            "state_id": "BTC-STATE-OLD",
            "plan_id": "BTC-OLD",
            "pricing_as_of": "2026-09-02 04:41 JST",
            "trigger_type": "SCHEDULED_STRATEGY_BOUNDARY",
        },
    }
    initial = contract.compute_fingerprints(bundle)
    bundle["previous_fingerprints"] = {
        "decision_fingerprint": initial["decision_fingerprint"],
        "report_fingerprint": initial["report_fingerprint"],
    }
    _apply_control(bundle)
    if include_baseline:
        state, plan = default_revision3_baseline_documents()
        binding, errors = contract._derive_revision3_baseline_binding(state, plan)
        if errors or binding is None:
            raise AssertionError(errors)
        bundle["previous_projection_baseline"] = deepcopy(binding)
    return bundle


def default_revision3_baseline_documents() -> tuple[str, str]:
    baseline_bundle = make_bundle(include_baseline=False)
    baseline_bundle["observation"]["final_expiry_status"] = "COMPLETED"
    baseline_bundle["observation"][
        "event_discriminator_status"
    ] = "FINAL_EVENT_REACHED"
    baseline_bundle["event"]["final_event_reached"] = True
    _apply_control(baseline_bundle)
    state, plan, _ = ProjectionAndReportTests().projection_texts(baseline_bundle)
    return state, plan


@contextmanager
def revision3_baseline():
    state, plan = default_revision3_baseline_documents()
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / "策略状态.md"
        plan_path = Path(temp_dir) / "当前预期计划.md"
        state_path.write_text(state, encoding="utf-8")
        plan_path.write_text(plan, encoding="utf-8")
        yield contract.load_revision3_baseline(
            str(state_path), str(plan_path), require_owner_paths=False
        )


@contextmanager
def revision3_baseline_for_bundle(bundle: dict):
    state = contract.render_projection_document(bundle, "STATE")
    plan = contract.render_projection_document(bundle, "PLAN")
    binding, errors = contract._derive_revision3_baseline_binding(state, plan)
    if errors or binding is None:
        raise AssertionError(errors)
    semantics = contract.extract_projection_sidecar(state)["canonical_semantics"]
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / "策略状态.md"
        plan_path = Path(temp_dir) / "当前预期计划.md"
        state_path.write_text(state, encoding="utf-8")
        plan_path.write_text(plan, encoding="utf-8")
        baseline = contract.load_revision3_baseline(
            str(state_path), str(plan_path), require_owner_paths=False
        )
        yield baseline, semantics, binding


def validate_candidate(
    bundle: dict, fact_snapshot: contract.FactOwnerSnapshot | None = None
) -> list[str]:
    selected_snapshot = fact_snapshot or contract.load_fact_owner_snapshot()
    with revision3_baseline() as baseline:
        return contract.validate_bundle(
            bundle, previous_baseline=baseline,
            fact_owner_snapshot=selected_snapshot,
            require_owner_baseline=False,
            require_fact_owner_paths=fact_snapshot is None,
        )


@contextmanager
def changed_fact_owner_snapshot(owner_id: str):
    current = contract.load_fact_owner_snapshot()
    with tempfile.TemporaryDirectory() as temp_dir:
        paths = {}
        for current_owner_id, text in current.texts.items():
            path = Path(temp_dir) / f"{current_owner_id}.md"
            if current_owner_id == owner_id:
                text += "\n测试中的用户确认事实版本。\n"
            path.write_bytes(text.encode("utf-8"))
            paths[current_owner_id] = path
        yield contract.load_fact_owner_snapshot(paths, require_owner_paths=False)


def validate_projection_candidate(state: str, plan: str, bundle: dict) -> list[str]:
    with revision3_baseline() as baseline:
        return contract.validate_projection_texts(
            state, plan, bundle, baseline, contract.load_fact_owner_snapshot()
        )


def validate_receipt_candidate(receipt: dict, bundle: dict, *args, **kwargs) -> list[str]:
    with revision3_baseline() as baseline:
        return contract.validate_receipt(
            receipt,
            bundle,
            *args,
            previous_baseline=baseline,
            fact_owner_snapshot=contract.load_fact_owner_snapshot(),
            require_owner_readback=False,
            **kwargs,
        )


def verify_dimensions(bundle: dict, *dimensions: str) -> None:
    claim_type_by_dimension = {
        "spot_flow": "NATIVE_SPOT_FLOW",
        "cross_exchange_acceptance": "CROSS_PERIOD_ACCEPTANCE",
        "capital_source": "CAPITAL_SOURCE",
        "derivatives_flow": "DERIVATIVES_ACTIVE_FLOW",
        "oi_timeline": "OI_TIMELINE",
        "funding_basis": "LEVERAGE_COST_EXPANSION",
        "macro": "MACRO_EVENT_TIMELINE",
    }
    for dimension in dimensions:
        bundle["evidence_matrix"][dimension] = evidence(
            "VERIFIED", f"PUBLIC_{dimension.upper()}_SOURCE", dimension
        )
        bundle["evidence_matrix"][dimension]["time_range"] = time_range("MULTI_PERIOD")
        bundle["observation"]["evidence_availability_by_dimension"][
            dimension
        ] = "VERIFIED"
        if dimension == "cross_exchange_acceptance":
            second = deepcopy(bundle["evidence_matrix"][dimension]["sources"][0])
            second.update({
                "source_id": f"PUBLIC_{dimension.upper()}_SOURCE_B",
                "endpoint_url": "https://market.example.org/cross-venue-sequence",
                "canonical_origin": "market.example.org",
                "venue": "venue_b",
            })
            bundle["evidence_matrix"][dimension]["sources"].append(second)
        claim_type = claim_type_by_dimension.get(dimension)
        if claim_type:
            item = bundle["evidence_matrix"][dimension]
            item["evidence_claims"] = [{
                "claim_id": f"CLAIM_{dimension.upper()}",
                "claim_type": claim_type,
                "status": "VERIFIED",
                "source_ids": [source["source_id"] for source in item["sources"]],
                "time_range": item["time_range"],
                "metric": metric(),
                **({"direction": "BUY"} if dimension == "spot_flow" else {}),
            }]


def select_driver(bundle: dict, driver: str) -> None:
    selected_by_driver = {
        "UNRESOLVED": "H_UNRESOLVED",
        "CRYPTO_NATIVE_SPOT": "H_SPOT",
        "DERIVATIVES_LEVERAGE": "H_LEVERAGE",
        "MACRO_BETA": "H_MACRO",
    }
    primary_by_driver = {
        "UNRESOLVED": "CAUSE_UNRESOLVED_KEEP_LAST_VALID_ANCHOR",
        "CRYPTO_NATIVE_SPOT": "SPOT_LED_ACCEPTANCE",
        "DERIVATIVES_LEVERAGE": "LEVERAGE_OVERLAY_ONLY",
        "MACRO_BETA": "MACRO_OVERLAY_ONLY",
    }
    selected = selected_by_driver[driver]
    causal = bundle["causal_attribution"]
    causal["driver_regime"] = driver
    causal["selected_hypothesis_id"] = selected
    rejected = [
        item["hypothesis_id"]
        for item in causal["hypotheses"]
        if item["hypothesis_id"] != selected
    ]
    causal["rejected_hypothesis_ids"] = rejected
    if driver != "UNRESOLVED":
        selected_hypothesis = next(
            item for item in causal["hypotheses"]
            if item["hypothesis_id"] == selected
        )
        if driver == "CRYPTO_NATIVE_SPOT":
            spot_claim = next(
                claim["claim_id"]
                for claim in bundle["evidence_matrix"]["spot_flow"]["evidence_claims"]
                if claim["claim_type"] in {
                    "NATIVE_SPOT_FLOW", "NATIVE_SPOT_BUY_FLOW", "NATIVE_SPOT_BUY",
                }
            )
            acceptance_claim = next(
                claim["claim_id"]
                for claim in bundle["evidence_matrix"]["cross_exchange_acceptance"]["evidence_claims"]
                if claim["claim_type"] == "CROSS_PERIOD_ACCEPTANCE"
            )
            event_specs = [
                ("2026-09-02T04:37:00+09:00", "spot_flow", spot_claim, "CAUSE_OBSERVED"),
                ("2026-09-02T04:38:00+09:00", "capital_source", "CLAIM_CAPITAL_SOURCE", "CAUSE_OBSERVED"),
                ("2026-09-02T04:40:00+09:00", "price_quotes", "BINANCE_SPOT_PUBLIC", "PRICE_RESPONSE_OBSERVED"),
                ("2026-09-02T04:40:00+09:00", "cross_exchange_acceptance", acceptance_claim, "RETENTION_OBSERVED"),
            ]
        elif driver == "DERIVATIVES_LEVERAGE":
            event_specs = [
                *(
                    [("2026-09-02T04:37:00+09:00", "funding_basis", "CLAIM_FUNDING_BASIS", "CAUSE_OBSERVED")]
                    if bundle["evidence_matrix"]["funding_basis"].get("status")
                    == "VERIFIED"
                    else []
                ),
                ("2026-09-02T04:38:00+09:00", "derivatives_flow", bundle["evidence_matrix"]["derivatives_flow"]["evidence_claims"][0]["claim_id"], "CAUSE_OBSERVED"),
                ("2026-09-02T04:39:00+09:00", "oi_timeline", "CLAIM_OI_TIMELINE", "CAUSE_OBSERVED"),
                ("2026-09-02T04:40:00+09:00", "price_quotes", "BINANCE_SPOT_PUBLIC", "PRICE_RESPONSE_OBSERVED"),
            ]
        else:
            event_specs = [
                ("2026-09-02T04:39:00+09:00", "macro", "CLAIM_MACRO", "CAUSE_OBSERVED"),
                ("2026-09-02T04:40:00+09:00", "price_quotes", "BINANCE_SPOT_PUBLIC", "PRICE_RESPONSE_OBSERVED"),
            ]
        selected_hypothesis["causal_timeline"] = [
            {
                "sequence": sequence,
                "occurred_at": occurred_at,
                "evidence_dimension": dimension,
                "evidence_ref": evidence_ref,
                "event_role": role,
            }
            for sequence, (occurred_at, dimension, evidence_ref, role)
            in enumerate(event_specs, start=1)
        ]
    report = bundle["report_semantics"]
    report["primary_causal_explanation"] = primary_by_driver[driver]
    report["rejected_competing_explanations"] = [
        {
            "hypothesis_id": hypothesis_id,
            "reason_class": contract._expected_rejected_reason(
                bundle, hypothesis_id
            ),
        }
        for hypothesis_id in rejected
    ]


def qualify_candidate_evidence(bundle: dict, kind: str) -> None:
    verify_dimensions(
        bundle,
        "cross_exchange_acceptance",
        "spot_flow",
        "derivatives_flow",
        "oi_timeline",
        "capital_source",
    )
    bundle["decision_evidence_status"] = "RESOLVED"
    acceptance = bundle["evidence_matrix"]["cross_exchange_acceptance"]
    extended_range = {
        "kind": "MULTI_PERIOD",
        "start": "2026-09-02T03:40:00+09:00",
        "end": "2026-09-02T04:40:30+09:00",
    }
    acceptance["time_range"] = extended_range
    for source in acceptance["sources"]:
        source["as_of"] = "2026-09-02T04:40:30+09:00"
        source["retrieved_at"] = "2026-09-02T04:40:30+09:00"
    for claim in acceptance["evidence_claims"]:
        claim["time_range"] = extended_range
    if kind == "PREMIUM_SHORT":
        verify_dimensions(bundle, "funding_basis")
        acceptance["evidence_claims"].append({
            "claim_id": "CLAIM_SPOT_VALUE_NON_ACCEPTANCE",
            "claim_type": "SPOT_VALUE_NON_ACCEPTANCE",
            "subject_zone_id": "V1",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in acceptance["sources"]],
            "time_range": acceptance["time_range"],
            "metric": metric(),
        })
        derivatives = bundle["evidence_matrix"]["derivatives_flow"]
        derivatives["evidence_claims"].insert(0, {
            "claim_id": "CLAIM_DERIVATIVES_LEVERAGED_BUY_DOMINANCE",
            "claim_type": "DERIVATIVES_LEVERAGED_BUY_DOMINANCE",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in derivatives["sources"]],
            "time_range": derivatives["time_range"],
            "metric": metric(),
        })
        select_driver(bundle, "DERIVATIVES_LEVERAGE")
        selected = next(
            item for item in bundle["causal_attribution"]["hypotheses"]
            if item["hypothesis_id"] == "H_LEVERAGE"
        )
        selected["causal_timeline"].append({
            "sequence": len(selected["causal_timeline"]) + 1,
            "occurred_at": "2026-09-02T04:40:30+09:00",
            "evidence_dimension": "cross_exchange_acceptance",
            "evidence_ref": "CLAIM_SPOT_VALUE_NON_ACCEPTANCE",
            "event_role": "RETENTION_OBSERVED",
        })
    elif kind == "DISCOUNT_SPOT":
        spot = bundle["evidence_matrix"]["spot_flow"]
        spot["evidence_claims"][0]["claim_type"] = "NATIVE_SPOT_BUY_FLOW"
        derivatives = bundle["evidence_matrix"]["derivatives_flow"]
        derivatives["evidence_claims"].append({
            "claim_id": "CLAIM_DERIVATIVES_DELEVERAGING_FLOW",
            "claim_type": "DERIVATIVES_DELEVERAGING_FLOW",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in derivatives["sources"]],
            "time_range": derivatives["time_range"],
            "metric": metric(),
        })
        oi = bundle["evidence_matrix"]["oi_timeline"]
        oi["evidence_claims"].append({
            "claim_id": "CLAIM_OI_DELEVERAGING",
            "claim_type": "OI_DELEVERAGING",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in oi["sources"]],
            "time_range": oi["time_range"],
            "metric": metric(),
        })
        select_driver(bundle, "CRYPTO_NATIVE_SPOT")
        selected = next(
            item for item in bundle["causal_attribution"]["hypotheses"]
            if item["hypothesis_id"] == "H_SPOT"
        )
        selected["causal_timeline"] = [
            {
                "sequence": 1,
                "occurred_at": "2026-09-02T04:36:00+09:00",
                "evidence_dimension": "derivatives_flow",
                "evidence_ref": "CLAIM_DERIVATIVES_DELEVERAGING_FLOW",
                "event_role": "CAUSE_OBSERVED",
            },
            {
                "sequence": 2,
                "occurred_at": "2026-09-02T04:37:00+09:00",
                "evidence_dimension": "oi_timeline",
                "evidence_ref": "CLAIM_OI_DELEVERAGING",
                "event_role": "CAUSE_OBSERVED",
            },
            {
                "sequence": 3,
                "occurred_at": "2026-09-02T04:38:00+09:00",
                "evidence_dimension": "capital_source",
                "evidence_ref": "CLAIM_CAPITAL_SOURCE",
                "event_role": "CAUSE_OBSERVED",
            },
            {
                "sequence": 4,
                "occurred_at": "2026-09-02T04:39:00+09:00",
                "evidence_dimension": "spot_flow",
                "evidence_ref": "CLAIM_SPOT_FLOW",
                "event_role": "CAUSE_OBSERVED",
            },
            {
                "sequence": 5,
                "occurred_at": "2026-09-02T04:40:00+09:00",
                "evidence_dimension": "price_quotes",
                "evidence_ref": "BINANCE_SPOT_PUBLIC",
                "event_role": "PRICE_RESPONSE_OBSERVED",
            },
            {
                "sequence": 6,
                "occurred_at": "2026-09-02T04:40:30+09:00",
                "evidence_dimension": "cross_exchange_acceptance",
                "evidence_ref": "CLAIM_CROSS_EXCHANGE_ACCEPTANCE",
                "event_role": "RETENTION_OBSERVED",
            },
        ]
    else:
        raise AssertionError(f"unsupported candidate kind: {kind}")
    bundle["report_semantics"]["material_uncertainties"] = ["MACRO_TIMELINE"]
    bundle["report_semantics"]["next_decision_changing_evidence"] = [
        "MACRO_CAUSAL_TIMELINE"
    ]


def set_candidate_eligible(
    bundle: dict,
    candidate_index: int,
    *,
    report_relevant: bool = False,
    qualify_evidence: bool = True,
) -> None:
    candidate = bundle["trade_candidates"][candidate_index]
    if qualify_evidence:
        qualify_candidate_evidence(bundle, candidate["kind"])
    candidate["eligibility"] = "ELIGIBLE_FOR_REVIEW"
    candidate["entries"][0]["eligibility"] = "ELIGIBLE_FOR_REVIEW"
    candidate["report_relevant"] = report_relevant
    if report_relevant:
        bundle["report_semantics"][
            "overall_user_decision"
        ] = "REVIEW_ELIGIBLE_CANDIDATE"
        expose_candidate_entry_change(bundle, candidate_index)


def expose_candidate_entry_change(
    bundle: dict,
    candidate_index: int,
    entry_index: int = 0,
    *,
    previous_semantics: dict | None = None,
) -> None:
    """Project one write-before candidate delta into the user-visible level list."""

    entry = bundle["trade_candidates"][candidate_index]["entries"][entry_index]
    if previous_semantics is None:
        previous_state, _ = default_revision3_baseline_documents()
        previous_semantics = contract.extract_projection_sidecar(previous_state)[
            "canonical_semantics"
        ]
    previous_commitments = contract._candidate_commitments_by_entry(
        previous_semantics["trade_candidates"]
    )
    current_commitments = contract._candidate_commitments_by_entry(
        bundle["trade_candidates"]
    )
    entry_id = entry["entry_id"]
    change_fields = contract._candidate_commitment_change_fields(
        previous_commitments.get(entry_id), current_commitments[entry_id]
    )
    previous_items = contract._report_candidate_items(
        previous_semantics["report_semantics"],
        set(previous_commitments).union(current_commitments),
    )
    prior_change_fields = previous_items.get(entry_id, {}).get(
        "commitment_change_fields", []
    )
    if not change_fields and entry_id in previous_items:
        level = deepcopy(previous_items[entry_id])
    else:
        level = {
            "level_id": entry_id,
            "amount_u": entry["amount_u"],
            "commitment_change_fields": sorted(
                set(prior_change_fields).union(change_fields)
            ),
            "commitment_snapshot": contract._candidate_commitment_snapshot(
                current_commitments[entry_id]
            ),
        }
        if not level["commitment_change_fields"]:
            raise AssertionError("candidate entry has no write-before commitment delta")
        if "price_u" in entry:
            level["price_u"] = entry["price_u"]
        else:
            level["price_band_u"] = deepcopy(entry["price_band_u"])
    levels = bundle["report_semantics"]["decision_relevant_levels_and_sizes"]
    bundle["report_semantics"]["decision_relevant_levels_and_sizes"] = [
        item for item in levels if item.get("level_id") != entry_id
    ] + [level]


def add_path_claims(bundle: dict, path: str) -> None:
    acceptance = bundle["evidence_matrix"]["cross_exchange_acceptance"]
    acceptance["evidence_claims"] = [
        {"claim_id": f"CLAIM_ACCEPTANCE_{claim}", "claim_type": claim, "status": "VERIFIED", "source_ids": [source["source_id"] for source in acceptance["sources"]], "time_range": acceptance["time_range"], "metric": metric()}
        for claim in ("CROSS_PERIOD_ACCEPTANCE", "RETENTION", "TWO_WAY_EXECUTION", "EFFECTIVE_RETEST")
    ]
    if path == "MOVE_UP":
        acceptance["evidence_claims"].append({
            "claim_id": "CLAIM_LIFECYCLE_V1_RETEST_CONFIRMED",
            "claim_type": "VALUE_ZONE_RETEST_CONFIRMED",
            "subject_zone_id": "V1",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in acceptance["sources"]],
            "time_range": acceptance["time_range"],
            "metric": metric(),
        })
    elif path == "MOVE_DOWN":
        acceptance["evidence_claims"].append({
            "claim_id": "CLAIM_LIFECYCLE_V0_INVALIDATED",
            "claim_type": "VALUE_ZONE_INVALIDATED",
            "subject_zone_id": "V0",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in acceptance["sources"]],
            "time_range": acceptance["time_range"],
            "metric": metric(),
        })
    bundle["evidence_matrix"]["spot_flow"]["evidence_claims"] = [
        {
            "claim_id": "CLAIM_SPOT_FLOW",
            "claim_type": "NATIVE_SPOT_FLOW",
            "direction": "BUY" if path == "MOVE_UP" else "SELL",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in bundle["evidence_matrix"]["spot_flow"]["sources"]],
            "time_range": bundle["evidence_matrix"]["spot_flow"]["time_range"],
            "metric": metric(),
        }
    ]


def conditional_add_bundle() -> dict:
    bundle = make_bundle()
    verify_dimensions(
        bundle,
        "cross_exchange_acceptance", "spot_flow", "derivatives_flow",
        "oi_timeline", "capital_source",
    )
    bundle["decision_evidence_status"] = "RESOLVED"
    select_driver(bundle, "CRYPTO_NATIVE_SPOT")
    bundle["report_semantics"]["material_uncertainties"] = ["MACRO_TIMELINE"]
    bundle["report_semantics"]["next_decision_changing_evidence"] = [
        "MACRO_CAUSAL_TIMELINE"
    ]
    spot = bundle["portfolio_decision"]["new_spot"]
    spot.update({
        "decision": "CONDITIONAL_ADD",
        "amount_u": "600",
        "reason_class": "DELEVERAGING_WITH_SPOT_ABSORPTION",
        "net_exposure_impact": "INCREASE_NET_LONG_CONDITIONALLY",
        "capital_pool_impact": "RESERVE_600U_IF_ACTIVATED",
    })
    spot.pop("zero_amount_reason_class", None)
    set_candidate_eligible(bundle, 1, report_relevant=True)
    bundle["report_semantics"][
        "overall_user_decision"
    ] = "CONDITIONAL_DISCOUNT_SPOT_REVIEW"
    bundle["report_semantics"]["portfolio_action_by_module"][
        "new_spot"
    ] = "CONDITIONAL_ADD_600U"
    _apply_control(bundle)
    return bundle


def bind_revision2_artifacts(bundle: dict) -> contract.MigrationBaseline:
    bundle["runtime"]["trigger_type"] = "IMPLEMENTATION_MIGRATION"
    state_path = FIXTURES / "revision2_state_1346.md"
    plan_path = FIXTURES / "revision2_plan_1346.md"
    report_path = FIXTURES / "revision2_report_1346.md"
    projection_path = FIXTURES / "revision2_report_1346_semantics.json"
    state = state_path.read_text(encoding="utf-8")
    plan = plan_path.read_text(encoding="utf-8")
    report = report_path.read_text(encoding="utf-8")
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    bundle["previous_report_semantics"] = deepcopy(projection["report_semantics"])
    bundle["previous_fingerprints"]["report_fingerprint"] = contract._fingerprint(
        projection["report_semantics"]
    )
    bundle["previous_revision2_state_fingerprint"] = contract.content_fingerprint(state)
    bundle["previous_revision2_plan_fingerprint"] = contract.content_fingerprint(plan)
    bundle["previous_revision2_report_fingerprint"] = contract.content_fingerprint(report)
    bundle["previous_revision2_report_projection_fingerprint"] = contract._fingerprint(projection)
    baseline = contract.MigrationBaseline(
        state_path=state_path,
        plan_path=plan_path,
        report_path=report_path,
        report_projection_path=projection_path,
        state_text=state,
        plan_text=plan,
        report_text=report,
        report_projection=projection,
    )
    bundle["previous_fingerprints"][
        "decision_fingerprint"
    ] = contract.previous_decision_fingerprint_from_revision2(baseline)
    bundle.pop("previous_projection_baseline", None)
    return baseline


def unavailable_observation_from(bundle: dict) -> dict:
    observation = deepcopy(bundle["observation"])
    observation["event_discriminator_status"] = "DEEP_REVIEW_CANDIDATE"
    observation["raw_prices"] = {
        "binance": "77600", "coinbase": "77590", "kraken": "77595",
    }
    observation["quote_relation_by_venue"] = {
        venue: "ABOVE_ZONE" for venue in observation["raw_prices"]
    }
    observation["price_relation"] = "ABOVE_ZONE"
    observation["prior_price_relation"] = "INSIDE_ZONE"
    observation["relation_transition"] = "EXITED_ABOVE"
    return {
        "schema": contract.UNAVAILABLE_OBSERVATION_SCHEMA,
        "evaluation_as_of": "2026-09-02T04:41:00+09:00",
        "observation": observation,
        "value_zones": deepcopy(bundle["value_zones"]),
        "anchor_map_semantics": deepcopy(bundle["anchor_map_semantics"]),
        "price_quote_evidence": deepcopy(bundle["evidence_matrix"]["price_quotes"]),
        "fact_owner_fingerprints": deepcopy(
            bundle["account_facts"]["fact_owner_fingerprints"]
        ),
    }


def receipt_for(bundle: dict) -> dict:
    derived = contract._derive_control_unchecked(bundle)
    receipt = {
        "schema": contract.RUN_RECEIPT_SCHEMA,
        "receipt_phase": "FINAL",
        "bundle_identity": contract.compute_bundle_identity(bundle),
        "decision_class": derived["decision_class"],
        "attention_level": derived["attention_level"],
        **contract.compute_fingerprints(bundle),
        "monitor_state": derived["monitor_state"],
        "write_transition": derived["write_transition"],
        "project_patch_required": derived["project_patch_required"],
        "report_required": derived["report_required"],
        "attention_planned": derived["report_required"],
        "local_readback_state": (
            "READBACK_VERIFIED" if derived["project_patch_required"] else "NOT_REQUIRED"
        ),
        "report_validation_state": (
            "VALIDATED" if derived["report_required"] else "NOT_REQUIRED"
        ),
        "cloud_delivery_state": (
            "PENDING_RETRY" if derived["report_required"] else "NOT_REQUIRED"
        ),
        "report_visible": False,
    }
    if "previous_delivery" in bundle:
        receipt["previous_delivery"] = deepcopy(bundle["previous_delivery"])
    if derived["report_required"]:
        report = contract.render_report_body(bundle["report_semantics"])
        receipt["report_body_fingerprint"] = contract.content_fingerprint(report)
    return receipt


def unavailable_observation_fingerprint(observation: dict) -> str:
    return contract._fingerprint({
        "observation": contract.observation_semantics(
            {"observation": observation["observation"]}
        ),
        "evaluation_as_of": observation["evaluation_as_of"],
    })


def unavailable_receipt_for(bundle: dict, observation: dict) -> dict:
    return {
        "schema": contract.UNAVAILABLE_RECEIPT_SCHEMA,
        "deep_eval_status": "UNAVAILABLE",
        "retry_required": True,
        "receipt_phase": "FINAL",
        "monitor_state": "A",
        "attention_planned": False,
        "project_patch_required": False,
        "report_required": False,
        "local_readback_state": "NOT_REQUIRED",
        "report_validation_state": "NOT_REQUIRED",
        "cloud_delivery_state": "NOT_REQUIRED",
        "report_visible": False,
        "observation_fingerprint": unavailable_observation_fingerprint(observation),
        "reused_decision_fingerprint": bundle["previous_fingerprints"][
            "decision_fingerprint"
        ],
        "reused_report_fingerprint": bundle["previous_fingerprints"][
            "report_fingerprint"
        ],
    }
class ZoneAndCandleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.history = json.loads(
            (FIXTURES / "history_replays.json").read_text(encoding="utf-8")
        )
        cls.candles = json.loads(
            (FIXTURES / "candles_1346.json").read_text(encoding="utf-8")
        )

    def test_five_historical_price_relations(self) -> None:
        zone = self.history["value_zone"]
        for sample in self.history["samples"]:
            with self.subTest(sample=sample["label"]):
                result = contract.classify_zone_observation(
                    sample["prices"],
                    zone["lower"],
                    zone["upper"],
                    sample["prior_relation"],
                )
                self.assertEqual(sample["expected_relation"], result["price_relation"])

    def test_reentered_is_transition_not_lifecycle(self) -> None:
        result = contract.classify_zone_observation(
            {"a": 77100, "b": 77200}, 77000, 77500, "BELOW_ZONE"
        )
        self.assertEqual("INSIDE_ZONE", result["price_relation"])
        self.assertEqual("REENTERED_FROM_BELOW", result["relation_transition"])
        self.assertNotIn(result["relation_transition"], contract.LIFECYCLES)

    def test_1346_is_above_v0(self) -> None:
        sample = next(item for item in self.history["samples"] if item["label"] == "13:46")
        self.assertEqual(
            "ABOVE_ZONE", contract.classify_zone(sample["prices"], 77000, 77500)
        )

    def test_closed_candle_low_prevents_held_above_claim(self) -> None:
        result = contract.inspect_candles(
            self.candles, 77500, as_of_ms=1788324299999
        )
        self.assertEqual(2, result["completed_count"])
        self.assertEqual(1, result["incomplete_count"])
        self.assertFalse(result["held_above"])
        self.assertTrue(result["closed_above"])
        self.assertEqual([0], result["low_breach_indices"])
        self.assertEqual("CLOSED", result["bars"][0]["bar_status"])
        self.assertEqual("OPEN", result["bars"][2]["bar_status"])

    def test_open_candle_never_enters_completed_claim(self) -> None:
        candles = deepcopy(self.candles)
        candles[0]["low"] = "77500"
        candles[2]["low"] = "76000"
        result = contract.inspect_candles(candles, 77500, 1788324299999)
        self.assertTrue(result["held_above"])
        self.assertEqual([2], result["incomplete_indices"])

    def test_overlapping_candles_are_rejected(self) -> None:
        candles = deepcopy(self.candles)
        candles[1]["open_time"] = candles[0]["open_time"] + 1
        with self.assertRaisesRegex(contract.GuardError, "overlapping intervals"):
            contract.inspect_candles(candles, 77500, 1788324299999)

    def test_reverse_order_candles_have_identical_semantic_output(self) -> None:
        forward = contract.inspect_candles(self.candles, 77500, 1788324299999)
        reverse = contract.inspect_candles(
            list(reversed(self.candles)), 77500, 1788324299999
        )
        self.assertEqual(forward, reverse)


class ReplayAndFingerprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.history = json.loads(
            (FIXTURES / "history_replays.json").read_text(encoding="utf-8")
        )

    def test_all_five_historical_observation_changes_are_silent(self) -> None:
        baseline = make_bundle()
        prior = deepcopy(baseline["previous_fingerprints"])
        observation_fingerprints = set()
        zone = self.history["value_zone"]
        for sample in self.history["samples"]:
            bundle = deepcopy(baseline)
            classified = contract.classify_zone_observation(
                sample["prices"], zone["lower"], zone["upper"], sample["prior_relation"]
            )
            bundle["observation"].update(classified)
            bundle["observation"]["raw_prices"] = sample["prices"]
            bundle["observation"]["cross_venue_divergence_class"] = (
                "ACTIVE"
                if classified["price_relation"] == "CROSS_VENUE_DIVERGENCE"
                else "NONE"
            )
            bundle["previous_fingerprints"] = prior
            _apply_control(bundle)
            observation_fingerprints.add(
                bundle["fingerprints"]["observation_fingerprint"]
            )
            self.assertEqual("OBSERVATION_ONLY", bundle["control"]["decision_class"])
            self.assertEqual("A", bundle["control"]["monitor_state"])
            self.assertFalse(bundle["control"]["project_patch_required"])
            self.assertFalse(bundle["control"]["report_required"])
            self.assertEqual([], validate_candidate(bundle))
        self.assertGreater(len(observation_fingerprints), 1)

    def test_time_price_ids_and_runtime_do_not_change_decision_or_report(self) -> None:
        baseline = make_bundle()
        changed = deepcopy(baseline)
        changed["observation"]["raw_prices"] = {
            "binance": "99999.10",
            "coinbase": "99998.20",
            "kraken": "99997.30",
        }
        changed["observation"]["observed_at"] = "2030-01-01T00:00:00+09:00"
        changed["runtime"] = {
            "run_id": "BTC-CYCLE-NEW",
            "state_id": "BTC-STATE-NEW",
            "plan_id": "BTC-NEW",
            "pricing_as_of": "2030-01-01 00:00 JST",
            "trigger_type": "MANUAL_REEVALUATION",
        }
        before = contract.compute_fingerprints(baseline)
        after = contract.compute_fingerprints(changed)
        self.assertEqual(before["decision_fingerprint"], after["decision_fingerprint"])
        self.assertEqual(before["report_fingerprint"], after["report_fingerprint"])

    def test_runtime_trigger_is_metadata_only_and_renderer_preserves_entry(self) -> None:
        baseline = make_bundle()
        expected = contract.compute_fingerprints(baseline)
        for trigger_type in (
            "PLAN_GENERATION",
            "SCHEDULED_STRATEGY_BOUNDARY",
            "MANUAL_REEVALUATION",
        ):
            with self.subTest(trigger_type=trigger_type):
                bundle = deepcopy(baseline)
                bundle["runtime"]["trigger_type"] = trigger_type
                _apply_control(bundle)
                actual = contract.compute_fingerprints(bundle)
                self.assertEqual(
                    expected["decision_fingerprint"], actual["decision_fingerprint"]
                )
                self.assertEqual(
                    expected["report_fingerprint"], actual["report_fingerprint"]
                )
                state = contract.render_projection_document(bundle, "STATE")
                self.assertEqual(
                    trigger_type,
                    contract.parse_markdown_fields(state)["trigger_type"],
                )
                self.assertEqual([], validate_candidate(bundle))

    def test_mapping_order_and_decimal_rendering_are_stable(self) -> None:
        baseline = make_bundle()
        changed = deepcopy(baseline)
        changed["trade_candidates"][0]["entries"][0]["amount_u"] = 250.0
        changed["trade_candidates"][0]["entries"][0]["price_u"] = 81800.00
        self.assertEqual(
            contract.compute_fingerprints(baseline)["decision_fingerprint"],
            contract.compute_fingerprints(changed)["decision_fingerprint"],
        )

    def test_inactive_ladder_commitments_are_complete_decision_semantics(self) -> None:
        baseline = make_bundle()
        changed = deepcopy(baseline)
        entry = changed["trade_candidates"][0]["entries"][0]
        entry["price_u"] = "90000"
        entry["amount_u"] = "999"
        entry["amount_basis"][
            "risk_assumption"
        ] = "VOLATILITY_BUDGET_RECONFIRMED"
        self.assertNotEqual(
            contract.compute_fingerprints(baseline)["decision_fingerprint"],
            contract.compute_fingerprints(changed)["decision_fingerprint"],
        )
        changed["previous_fingerprints"] = deepcopy(baseline["previous_fingerprints"])
        _apply_control(changed)
        self.assertEqual("D", changed["control"]["monitor_state"])
        self.assertTrue(any(
            "candidate commitment changes must change report_semantics" in error
            for error in validate_candidate(changed)
        ))
        expose_candidate_entry_change(changed, 0)
        _apply_control(changed)
        self.assertTrue(changed["control"]["report_required"])
        self.assertTrue(changed["control"]["attention_planned"])
        self.assertEqual([], validate_candidate(changed))

    def test_entry_eligibility_is_decision_semantics(self) -> None:
        baseline = make_bundle()
        changed = deepcopy(baseline)
        changed["trade_candidates"][0]["entries"][0]["eligibility"] = "ELIGIBLE_FOR_REVIEW"
        self.assertNotEqual(
            contract.compute_fingerprints(baseline)["decision_fingerprint"],
            contract.compute_fingerprints(changed)["decision_fingerprint"],
        )

    def test_same_price_driver_switch_changes_decision(self) -> None:
        baseline = make_bundle()
        leverage = deepcopy(baseline)
        leverage["causal_attribution"]["driver_regime"] = "DERIVATIVES_LEVERAGE"
        leverage["causal_attribution"]["selected_hypothesis_id"] = "H_LEVERAGE"
        leverage["report_semantics"][
            "primary_causal_explanation"
        ] = "DERIVATIVES_LEVERAGE_WITHOUT_SPOT_VALUE"
        _apply_control(leverage)
        self.assertEqual(
            baseline["observation"]["raw_prices"], leverage["observation"]["raw_prices"]
        )
        self.assertNotEqual(
            baseline["fingerprints"]["decision_fingerprint"],
            leverage["fingerprints"]["decision_fingerprint"],
        )
        self.assertEqual("D", leverage["control"]["monitor_state"])

    def test_equivalent_decimal_formats_are_fingerprint_stable(self) -> None:
        baseline = make_bundle()
        changed = deepcopy(baseline)
        changed["portfolio_decision"]["new_spot"]["amount_u"] = "0.000"
        changed["report_semantics"]["decision_relevant_levels_and_sizes"][0][
            "amount_u"
        ] = 0.0
        before = contract.compute_fingerprints(baseline)
        after = contract.compute_fingerprints(changed)
        self.assertEqual(before["decision_fingerprint"], after["decision_fingerprint"])
        self.assertEqual(before["report_fingerprint"], after["report_fingerprint"])

    def test_unordered_semantic_lists_are_fingerprint_stable(self) -> None:
        first = make_bundle()
        first["major_risk"]["active"] = True
        first["major_risk"]["kinds"] = ["RISK_B", "RISK_A"]
        first["major_risk"]["portfolio_effect"] = "FREEZE_NEW_RISK"
        first["report_semantics"]["rejected_competing_explanations"] = [
            {"hypothesis_id": "H_MACRO", "reason_class": "NO_TIMELINE"},
            {"hypothesis_id": "H_SPOT", "reason_class": "NO_ACCEPTANCE"},
        ]
        first["report_semantics"]["material_uncertainties"] = ["OI", "SPOT"]
        first["report_semantics"]["next_decision_changing_evidence"] = [
            "NEXT_B",
            "NEXT_A",
        ]
        first["report_semantics"]["decision_relevant_levels_and_sizes"] = [
            {"level_id": "L2", "price_u": "82000.00", "amount_u": "500.0"},
            {"level_id": "L1", "price_u": "81000", "amount_u": "250"},
        ]
        second = deepcopy(first)
        second["major_risk"]["kinds"].reverse()
        for field in (
            "rejected_competing_explanations",
            "material_uncertainties",
            "next_decision_changing_evidence",
            "decision_relevant_levels_and_sizes",
        ):
            second["report_semantics"][field].reverse()
        before = contract.compute_fingerprints(first)
        after = contract.compute_fingerprints(second)
        self.assertEqual(before["decision_fingerprint"], after["decision_fingerprint"])
        self.assertEqual(before["report_fingerprint"], after["report_fingerprint"])


class BundleSchemaTests(unittest.TestCase):
    def test_valid_quote_only_bundle_is_explicitly_limited(self) -> None:
        bundle = make_bundle()
        self.assertEqual("VERIFIED", bundle["quote_verification_status"])
        self.assertEqual("EVIDENCE_LIMITED", bundle["decision_evidence_status"])
        self.assertEqual("UNRESOLVED", bundle["causal_attribution"]["driver_regime"])
        self.assertEqual([], validate_candidate(bundle))

    def test_completed_candle_quotes_keep_their_type_time_and_independence(self) -> None:
        bundle = make_bundle()
        urls = (
            "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=240",
            "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60",
            "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600",
        )
        quotes = bundle["evidence_matrix"]["price_quotes"]
        for source, endpoint in zip(quotes["sources"], urls):
            source.update(endpoint_url=endpoint, payload_kind="BTC_SPOT_CANDLE_CLOSE", as_of="2026-09-01T19:00:00Z")
        quotes["time_range"].update(start="2026-09-01T19:00:00Z", end="2026-09-01T19:00:00Z")
        for hypothesis in bundle["causal_attribution"]["hypotheses"]:
            for event in hypothesis["causal_timeline"]:
                if event["evidence_dimension"] == "price_quotes":
                    event["occurred_at"] = "2026-09-01T19:00:00Z"
        _apply_control(bundle)
        self.assertEqual([], validate_candidate(bundle))
        self.assertEqual("EVIDENCE_LIMITED", bundle["decision_evidence_status"])
        self.assertEqual("UNRESOLVED", bundle["causal_attribution"]["driver_regime"])
        quotes["sources"][0]["as_of"] = "2026-09-03T00:00:00Z"
        _apply_control(bundle)
        self.assertTrue(any("as_of" in error for error in validate_candidate(bundle)))

    def test_candle_close_cannot_be_labelled_ticker_or_forge_venue(self) -> None:
        source = make_bundle()["evidence_matrix"]["price_quotes"]["sources"][0]
        source["endpoint_url"] = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h"
        self.assertIsNone(contract._registered_quote_provenance(source))
        source["payload_kind"] = "BTC_SPOT_CANDLE_CLOSE"
        self.assertIsNotNone(contract._registered_quote_provenance(source))
        source["canonical_origin"] = "KRAKEN"
        self.assertIsNone(contract._registered_quote_provenance(source))
        source.update(canonical_origin="BINANCE", endpoint_url="https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
        self.assertIsNone(contract._registered_quote_provenance(source))

    def test_verified_quotes_require_two_independent_public_sources_and_venues(self) -> None:
        bundle = make_bundle()
        quotes = bundle["evidence_matrix"]["price_quotes"]
        quotes["sources"] = [
            {
                "source_id": "BINANCE_TICKER_A",
                "canonical_origin": "BINANCE",
                "venue": "binance",
                "access_scope": "PUBLIC_UNAUTHENTICATED",
            },
            {
                "source_id": "BINANCE_TICKER_B",
                "canonical_origin": "BINANCE",
                "venue": "binance",
                "access_scope": "PUBLIC_UNAUTHENTICATED",
            },
        ]
        quotes["independence_groups"] = ["BINANCE"]
        bundle["observation"]["quote_relation_by_venue"] = {
            "binance": "INSIDE_ZONE"
        }
        bundle["observation"]["evidence_availability_by_dimension"][
            "price_quotes"
        ] = "VERIFIED"
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("two independent canonical origins" in error for error in errors))
        self.assertTrue(any("at least two venues" in error for error in errors))

    def test_authenticated_or_unclassified_quotes_cannot_be_verified(self) -> None:
        bundle = make_bundle()
        bundle["evidence_matrix"]["price_quotes"]["access_scope"] = "AUTHENTICATED"
        for source in bundle["evidence_matrix"]["price_quotes"]["sources"]:
            source["access_scope"] = "AUTHENTICATED"
        _apply_control(bundle)
        self.assertTrue(
            any(
                "deduplicated public sources" in error
                for error in validate_candidate(bundle)
            )
        )

    def test_declared_groups_cannot_make_one_canonical_origin_independent(self) -> None:
        bundle = make_bundle()
        quotes = bundle["evidence_matrix"]["price_quotes"]
        quotes["sources"] = [
            {
                "source_id": "BINANCE_API_A",
                "canonical_origin": "BINANCE",
                "venue": "binance",
                "access_scope": "PUBLIC_UNAUTHENTICATED",
            },
            {
                "source_id": "BINANCE_API_B",
                "canonical_origin": "BINANCE",
                "venue": "coinbase",
                "access_scope": "PUBLIC_UNAUTHENTICATED",
            },
        ]
        quotes["independence_groups"] = ["GROUP_A", "GROUP_B"]
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("canonical origins" in error for error in errors))
        self.assertTrue(any("cross-validate" in error for error in errors))

    def test_quote_provenance_is_registry_derived_not_caller_claimed(self) -> None:
        bundle = make_bundle()
        source = bundle["evidence_matrix"]["price_quotes"]["sources"][0]
        source["canonical_origin"] = "COINBASE"
        source["venue"] = "coinbase"
        source["endpoint_url"] = "https://attacker.example/ticker"
        _apply_control(bundle)
        self.assertTrue(
            any("fixed provenance registry" in error for error in validate_candidate(bundle))
        )

    def test_noncanonical_lifecycle_is_rejected(self) -> None:
        bundle = make_bundle()
        bundle["value_zones"][0]["lifecycle"] = "RETEST_PENDING_REENTRY"
        _apply_control(bundle)
        self.assertTrue(any("lifecycle" in error for error in validate_candidate(bundle)))

    def test_reentered_requires_real_prior_relation_and_closed_bar_records(self) -> None:
        bundle = make_bundle()
        del bundle["observation"]["prior_price_relation"]
        bundle["observation"]["relation_transition"] = "REENTERED_FROM_BELOW"
        bundle["observation"]["closed_bar_relation_sequence"] = [
            {
                "bar_status": "OPEN",
                "open_time_ms": 1000, "close_time_ms": 1999, "as_of_ms": 3999,
                "boundary_role": "UPPER",
                "open_u": "77600", "high_u": "77700", "low_u": "77510",
                "close_u": "77650", "boundary_u": "77500",
                "open_relation": "AT_OR_ABOVE_BOUNDARY",
                "high_relation": "AT_OR_ABOVE_BOUNDARY",
                "low_relation": "AT_OR_ABOVE_BOUNDARY",
                "close_relation": "AT_OR_ABOVE_BOUNDARY",
            },
            {
                "bar_status": "CLOSED",
                "open_time_ms": 2000, "close_time_ms": 2999, "as_of_ms": 3999,
                "boundary_role": "UPPER",
                "open_u": "77600", "high_u": "77500", "low_u": "77400",
                "close_u": "77650", "boundary_u": "77500",
                "open_relation": "AT_OR_ABOVE_BOUNDARY",
                "high_relation": "BELOW_BOUNDARY",
                "low_relation": "BELOW_BOUNDARY",
                "close_relation": "AT_OR_ABOVE_BOUNDARY",
            },
        ]
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("REENTERED transition requires" in error for error in errors))
        self.assertTrue(any("bar_status must equal CLOSED" in error for error in errors))
        self.assertTrue(any("high_u is inconsistent" in error for error in errors))
        self.assertTrue(any("high_relation conflicts" in error for error in errors))

    def test_quote_only_cannot_claim_resolved_or_mixed(self) -> None:
        bundle = make_bundle()
        bundle["decision_evidence_status"] = "RESOLVED"
        bundle["causal_attribution"]["driver_regime"] = "MIXED"
        bundle["causal_attribution"]["driver_components"] = ["SPOT", "LEVERAGE"]
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("cannot be RESOLVED" in error for error in errors))
        self.assertTrue(any("MIXED requires" in error for error in errors))

    def test_driver_regimes_require_their_own_evidence(self) -> None:
        scenarios = (
            ("CRYPTO_NATIVE_SPOT", "H_SPOT", "spot_flow"),
            ("DERIVATIVES_LEVERAGE", "H_LEVERAGE", "derivatives_flow"),
            ("MACRO_BETA", "H_MACRO", "macro"),
        )
        for driver, selected, expected_dimension in scenarios:
            with self.subTest(driver=driver):
                bundle = make_bundle()
                bundle["causal_attribution"]["driver_regime"] = driver
                bundle["causal_attribution"]["selected_hypothesis_id"] = selected
                bundle["report_semantics"]["primary_causal_explanation"] = driver
                _apply_control(bundle)
                self.assertTrue(
                    any(
                        expected_dimension in error
                        for error in validate_candidate(bundle)
                    )
                )

    def test_unresolved_driver_cannot_claim_resolved_decision_evidence(self) -> None:
        bundle = make_bundle()
        bundle["decision_evidence_status"] = "RESOLVED"
        self.assertTrue(
            any(
                "UNRESOLVED cannot" in error
                for error in validate_candidate(bundle)
            )
        )

    def test_move_up_or_down_requires_multi_period_structural_evidence(self) -> None:
        for path in ("MOVE_UP", "MOVE_DOWN"):
            with self.subTest(path=path):
                bundle = make_bundle()
                bundle["path_decision"] = path
                bundle["forward_value_band_semantics"]["migration_state"] = path
                bundle["report_semantics"]["overall_user_decision"] = path
                _apply_control(bundle)
                errors = validate_candidate(bundle)
                self.assertTrue(any("structural spot_flow" in error for error in errors))
                self.assertTrue(
                    any("structural cross_exchange_acceptance" in error for error in errors)
                )

    def test_path_migrations_require_typed_claims_not_generic_verified_status(self) -> None:
        bundle = make_bundle()
        verify_dimensions(bundle, "cross_exchange_acceptance", "spot_flow", "capital_source")
        bundle["evidence_matrix"]["cross_exchange_acceptance"].pop("evidence_claims")
        bundle["evidence_matrix"]["spot_flow"].pop("evidence_claims")
        bundle["path_decision"] = "MOVE_DOWN"
        bundle["value_zones"][0]["lifecycle"] = "INVALIDATED"
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("evidence_claims.CROSS_PERIOD_ACCEPTANCE" in error for error in errors))
        self.assertTrue(any("native spot SELL" in error for error in errors))

    def test_widen_requires_verified_distribution_expansion_claim(self) -> None:
        bundle = make_bundle()
        bundle["path_decision"] = "WIDEN"
        bundle["forward_value_band_semantics"][
            "migration_state"
        ] = "DISTRIBUTION_WIDENED"
        bundle["report_semantics"][
            "overall_user_decision"
        ] = "WIDENED_VALUE_PATH"
        _apply_control(bundle)
        self.assertTrue(
            any("distribution_expansion" in error for error in validate_candidate(bundle))
        )
        acceptance = bundle["evidence_matrix"]["cross_exchange_acceptance"]
        acceptance.update(evidence(
            "VERIFIED", "PUBLIC_ACCEPTANCE_A", "cross_exchange_acceptance"
        ))
        second = deepcopy(acceptance["sources"][0])
        second.update({
            "source_id": "PUBLIC_ACCEPTANCE_B",
            "endpoint_url": "https://market.example.org/acceptance",
            "canonical_origin": "market.example.org",
            "venue": "venue_b",
        })
        acceptance["sources"].append(second)
        acceptance["time_range"] = time_range("MULTI_PERIOD")
        bundle["observation"]["evidence_availability_by_dimension"]["cross_exchange_acceptance"] = "VERIFIED"
        acceptance["evidence_claims"] = [{
            "claim_id": "CLAIM_DISTRIBUTION_EXPANSION",
            "claim_type": "DISTRIBUTION_EXPANSION", "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in acceptance["sources"]],
            "time_range": acceptance["time_range"],
            "metric": metric(periods=3),
        }]
        select_driver(bundle, "UNRESOLVED")
        _apply_control(bundle)
        self.assertEqual([], validate_candidate(bundle))

    def test_hypothesis_audit_fields_are_required(self) -> None:
        bundle = make_bundle()
        del bundle["causal_attribution"]["hypotheses"][0]["supporting_evidence"]
        errors = validate_candidate(bundle)
        self.assertTrue(any("supporting_evidence" in error for error in errors))

    def test_all_five_portfolio_modules_are_required(self) -> None:
        bundle = make_bundle()
        del bundle["portfolio_decision"]["optionality"]
        _apply_control(bundle)
        self.assertTrue(
            any("portfolio_decision.optionality" in error for error in validate_candidate(bundle))
        )

    def test_zero_amount_requires_composite_reason(self) -> None:
        bundle = make_bundle()
        del bundle["portfolio_decision"]["new_spot"]["zero_amount_reason_class"]
        self.assertTrue(
            any("zero_amount_reason_class" in error for error in validate_candidate(bundle))
        )

    def test_each_ladder_entry_requires_structured_amount_basis(self) -> None:
        bundle = make_bundle()
        bundle["trade_candidates"][0]["entries"][0]["amount_basis"] = "保持不变"
        _apply_control(bundle)
        self.assertTrue(any("must be structured" in error for error in validate_candidate(bundle)))

    def test_candidate_amount_and_price_are_numeric_and_nonnegative(self) -> None:
        bundle = make_bundle()
        entry = bundle["trade_candidates"][0]["entries"][0]
        entry["amount_u"] = "not-a-number"
        entry["price_u"] = "-1"
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("amount_u is not a valid number" in error for error in errors))
        self.assertTrue(any("price_u must be positive" in error for error in errors))

    def test_candidate_price_band_is_positive_and_ordered(self) -> None:
        bundle = make_bundle()
        entry = bundle["trade_candidates"][0]["entries"][0]
        del entry["price_u"]
        entry["price_band_u"] = {"lower_u": "82000", "upper_u": "81000"}
        _apply_control(bundle)
        self.assertTrue(
            any(
                "lower_u < upper_u" in error
                for error in validate_candidate(bundle)
            )
        )

    def test_entry_ids_are_globally_unique(self) -> None:
        bundle = make_bundle()
        bundle["trade_candidates"][1]["entries"][0]["entry_id"] = "S1"
        _apply_control(bundle)
        self.assertTrue(
            any("globally unique" in error for error in validate_candidate(bundle))
        )

    def test_observation_aggregate_divergence_and_availability_are_consistent(self) -> None:
        aggregate = make_bundle()
        aggregate["observation"]["price_relation"] = "ABOVE_ZONE"
        _apply_control(aggregate)
        self.assertTrue(
            any(
                "price_relation conflicts" in error
                for error in validate_candidate(aggregate)
            )
        )

        divergence = make_bundle()
        divergence["observation"]["quote_relation_by_venue"][
            "coinbase"
        ] = "BELOW_ZONE"
        divergence["observation"]["price_relation"] = "CROSS_VENUE_DIVERGENCE"
        divergence["observation"]["cross_venue_divergence_class"] = "NONE"
        _apply_control(divergence)
        self.assertTrue(
            any(
                "divergence_class conflicts" in error
                for error in validate_candidate(divergence)
            )
        )

        availability = make_bundle()
        availability["observation"]["evidence_availability_by_dimension"][
            "spot_flow"
        ] = "VERIFIED"
        _apply_control(availability)
        self.assertTrue(
            any(
                "spot_flow matrix status" in error
                for error in validate_candidate(availability)
            )
        )

    def test_fact_sync_gate_is_strict(self) -> None:
        bundle = make_bundle()
        bundle["user_input_requirement"] = "FACT_SYNC"
        self.assertTrue(any("FACT_SYNC requires" in error for error in validate_candidate(bundle)))

    def test_known_account_change_needs_evidence_and_both_semantic_changes(self) -> None:
        bundle = make_bundle()
        bundle["account_facts"]["account_fact_changed_known"] = True
        bundle["user_input_requirement"] = "FACT_SYNC"
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("known_change_evidence" in error for error in errors))
        self.assertTrue(any("actual local fact Owner version change" in error for error in errors))
        self.assertTrue(any("report_fingerprint semantics" in error for error in errors))

    def test_attention_truth_table_for_d_and_e(self) -> None:
        d_notice = make_bundle()
        set_candidate_eligible(d_notice, 0, report_relevant=True)
        _apply_control(d_notice)
        self.assertEqual("D", d_notice["control"]["monitor_state"])
        self.assertTrue(d_notice["control"]["report_required"])
        self.assertTrue(d_notice["control"]["attention_planned"])
        self.assertEqual([], validate_candidate(d_notice))
        d_notice["control"]["attention_planned"] = False
        self.assertTrue(
            any("attention_planned" in error for error in validate_candidate(d_notice))
        )

        e_report = make_bundle()
        e_report["event"]["final_event_reached"] = True
        e_report["observation"]["final_expiry_status"] = "COMPLETED"
        e_report["observation"]["event_discriminator_status"] = "FINAL_EVENT_REACHED"
        set_candidate_eligible(e_report, 0, report_relevant=True)
        e_report["report_semantics"]["overall_user_decision"] = "NEW_CYCLE_DECISION_CHANGED"
        _apply_control(e_report)
        self.assertEqual("E", e_report["control"]["monitor_state"])
        self.assertTrue(e_report["control"]["report_required"])
        self.assertTrue(e_report["control"]["attention_planned"])
        self.assertEqual([], validate_candidate(e_report))
        e_report["control"]["attention_emitted"] = False
        self.assertTrue(
            any("final-run fact" in error for error in validate_candidate(e_report))
        )

    def test_prewrite_phase_bans_future_delivery_facts_but_allows_history(self) -> None:
        bundle = make_bundle()
        bundle.pop("bundle_phase")
        self.assertTrue(
            any("bundle_phase" in error for error in validate_candidate(bundle))
        )
        bundle["bundle_phase"] = "PRE_WRITE"
        bundle["control"]["cloud_delivery_state"] = "DELIVERED"
        self.assertTrue(
            any("final-run fact" in error for error in validate_candidate(bundle))
        )
        del bundle["control"]["cloud_delivery_state"]
        bundle["previous_delivery"] = {
            "cloud_delivery_state": "DELIVERED",
            "report_visible": True,
            "report_body_fingerprint": "sha256:" + "a" * 64,
            "cloud_visible_body_fingerprint": "sha256:" + "a" * 64,
            "cloud_readback_fingerprint": "sha256:" + "b" * 64,
        }
        self.assertEqual([], validate_candidate(bundle))

    def test_final_receipt_truth_table_and_cloud_readback(self) -> None:
        a_bundle = make_bundle()
        base = receipt_for(a_bundle)
        self.assertEqual([], validate_receipt_candidate(base, a_bundle))

        changed_bundle = make_bundle()
        set_candidate_eligible(changed_bundle, 0, report_relevant=True)
        _apply_control(changed_bundle)
        changed_update = receipt_for(changed_bundle)
        state, plan, _ = ProjectionAndReportTests().projection_texts(changed_bundle)
        changed_report = contract.render_report_body(changed_bundle["report_semantics"])
        self.assertEqual(
            [], validate_receipt_candidate(
                changed_update, changed_bundle, changed_report,
                state_text=state, plan_text=plan
            )
        )

        report_bundle = deepcopy(changed_bundle)
        report_bundle["trade_candidates"][0]["report_relevant"] = True
        report_bundle["report_semantics"]["overall_user_decision"] = "REVIEW_ELIGIBLE_CANDIDATE"
        set_candidate_eligible(report_bundle, 0, report_relevant=True)
        _apply_control(report_bundle)
        report = contract.render_report_body(report_bundle["report_semantics"])
        state, plan, _ = ProjectionAndReportTests().projection_texts(report_bundle)
        cloud_readback = {
            "schema": contract.CLOUD_READBACK_SCHEMA,
            "visible_report_text": report,
            "assistant_responses": ["已收录"],
        }
        delivered = receipt_for(report_bundle)
        delivered.update(
            {
                "cloud_delivery_state": "DELIVERED",
                "report_visible": True,
                "report_body_fingerprint": contract.content_fingerprint(report),
                "cloud_visible_body_fingerprint": contract.content_fingerprint(report),
                "cloud_readback_fingerprint": contract._fingerprint(cloud_readback),
            }
        )
        self.assertEqual(
            [],
            validate_receipt_candidate(
                delivered, report_bundle, report, state_text=state, plan_text=plan,
                cloud_readback=cloud_readback,
            ),
        )
        bad_readback = deepcopy(cloud_readback)
        bad_readback["assistant_responses"].append("补充分析")
        self.assertTrue(
            any(
                "exactly one full response" in error
                for error in validate_receipt_candidate(
                    delivered, report_bundle, report, state_text=state, plan_text=plan,
                    cloud_readback=bad_readback,
                )
            )
        )
        malformed_receipt = deepcopy(delivered)
        malformed_receipt["no_second_analysis"] = False
        self.assertTrue(
            any(
                "exactly match" in error
                for error in validate_receipt_candidate(
                    malformed_receipt, report_bundle, report,
                    state_text=state, plan_text=plan, cloud_readback=cloud_readback,
                )
            )
        )
        pending = receipt_for(report_bundle)
        pending["report_body_fingerprint"] = contract.content_fingerprint(report)
        pending["report_visible"] = True
        self.assertTrue(
            any(
                "cannot claim" in error
                for error in validate_receipt_candidate(
                    pending, report_bundle, report, state_text=state, plan_text=plan
                )
            )
        )

    def test_fingerprint_mismatch_is_hard_failure(self) -> None:
        bundle = make_bundle()
        bundle["fingerprints"]["decision_fingerprint"] = "sha256:" + "0" * 64
        self.assertTrue(any("recomputation" in error for error in validate_candidate(bundle)))

    def test_unavailable_deep_eval_reuses_only_valid_old_decision(self) -> None:
        baseline = make_bundle()
        receipt = {
            "schema": contract.UNAVAILABLE_RECEIPT_SCHEMA,
            "deep_eval_status": "UNAVAILABLE",
            "retry_required": True,
            "receipt_phase": "FINAL",
            "monitor_state": "A",
            "attention_planned": False,
            "project_patch_required": False,
            "report_required": False,
            "local_readback_state": "NOT_REQUIRED",
            "report_validation_state": "NOT_REQUIRED",
            "cloud_delivery_state": "NOT_REQUIRED",
            "report_visible": False,
            "observation_fingerprint": baseline["fingerprints"]["observation_fingerprint"],
            "reused_decision_fingerprint": baseline["fingerprints"]["decision_fingerprint"],
            "reused_report_fingerprint": baseline["fingerprints"]["report_fingerprint"],
        }
        observation_input = unavailable_observation_from(baseline)
        receipt["observation_fingerprint"] = unavailable_observation_fingerprint(
            observation_input
        )
        with revision3_baseline() as previous:
            self.assertEqual(
                [], contract.validate_unavailable_receipt(
                    receipt, previous, observation_input,
                    contract.load_fact_owner_snapshot(),
                    require_owner_baseline=False,
                )
            )
        self.assertTrue(contract.validate_receipt(receipt))
        receipt["reused_report_fingerprint"] = ""
        with revision3_baseline() as previous:
            self.assertTrue(
                contract.validate_unavailable_receipt(
                    receipt, previous, observation_input,
                    contract.load_fact_owner_snapshot(),
                    require_owner_baseline=False,
                )
            )

    def test_revision_migration_is_not_disguised_as_d(self) -> None:
        bundle = make_bundle()
        bundle["write_transition"] = "MIGRATION"
        bundle["previous_strategy_revision"] = 2
        baseline = bind_revision2_artifacts(bundle)
        _apply_control(bundle)
        self.assertEqual([], contract.validate_bundle(
            bundle, baseline,
            fact_owner_snapshot=contract.load_fact_owner_snapshot(),
        ))
        self.assertEqual("REVISION_MIGRATION", bundle["control"]["decision_class"])
        self.assertEqual("NONE", bundle["control"]["monitor_state"])
        self.assertEqual("MIGRATION", bundle["control"]["write_transition"])
        self.assertTrue(bundle["control"]["project_patch_required"])
        self.assertFalse(bundle["control"]["report_required"])

    def test_runtime_trigger_and_migration_transition_are_strictly_bound(self) -> None:
        normal = make_bundle()
        normal["runtime"]["trigger_type"] = "IMPLEMENTATION_MIGRATION"
        _apply_control(normal)
        self.assertTrue(any(
            "non-MIGRATION runtime.trigger_type" in error
            for error in validate_candidate(normal)
        ))
        with self.assertRaisesRegex(contract.GuardError, "non-MIGRATION projection"):
            contract.render_projection_document(normal, "STATE")

        invalid = make_bundle()
        invalid["runtime"]["trigger_type"] = "TOTALLY_INVALID"
        _apply_control(invalid)
        self.assertTrue(any(
            "runtime.trigger_type is invalid" in error
            for error in validate_candidate(invalid)
        ))

        missing = make_bundle()
        missing["runtime"].pop("trigger_type")
        _apply_control(missing)
        self.assertTrue(any(
            "runtime must contain exactly" in error
            for error in validate_candidate(missing)
        ))

        migration = make_bundle()
        migration["write_transition"] = "MIGRATION"
        migration["previous_strategy_revision"] = 2
        baseline = bind_revision2_artifacts(migration)
        migration["runtime"]["trigger_type"] = "MANUAL_REEVALUATION"
        _apply_control(migration)
        errors = contract.validate_bundle(
            migration, baseline,
            fact_owner_snapshot=contract.load_fact_owner_snapshot(),
        )
        self.assertTrue(any(
            "MIGRATION requires runtime.trigger_type=IMPLEMENTATION_MIGRATION"
            in error for error in errors
        ))
        with self.assertRaisesRegex(contract.GuardError, "MIGRATION projection requires"):
            contract.render_projection_document(migration, "STATE")

        migration["runtime"]["trigger_type"] = "IMPLEMENTATION_MIGRATION"
        _apply_control(migration)
        self.assertEqual([], contract.validate_bundle(
            migration, baseline,
            fact_owner_snapshot=contract.load_fact_owner_snapshot(),
        ))
        rendered = contract.render_projection_document(migration, "STATE")
        self.assertEqual(
            "IMPLEMENTATION_MIGRATION",
            contract.parse_markdown_fields(rendered)["trigger_type"],
        )

    def test_migration_projection_is_immediately_loadable_for_next_a_d_e(self) -> None:
        migration = make_bundle()
        migration["write_transition"] = "MIGRATION"
        migration["previous_strategy_revision"] = 2
        revision2 = bind_revision2_artifacts(migration)
        _apply_control(migration)
        fact_snapshot = contract.load_fact_owner_snapshot()
        self.assertEqual([], contract.validate_bundle(
            migration, revision2, fact_owner_snapshot=fact_snapshot,
        ))
        state = contract.render_projection_document(migration, "STATE")
        plan = contract.render_projection_document(migration, "PLAN")
        self.assertEqual([], contract.validate_projection_texts(
            state, plan, migration, fact_owner_snapshot=fact_snapshot,
        ))
        binding, binding_errors = contract._derive_revision3_baseline_binding(
            state, plan
        )
        self.assertEqual([], binding_errors)
        self.assertIsNotNone(binding)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "migration-state.md"
            plan_path = Path(temp_dir) / "migration-plan.md"
            state_path.write_text(state, encoding="utf-8")
            plan_path.write_text(plan, encoding="utf-8")
            baseline = contract.load_revision3_baseline(
                str(state_path), str(plan_path), require_owner_paths=False
            )
            previous_semantics = contract.extract_projection_sidecar(state)[
                "canonical_semantics"
            ]

            for expected_monitor in ("A", "D", "E"):
                with self.subTest(expected_monitor=expected_monitor):
                    candidate = make_bundle()
                    candidate["previous_fingerprints"] = {
                        "decision_fingerprint": baseline.binding[
                            "decision_fingerprint"
                        ],
                        "report_fingerprint": baseline.binding[
                            "report_fingerprint"
                        ],
                    }
                    candidate["previous_projection_baseline"] = deepcopy(
                        baseline.binding
                    )
                    if expected_monitor == "D":
                        candidate["trade_candidates"][0]["entries"][0][
                            "amount_u"
                        ] = "251"
                        expose_candidate_entry_change(
                            candidate, 0, previous_semantics=previous_semantics
                        )
                    elif expected_monitor == "E":
                        candidate["event"]["final_event_reached"] = True
                        candidate["observation"][
                            "final_expiry_status"
                        ] = "COMPLETED"
                        candidate["observation"][
                            "event_discriminator_status"
                        ] = "FINAL_EVENT_REACHED"
                    _apply_control(candidate)
                    self.assertEqual(
                        expected_monitor, candidate["control"]["monitor_state"]
                    )
                    self.assertEqual([], contract.validate_bundle(
                        candidate,
                        previous_baseline=baseline,
                        fact_owner_snapshot=fact_snapshot,
                        require_owner_baseline=False,
                    ))
                    if expected_monitor in {"D", "E"}:
                        next_state = contract.render_projection_document(
                            candidate, "STATE"
                        )
                        next_plan = contract.render_projection_document(
                            candidate, "PLAN"
                        )
                        self.assertEqual([], contract.validate_projection_texts(
                            next_state,
                            next_plan,
                            candidate,
                            previous_baseline=baseline,
                            fact_owner_snapshot=fact_snapshot,
                        ))

        forged_daily = make_bundle()
        forged_daily["runtime"][
            "trigger_type"
        ] = "IMPLEMENTATION_MIGRATION"
        forged_daily["control"].update({
            "write_transition": "MIGRATION",
            "decision_class": "REVISION_MIGRATION",
            "monitor_state": "NONE",
            "project_patch_required": True,
        })
        self.assertTrue(any(
            "non-MIGRATION runtime.trigger_type" in error
            for error in validate_candidate(forged_daily)
        ))

        forged_migration = deepcopy(migration)
        forged_migration["runtime"][
            "trigger_type"
        ] = "SCHEDULED_STRATEGY_BOUNDARY"
        _apply_control(forged_migration)
        self.assertTrue(any(
            "MIGRATION requires runtime.trigger_type=IMPLEMENTATION_MIGRATION"
            in error
            for error in contract.validate_bundle(
                forged_migration,
                revision2,
                fact_owner_snapshot=fact_snapshot,
            )
        ))

    def test_migration_rejects_arbitrary_old_report_hash(self) -> None:
        bundle = make_bundle()
        bundle["write_transition"] = "MIGRATION"
        bundle["previous_strategy_revision"] = 2
        baseline = bind_revision2_artifacts(bundle)
        bundle["previous_fingerprints"]["report_fingerprint"] = "sha256:" + "f" * 64
        _apply_control(bundle)
        errors = contract.validate_bundle(
            bundle, baseline,
            fact_owner_snapshot=contract.load_fact_owner_snapshot(),
        )
        self.assertTrue(any("reprojected fingerprint" in error for error in errors))

    def test_spot_led_acceptance_counterfactual(self) -> None:
        bundle = make_bundle()
        verify_dimensions(
            bundle,
            "cross_exchange_acceptance",
            "spot_flow",
            "oi_timeline",
            "capital_source",
        )
        bundle["decision_evidence_status"] = "RESOLVED"
        select_driver(bundle, "CRYPTO_NATIVE_SPOT")
        bundle["value_zones"][1]["lifecycle"] = "RETEST_CONFIRMED"
        bundle["path_decision"] = "MOVE_UP"
        bundle["forward_value_band_semantics"]["migration_state"] = "UPWARD_CONFIRMED"
        bundle["report_semantics"]["overall_user_decision"] = "UPWARD_VALUE_MIGRATION"
        bundle["report_semantics"]["material_uncertainties"] = ["MACRO_TIMELINE"]
        bundle["report_semantics"]["next_decision_changing_evidence"] = [
            "MACRO_CAUSAL_TIMELINE"
        ]
        add_path_claims(bundle, "MOVE_UP")
        select_driver(bundle, "CRYPTO_NATIVE_SPOT")
        _apply_control(bundle)
        self.assertEqual([], validate_candidate(bundle))
        self.assertEqual("D", bundle["control"]["monitor_state"])

    def test_leverage_led_counterfactual(self) -> None:
        bundle = make_bundle()
        verify_dimensions(bundle, "derivatives_flow", "funding_basis", "oi_timeline")
        select_driver(bundle, "DERIVATIVES_LEVERAGE")
        bundle["report_semantics"]["material_uncertainties"] = [
            "SPOT_FLOW", "CAPITAL_SOURCE"
        ]
        _apply_control(bundle)
        self.assertEqual([], validate_candidate(bundle))
        self.assertEqual("D", bundle["control"]["monitor_state"])

    def test_macro_led_counterfactual(self) -> None:
        bundle = make_bundle()
        verify_dimensions(bundle, "macro", "relative_strength")
        select_driver(bundle, "MACRO_BETA")
        _apply_control(bundle)
        self.assertEqual([], validate_candidate(bundle))

    def test_deleveraging_with_spot_absorption_counterfactual(self) -> None:
        bundle = make_bundle()
        verify_dimensions(
            bundle,
            "cross_exchange_acceptance",
            "spot_flow",
            "derivatives_flow",
            "oi_timeline",
            "capital_source",
        )
        bundle["decision_evidence_status"] = "RESOLVED"
        select_driver(bundle, "CRYPTO_NATIVE_SPOT")
        bundle["report_semantics"]["material_uncertainties"] = ["MACRO_TIMELINE"]
        bundle["report_semantics"]["next_decision_changing_evidence"] = [
            "MACRO_CAUSAL_TIMELINE"
        ]
        spot = bundle["portfolio_decision"]["new_spot"]
        spot["decision"] = "CONDITIONAL_ADD"
        spot["amount_u"] = "600"
        del spot["zero_amount_reason_class"]
        spot["reason_class"] = "DELEVERAGING_WITH_SPOT_ABSORPTION"
        spot["net_exposure_impact"] = "INCREASE_NET_LONG_CONDITIONALLY"
        spot["capital_pool_impact"] = "RESERVE_600U_IF_ACTIVATED"
        set_candidate_eligible(bundle, 1, report_relevant=True)
        bundle["report_semantics"]["overall_user_decision"] = "CONDITIONAL_DISCOUNT_SPOT_REVIEW"
        bundle["report_semantics"]["portfolio_action_by_module"][
            "new_spot"
        ] = "CONDITIONAL_ADD_600U"
        _apply_control(bundle)
        self.assertEqual([], validate_candidate(bundle))

    def test_decisive_source_conflict_counterfactual(self) -> None:
        bundle = make_bundle()
        for dimension in ("price_quotes", "cross_exchange_acceptance"):
            item = deepcopy(bundle["evidence_matrix"][dimension])
            item["status"] = "CONTRADICTED"
            item["conflicts"] = ["DIRECTIONAL_CONFLICT_AFTER_ALTERNATE_CHECK"]
            item["missing"] = []
            bundle["evidence_matrix"][dimension] = item
            bundle["observation"]["evidence_availability_by_dimension"][
                dimension
            ] = "CONTRADICTED"
        bundle["quote_verification_status"] = "CONFLICTED"
        bundle["decision_evidence_status"] = "UNRESOLVED"
        bundle["observation"]["source_health_class"] = "DECISIVE_SOURCE_CONFLICT"
        bundle["major_risk"] = {
            "active": True,
            "kinds": ["DECISIVE_SOURCE_CONFLICT"],
            "portfolio_effect": "FREEZE_INCREMENTAL_RISK",
        }
        bundle["report_semantics"]["overall_user_decision"] = "CONSERVATIVE_RISK_FREEZE"
        bundle["report_semantics"]["user_visible_major_risk"] = "DECISIVE_SOURCE_CONFLICT"
        _apply_control(bundle)
        self.assertEqual([], validate_candidate(bundle))
        self.assertEqual("D", bundle["control"]["monitor_state"])

    def test_final_event_forces_e_without_duplicate_report(self) -> None:
        bundle = make_bundle()
        bundle["event"]["final_event_reached"] = True
        bundle["observation"]["final_expiry_status"] = "COMPLETED"
        bundle["observation"]["event_discriminator_status"] = "FINAL_EVENT_REACHED"
        _apply_control(bundle)
        self.assertEqual([], validate_candidate(bundle))
        self.assertEqual("E", bundle["control"]["monitor_state"])
        self.assertTrue(bundle["control"]["project_patch_required"])
        self.assertFalse(bundle["control"]["report_required"])

    def test_known_account_fact_change_requires_fact_sync_and_semantic_update(self) -> None:
        bundle = make_bundle()
        bundle["account_facts"]["account_fact_changed_known"] = True
        bundle["account_facts"]["known_change_evidence"] = [
            {
                "confirmation_type": "USER_CONFIRMED_POSITION_CHANGE",
                "owner_id": "ASSET_CONFIGURATION",
            }
        ]
        bundle["account_facts"]["local_record_status"] = "FACT_SYNC_PENDING"
        bundle["user_input_requirement"] = "FACT_SYNC"
        module = bundle["portfolio_decision"]["existing_digital_beta"]
        module["decision"] = "REVIEW_AFTER_KNOWN_FACT_CHANGE"
        module["reason_class"] = "LOCAL_FACT_RECORD_LAGS_KNOWN_CHANGE"
        bundle["report_semantics"]["overall_user_decision"] = "FACT_SYNC_NEEDED_BEFORE_EXECUTION_REVIEW"
        bundle["report_semantics"]["portfolio_action_by_module"][
            "existing_digital_beta"
        ] = "REVIEW_AFTER_FACT_SYNC"
        with changed_fact_owner_snapshot("ASSET_CONFIGURATION") as fact_snapshot:
            bundle["account_facts"]["fact_owner_fingerprints"] = dict(
                fact_snapshot.fingerprints
            )
            _apply_control(bundle)
            self.assertEqual([], validate_candidate(bundle, fact_snapshot))
            self.assertEqual("D", bundle["control"]["monitor_state"])


class ProjectionAndReportTests(unittest.TestCase):
    HASHES = {
        "observation": "sha256:" + "1" * 64,
        "decision": "sha256:" + "2" * 64,
        "report": "sha256:" + "3" * 64,
    }

    def projection_texts(self, bundle: dict | None = None) -> tuple[str, str, dict]:
        if bundle is None:
            bundle = make_bundle()
            set_candidate_eligible(bundle, 0, report_relevant=True)
            bundle["report_semantics"]["overall_user_decision"] = (
                "REVIEW_ELIGIBLE_CANDIDATE"
            )
            _apply_control(bundle)
        return (
            contract.render_projection_document(bundle, "STATE"),
            contract.render_projection_document(bundle, "PLAN"),
            bundle,
        )
        # Kept below only as historical test-source context; production and
        # tests both consume the deterministic renderer above.
        fps = bundle["fingerprints"]
        transition = bundle["control"]["write_transition"]
        state = f"""---
strategy_id: BTC-CORE
strategy_revision: 3
state_id: {bundle['runtime']['state_id']}
cycle_id: {bundle['runtime']['run_id']}
created_by_transition: {transition}
projection_plan_id: {bundle['runtime']['plan_id']}
derived_at: 2026-09-02 17:00 JST
state_recorded_at: 2026-09-02 17:00 JST
trigger_type: SCHEDULED_STRATEGY_BOUNDARY
pricing_as_of: {bundle['runtime']['pricing_as_of']}
account_fact_source: LOCAL_CONFIRMED_RECORDS
account_fact_as_of: 2026-09-02 17:00 JST
derivation_status: READY
quote_verification_status: {bundle['quote_verification_status']}
decision_evidence_status: {bundle['decision_evidence_status']}
driver_regime: {bundle['causal_attribution']['driver_regime']}
path_decision: {bundle['path_decision']}
projection_mode: {bundle['projection_mode']}
user_input_requirement: {bundle['user_input_requirement']}
observation_fingerprint: {fps['observation_fingerprint']}
decision_fingerprint: {fps['decision_fingerprint']}
report_fingerprint: {fps['report_fingerprint']}
---
## observation
已按跨所报价、完成K线与证据维度归一化记录本轮观察。
## evidence_matrix
分维证据矩阵保留来源、时间范围、冲突和缺失项。
## hypothesis_competition
竞争假设分别记录支持、反证、时序、证伪条件与下一证据。
## anchor_map
价值锚与生命周期分开维护，不由单点价格关系直接改写。
## forward_value_band
前向价值带继续使用最近有效结构锚，未发生路径迁移。
## trade_candidates
候选阶梯保留内部完整条件，资格变化独立记录。
## portfolio_decision
五个组合模块已分别复核事实基础、规模和净敞口影响。
## event
当前事件尚未结束，只有能区分假设的窗口才作为下一复核点。
"""
        plan = f"""# plan
- **plan_id**：{bundle['runtime']['plan_id']}
- **source_strategy_id**：BTC-CORE
- **source_strategy_revision**：3
- **source_state_id**：{bundle['runtime']['state_id']}
- **cycle_id**：{bundle['runtime']['run_id']}
- **state_as_of**：2026-09-02 17:00 JST
- **observation_fingerprint**：{fps['observation_fingerprint']}
- **decision_fingerprint**：{fps['decision_fingerprint']}
- **report_fingerprint**：{fps['report_fingerprint']}
## evidence
证据矩阵是计划的事实输入，报价验证不等于决策证据验证。
## hypothesis
主假设与未采用假设同时保留，下一证据用于区分而非机械确认。
## lifecycle
价值区只使用四个生命周期状态，即时价格关系不是生命周期。
## path
当前继续保持路径，需要跨所、跨时段且有因果先后的证据才迁移。
## portfolio
既有 Digital Beta、Alpha 空头、新现货、新空头与 Optionality 分别评估。
## candidates
价位、规模、形成和失效条件保留为候选，不代表委托或成交。
## event
最终事件强制复核并建立下一周期，是否报告仍取决于用户决策变化。
"""
        state += "\n" + contract.render_projection_sidecar(bundle, state, "STATE") + "\n"
        plan += "\n" + contract.render_projection_sidecar(bundle, plan, "PLAN") + "\n"
        return state, plan, bundle

    def test_atomic_projection_identity_and_three_fingerprints(self) -> None:
        state, plan, bundle = self.projection_texts()
        self.assertEqual([], validate_projection_candidate(state, plan, bundle))

    def test_old_projection_fingerprint_is_rejected(self) -> None:
        state, plan, bundle = self.projection_texts()
        state += "projection_fingerprint: sha256:" + "a" * 64 + "\n"
        plan += "- **projection_fingerprint**：sha256:" + "b" * 64 + "\n"
        errors = validate_projection_candidate(state, plan, bundle)
        self.assertEqual(2, sum("legacy revision-2" in error for error in errors))

    def test_projection_mismatch_and_empty_fingerprint_fail(self) -> None:
        state, plan, bundle = self.projection_texts()
        bad_plan = plan.replace(bundle["runtime"]["run_id"], "BTC-CYCLE-OTHER")
        bad_plan = bad_plan.replace(bundle["fingerprints"]["decision_fingerprint"], "")
        errors = validate_projection_candidate(state, bad_plan, bundle)
        self.assertTrue(any("cycle identity mismatch" in error for error in errors))
        self.assertTrue(any("decision_fingerprint is missing" in error for error in errors))

    def test_baseline_hash_detects_concurrent_owner_change(self) -> None:
        state, plan, _ = self.projection_texts()
        expected_state = contract.content_fingerprint(state)
        expected_plan = contract.content_fingerprint(plan)
        self.assertEqual(
            [],
            contract.validate_baseline_hashes(
                state, plan, expected_state, expected_plan
            ),
        )
        errors = contract.validate_baseline_hashes(
            state + "\nconcurrent: change\n", plan, expected_state, expected_plan
        )
        self.assertTrue(any("state baseline changed" in error for error in errors))

    def test_created_by_transition_is_persistent_write_origin(self) -> None:
        state, plan, bundle = self.projection_texts()
        bad = state.replace("created_by_transition: D", "created_by_transition: A")
        self.assertTrue(
            any("created_by_transition" in error for error in validate_projection_candidate(bad, plan, bundle))
        )

    def report_and_projection(self) -> tuple[str, dict]:
        excerpts = {
            "overall_user_decision": "目前不改变任何新增仓位",
            "primary_causal_explanation": "单点报价没有回答资金来自哪里",
            "rejected_competing_explanations": "现货主导、杠杆主导和宏观主导都暂未胜出",
            "portfolio_action_by_module": "既有现货与保护性空头维持，新现货和新空头均不增加",
            "decision_relevant_levels_and_sizes": "此刻仍有意义的区间是77,000至77,500U，对应新增规模0U",
            "material_uncertainties": "关键缺口是跨所成交留存、主动现货流和OI时序",
            "next_decision_changing_evidence": "下一项能改变决定的证据是跨窗口现货接受与资金来源同时获得验证",
            "user_visible_major_risk": "目前没有新增重大风险",
        }
        projection = deepcopy(make_bundle()["report_semantics"])
        projection["rendered_evidence"] = contract.generate_report_witnesses(projection)
        report = contract.render_report_body(projection)
        disclaimer = "这些判断只用于你的评估，不代表成交或交易授权。"
        projection["authorization_disclaimer_excerpt"] = disclaimer
        return report, projection

    def test_report_uses_structured_witnesses_without_fixed_headings(self) -> None:
        report, projection = self.report_and_projection()
        self.assertNotIn("##", report)
        self.assertEqual([], contract.validate_report_text(report, projection))

    def test_report_leakage_is_hard_failure(self) -> None:
        report, projection = self.report_and_projection()
        errors = contract.validate_report_text(report + "\nmonitor_state=A", projection)
        self.assertTrue(any("leaks internal" in error for error in errors))

    def test_visible_body_rejects_marker_field_name_and_canonical_json(self) -> None:
        report, projection = self.report_and_projection()
        for leaked in (
            "[[BTC-R3-REPORT-WITNESS|overall_user_decision|{}]]",
            'overall_user_decision: NO_INCREMENTAL_POSITION_CHANGE',
            '{"overall_user_decision":"NO_INCREMENTAL_POSITION_CHANGE"}',
        ):
            with self.subTest(leaked=leaked):
                self.assertTrue(
                    any(
                        "leaks internal" in error
                        for error in contract.validate_report_text(report + "\n" + leaked, projection)
                    )
                )

    def test_report_missing_semantic_witness_fails(self) -> None:
        report, projection = self.report_and_projection()
        report = report.replace(
            projection["rendered_evidence"]["material_uncertainties"]["visible_excerpt"], ""
        )
        self.assertTrue(
            any("material_uncertainties" in error for error in contract.validate_report_text(report, projection))
        )

    def test_report_cannot_reuse_one_generic_witness_for_all_semantics(self) -> None:
        report, projection = self.report_and_projection()
        shared = "目前不改变任何新增仓位"
        projection["rendered_evidence"] = {
            field: shared for field in contract.REPORT_SEMANTIC_FIELDS
        }
        errors = contract.validate_report_text(report, projection)
        self.assertTrue(any("deterministic semantic sidecar" in error for error in errors))

    def test_plain_unique_substrings_are_not_semantic_witnesses(self) -> None:
        report, projection = self.report_and_projection()
        projection["rendered_evidence"] = {
            field: f"unique unrelated substring number {index}"
            for index, field in enumerate(contract.REPORT_SEMANTIC_FIELDS)
        }
        errors = contract.validate_report_text(report, projection)
        self.assertTrue(any("deterministic semantic sidecar" in error for error in errors))

    def test_semantic_witness_rejects_binding_for_a_different_field(self) -> None:
        report, projection = self.report_and_projection()
        witness = projection["rendered_evidence"]["overall_user_decision"]
        projection["rendered_evidence"]["primary_causal_explanation"] = witness
        errors = contract.validate_report_text(report, projection)
        self.assertTrue(any("deterministic semantic sidecar" in error for error in errors))

    def test_cli_classify_zone_smoke(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
            json.dump({"a": 77600, "b": 77501}, handle)
            handle.flush()
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "strategy_contract.py"),
                    "classify-zone",
                    "--input",
                    handle.name,
                    "--lower",
                    "77000",
                    "--upper",
                    "77500",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("ABOVE_ZONE", json.loads(result.stdout)["price_relation"])

    def test_cli_validate_receipt_rejects_non_owner_readback_paths(self) -> None:
        bundle = make_bundle()
        set_candidate_eligible(bundle, 0, report_relevant=True)
        _apply_control(bundle)
        receipt = receipt_for(bundle)
        state, plan, _ = self.projection_texts(bundle)
        old_state, old_plan = default_revision3_baseline_documents()
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = {
                "receipt": Path(temp_dir) / "receipt.json",
                "bundle": Path(temp_dir) / "bundle.json",
                "state": Path(temp_dir) / "state.md",
                "plan": Path(temp_dir) / "plan.md",
                "old_state": Path(temp_dir) / "old-state.md",
                "old_plan": Path(temp_dir) / "old-plan.md",
            }
            paths["receipt"].write_text(json.dumps(receipt), encoding="utf-8")
            paths["bundle"].write_text(json.dumps(bundle), encoding="utf-8")
            paths["state"].write_text(state, encoding="utf-8")
            paths["plan"].write_text(plan, encoding="utf-8")
            paths["old_state"].write_text(old_state, encoding="utf-8")
            paths["old_plan"].write_text(old_plan, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "strategy_contract.py"),
                    "validate-receipt",
                    "--input",
                    str(paths["receipt"]),
                    "--bundle",
                    str(paths["bundle"]),
                    "--state",
                    str(paths["state"]),
                    "--plan",
                    str(paths["plan"]),
                    "--previous-state-snapshot",
                    str(paths["old_state"]),
                    "--previous-plan-snapshot",
                    str(paths["old_plan"]),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(
            any("fixed state/plan Owner paths" in error for error in payload["errors"])
        )


class StructuralBypassRegressionTests(unittest.TestCase):
    def test_each_eligible_candidate_kind_requires_typed_formation_evidence(self) -> None:
        for candidate_index, kind in ((0, "PREMIUM_SHORT"), (1, "DISCOUNT_SPOT")):
            with self.subTest(kind=kind):
                forged = make_bundle()
                set_candidate_eligible(
                    forged,
                    candidate_index,
                    report_relevant=True,
                    qualify_evidence=False,
                )
                _apply_control(forged)
                errors = validate_candidate(forged)
                self.assertTrue(any(
                    f"trade_candidates[{candidate_index}] eligibility requires "
                    "RESOLVED decision evidence" in error
                    for error in errors
                ))
                self.assertTrue(any(
                    f"trade_candidates[{candidate_index}] eligibility requires "
                    "bound VERIFIED" in error
                    for error in errors
                ))
                self.assertTrue(any(
                    f"{kind} formation requires" in error
                    for error in errors
                ))

    def test_each_eligible_candidate_kind_accepts_complete_formation_evidence(self) -> None:
        for candidate_index, kind in ((0, "PREMIUM_SHORT"), (1, "DISCOUNT_SPOT")):
            with self.subTest(kind=kind):
                bundle = make_bundle()
                set_candidate_eligible(bundle, candidate_index, report_relevant=True)
                _apply_control(bundle)
                self.assertEqual([], validate_candidate(bundle))

                if kind == "PREMIUM_SHORT":
                    dimension = "funding_basis"
                    missing_claim = "LEVERAGE_COST_EXPANSION"
                else:
                    dimension = "oi_timeline"
                    missing_claim = "OI_DELEVERAGING"
                evidence_claims = bundle["evidence_matrix"][dimension][
                    "evidence_claims"
                ]
                bundle["evidence_matrix"][dimension]["evidence_claims"] = [
                    claim for claim in evidence_claims
                    if claim["claim_type"] != missing_claim
                ]
                _apply_control(bundle)
                self.assertTrue(any(
                    f"formation claim ['{missing_claim}']" in error
                    for error in validate_candidate(bundle)
                ))

    def test_each_candidate_formation_claim_requires_a_multi_period_sequence(self) -> None:
        dimensions_by_kind = {
            "PREMIUM_SHORT": (
                "derivatives_flow", "oi_timeline", "funding_basis",
                "cross_exchange_acceptance",
            ),
            "DISCOUNT_SPOT": (
                "cross_exchange_acceptance", "spot_flow", "derivatives_flow",
                "oi_timeline", "capital_source",
            ),
        }
        for candidate_index, kind in ((0, "PREMIUM_SHORT"), (1, "DISCOUNT_SPOT")):
            for dimension in dimensions_by_kind[kind]:
                with self.subTest(kind=kind, dimension=dimension):
                    bundle = make_bundle()
                    set_candidate_eligible(bundle, candidate_index, report_relevant=True)
                    item = bundle["evidence_matrix"][dimension]
                    point = time_range("POINT_IN_TIME")
                    item["time_range"] = point
                    for claim in item["evidence_claims"]:
                        claim["time_range"] = point
                        claim["metric"] = metric(periods=1)
                    _apply_control(bundle)
                    self.assertTrue(any(
                        f"multi-period {dimension} formation claim" in error
                        for error in validate_candidate(bundle)
                    ))

    def test_every_candidate_formation_claim_must_enter_selected_timeline(self) -> None:
        for candidate_index, kind, removed_ref in (
            (0, "PREMIUM_SHORT", "CLAIM_SPOT_VALUE_NON_ACCEPTANCE"),
            (1, "DISCOUNT_SPOT", "CLAIM_DERIVATIVES_DELEVERAGING_FLOW"),
        ):
            with self.subTest(kind=kind):
                bundle = make_bundle()
                set_candidate_eligible(bundle, candidate_index, report_relevant=True)
                selected = next(
                    hypothesis
                    for hypothesis in bundle["causal_attribution"]["hypotheses"]
                    if hypothesis["hypothesis_id"]
                    == bundle["causal_attribution"]["selected_hypothesis_id"]
                )
                selected["causal_timeline"] = [
                    event for event in selected["causal_timeline"]
                    if event["evidence_ref"] != removed_ref
                ]
                for sequence, event in enumerate(
                    selected["causal_timeline"], start=1
                ):
                    event["sequence"] = sequence
                _apply_control(bundle)
                self.assertTrue(any(
                    "every formation claim in its selected hypothesis timeline"
                    in error
                    for error in validate_candidate(bundle)
                ))

    def test_premium_nonacceptance_must_bind_the_corresponding_unretested_anchor(self) -> None:
        bundle = make_bundle()
        set_candidate_eligible(bundle, 0, report_relevant=True)
        claim = next(
            claim
            for claim in bundle["evidence_matrix"]["cross_exchange_acceptance"][
                "evidence_claims"
            ]
            if claim["claim_type"] == "SPOT_VALUE_NON_ACCEPTANCE"
        )
        claim["subject_zone_id"] = "V0"
        _apply_control(bundle)
        self.assertTrue(any(
            "SPOT_VALUE_NON_ACCEPTANCE" in error
            and "multi-period cross_exchange_acceptance" in error
            for error in validate_candidate(bundle)
        ))

    def test_discount_deleveraging_and_oi_cannot_follow_price_response(self) -> None:
        for dimension, evidence_ref in (
            ("derivatives_flow", "CLAIM_DERIVATIVES_DELEVERAGING_FLOW"),
            ("oi_timeline", "CLAIM_OI_DELEVERAGING"),
        ):
            with self.subTest(dimension=dimension):
                bundle = make_bundle()
                set_candidate_eligible(bundle, 1, report_relevant=True)
                item = bundle["evidence_matrix"][dimension]
                extended = {
                    "kind": "MULTI_PERIOD",
                    "start": "2026-09-02T03:40:00+09:00",
                    "end": "2026-09-02T04:40:30+09:00",
                }
                item["time_range"] = extended
                for source in item["sources"]:
                    source["as_of"] = "2026-09-02T04:40:30+09:00"
                    source["retrieved_at"] = "2026-09-02T04:40:30+09:00"
                for claim in item["evidence_claims"]:
                    claim["time_range"] = extended
                selected = next(
                    hypothesis
                    for hypothesis in bundle["causal_attribution"]["hypotheses"]
                    if hypothesis["hypothesis_id"] == "H_SPOT"
                )
                late = next(
                    event for event in selected["causal_timeline"]
                    if event["evidence_ref"] == evidence_ref
                )
                selected["causal_timeline"].remove(late)
                late["occurred_at"] = "2026-09-02T04:40:15+09:00"
                selected["causal_timeline"].insert(-1, late)
                for sequence, event in enumerate(
                    selected["causal_timeline"], start=1
                ):
                    event["sequence"] = sequence
                _apply_control(bundle)
                self.assertTrue(any(
                    "DISCOUNT_SPOT formation requires deleveraging" in error
                    for error in validate_candidate(bundle)
                ))

    def test_evidence_limited_drivers_still_require_all_typed_causal_refs(self) -> None:
        scenarios = (
            (
                "CRYPTO_NATIVE_SPOT",
                ("cross_exchange_acceptance", "spot_flow", "capital_source"),
                "capital_source",
            ),
            (
                "DERIVATIVES_LEVERAGE",
                ("derivatives_flow", "oi_timeline"),
                "oi_timeline",
            ),
            ("MACRO_BETA", ("macro",), "macro"),
        )
        for driver, dimensions, removed_dimension in scenarios:
            with self.subTest(driver=driver):
                bundle = make_bundle()
                verify_dimensions(bundle, *dimensions)
                select_driver(bundle, driver)
                self.assertEqual("EVIDENCE_LIMITED", bundle["decision_evidence_status"])
                selected = next(
                    hypothesis
                    for hypothesis in bundle["causal_attribution"]["hypotheses"]
                    if hypothesis["hypothesis_id"]
                    == bundle["causal_attribution"]["selected_hypothesis_id"]
                )
                selected["causal_timeline"] = [
                    event for event in selected["causal_timeline"]
                    if event["evidence_dimension"] != removed_dimension
                ]
                for sequence, event in enumerate(
                    selected["causal_timeline"], start=1
                ):
                    event["sequence"] = sequence
                _apply_control(bundle)
                self.assertTrue(any(
                    f"selected hypothesis must reference bound VERIFIED {driver} "
                    f"{removed_dimension}" in error
                    for error in validate_candidate(bundle)
                ))

    def test_candidate_commitment_d_patch_uses_report_receipt_branch(self) -> None:
        bundle = make_bundle()
        bundle["trade_candidates"][0]["entries"][0]["amount_u"] = "251"
        expose_candidate_entry_change(bundle, 0)
        _apply_control(bundle)
        self.assertEqual("D", bundle["control"]["monitor_state"])
        self.assertTrue(bundle["control"]["project_patch_required"])
        self.assertTrue(bundle["control"]["report_changed"])
        self.assertTrue(bundle["control"]["report_required"])

        state, plan, _ = ProjectionAndReportTests().projection_texts(bundle)
        report = contract.render_report_body(bundle["report_semantics"])
        receipt = receipt_for(bundle)
        self.assertIn("report_body_fingerprint", receipt)
        self.assertEqual(
            [],
            validate_receipt_candidate(
                receipt, bundle, report, state_text=state, plan_text=plan
            ),
        )
        binding, errors = contract._derive_revision3_baseline_binding(state, plan)
        self.assertEqual([], errors)
        self.assertIsNotNone(binding)

    def test_e_with_unchanged_report_is_patch_only_and_silent(self) -> None:
        bundle = make_bundle()
        bundle["event"]["final_event_reached"] = True
        bundle["observation"]["final_expiry_status"] = "COMPLETED"
        bundle["observation"]["event_discriminator_status"] = "FINAL_EVENT_REACHED"
        _apply_control(bundle)
        self.assertEqual("E", bundle["control"]["monitor_state"])
        self.assertTrue(bundle["control"]["project_patch_required"])
        self.assertFalse(bundle["control"]["report_changed"])
        self.assertFalse(bundle["control"]["report_required"])
        self.assertEqual([], validate_candidate(bundle))
        state, plan, _ = ProjectionAndReportTests().projection_texts(bundle)
        receipt = receipt_for(bundle)
        self.assertNotIn("report_body_fingerprint", receipt)
        self.assertEqual(
            [],
            validate_receipt_candidate(
                receipt, bundle, state_text=state, plan_text=plan
            ),
        )

    def test_control_change_flags_cannot_be_caller_tampered(self) -> None:
        for field in ("decision_changed", "report_changed"):
            with self.subTest(field=field):
                bundle = make_bundle()
                bundle["control"][field] = not bundle["control"][field]
                self.assertTrue(any(
                    f"control.{field} must equal derived value" in error
                    for error in validate_candidate(bundle)
                ))

    def test_report_overall_and_lifecycle_are_bound_to_strategy_facts(self) -> None:
        false_upward = make_bundle()
        false_upward["report_semantics"][
            "overall_user_decision"
        ] = "UPWARD_VALUE_MIGRATION"
        _apply_control(false_upward)
        self.assertTrue(
            any(
                "overall_user_decision conflicts" in error
                for error in validate_candidate(false_upward)
            )
        )

        wrong_lifecycle = make_bundle()
        wrong_lifecycle["path_decision"] = "MOVE_UP"
        wrong_lifecycle["forward_value_band_semantics"][
            "migration_state"
        ] = "UPWARD_CONFIRMED"
        wrong_lifecycle["report_semantics"][
            "overall_user_decision"
        ] = "UPWARD_VALUE_MIGRATION"
        _apply_control(wrong_lifecycle)
        self.assertTrue(
            any(
                "path migration requires" in error
                for error in validate_candidate(wrong_lifecycle)
            )
        )

        wrong_portfolio = make_bundle()
        wrong_portfolio["report_semantics"]["portfolio_action_by_module"][
            "new_spot"
        ] = "CONDITIONAL_ADD_600U"
        _apply_control(wrong_portfolio)
        self.assertTrue(
            any(
                "new_spot conflicts with portfolio_decision" in error
                for error in validate_candidate(wrong_portfolio)
            )
        )

        wrong_anchor = make_bundle()
        verify_dimensions(
            wrong_anchor, "cross_exchange_acceptance", "spot_flow", "capital_source"
        )
        wrong_anchor["path_decision"] = "MOVE_DOWN"
        wrong_anchor["forward_value_band_semantics"][
            "migration_state"
        ] = "DOWNWARD_CONFIRMED"
        wrong_anchor["value_zones"][1]["lifecycle"] = "INVALIDATED"
        wrong_anchor["report_semantics"][
            "overall_user_decision"
        ] = "DOWNWARD_VALUE_MIGRATION"
        add_path_claims(wrong_anchor, "MOVE_DOWN")
        select_driver(wrong_anchor, "UNRESOLVED")
        _apply_control(wrong_anchor)
        self.assertTrue(
            any(
                "primary persistent anchor" in error
                for error in validate_candidate(wrong_anchor)
            )
        )

    def test_portfolio_decisions_are_scoped_to_their_asset_module(self) -> None:
        for module, decision in (
            ("new_spot", "EXIT_REVIEW"),
            ("new_short", "REDUCE_REVIEW"),
            ("optionality", "EXIT_REVIEW"),
        ):
            with self.subTest(module=module):
                bundle = make_bundle()
                bundle["portfolio_decision"][module]["decision"] = decision
                bundle["report_semantics"]["portfolio_action_by_module"][
                    module
                ] = contract.PORTFOLIO_REPORT_BY_DECISION[decision]
                bundle["report_semantics"][
                    "overall_user_decision"
                ] = "REVIEW_ELIGIBLE_CANDIDATE"
                _apply_control(bundle)
                self.assertTrue(
                    any(
                        "not valid for this portfolio module" in error
                        for error in validate_candidate(bundle)
                    )
                )

    def test_selected_primary_hypothesis_cannot_be_reported_as_rejected(self) -> None:
        bundle = make_bundle()
        bundle["causal_attribution"]["rejected_hypothesis_ids"].append(
            "H_UNRESOLVED"
        )
        bundle["report_semantics"]["rejected_competing_explanations"].append(
            {"hypothesis_id": "H_UNRESOLVED", "reason_class": "NO_TIMELINE"}
        )
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("contradicts rejected" in error for error in errors))
        self.assertTrue(any("selected hypothesis cannot also be rejected" in error for error in errors))

    def test_mixed_driver_uses_an_explicit_umbrella_hypothesis(self) -> None:
        invalid = make_bundle()
        verify_dimensions(
            invalid,
            "cross_exchange_acceptance", "spot_flow", "capital_source",
            "derivatives_flow", "oi_timeline",
        )
        invalid["causal_attribution"]["driver_regime"] = "MIXED"
        invalid["causal_attribution"]["driver_components"] = [
            "CRYPTO_NATIVE_SPOT", "DERIVATIVES_LEVERAGE",
        ]
        invalid["report_semantics"][
            "primary_causal_explanation"
        ] = "MIXED_CAUSAL_DRIVERS"
        invalid["report_semantics"]["material_uncertainties"] = ["MACRO_TIMELINE"]
        invalid["report_semantics"]["next_decision_changing_evidence"] = [
            "MACRO_CAUSAL_TIMELINE"
        ]
        _apply_control(invalid)
        invalid_errors = validate_candidate(invalid)
        self.assertTrue(
            any("selected_hypothesis_id conflicts" in error for error in invalid_errors)
        )
        self.assertTrue(
            any("non-selected, non-component" in error for error in invalid_errors)
        )

        valid = deepcopy(invalid)
        valid["causal_attribution"]["hypotheses"].append(
            hypothesis("H_MIXED", "现货与杠杆共同形成可验证的因果解释")
        )
        valid["causal_attribution"]["selected_hypothesis_id"] = "H_MIXED"
        valid["causal_attribution"]["rejected_hypothesis_ids"] = [
            "H_UNRESOLVED", "H_MACRO",
        ]
        mixed = next(
            item for item in valid["causal_attribution"]["hypotheses"]
            if item["hypothesis_id"] == "H_MIXED"
        )
        mixed["causal_timeline"] = [
            {
                "sequence": 1,
                "occurred_at": "2026-09-02T04:35:00+09:00",
                "evidence_dimension": "spot_flow",
                "evidence_ref": "CLAIM_SPOT_FLOW",
                "event_role": "CAUSE_OBSERVED",
            },
            {
                "sequence": 2,
                "occurred_at": "2026-09-02T04:36:00+09:00",
                "evidence_dimension": "capital_source",
                "evidence_ref": "CLAIM_CAPITAL_SOURCE",
                "event_role": "CAUSE_OBSERVED",
            },
            {
                "sequence": 3,
                "occurred_at": "2026-09-02T04:37:00+09:00",
                "evidence_dimension": "derivatives_flow",
                "evidence_ref": "CLAIM_DERIVATIVES_FLOW",
                "event_role": "CAUSE_OBSERVED",
            },
            {
                "sequence": 4,
                "occurred_at": "2026-09-02T04:38:00+09:00",
                "evidence_dimension": "oi_timeline",
                "evidence_ref": "CLAIM_OI_TIMELINE",
                "event_role": "CAUSE_OBSERVED",
            },
            {
                "sequence": 5,
                "occurred_at": "2026-09-02T04:40:00+09:00",
                "evidence_dimension": "price_quotes",
                "evidence_ref": "BINANCE_SPOT_PUBLIC",
                "event_role": "PRICE_RESPONSE_OBSERVED",
            },
            {
                "sequence": 6,
                "occurred_at": "2026-09-02T04:40:00+09:00",
                "evidence_dimension": "cross_exchange_acceptance",
                "evidence_ref": "CLAIM_CROSS_EXCHANGE_ACCEPTANCE",
                "event_role": "RETENTION_OBSERVED",
            },
        ]
        valid["report_semantics"]["rejected_competing_explanations"] = [
            {
                "hypothesis_id": hypothesis_id,
                "reason_class": contract._expected_rejected_reason(
                    valid, hypothesis_id
                ),
            }
            for hypothesis_id in valid["causal_attribution"][
                "rejected_hypothesis_ids"
            ]
        ]
        _apply_control(valid)
        self.assertEqual([], validate_candidate(valid))

        missing_component_cause = deepcopy(valid)
        selected_mixed = next(
            item
            for item in missing_component_cause["causal_attribution"]["hypotheses"]
            if item["hypothesis_id"] == "H_MIXED"
        )
        selected_mixed["causal_timeline"] = [
            event for event in selected_mixed["causal_timeline"]
            if event["evidence_dimension"] != "oi_timeline"
        ]
        for sequence, event in enumerate(
            selected_mixed["causal_timeline"], start=1
        ):
            event["sequence"] = sequence
        _apply_control(missing_component_cause)
        self.assertTrue(any(
            "driver_regime=MIXED selected hypothesis must reference bound VERIFIED "
            "DERIVATIVES_LEVERAGE oi_timeline" in error
            for error in validate_candidate(missing_component_cause)
        ))

    def test_rejected_reason_and_open_questions_bind_actual_evidence(self) -> None:
        stale_reason = make_bundle()
        verify_dimensions(
            stale_reason,
            "cross_exchange_acceptance", "spot_flow", "capital_source",
            "derivatives_flow", "funding_basis", "oi_timeline",
        )
        select_driver(stale_reason, "DERIVATIVES_LEVERAGE")
        for item in stale_reason["report_semantics"][
            "rejected_competing_explanations"
        ]:
            if item["hypothesis_id"] == "H_SPOT":
                item["reason_class"] = "NO_ACCEPTANCE_SEQUENCE"
        stale_reason["report_semantics"]["material_uncertainties"] = [
            "MACRO_TIMELINE"
        ]
        _apply_control(stale_reason)
        self.assertTrue(
            any(
                "actual evidence" in error
                for error in validate_candidate(stale_reason)
            )
        )

        resolved_as_open = make_bundle()
        verify_dimensions(
            resolved_as_open,
            "cross_exchange_acceptance", "spot_flow", "capital_source",
            "oi_timeline",
        )
        resolved_as_open["decision_evidence_status"] = "RESOLVED"
        select_driver(resolved_as_open, "CRYPTO_NATIVE_SPOT")
        _apply_control(resolved_as_open)
        errors = validate_candidate(resolved_as_open)
        self.assertTrue(any("contradicts VERIFIED evidence" in error for error in errors))
        self.assertTrue(any("is already VERIFIED" in error for error in errors))

    def test_structural_report_level_cannot_omit_its_price_band(self) -> None:
        bundle = make_bundle()
        del bundle["report_semantics"]["decision_relevant_levels_and_sizes"][0][
            "price_band_u"
        ]
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("exactly one of price_u or price_band_u" in error for error in errors))
        self.assertTrue(any("must include its price_band_u" in error for error in errors))

    def test_decimal_invalid_operation_is_a_structured_cli_failure(self) -> None:
        with mock.patch.object(
            contract, "_load_json", side_effect=InvalidOperation("decimal trap")
        ), mock.patch.object(
            sys, "argv", [str(SCRIPTS / "strategy_contract.py"), "validate-bundle", "--input", "x"]
        ), mock.patch.object(contract, "_emit") as emit:
            self.assertEqual(2, contract.main())
        emitted = emit.call_args.args[0]
        self.assertFalse(emitted["ok"])
        self.assertTrue(emitted["hard_failure"])
        self.assertIn("decimal trap", emitted["errors"][0])

    def test_report_sections_follow_decision_first_contract_order(self) -> None:
        body = contract.render_report_body(make_bundle()["report_semantics"])
        expected_labels = [
            contract.REPORT_VISIBLE_LABELS[field]
            for field in contract.REPORT_SEMANTIC_FIELDS
        ]
        positions = [body.index(label + "：") for label in expected_labels]
        self.assertEqual(sorted(positions), positions)
        self.assertEqual(
            [
                "综合判断", "主要原因", "未采用解释", "组合影响",
                "关键不确定性", "下一项改变判断的证据", "重大风险",
                "关键价位与规模",
            ],
            expected_labels,
        )

    def test_unavailable_receipt_cannot_silence_decisive_source_conflict(self) -> None:
        bundle = make_bundle()
        observation = unavailable_observation_from(bundle)
        observation["observation"][
            "source_health_class"
        ] = "DECISIVE_SOURCE_CONFLICT"
        with revision3_baseline() as baseline:
            receipt = {
                "schema": contract.UNAVAILABLE_RECEIPT_SCHEMA,
                "deep_eval_status": "UNAVAILABLE",
                "retry_required": True,
                "receipt_phase": "FINAL",
                "monitor_state": "A",
                "attention_planned": False,
                "project_patch_required": False,
                "report_required": False,
                "local_readback_state": "NOT_REQUIRED",
                "report_validation_state": "NOT_REQUIRED",
                "cloud_delivery_state": "NOT_REQUIRED",
                "report_visible": False,
                "observation_fingerprint": unavailable_observation_fingerprint(
                    observation
                ),
                "reused_decision_fingerprint": baseline.binding[
                    "decision_fingerprint"
                ],
                "reused_report_fingerprint": baseline.binding[
                    "report_fingerprint"
                ],
            }
            errors = contract.validate_unavailable_receipt(
                receipt, baseline, observation, contract.load_fact_owner_snapshot(),
                require_owner_baseline=False
            )
        self.assertTrue(any("cannot silence" in error for error in errors))

    def test_normal_bundle_cannot_silence_decisive_source_conflict(self) -> None:
        bundle = make_bundle()
        bundle["observation"][
            "source_health_class"
        ] = "DECISIVE_SOURCE_CONFLICT"
        _apply_control(bundle)
        self.assertTrue(
            any(
                "source health and major_risk must agree" in error
                for error in validate_candidate(bundle)
            )
        )

    def test_cli_non_object_bundle_inputs_fail_structurally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle_path = root / "bundle.json"
            empty = root / "empty.md"
            bundle_path.write_text("[]", encoding="utf-8")
            empty.write_text("", encoding="utf-8")
            commands = (
                [
                    "render-projection-sidecar", "--replay-only", "--bundle", str(bundle_path),
                    "--input", str(empty), "--role", "STATE",
                ],
                [
                    "validate-projection", "--bundle", str(bundle_path),
                    "--state", str(empty), "--plan", str(empty),
                ],
            )
            for arguments in commands:
                with self.subTest(command=arguments[0]):
                    result = subprocess.run(
                        [sys.executable, str(SCRIPTS / "strategy_contract.py"), *arguments],
                        check=False, capture_output=True, text=True,
                    )
                    self.assertEqual(2, result.returncode, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertFalse(payload["ok"])
                    self.assertTrue(payload["hard_failure"])
                    self.assertEqual("", result.stderr)

    def test_previous_fingerprints_cannot_fake_a_or_force_d(self) -> None:
        changed = make_bundle()
        changed["trade_candidates"][0]["entries"][0]["eligibility"] = "ELIGIBLE_FOR_REVIEW"
        changed_fp = contract.compute_fingerprints(changed)
        changed["previous_fingerprints"] = {
            "decision_fingerprint": changed_fp["decision_fingerprint"],
            "report_fingerprint": changed_fp["report_fingerprint"],
        }
        _apply_control(changed)
        self.assertEqual("A", changed["control"]["monitor_state"])
        self.assertTrue(
            any("actual revision-3 projection" in error for error in validate_candidate(changed))
        )

        unchanged = make_bundle()
        unchanged["previous_fingerprints"]["decision_fingerprint"] = "sha256:" + "9" * 64
        _apply_control(unchanged)
        self.assertEqual("D", unchanged["control"]["monitor_state"])
        self.assertTrue(
            any("actual revision-3 projection" in error for error in validate_candidate(unchanged))
        )

    def test_control_derivation_requires_explicit_real_baseline(self) -> None:
        bundle = make_bundle()
        with self.assertRaisesRegex(contract.GuardError, "actual revision-3"):
            contract.derive_control(bundle)
        with revision3_baseline() as baseline:
            self.assertEqual(
                "A",
                contract.derive_control(
                    bundle, previous_baseline=baseline,
                    fact_owner_snapshot=contract.load_fact_owner_snapshot(),
                    require_owner_baseline=False,
                )["monitor_state"],
            )

    def test_postwrite_projection_cannot_impersonate_previous_snapshot(self) -> None:
        bundle = make_bundle()
        set_candidate_eligible(bundle, 0, report_relevant=True)
        _apply_control(bundle)
        new_state, new_plan, _ = ProjectionAndReportTests().projection_texts(bundle)
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "new-state.md"
            plan_path = Path(temp_dir) / "new-plan.md"
            state_path.write_text(new_state, encoding="utf-8")
            plan_path.write_text(new_plan, encoding="utf-8")
            new_baseline = contract.load_revision3_baseline(
                str(state_path), str(plan_path), require_owner_paths=False
            )
            errors = contract.validate_bundle(
                bundle, previous_baseline=new_baseline,
                fact_owner_snapshot=contract.load_fact_owner_snapshot(),
                require_owner_baseline=False,
            )
        self.assertTrue(any("previous_projection_baseline" in error for error in errors))

    def test_revision3_baseline_detects_body_sidecar_and_role_tampering(self) -> None:
        state, plan = default_revision3_baseline_documents()
        variants = [
            (state.replace("- canonical:", "- forged:", 1), plan),
            (state.replace('"document_role":"STATE"', '"document_role":"PLAN"', 1), plan),
            (
                state.replace(
                    '"selected_hypothesis_id":"H_UNRESOLVED"',
                    '"selected_hypothesis_id":"H_SPOT"',
                    1,
                ),
                plan,
            ),
        ]
        for bad_state, good_plan in variants:
            with self.subTest():
                with tempfile.TemporaryDirectory() as temp_dir:
                    state_path = Path(temp_dir) / "state.md"
                    plan_path = Path(temp_dir) / "plan.md"
                    state_path.write_text(bad_state, encoding="utf-8")
                    plan_path.write_text(good_plan, encoding="utf-8")
                    with self.assertRaisesRegex(contract.GuardError, "invalid revision-3 baseline"):
                        contract.load_revision3_baseline(
                            str(state_path), str(plan_path), require_owner_paths=False
                        )

    def test_report_body_is_natural_code_free_and_decimal_canonical(self) -> None:
        first = deepcopy(make_bundle()["report_semantics"])
        first["decision_relevant_levels_and_sizes"] = [
            {"level_id": "V0", "price_u": "77000.000", "amount_u": "0.000"}
        ]
        second = deepcopy(first)
        second["decision_relevant_levels_and_sizes"][0].update(
            {"price_u": 77000, "amount_u": 0}
        )
        first_body = contract.render_report_body(first)
        second_body = contract.render_report_body(second)
        self.assertEqual(first_body, second_body)
        self.assertEqual(contract._fingerprint(first), contract._fingerprint(second))
        self.assertNotIn('"', first_body)
        for code in contract.REPORT_CODE_DISPLAY:
            self.assertNotIn(code, first_body)

    def test_report_codes_are_rejected_outside_their_field_context(self) -> None:
        report = deepcopy(make_bundle()["report_semantics"])
        report["overall_user_decision"] = "V0"
        with self.assertRaisesRegex(contract.GuardError, "controlled report semantic code"):
            contract.render_report_body(report)

    def test_report_and_value_zone_numbers_are_finite_positive_and_ordered(self) -> None:
        for value in ("NaN", "Infinity", "-1"):
            bundle = make_bundle()
            bundle["value_zones"][0]["band"]["lower_u"] = value
            _apply_control(bundle)
            self.assertTrue(validate_candidate(bundle), value)
        report = deepcopy(make_bundle()["report_semantics"])
        report["decision_relevant_levels_and_sizes"] = [{
            "level_id": "V0",
            "price_band_u": {"lower_u": "78000", "upper_u": "77000"},
            "amount_u": "-1",
        }]
        errors: list[str] = []
        contract._validate_report_semantics_shape(report, "report", errors)
        self.assertTrue(any("non-negative" in error for error in errors))
        self.assertTrue(any("lower_u <= upper_u" in error for error in errors))

    def test_projection_metadata_enums_and_times_cannot_be_sidecar_self_asserted(self) -> None:
        state, plan, bundle = ProjectionAndReportTests().projection_texts()
        cases = (
            ("trigger_type: SCHEDULED_STRATEGY_BOUNDARY", "trigger_type: TOTALLY_INVALID", "trigger_type"),
            (
                f"derived_at: {bundle['runtime']['pricing_as_of']}",
                "derived_at: yesterday", "derived_at",
            ),
            ("account_fact_source: LOCAL_CONFIRMED_RECORDS", "account_fact_source: INTERNET", "account_fact_source"),
        )
        for old, new, expected in cases:
            with self.subTest(expected=expected):
                body = contract._remove_projection_sidecar(state).replace(old, new, 1)
                bad_state = body.rstrip() + "\n\n" + contract.render_projection_sidecar(
                    bundle, body, "STATE"
                ) + "\n"
                errors = validate_projection_candidate(bad_state, plan, bundle)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_verified_quote_venues_match_sources_raw_prices_and_relations_exactly(self) -> None:
        bundle = make_bundle()
        bundle["observation"]["raw_prices"]["fabricated"] = "99999"
        bundle["observation"]["quote_relation_by_venue"]["fabricated"] = "ABOVE_ZONE"
        bundle["observation"]["price_relation"] = "CROSS_VENUE_DIVERGENCE"
        bundle["observation"]["cross_venue_divergence_class"] = "ACTIVE"
        bundle["observation"]["relation_transition"] = "DIVERGED"
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("must match exactly" in error for error in errors))

    def test_raw_price_time_and_primary_zone_relation_are_recomputed(self) -> None:
        for mutate, expected in (
            (lambda b: b["observation"]["raw_prices"].update({"binance": "NaN"}), "finite"),
            (lambda b: b["observation"].update({"observed_at": "not-a-time"}), "ISO-8601"),
            (lambda b: b["observation"]["raw_prices"].update({"binance": "90000"}), "raw_prices"),
        ):
            with self.subTest(expected=expected):
                bundle = make_bundle()
                mutate(bundle)
                _apply_control(bundle)
                self.assertTrue(any(expected in error for error in validate_candidate(bundle)))

    def test_closed_ohlc_is_validated_but_raw_values_do_not_churn_observation_fp(self) -> None:
        first = make_bundle()
        first["observation"]["closed_bar_relation_sequence"] = [{
            "open_u": "77600", "high_u": "77800", "low_u": "77500",
            "close_u": "77700", "boundary_u": "77500",
            "open_relation": "AT_OR_ABOVE_BOUNDARY",
            "high_relation": "AT_OR_ABOVE_BOUNDARY",
            "low_relation": "AT_OR_ABOVE_BOUNDARY",
            "close_relation": "AT_OR_ABOVE_BOUNDARY",
        }]
        second = deepcopy(first)
        second["observation"]["closed_bar_relation_sequence"][0].update({
            "open_u": "77650.0", "high_u": "77900", "low_u": "77550",
            "close_u": "77800", "boundary_u": "77500.000",
        })
        self.assertEqual(
            contract.compute_fingerprints(first)["observation_fingerprint"],
            contract.compute_fingerprints(second)["observation_fingerprint"],
        )
        broken = deepcopy(first)
        broken["observation"]["closed_bar_relation_sequence"][0]["low_u"] = "78000"
        _apply_control(broken)
        self.assertTrue(any("low_u is inconsistent" in error for error in validate_candidate(broken)))

    def test_malformed_url_and_invalid_utf8_fail_as_structured_errors(self) -> None:
        bundle = make_bundle()
        bundle["evidence_matrix"]["price_quotes"]["sources"][0][
            "endpoint_url"
        ] = "https://api.binance.com:bad/api/v3/ticker/price?symbol=BTCUSDT"
        _apply_control(bundle)
        self.assertTrue(validate_candidate(bundle))
        with tempfile.NamedTemporaryFile("wb", suffix=".json") as handle:
            handle.write(b"\xff")
            handle.flush()
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "strategy_contract.py"),
                    "fingerprints", "--input", handle.name,
                ],
                check=False, capture_output=True, text=True,
            )
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["hard_failure"])
        self.assertEqual("", result.stderr)

    def test_unavailable_receipt_binds_owner_and_light_observation(self) -> None:
        bundle = make_bundle()
        observation = unavailable_observation_from(bundle)
        receipt = {
            "schema": contract.UNAVAILABLE_RECEIPT_SCHEMA,
            "deep_eval_status": "UNAVAILABLE", "retry_required": True,
            "receipt_phase": "FINAL", "monitor_state": "A",
            "attention_planned": False, "project_patch_required": False,
            "report_required": False, "local_readback_state": "NOT_REQUIRED",
            "report_validation_state": "NOT_REQUIRED",
            "cloud_delivery_state": "NOT_REQUIRED", "report_visible": False,
            "observation_fingerprint": bundle["fingerprints"]["observation_fingerprint"],
            "reused_decision_fingerprint": bundle["previous_fingerprints"]["decision_fingerprint"],
            "reused_report_fingerprint": bundle["previous_fingerprints"]["report_fingerprint"],
        }
        receipt["observation_fingerprint"] = unavailable_observation_fingerprint(
            observation
        )
        with revision3_baseline() as baseline:
            self.assertEqual([], contract.validate_unavailable_receipt(
                receipt, baseline, observation, contract.load_fact_owner_snapshot(),
                require_owner_baseline=False
            ))
            forged = deepcopy(receipt)
            forged["reused_decision_fingerprint"] = "sha256:" + "7" * 64
            self.assertTrue(contract.validate_unavailable_receipt(
                forged, baseline, observation, contract.load_fact_owner_snapshot(),
                require_owner_baseline=False
            ))
            changed_observation = deepcopy(observation)
            changed_observation["observation"]["raw_prices"]["binance"] = "76000"
            self.assertTrue(
                contract.validate_unavailable_receipt(
                    receipt, baseline, changed_observation,
                    contract.load_fact_owner_snapshot(),
                    require_owner_baseline=False,
                )
            )

    def test_cloud_receipt_is_exact_not_trimmed(self) -> None:
        bundle = make_bundle()
        bundle["report_semantics"]["overall_user_decision"] = "REVIEW_ELIGIBLE_CANDIDATE"
        set_candidate_eligible(bundle, 0, report_relevant=True)
        _apply_control(bundle)
        report = contract.render_report_body(bundle["report_semantics"])
        state, plan, _ = ProjectionAndReportTests().projection_texts(bundle)
        cloud_readback = {
            "schema": contract.CLOUD_READBACK_SCHEMA,
            "visible_report_text": report,
            "assistant_responses": ["  已收录\n"],
        }
        receipt = receipt_for(bundle)
        receipt.update({
            "cloud_delivery_state": "DELIVERED", "report_visible": True,
            "report_body_fingerprint": contract.content_fingerprint(report),
            "cloud_visible_body_fingerprint": contract.content_fingerprint(report),
            "cloud_readback_fingerprint": contract._fingerprint(cloud_readback),
        })
        errors = validate_receipt_candidate(
            receipt, bundle, report, state, plan,
            cloud_readback=cloud_readback,
        )
        self.assertTrue(any("exactly one full response" in error for error in errors))

    def test_recursive_exact_key_allowlists_reject_nested_smuggling(self) -> None:
        mutations = []

        def add(name, mutate):
            mutations.append((name, mutate))

        add("runtime", lambda b: b["runtime"].update({"cloud_delivery_state": "DELIVERED"}))
        add("observation", lambda b: b["observation"].update({"strategy_decision": "BUY"}))
        add("source", lambda b: b["evidence_matrix"]["price_quotes"]["sources"][0].update({"origin_hint": "COINBASE"}))
        add("zone_band", lambda b: b["value_zones"][0]["band"].update({"close": "99999"}))
        add("hypothesis", lambda b: b["causal_attribution"]["hypotheses"][0].update({"raw_chain": "hidden"}))
        add("portfolio", lambda b: b["portfolio_decision"]["new_spot"].update({"final_order": True}))
        add("amount_basis", lambda b: b["trade_candidates"][0]["entries"][0]["amount_basis"].update({"approval": "DONE"}))
        add("report_item", lambda b: b["report_semantics"]["rejected_competing_explanations"][0].update({"hidden": "override"}))
        add("control", lambda b: b["control"].update({"attention_emitted": True}))
        for name, mutate in mutations:
            with self.subTest(name=name):
                bundle = make_bundle()
                mutate(bundle)
                self.assertTrue(
                    any("not allowed" in error or "final-run fact" in error for error in validate_candidate(bundle))
                )

    def test_nonquote_public_source_requires_https_origin_payload_and_venue(self) -> None:
        bundle = make_bundle()
        verify_dimensions(bundle, "spot_flow")
        source = bundle["evidence_matrix"]["spot_flow"]["sources"][0]
        source["endpoint_url"] = "http://data.example.com/flow"
        source["canonical_origin"] = "attacker.example"
        del source["venue"]
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("legal public HTTPS" in error for error in errors))
        self.assertTrue(any("canonical_origin" in error for error in errors))
        self.assertTrue(any("venue" in error for error in errors))

    def test_public_source_accepts_only_public_dns_shaped_hosts(self) -> None:
        blocked = (
            ("https://localhost/flow", "localhost"),
            ("https://node.localhost/flow", "node.localhost"),
            ("https://node.local/flow", "node.local"),
            ("https://market.internal/flow", "market.internal"),
            ("https://service.test/flow", "service.test"),
            ("https://127.0.0.1.nip.io/flow", "127.0.0.1.nip.io"),
            ("https://169.254.169.254.sslip.io/flow", "169.254.169.254.sslip.io"),
            ("https://127.0.0.1.attacker.example.org/flow", "127.0.0.1.attacker.example.org"),
            (
                "https://cdn.0177.000.000.001.market.example.org/flow",
                "cdn.0177.000.000.001.market.example.org",
            ),
            ("https://127.0.0.1/flow", "127.0.0.1"),
            ("https://10.0.0.8/flow", "10.0.0.8"),
            ("https://169.254.169.254/flow", "169.254.169.254"),
            ("https://0.0.0.0/flow", "0.0.0.0"),
            ("https://224.0.0.1/flow", "224.0.0.1"),
            ("https://192.0.2.1/flow", "192.0.2.1"),
            ("https://8.8.8.8/flow", "8.8.8.8"),
            ("https://[::1]/flow", "::1"),
            ("https://[fe80::1]/flow", "fe80::1"),
            ("https://[fc00::1]/flow", "fc00::1"),
            ("https://[::]/flow", "::"),
            ("https://[ff02::1]/flow", "ff02::1"),
            ("https://[2001:db8::1]/flow", "2001:db8::1"),
            (
                "https://[2606:4700:4700::1111]/flow",
                "2606:4700:4700::1111",
            ),
            (
                "https://cdn.8.8.8.8.market.example.org/flow",
                "cdn.8.8.8.8.market.example.org",
            ),
        )
        for endpoint, origin in blocked:
            with self.subTest(endpoint=endpoint):
                bundle = make_bundle()
                verify_dimensions(bundle, "spot_flow")
                source = bundle["evidence_matrix"]["spot_flow"]["sources"][0]
                source["endpoint_url"] = endpoint
                source["canonical_origin"] = origin
                _apply_control(bundle)
                self.assertTrue(any(
                    "legal public HTTPS provenance URL" in error
                    for error in validate_candidate(bundle)
                ))

        for endpoint, origin in (
            ("https://market-data.example.org/flow", "market-data.example.org"),
            ("https://data.coindesk.com/flow", "data.coindesk.com"),
        ):
            with self.subTest(endpoint=endpoint):
                bundle = make_bundle()
                verify_dimensions(bundle, "spot_flow")
                source = bundle["evidence_matrix"]["spot_flow"]["sources"][0]
                source["endpoint_url"] = endpoint
                source["canonical_origin"] = origin
                bundle["report_semantics"]["material_uncertainties"] = [
                    "OI_TIMELINE", "CAPITAL_SOURCE",
                ]
                _apply_control(bundle)
                self.assertEqual([], validate_candidate(bundle))

    def test_all_ip_literals_are_rejected_in_helper_and_prewrite(self) -> None:
        ip_literals = {
            # blind_release_v12 outer-global dotted-tail/5efe reproductions
            "blind_dotted_tail_loopback": "2001:4860:4860::127.0.0.1",
            "blind_5efe_loopback": "2001:4860:4860::5efe:127.0.0.1",
            "blind_5efe_link_local": "2001:4860:4860::5efe:169.254.169.254",
            "blind_5efe_private": "2001:4860:4860::5efe:10.0.0.1",
            "6to4_loopback": "2002:7f00:1::",
            "6to4_link_local": "2002:a9fe:a9fe::",
            "6to4_private_10": "2002:a00:1::",
            "6to4_private_192": "2002:c0a8:101::",
            "6to4_public_conservatively_blocked": "2002:808:808::",
            "ipv4_mapped_loopback": "::ffff:127.0.0.1",
            "ipv4_mapped_public_conservatively_blocked": "::ffff:8.8.8.8",
            "teredo_loopback_client": "2001:0:808:808:0:0:80ff:fffe",
            "teredo_private_client": "2001:0:808:808:0:0:f5ff:fffe",
            "teredo_link_local_client": "2001:0:808:808:0:0:5601:5601",
            "ipv4_compatible_loopback": "::7f00:1",
            "native_global_ipv6": "2606:4700:4700::1111",
            "global_ipv4": "8.8.8.8",
        }
        for label, literal in ip_literals.items():
            with self.subTest(helper=label):
                self.assertFalse(contract._is_public_network_hostname(literal))

        self.assertTrue(
            contract._is_public_network_hostname("market-data.example.org")
        )
        for hostname in (
            "localhost", "node.localhost", "node.local", "market.internal",
            "127.0.0.1.nip.io", "169.254.169.254.sslip.io",
            "127.0.0.1.attacker.example.org",
            "cdn.8.8.8.8.market.example.org",
            "v1.8.8.8", "vf.dead.beef",
        ):
            with self.subTest(rejected_dns_or_mapping_name=hostname):
                self.assertFalse(contract._is_public_network_hostname(hostname))

        for label, literal in ip_literals.items():
            with self.subTest(prewrite_source=label):
                bundle = make_bundle()
                verify_dimensions(bundle, "spot_flow")
                source = bundle["evidence_matrix"]["spot_flow"]["sources"][0]
                bracketed = ":" in literal
                source["endpoint_url"] = (
                    f"https://[{literal}]/flow" if bracketed
                    else f"https://{literal}/flow"
                )
                source["canonical_origin"] = literal
                bundle["report_semantics"]["material_uncertainties"] = [
                    "OI_TIMELINE", "CAPITAL_SOURCE",
                ]
                _apply_control(bundle)
                self.assertTrue(any(
                    "legal public HTTPS provenance URL" in error
                    for error in validate_candidate(bundle)
                ))

        for ipvfuture in ("v1.8.8.8", "vF.dead.beef"):
            with self.subTest(prewrite_ipvfuture=ipvfuture):
                bundle = make_bundle()
                verify_dimensions(bundle, "spot_flow")
                source = bundle["evidence_matrix"]["spot_flow"]["sources"][0]
                source["endpoint_url"] = f"https://[{ipvfuture}]/flow"
                source["canonical_origin"] = ipvfuture.casefold()
                bundle["report_semantics"]["material_uncertainties"] = [
                    "OI_TIMELINE", "CAPITAL_SOURCE",
                ]
                _apply_control(bundle)
                self.assertTrue(any(
                    "legal public HTTPS provenance URL" in error
                    for error in validate_candidate(bundle)
                ))

    def test_unresolved_parent_cannot_carry_verified_child_claim(self) -> None:
        bundle = make_bundle()
        verified = make_bundle()
        verify_dimensions(verified, "spot_flow")
        bundle["evidence_matrix"]["spot_flow"]["evidence_claims"] = deepcopy(
            verified["evidence_matrix"]["spot_flow"]["evidence_claims"]
        )
        _apply_control(bundle)
        self.assertTrue(
            any("only under a VERIFIED parent" in error for error in validate_candidate(bundle))
        )

    def test_claim_dimension_source_time_and_metric_are_parent_bound(self) -> None:
        bundle = make_bundle()
        verify_dimensions(bundle, "spot_flow")
        claim = bundle["evidence_matrix"]["spot_flow"]["evidence_claims"][0]
        claim["claim_type"] = "OI_TIMELINE"
        claim["source_ids"] = ["UNRELATED"]
        claim["time_range"] = time_range()
        claim["metric"] = "POINT_VALUE"
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        for expected in ("not legal for dimension", "source_ids must equal", "time_range must equal", "metric must be an object"):
            self.assertTrue(any(expected in error for error in errors), expected)

    def test_intrinsic_spot_direction_cannot_contradict_claim_type(self) -> None:
        bundle = make_bundle()
        verify_dimensions(bundle, "spot_flow")
        claim = bundle["evidence_matrix"]["spot_flow"]["evidence_claims"][0]
        claim["claim_type"] = "NATIVE_SPOT_BUY_FLOW"
        claim["direction"] = "SELL"
        _apply_control(bundle)
        self.assertTrue(
            any("direction conflicts" in error for error in validate_candidate(bundle))
        )

    def test_quote_registry_rejects_wrong_path_and_non_ticker_endpoint(self) -> None:
        for endpoint in (
            "https://api.binance.com/api/v3/time?symbol=BTCUSDT",
            "https://api.kraken.com/0/public/Time?pair=XBTUSD",
        ):
            with self.subTest(endpoint=endpoint):
                bundle = make_bundle()
                bundle["evidence_matrix"]["price_quotes"]["sources"][0]["endpoint_url"] = endpoint
                _apply_control(bundle)
                self.assertTrue(
                    any("fixed provenance registry" in error for error in validate_candidate(bundle))
                )

    def test_quote_registry_requires_exact_raw_urls_without_parser_equivalence(self) -> None:
        registered = (
            (
                0,
                "api.binance.com",
                "/api/v3/ticker/price",
                "symbol",
                "BTCUSDT",
            ),
            (1, "api.kraken.com", "/0/public/Ticker", "pair", "XBTUSD"),
            (
                2,
                "api.exchange.coinbase.com",
                "/products/BTC-USD/ticker",
                None,
                None,
            ),
        )
        for source_index, host, path, query_key, query_value in registered:
            query = (
                f"{query_key}={query_value}"
                if query_key is not None and query_value is not None else ""
            )
            base = f"https://{host}{path}" + (f"?{query}" if query else "")
            valid_source = make_bundle()["evidence_matrix"]["price_quotes"][
                "sources"
            ][source_index]
            self.assertEqual(base, valid_source["endpoint_url"])
            self.assertIsNotNone(
                contract._registered_quote_provenance(valid_source)
            )

            path_tail = path.rsplit("/", 1)[1]
            encoded_path_tail = (
                f"%{ord(path_tail[0]):02X}{path_tail[1:]}"
            )
            variants = {
                "http_scheme": base.replace("https://", "http://", 1),
                "uppercase_scheme": base.replace("https://", "HTTPS://", 1),
                "uppercase_host": base.replace(host, host.upper(), 1),
                "trailing_dot_host": base.replace(host, host + ".", 1),
                "userinfo": base.replace(host, "public-user@" + host, 1),
                "explicit_default_port": base.replace(host, host + ":443", 1),
                "noncanonical_port": base.replace(host, host + ":0443", 1),
                "wrong_port": base.replace(host, host + ":444", 1),
                "trailing_path_slash": base.replace(path, path + "/", 1),
                "duplicate_path_slash": base.replace(path, path + "//", 1),
                "encoded_path": base.replace(
                    path_tail, encoded_path_tail, 1
                ),
                "path_parameter": base.replace(path, path + ";format=json", 1),
                "fragment": base + "#ticker",
            }
            if query:
                encoded_key = f"%{ord(query_key[0]):02X}{query_key[1:]}"
                encoded_value = query_value.replace("U", "%55", 1)
                prefix = f"https://{host}{path}?"
                variants.update({
                    "encoded_query_key": prefix + encoded_key + "=" + query_value,
                    "encoded_query_value": prefix + query_key + "=" + encoded_value,
                    "empty_value": prefix + query_key + "=",
                    "missing_query": f"https://{host}{path}",
                    "trailing_separator": base + "&",
                    "leading_separator": prefix + "&" + query,
                    "duplicate_separator": base + "&&extra=value",
                    "duplicate_key": base + "&" + query,
                    "extra_key": base + "&extra=value",
                    "semicolon_separator": base + ";extra=value",
                })
            else:
                variants.update({
                    "empty_query_marker": base + "?",
                    "extra_query": base + "?extra=value",
                    "empty_query_segment": base + "?&",
                })

            for label, endpoint in variants.items():
                with self.subTest(venue=host, mutation=label):
                    bundle = make_bundle()
                    bundle["evidence_matrix"]["price_quotes"]["sources"][
                        source_index
                    ]["endpoint_url"] = endpoint
                    _apply_control(bundle)
                    errors = validate_candidate(bundle)
                    self.assertTrue(
                        any(
                            "fixed provenance registry" in error
                            for error in errors
                        ),
                        errors,
                    )

    def test_widen_rejects_point_quote_claim_even_when_typed(self) -> None:
        bundle = make_bundle()
        bundle["path_decision"] = "WIDEN"
        quotes = bundle["evidence_matrix"]["price_quotes"]
        quotes["evidence_claims"] = [{
            "claim_type": "DISTRIBUTION_EXPANSION",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in quotes["sources"]],
            "time_range": deepcopy(quotes["time_range"]),
            "metric": metric(periods=1),
        }]
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("not legal for dimension price_quotes" in error for error in errors))
        self.assertTrue(any("multi-period structural" in error for error in errors))

    def test_receipt_is_bound_to_bundle_and_actual_readbacks(self) -> None:
        bundle = make_bundle()
        receipt = receipt_for(bundle)
        receipt["bundle_identity"] = "sha256:" + "0" * 64
        receipt["decision_class"] = "FINAL_EVENT"
        errors = validate_receipt_candidate(receipt, bundle)
        self.assertTrue(any("bundle_identity" in error for error in errors))
        self.assertTrue(any("decision_class" in error for error in errors))

        changed = make_bundle()
        set_candidate_eligible(changed, 0, report_relevant=True)
        _apply_control(changed)
        patched = receipt_for(changed)
        self.assertTrue(
            any("actual --state and --plan" in error for error in validate_receipt_candidate(patched, changed))
        )

    def test_receipt_silent_pending_and_delivered_fields_are_mutually_exclusive(self) -> None:
        bundle = make_bundle()
        receipt = receipt_for(bundle)
        receipt["report_body_fingerprint"] = "sha256:" + "a" * 64
        self.assertTrue(any("exactly match" in error for error in validate_receipt_candidate(receipt, bundle)))

        changed = make_bundle()
        set_candidate_eligible(changed, 0, report_relevant=True)
        changed["report_semantics"]["overall_user_decision"] = "REVIEW_ELIGIBLE_CANDIDATE"
        _apply_control(changed)
        report = contract.render_report_body(changed["report_semantics"])
        state, plan, _ = ProjectionAndReportTests().projection_texts(changed)
        pending = receipt_for(changed)
        pending["report_body_fingerprint"] = contract.content_fingerprint(report)
        pending["cloud_readback_fingerprint"] = "sha256:" + "b" * 64
        self.assertTrue(
            any("exactly match" in error for error in validate_receipt_candidate(
                pending, changed, report, state, plan
            ))
        )

        delivered = receipt_for(changed)
        cloud_readback = {
            "schema": contract.CLOUD_READBACK_SCHEMA,
            "visible_report_text": report + "\n二次解释",
            "assistant_responses": ["已收录"],
        }
        delivered.update({
            "cloud_delivery_state": "DELIVERED",
            "report_visible": True,
            "report_body_fingerprint": contract.content_fingerprint(report),
            "cloud_visible_body_fingerprint": contract.content_fingerprint(
                cloud_readback["visible_report_text"]
            ),
            "cloud_readback_fingerprint": contract._fingerprint(cloud_readback),
        })
        errors = validate_receipt_candidate(
            delivered, changed, report, state, plan,
            cloud_readback=cloud_readback,
        )
        self.assertTrue(any("exactly equal local report" in error for error in errors))

    def test_major_risk_attention_is_derived_from_bundle(self) -> None:
        bundle = make_bundle()
        set_candidate_eligible(bundle, 0, report_relevant=True)
        bundle["major_risk"] = {
            "active": True,
            "kinds": ["SOURCE_CONFLICT"],
            "portfolio_effect": "FREEZE_INCREMENTAL_RISK",
        }
        bundle["report_semantics"]["overall_user_decision"] = "FREEZE_INCREMENTAL_RISK"
        bundle["report_semantics"]["user_visible_major_risk"] = "SOURCE_CONFLICT"
        _apply_control(bundle)
        self.assertEqual("MAJOR_RISK", bundle["control"]["attention_level"])
        receipt = receipt_for(bundle)
        receipt["attention_level"] = "CHANGE_NOTICE"
        self.assertTrue(
            any("attention_level" in error for error in validate_receipt_candidate(receipt, bundle))
        )

    def test_migration_needs_full_immutable_baseline(self) -> None:
        bundle = make_bundle()
        bundle["write_transition"] = "MIGRATION"
        bundle["previous_strategy_revision"] = 2
        baseline = bind_revision2_artifacts(bundle)
        _apply_control(bundle)
        self.assertTrue(any("CLI-bound" in error for error in validate_candidate(bundle)))
        tampered = contract.MigrationBaseline(
            **{**baseline.__dict__, "state_text": baseline.state_text.replace("**strategy_revision**：2", "**strategy_revision**：1", 1)}
        )
        self.assertTrue(contract.validate_bundle(
            bundle, tampered,
            fact_owner_snapshot=contract.load_fact_owner_snapshot(),
        ))

    def test_migration_previous_decision_fingerprint_is_fixed_to_r2_documents(self) -> None:
        bundle = make_bundle()
        bundle["write_transition"] = "MIGRATION"
        bundle["previous_strategy_revision"] = 2
        baseline = bind_revision2_artifacts(bundle)
        bundle["previous_fingerprints"]["decision_fingerprint"] = "sha256:" + "4" * 64
        _apply_control(bundle)
        errors = contract.validate_bundle(
            bundle, baseline,
            fact_owner_snapshot=contract.load_fact_owner_snapshot(),
        )
        self.assertTrue(any("decision_fingerprint" in error for error in errors))

    def test_projection_rejects_comment_keywords_missing_or_tampered_sidecar(self) -> None:
        state, plan, bundle = ProjectionAndReportTests().projection_texts()
        no_sidecar = state.split(contract.PROJECTION_SIDECAR_START)[0]
        errors = validate_projection_candidate(no_sidecar, plan, bundle)
        self.assertTrue(any("exactly one canonical embedded sidecar" in error for error in errors))

        fake_state = state.replace("## observation\n", "<!-- ## observation\n伪造内容 -->\n", 1)
        errors = validate_projection_candidate(fake_state, plan, bundle)
        self.assertTrue(any("missing real section observation" in error for error in errors))

        tampered = state.replace(bundle["runtime"]["state_id"], "BTC-STATE-TAMPERED", 1)
        errors = validate_projection_candidate(tampered, plan, bundle)
        self.assertTrue(any("does not bind PRE_WRITE bundle" in error or "deterministic bundle recomputation" in error for error in errors))

    def test_report_renderer_is_injective_for_codes_and_delimiters(self) -> None:
        first = deepcopy(make_bundle()["report_semantics"])
        second = deepcopy(first)
        first["overall_user_decision"] = "NO_INCREMENTAL_POSITION_CHANGE"
        second["overall_user_decision"] = "UPWARD_VALUE_MIGRATION"
        self.assertNotEqual(contract.render_report_body(first), contract.render_report_body(second))
        unknown = deepcopy(first)
        unknown["overall_user_decision"] = "UNKNOWN_CODE_A"
        with self.assertRaisesRegex(contract.GuardError, "controlled report semantic code"):
            contract.render_report_body(unknown)
        third = deepcopy(first)
        fourth = deepcopy(first)
        third["primary_causal_explanation"] = "A；B：C。\nD"
        fourth["primary_causal_explanation"] = "A；B：C。\\nD"
        for invalid in (third, fourth):
            with self.assertRaisesRegex(contract.GuardError, "controlled report semantic code"):
                contract.render_report_body(invalid)

    def test_cli_render_report_outputs_directly_validatable_projection(self) -> None:
        semantics = make_bundle()["report_semantics"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
            json.dump(semantics, handle)
            handle.flush()
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "strategy_contract.py"),
                    "render-report",
                    "--replay-only",
                    "--projection",
                    handle.name,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [],
            contract.validate_report_text(payload["body"], payload["validation_projection"]),
        )

    def test_cli_render_sidecar_accepts_bound_write_before_snapshot(self) -> None:
        bundle = make_bundle()
        set_candidate_eligible(bundle, 0, report_relevant=True)
        _apply_control(bundle)
        candidate_state, _, _ = ProjectionAndReportTests().projection_texts(bundle)
        old_state, old_plan = default_revision3_baseline_documents()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = {
                "bundle": root / "bundle.json",
                "input": root / "candidate-state.md",
                "old_state": root / "old-state.md",
                "old_plan": root / "old-plan.md",
            }
            paths["bundle"].write_text(
                json.dumps(bundle, ensure_ascii=False), encoding="utf-8"
            )
            paths["input"].write_text(
                contract._remove_projection_sidecar(candidate_state), encoding="utf-8"
            )
            paths["old_state"].write_text(old_state, encoding="utf-8")
            paths["old_plan"].write_text(old_plan, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "strategy_contract.py"),
                    "render-projection-sidecar",
                    "--replay-only",
                    "--bundle",
                    str(paths["bundle"]),
                    "--input",
                    str(paths["input"]),
                    "--role",
                    "STATE",
                    "--previous-state-snapshot",
                    str(paths["old_state"]),
                    "--previous-plan-snapshot",
                    str(paths["old_plan"]),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertIn(contract.PROJECTION_SIDECAR_START, payload["sidecar_block"])

    def test_cli_help_explains_projection_envelopes_and_baseline_modes(self) -> None:
        checks = {
            "validate-bundle": ("--current-state", "MIGRATION"),
            "validate-receipt": ("A-only", "write-before"),
            "validate-report": ("exactly eight semantics", "validation_projection"),
            "render-report": ("JSON envelope", "body"),
            "render-projection-sidecar": ("sidecar_block", "JSON envelope"),
        }
        for command, expected in checks.items():
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, str(SCRIPTS / "strategy_contract.py"), command, "--help"],
                    check=False, capture_output=True, text=True,
                )
                self.assertEqual(0, result.returncode)
                for fragment in expected:
                    self.assertIn(fragment, result.stdout)

    def test_revision2_fixtures_remain_byte_exact_historical_artifacts(self) -> None:
        # Current Owners advance to v4. Frozen replay fixtures retain their original
        # content hashes; isolated tests must not depend on a live strategy version.
        for fixture in (FIXTURES / "revision2_state_1346.md", FIXTURES / "revision2_plan_1346.md"):
            with self.subTest(fixture=fixture.name):
                self.assertEqual(
                    contract.IMMUTABLE_REVISION2_ARTIFACT_HASHES[fixture],
                    contract.content_fingerprint(
                        fixture.read_bytes().decode("utf-8", errors="strict")
                    ),
                )
                self.assertNotEqual(
                    contract.content_fingerprint(
                        fixture.read_bytes().decode("utf-8", errors="strict")
                    ),
                    contract.content_fingerprint(
                        fixture.read_bytes().decode("utf-8", errors="strict").replace(
                            "\n", "\r\n"
                        )
                    ),
                )

    def test_future_sources_claims_and_causal_events_fail_closed(self) -> None:
        future_observation = make_bundle()
        future_observation["observation"][
            "observed_at"
        ] = "2099-01-01T00:00:00+09:00"
        _apply_control(future_observation)
        self.assertTrue(any(
            "observed_at cannot exceed runtime.pricing_as_of" in error
            for error in validate_candidate(future_observation)
        ))

        shifted_future = make_bundle()
        shifted_future["observation"][
            "observed_at"
        ] = "2099-01-01T00:01:00+09:00"
        shifted_future["runtime"]["pricing_as_of"] = "2099-01-01 00:01 JST"
        for source in shifted_future["evidence_matrix"]["price_quotes"]["sources"]:
            source["as_of"] = "2099-01-01T00:00:00+09:00"
            source["retrieved_at"] = "2099-01-01T00:00:30+09:00"
        shifted_future["evidence_matrix"]["price_quotes"]["time_range"] = {
            "kind": "POINT_IN_TIME",
            "start": "2099-01-01T00:00:00+09:00",
            "end": "2099-01-01T00:00:00+09:00",
        }
        shifted_future["evidence_matrix"]["account_impact"]["sources"][0][
            "as_of"
        ] = "2099-01-01T00:00:00+09:00"
        shifted_future["evidence_matrix"]["account_impact"]["sources"][0][
            "retrieved_at"
        ] = "2099-01-01T00:00:30+09:00"
        for hypothesis_item in shifted_future["causal_attribution"]["hypotheses"]:
            hypothesis_item["causal_timeline"][0][
                "occurred_at"
            ] = "2099-01-01T00:00:00+09:00"
        _apply_control(shifted_future)
        self.assertTrue(any(
            "runtime.pricing_as_of cannot be in the future" in error
            for error in validate_candidate(shifted_future)
        ))

        future_range = make_bundle()
        future_range["evidence_matrix"]["price_quotes"]["time_range"] = {
            "kind": "POINT_IN_TIME",
            "start": "2099-01-01T00:00:00+09:00",
            "end": "2099-01-01T00:00:00+09:00",
        }
        _apply_control(future_range)
        self.assertTrue(any(
            "cannot exceed observation.observed_at" in error
            for error in validate_candidate(future_range)
        ))

        future_source = make_bundle()
        future_source["evidence_matrix"]["price_quotes"]["sources"][0][
            "retrieved_at"
        ] = "2099-01-01T00:00:00+09:00"
        _apply_control(future_source)
        self.assertTrue(any(
            "retrieved_at cannot exceed" in error
            for error in validate_candidate(future_source)
        ))

        unknown_ref = make_bundle()
        unknown_ref["causal_attribution"]["hypotheses"][0]["causal_timeline"][0][
            "evidence_ref"
        ] = "NON_EXISTENT_EVIDENCE"
        _apply_control(unknown_ref)
        self.assertTrue(any(
            "not bound to its evidence dimension" in error
            for error in validate_candidate(unknown_ref)
        ))

        future_event = make_bundle()
        future_event["causal_attribution"]["hypotheses"][0]["causal_timeline"][0][
            "occurred_at"
        ] = "2099-01-01T00:00:00+09:00"
        _apply_control(future_event)
        errors = validate_candidate(future_event)
        self.assertTrue(any("cannot exceed observation.observed_at" in e for e in errors))
        self.assertTrue(any("referenced evidence time" in e for e in errors))

    def test_local_account_source_times_are_ordered_and_bounded(self) -> None:
        valid = make_bundle()
        self.assertEqual([], validate_candidate(valid))

        reversed_times = make_bundle()
        local_source = reversed_times["evidence_matrix"]["account_impact"][
            "sources"
        ][0]
        local_source["as_of"] = "2026-09-02T04:40:45+09:00"
        local_source["retrieved_at"] = "2026-09-02T04:40:30+09:00"
        _apply_control(reversed_times)
        self.assertTrue(any(
            "account_impact.sources[0].as_of cannot exceed retrieved_at" in error
            for error in validate_candidate(reversed_times)
        ))

        after_cutoff = make_bundle()
        after_cutoff["evidence_matrix"]["account_impact"]["sources"][0][
            "retrieved_at"
        ] = "2026-09-02T04:41:01+09:00"
        _apply_control(after_cutoff)
        self.assertTrue(any(
            "account_impact.sources[0].retrieved_at cannot exceed "
            "observation.observed_at" in error
            for error in validate_candidate(after_cutoff)
        ))

    def test_maintain_lifecycle_delta_requires_typed_structural_claim(self) -> None:
        quote_only = make_bundle()
        quote_only["value_zones"][0]["lifecycle"] = "CURRENT_CYCLE_VALIDATED"
        _apply_control(quote_only)
        self.assertTrue(any(
            "requires a bound typed multi-period structural evidence_claim" in error
            for error in validate_candidate(quote_only)
        ))

        proven = make_bundle()
        verify_dimensions(proven, "cross_exchange_acceptance")
        acceptance = proven["evidence_matrix"]["cross_exchange_acceptance"]
        acceptance["evidence_claims"].append({
            "claim_id": "CLAIM_V0_VALIDATED",
            "claim_type": "VALUE_ZONE_VALIDATED",
            "subject_zone_id": "V0",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in acceptance["sources"]],
            "time_range": acceptance["time_range"],
            "metric": metric(),
        })
        proven["value_zones"][0]["lifecycle"] = "CURRENT_CYCLE_VALIDATED"
        select_driver(proven, "UNRESOLVED")
        _apply_control(proven)
        self.assertEqual([], validate_candidate(proven))

        introduced = make_bundle()
        introduced["value_zones"].append({
            "zone_id": "V2",
            "role": "FORWARD_VALUE",
            "band": {"lower_u": "83000", "upper_u": "84000"},
            "lifecycle": "RETEST_PENDING",
        })
        introduced["forward_value_band_semantics"]["primary_zone_ids"].append("V2")
        _apply_control(introduced)
        self.assertTrue(any(
            "value_zone V2 lifecycle None->RETEST_PENDING" in error
            for error in validate_candidate(introduced)
        ))

        removed = make_bundle()
        removed["value_zones"] = removed["value_zones"][1:]
        removed["anchor_map_semantics"]["persistent_anchor_ids"] = ["V1"]
        _apply_control(removed)
        self.assertTrue(any(
            "previous value_zones cannot disappear" in error
            for error in validate_candidate(removed)
        ))

    def test_conditional_add_rejects_wrong_candidate_amount_and_evidence(self) -> None:
        valid = conditional_add_bundle()
        self.assertEqual([], validate_candidate(valid))

        no_candidate = deepcopy(valid)
        no_candidate["trade_candidates"][1]["eligibility"] = "CONDITIONAL_INACTIVE"
        no_candidate["trade_candidates"][1]["entries"][0]["eligibility"] = "INACTIVE"
        no_candidate["trade_candidates"][1]["report_relevant"] = False
        _apply_control(no_candidate)
        self.assertTrue(any(
            "same-direction eligible DISCOUNT_SPOT" in error
            for error in validate_candidate(no_candidate)
        ))

        amount_mismatch = deepcopy(valid)
        amount_mismatch["portfolio_decision"]["new_spot"]["amount_u"] = "601"
        _apply_control(amount_mismatch)
        self.assertTrue(any(
            "amount must equal" in error for error in validate_candidate(amount_mismatch)
        ))

        unresolved = deepcopy(valid)
        unresolved["evidence_matrix"]["capital_source"] = evidence(
            "UNRESOLVED", dimension="capital_source"
        )
        unresolved["observation"]["evidence_availability_by_dimension"][
            "capital_source"
        ] = "UNRESOLVED"
        unresolved["decision_evidence_status"] = "EVIDENCE_LIMITED"
        _apply_control(unresolved)
        self.assertTrue(any(
            "CONDITIONAL_ADD requires RESOLVED decision evidence" in error
            for error in validate_candidate(unresolved)
        ))
        self.assertTrue(any("capital_source" in error for error in validate_candidate(unresolved)))

    def test_fact_sync_needs_real_owner_delta_and_controlled_confirmation(self) -> None:
        self_asserted = make_bundle()
        self_asserted["account_facts"].update({
            "account_fact_changed_known": True,
            "known_change_evidence": [{
                "confirmation_type": "USER_CONFIRMED_POSITION_CHANGE",
                "owner_id": "ASSET_CONFIGURATION",
            }],
            "local_record_status": "FACT_SYNC_PENDING",
        })
        self_asserted["user_input_requirement"] = "FACT_SYNC"
        module = self_asserted["portfolio_decision"]["existing_digital_beta"]
        module["decision"] = "REVIEW_AFTER_KNOWN_FACT_CHANGE"
        self_asserted["report_semantics"][
            "overall_user_decision"
        ] = "FACT_SYNC_NEEDED_BEFORE_EXECUTION_REVIEW"
        self_asserted["report_semantics"]["portfolio_action_by_module"][
            "existing_digital_beta"
        ] = "REVIEW_AFTER_FACT_SYNC"
        _apply_control(self_asserted)
        self.assertTrue(any(
            "actual local fact Owner version change" in error
            for error in validate_candidate(self_asserted)
        ))

        arbitrary = deepcopy(self_asserted)
        arbitrary["account_facts"]["known_change_evidence"] = [
            {"confirmation_type": "PUBLIC_PRICE_CROSSED_V0", "owner_id": "ASSET_CONFIGURATION"}
        ]
        _apply_control(arbitrary)
        self.assertTrue(any(
            "confirmation_type is invalid" in error
            for error in validate_candidate(arbitrary)
        ))

    def test_every_inactive_candidate_commitment_delta_requires_report_visibility(self) -> None:
        def change_price_shape(candidate: dict, entry: dict) -> None:
            entry.pop("price_u")
            entry["price_band_u"] = {"lower_u": "81800", "upper_u": "82000"}

        def change_eligibility(candidate: dict, entry: dict) -> None:
            entry["eligibility"] = "INVALIDATED"
            candidate["eligibility"] = "INVALIDATED"

        def stop_carrying(candidate: dict, entry: dict) -> None:
            entry.pop("carried_forward")

        cases = {
            "amount": lambda candidate, entry: entry.__setitem__("amount_u", "251"),
            "price": lambda candidate, entry: entry.__setitem__("price_u", "81900"),
            "price_shape": change_price_shape,
            "amount_basis": lambda candidate, entry: entry["amount_basis"].__setitem__(
                "risk_assumption", "VOLATILITY_BUDGET_RECONFIRMED"
            ),
            "formation_condition": lambda candidate, entry: entry[
                "formation_conditions"
            ].append("FUNDING_COST_REMAINS_ELEVATED"),
            "invalidation_condition": lambda candidate, entry: entry[
                "invalidation_conditions"
            ].append("LEVERAGE_COST_NORMALIZES"),
            "evidence_confidence": lambda candidate, entry: entry.__setitem__(
                "evidence_confidence", "LOW"
            ),
            "account_precondition": lambda candidate, entry: entry.__setitem__(
                "account_precondition", "LOCAL_SAFETY_RECHECK_REQUIRED"
            ),
            "carried_forward": stop_carrying,
            "eligibility": change_eligibility,
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                bundle = make_bundle()
                old_decision = bundle["fingerprints"]["decision_fingerprint"]
                candidate = bundle["trade_candidates"][0]
                entry = candidate["entries"][0]
                mutate(candidate, entry)
                _apply_control(bundle)
                self.assertNotEqual(
                    old_decision, bundle["fingerprints"]["decision_fingerprint"]
                )
                self.assertEqual("D", bundle["control"]["monitor_state"])
                self.assertTrue(bundle["control"]["project_patch_required"])
                self.assertFalse(bundle["control"]["report_changed"])
                self.assertTrue(any(
                    "candidate commitment changes must change report_semantics" in error
                    for error in validate_candidate(bundle)
                ))

                expose_candidate_entry_change(bundle, 0)
                _apply_control(bundle)
                self.assertTrue(bundle["control"]["report_changed"])
                self.assertTrue(bundle["control"]["report_required"])
                self.assertEqual("CHANGE_NOTICE", bundle["control"]["attention_level"])
                self.assertEqual([], validate_candidate(bundle))

    def test_new_inactive_entry_requires_visibility_but_unchanged_ladder_is_silent(self) -> None:
        unchanged = make_bundle()
        self.assertEqual("A", unchanged["control"]["monitor_state"])
        self.assertFalse(unchanged["control"]["report_required"])
        self.assertEqual([], validate_candidate(unchanged))

        mechanically_copied = make_bundle()
        commitment = contract._candidate_commitments_by_entry(
            mechanically_copied["trade_candidates"]
        )["S1"]
        mechanically_copied["report_semantics"][
            "decision_relevant_levels_and_sizes"
        ].append({
            "level_id": "S1",
            "price_u": "81800",
            "amount_u": "250",
            "commitment_change_fields": ["AMOUNT_COMMITMENT"],
            "commitment_snapshot": contract._candidate_commitment_snapshot(
                commitment
            ),
        })
        _apply_control(mechanically_copied)
        self.assertTrue(any(
            "unchanged never-visible inactive" in error
            for error in validate_candidate(mechanically_copied)
        ))

        changed = make_bundle()
        new_entry = deepcopy(changed["trade_candidates"][0]["entries"][0])
        new_entry.update({"entry_id": "S2", "price_u": "82400", "amount_u": "550"})
        changed["trade_candidates"][0]["entries"].append(new_entry)
        _apply_control(changed)
        self.assertTrue(any(
            "candidate commitment changes must change report_semantics" in error
            for error in validate_candidate(changed)
        ))
        expose_candidate_entry_change(changed, 0, 1)
        _apply_control(changed)
        self.assertTrue(changed["control"]["report_required"])
        self.assertEqual([], validate_candidate(changed))

    def test_previously_visible_inactive_commitment_tracks_repeated_changes_without_bounce(self) -> None:
        cases = {
            "amount_basis": (
                lambda entry: entry["amount_basis"].__setitem__(
                    "risk_assumption", "VOLATILITY_BUDGET_RECONFIRMED"
                ),
                lambda entry: entry["amount_basis"].__setitem__(
                    "risk_assumption", "NO_ACCOUNT_SAFETY_CONFLICT"
                ),
            ),
            "formation_condition": (
                lambda entry: entry["formation_conditions"].append(
                    "FUNDING_COST_REMAINS_ELEVATED"
                ),
                lambda entry: entry["formation_conditions"].remove(
                    "FUNDING_COST_REMAINS_ELEVATED"
                ),
            ),
        }
        for label, (first_mutation, second_mutation) in cases.items():
            with self.subTest(label=label):
                first = make_bundle()
                first_mutation(first["trade_candidates"][0]["entries"][0])
                expose_candidate_entry_change(first, 0)
                _apply_control(first)
                self.assertEqual([], validate_candidate(first))
                first_body = contract.render_report_body(
                    first["report_semantics"]
                )

                with revision3_baseline_for_bundle(first) as (
                    first_baseline, first_semantics, first_binding,
                ):
                    second = deepcopy(first)
                    second_mutation(
                        second["trade_candidates"][0]["entries"][0]
                    )
                    second["previous_fingerprints"] = {
                        "decision_fingerprint": first_binding[
                            "decision_fingerprint"
                        ],
                        "report_fingerprint": first_binding[
                            "report_fingerprint"
                        ],
                    }
                    second["previous_projection_baseline"] = deepcopy(
                        first_binding
                    )
                    expose_candidate_entry_change(
                        second, 0, previous_semantics=first_semantics
                    )
                    _apply_control(second)
                    self.assertTrue(second["control"]["report_changed"])
                    self.assertTrue(second["control"]["report_required"])
                    self.assertNotEqual(
                        first_body,
                        contract.render_report_body(second["report_semantics"]),
                    )
                    self.assertEqual([], contract.validate_bundle(
                        second,
                        previous_baseline=first_baseline,
                        fact_owner_snapshot=contract.load_fact_owner_snapshot(),
                        require_owner_baseline=False,
                        require_fact_owner_paths=False,
                    ))

                with revision3_baseline_for_bundle(second) as (
                    second_baseline, _second_semantics, second_binding,
                ):
                    unchanged = deepcopy(second)
                    unchanged["previous_fingerprints"] = {
                        "decision_fingerprint": second_binding[
                            "decision_fingerprint"
                        ],
                        "report_fingerprint": second_binding[
                            "report_fingerprint"
                        ],
                    }
                    unchanged["previous_projection_baseline"] = deepcopy(
                        second_binding
                    )
                    _apply_control(unchanged)
                    self.assertEqual("A", unchanged["control"]["monitor_state"])
                    self.assertFalse(unchanged["control"]["report_changed"])
                    self.assertFalse(unchanged["control"]["report_required"])
                    self.assertEqual([], contract.validate_bundle(
                        unchanged,
                        previous_baseline=second_baseline,
                        fact_owner_snapshot=contract.load_fact_owner_snapshot(),
                        require_owner_baseline=False,
                        require_fact_owner_paths=False,
                    ))

    def test_candidate_commitment_report_is_controlled_and_natural_language_only(self) -> None:
        bundle = make_bundle()
        bundle["trade_candidates"][0]["entries"][0]["amount_u"] = "251"
        expose_candidate_entry_change(bundle, 0)
        _apply_control(bundle)
        body = contract.render_report_body(bundle["report_semantics"])
        self.assertIn("评估规模", body)
        self.assertIn("规模依据", body)
        self.assertNotIn('"', body)
        for code in contract.COMMITMENT_CHANGE_FIELDS.union(
            contract.COMMITMENT_VALUE_CODES
        ):
            self.assertNotIn(code, body)

        forged = deepcopy(bundle["report_semantics"])
        candidate_item = next(
            item for item in forged["decision_relevant_levels_and_sizes"]
            if item["level_id"] == "S1"
        )
        candidate_item["commitment_snapshot"]["amount_basis_classes"][
            "risk_assumption"
        ] = "任意自由文本；可伪造语义"
        with self.assertRaisesRegex(
            contract.GuardError, "controlled code"
        ):
            contract.render_report_body(forged)

    def test_candidate_kind_cannot_disguise_premium_short_as_discount_spot(self) -> None:
        bundle = conditional_add_bundle()
        real_discount = bundle["trade_candidates"][1]
        real_discount["eligibility"] = "CONDITIONAL_INACTIVE"
        real_discount["report_relevant"] = False
        real_discount["entries"][0]["eligibility"] = "INACTIVE"
        disguised = bundle["trade_candidates"][0]
        disguised["kind"] = "DISCOUNT_SPOT"
        disguised["eligibility"] = "ELIGIBLE_FOR_REVIEW"
        disguised["report_relevant"] = True
        disguised["entries"][0]["eligibility"] = "ELIGIBLE_FOR_REVIEW"
        disguised["entries"][0]["amount_u"] = "600"
        bundle["report_semantics"]["decision_relevant_levels_and_sizes"] = [{
            "level_id": "S1", "price_u": "81800", "amount_u": "600",
        }]
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("candidate_id must equal" in error for error in errors))
        self.assertTrue(any("value_role conflicts" in error for error in errors))
        self.assertTrue(any("DISCOUNT_SPOT must be below" in error for error in errors))

    def test_quote_endpoint_cannot_self_label_as_structural_acceptance(self) -> None:
        bundle = make_bundle()
        verify_dimensions(bundle, "cross_exchange_acceptance")
        acceptance = bundle["evidence_matrix"]["cross_exchange_acceptance"]
        quote_endpoints = (
            ("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", "BINANCE", "binance"),
            ("https://api.exchange.coinbase.com/products/BTC-USD/ticker", "COINBASE", "coinbase"),
        )
        for source, (endpoint, origin, venue) in zip(
            acceptance["sources"], quote_endpoints
        ):
            source.update({
                "endpoint_url": endpoint,
                "canonical_origin": origin,
                "venue": venue,
            })
        acceptance["evidence_claims"].append({
            "claim_id": "CLAIM_V0_VALIDATED_FROM_QUOTES",
            "claim_type": "VALUE_ZONE_VALIDATED",
            "subject_zone_id": "V0",
            "status": "VERIFIED",
            "source_ids": [source["source_id"] for source in acceptance["sources"]],
            "time_range": acceptance["time_range"],
            "metric": metric(),
        })
        bundle["value_zones"][0]["lifecycle"] = "CURRENT_CYCLE_VALIDATED"
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("quote-only endpoint" in error for error in errors))

    def test_resolved_causal_roles_require_typed_dimension_references(self) -> None:
        bundle = conditional_add_bundle()
        selected = next(
            hypothesis for hypothesis in bundle["causal_attribution"]["hypotheses"]
            if hypothesis["hypothesis_id"] == "H_SPOT"
        )
        selected["causal_timeline"][0]["evidence_ref"] = (
            bundle["evidence_matrix"]["spot_flow"]["sources"][0]["source_id"]
        )
        price_event = next(
            event for event in selected["causal_timeline"]
            if event["event_role"] == "PRICE_RESPONSE_OBSERVED"
        )
        price_event.update({
            "evidence_dimension": "spot_flow",
            "evidence_ref": bundle["evidence_matrix"]["spot_flow"]["sources"][0][
                "source_id"
            ],
        })
        _apply_control(bundle)
        errors = validate_candidate(bundle)
        self.assertTrue(any("VERIFIED typed claim" in error for error in errors))
        self.assertTrue(any("PRICE_RESPONSE_OBSERVED cannot use" in error for error in errors))

    def test_hidden_candidate_delta_and_hidden_eligibility_both_fail(self) -> None:
        hidden_delta = make_bundle()
        hidden_delta["trade_candidates"][0]["entries"][0]["amount_u"] = "251"
        _apply_control(hidden_delta)
        self.assertTrue(any(
            "candidate commitment changes must change report_semantics" in error
            for error in validate_candidate(hidden_delta)
        ))
        self.assertEqual("D", hidden_delta["control"]["monitor_state"])
        self.assertFalse(hidden_delta["control"]["report_required"])

        hidden = conditional_add_bundle()
        hidden["trade_candidates"][1]["report_relevant"] = False
        hidden["report_semantics"]["decision_relevant_levels_and_sizes"] = []
        _apply_control(hidden)
        hidden_errors = validate_candidate(hidden)
        self.assertTrue(any(
            "eligible entries must be report_relevant" in error
            for error in hidden_errors
        ))

    def test_projection_renderer_rejects_body_tamper_even_when_resigned(self) -> None:
        bundle = make_bundle()
        set_candidate_eligible(bundle, 0, report_relevant=True)
        bundle["report_semantics"]["overall_user_decision"] = "REVIEW_ELIGIBLE_CANDIDATE"
        _apply_control(bundle)
        state = contract.render_projection_document(bundle, "STATE")
        plan = contract.render_projection_document(bundle, "PLAN")
        body = contract._remove_projection_sidecar(state)
        forged_body = body.replace(
            "## portfolio_decision",
            "## portfolio_decision\nBTC 已确认必须立刻加仓。",
            1,
        )
        forged = forged_body + contract.render_projection_sidecar(
            bundle, forged_body, "STATE"
        ) + "\n"
        errors = validate_projection_candidate(forged, plan, bundle)
        self.assertTrue(any("deterministic bundle rendering" in error for error in errors))

    def test_strict_newline_and_invalid_utf8_are_not_normalized(self) -> None:
        semantics = make_bundle()["report_semantics"]
        report = contract.render_report_body(semantics)
        projection = dict(semantics)
        projection["rendered_evidence"] = contract.generate_report_witnesses(semantics)
        projection["authorization_disclaimer_excerpt"] = contract.REPORT_AUTHORIZATION_DISCLAIMER
        self.assertEqual([], contract.validate_report_text(report, projection))
        self.assertTrue(contract.validate_report_text(report.replace("\n", "\r\n"), projection))

        with tempfile.TemporaryDirectory() as temp_dir:
            invalid = Path(temp_dir) / "invalid.md"
            semantics_path = Path(temp_dir) / "semantics.json"
            invalid.write_bytes(b"\xff")
            semantics_path.write_text(json.dumps(semantics), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "strategy_contract.py"),
                    "validate-report", "--replay-only", "--input", str(invalid),
                    "--projection", str(semantics_path),
                ],
                check=False, capture_output=True,
            )
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(b"", result.stderr)

    def test_unavailable_requires_real_deep_review_candidate(self) -> None:
        bundle = make_bundle()
        observation = unavailable_observation_from(bundle)
        observation["observation"][
            "event_discriminator_status"
        ] = "NO_DISCRIMINATING_EVENT"
        receipt = unavailable_receipt_for(bundle, observation)
        with revision3_baseline() as baseline:
            errors = contract.validate_unavailable_receipt(
                receipt, baseline, observation, contract.load_fact_owner_snapshot(),
                require_owner_baseline=False,
            )
        self.assertTrue(any("DEEP_REVIEW_CANDIDATE" in error for error in errors))

        self_declared = unavailable_observation_from(bundle)
        self_declared["observation"] = deepcopy(bundle["observation"])
        self_declared["observation"][
            "event_discriminator_status"
        ] = "DEEP_REVIEW_CANDIDATE"
        receipt = unavailable_receipt_for(bundle, self_declared)
        with revision3_baseline() as baseline:
            errors = contract.validate_unavailable_receipt(
                receipt, baseline, self_declared, contract.load_fact_owner_snapshot(),
                require_owner_baseline=False,
            )
        self.assertTrue(any("changed normalized observation" in error for error in errors))

        future = unavailable_observation_from(bundle)
        future["evaluation_as_of"] = "2099-01-01T00:01:00+09:00"
        future["observation"]["observed_at"] = "2099-01-01T00:01:00+09:00"
        quote_evidence = future["price_quote_evidence"]
        quote_evidence["time_range"] = {
            "kind": "POINT_IN_TIME",
            "start": "2099-01-01T00:00:00+09:00",
            "end": "2099-01-01T00:00:00+09:00",
        }
        for source in quote_evidence["sources"]:
            source["as_of"] = "2099-01-01T00:00:00+09:00"
            source["retrieved_at"] = "2099-01-01T00:00:30+09:00"
        receipt = unavailable_receipt_for(bundle, future)
        with revision3_baseline() as baseline:
            errors = contract.validate_unavailable_receipt(
                receipt, baseline, future, contract.load_fact_owner_snapshot(),
                require_owner_baseline=False,
            )
        self.assertTrue(any("evaluation_as_of cannot be in the future" in error for error in errors))

    def test_render_projection_document_cli_round_trip(self) -> None:
        bundle = make_bundle()
        set_candidate_eligible(bundle, 0, report_relevant=True)
        bundle["report_semantics"]["overall_user_decision"] = "REVIEW_ELIGIBLE_CANDIDATE"
        _apply_control(bundle)
        old_state, old_plan = default_revision3_baseline_documents()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle_path = root / "bundle.json"
            state_path = root / "old-state.md"
            plan_path = root / "old-plan.md"
            bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            state_path.write_text(old_state, encoding="utf-8")
            plan_path.write_text(old_plan, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "strategy_contract.py"),
                    "render-projection-document", "--replay-only", "--bundle", str(bundle_path),
                    "--role", "STATE", "--previous-state-snapshot", str(state_path),
                    "--previous-plan-snapshot", str(plan_path),
                ],
                check=False, capture_output=True, text=True,
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            contract.render_projection_document(bundle, "STATE"), payload["document"]
        )


if __name__ == "__main__":
    unittest.main()
