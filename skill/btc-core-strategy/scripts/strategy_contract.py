#!/usr/bin/env python3
"""Deterministic revision-3 contract guards for the BTC core-strategy skill.

The module deliberately uses only the Python standard library.  It is both an
importable validation library and a small JSON CLI.  It does not fetch market
or account data and has no write path to the BTC project.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlparse


SCHEMA_VERSION = "BTC_STRATEGY_DECISION_V3"
UNAVAILABLE_RECEIPT_SCHEMA = "BTC_DEEP_EVAL_UNAVAILABLE_V3"
UNAVAILABLE_OBSERVATION_SCHEMA = "BTC_UNAVAILABLE_OBSERVATION_V3"
RUN_RECEIPT_SCHEMA = "BTC_STRATEGY_RUN_RECEIPT_V3"
CLOUD_READBACK_SCHEMA = "BTC_CLOUD_READBACK_V3"
PROJECTION_SIDECAR_SCHEMA = "BTC_STRATEGY_PROJECTION_SIDECAR_V3"
REVISION3_BASELINE_SCHEMA = "BTC_REVISION3_PROJECTION_BASELINE_V3"
REVISION2_REPORT_BASELINE_SCHEMA = "BTC_REVISION2_REPORT_BASELINE_V3"
PROJECTION_SIDECAR_START = "<!-- BTC_STRATEGY_PROJECTION_SIDECAR_V3"
PROJECTION_SIDECAR_END = "BTC_STRATEGY_PROJECTION_SIDECAR_V3_END -->"
import node_config
OWNER_STATE_PATH = node_config.project() / "策略状态.md"
OWNER_PLAN_PATH = node_config.project() / "当前预期计划.md"
FACT_OWNER_PATHS = {
    "ASSET_CONFIGURATION": node_config.project() / "资产配置.md",
    "OPERATION_LOG": node_config.project() / "操作记录.md",
    "CORRECTION_LOG": node_config.project() / "纠正记录.md",
}
_REVISION2_DIR = node_config.legacy_directory()
REVISION2_STATE_FIXTURE_PATH = _REVISION2_DIR / "revision2_state_1346.md"
REVISION2_PLAN_FIXTURE_PATH = _REVISION2_DIR / "revision2_plan_1346.md"
REVISION2_REPORT_PATH = _REVISION2_DIR / "revision2_report_1346.md"
REVISION2_REPORT_PROJECTION_PATH = _REVISION2_DIR / "revision2_report_1346_semantics.json"
IMMUTABLE_REVISION2_ARTIFACT_HASHES = {
    _REVISION2_DIR / name: fingerprint
    for name, fingerprint in node_config.legacy_profile().get("artifacts", {}).values()
}
QUOTE_ENDPOINT_REGISTRY = {
    "api.binance.com": (
        "BINANCE", "binance", "/api/v3/ticker/price", "symbol=BTCUSDT",
    ),
    "api.kraken.com": (
        "KRAKEN", "kraken", "/0/public/Ticker", "pair=XBTUSD",
    ),
    "api.exchange.coinbase.com": (
        "COINBASE", "coinbase", "/products/BTC-USD/ticker", "",
    ),
}
CANDIDATE_KIND_CONTRACT = {
    "PREMIUM_SHORT": {
        "entry_pattern": r"S[1-9][0-9]*",
        "value_role": "UNCONFIRMED_PREMIUM_EXTENSION",
        "formation_condition": "LEVERAGE_DOMINATES_WITHOUT_SPOT_VALUE",
        "budget_source": "ALPHA_RISK_BUDGET",
        "side": "ABOVE",
    },
    "DISCOUNT_SPOT": {
        "entry_pattern": r"D[1-9][0-9]*",
        "value_role": "DELEVERAGING_DISCOUNT",
        "formation_condition": "DELEVERAGING_WITH_SPOT_ABSORPTION",
        "budget_source": "SHARED_CAPITAL_POOL",
        "side": "BELOW",
    },
}
CANDIDATE_ELIGIBILITY_EVIDENCE = {
    "PREMIUM_SHORT": {
        "driver_regime": "DERIVATIVES_LEVERAGE",
        "selected_hypothesis_id": "H_LEVERAGE",
        "required_claims": (
            (
                "derivatives_flow",
                {"DERIVATIVES_LEVERAGED_BUY_DOMINANCE"},
                None,
                False,
                "CAUSE_OBSERVED",
            ),
            ("oi_timeline", {"OI_TIMELINE"}, None, False, "CAUSE_OBSERVED"),
            (
                "funding_basis", {"LEVERAGE_COST_EXPANSION"}, None, False,
                "CAUSE_OBSERVED",
            ),
            (
                "cross_exchange_acceptance",
                {"SPOT_VALUE_NON_ACCEPTANCE"},
                None,
                True,
                "RETENTION_OBSERVED",
            ),
        ),
    },
    "DISCOUNT_SPOT": {
        "driver_regime": "CRYPTO_NATIVE_SPOT",
        "selected_hypothesis_id": "H_SPOT",
        "required_claims": (
            (
                "cross_exchange_acceptance",
                {"CROSS_PERIOD_ACCEPTANCE"},
                None,
                False,
                "RETENTION_OBSERVED",
            ),
            (
                "spot_flow",
                {"NATIVE_SPOT_FLOW", "NATIVE_SPOT_BUY_FLOW", "NATIVE_SPOT_BUY"},
                "BUY",
                False,
                "CAUSE_OBSERVED",
            ),
            (
                "derivatives_flow",
                {"DERIVATIVES_DELEVERAGING_FLOW"},
                None,
                False,
                "CAUSE_OBSERVED",
            ),
            (
                "oi_timeline", {"OI_DELEVERAGING"}, None, False,
                "CAUSE_OBSERVED",
            ),
            (
                "capital_source", {"CAPITAL_SOURCE"}, None, False,
                "CAUSE_OBSERVED",
            ),
        ),
    },
}
DRIVER_CAUSAL_EVIDENCE = {
    "CRYPTO_NATIVE_SPOT": (
        (
            "spot_flow",
            {"NATIVE_SPOT_FLOW", "NATIVE_SPOT_BUY_FLOW", "NATIVE_SPOT_BUY", "NATIVE_SPOT_SELL_FLOW", "NATIVE_SPOT_SELL"},
            "CAUSE_OBSERVED",
        ),
        ("capital_source", {"CAPITAL_SOURCE"}, "CAUSE_OBSERVED"),
        (
            "cross_exchange_acceptance",
            {"CROSS_PERIOD_ACCEPTANCE"},
            "RETENTION_OBSERVED",
        ),
    ),
    "DERIVATIVES_LEVERAGE": (
        (
            "derivatives_flow",
            {"DERIVATIVES_ACTIVE_FLOW", "DERIVATIVES_LEVERAGED_BUY_DOMINANCE"},
            "CAUSE_OBSERVED",
        ),
        ("oi_timeline", {"OI_TIMELINE"}, "CAUSE_OBSERVED"),
    ),
    "MACRO_BETA": (
        ("macro", {"MACRO_EVENT_TIMELINE"}, "CAUSE_OBSERVED"),
    ),
}
LIFECYCLES = {
    "CURRENT_CYCLE_VALIDATED",
    "RETEST_PENDING",
    "RETEST_CONFIRMED",
    "INVALIDATED",
}
ZONE_RELATIONS = {
    "ABOVE_ZONE",
    "INSIDE_ZONE",
    "BELOW_ZONE",
    "CROSS_VENUE_DIVERGENCE",
}
EVIDENCE_STATUSES = {
    "VERIFIED",
    "CONTRADICTED",
    "UNRESOLVED",
    "UNAVAILABLE",
}
REQUIRED_EVIDENCE_DIMENSIONS = (
    "price_quotes",
    "cross_exchange_acceptance",
    "spot_flow",
    "derivatives_flow",
    "oi_timeline",
    "funding_basis",
    "capital_source",
    "macro",
    "relative_strength",
    "account_impact",
)
CAUSAL_CORE_DIMENSIONS = (
    "spot_flow",
    "derivatives_flow",
    "capital_source",
    "macro",
)
DECISIVE_COMPLETENESS_DIMENSIONS = (
    "cross_exchange_acceptance",
    "spot_flow",
    "oi_timeline",
    "capital_source",
)
PORTFOLIO_MODULES = (
    "existing_digital_beta",
    "existing_alpha_short",
    "new_spot",
    "new_short",
    "optionality",
)
HYPOTHESIS_FIELDS = (
    "hypothesis_id",
    "thesis",
    "supporting_evidence",
    "counter_evidence",
    "causal_timeline",
    "missing_evidence",
    "falsification_conditions",
    "next_discriminating_evidence",
)
FINGERPRINT_FIELDS = (
    "observation_fingerprint",
    "decision_fingerprint",
    "report_fingerprint",
)
OBSERVATION_SEMANTIC_FIELDS = (
    "normalization_revision",
    "source_health_class",
    "quote_relation_by_venue",
    "closed_bar_relation_sequence",
    "cross_venue_divergence_class",
    "evidence_availability_by_dimension",
    "event_discriminator_status",
    "final_expiry_status",
)
REPORT_SEMANTIC_FIELDS = (
    "overall_user_decision",
    "primary_causal_explanation",
    "rejected_competing_explanations",
    "portfolio_action_by_module",
    "material_uncertainties",
    "next_decision_changing_evidence",
    "user_visible_major_risk",
    "decision_relevant_levels_and_sizes",
)
REPORT_VISIBLE_LABELS = {
    "overall_user_decision": "综合判断",
    "primary_causal_explanation": "主要原因",
    "rejected_competing_explanations": "未采用解释",
    "portfolio_action_by_module": "组合影响",
    "decision_relevant_levels_and_sizes": "关键价位与规模",
    "material_uncertainties": "关键不确定性",
    "next_decision_changing_evidence": "下一项改变判断的证据",
    "user_visible_major_risk": "重大风险",
}
PORTFOLIO_VISIBLE_LABELS = {
    "existing_digital_beta": "既有 Digital Beta",
    "existing_alpha_short": "既有 Alpha 空头",
    "new_spot": "新增现货",
    "new_short": "新增空头",
    "optionality": "可选性",
}
REPORT_CODE_DISPLAY = {
    "NO_INCREMENTAL_POSITION_CHANGE": "暂不改变新增敞口",
    "UPWARD_VALUE_MIGRATION": "确认上移价值路径",
    "DOWNWARD_VALUE_MIGRATION": "确认下移价值路径",
    "WIDENED_VALUE_PATH": "确认扩大价值分布路径",
    "CONDITIONAL_DISCOUNT_SPOT_REVIEW": "进入折价现货条件复核",
    "CONSERVATIVE_RISK_FREEZE": "保守冻结新增风险",
    "NEW_CYCLE_DECISION_CHANGED": "新周期的用户决策已变化",
    "FACT_SYNC_NEEDED_BEFORE_EXECUTION_REVIEW": "执行复核前需同步已变资产事实",
    "REVIEW_ELIGIBLE_CANDIDATE": "复核已获得资格的候选方案",
    "FREEZE_INCREMENTAL_RISK": "停止新增风险敞口",
    "CAUSE_UNRESOLVED_KEEP_LAST_VALID_ANCHOR": "因果尚未解决，继续沿用最近有效结构锚",
    "DERIVATIVES_LEVERAGE_WITHOUT_SPOT_VALUE": "衍生品杠杆占优，现货价值尚未建立",
    "CRYPTO_NATIVE_SPOT": "原生现货资金主导",
    "DERIVATIVES_LEVERAGE": "衍生品杠杆主导",
    "MACRO_BETA": "宏观风险偏好主导",
    "SPOT_LED_ACCEPTANCE": "现货主导的跨所接受已确认",
    "LEVERAGE_OVERLAY_ONLY": "当前仅形成杠杆覆盖层",
    "MACRO_OVERLAY_ONLY": "当前仅形成宏观覆盖层",
    "MIXED_CAUSAL_DRIVERS": "多重资金驱动共同取得解释权",
    "H_SPOT": "现货主导假设",
    "H_LEVERAGE": "杠杆主导假设",
    "H_MACRO": "宏观主导假设",
    "H_UNRESOLVED": "未解决因果假设",
    "H_MIXED": "多重驱动共同解释假设",
    "NO_ACCEPTANCE_SEQUENCE": "缺少跨时段接受序列",
    "NO_OI_SEQUENCE": "缺少持仓量时序",
    "NO_MACRO_TIMELINE": "缺少宏观事件时序",
    "NO_TIMELINE": "缺少可验证因果时序",
    "NO_ACCEPTANCE": "缺少结构接受",
    "NO_SPOT_FLOW_SEQUENCE": "缺少原生现货主动流时序",
    "NO_CAPITAL_SOURCE_SEQUENCE": "缺少资金来源时序",
    "SPOT_NOT_PRIMARY_DRIVER": "现货证据存在但未取得主要解释权",
    "NO_DERIVATIVES_FLOW_SEQUENCE": "缺少衍生品主动流时序",
    "NO_FUNDING_BASIS_SEQUENCE": "缺少资金成本时序",
    "LEVERAGE_NOT_PRIMARY_DRIVER": "杠杆证据存在但未取得主要解释权",
    "NO_RELATIVE_STRENGTH_SEQUENCE": "缺少相对强弱时序",
    "MACRO_NOT_PRIMARY_DRIVER": "宏观证据存在但未取得主要解释权",
    "CAUSAL_SEQUENCE_RESOLVED": "已有因果序列足以排除未解决解释",
    "NO_MIXED_DRIVER_SEQUENCE": "缺少多重驱动的共同因果序列",
    "MIXED_NOT_PRIMARY_DRIVER": "多重驱动证据存在但未取得主要解释权",
    "MAINTAIN": "维持当前安排",
    "CONDITIONAL_ADD_600U": "条件成立时复核增加六百U现货",
    "REVIEW_AFTER_FACT_SYNC": "资产事实同步后再复核",
    "PORTFOLIO_REDUCE_REVIEW": "复核降低既有敞口",
    "PORTFOLIO_EXIT_REVIEW": "复核退出既有敞口",
    "PORTFOLIO_SAFETY_OVERRIDE": "账户安全约束覆盖市场方案",
    "V0": "下层持续价值区",
    "V1": "上层待回测价值区",
    "D1": "第一折价现货候选档",
    "D2": "第二折价现货候选档",
    "S1": "第一溢价空头候选档",
    "S2": "第二溢价空头候选档",
    "SPOT_FLOW": "现货主动流向仍不确定",
    "OI_TIMELINE": "持仓量时序仍不完整",
    "CAPITAL_SOURCE": "资金来源仍未解决",
    "OI": "持仓量证据仍不确定",
    "SPOT": "现货证据仍不确定",
    "MACRO_TIMELINE": "宏观事件时序仍不完整",
    "CROSS_VENUE_ACCEPTANCE_WITH_CAUSAL_TIMELINE": "跨所接受与资金因果时序同时获得验证",
    "MACRO_CAUSAL_TIMELINE": "宏观事件与相对强弱时序同时获得验证",
    "NEXT_A": "下一项区分证据甲",
    "NEXT_B": "下一项区分证据乙",
    "NONE": "目前没有新增重大风险",
    "DECISIVE_SOURCE_CONFLICT": "决定性数据来源持续冲突",
    "SOURCE_CONFLICT": "关键数据来源冲突",
}
REPORT_CODE_ALLOWLIST = {
    "overall_user_decision": {
        "NO_INCREMENTAL_POSITION_CHANGE", "UPWARD_VALUE_MIGRATION",
        "DOWNWARD_VALUE_MIGRATION", "WIDENED_VALUE_PATH",
        "CONDITIONAL_DISCOUNT_SPOT_REVIEW", "CONSERVATIVE_RISK_FREEZE",
        "NEW_CYCLE_DECISION_CHANGED", "FACT_SYNC_NEEDED_BEFORE_EXECUTION_REVIEW",
        "REVIEW_ELIGIBLE_CANDIDATE", "FREEZE_INCREMENTAL_RISK",
    },
    "primary_causal_explanation": {
        "CAUSE_UNRESOLVED_KEEP_LAST_VALID_ANCHOR",
        "DERIVATIVES_LEVERAGE_WITHOUT_SPOT_VALUE", "CRYPTO_NATIVE_SPOT",
        "DERIVATIVES_LEVERAGE", "MACRO_BETA", "SPOT_LED_ACCEPTANCE",
        "LEVERAGE_OVERLAY_ONLY", "MACRO_OVERLAY_ONLY", "MIXED_CAUSAL_DRIVERS",
    },
    "rejected_hypothesis_id": {
        "H_SPOT", "H_LEVERAGE", "H_MACRO", "H_UNRESOLVED", "H_MIXED",
    },
    "rejected_reason_class": {
        "NO_ACCEPTANCE_SEQUENCE", "NO_OI_SEQUENCE", "NO_MACRO_TIMELINE",
        "NO_TIMELINE", "NO_ACCEPTANCE", "NO_SPOT_FLOW_SEQUENCE",
        "NO_CAPITAL_SOURCE_SEQUENCE", "SPOT_NOT_PRIMARY_DRIVER",
        "NO_DERIVATIVES_FLOW_SEQUENCE", "NO_FUNDING_BASIS_SEQUENCE",
        "LEVERAGE_NOT_PRIMARY_DRIVER", "NO_RELATIVE_STRENGTH_SEQUENCE",
        "MACRO_NOT_PRIMARY_DRIVER", "CAUSAL_SEQUENCE_RESOLVED",
        "NO_MIXED_DRIVER_SEQUENCE", "MIXED_NOT_PRIMARY_DRIVER",
    },
    "portfolio_action": {
        "MAINTAIN", "CONDITIONAL_ADD_600U", "REVIEW_AFTER_FACT_SYNC",
        "FREEZE_INCREMENTAL_RISK", "PORTFOLIO_REDUCE_REVIEW",
        "PORTFOLIO_EXIT_REVIEW", "PORTFOLIO_SAFETY_OVERRIDE",
    },
    "level_id": {"V0", "V1", "D1", "D2", "S1", "S2"},
    "material_uncertainties": {
        "SPOT_FLOW", "OI_TIMELINE", "CAPITAL_SOURCE", "OI", "SPOT",
        "MACRO_TIMELINE",
    },
    "next_decision_changing_evidence": {
        "CROSS_VENUE_ACCEPTANCE_WITH_CAUSAL_TIMELINE",
        "MACRO_CAUSAL_TIMELINE", "NEXT_A", "NEXT_B",
    },
    "user_visible_major_risk": {"NONE", "DECISIVE_SOURCE_CONFLICT", "SOURCE_CONFLICT"},
}
_REPORT_DECLARED_CODES = set().union(*REPORT_CODE_ALLOWLIST.values())
if set(REPORT_CODE_DISPLAY) != _REPORT_DECLARED_CODES:
    raise RuntimeError(
        "REPORT_CODE_DISPLAY must cover exactly every controlled report code"
    )
if len(set(REPORT_CODE_DISPLAY.values())) != len(REPORT_CODE_DISPLAY):
    raise RuntimeError("REPORT_CODE_DISPLAY natural-language values must be unique")

COMMITMENT_CHANGE_FIELD_DISPLAY = {
    "ENTRY_ADDED": "新增候选档位",
    "CANDIDATE_IDENTITY": "候选身份",
    "CANDIDATE_KIND": "候选类型",
    "PRICE_COMMITMENT": "价位形式或数值",
    "AMOUNT_COMMITMENT": "评估规模",
    "AMOUNT_BASIS": "规模依据",
    "VALUE_ROLE": "价值角色",
    "ENTRY_ELIGIBILITY": "档位资格",
    "FORMATION_CONDITIONS": "形成条件",
    "INVALIDATION_CONDITIONS": "失效条件",
    "EVIDENCE_CONFIDENCE": "证据置信度",
    "ACCOUNT_PRECONDITION": "账户前置条件",
    "CARRIED_FORWARD": "沿用状态",
}
COMMITMENT_VALUE_DISPLAY = {
    "PREMIUM_SHORT": "溢价空头候选",
    "DISCOUNT_SPOT": "折价现货候选",
    "UNCONFIRMED_PREMIUM_EXTENSION": "未确认价值的溢价延伸",
    "DELEVERAGING_DISCOUNT": "去杠杆折价",
    "INACTIVE": "尚未激活",
    "ELIGIBLE_FOR_REVIEW": "已取得评估资格",
    "INVALIDATED": "已经失效",
    "ALPHA_RISK_BUDGET": "Alpha 风险预算",
    "SHARED_CAPITAL_POOL": "共用资金池",
    "PORTFOLIO_REMAINS_NET_LONG": "组合继续保持净多",
    "STRUCTURAL_MISPRICING_ONLY": "仅在结构性错配时使用",
    "NO_ACCOUNT_SAFETY_CONFLICT": "账户安全约束无冲突",
    "VOLATILITY_BUDGET_RECONFIRMED": "波动预算约束已经复核",
    "ANCHOR_AND_PORTFOLIO_CONSTRAINTS_UNCHANGED": "结构锚与组合约束未变",
    "CONTINUATION_NOT_APPLICABLE": "本项不适用沿用理由",
    "LEVERAGE_DOMINATES_WITHOUT_SPOT_VALUE": "杠杆主导且现货价值未建立",
    "DELEVERAGING_WITH_SPOT_ABSORPTION": "去杠杆同时出现现货吸收",
    "FUNDING_COST_REMAINS_ELEVATED": "杠杆资金成本仍处高位",
    "SPOT_VALUE_ACCEPTED_ABOVE_80000": "八万U上方形成现货价值接受",
    "NATIVE_SPOT_SELLING_DOMINATES": "原生现货卖出取得主导",
    "LEVERAGE_COST_NORMALIZES": "杠杆资金成本恢复常态",
    "CONDITIONAL": "条件性证据",
    "LOW": "低置信证据",
    "LOCAL_SAFETY_FACTS_ALLOW": "本地安全事实允许继续评估",
    "USER_CONFIRMED_CASH_AVAILABLE": "用户确认可用现金后再评估",
    "LOCAL_SAFETY_RECHECK_REQUIRED": "需重新核对本地安全事实",
}
COMMITMENT_CHANGE_FIELDS = set(COMMITMENT_CHANGE_FIELD_DISPLAY)
COMMITMENT_VALUE_CODES = set(COMMITMENT_VALUE_DISPLAY)
COMMITMENT_SNAPSHOT_VISIBLE_LABELS = {
    "candidate_kind": "候选类型",
    "value_role": "价值角色",
    "entry_eligibility": "当前资格",
    "evidence_confidence": "证据置信度",
    "account_precondition": "账户前置条件",
}
COMMITMENT_AMOUNT_BASIS_VISIBLE_LABELS = {
    "budget_source": "预算来源",
    "net_exposure_constraint": "净敞口约束",
    "value_gap_rationale": "价值差依据",
    "risk_assumption": "风险假设",
    "continuation_reason": "沿用依据",
}
COMMITMENT_SNAPSHOT_CODE_ALLOWLIST = {
    "candidate_kind": {"PREMIUM_SHORT", "DISCOUNT_SPOT"},
    "value_role": {
        "UNCONFIRMED_PREMIUM_EXTENSION", "DELEVERAGING_DISCOUNT",
    },
    "entry_eligibility": {
        "INACTIVE", "ELIGIBLE_FOR_REVIEW", "INVALIDATED",
    },
    "evidence_confidence": {"CONDITIONAL", "LOW"},
    "account_precondition": {
        "LOCAL_SAFETY_FACTS_ALLOW", "USER_CONFIRMED_CASH_AVAILABLE",
        "LOCAL_SAFETY_RECHECK_REQUIRED",
    },
}
COMMITMENT_AMOUNT_BASIS_CODE_ALLOWLIST = {
    "budget_source": {"ALPHA_RISK_BUDGET", "SHARED_CAPITAL_POOL"},
    "net_exposure_constraint": {"PORTFOLIO_REMAINS_NET_LONG"},
    "value_gap_rationale": {"STRUCTURAL_MISPRICING_ONLY"},
    "risk_assumption": {
        "NO_ACCOUNT_SAFETY_CONFLICT", "VOLATILITY_BUDGET_RECONFIRMED",
    },
    "continuation_reason": {
        "ANCHOR_AND_PORTFOLIO_CONSTRAINTS_UNCHANGED",
        "CONTINUATION_NOT_APPLICABLE",
    },
}
COMMITMENT_CONDITION_CODE_ALLOWLIST = {
    "formation_condition_classes": {
        "LEVERAGE_DOMINATES_WITHOUT_SPOT_VALUE",
        "DELEVERAGING_WITH_SPOT_ABSORPTION",
        "FUNDING_COST_REMAINS_ELEVATED",
    },
    "invalidation_condition_classes": {
        "SPOT_VALUE_ACCEPTED_ABOVE_80000",
        "NATIVE_SPOT_SELLING_DOMINATES",
        "LEVERAGE_COST_NORMALIZES",
    },
}
_DECLARED_COMMITMENT_VALUE_CODES = set().union(
    *COMMITMENT_SNAPSHOT_CODE_ALLOWLIST.values(),
    *COMMITMENT_AMOUNT_BASIS_CODE_ALLOWLIST.values(),
    *COMMITMENT_CONDITION_CODE_ALLOWLIST.values(),
)
if _DECLARED_COMMITMENT_VALUE_CODES != COMMITMENT_VALUE_CODES:
    raise RuntimeError(
        "COMMITMENT_VALUE_DISPLAY must cover exactly every controlled commitment code"
    )
if len(set(COMMITMENT_CHANGE_FIELD_DISPLAY.values())) != len(
    COMMITMENT_CHANGE_FIELD_DISPLAY
):
    raise RuntimeError("commitment change-field displays must be unique")
if len(set(COMMITMENT_VALUE_DISPLAY.values())) != len(COMMITMENT_VALUE_DISPLAY):
    raise RuntimeError("commitment value displays must be unique")
_ALL_VISIBLE_SEMANTIC_LABELS = (
    list(REPORT_CODE_DISPLAY.values())
    + list(COMMITMENT_CHANGE_FIELD_DISPLAY.values())
    + list(COMMITMENT_VALUE_DISPLAY.values())
)
if len(set(_ALL_VISIBLE_SEMANTIC_LABELS)) != len(_ALL_VISIBLE_SEMANTIC_LABELS):
    raise RuntimeError("all controlled visible semantic labels must be unique")
PRIMARY_REPORT_HYPOTHESIS = {
    "CAUSE_UNRESOLVED_KEEP_LAST_VALID_ANCHOR": "H_UNRESOLVED",
    "CRYPTO_NATIVE_SPOT": "H_SPOT",
    "SPOT_LED_ACCEPTANCE": "H_SPOT",
    "DERIVATIVES_LEVERAGE_WITHOUT_SPOT_VALUE": "H_LEVERAGE",
    "DERIVATIVES_LEVERAGE": "H_LEVERAGE",
    "LEVERAGE_OVERLAY_ONLY": "H_LEVERAGE",
    "MACRO_BETA": "H_MACRO",
    "MACRO_OVERLAY_ONLY": "H_MACRO",
    "MIXED_CAUSAL_DRIVERS": "H_MIXED",
}
REPORT_REASON_BY_HYPOTHESIS = {
    "H_SPOT": "NO_ACCEPTANCE_SEQUENCE",
    "H_LEVERAGE": "NO_OI_SEQUENCE",
    "H_MACRO": "NO_MACRO_TIMELINE",
    "H_UNRESOLVED": "NO_TIMELINE",
    "H_MIXED": "NO_MIXED_DRIVER_SEQUENCE",
}


def _expected_rejected_reason(
    bundle: Mapping[str, Any], hypothesis_id: str
) -> str | None:
    """Derive a user-visible rejection reason from the actual evidence state."""

    matrix = bundle.get("evidence_matrix")
    statuses = {
        dimension: (
            item.get("status") if isinstance(item, Mapping) else None
        )
        for dimension, item in (
            matrix.items() if isinstance(matrix, Mapping) else ()
        )
    }
    if hypothesis_id == "H_SPOT":
        if statuses.get("cross_exchange_acceptance") != "VERIFIED":
            return "NO_ACCEPTANCE_SEQUENCE"
        if statuses.get("spot_flow") != "VERIFIED":
            return "NO_SPOT_FLOW_SEQUENCE"
        if statuses.get("capital_source") != "VERIFIED":
            return "NO_CAPITAL_SOURCE_SEQUENCE"
        return "SPOT_NOT_PRIMARY_DRIVER"
    if hypothesis_id == "H_LEVERAGE":
        if statuses.get("oi_timeline") != "VERIFIED":
            return "NO_OI_SEQUENCE"
        if statuses.get("derivatives_flow") != "VERIFIED":
            return "NO_DERIVATIVES_FLOW_SEQUENCE"
        if statuses.get("funding_basis") != "VERIFIED":
            return "NO_FUNDING_BASIS_SEQUENCE"
        return "LEVERAGE_NOT_PRIMARY_DRIVER"
    if hypothesis_id == "H_MACRO":
        if statuses.get("macro") != "VERIFIED":
            return "NO_MACRO_TIMELINE"
        if statuses.get("relative_strength") != "VERIFIED":
            return "NO_RELATIVE_STRENGTH_SEQUENCE"
        return "MACRO_NOT_PRIMARY_DRIVER"
    if hypothesis_id == "H_UNRESOLVED":
        return (
            "CAUSAL_SEQUENCE_RESOLVED"
            if bundle.get("decision_evidence_status") == "RESOLVED"
            else "NO_TIMELINE"
        )
    if hypothesis_id == "H_MIXED":
        verified_causal_dimensions = sum(
            1
            for dimension in CAUSAL_CORE_DIMENSIONS
            if statuses.get(dimension) == "VERIFIED"
        )
        return (
            "MIXED_NOT_PRIMARY_DRIVER"
            if verified_causal_dimensions >= 2
            else "NO_MIXED_DRIVER_SEQUENCE"
        )
    return None


UNCERTAINTY_EVIDENCE_DIMENSIONS = {
    "SPOT_FLOW": ("spot_flow",),
    "SPOT": ("spot_flow",),
    "OI_TIMELINE": ("oi_timeline",),
    "OI": ("oi_timeline",),
    "CAPITAL_SOURCE": ("capital_source",),
    "MACRO_TIMELINE": ("macro",),
}
NEXT_EVIDENCE_DIMENSIONS = {
    "CROSS_VENUE_ACCEPTANCE_WITH_CAUSAL_TIMELINE": (
        "cross_exchange_acceptance", "spot_flow", "oi_timeline", "capital_source",
    ),
    "MACRO_CAUSAL_TIMELINE": ("macro", "relative_strength"),
}
PORTFOLIO_REPORT_BY_DECISION = {
    "MAINTAIN": "MAINTAIN",
    "CONDITIONAL_ADD": "CONDITIONAL_ADD_600U",
    "REVIEW_AFTER_KNOWN_FACT_CHANGE": "REVIEW_AFTER_FACT_SYNC",
    "FREEZE_INCREMENTAL_RISK": "FREEZE_INCREMENTAL_RISK",
    "REDUCE_REVIEW": "PORTFOLIO_REDUCE_REVIEW",
    "EXIT_REVIEW": "PORTFOLIO_EXIT_REVIEW",
    "SAFETY_OVERRIDE": "PORTFOLIO_SAFETY_OVERRIDE",
}
REPORT_AUTHORIZATION_DISCLAIMER = "这些判断只用于你的评估，不代表成交或交易授权。"
SET_LIKE_FIELDS = {
    "sources",
    "independence_groups",
    "counter_evidence",
    "missing_evidence",
    "falsification_conditions",
    "next_discriminating_evidence",
    "risk_flags",
    "driver_components",
    "evidence_considered",
    "supporting_evidence",
    "kinds",
    "rejected_competing_explanations",
    "decision_relevant_levels_and_sizes",
    "material_uncertainties",
    "next_decision_changing_evidence",
    "user_visible_major_risk",
    "formation_conditions",
    "invalidation_conditions",
    "commitment_change_fields",
}
DECIMAL_FIELD_RE = re.compile(
    r"(?:^|_)(?:amount|price|lower|upper|open|high|low|close|boundary)(?:_u|_usd)?$",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
PROJECT_JST_TIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)? JST\Z"
)

TRIGGER_TYPES = {
    "PLAN_GENERATION",
    "SCHEDULED_STRATEGY_BOUNDARY",
    "MANUAL_REEVALUATION",
    "IMPLEMENTATION_MIGRATION",
}
DERIVATION_STATUSES = {"READY"}
ACCOUNT_FACT_SOURCES = {"LOCAL_CONFIRMED_RECORDS"}
CREATED_BY_TRANSITIONS = {"D", "E", "MIGRATION"}
OBSERVATION_NORMALIZATION_REVISIONS = {"BTC_OBSERVATION_V3"}
SOURCE_HEALTH_CLASSES = {
    "PUBLIC_QUOTES_HEALTHY_DECISION_DATA_LIMITED",
    "DECISIVE_SOURCE_CONFLICT",
    "PUBLIC_QUOTES_CONFLICTED",
    "PUBLIC_QUOTES_UNAVAILABLE",
}
EVENT_DISCRIMINATOR_STATUSES = {
    "NO_DISCRIMINATING_EVENT",
    "DEEP_REVIEW_CANDIDATE",
    "FINAL_EVENT_REACHED",
}
FINAL_EXPIRY_STATUSES = {"PENDING", "COMPLETED"}
EVIDENCE_STATUSES = {"VERIFIED", "UNRESOLVED", "CONTRADICTED", "UNAVAILABLE"}
DRIVER_COMPONENTS = {
    "CRYPTO_NATIVE_SPOT", "DERIVATIVES_LEVERAGE", "MACRO_BETA",
}
HYPOTHESIS_IDS = {
    "H_UNRESOLVED", "H_SPOT", "H_LEVERAGE", "H_MACRO", "H_MIXED",
}
ZONE_ROLES = {
    "PERSISTENT_VALUE_DISPUTE_ZONE", "UNRETESTED_VALUE_ZONE",
    "CURRENT_CYCLE_VALUE_ZONE", "CONFIRMED_VALUE_ZONE",
}
MIGRATION_STATES = {
    "NO_MIGRATION", "UPWARD_CONFIRMED", "DOWNWARD_CONFIRMED",
    "DISTRIBUTION_WIDENED",
}
PORTFOLIO_DECISIONS = {
    "MAINTAIN", "CONDITIONAL_ADD", "REVIEW_AFTER_KNOWN_FACT_CHANGE",
    "FREEZE_INCREMENTAL_RISK", "REDUCE_REVIEW", "EXIT_REVIEW",
    "SAFETY_OVERRIDE",
}
PORTFOLIO_DECISIONS_BY_MODULE = {
    "existing_digital_beta": PORTFOLIO_DECISIONS.difference({"CONDITIONAL_ADD"}),
    "existing_alpha_short": PORTFOLIO_DECISIONS.difference({"CONDITIONAL_ADD"}),
    "new_spot": {
        "MAINTAIN", "CONDITIONAL_ADD", "REVIEW_AFTER_KNOWN_FACT_CHANGE",
        "FREEZE_INCREMENTAL_RISK", "SAFETY_OVERRIDE",
    },
    "new_short": {
        "MAINTAIN", "CONDITIONAL_ADD", "REVIEW_AFTER_KNOWN_FACT_CHANGE",
        "FREEZE_INCREMENTAL_RISK", "SAFETY_OVERRIDE",
    },
    "optionality": {
        "MAINTAIN", "REVIEW_AFTER_KNOWN_FACT_CHANGE",
        "FREEZE_INCREMENTAL_RISK", "SAFETY_OVERRIDE",
    },
}
CANDIDATE_KINDS = {"PREMIUM_SHORT", "DISCOUNT_SPOT"}
CANDIDATE_ELIGIBILITIES = {
    "CONDITIONAL_INACTIVE", "ELIGIBLE_FOR_REVIEW", "INVALIDATED",
}
ENTRY_ELIGIBILITIES = {"INACTIVE", "ELIGIBLE_FOR_REVIEW", "INVALIDATED"}

TIME_RANGE_KINDS = {"POINT_IN_TIME", "MULTI_PERIOD", "NOT_ESTABLISHED"}
EVIDENCE_PAYLOAD_KINDS = {
    "price_quotes": "BTC_SPOT_TICKER",
    "cross_exchange_acceptance": "BTC_CROSS_VENUE_EXECUTION_SEQUENCE",
    "spot_flow": "BTC_NATIVE_SPOT_FLOW_SEQUENCE",
    "derivatives_flow": "BTC_DERIVATIVES_FLOW_SEQUENCE",
    "oi_timeline": "BTC_OPEN_INTEREST_SEQUENCE",
    "funding_basis": "BTC_FUNDING_BASIS_SEQUENCE",
    "capital_source": "BTC_CAPITAL_SOURCE_SEQUENCE",
    "macro": "MACRO_EVENT_SEQUENCE",
    "relative_strength": "BTC_RELATIVE_STRENGTH_SEQUENCE",
    "account_impact": "LOCAL_ACCOUNT_FACT_RECORD",
}
CLAIM_DIMENSIONS = {
    "CROSS_PERIOD_ACCEPTANCE": "cross_exchange_acceptance",
    "RETENTION": "cross_exchange_acceptance",
    "TWO_WAY_EXECUTION": "cross_exchange_acceptance",
    "EFFECTIVE_RETEST": "cross_exchange_acceptance",
    "DISTRIBUTION_EXPANSION": "cross_exchange_acceptance",
    "NATIVE_SPOT_FLOW": "spot_flow",
    "NATIVE_SPOT_BUY_FLOW": "spot_flow",
    "NATIVE_SPOT_BUY": "spot_flow",
    "NATIVE_SPOT_SELL_FLOW": "spot_flow",
    "NATIVE_SPOT_SELL": "spot_flow",
    "DERIVATIVES_ACTIVE_FLOW": "derivatives_flow",
    "DERIVATIVES_LEVERAGED_BUY_DOMINANCE": "derivatives_flow",
    "DERIVATIVES_DELEVERAGING_FLOW": "derivatives_flow",
    "OI_TIMELINE": "oi_timeline",
    "OI_DELEVERAGING": "oi_timeline",
    "LEVERAGE_COST_EXPANSION": "funding_basis",
    "CAPITAL_SOURCE": "capital_source",
    "MACRO_EVENT_TIMELINE": "macro",
    "VALUE_ZONE_VALIDATED": "cross_exchange_acceptance",
    "VALUE_ZONE_RETEST_PENDING": "cross_exchange_acceptance",
    "VALUE_ZONE_RETEST_CONFIRMED": "cross_exchange_acceptance",
    "VALUE_ZONE_INVALIDATED": "cross_exchange_acceptance",
    "SPOT_VALUE_NON_ACCEPTANCE": "cross_exchange_acceptance",
}
LIFECYCLE_CLAIM_BY_STATE = {
    "CURRENT_CYCLE_VALIDATED": "VALUE_ZONE_VALIDATED",
    "RETEST_PENDING": "VALUE_ZONE_RETEST_PENDING",
    "RETEST_CONFIRMED": "VALUE_ZONE_RETEST_CONFIRMED",
    "INVALIDATED": "VALUE_ZONE_INVALIDATED",
}
ZONE_SCOPED_CLAIM_TYPES = set(LIFECYCLE_CLAIM_BY_STATE.values()).union(
    {"SPOT_VALUE_NON_ACCEPTANCE"}
)

BUNDLE_KEYS = {
    "schema", "bundle_phase", "strategy_revision", "observation",
    "quote_verification_status", "decision_evidence_status", "evidence_matrix",
    "causal_attribution", "value_zones", "anchor_map_semantics", "path_decision",
    "forward_value_band_semantics", "portfolio_decision", "trade_candidates",
    "major_risk", "account_facts", "user_input_requirement", "projection_mode",
    "event", "report_semantics", "runtime", "previous_fingerprints",
    "previous_report_semantics", "fingerprints", "control", "write_transition",
    "previous_strategy_revision", "previous_revision2_state_fingerprint",
    "previous_revision2_plan_fingerprint", "previous_revision2_report_fingerprint",
    "previous_revision2_report_projection_fingerprint", "previous_delivery",
    "previous_projection_baseline",
}
OBSERVATION_KEYS = {
    "normalization_revision", "source_health_class", "quote_relation_by_venue",
    "closed_bar_relation_sequence", "cross_venue_divergence_class",
    "evidence_availability_by_dimension", "event_discriminator_status",
    "final_expiry_status", "price_relation", "prior_price_relation",
    "relation_transition", "raw_prices", "observed_at",
    "hourly_review_binding",
}
OBSERVATION_REQUIRED_KEYS = OBSERVATION_KEYS - {"hourly_review_binding"}
EVIDENCE_KEYS = {
    "status", "sources", "time_range", "supporting", "conflicts", "missing",
    "evidence_claims", "independence_groups", "access_scope",
}
QUOTE_SOURCE_KEYS = {
    "source_id", "endpoint_url", "payload_kind", "canonical_origin", "venue",
    "access_scope", "retrieved_at", "as_of",
}
PUBLIC_SOURCE_KEYS = {
    "source_id", "endpoint_url", "payload_kind", "canonical_origin",
    "access_scope", "venue", "retrieved_at", "as_of",
}
LOCAL_SOURCE_KEYS = {
    "source_id", "payload_kind", "access_scope", "retrieved_at", "as_of",
}
TIME_RANGE_KEYS = {"kind", "start", "end"}
CLAIM_KEYS = {
    "claim_id", "claim_type", "status", "source_ids", "time_range", "metric",
    "direction", "subject_zone_id",
}
METRIC_KEYS = {"metric_id", "unit", "aggregation", "periods"}
CAUSAL_KEYS = {
    "driver_regime", "driver_components", "selected_hypothesis_id",
    "rejected_hypothesis_ids", "reasoning_summary", "hypotheses",
    "material_pattern",
}
CAUSAL_REQUIRED_KEYS = CAUSAL_KEYS - {"material_pattern"}
ZONE_KEYS = {"zone_id", "role", "band", "lifecycle"}
ZONE_BAND_KEYS = {"lower_u", "upper_u"}
ANCHOR_KEYS = {"persistent_anchor_ids", "unretested_anchor_ids"}
FORWARD_BAND_KEYS = {"primary_zone_ids", "migration_state"}
PORTFOLIO_ITEM_KEYS = {
    "local_fact_basis", "portfolio_role", "decision", "amount_u", "reason_class",
    "zero_amount_reason_class", "formation_conditions", "invalidation_conditions",
    "net_exposure_impact", "capital_pool_impact", "evidence_considered",
}
CANDIDATE_KEYS = {"candidate_id", "kind", "eligibility", "report_relevant", "entries"}
ENTRY_KEYS = {
    "entry_id", "price_u", "price_band_u", "amount_u", "amount_basis",
    "value_role", "eligibility", "formation_conditions", "evidence_confidence",
    "account_precondition", "invalidation_conditions", "carried_forward",
}
AMOUNT_BASIS_KEYS = {
    "budget_source", "net_exposure_constraint", "value_gap_rationale",
    "risk_assumption", "continuation_reason",
}
MAJOR_RISK_KEYS = {"active", "kinds", "portfolio_effect"}
ACCOUNT_FACT_KEYS = {
    "account_fact_changed_known", "fact_owner_fingerprints",
    "known_change_evidence", "local_record_status",
}
FACT_CONFIRMATION_KEYS = {"confirmation_type", "owner_id"}
FACT_CONFIRMATION_TYPES = {
    "USER_CONFIRMED_POSITION_CHANGE",
    "USER_CONFIRMED_EXECUTION",
    "USER_CONFIRMED_ACCOUNT_FACT_CORRECTION",
}
PREFERENCE_CONFIRMATION_TYPE = "USER_CONFIRMED_PREFERENCE_CHANGE"
CAUSAL_TIMELINE_EVENT_KEYS = {
    "sequence", "occurred_at", "evidence_dimension", "evidence_ref", "event_role",
}
CAUSAL_TIMELINE_EVENT_ROLES = {
    "CAUSE_OBSERVED", "PRICE_RESPONSE_OBSERVED", "RETENTION_OBSERVED",
    "UNRESOLVED_PRICE_OBSERVATION",
}
CAUSAL_EVENT_DIMENSIONS = {
    "CAUSE_OBSERVED": {
        "spot_flow", "derivatives_flow", "oi_timeline", "funding_basis",
        "capital_source", "macro",
    },
    "PRICE_RESPONSE_OBSERVED": {"price_quotes"},
    "RETENTION_OBSERVED": {"cross_exchange_acceptance"},
    "UNRESOLVED_PRICE_OBSERVATION": {"price_quotes"},
}
EVENT_KEYS = {"final_event_reached", "cycle", "trigger"}
RUNTIME_KEYS = {
    "run_id", "state_id", "plan_id", "pricing_as_of", "trigger_type",
}
PREVIOUS_FINGERPRINT_KEYS = {"decision_fingerprint", "report_fingerprint"}
PREVIOUS_PROJECTION_BASELINE_KEYS = {
    "schema", "state_content_fingerprint", "plan_content_fingerprint",
    "baseline_bundle_identity", "canonical_semantics_fingerprint",
    "strategy_id", "strategy_revision", "state_id", "plan_id", "cycle_id",
    "observation_fingerprint", "decision_fingerprint", "report_fingerprint",
}
CONTROL_KEYS = {
    "write_transition", "decision_class", "monitor_state", "project_patch_required",
    "report_required", "attention_planned", "attention_level", "decision_changed",
    "report_changed",
}
PREVIOUS_DELIVERY_KEYS = {
    "cloud_delivery_state", "report_visible", "report_body_fingerprint",
    "cloud_visible_body_fingerprint", "cloud_readback_fingerprint",
}
CLOUD_READBACK_KEYS = {"schema", "visible_report_text", "assistant_responses"}
REPORT_REJECTED_ITEM_KEYS = {"hypothesis_id", "reason_class"}
REPORT_LEVEL_BASE_KEYS = {"level_id", "price_u", "price_band_u", "amount_u"}
REPORT_LEVEL_ITEM_KEYS = REPORT_LEVEL_BASE_KEYS | {
    "commitment_change_fields", "commitment_snapshot",
}
REPORT_COMMITMENT_SNAPSHOT_KEYS = {
    "candidate_kind", "value_role", "entry_eligibility", "amount_basis_classes",
    "formation_condition_classes", "invalidation_condition_classes",
    "evidence_confidence", "account_precondition", "carried_forward",
}
REPORT_AMOUNT_BASIS_CLASS_KEYS = {
    "budget_source", "net_exposure_constraint", "value_gap_rationale",
    "risk_assumption", "continuation_reason",
}
CLOSED_BAR_KEYS = {
    "open_u", "high_u", "low_u", "close_u", "boundary_u",
    "open_relation", "high_relation", "low_relation", "close_relation",
    "bar_status", "open_time_ms", "close_time_ms", "as_of_ms", "boundary_role",
}
UNAVAILABLE_OBSERVATION_KEYS = {
    "schema", "observation", "value_zones", "anchor_map_semantics",
    "price_quote_evidence", "fact_owner_fingerprints", "evaluation_as_of",
}
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
LOCAL_HOST_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".lan", ".home",
    ".home.arpa", ".test", ".example", ".invalid", ".onion",
)
DNS_TO_ADDRESS_SUFFIXES = (
    "nip.io", "sslip.io", "xip.io", "localtest.me", "lvh.me", "vcap.me",
)
PUBLIC_DNS_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MigrationBaseline:
    state_path: Path
    plan_path: Path
    report_path: Path
    report_projection_path: Path
    state_text: str
    plan_text: str
    report_text: str
    report_projection: Mapping[str, Any]


@dataclass(frozen=True)
class Revision3Baseline:
    """A write-before snapshot of the two revision-3 Owner projections."""

    state_path: Path
    plan_path: Path
    state_text: str
    plan_text: str
    binding: Mapping[str, str]


@dataclass(frozen=True)
class FactOwnerSnapshot:
    """Byte-exact snapshot of the three local account-fact Owner documents."""

    paths: Mapping[str, Path]
    texts: Mapping[str, str]
    fingerprints: Mapping[str, str]


class GuardError(ValueError):
    """Raised for malformed inputs that cannot be normalized safely."""


def _reject_unknown_keys(
    value: Any, allowed: set[str], path: str, errors: list[str]
) -> bool:
    """Enforce an exact key boundary at one schema node.

    This helper is deliberately called at every nested object boundary.  A
    global forbidden-name scan is not a schema: an attacker can always choose
    a new key and hide completion facts below an otherwise benign container.
    """

    if not isinstance(value, Mapping):
        errors.append(f"{path} must be an object")
        return False
    for key in value:
        if not isinstance(key, str) or key not in allowed:
            errors.append(f"{path} field is not allowed: {key}")
    return True


def _validate_string_list(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        errors.append(f"{path} must be an array of non-empty strings")


def _parse_iso8601(value: Any, path: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be an ISO-8601 timestamp")
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        errors.append(f"{path} must be an ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{path} must include a timezone offset")
        return None
    return parsed


def _parse_project_jst_time(
    value: Any, path: str, errors: list[str]
) -> datetime | None:
    if not isinstance(value, str) or not PROJECT_JST_TIME_RE.fullmatch(value):
        errors.append(f"{path} must use YYYY-MM-DD HH:MM[:SS] JST")
        return None
    try:
        return datetime.fromisoformat(value.removesuffix(" JST"))
    except ValueError:
        errors.append(f"{path} must be a real project timestamp")
        return None


def _parse_endpoint(endpoint: Any) -> Any | None:
    """Parse an endpoint without allowing malformed ports/hosts to escape validation."""

    if not isinstance(endpoint, str):
        return None
    try:
        parsed = urlparse(endpoint)
        # Accessors are lazy and may raise (notably ``port`` and malformed IPv6).
        _ = parsed.hostname
        _ = parsed.username
        _ = parsed.password
        _ = parsed.port
    except (ValueError, UnicodeError):
        return None
    return parsed


def _is_public_network_hostname(hostname: Any) -> bool:
    """Accept only a canonical, public-DNS-shaped hostname.

    This intentionally does not resolve DNS.  Every IP literal is rejected,
    regardless of address family or apparent routability, so provenance does
    not depend on exhaustively recognizing IPv6 transition/tunnel encodings.
    DNS ownership and content verification remain responsibilities of the
    surrounding evidence-collection boundary.
    """

    if not isinstance(hostname, str):
        return False
    # A trailing root dot is semantically equivalent in DNS but is not the
    # canonical spelling accepted by this provenance contract.
    if hostname.endswith("."):
        return False
    normalized = hostname.casefold()
    if not normalized or normalized == "localhost" or normalized.endswith(
        LOCAL_HOST_SUFFIXES
    ):
        return False
    # RFC 3986 IPvFuture literals lose their brackets in ``parsed.hostname``
    # and can otherwise look like an ordinary multi-label DNS name.  No
    # provenance DNS identity beginning with this reserved literal shape is
    # accepted by this conservative contract.
    if re.match(r"v[0-9a-f]+\.", normalized):
        return False
    if any(
        normalized == suffix or normalized.endswith("." + suffix)
        for suffix in DNS_TO_ADDRESS_SUFFIXES
    ):
        return False
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        # Reject alternative numeric spellings that some URL clients interpret
        # as IP literals, and require a normal multi-label ASCII DNS name.
        if re.fullmatch(r"[0-9.]+", normalized) or normalized.startswith("0x"):
            return False
        labels = normalized.split(".")
        for index in range(max(0, len(labels) - 3)):
            candidate_labels = labels[index : index + 4]
            if not all(
                re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", label)
                for label in candidate_labels
            ):
                continue
            # Four consecutive numeric/hex-like labels are an encoded-address
            # shape, not an accepted provenance DNS name.  Reject the shape
            # regardless of whether its decimal spelling denotes a public,
            # private, or even syntactically invalid IPv4 address.
            return False
        return PUBLIC_DNS_HOST_RE.fullmatch(normalized) is not None
    # PUBLIC_UNAUTHENTICATED provenance never accepts a literal address.  This
    # covers native IPv4/IPv6 and every parser-recognized mapped, embedded,
    # transition, tunnel, scoped, hexadecimal, or dotted-tail spelling.
    return False


def _has_bracketed_authority(parsed: Any) -> bool:
    """Return whether a parsed URL used literal-host bracket syntax."""

    netloc = getattr(parsed, "netloc", None)
    return not isinstance(netloc, str) or "[" in netloc or "]" in netloc


def _validate_time_range(
    value: Any,
    path: str,
    errors: list[str],
    *,
    not_after: datetime | None = None,
) -> str | None:
    if not _reject_unknown_keys(value, TIME_RANGE_KEYS, path, errors):
        return None
    kind = value.get("kind")
    if kind not in TIME_RANGE_KINDS:
        errors.append(f"{path}.kind is invalid")
        return None
    if kind == "NOT_ESTABLISHED":
        if set(value) != {"kind"}:
            errors.append(f"{path} NOT_ESTABLISHED cannot contain start/end")
        return kind
    if set(value) != TIME_RANGE_KEYS:
        errors.append(f"{path} {kind} requires exactly kind/start/end")
        return kind
    start = _parse_iso8601(value.get("start"), f"{path}.start", errors)
    end = _parse_iso8601(value.get("end"), f"{path}.end", errors)
    if start is not None and end is not None:
        if end < start:
            errors.append(f"{path}.end must not precede start")
        if kind == "POINT_IN_TIME" and end != start:
            errors.append(f"{path} POINT_IN_TIME requires start=end")
        if kind == "MULTI_PERIOD" and end <= start:
            errors.append(f"{path} MULTI_PERIOD requires end>start")
        if not_after is not None and end > not_after:
            errors.append(f"{path}.end cannot exceed observation.observed_at")
    return kind


def _validate_https_source(
    source: Any,
    dimension: str,
    index: int,
    errors: list[str],
    *,
    observed_at: datetime | None = None,
) -> None:
    path = f"evidence_matrix.{dimension}.sources[{index}]"
    if dimension == "account_impact":
        if not _reject_unknown_keys(source, LOCAL_SOURCE_KEYS, path, errors):
            return
        if set(source) != LOCAL_SOURCE_KEYS:
            errors.append(f"{path} must contain exactly {sorted(LOCAL_SOURCE_KEYS)}")
        expected = {
            "access_scope": "LOCAL_CONFIRMED_RECORD",
            "payload_kind": EVIDENCE_PAYLOAD_KINDS[dimension],
        }
        for field, value in expected.items():
            if source.get(field) != value:
                errors.append(f"{path}.{field} must equal {value}")
        if _source_identity(source) is None:
            errors.append(f"{path}.source_id is required")
        source_times: dict[str, datetime] = {}
        for field in ("as_of", "retrieved_at"):
            instant = _parse_iso8601(source.get(field), f"{path}.{field}", errors)
            if instant is not None:
                source_times[field] = instant
                if observed_at is not None and instant > observed_at:
                    errors.append(
                        f"{path}.{field} cannot exceed observation.observed_at"
                    )
        if set(source_times) == {"as_of", "retrieved_at"} and source_times[
            "as_of"
        ] > source_times["retrieved_at"]:
            errors.append(f"{path}.as_of cannot exceed retrieved_at")
        return

    allowed = QUOTE_SOURCE_KEYS if dimension == "price_quotes" else PUBLIC_SOURCE_KEYS
    if not _reject_unknown_keys(source, allowed, path, errors):
        return
    if set(source) != allowed:
        errors.append(f"{path} must contain exactly {sorted(allowed)}")
    endpoint = source.get("endpoint_url")
    parsed = _parse_endpoint(endpoint)
    if (
        parsed is None
        or parsed.scheme != "https"
        or not parsed.hostname
        or _has_bracketed_authority(parsed)
        or not _is_public_network_hostname(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
        or parsed.hostname.casefold().endswith(".invalid")
    ):
        errors.append(f"{path}.endpoint_url must be a legal public HTTPS provenance URL")
    if source.get("access_scope") != "PUBLIC_UNAUTHENTICATED":
        errors.append(f"{path}.access_scope must be PUBLIC_UNAUTHENTICATED")
    payload_kinds = {EVIDENCE_PAYLOAD_KINDS[dimension]}
    if dimension == "price_quotes":
        payload_kinds.add("BTC_SPOT_CANDLE_CLOSE")
    if source.get("payload_kind") not in payload_kinds:
        errors.append(
            f"{path}.payload_kind must be one of {sorted(payload_kinds)}"
        )
    if dimension != "price_quotes" and _is_registered_quote_endpoint(endpoint):
        errors.append(
            f"{path}.endpoint_url is a quote-only endpoint and cannot prove {dimension}"
        )
    if _source_identity(source) is None:
        errors.append(f"{path}.source_id is required")
    if not isinstance(source.get("venue"), str) or not source["venue"].strip():
        errors.append(f"{path}.venue must be a venue or controlled NOT_APPLICABLE scope")
    if parsed is not None and parsed.hostname and dimension != "price_quotes":
        expected_origin = parsed.hostname.casefold()
        if _source_identity(source.get("canonical_origin")) != expected_origin:
            errors.append(f"{path}.canonical_origin must equal endpoint hostname")
    source_times: dict[str, datetime] = {}
    for field in ("as_of", "retrieved_at"):
        instant = _parse_iso8601(source.get(field), f"{path}.{field}", errors)
        if instant is not None:
            source_times[field] = instant
            if observed_at is not None and instant > observed_at:
                errors.append(f"{path}.{field} cannot exceed observation.observed_at")
    if set(source_times) == {"as_of", "retrieved_at"} and source_times[
        "as_of"
    ] > source_times["retrieved_at"]:
        errors.append(f"{path}.as_of cannot exceed retrieved_at")


def _validate_metric(value: Any, path: str, errors: list[str]) -> None:
    if not _reject_unknown_keys(value, METRIC_KEYS, path, errors):
        return
    if set(value) != METRIC_KEYS:
        errors.append(f"{path} requires exactly metric_id/unit/aggregation/periods")
    for field in ("metric_id", "unit", "aggregation"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"{path}.{field} must be a non-empty string")
    periods = value.get("periods")
    if isinstance(periods, bool) or not isinstance(periods, int) or periods < 1:
        errors.append(f"{path}.periods must be a positive integer")


def _evidence_reference_ids(matrix: Any, dimension: str) -> set[str]:
    item = matrix.get(dimension) if isinstance(matrix, Mapping) else None
    if not isinstance(item, Mapping):
        return set()
    result = {
        source_id
        for source in item.get("sources", [])
        if (source_id := _source_identity(source)) is not None
    } if isinstance(item.get("sources"), list) else set()
    claims = item.get("evidence_claims")
    if isinstance(claims, list):
        result.update(
            claim_id
            for claim in claims
            if isinstance(claim, Mapping)
            and isinstance((claim_id := claim.get("claim_id")), str)
            and claim_id.strip()
        )
    return result


def _evidence_reference_window(
    matrix: Any, dimension: str, reference: str
) -> tuple[datetime, datetime] | None:
    item = matrix.get(dimension) if isinstance(matrix, Mapping) else None
    if not isinstance(item, Mapping):
        return None
    normalized = reference.strip().casefold()
    for source in item.get("sources", []):
        if _source_identity(source) != normalized or not isinstance(source, Mapping):
            continue
        point_errors: list[str] = []
        point = _parse_iso8601(source.get("as_of"), "source.as_of", point_errors)
        return (point, point) if point is not None and not point_errors else None
    for claim in item.get("evidence_claims", []):
        if not isinstance(claim, Mapping) or not isinstance(claim.get("claim_id"), str):
            continue
        if claim["claim_id"].strip().casefold() != normalized:
            continue
        value = claim.get("time_range")
        if not isinstance(value, Mapping) or value.get("kind") == "NOT_ESTABLISHED":
            return None
        range_errors: list[str] = []
        start = _parse_iso8601(value.get("start"), "claim.start", range_errors)
        end = _parse_iso8601(value.get("end"), "claim.end", range_errors)
        return (start, end) if start is not None and end is not None and not range_errors else None
    return None


def _evidence_reference_descriptor(
    matrix: Any, dimension: str, reference: str
) -> tuple[str, str | None, str | None] | None:
    """Return (kind, claim_type, status) for a dimension-bound reference."""

    item = matrix.get(dimension) if isinstance(matrix, Mapping) else None
    if not isinstance(item, Mapping):
        return None
    normalized = reference.strip().casefold()
    for source in item.get("sources", []):
        if _source_identity(source) == normalized:
            return ("SOURCE", None, item.get("status"))
    for claim in item.get("evidence_claims", []):
        if not isinstance(claim, Mapping) or not isinstance(claim.get("claim_id"), str):
            continue
        if claim["claim_id"].strip().casefold() == normalized:
            return ("CLAIM", _claim_type(claim), claim.get("status"))
    return None


def _validate_causal_timeline(
    value: Any,
    path: str,
    matrix: Any,
    observed_at: datetime | None,
    errors: list[str],
    *,
    require_resolved_sequence: bool,
) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{path} must be a non-empty array")
        return
    previous_time: datetime | None = None
    roles: list[str] = []
    for index, event in enumerate(value):
        event_path = f"{path}[{index}]"
        if not _reject_unknown_keys(
            event, CAUSAL_TIMELINE_EVENT_KEYS, event_path, errors
        ):
            continue
        if set(event) != CAUSAL_TIMELINE_EVENT_KEYS:
            errors.append(f"{event_path} must contain exactly the causal event fields")
        if event.get("sequence") != index + 1:
            errors.append(f"{event_path}.sequence must be contiguous and start at 1")
        dimension = event.get("evidence_dimension")
        if dimension not in REQUIRED_EVIDENCE_DIMENSIONS:
            errors.append(f"{event_path}.evidence_dimension is invalid")
        reference = event.get("evidence_ref")
        if not isinstance(reference, str) or not reference.strip():
            errors.append(f"{event_path}.evidence_ref must be a non-empty string")
        elif isinstance(dimension, str) and reference.casefold() not in {
            item.casefold() for item in _evidence_reference_ids(matrix, dimension)
        }:
            errors.append(f"{event_path}.evidence_ref is not bound to its evidence dimension")
        role = event.get("event_role")
        if role not in CAUSAL_TIMELINE_EVENT_ROLES:
            errors.append(f"{event_path}.event_role is invalid")
        else:
            roles.append(role)
            allowed_dimensions = CAUSAL_EVENT_DIMENSIONS[role]
            if dimension not in allowed_dimensions:
                errors.append(
                    f"{event_path}.{role} cannot use evidence dimension {dimension!r}"
                )
            if isinstance(dimension, str) and isinstance(reference, str):
                descriptor = _evidence_reference_descriptor(
                    matrix, dimension, reference
                )
                if role == "CAUSE_OBSERVED" and (
                    descriptor is None
                    or descriptor[0] != "CLAIM"
                    or descriptor[2] != "VERIFIED"
                ):
                    errors.append(
                        f"{event_path}.CAUSE_OBSERVED must reference a VERIFIED typed claim"
                    )
                if role == "PRICE_RESPONSE_OBSERVED" and (
                    descriptor is None or descriptor[0] != "SOURCE"
                ):
                    errors.append(
                        f"{event_path}.PRICE_RESPONSE_OBSERVED must reference a price source"
                    )
                if role == "RETENTION_OBSERVED" and (
                    descriptor is None
                    or descriptor[0] != "CLAIM"
                    or descriptor[1] not in {
                        "CROSS_PERIOD_ACCEPTANCE", "RETENTION", "EFFECTIVE_RETEST",
                        "SPOT_VALUE_NON_ACCEPTANCE",
                    }
                    or descriptor[2] != "VERIFIED"
                ):
                    errors.append(
                        f"{event_path}.RETENTION_OBSERVED must reference a VERIFIED retention claim"
                    )
                if role == "UNRESOLVED_PRICE_OBSERVATION" and (
                    descriptor is None or descriptor[0] != "SOURCE"
                ):
                    errors.append(
                        f"{event_path}.UNRESOLVED_PRICE_OBSERVATION must reference a price source"
                    )
        occurred = _parse_iso8601(
            event.get("occurred_at"), f"{event_path}.occurred_at", errors
        )
        if occurred is not None:
            if observed_at is not None and occurred > observed_at:
                errors.append(f"{event_path}.occurred_at cannot exceed observation.observed_at")
            if previous_time is not None and occurred < previous_time:
                errors.append(f"{path} must be chronologically ordered")
            previous_time = occurred
            if isinstance(dimension, str) and isinstance(reference, str):
                window = _evidence_reference_window(matrix, dimension, reference)
                if window is not None and not (window[0] <= occurred <= window[1]):
                    errors.append(
                        f"{event_path}.occurred_at must fall within its referenced evidence time"
                    )
    if require_resolved_sequence:
        if "CAUSE_OBSERVED" not in roles or not any(
            role in {"PRICE_RESPONSE_OBSERVED", "RETENTION_OBSERVED"} for role in roles
        ):
            errors.append(
                f"{path} resolved hypothesis requires cause followed by price response or retention"
            )
        elif roles.index("CAUSE_OBSERVED") >= min(
            index for index, role in enumerate(roles)
            if role in {"PRICE_RESPONSE_OBSERVED", "RETENTION_OBSERVED"}
        ):
            errors.append(f"{path} cause must precede price response or retention")
        else:
            causal_events = [
                event for event in value
                if isinstance(event, Mapping)
                and event.get("event_role") == "CAUSE_OBSERVED"
            ]
            response_events = [
                event for event in value
                if isinstance(event, Mapping)
                and event.get("event_role") in {
                    "PRICE_RESPONSE_OBSERVED", "RETENTION_OBSERVED",
                }
            ]
            cause_times: list[datetime] = []
            response_times: list[datetime] = []
            for target, destination in (
                (causal_events, cause_times), (response_events, response_times)
            ):
                for event in target:
                    parsed = _parse_iso8601(event.get("occurred_at"), "causal event", [])
                    if parsed is not None:
                        destination.append(parsed)
            if cause_times and response_times and min(response_times) <= max(cause_times):
                errors.append(
                    f"{path} verified cause time must strictly precede the first response"
                )


def _is_expressive_report(value: Any) -> bool:
    """Recognize the production semantic/narrative contract, not a report code."""
    return isinstance(value, Mapping) and isinstance(
        value.get("overall_user_decision"), Mapping
    ) and "semantic" in value["overall_user_decision"]


def _production_contract_errors(bundle: Any) -> list[str]:
    if not isinstance(bundle, Mapping):
        return ["production PRE_WRITE must be an object"]
    errors = []
    if not _is_expressive_report(bundle.get("report_semantics")):
        errors.append("production requires semantic/narrative reports; fixed report codes are replay-only")
    event = bundle.get("event")
    if not isinstance(event, Mapping) or set(event) != {"final_event_reached", "cycle", "trigger"}:
        errors.append("production requires a persisted event cycle and bound trigger; boolean-only events are replay-only")
    return errors


def _validate_report_semantics_shape(value: Any, path: str, errors: list[str]) -> None:
    if _is_expressive_report(value):
        from decision_report import validate_report_projection
        errors.extend(validate_report_projection(value))
        return
    if not _reject_unknown_keys(value, set(REPORT_SEMANTIC_FIELDS), path, errors):
        return
    if set(value) != set(REPORT_SEMANTIC_FIELDS):
        errors.append(f"{path} must contain exactly the eight report semantic fields")
    if len(set(REPORT_CODE_DISPLAY.values())) != len(REPORT_CODE_DISPLAY):
        errors.append("REPORT_CODE_DISPLAY values must be globally unique")
    allowlisted_codes = set().union(*REPORT_CODE_ALLOWLIST.values())
    if allowlisted_codes != set(REPORT_CODE_DISPLAY):
        errors.append("report code display and contextual allowlists must cover each other exactly")
    for field in ("overall_user_decision", "primary_causal_explanation"):
        code = value.get(field)
        if not isinstance(code, str) or code not in REPORT_CODE_ALLOWLIST[field]:
            errors.append(f"{path}.{field} must be a controlled report semantic code")
    rejected = value.get("rejected_competing_explanations")
    rejected_ids: set[str] = set()
    if not isinstance(rejected, list) or not rejected:
        errors.append(f"{path}.rejected_competing_explanations must be non-empty")
    else:
        for index, item in enumerate(rejected):
            item_path = f"{path}.rejected_competing_explanations[{index}]"
            if _reject_unknown_keys(item, REPORT_REJECTED_ITEM_KEYS, item_path, errors):
                hypothesis_code = item.get("hypothesis_id")
                reason_code = item.get("reason_class")
                if (
                    set(item) != REPORT_REJECTED_ITEM_KEYS
                    or not isinstance(hypothesis_code, str)
                    or hypothesis_code not in REPORT_CODE_ALLOWLIST["rejected_hypothesis_id"]
                    or not isinstance(reason_code, str)
                    or reason_code not in REPORT_CODE_ALLOWLIST["rejected_reason_class"]
                ):
                    errors.append(f"{item_path} requires controlled hypothesis and reason codes")
                elif isinstance(hypothesis_code, str):
                    rejected_ids.add(hypothesis_code)
    primary_hypothesis = PRIMARY_REPORT_HYPOTHESIS.get(
        value.get("primary_causal_explanation")
    )
    if primary_hypothesis is not None and primary_hypothesis in rejected_ids:
        errors.append(
            f"{path}.primary_causal_explanation contradicts rejected_competing_explanations"
        )
    portfolio = value.get("portfolio_action_by_module")
    if _reject_unknown_keys(portfolio, set(PORTFOLIO_MODULES), f"{path}.portfolio_action_by_module", errors):
        if set(portfolio) != set(PORTFOLIO_MODULES):
            errors.append(f"{path}.portfolio_action_by_module must contain exactly five modules")
        for module in PORTFOLIO_MODULES:
            action = portfolio.get(module)
            if not isinstance(action, str) or action not in REPORT_CODE_ALLOWLIST["portfolio_action"]:
                errors.append(
                    f"{path}.portfolio_action_by_module.{module} must be a controlled code"
                )
    levels = value.get("decision_relevant_levels_and_sizes")
    if not isinstance(levels, list) or not levels:
        errors.append(f"{path}.decision_relevant_levels_and_sizes must be non-empty")
    else:
        for index, item in enumerate(levels):
            item_path = f"{path}.decision_relevant_levels_and_sizes[{index}]"
            if _reject_unknown_keys(item, REPORT_LEVEL_ITEM_KEYS, item_path, errors):
                level_id = item.get("level_id")
                if not isinstance(level_id, str) or level_id not in REPORT_CODE_ALLOWLIST["level_id"]:
                    errors.append(f"{item_path}.level_id must be a controlled code")
                has_change_fields = "commitment_change_fields" in item
                has_snapshot = "commitment_snapshot" in item
                if has_change_fields != has_snapshot:
                    errors.append(
                        f"{item_path} must contain commitment_change_fields and "
                        "commitment_snapshot together"
                    )
                if (has_change_fields or has_snapshot) and level_id not in {
                    "S1", "S2", "D1", "D2",
                }:
                    errors.append(
                        f"{item_path} structural value zones cannot contain "
                        "candidate commitment semantics"
                    )
                if has_change_fields:
                    change_fields = item.get("commitment_change_fields")
                    if (
                        not isinstance(change_fields, list)
                        or not change_fields
                        or any(
                            not isinstance(code, str)
                            or code not in COMMITMENT_CHANGE_FIELDS
                            for code in change_fields
                        )
                    ):
                        errors.append(
                            f"{item_path}.commitment_change_fields must be a "
                            "non-empty array of controlled change classes"
                        )
                    elif len(change_fields) != len(set(change_fields)):
                        errors.append(
                            f"{item_path}.commitment_change_fields must be unique"
                        )
                if has_snapshot:
                    snapshot = item.get("commitment_snapshot")
                    snapshot_path = f"{item_path}.commitment_snapshot"
                    if _reject_unknown_keys(
                        snapshot, REPORT_COMMITMENT_SNAPSHOT_KEYS,
                        snapshot_path, errors,
                    ):
                        if set(snapshot) != REPORT_COMMITMENT_SNAPSHOT_KEYS:
                            errors.append(
                                f"{snapshot_path} must contain exactly the controlled "
                                "commitment fields"
                            )
                        for field, allowed in COMMITMENT_SNAPSHOT_CODE_ALLOWLIST.items():
                            code = snapshot.get(field)
                            if not isinstance(code, str) or code not in allowed:
                                errors.append(
                                    f"{snapshot_path}.{field} must be a controlled code"
                                )
                        amount_basis_classes = snapshot.get("amount_basis_classes")
                        amount_basis_path = f"{snapshot_path}.amount_basis_classes"
                        if _reject_unknown_keys(
                            amount_basis_classes, REPORT_AMOUNT_BASIS_CLASS_KEYS,
                            amount_basis_path, errors,
                        ):
                            if set(amount_basis_classes) != REPORT_AMOUNT_BASIS_CLASS_KEYS:
                                errors.append(
                                    f"{amount_basis_path} must contain exactly the "
                                    "controlled amount-basis fields"
                                )
                            for field, allowed in (
                                COMMITMENT_AMOUNT_BASIS_CODE_ALLOWLIST.items()
                            ):
                                code = amount_basis_classes.get(field)
                                if not isinstance(code, str) or code not in allowed:
                                    errors.append(
                                        f"{amount_basis_path}.{field} must be a "
                                        "controlled code"
                                    )
                        for field, allowed in COMMITMENT_CONDITION_CODE_ALLOWLIST.items():
                            codes = snapshot.get(field)
                            if (
                                not isinstance(codes, list)
                                or not codes
                                or any(
                                    not isinstance(code, str) or code not in allowed
                                    for code in codes
                                )
                            ):
                                errors.append(
                                    f"{snapshot_path}.{field} must be a non-empty "
                                    "array of controlled condition classes"
                                )
                            elif len(codes) != len(set(codes)):
                                errors.append(
                                    f"{snapshot_path}.{field} must be unique"
                                )
                        if not isinstance(snapshot.get("carried_forward"), bool):
                            errors.append(
                                f"{snapshot_path}.carried_forward must be boolean"
                            )
                if "amount_u" not in item:
                    errors.append(f"{item_path}.amount_u is required")
                else:
                    try:
                        amount = _decimal(item["amount_u"], f"{item_path}.amount_u")
                        if amount < 0:
                            errors.append(f"{item_path}.amount_u must be non-negative")
                    except GuardError as exc:
                        errors.append(str(exc))
                has_price = "price_u" in item
                has_band = "price_band_u" in item
                if has_price == has_band:
                    errors.append(
                        f"{item_path} must contain exactly one of price_u or price_band_u"
                    )
                if has_band:
                    band = item.get("price_band_u")
                    if _reject_unknown_keys(
                        band, ZONE_BAND_KEYS, f"{item_path}.price_band_u", errors
                    ) and set(band) != ZONE_BAND_KEYS:
                        errors.append(f"{item_path}.price_band_u requires lower_u and upper_u")
                    if isinstance(band, Mapping):
                        try:
                            lower = _decimal(band.get("lower_u"), f"{item_path}.price_band_u.lower_u")
                            upper = _decimal(band.get("upper_u"), f"{item_path}.price_band_u.upper_u")
                            if lower <= 0 or upper <= 0:
                                errors.append(f"{item_path}.price_band_u bounds must be positive")
                            if lower > upper:
                                errors.append(f"{item_path}.price_band_u requires lower_u <= upper_u")
                        except GuardError as exc:
                            errors.append(str(exc))
                if has_price:
                    try:
                        price = _decimal(item.get("price_u"), f"{item_path}.price_u")
                        if price <= 0:
                            errors.append(f"{item_path}.price_u must be positive")
                    except GuardError as exc:
                        errors.append(str(exc))
    for field in ("material_uncertainties", "next_decision_changing_evidence"):
        _validate_string_list(value.get(field), f"{path}.{field}", errors)
        if isinstance(value.get(field), list):
            for index, code in enumerate(value[field]):
                if not isinstance(code, str) or code not in REPORT_CODE_ALLOWLIST[field]:
                    errors.append(f"{path}.{field}[{index}] must be a controlled code")
    risk = value.get("user_visible_major_risk")
    if not isinstance(risk, str) or risk not in REPORT_CODE_ALLOWLIST["user_visible_major_risk"]:
        errors.append(f"{path}.user_visible_major_risk must be a controlled code")


def _validate_report_semantics_against_bundle(
    bundle: Mapping[str, Any],
    errors: list[str],
    required_visible_candidate_entry_ids: set[str] | None = None,
    *,
    allow_persisted_candidate_disclosures: bool = False,
) -> None:
    if _is_expressive_report(bundle.get("report_semantics")):
        from decision_report import validate_report_projection
        errors.extend(validate_report_projection(
            bundle["report_semantics"], bundle,
            required_visible_level_ids=required_visible_candidate_entry_ids,
        ))
        return
    required_visible_candidate_entry_ids = (
        required_visible_candidate_entry_ids or set()
    )
    report = bundle.get("report_semantics")
    if not isinstance(report, Mapping):
        return
    causal = bundle.get("causal_attribution")
    if not isinstance(causal, Mapping):
        causal = {}
    expected_primary = {
        "UNRESOLVED": "CAUSE_UNRESOLVED_KEEP_LAST_VALID_ANCHOR",
        "CRYPTO_NATIVE_SPOT": "SPOT_LED_ACCEPTANCE",
        "DERIVATIVES_LEVERAGE": "LEVERAGE_OVERLAY_ONLY",
        "MACRO_BETA": "MACRO_OVERLAY_ONLY",
        "MIXED": "MIXED_CAUSAL_DRIVERS",
    }.get(causal.get("driver_regime"))
    if report.get("primary_causal_explanation") != expected_primary:
        errors.append(
            "report_semantics.primary_causal_explanation conflicts with selected driver"
        )

    rejected_items = report.get("rejected_competing_explanations")
    if isinstance(rejected_items, list) and all(
        isinstance(item, Mapping) for item in rejected_items
    ):
        reported_rejections = {
            item.get("hypothesis_id"): item.get("reason_class")
            for item in rejected_items
            if isinstance(item.get("hypothesis_id"), str)
        }
        causal_rejections = causal.get("rejected_hypothesis_ids")
        expected_rejections = {
            hypothesis_id: _expected_rejected_reason(bundle, hypothesis_id)
            for hypothesis_id in causal_rejections
        } if isinstance(causal_rejections, list) and all(
            isinstance(item, str) for item in causal_rejections
        ) else {}
        if len(reported_rejections) != len(rejected_items):
            errors.append("report rejected competing explanations must be unique")
        if reported_rejections != expected_rejections:
            errors.append(
                "report rejected competing explanations must bind causal rejected hypotheses and actual evidence"
            )

    evidence_matrix = bundle.get("evidence_matrix")
    evidence_statuses = {
        dimension: item.get("status")
        for dimension, item in (
            evidence_matrix.items() if isinstance(evidence_matrix, Mapping) else ()
        )
        if isinstance(item, Mapping)
    }
    uncertainties = report.get("material_uncertainties")
    if isinstance(uncertainties, list):
        for code in uncertainties:
            dimensions = UNCERTAINTY_EVIDENCE_DIMENSIONS.get(code, ())
            if dimensions and all(
                evidence_statuses.get(dimension) == "VERIFIED"
                for dimension in dimensions
            ):
                errors.append(
                    f"report material uncertainty {code} contradicts VERIFIED evidence"
                )
    next_evidence = report.get("next_decision_changing_evidence")
    if isinstance(next_evidence, list):
        for code in next_evidence:
            dimensions = NEXT_EVIDENCE_DIMENSIONS.get(code, ())
            if dimensions and all(
                evidence_statuses.get(dimension) == "VERIFIED"
                for dimension in dimensions
            ):
                errors.append(f"report next evidence {code} is already VERIFIED")

    portfolio = bundle.get("portfolio_decision")
    visible_portfolio = report.get("portfolio_action_by_module")
    if isinstance(portfolio, Mapping) and isinstance(visible_portfolio, Mapping):
        for module in PORTFOLIO_MODULES:
            decision = portfolio.get(module)
            internal_decision = (
                decision.get("decision") if isinstance(decision, Mapping) else None
            )
            expected_visible = PORTFOLIO_REPORT_BY_DECISION.get(internal_decision)
            if visible_portfolio.get(module) != expected_visible:
                errors.append(
                    f"report portfolio action for {module} conflicts with portfolio_decision"
                )

    risk = bundle.get("major_risk")
    risk_active = isinstance(risk, Mapping) and risk.get("active") is True
    raw_risk_kinds = risk.get("kinds", []) if isinstance(risk, Mapping) else []
    risk_kinds = raw_risk_kinds if isinstance(raw_risk_kinds, list) else []
    expected_visible_risk = (
        "DECISIVE_SOURCE_CONFLICT"
        if risk_active and "DECISIVE_SOURCE_CONFLICT" in risk_kinds
        else "SOURCE_CONFLICT" if risk_active else "NONE"
    )
    if report.get("user_visible_major_risk") != expected_visible_risk:
        errors.append("report user-visible major risk conflicts with major_risk")

    path = bundle.get("path_decision")
    account_requirement = bundle.get("user_input_requirement")
    control = bundle.get("control")
    final_event = (
        isinstance(bundle.get("event"), Mapping)
        and bundle["event"].get("final_event_reached") is True
    )
    portfolio_new_spot = (
        portfolio.get("new_spot") if isinstance(portfolio, Mapping) else None
    )
    portfolio_decisions = {
        item.get("decision")
        for item in (portfolio.values() if isinstance(portfolio, Mapping) else ())
        if isinstance(item, Mapping)
    }
    raw_candidates = bundle.get("trade_candidates")
    candidate_list = raw_candidates if isinstance(raw_candidates, list) else []
    report_relevant_eligible = any(
        isinstance(candidate, Mapping)
        and candidate.get("report_relevant") is True
        and isinstance(candidate.get("entries"), list)
        and any(
            isinstance(entry, Mapping)
            and entry.get("eligibility") == "ELIGIBLE_FOR_REVIEW"
            for entry in candidate["entries"]
        )
        for candidate in candidate_list
    )
    if account_requirement == "FACT_SYNC":
        expected_overall = "FACT_SYNC_NEEDED_BEFORE_EXECUTION_REVIEW"
    elif risk_active:
        expected_overall = (
            "CONSERVATIVE_RISK_FREEZE"
            if "DECISIVE_SOURCE_CONFLICT" in risk_kinds
            else "FREEZE_INCREMENTAL_RISK"
        )
    elif final_event and isinstance(control, Mapping) and control.get(
        "decision_changed"
    ) is True:
        expected_overall = "NEW_CYCLE_DECISION_CHANGED"
    elif path == "MOVE_UP":
        expected_overall = "UPWARD_VALUE_MIGRATION"
    elif path == "MOVE_DOWN":
        expected_overall = "DOWNWARD_VALUE_MIGRATION"
    elif path == "WIDEN":
        expected_overall = "WIDENED_VALUE_PATH"
    elif isinstance(portfolio_new_spot, Mapping) and portfolio_new_spot.get(
        "decision"
    ) == "CONDITIONAL_ADD":
        expected_overall = "CONDITIONAL_DISCOUNT_SPOT_REVIEW"
    elif portfolio_decisions.intersection(
        {"FREEZE_INCREMENTAL_RISK", "SAFETY_OVERRIDE"}
    ):
        expected_overall = "FREEZE_INCREMENTAL_RISK"
    elif portfolio_decisions.intersection({"REDUCE_REVIEW", "EXIT_REVIEW"}):
        expected_overall = "REVIEW_ELIGIBLE_CANDIDATE"
    elif report_relevant_eligible:
        expected_overall = "REVIEW_ELIGIBLE_CANDIDATE"
    else:
        expected_overall = "NO_INCREMENTAL_POSITION_CHANGE"
    if report.get("overall_user_decision") != expected_overall:
        errors.append(
            "report_semantics.overall_user_decision conflicts with strategy/portfolio facts"
        )

    zones = {
        zone.get("zone_id"): zone for zone in bundle.get("value_zones", [])
        if isinstance(zone, Mapping) and isinstance(zone.get("zone_id"), str)
    } if isinstance(bundle.get("value_zones"), list) else {}
    candidate_entries: dict[str, Mapping[str, Any]] = {}
    eligible_entries: dict[str, Mapping[str, Any]] = {}
    candidate_commitments = _candidate_commitments_by_entry(candidate_list)
    if candidate_list:
        for candidate in candidate_list:
            if not isinstance(candidate, Mapping):
                continue
            entries = candidate.get("entries")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping) or not isinstance(
                    entry.get("entry_id"), str
                ):
                    continue
                candidate_entries[entry["entry_id"]] = entry
                if (
                    candidate.get("report_relevant") is True
                    and entry.get("eligibility") == "ELIGIBLE_FOR_REVIEW"
                ):
                    eligible_entries[entry["entry_id"]] = entry
    levels = report.get("decision_relevant_levels_and_sizes")
    reported_level_ids: set[str] = set()
    if isinstance(levels, list):
        for index, item in enumerate(levels):
            if not isinstance(item, Mapping) or not isinstance(
                item.get("level_id"), str
            ):
                continue
            level_id = item["level_id"]
            if level_id in reported_level_ids:
                errors.append("report decision levels must be unique")
            reported_level_ids.add(level_id)
            if level_id in candidate_entries:
                entry = candidate_entries[level_id]
                persisted_disclosure = (
                    allow_persisted_candidate_disclosures
                    and "commitment_change_fields" in item
                    and "commitment_snapshot" in item
                )
                if (
                    level_id not in eligible_entries
                    and level_id not in required_visible_candidate_entry_ids
                    and not persisted_disclosure
                ):
                    errors.append(
                        f"report level {level_id} must be an eligible report-relevant "
                        "candidate or a required write-before commitment disclosure"
                    )
                for field in ("price_u", "price_band_u", "amount_u"):
                    if (field in item) != (field in entry):
                        errors.append(
                            f"report level {level_id}.{field} does not match candidate entry shape"
                        )
                    elif field == "price_band_u" and field in item:
                        report_band = item[field]
                        entry_band = entry[field]
                        if not isinstance(report_band, Mapping) or not isinstance(entry_band, Mapping):
                            errors.append(f"report level {level_id}.price_band_u is invalid")
                        else:
                            for bound in ("lower_u", "upper_u"):
                                try:
                                    same = _decimal(report_band.get(bound), bound) == _decimal(
                                        entry_band.get(bound), bound
                                    )
                                except GuardError:
                                    same = False
                                if not same:
                                    errors.append(
                                        f"report level {level_id}.{bound} conflicts with candidate entry"
                                    )
                    elif field in item:
                        try:
                            same = _decimal(item[field], field) == _decimal(entry[field], field)
                        except GuardError:
                            same = False
                        if not same:
                            errors.append(
                                f"report level {level_id}.{field} conflicts with candidate entry"
                            )
                if "commitment_snapshot" in item:
                    expected_snapshot = _candidate_commitment_snapshot(
                        candidate_commitments.get(level_id, {})
                    )
                    if item.get("commitment_snapshot") != expected_snapshot:
                        errors.append(
                            f"report candidate {level_id}.commitment_snapshot must "
                            "equal the current controlled commitment"
                        )
            elif level_id in zones:
                try:
                    if _decimal(item.get("amount_u"), "report zone amount_u") != 0:
                        errors.append(f"report structural zone {level_id} must use amount_u=0")
                except GuardError:
                    pass
                zone_band = zones[level_id].get("band")
                if "price_u" in item:
                    errors.append(f"report structural zone {level_id} must use its band, not price_u")
                if "price_band_u" not in item:
                    errors.append(
                        f"report structural zone {level_id} must include its price_band_u"
                    )
                elif item.get("price_band_u") != zone_band:
                    try:
                        same_band = all(
                            _decimal(item["price_band_u"].get(bound), bound)
                            == _decimal(zone_band.get(bound), bound)
                            for bound in ("lower_u", "upper_u")
                        )
                    except (GuardError, AttributeError):
                        same_band = False
                    if not same_band:
                        errors.append(f"report structural zone {level_id} band conflicts with value_zones")
            else:
                errors.append(f"report level {level_id} is not a current zone or candidate")
    required_candidate_levels = set(eligible_entries).union(
        required_visible_candidate_entry_ids
    )
    missing_required = sorted(required_candidate_levels.difference(reported_level_ids))
    if missing_required:
        errors.append(
            "report omits eligible or changed candidate entries: "
            + ", ".join(missing_required)
        )


def _candidate_commitments_by_entry(candidates: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(candidates, list):
        return result
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        entries = candidate.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            entry_id = entry.get("entry_id") if isinstance(entry, Mapping) else None
            if not isinstance(entry_id, str) or not entry_id:
                continue
            result[entry_id] = {
                "candidate_id": candidate.get("candidate_id"),
                "kind": candidate.get("kind"),
                # Candidate eligibility/report relevance are derived aggregates.
                # Excluding them prevents one changed sibling entry from making
                # every other entry look changed.
                "entry": {
                    "entry_id": entry.get("entry_id"),
                    "price_band_u": entry.get("price_band_u"),
                    "price_u": entry.get("price_u"),
                    "amount_u": entry.get("amount_u"),
                    "amount_basis": entry.get("amount_basis"),
                    "value_role": entry.get("value_role"),
                    "eligibility": entry.get("eligibility"),
                    "formation_conditions": entry.get("formation_conditions"),
                    "invalidation_conditions": entry.get(
                        "invalidation_conditions"
                    ),
                    "evidence_confidence": entry.get("evidence_confidence"),
                    "account_precondition": entry.get("account_precondition"),
                    "carried_forward": entry.get("carried_forward", False),
                },
            }
    return result


def _candidate_commitment_change_fields(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> list[str]:
    if previous is None:
        return ["ENTRY_ADDED"]
    changed: list[str] = []
    if _fingerprint(previous.get("candidate_id")) != _fingerprint(
        current.get("candidate_id")
    ):
        changed.append("CANDIDATE_IDENTITY")
    if _fingerprint(previous.get("kind")) != _fingerprint(current.get("kind")):
        changed.append("CANDIDATE_KIND")
    previous_entry = previous.get("entry")
    current_entry = current.get("entry")
    if not isinstance(previous_entry, Mapping) or not isinstance(
        current_entry, Mapping
    ):
        return sorted(set(changed) | {"ENTRY_ADDED"})
    comparisons = {
        "PRICE_COMMITMENT": {
            "price_u": current_entry.get("price_u"),
            "price_band_u": current_entry.get("price_band_u"),
        },
        "AMOUNT_COMMITMENT": current_entry.get("amount_u"),
        "AMOUNT_BASIS": current_entry.get("amount_basis"),
        "VALUE_ROLE": current_entry.get("value_role"),
        "ENTRY_ELIGIBILITY": current_entry.get("eligibility"),
        "FORMATION_CONDITIONS": current_entry.get("formation_conditions"),
        "INVALIDATION_CONDITIONS": current_entry.get("invalidation_conditions"),
        "EVIDENCE_CONFIDENCE": current_entry.get("evidence_confidence"),
        "ACCOUNT_PRECONDITION": current_entry.get("account_precondition"),
        "CARRIED_FORWARD": current_entry.get("carried_forward", False),
    }
    previous_values = {
        "PRICE_COMMITMENT": {
            "price_u": previous_entry.get("price_u"),
            "price_band_u": previous_entry.get("price_band_u"),
        },
        "AMOUNT_COMMITMENT": previous_entry.get("amount_u"),
        "AMOUNT_BASIS": previous_entry.get("amount_basis"),
        "VALUE_ROLE": previous_entry.get("value_role"),
        "ENTRY_ELIGIBILITY": previous_entry.get("eligibility"),
        "FORMATION_CONDITIONS": previous_entry.get("formation_conditions"),
        "INVALIDATION_CONDITIONS": previous_entry.get("invalidation_conditions"),
        "EVIDENCE_CONFIDENCE": previous_entry.get("evidence_confidence"),
        "ACCOUNT_PRECONDITION": previous_entry.get("account_precondition"),
        "CARRIED_FORWARD": previous_entry.get("carried_forward", False),
    }
    for field, current_value in comparisons.items():
        previous_value = previous_values[field]
        if field in {"FORMATION_CONDITIONS", "INVALIDATION_CONDITIONS"}:
            current_value = (
                sorted(current_value) if isinstance(current_value, list)
                else current_value
            )
            previous_value = (
                sorted(previous_value) if isinstance(previous_value, list)
                else previous_value
            )
        if _fingerprint(current_value) != _fingerprint(previous_value):
            changed.append(field)
    return sorted(changed)


def _candidate_commitment_snapshot(commitment: Mapping[str, Any]) -> dict[str, Any]:
    entry = commitment.get("entry")
    entry = entry if isinstance(entry, Mapping) else {}
    amount_basis = entry.get("amount_basis")
    amount_basis = amount_basis if isinstance(amount_basis, Mapping) else {}
    return {
        "candidate_kind": commitment.get("kind"),
        "value_role": entry.get("value_role"),
        "entry_eligibility": entry.get("eligibility"),
        "amount_basis_classes": {
            "budget_source": amount_basis.get("budget_source"),
            "net_exposure_constraint": amount_basis.get(
                "net_exposure_constraint"
            ),
            "value_gap_rationale": amount_basis.get("value_gap_rationale"),
            "risk_assumption": amount_basis.get("risk_assumption"),
            "continuation_reason": amount_basis.get(
                "continuation_reason", "CONTINUATION_NOT_APPLICABLE"
            ),
        },
        "formation_condition_classes": sorted(
            entry.get("formation_conditions", [])
        ) if isinstance(entry.get("formation_conditions"), list) else [],
        "invalidation_condition_classes": sorted(
            entry.get("invalidation_conditions", [])
        ) if isinstance(entry.get("invalidation_conditions"), list) else [],
        "evidence_confidence": entry.get("evidence_confidence"),
        "account_precondition": entry.get("account_precondition"),
        "carried_forward": entry.get("carried_forward", False),
    }


def _report_candidate_items(
    report: Any, candidate_entry_ids: set[str]
) -> dict[str, Mapping[str, Any]]:
    levels = report.get("decision_relevant_levels_and_sizes") if isinstance(
        report, Mapping
    ) else None
    if isinstance(levels, Mapping) and "semantic" in levels:
        levels = levels["semantic"]
    if not isinstance(levels, list):
        return {}
    return {
        item["level_id"]: item
        for item in levels
        if isinstance(item, Mapping)
        and isinstance(item.get("level_id"), str)
        and item["level_id"] in candidate_entry_ids
    }


def _validate_candidate_delta_visibility(
    bundle: Mapping[str, Any], previous_semantics: Mapping[str, Any], errors: list[str]
) -> set[str]:
    """Bind changed/currently eligible/sticky-visible entries to report semantics."""

    previous = _candidate_commitments_by_entry(
        previous_semantics.get("trade_candidates")
    )
    current = _candidate_commitments_by_entry(bundle.get("trade_candidates"))
    removed = sorted(set(previous).difference(current))
    if removed:
        errors.append(
            "previous candidate entries cannot disappear; retain them as INVALIDATED: "
            + ", ".join(removed)
        )
    change_fields = {
        entry_id: fields
        for entry_id, commitment in current.items()
        if (
            fields := _candidate_commitment_change_fields(
                previous.get(entry_id), commitment
            )
        )
    }
    previous_report = previous_semantics.get("report_semantics")
    candidate_ids = set(previous).union(current)
    previous_visible = _report_candidate_items(previous_report, candidate_ids)
    current_report = report_semantics(bundle)
    current_visible = _report_candidate_items(current_report, candidate_ids)
    currently_eligible = {
        entry_id
        for entry_id, commitment in current.items()
        if isinstance(commitment.get("entry"), Mapping)
        and commitment["entry"].get("eligibility") == "ELIGIBLE_FOR_REVIEW"
    }
    required_visible = set(previous_visible).union(change_fields).union(
        currently_eligible
    )
    missing = sorted(required_visible.difference(current_visible))
    unexpected = sorted(set(current_visible).difference(required_visible))
    if missing:
        errors.append(
            "report omits eligible, changed, or previously visible candidate entries: "
            + ", ".join(missing)
        )
    if unexpected:
        errors.append(
            "report cannot add unchanged never-visible inactive candidate entries: "
            + ", ".join(unexpected)
        )
    if _is_expressive_report(bundle.get("report_semantics")):
        # The natural report carries the full typed commitment rather than a
        # canned description. Its validator binds each visible level to bundle.
        from decision_report import semantic_projection
        prior = semantic_projection(previous_report) if _is_expressive_report(previous_report) else previous_report
        if change_fields and _fingerprint(current_report) == _fingerprint(prior):
            errors.append("candidate commitment changes must change report_semantics")
        for entry_id in set(previous_visible).intersection(current_visible).difference(change_fields):
            if _fingerprint(current_visible[entry_id]) != _fingerprint(previous_visible[entry_id]):
                errors.append(f"unchanged candidate {entry_id} must retain its report semantics")
        return required_visible
    for entry_id in sorted(set(current_visible).intersection(current)):
        item = current_visible[entry_id]
        expected_snapshot = _candidate_commitment_snapshot(current[entry_id])
        if entry_id in change_fields:
            prior_change_fields = (
                previous_visible[entry_id].get("commitment_change_fields", [])
                if entry_id in previous_visible else []
            )
            if not isinstance(prior_change_fields, list):
                prior_change_fields = []
            expected_change_fields = sorted(
                set(prior_change_fields).union(change_fields[entry_id])
            )
            if item.get("commitment_change_fields") != expected_change_fields:
                errors.append(
                    f"report candidate {entry_id}.commitment_change_fields must "
                    "retain its visible lineage and include its write-before delta"
                )
            if item.get("commitment_snapshot") != expected_snapshot:
                errors.append(
                    f"report candidate {entry_id}.commitment_snapshot must equal "
                    "the current controlled commitment"
                )
        elif entry_id in previous_visible:
            if _fingerprint(item) != _fingerprint(previous_visible[entry_id]):
                errors.append(
                    f"unchanged previously visible candidate {entry_id} must retain "
                    "its report semantics"
                )
    if change_fields:
        if not isinstance(previous_report, Mapping):
            errors.append(
                "previous projection lacks report semantics for candidate delta visibility"
            )
        elif _fingerprint(current_report) == _fingerprint(previous_report):
            errors.append(
                "candidate commitment changes must change report_semantics: "
                + ", ".join(sorted(change_fields))
            )
    return required_visible


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise GuardError(f"{label} must be numeric, not boolean")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise GuardError(f"{label} is not a valid number: {value!r}") from exc
    if not number.is_finite():
        raise GuardError(f"{label} must be finite")
    return number


def _nonnegative_integer(value: Any, label: str) -> int:
    number = _decimal(value, label)
    try:
        integral = number.to_integral_value()
    except InvalidOperation as exc:
        raise GuardError(f"{label} must be a non-negative integer") from exc
    if number != integral or number < 0:
        raise GuardError(f"{label} must be a non-negative integer")
    try:
        return int(number)
    except (ValueError, OverflowError) as exc:
        raise GuardError(f"{label} must be a non-negative integer") from exc


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _source_identity(source: Any) -> str | None:
    if isinstance(source, str) and source.strip():
        return source.strip().casefold()
    if isinstance(source, Mapping):
        source_id = source.get("source_id")
        if isinstance(source_id, str) and source_id.strip():
            return source_id.strip().casefold()
    return None


def _source_is_public(source: Any, default_access: Any) -> bool:
    access = source.get("access_scope") if isinstance(source, Mapping) else default_access
    return access == "PUBLIC_UNAUTHENTICATED"


def _canonical_source_origin(source: Any) -> str | None:
    """Return a source's real independence boundary, never a self-declared group."""

    if not isinstance(source, Mapping):
        return None
    for field in ("canonical_origin", "origin"):
        origin = _source_identity(source.get(field))
        if origin is not None:
            return origin
    return None


def _source_venue(source: Any) -> str | None:
    if not isinstance(source, Mapping):
        return None
    return _source_identity(source.get("venue"))


def _registered_quote_provenance(source: Any) -> tuple[str, str, str] | None:
    """Derive provenance from registered ticker or explicit candle-close data.

    Existing ticker URLs stay byte-exact: decoding percent escapes or
    ignoring empty separators would make multiple raw URLs equivalent to the
    single reviewed public endpoint.  The registry owns the canonical scheme,
    host, path, raw query spelling and absence of every optional URL component.
    """

    if not isinstance(source, Mapping):
        return None
    endpoint = source.get("endpoint_url")
    if source.get("payload_kind") == "BTC_SPOT_CANDLE_CLOSE":
        from event_contract import public_candle_provenance
        provenance = public_candle_provenance(endpoint)
        if provenance is None or (source.get("canonical_origin"), source.get("venue")) != provenance[:2]:
            return None
        return provenance
    if not isinstance(endpoint, str):
        return None
    parsed = _parse_endpoint(endpoint)
    if parsed is None:
        return None
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    registry_host = parsed.hostname.casefold()
    registered = QUOTE_ENDPOINT_REGISTRY.get(registry_host)
    if registered is None:
        return None
    origin, venue, allowed_path, allowed_raw_query = registered
    canonical_endpoint = f"https://{registry_host}{allowed_path}"
    if allowed_raw_query:
        canonical_endpoint += "?" + allowed_raw_query
    if (
        endpoint != canonical_endpoint
        or parsed.netloc != registry_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.params
        or parsed.fragment
        or parsed.path != allowed_path
        or parsed.query != allowed_raw_query
        or source.get("payload_kind") != "BTC_SPOT_TICKER"
    ):
        return None
    if source.get("canonical_origin") != origin or source.get("venue") != venue:
        return None
    return (origin, venue, endpoint)


def _is_registered_quote_endpoint(endpoint: Any) -> bool:
    """Recognize a quote-only endpoint independently of caller payload labels."""

    parsed = _parse_endpoint(endpoint)
    if parsed is None or parsed.scheme != "https" or not parsed.hostname:
        return False
    registered = QUOTE_ENDPOINT_REGISTRY.get(parsed.hostname.casefold())
    if registered is None:
        return False
    _, _, allowed_path, _ = registered
    # Query decoration cannot turn a ticker resource into execution/flow data.
    decoded_path = parsed.path
    for _ in range(3):
        next_path = unquote(decoded_path)
        if next_path == decoded_path:
            break
        decoded_path = next_path
    return decoded_path == allowed_path


def _structured_public_source(source: Any, dimension: str | None = None) -> bool:
    if not isinstance(source, Mapping):
        return False
    endpoint = source.get("endpoint_url")
    parsed = _parse_endpoint(endpoint)
    if not (
        _source_identity(source) is not None
        and parsed is not None
        and parsed.scheme == "https"
        and parsed.hostname
        and not _has_bracketed_authority(parsed)
        and _is_public_network_hostname(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 443}
        and not parsed.fragment
        and not parsed.hostname.casefold().endswith(".invalid")
        and source.get("access_scope") == "PUBLIC_UNAUTHENTICATED"
    ):
        return False
    if dimension == "price_quotes":
        return _registered_quote_provenance(source) is not None
    if dimension is not None and source.get("payload_kind") != EVIDENCE_PAYLOAD_KINDS[dimension]:
        return False
    return _source_identity(source.get("canonical_origin")) == parsed.hostname.casefold()


def _claim_type(claim: Any) -> str | None:
    if not isinstance(claim, Mapping):
        return None
    for field in ("claim_type", "claim", "kind"):
        value = claim.get(field)
        if isinstance(value, str) and value.strip():
            return re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    return None


def _verified_claim_types(evidence: Any) -> set[str]:
    if not isinstance(evidence, Mapping):
        return set()
    claims = evidence.get("evidence_claims")
    if not isinstance(claims, list):
        return set()
    return {
        claim_type
        for claim in claims
        if isinstance(claim, Mapping)
        and claim.get("status") == "VERIFIED"
        and (claim_type := _claim_type(claim)) is not None
    }


def _has_verified_claim(evidence: Any, *accepted_types: str) -> bool:
    return bool(_verified_claim_types(evidence).intersection(accepted_types))


def _has_verified_directional_native_spot_flow(evidence: Any, direction: str) -> bool:
    if not isinstance(evidence, Mapping) or not isinstance(
        evidence.get("evidence_claims"), list
    ):
        return False
    accepted_types = {
        f"NATIVE_SPOT_{direction}_FLOW",
        f"NATIVE_SPOT_{direction}",
        "NATIVE_SPOT_FLOW",
    }
    for claim in evidence["evidence_claims"]:
        if not isinstance(claim, Mapping) or claim.get("status") != "VERIFIED":
            continue
        if _claim_type(claim) not in accepted_types:
            continue
        claim_direction = claim.get("direction")
        if _claim_type(claim) in {
            f"NATIVE_SPOT_{direction}_FLOW",
            f"NATIVE_SPOT_{direction}",
        } or (
            isinstance(claim_direction, str) and claim_direction.upper() == direction
        ):
            return True
    return False


def _bound_verified_claim_ids(
    evidence: Any,
    accepted_types: set[str],
    direction: str | None = None,
    dimension: str | None = None,
    subject_zone_ids: set[str] | None = None,
    *,
    require_multi_period: bool = False,
) -> set[str]:
    """Claims must be public, measurable children of their VERIFIED parent."""

    if not isinstance(evidence, Mapping) or evidence.get("status") != "VERIFIED":
        return set()
    if require_multi_period and (
        not isinstance(evidence.get("time_range"), Mapping)
        or evidence["time_range"].get("kind") != "MULTI_PERIOD"
    ):
        return set()
    claims = evidence.get("evidence_claims")
    parent_sources = {
        _source_identity(source) for source in evidence.get("sources", [])
        if _source_identity(source) is not None
    }
    if not isinstance(claims, list) or not parent_sources:
        return set()
    result: set[str] = set()
    for claim in claims:
        if not isinstance(claim, Mapping) or claim.get("status") != "VERIFIED":
            continue
        if _claim_type(claim) not in accepted_types:
            continue
        if direction is not None and claim.get("direction") != direction:
            continue
        if dimension is not None and CLAIM_DIMENSIONS.get(_claim_type(claim)) != dimension:
            continue
        if (
            subject_zone_ids is not None
            and claim.get("subject_zone_id") not in subject_zone_ids
        ):
            continue
        claim_sources = claim.get("source_ids")
        source_ids = {
            _source_identity(source) for source in claim_sources
            if _source_identity(source) is not None
        } if isinstance(claim_sources, list) else set()
        if (
            source_ids != parent_sources
            or claim.get("time_range") != evidence.get("time_range")
            or not isinstance(claim.get("metric"), Mapping)
        ):
            continue
        if require_multi_period and (
            claim["metric"].get("periods") is None
            or isinstance(claim["metric"].get("periods"), bool)
            or not isinstance(claim["metric"].get("periods"), int)
            or claim["metric"]["periods"] < 2
        ):
            continue
        claim_id = _source_identity(claim.get("claim_id"))
        if claim_id is not None:
            result.add(claim_id)
    return result


def _has_bound_verified_claim(
    evidence: Any,
    accepted_types: set[str],
    direction: str | None = None,
    dimension: str | None = None,
    subject_zone_ids: set[str] | None = None,
    *,
    require_multi_period: bool = False,
) -> bool:
    return bool(
        _bound_verified_claim_ids(
            evidence,
            accepted_types,
            direction=direction,
            dimension=dimension,
            subject_zone_ids=subject_zone_ids,
            require_multi_period=require_multi_period,
        )
    )


def _has_bound_verified_lifecycle_claim(
    evidence: Any, claim_type: str, zone_id: str
) -> bool:
    if not isinstance(evidence, Mapping) or evidence.get("status") != "VERIFIED":
        return False
    parent_sources = {
        _source_identity(source)
        for source in evidence.get("sources", [])
        if _source_identity(source) is not None
    }
    if not parent_sources or not isinstance(evidence.get("evidence_claims"), list):
        return False
    for claim in evidence["evidence_claims"]:
        if not isinstance(claim, Mapping):
            continue
        claim_sources = {
            _source_identity(source)
            for source in claim.get("source_ids", [])
            if _source_identity(source) is not None
        } if isinstance(claim.get("source_ids"), list) else set()
        if (
            claim.get("status") == "VERIFIED"
            and _claim_type(claim) == claim_type
            and claim.get("subject_zone_id") == zone_id
            and claim_sources == parent_sources
            and claim.get("time_range") == evidence.get("time_range")
            and isinstance(claim.get("metric"), Mapping)
        ):
            return True
    return False


def _validate_candidate_kind_contract(
    candidates: Any, zones: Any, errors: list[str]
) -> None:
    """Bind candidate direction to stable IDs, roles, conditions and price side."""

    structural_lowers: list[Decimal] = []
    structural_uppers: list[Decimal] = []
    if isinstance(zones, list):
        for zone in zones:
            band = zone.get("band") if isinstance(zone, Mapping) else None
            if not isinstance(band, Mapping):
                continue
            try:
                structural_lowers.append(_decimal(band.get("lower_u"), "zone lower"))
                structural_uppers.append(_decimal(band.get("upper_u"), "zone upper"))
            except GuardError:
                continue
    if not isinstance(candidates, list) or not structural_lowers or not structural_uppers:
        return
    lowest_structure = min(structural_lowers)
    highest_structure = max(structural_uppers)
    for candidate_index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            continue
        path = f"trade_candidates[{candidate_index}]"
        kind = candidate.get("kind")
        kind_contract = CANDIDATE_KIND_CONTRACT.get(kind)
        if kind_contract is None:
            continue
        if candidate.get("candidate_id") != kind:
            errors.append(f"{path}.candidate_id must equal its controlled kind {kind}")
        entries = candidate.get("entries")
        if not isinstance(entries, list):
            continue
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                continue
            entry_path = f"{path}.entries[{entry_index}]"
            if not isinstance(entry.get("entry_id"), str) or not re.fullmatch(
                kind_contract["entry_pattern"], entry["entry_id"]
            ):
                errors.append(f"{entry_path}.entry_id is not valid for {kind}")
            if entry.get("value_role") != kind_contract["value_role"]:
                errors.append(f"{entry_path}.value_role conflicts with {kind}")
            formation = entry.get("formation_conditions")
            if not isinstance(formation, list) or kind_contract[
                "formation_condition"
            ] not in formation:
                errors.append(f"{entry_path}.formation_conditions conflict with {kind}")
            amount_basis = entry.get("amount_basis")
            if not isinstance(amount_basis, Mapping) or amount_basis.get(
                "budget_source"
            ) != kind_contract["budget_source"]:
                errors.append(f"{entry_path}.amount_basis.budget_source conflicts with {kind}")
            try:
                if "price_u" in entry:
                    entry_lower = entry_upper = _decimal(
                        entry.get("price_u"), f"{entry_path}.price_u"
                    )
                else:
                    band = entry.get("price_band_u")
                    entry_lower = _decimal(
                        band.get("lower_u") if isinstance(band, Mapping) else None,
                        f"{entry_path}.price_band_u.lower_u",
                    )
                    entry_upper = _decimal(
                        band.get("upper_u") if isinstance(band, Mapping) else None,
                        f"{entry_path}.price_band_u.upper_u",
                    )
            except GuardError:
                continue
            if kind_contract["side"] == "ABOVE" and entry_lower <= highest_structure:
                errors.append(f"{entry_path} PREMIUM_SHORT must be above all current value zones")
            if kind_contract["side"] == "BELOW" and entry_upper >= lowest_structure:
                errors.append(f"{entry_path} DISCOUNT_SPOT must be below all current value zones")


def _selected_hypothesis_timeline(causal: Any) -> list[Any] | None:
    if not isinstance(causal, Mapping):
        return None
    selected = causal.get("selected_hypothesis_id")
    hypotheses = causal.get("hypotheses")
    if not isinstance(hypotheses, list):
        return None
    selected_hypothesis = next(
        (
            item for item in hypotheses
            if isinstance(item, Mapping)
            and item.get("hypothesis_id") == selected
        ),
        None,
    )
    timeline = (
        selected_hypothesis.get("causal_timeline")
        if isinstance(selected_hypothesis, Mapping)
        else None
    )
    return timeline if isinstance(timeline, list) else None


def _timeline_matching_events(
    timeline: Any,
    *,
    role: str,
    dimension: str | None = None,
    evidence_ids: set[str] | None = None,
) -> list[tuple[int, datetime]]:
    matches: list[tuple[int, datetime]] = []
    if not isinstance(timeline, list):
        return matches
    for index, event in enumerate(timeline):
        if not isinstance(event, Mapping) or event.get("event_role") != role:
            continue
        if dimension is not None and event.get("evidence_dimension") != dimension:
            continue
        if evidence_ids is not None and _source_identity(
            event.get("evidence_ref")
        ) not in evidence_ids:
            continue
        occurred = _parse_iso8601(event.get("occurred_at"), "causal event", [])
        if occurred is not None:
            matches.append((index, occurred))
    return matches


def _validate_selected_driver_causal_binding(
    causal: Any,
    matrix: Any,
    driver: Any,
    components: Any,
    errors: list[str],
) -> None:
    """Bind every asserted non-unresolved driver to typed causal evidence."""

    if driver == "UNRESOLVED" or not isinstance(matrix, Mapping):
        return
    selected_timeline = _selected_hypothesis_timeline(causal)
    asserted_components = (
        components
        if driver == "MIXED" and isinstance(components, list)
        else [driver]
    )
    required_event_groups: list[list[tuple[int, datetime]]] = []
    for component in asserted_components:
        for dimension, claim_types, role in DRIVER_CAUSAL_EVIDENCE.get(
            component, ()
        ):
            claim_ids = _bound_verified_claim_ids(
                matrix.get(dimension), claim_types, dimension=dimension
            )
            matches = _timeline_matching_events(
                selected_timeline,
                role=role,
                dimension=dimension,
                evidence_ids=claim_ids,
            )
            if not matches:
                errors.append(
                    f"driver_regime={driver} selected hypothesis must reference "
                    f"bound VERIFIED {component} {dimension} {role} evidence"
                )
            elif role == "CAUSE_OBSERVED":
                required_event_groups.append(matches)
    response_events = _timeline_matching_events(
        selected_timeline, role="PRICE_RESPONSE_OBSERVED"
    ) + _timeline_matching_events(
        selected_timeline, role="RETENTION_OBSERVED"
    )
    if not response_events:
        errors.append(
            f"driver_regime={driver} selected hypothesis requires a verified "
            "price-response or retention event"
        )
    elif required_event_groups and not any(
        all(
            any(
                cause_index < response_index and cause_time < response_time
                for cause_index, cause_time in group
            )
            for group in required_event_groups
        )
        for response_index, response_time in response_events
    ):
        errors.append(
            f"driver_regime={driver} all bound cause evidence must strictly "
            "precede its price-response or retention event"
        )


def _premium_candidate_anchor_ids(
    bundle: Mapping[str, Any], candidate: Mapping[str, Any]
) -> set[str]:
    anchor_map = bundle.get("anchor_map_semantics")
    unretested = anchor_map.get("unretested_anchor_ids") if isinstance(
        anchor_map, Mapping
    ) else None
    if not isinstance(unretested, list):
        return set()
    entry_lowers: list[Decimal] = []
    for entry in candidate.get("entries", []):
        if not isinstance(entry, Mapping):
            continue
        try:
            if "price_u" in entry:
                entry_lowers.append(_decimal(entry.get("price_u"), "candidate price"))
            elif isinstance(entry.get("price_band_u"), Mapping):
                entry_lowers.append(
                    _decimal(entry["price_band_u"].get("lower_u"), "candidate lower")
                )
        except GuardError:
            continue
    if not entry_lowers:
        return set()
    candidate_lower = min(entry_lowers)
    matching: list[tuple[str, Decimal]] = []
    for zone in bundle.get("value_zones", []):
        if (
            not isinstance(zone, Mapping)
            or zone.get("zone_id") not in unretested
            or zone.get("role") != "UNRETESTED_VALUE_ZONE"
        ):
            continue
        band = zone.get("band")
        try:
            upper = _decimal(
                band.get("upper_u") if isinstance(band, Mapping) else None,
                "unretested anchor upper",
            )
        except GuardError:
            continue
        if upper < candidate_lower:
            matching.append((zone["zone_id"], upper))
    if not matching:
        return set()
    closest_upper = max(upper for _, upper in matching)
    return {zone_id for zone_id, upper in matching if upper == closest_upper}


def _validate_candidate_eligibility_evidence(
    bundle: Mapping[str, Any],
    candidates: Any,
    matrix: Any,
    causal: Any,
    errors: list[str],
) -> None:
    """An eligible ladder must prove its controlled formation thesis."""

    if not isinstance(candidates, list) or not isinstance(matrix, Mapping):
        return
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            continue
        entries = candidate.get("entries")
        entry_declares_eligible = isinstance(entries, list) and any(
            isinstance(entry, Mapping)
            and entry.get("eligibility") == "ELIGIBLE_FOR_REVIEW"
            for entry in entries
        )
        if not (
            candidate.get("eligibility") == "ELIGIBLE_FOR_REVIEW"
            or entry_declares_eligible
        ):
            continue
        path = f"trade_candidates[{index}]"
        kind = candidate.get("kind")
        contract = CANDIDATE_ELIGIBILITY_EVIDENCE.get(kind)
        if contract is None:
            errors.append(f"{path} has no controlled eligibility evidence contract")
            continue
        if bundle.get("decision_evidence_status") != "RESOLVED":
            errors.append(f"{path} eligibility requires RESOLVED decision evidence")
        production = _is_expressive_report(bundle.get("report_semantics"))
        driver_matches = isinstance(causal, Mapping) and (
            causal.get("driver_regime") == contract["driver_regime"]
            or (production and causal.get("driver_regime") == "MIXED"
                and contract["driver_regime"] in causal.get("driver_components", []))
        )
        if not driver_matches:
            errors.append(
                f"{path} eligibility requires driver_regime="
                f"{contract['driver_regime']}"
            )
        if not production and (not isinstance(causal, Mapping) or causal.get(
            "selected_hypothesis_id"
        ) != contract["selected_hypothesis_id"]):
            errors.append(
                f"{path} eligibility requires selected_hypothesis_id="
                f"{contract['selected_hypothesis_id']}"
            )
        selected_timeline = _selected_hypothesis_timeline(causal)
        formation_events: dict[str, list[tuple[int, datetime]]] = {}
        for dimension, claim_types, direction, needs_unretested_zone, role in contract[
            "required_claims"
        ]:
            subject_zone_ids = (
                _premium_candidate_anchor_ids(bundle, candidate)
                if needs_unretested_zone else None
            )
            if needs_unretested_zone and not subject_zone_ids:
                errors.append(
                    f"{path} eligibility requires its closest unretested value anchor"
                )
                continue
            claim_ids = _bound_verified_claim_ids(
                matrix.get(dimension),
                claim_types,
                direction=direction,
                dimension=dimension,
                subject_zone_ids=subject_zone_ids,
                require_multi_period=True,
            )
            if not claim_ids:
                errors.append(
                    f"{path} eligibility requires bound VERIFIED multi-period "
                    f"{dimension} formation claim {sorted(claim_types)}"
                )
                continue
            matches = _timeline_matching_events(
                selected_timeline,
                role=role,
                dimension=dimension,
                evidence_ids=claim_ids,
            )
            if not matches:
                errors.append(
                    f"{path} eligibility requires every formation claim in its "
                    f"selected hypothesis timeline as {role}"
                )
            formation_events[dimension] = matches
        price_events = _timeline_matching_events(
            selected_timeline, role="PRICE_RESPONSE_OBSERVED"
        )
        if kind == "PREMIUM_SHORT":
            cause_groups = [
                formation_events.get("derivatives_flow", []),
                formation_events.get("oi_timeline", []),
                formation_events.get("funding_basis", []),
            ]
            retention = formation_events.get("cross_exchange_acceptance", [])
            if not (
                price_events
                and retention
                and all(cause_groups)
                and any(
                    all(
                        any(
                            cause_index < price_index and cause_time < price_time
                            for cause_index, cause_time in group
                        )
                        for group in cause_groups
                    )
                    and any(
                        price_index < retention_index
                        and price_time < retention_time
                        for retention_index, retention_time in retention
                    )
                    for price_index, price_time in price_events
                )
            ):
                errors.append(
                    f"{path} PREMIUM_SHORT formation requires leverage, OI and "
                    "cost-expansion causes, then price response, then spot-value "
                    "non-acceptance"
                )
        elif kind == "DISCOUNT_SPOT":
            prerequisite_groups = [
                formation_events.get("derivatives_flow", []),
                formation_events.get("oi_timeline", []),
                formation_events.get("capital_source", []),
            ]
            spot_events = formation_events.get("spot_flow", [])
            retention = formation_events.get("cross_exchange_acceptance", [])
            if not (
                price_events
                and retention
                and spot_events
                and all(prerequisite_groups)
                and any(
                    all(
                        any(
                            prerequisite_index < spot_index
                            and prerequisite_time < spot_time
                            for prerequisite_index, prerequisite_time in group
                        )
                        for group in prerequisite_groups
                    )
                    and any(
                        spot_index < price_index
                        and spot_time < price_time
                        and any(
                            price_index < retention_index
                            and price_time < retention_time
                            for retention_index, retention_time in retention
                        )
                        for price_index, price_time in price_events
                    )
                    for spot_index, spot_time in spot_events
                )
            ):
                errors.append(
                    f"{path} DISCOUNT_SPOT formation requires deleveraging, OI "
                    "and capital causes, then spot absorption, price response, "
                    "then cross-venue acceptance"
                )


def _canonical_number(value: int | float | Decimal) -> list[str]:
    if isinstance(value, bool):
        raise GuardError("boolean cannot be canonicalized as a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise GuardError("fingerprints do not accept NaN or infinity")
    number = _decimal(value, "fingerprint number")
    if number == 0:
        rendered = "0"
    else:
        # Decimal.normalize() is context-sensitive and silently rounds when a
        # value has more significant digits than the ambient precision.  Work
        # from the exact tuple instead so distinct finite decimals cannot
        # collapse merely because the process uses Decimal's default context.
        raw = number.as_tuple()
        digits = list(raw.digits)
        exponent = raw.exponent
        while len(digits) > 1 and digits[-1] == 0:
            digits.pop()
            exponent += 1
        coefficient = "".join(str(digit) for digit in digits)
        sign = "-" if raw.sign else ""
        decimal_point = len(coefficient) + exponent
        # Avoid allocating gigabytes for a legal Decimal such as 1e+999999.
        # Scientific notation remains an exact, deterministic decimal form.
        plain_length = (
            decimal_point
            if exponent >= 0
            else max(len(coefficient), 2 - decimal_point + len(coefficient))
        )
        if abs(exponent) <= 1024 and plain_length <= 2048:
            if exponent >= 0:
                rendered = sign + coefficient + ("0" * exponent)
            elif decimal_point > 0:
                rendered = (
                    sign + coefficient[:decimal_point] + "."
                    + coefficient[decimal_point:]
                )
            else:
                rendered = sign + "0." + ("0" * -decimal_point) + coefficient
        else:
            adjusted_exponent = exponent + len(coefficient) - 1
            mantissa = (
                coefficient
                if len(coefficient) == 1
                else coefficient[0] + "." + coefficient[1:]
            )
            exponent_sign = "+" if adjusted_exponent >= 0 else ""
            rendered = f"{sign}{mantissa}e{exponent_sign}{adjusted_exponent}"
    # The tag keeps numeric 1 distinct from the string "1" while making
    # 1, 1.0, and Decimal("1.00") identical.
    return ["$number", rendered]


def _canonicalize(value: Any, field_name: str | None = None) -> Any:
    if isinstance(value, str) and field_name and DECIMAL_FIELD_RE.search(field_name):
        try:
            return _canonical_number(_decimal(value, field_name))
        except GuardError:
            return value
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float, Decimal)):
        return _canonical_number(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(value[key], str(key))
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        normalized = [_canonicalize(item) for item in value]
        if field_name in SET_LIKE_FIELDS:
            normalized.sort(
                key=lambda item: json.dumps(
                    item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
        return normalized
    raise GuardError(f"unsupported fingerprint value type: {type(value).__name__}")


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def classify_zone(
    prices: Mapping[str, Any] | Sequence[Any], lower: Any, upper: Any
) -> str:
    """Classify a multi-venue snapshot relative to one closed price band."""

    low = _decimal(lower, "lower")
    high = _decimal(upper, "upper")
    if low <= 0 or high <= 0 or low > high:
        raise GuardError("zone bounds must be positive and lower must not exceed upper")
    if isinstance(prices, Mapping):
        values = list(prices.values())
    elif isinstance(prices, Sequence) and not isinstance(prices, (str, bytes)):
        values = list(prices)
    else:
        raise GuardError("prices must be a non-empty object or array")
    if not values:
        raise GuardError("prices must not be empty")

    venue_relations: list[str] = []
    for index, raw_price in enumerate(values):
        price = _decimal(raw_price, f"prices[{index}]")
        if price <= 0:
            raise GuardError(f"prices[{index}] must be positive")
        if price < low:
            venue_relations.append("BELOW_ZONE")
        elif price > high:
            venue_relations.append("ABOVE_ZONE")
        else:
            venue_relations.append("INSIDE_ZONE")
    unique = set(venue_relations)
    if len(unique) == 1:
        return venue_relations[0]
    return "CROSS_VENUE_DIVERGENCE"


def relation_transition(prior: str | None, current: str) -> str:
    """Describe snapshot movement without mutating a value-zone lifecycle."""

    if current not in ZONE_RELATIONS:
        raise GuardError(f"invalid current relation: {current}")
    if prior in (None, ""):
        return "INITIAL_OBSERVATION"
    if prior not in ZONE_RELATIONS:
        raise GuardError(f"invalid prior relation: {prior}")
    if prior == current:
        return "UNCHANGED"
    if current == "INSIDE_ZONE" and prior == "BELOW_ZONE":
        return "REENTERED_FROM_BELOW"
    if current == "INSIDE_ZONE" and prior == "ABOVE_ZONE":
        return "REENTERED_FROM_ABOVE"
    if prior == "INSIDE_ZONE" and current == "ABOVE_ZONE":
        return "EXITED_ABOVE"
    if prior == "INSIDE_ZONE" and current == "BELOW_ZONE":
        return "EXITED_BELOW"
    if current == "CROSS_VENUE_DIVERGENCE":
        return "DIVERGED"
    if prior == "CROSS_VENUE_DIVERGENCE":
        return "CONVERGED_" + current.removesuffix("_ZONE")
    return "RELATION_CHANGED"


def classify_zone_observation(
    prices: Mapping[str, Any] | Sequence[Any],
    lower: Any,
    upper: Any,
    prior_relation: str | None = None,
) -> dict[str, Any]:
    current = classify_zone(prices, lower, upper)
    if isinstance(prices, Mapping):
        by_venue = {
            str(venue): classify_zone({str(venue): price}, lower, upper)
            for venue, price in prices.items()
        }
    else:
        by_venue = {
            str(index): classify_zone([price], lower, upper)
            for index, price in enumerate(prices)
        }
    result = {
        "price_relation": current,
        "quote_relation_by_venue": by_venue,
        "relation_transition": relation_transition(prior_relation, current),
    }
    if prior_relation is not None:
        result["prior_price_relation"] = prior_relation
    return result


def _normalize_candle(candle: Any, index: int) -> dict[str, Any]:
    if isinstance(candle, Mapping):
        required = ("open_time", "close_time", "open", "high", "low", "close")
        missing = [field for field in required if field not in candle]
        if missing:
            raise GuardError(f"candles[{index}] missing fields: {', '.join(missing)}")
        values = {field: candle[field] for field in required}
    elif isinstance(candle, Sequence) and not isinstance(candle, (str, bytes)):
        if len(candle) < 7:
            raise GuardError(f"candles[{index}] Binance array must have at least 7 items")
        values = {
            "open_time": candle[0],
            "open": candle[1],
            "high": candle[2],
            "low": candle[3],
            "close": candle[4],
            "close_time": candle[6],
        }
    else:
        raise GuardError(f"candles[{index}] must be an object or Binance kline array")

    open_time = _nonnegative_integer(
        values["open_time"], f"candles[{index}].open_time"
    )
    close_time = _nonnegative_integer(
        values["close_time"], f"candles[{index}].close_time"
    )
    if close_time <= open_time:
        raise GuardError(f"candles[{index}] close_time must be after open_time")
    prices = {
        field: _decimal(values[field], f"candles[{index}].{field}")
        for field in ("open", "high", "low", "close")
    }
    if any(price <= 0 for price in prices.values()):
        raise GuardError(f"candles[{index}] OHLC prices must be positive")
    if prices["low"] > min(prices["open"], prices["close"], prices["high"]):
        raise GuardError(f"candles[{index}] low is inconsistent with OHLC")
    if prices["high"] < max(prices["open"], prices["close"], prices["low"]):
        raise GuardError(f"candles[{index}] high is inconsistent with OHLC")
    return {
        "index": index,
        "open_time": open_time,
        "close_time": close_time,
        **prices,
    }


def inspect_candles(
    candles: Sequence[Any], threshold: Any, as_of_ms: Any,
    boundary_role: str = "UPPER",
) -> dict[str, Any]:
    """Inspect only completed candles; close and full-range claims stay separate."""

    if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes)):
        raise GuardError("candles must be an array")
    level = _decimal(threshold, "threshold")
    if level <= 0:
        raise GuardError("threshold must be positive")
    cutoff = _nonnegative_integer(as_of_ms, "as_of_ms")
    if boundary_role not in {"LOWER", "UPPER"}:
        raise GuardError("boundary_role must be LOWER or UPPER")
    normalized = [_normalize_candle(candle, index) for index, candle in enumerate(candles)]
    chronological = sorted(
        normalized, key=lambda candle: (candle["open_time"], candle["close_time"])
    )
    for earlier, later in zip(chronological, chronological[1:]):
        # Candle intervals include their close timestamp.  Adjacent exchange
        # bars therefore start strictly after the prior close (normally +1ms).
        if later["open_time"] <= earlier["close_time"]:
            raise GuardError(
                "candles contain overlapping intervals: "
                f"{earlier['index']} and {later['index']}"
            )
    # Semantic positions are chronological, never caller-array positions.  A
    # source order is audit-only and must not change an observation fingerprint.
    ordered = [
        {**candle, "semantic_index": index}
        for index, candle in enumerate(chronological)
    ]
    completed = [candle for candle in ordered if candle["close_time"] <= cutoff]
    incomplete = [candle for candle in ordered if candle["close_time"] > cutoff]
    low_breaches = [
        candle["semantic_index"] for candle in completed if candle["low"] < level
    ]
    close_breaches = [
        candle["semantic_index"] for candle in completed if candle["close"] < level
    ]
    def boundary_relation(value: Decimal) -> str:
        return "BELOW_BOUNDARY" if value < level else "AT_OR_ABOVE_BOUNDARY"

    bars = [
        {
            "index": candle["semantic_index"],
            "bar_status": "CLOSED" if candle["close_time"] <= cutoff else "OPEN",
            "open_relation": boundary_relation(candle["open"]),
            "high_relation": boundary_relation(candle["high"]),
            "low_relation": boundary_relation(candle["low"]),
            "close_relation": boundary_relation(candle["close"]),
        }
        for candle in ordered
    ]
    return {
        "completed_count": len(completed),
        "incomplete_count": len(incomplete),
        "completed_indices": [candle["semantic_index"] for candle in completed],
        "incomplete_indices": [candle["semantic_index"] for candle in incomplete],
        "held_above": bool(completed) and not low_breaches,
        "closed_above": bool(completed) and not close_breaches,
        "low_breach_indices": low_breaches,
        "close_breach_indices": close_breaches,
        "closed_bar_relation_sequence": [
            {
                "bar_status": "CLOSED",
                "open_time_ms": ordered[bar["index"]]["open_time"],
                "close_time_ms": ordered[bar["index"]]["close_time"],
                "as_of_ms": cutoff,
                "boundary_role": boundary_role,
                "open_u": _canonical_number(ordered[bar["index"]]["open"])[1],
                "high_u": _canonical_number(ordered[bar["index"]]["high"])[1],
                "low_u": _canonical_number(ordered[bar["index"]]["low"])[1],
                "close_u": _canonical_number(ordered[bar["index"]]["close"])[1],
                "boundary_u": _canonical_number(level)[1],
                "open_relation": bar["open_relation"],
                "high_relation": bar["high_relation"],
                "low_relation": bar["low_relation"],
                "close_relation": bar["close_relation"],
            }
            for bar in bars
            if bar["bar_status"] == "CLOSED"
        ],
        "bars": bars,
    }


def _zone_semantics(value_zones: Any) -> list[dict[str, Any]]:
    if not isinstance(value_zones, list):
        return []
    result: list[dict[str, Any]] = []
    for zone in value_zones:
        if not isinstance(zone, Mapping):
            continue
        result.append(
            {
                "zone_id": zone.get("zone_id"),
                "role": zone.get("role"),
                "band": zone.get("band"),
                "lifecycle": zone.get("lifecycle"),
            }
        )
    return sorted(result, key=lambda zone: str(zone.get("zone_id")))


def _portfolio_semantics(portfolio: Any, include_rationale: bool = False) -> dict[str, Any]:
    if not isinstance(portfolio, Mapping):
        return {}
    result: dict[str, Any] = {}
    for module in PORTFOLIO_MODULES:
        item = portfolio.get(module)
        if not isinstance(item, Mapping):
            continue
        projection = {
            "decision": item.get("decision"),
            "amount_u": item.get("amount_u"),
            "net_exposure_impact": item.get("net_exposure_impact"),
            "capital_pool_impact": item.get("capital_pool_impact"),
        }
        if include_rationale:
            projection["reason_class"] = item.get("reason_class")
            projection["zero_amount_reason_class"] = item.get(
                "zero_amount_reason_class"
            )
        result[module] = projection
    return result


def _candidate_semantics(candidates: Any, user_visible_only: bool = False) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if user_visible_only and not candidate.get("report_relevant", False):
            continue
        entries: list[dict[str, Any]] = []
        raw_entries = candidate.get("entries", [])
        if isinstance(raw_entries, list):
            for entry in raw_entries:
                if not isinstance(entry, Mapping):
                    continue
                entries.append(
                    {
                        "entry_id": entry.get("entry_id"),
                        "price_band_u": entry.get("price_band_u"),
                        "price_u": entry.get("price_u"),
                        "amount_u": entry.get("amount_u"),
                        "amount_basis": entry.get("amount_basis"),
                        "value_role": entry.get("value_role"),
                        "eligibility": entry.get("eligibility"),
                        "formation_conditions": entry.get("formation_conditions"),
                        "invalidation_conditions": entry.get(
                            "invalidation_conditions"
                        ),
                        "evidence_confidence": entry.get("evidence_confidence"),
                        "account_precondition": entry.get("account_precondition"),
                        "carried_forward": entry.get("carried_forward", False),
                    }
                )
        entries.sort(key=lambda item: str(item.get("entry_id")))
        result.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "kind": candidate.get("kind"),
                "eligibility": candidate.get("eligibility"),
                "report_relevant": candidate.get("report_relevant"),
                "entries": entries,
            }
        )
    return sorted(result, key=lambda item: str(item.get("candidate_id")))


def _candidate_eligibility_semantics(candidates: Any) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    entries: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        raw_entries = candidate.get("entries", [])
        if not isinstance(raw_entries, list):
            continue
        for entry in raw_entries:
            if isinstance(entry, Mapping):
                entries.append(
                    {
                        "entry_id": entry.get("entry_id"),
                        "eligibility": entry.get("eligibility"),
                    }
                )
    return sorted(entries, key=lambda item: str(item.get("entry_id")))


def observation_semantics(bundle: Mapping[str, Any]) -> dict[str, Any]:
    observation = bundle.get("observation", {})
    if not isinstance(observation, Mapping):
        observation = {}
    closed = observation.get("closed_bar_relation_sequence")
    if isinstance(closed, list):
        closed_semantics: Any = [
            {
                "boundary_u": bar.get("boundary_u"),
                "boundary_role": bar.get("boundary_role"),
                "open_relation": bar.get("open_relation"),
                "high_relation": bar.get("high_relation"),
                "low_relation": bar.get("low_relation"),
                "close_relation": bar.get("close_relation"),
            }
            for bar in closed
            if isinstance(bar, Mapping)
        ]
    else:
        closed_semantics = closed
    # Raw prices, fetch time, raw OHLC values, OPEN bars, and account unknowns
    # are deliberately absent. Legacy boundary relations and a separately
    # verified hourly review may open deep review; neither is a decision.
    semantics = {
        "normalization_revision": observation.get("normalization_revision"),
        "source_health_class": observation.get("source_health_class"),
        "quote_relation_by_venue": observation.get("quote_relation_by_venue"),
        "closed_bar_relation_sequence": closed_semantics,
        "cross_venue_divergence_class": observation.get(
            "cross_venue_divergence_class"
        ),
        "evidence_availability_by_dimension": observation.get(
            "evidence_availability_by_dimension"
        ),
        "event_discriminator_status": observation.get(
            "event_discriminator_status"
        ),
        "final_expiry_status": observation.get("final_expiry_status"),
    }
    if "hourly_review_binding" in observation:
        # A bound hourly input can change while all quote relations remain
        # unchanged. This is observation identity only, never a decision.
        semantics["hourly_review_binding"] = observation["hourly_review_binding"]
    return semantics


def decision_semantics(bundle: Mapping[str, Any]) -> dict[str, Any]:
    causal = bundle.get("causal_attribution", {})
    if not isinstance(causal, Mapping):
        causal = {}
    risk = bundle.get("major_risk", {})
    if not isinstance(risk, Mapping):
        risk = {}
    from decision_report import causal_mechanism_semantics
    portfolio = _portfolio_semantics(bundle.get("portfolio_decision"))
    if _is_expressive_report(bundle.get("report_semantics")) and isinstance(bundle.get("portfolio_decision"), Mapping):
        # Proposed purpose and activation/invalidation terms are decisions too;
        # account prose and stylistic explanation are deliberately not hashed.
        for module, item in bundle.get("portfolio_decision", {}).items():
            if module in portfolio and isinstance(item, Mapping):
                for key in ("portfolio_role", "reason_class", "zero_amount_reason_class",
                            "formation_conditions", "invalidation_conditions"):
                    portfolio[module][key] = item.get(key)
    semantics = {
        "primary_causal_hypothesis": {
            "hypothesis_id": causal.get("selected_hypothesis_id"),
            "mechanism": causal_mechanism_semantics(bundle),
        },
        "driver_regime": causal.get("driver_regime"),
        "driver_components": sorted(causal.get("driver_components", []))
        if isinstance(causal.get("driver_components"), list)
        else causal.get("driver_components"),
        "value_zone_lifecycle_by_zone_id": _zone_semantics(bundle.get("value_zones")),
        "anchor_map_semantics": bundle.get("anchor_map_semantics"),
        "path_decision": bundle.get("path_decision"),
        "forward_value_band_semantics": bundle.get("forward_value_band_semantics"),
        "portfolio_stance_by_module": portfolio,
        "trade_candidate_decisions": _candidate_semantics(bundle.get("trade_candidates")),
        "account_fact_semantics": bundle.get("account_facts"),
        "major_risk_semantics": {
            "active": risk.get("active", False),
            "kinds": risk.get("kinds", []),
            "portfolio_effect": risk.get("portfolio_effect"),
        },
    }
    if "material_pattern" in causal:
        from pattern_semantics import semantic as pattern_semantic
        semantics["primary_causal_hypothesis"]["material_pattern"] = pattern_semantic(causal["material_pattern"])
    return semantics


def report_semantics(bundle: Mapping[str, Any]) -> dict[str, Any]:
    semantics = bundle.get("report_semantics", {})
    if _is_expressive_report(semantics):
        from decision_report import semantic_projection
        return semantic_projection(semantics)
    if not isinstance(semantics, Mapping):
        return {}
    return {field: semantics.get(field) for field in REPORT_SEMANTIC_FIELDS}


def compute_fingerprints(bundle: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(bundle, Mapping):
        raise GuardError("decision bundle must be an object")
    return {
        "observation_fingerprint": _fingerprint(observation_semantics(bundle)),
        "decision_fingerprint": _fingerprint(decision_semantics(bundle)),
        "report_fingerprint": _fingerprint(report_semantics(bundle)),
    }


def compute_bundle_identity(bundle: Mapping[str, Any]) -> str:
    """Bind a FINAL receipt to the exact validated PRE_WRITE artifact."""

    if not isinstance(bundle, Mapping):
        raise GuardError("decision bundle must be an object")
    return _fingerprint({"schema": SCHEMA_VERSION, "prewrite_bundle": bundle})


def decision_comparison(
    bundle: Mapping[str, Any], previous_baseline: Revision3Baseline | None = None,
) -> dict[str, Any]:
    """Compare bound decisions without rewriting historical identity hashes.

    The three stored fingerprints retain their original algorithm.  Only a
    revision-3 baseline supplied by the validating caller enables this
    same-domain comparison; caller-declared previous hashes are never replaced.
    FACT_SYNC consumption is a run fact, while fact Owner versions remain a
    decision input.  This diagnostic is not a validation or write authority.
    """

    if previous_baseline is None or bundle.get("write_transition") == "MIGRATION":
        previous = bundle.get("previous_fingerprints", {})
        prior_hash = previous.get("decision_fingerprint") if isinstance(previous, Mapping) else None
        current_hash = compute_fingerprints(bundle)["decision_fingerprint"]
        mode = "RAW_FINGERPRINT"
    else:
        prior = extract_projection_sidecar(previous_baseline.state_text)["canonical_semantics"]

        def comparable(value: Mapping[str, Any]) -> dict[str, Any]:
            semantics = decision_semantics(value)
            account = semantics.get("account_fact_semantics")
            if isinstance(account, Mapping):
                account = dict(account)
                for field in (
                    "account_fact_changed_known", "known_change_evidence", "local_record_status",
                ):
                    account.pop(field, None)
                semantics["account_fact_semantics"] = account
            return semantics

        prior_hash = _fingerprint(comparable(prior))
        current_hash = _fingerprint(comparable(bundle))
        mode = "BOUND_REVISION3_FACT_SYNC_COMPAT"
    return {
        "mode": mode,
        "previous_decision_comparison_fingerprint": prior_hash,
        "current_decision_comparison_fingerprint": current_hash,
        "decision_changed": not prior_hash or current_hash != prior_hash,
    }


def _validate_hourly_integration(
    bundle: Mapping[str, Any], previous_baseline: Revision3Baseline | None,
    errors: list[str], *, baseline_mode: bool = False, allow_pattern_retirement: bool = False,
) -> None:
    """Bind new decisions to persisted hourly evidence without touching old v3."""
    from pattern_semantics import (
        load_hourly_review, semantic as pattern_semantic, validate_binding,
        validate_retention,
    )
    observation = bundle.get("observation", {})
    causal = bundle.get("causal_attribution", {})
    binding = observation.get("hourly_review_binding") if isinstance(observation, Mapping) else None
    material = causal.get("material_pattern") if isinstance(causal, Mapping) else None
    if isinstance(observation, Mapping) and "hourly_review_binding" in observation:
        binding_errors = validate_binding(binding)
        errors.extend(binding_errors)
        if not binding_errors and not baseline_mode:
            try:
                bound = load_hourly_review(binding)
            except (ValueError, OSError, ImportError) as exc:
                errors.append(f"hourly review binding is invalid: {exc}")
            else:
                if bound.get("deep_eligible") is not True:
                    errors.append("a decision bundle requires a real bound hourly deep-review candidate")
                cutoff = _parse_iso8601(bound.get("evidence", {}).get("cutoff"), "hourly evidence cutoff", errors)
                observed = _parse_iso8601(observation.get("observed_at"), "observation.observed_at", errors)
                if cutoff and observed and cutoff > observed:
                    errors.append("complete hourly evidence cutoff cannot postdate the decision evidence cutoff")
    if baseline_mode:
        return
    prior_material = None
    if previous_baseline is not None:
        try:
            previous = extract_projection_sidecar(previous_baseline.state_text)["canonical_semantics"]
            prior_material = previous.get("causal_attribution", {}).get("material_pattern")
        except (GuardError, KeyError, TypeError, AttributeError):
            pass  # The baseline contract emits the authoritative failure.
    # v4 may retire an obsolete optional v3 projection after actual whole-cycle
    # approval. Any pattern still supplied retains its original strict checks.
    if not (allow_pattern_retirement and material is None):
        errors.extend(validate_retention(material, prior_material))
    if material is not None:
        try:
            changed = pattern_semantic(material) != pattern_semantic(prior_material)
        except (ValueError, TypeError):
            changed = True
        if changed and (not isinstance(material, Mapping) or binding is None or material.get("review_binding") != binding):
            errors.append("new or changed material patterns must bind the current persisted hourly deep review")


def _validate_account_fact_transition(
    bundle: Mapping[str, Any], previous_baseline: Revision3Baseline | None,
    actual_fact_fingerprints: Mapping[str, str] | None, errors: list[str],
) -> bool:
    """Shared live fact-version/confirmation gate, also used before public derivation."""

    account_facts = bundle.get("account_facts")
    preference_recorded = False
    if not isinstance(account_facts, Mapping):
        errors.append("account_facts must be an object")
        account_changed = False
    else:
        account_changed = account_facts.get("account_fact_changed_known")
        if not isinstance(account_changed, bool):
            errors.append("account_facts.account_fact_changed_known must be boolean")
            account_changed = False
        if account_changed:
            if not isinstance(account_facts.get("known_change_evidence"), list) or not _nonempty(
                account_facts.get("known_change_evidence")
            ):
                errors.append("known account fact change requires explicit known_change_evidence")
            if account_facts.get("local_record_status") != "FACT_SYNC_PENDING":
                errors.append("known account fact change requires local_record_status=FACT_SYNC_PENDING")
        elif "known_change_evidence" in account_facts or "local_record_status" in account_facts:
            preference_recorded = account_facts.get("known_change_evidence") == [{
                "confirmation_type": PREFERENCE_CONFIRMATION_TYPE,
                "owner_id": "ASSET_CONFIGURATION",
            }] and account_facts.get("local_record_status") == "PREFERENCE_RECORDED"
            if not preference_recorded:
                errors.append("unchanged account facts may only acknowledge a recorded ASSET_CONFIGURATION preference change")
        declared = account_facts.get("fact_owner_fingerprints")
        if actual_fact_fingerprints is not None and declared != dict(actual_fact_fingerprints):
            errors.append("account_facts.fact_owner_fingerprints must equal the actual local fact Owners")
        previous_facts: Mapping[str, Any] | None = None
        if bundle.get("write_transition") != "MIGRATION" and previous_baseline is not None:
            try:
                prior = extract_projection_sidecar(previous_baseline.state_text)["canonical_semantics"]
            except (GuardError, KeyError, TypeError) as exc:
                errors.append(f"cannot derive previous fact Owner baseline: {exc}")
            else:
                previous_account = prior.get("account_facts") if isinstance(prior, Mapping) else None
                versions = previous_account.get("fact_owner_fingerprints") if isinstance(previous_account, Mapping) else None
                if isinstance(versions, Mapping):
                    previous_facts = versions
                else:
                    errors.append("previous projection lacks bound fact Owner fingerprints")
        if bundle.get("write_transition") == "MIGRATION" and account_changed:
            errors.append("MIGRATION cannot also assert a new account fact change")
        if bundle.get("write_transition") == "MIGRATION" and preference_recorded:
            errors.append("MIGRATION cannot also assert a new preference change")
        if previous_facts is not None and isinstance(declared, Mapping):
            changed_owner_ids = {
                owner_id for owner_id in FACT_OWNER_PATHS
                if declared.get(owner_id) != previous_facts.get(owner_id)
            }
            if account_changed:
                if not changed_owner_ids:
                    errors.append("FACT_SYNC requires an actual local fact Owner version change")
                confirmations = account_facts.get("known_change_evidence")
                confirmed_owner_ids = {
                    item.get("owner_id") for item in confirmations
                    if isinstance(item, Mapping) and item.get("confirmation_type") in FACT_CONFIRMATION_TYPES
                } if isinstance(confirmations, list) else set()
                if changed_owner_ids != confirmed_owner_ids:
                    errors.append("FACT_SYNC confirmations must exactly identify changed fact Owners")
            elif preference_recorded:
                if changed_owner_ids != {"ASSET_CONFIGURATION"}:
                    errors.append("recorded preference change requires only ASSET_CONFIGURATION to actually change")
            elif changed_owner_ids:
                errors.append("changed local fact Owner versions require account_fact_changed_known=true and FACT_SYNC")
    requirement = bundle.get("user_input_requirement")
    if requirement not in {"NONE", "FACT_SYNC"}:
        errors.append("user_input_requirement must be NONE or FACT_SYNC")
    if requirement == "FACT_SYNC" and not account_changed:
        errors.append("FACT_SYNC requires account_fact_changed_known=true")
    if account_changed and requirement != "FACT_SYNC":
        errors.append("known account fact change requires FACT_SYNC")
    return account_changed


def _derive_control_unchecked(
    bundle: Mapping[str, Any], previous_baseline: Revision3Baseline | None = None,
) -> dict[str, Any]:
    """Derive A/D/E from semantic fingerprints, never from a price relation alone."""

    current = compute_fingerprints(bundle)
    previous = bundle.get("previous_fingerprints", {})
    if not isinstance(previous, Mapping):
        previous = {}
    previous_report = previous.get("report_fingerprint")
    decision_changed = decision_comparison(bundle, previous_baseline)["decision_changed"]
    report_changed = not previous_report or current["report_fingerprint"] != previous_report

    event = bundle.get("event", {})
    final_event = bool(event.get("final_event_reached", False)) if isinstance(event, Mapping) else False
    requested_transition = bundle.get("write_transition")
    if requested_transition == "MIGRATION" and _is_expressive_report(bundle.get("report_semantics")):
        from revision2_report_bridge import migration_compare
        report_changed = migration_compare(report_semantics(bundle))["changed"]
    if requested_transition == "MIGRATION":
        decision_class = "REVISION_MIGRATION"
        monitor_state = "NONE"
        project_patch_required = True
    elif final_event:
        decision_class = "FINAL_EVENT"
        monitor_state = "E"
        project_patch_required = True
    elif decision_changed:
        decision_class = "STRATEGY_SEMANTIC_CHANGE"
        monitor_state = "D"
        project_patch_required = True
    else:
        decision_class = "OBSERVATION_ONLY"
        monitor_state = "A"
        project_patch_required = False

    report_required = bool(
        report_changed
        and (
            monitor_state in {"D", "E"}
            or requested_transition == "MIGRATION"
        )
    )
    risk = bundle.get("major_risk")
    attention_level = (
        "NONE"
        if not report_required
        else (
            "MAJOR_RISK"
            if isinstance(risk, Mapping) and risk.get("active") is True
            else "CHANGE_NOTICE"
        )
    )

    return {
        "write_transition": (
            "MIGRATION"
            if requested_transition == "MIGRATION"
            else (monitor_state if monitor_state in {"D", "E"} else "NONE")
        ),
        "decision_class": decision_class,
        "monitor_state": monitor_state,
        "project_patch_required": project_patch_required,
        "report_required": report_required,
        "attention_planned": report_required,
        "attention_level": attention_level,
        "decision_changed": decision_changed,
        "report_changed": report_changed,
    }


def derive_control(
    bundle: Mapping[str, Any],
    migration_baseline: MigrationBaseline | None = None,
    previous_baseline: Revision3Baseline | None = None,
    fact_owner_snapshot: FactOwnerSnapshot | None = None,
    *,
    require_owner_baseline: bool = True,
    require_fact_owner_paths: bool = True,
) -> dict[str, Any]:
    """Derive control only after binding the claimed previous projection."""

    if bundle.get("write_transition") == "MIGRATION":
        if migration_baseline is None:
            raise GuardError("MIGRATION control derivation requires the fixed revision-2 baseline")
        baseline_errors = validate_revision2_migration_baseline(bundle, migration_baseline)
    else:
        if previous_baseline is None:
            raise GuardError("control derivation requires an actual revision-3 previous baseline")
        baseline_errors = validate_revision3_baseline(
            bundle, previous_baseline, require_owner_paths=require_owner_baseline
        )
    if baseline_errors:
        raise GuardError("invalid previous baseline: " + "; ".join(baseline_errors))
    actual_facts, fact_errors = _validate_fact_owner_snapshot(
        fact_owner_snapshot, require_owner_paths=require_fact_owner_paths
    )
    _validate_account_fact_schema(bundle.get("account_facts"), fact_errors)
    _validate_account_fact_transition(bundle, previous_baseline, actual_facts, fact_errors)
    if fact_errors:
        raise GuardError("invalid fact Owner baseline: " + "; ".join(fact_errors))
    return _derive_control_unchecked(bundle, previous_baseline)


def reproject_revision2_report_semantics(previous: Any) -> dict[str, Any]:
    """Project an old report's user semantics into the revision-3 eight-field form."""

    if not isinstance(previous, Mapping):
        raise GuardError("MIGRATION requires structured previous_report_semantics")
    semantics = previous.get("report_semantics", previous)
    if not isinstance(semantics, Mapping):
        raise GuardError("MIGRATION previous_report_semantics.report_semantics must be an object")
    projected = {field: semantics.get(field) for field in REPORT_SEMANTIC_FIELDS}
    missing = [field for field, value in projected.items() if not _nonempty(value)]
    if missing:
        raise GuardError(
            "MIGRATION previous_report_semantics cannot be reprojected; missing "
            + ", ".join(missing)
        )
    return projected


def previous_report_fingerprint_from_revision2(bundle: Mapping[str, Any]) -> str:
    if _is_expressive_report(bundle.get("report_semantics")):
        from revision2_report_bridge import bridge_semantics
        return _fingerprint(bridge_semantics())
    return _fingerprint(
        reproject_revision2_report_semantics(bundle.get("previous_report_semantics"))
    )


def previous_decision_fingerprint_from_revision2(
    baseline: MigrationBaseline
) -> str:
    """Bind the one-time old decision identity to both immutable Owner documents."""

    return _fingerprint(
        {
            "schema": "BTC_REVISION2_DECISION_BASELINE_BINDING_V3",
            "strategy_revision": 2,
            "state_content_fingerprint": content_fingerprint(baseline.state_text),
            "plan_content_fingerprint": content_fingerprint(baseline.plan_text),
        }
    )


def _validate_observation_controlled_fields(
    observation: Any, path: str, errors: list[str]
) -> None:
    if not isinstance(observation, Mapping):
        errors.append(f"{path} must be an object")
        return
    if observation.get("normalization_revision") not in OBSERVATION_NORMALIZATION_REVISIONS:
        errors.append(f"{path}.normalization_revision is invalid")
    if observation.get("source_health_class") not in SOURCE_HEALTH_CLASSES:
        errors.append(f"{path}.source_health_class is invalid")
    if observation.get("event_discriminator_status") not in EVENT_DISCRIMINATOR_STATUSES:
        errors.append(f"{path}.event_discriminator_status is invalid")
    if observation.get("final_expiry_status") not in FINAL_EXPIRY_STATUSES:
        errors.append(f"{path}.final_expiry_status is invalid")
    final_completed = observation.get("final_expiry_status") == "COMPLETED"
    final_discriminator = (
        observation.get("event_discriminator_status") == "FINAL_EVENT_REACHED"
    )
    if final_completed != final_discriminator:
        errors.append(
            f"{path}.final_expiry_status and event_discriminator_status conflict"
        )
    availability = observation.get("evidence_availability_by_dimension")
    if not isinstance(availability, Mapping):
        errors.append(f"{path}.evidence_availability_by_dimension must be an object")
    else:
        _reject_unknown_keys(
            availability, set(REQUIRED_EVIDENCE_DIMENSIONS),
            f"{path}.evidence_availability_by_dimension", errors,
        )
        if set(availability) != set(REQUIRED_EVIDENCE_DIMENSIONS):
            errors.append(
                f"{path}.evidence_availability_by_dimension must contain every dimension"
            )
        for dimension, status in availability.items():
            if not isinstance(status, str) or status not in EVIDENCE_STATUSES:
                errors.append(
                    f"{path}.evidence_availability_by_dimension.{dimension} is invalid"
                )
        quote_availability = availability.get("price_quotes")
        health = observation.get("source_health_class")
        if health == "PUBLIC_QUOTES_HEALTHY_DECISION_DATA_LIMITED" and quote_availability != "VERIFIED":
            errors.append(f"{path}.source_health_class conflicts with quote availability")
        if health == "PUBLIC_QUOTES_CONFLICTED" and quote_availability != "CONTRADICTED":
            errors.append(f"{path}.source_health_class conflicts with quote availability")
        if health == "PUBLIC_QUOTES_UNAVAILABLE" and quote_availability != "UNAVAILABLE":
            errors.append(f"{path}.source_health_class conflicts with quote availability")
    prior = observation.get("prior_price_relation")
    if prior is not None and prior not in ZONE_RELATIONS:
        errors.append(f"{path}.prior_price_relation is invalid")


def _validate_closed_bar_sequence(
    closed: Any, path: str, errors: list[str],
    *, primary_band: Mapping[str, Any] | None = None,
    observed_at: Any = None,
) -> None:
    if closed == "NO_CLOSED_BAR_EVIDENCE":
        return
    if not isinstance(closed, list) or not closed:
        errors.append(f"{path} must be closed-bar records or NO_CLOSED_BAR_EVIDENCE")
        return
    previous_close: int | None = None
    shared_as_of: int | None = None
    observed = _parse_iso8601(observed_at, f"{path}.observed_at", [])
    observed_ms = int(observed.timestamp() * 1000) if observed is not None else None
    for index, bar in enumerate(closed):
        bar_path = f"{path}[{index}]"
        if not _reject_unknown_keys(bar, CLOSED_BAR_KEYS, bar_path, errors):
            continue
        if set(bar) != CLOSED_BAR_KEYS:
            errors.append(f"{bar_path} must contain exactly the closed-bar fields")
        if bar.get("bar_status") != "CLOSED":
            errors.append(f"{bar_path}.bar_status must equal CLOSED")
        role = bar.get("boundary_role")
        if role not in {"LOWER", "UPPER"}:
            errors.append(f"{bar_path}.boundary_role must be LOWER or UPPER")
        times: dict[str, int] = {}
        for field in ("open_time_ms", "close_time_ms", "as_of_ms"):
            try:
                times[field] = _nonnegative_integer(
                    bar.get(field), f"{bar_path}.{field}"
                )
            except GuardError as exc:
                errors.append(str(exc))
        if len(times) == 3:
            if times["open_time_ms"] >= times["close_time_ms"]:
                errors.append(f"{bar_path} close_time_ms must follow open_time_ms")
            if times["close_time_ms"] > times["as_of_ms"]:
                errors.append(f"{bar_path} is not closed at as_of_ms")
            if observed_ms is not None and times["as_of_ms"] > observed_ms:
                errors.append(f"{bar_path}.as_of_ms cannot exceed observation observed_at")
            if shared_as_of is None:
                shared_as_of = times["as_of_ms"]
            elif times["as_of_ms"] != shared_as_of:
                errors.append(f"{bar_path}.as_of_ms must equal the sequence cutoff")
            if previous_close is not None and times["open_time_ms"] <= previous_close:
                errors.append(f"{bar_path} overlaps or is not chronologically ordered")
            previous_close = times["close_time_ms"]
        numeric: dict[str, Decimal] = {}
        for field in ("open", "high", "low", "close", "boundary"):
            key = f"{field}_u"
            try:
                numeric[field] = _decimal(bar.get(key), f"{bar_path}.{key}")
            except GuardError as exc:
                errors.append(str(exc))
        if len(numeric) != 5:
            continue
        if any(number <= 0 for number in numeric.values()):
            errors.append(f"{bar_path} OHLC and boundary must be positive")
        if numeric["low"] > min(numeric["open"], numeric["close"], numeric["high"]):
            errors.append(f"{bar_path}.low_u is inconsistent with OHLC")
        if numeric["high"] < max(numeric["open"], numeric["close"], numeric["low"]):
            errors.append(f"{bar_path}.high_u is inconsistent with OHLC")
        for field in ("open", "high", "low", "close"):
            expected = (
                "BELOW_BOUNDARY"
                if numeric[field] < numeric["boundary"]
                else "AT_OR_ABOVE_BOUNDARY"
            )
            if bar.get(f"{field}_relation") != expected:
                errors.append(
                    f"{bar_path}.{field}_relation conflicts with numeric OHLC/boundary"
                )
        if primary_band is not None and role in {"LOWER", "UPPER"}:
            bound_key = "lower_u" if role == "LOWER" else "upper_u"
            try:
                expected_boundary = _decimal(
                    primary_band.get(bound_key), f"primary_band.{bound_key}"
                )
            except GuardError as exc:
                errors.append(str(exc))
            else:
                if numeric["boundary"] != expected_boundary:
                    errors.append(
                        f"{bar_path}.boundary_u must equal primary {role.lower()} bound"
                    )


def _validate_unavailable_quote_evidence(
    evidence_value: Any, observation: Any, errors: list[str]
) -> None:
    path = "unavailable observation.price_quote_evidence"
    exact_keys = {
        "status", "sources", "time_range", "supporting", "conflicts", "missing",
        "independence_groups", "access_scope",
    }
    if not _reject_unknown_keys(evidence_value, exact_keys, path, errors):
        return
    if set(evidence_value) != exact_keys:
        errors.append(f"{path} must contain exactly the lightweight quote evidence fields")
    if evidence_value.get("status") != "VERIFIED":
        errors.append(f"{path}.status must equal VERIFIED")
    if isinstance(observation, Mapping):
        availability = observation.get("evidence_availability_by_dimension")
        if not isinstance(availability, Mapping) or availability.get(
            "price_quotes"
        ) != "VERIFIED":
            errors.append(f"{path} conflicts with observation quote availability")
        if observation.get("source_health_class") in {
            "PUBLIC_QUOTES_CONFLICTED", "PUBLIC_QUOTES_UNAVAILABLE",
        }:
            errors.append(f"{path} conflicts with observation source health")
    if evidence_value.get("access_scope") != "PUBLIC_UNAUTHENTICATED":
        errors.append(f"{path}.access_scope must equal PUBLIC_UNAUTHENTICATED")
    observed_at = _parse_iso8601(
        observation.get("observed_at") if isinstance(observation, Mapping) else None,
        f"{path}.observation_observed_at", [],
    )
    _validate_time_range(
        evidence_value.get("time_range"), f"{path}.time_range", errors,
        not_after=observed_at,
    )
    for field in ("supporting", "conflicts", "missing", "independence_groups"):
        _validate_string_list(evidence_value.get(field), f"{path}.{field}", errors)
    sources = evidence_value.get("sources")
    if not isinstance(sources, list) or len(sources) < 2:
        errors.append(f"{path}.sources requires at least two independent public quote sources")
        return
    for index, source in enumerate(sources):
        _validate_https_source(
            source, "price_quotes", index, errors, observed_at=observed_at
        )
    provenance = [_registered_quote_provenance(source) for source in sources]
    if any(item is None for item in provenance):
        errors.append(f"{path}.sources must use the fixed quote provenance registry")
    origins = {item[0].casefold() for item in provenance if item is not None}
    venues = {item[1].casefold() for item in provenance if item is not None}
    endpoints = {item[2] for item in provenance if item is not None}
    groups = {
        identity for value in evidence_value.get("independence_groups", [])
        if (identity := _source_identity(value)) is not None
    } if isinstance(evidence_value.get("independence_groups"), list) else set()
    if len(origins) < 2 or groups != origins:
        errors.append(f"{path}.independence_groups must equal independent quote origins")
    if len(endpoints) != len(sources):
        errors.append(f"{path}.sources cannot repeat endpoint URLs")
    relations = observation.get("quote_relation_by_venue") if isinstance(
        observation, Mapping
    ) else None
    raw_prices = observation.get("raw_prices") if isinstance(observation, Mapping) else None
    relation_venues = {
        identity for venue in relations
        if (identity := _source_identity(venue)) is not None
    } if isinstance(relations, Mapping) else set()
    raw_venues = {
        identity for venue in raw_prices
        if (identity := _source_identity(venue)) is not None
    } if isinstance(raw_prices, Mapping) else set()
    if (
        venues != relation_venues or venues != raw_venues
        or len(relation_venues) != len(relations or {})
        or len(raw_venues) != len(raw_prices or {})
    ):
        errors.append(f"{path} source/raw/relation venues must match exactly")


def _validate_unavailable_observation(
    value: Any, errors: list[str],
    baseline_semantics: Mapping[str, Any] | None = None,
    actual_fact_fingerprints: Mapping[str, str] | None = None,
) -> str | None:
    if not _reject_unknown_keys(
        value, UNAVAILABLE_OBSERVATION_KEYS, "unavailable observation", errors
    ):
        return None
    if set(value) != UNAVAILABLE_OBSERVATION_KEYS:
        errors.append("unavailable observation must contain exactly its lightweight fields")
    if value.get("schema") != UNAVAILABLE_OBSERVATION_SCHEMA:
        errors.append(f"unavailable observation.schema must be {UNAVAILABLE_OBSERVATION_SCHEMA}")
    observation = value.get("observation")
    evaluation_as_of = _parse_iso8601(
        value.get("evaluation_as_of"),
        "unavailable observation.evaluation_as_of", errors,
    )
    if (
        evaluation_as_of is not None
        and evaluation_as_of > datetime.now(timezone.utc) + MAX_FUTURE_CLOCK_SKEW
    ):
        errors.append("unavailable observation.evaluation_as_of cannot be in the future")
    if isinstance(baseline_semantics, Mapping) and _is_expressive_report(baseline_semantics.get("report_semantics")):
        cycle = baseline_semantics.get("event", {}).get("cycle", {})
        final = next((item for item in cycle.get("events", [])
                      if item.get("event_id") == cycle.get("final_expiry_event_id")), None)
        deadline = _parse_iso8601(final.get("due_at") if final else None,
                                  "unavailable baseline final deadline", errors)
        if deadline is not None and deadline <= max(
            datetime.now(timezone.utc), evaluation_as_of or datetime.now(timezone.utc)
        ):
            errors.append("unavailable fallback cannot hide the actual baseline final deadline")
    observed_at: datetime | None = None
    if _reject_unknown_keys(observation, OBSERVATION_KEYS, "unavailable observation.observation", errors):
        if set(observation) - {"hourly_review_binding"} != OBSERVATION_REQUIRED_KEYS:
            errors.append("unavailable observation.observation must contain all normalized fields")
        _validate_observation_controlled_fields(
            observation, "unavailable observation.observation", errors
        )
        if observation.get("source_health_class") == "DECISIVE_SOURCE_CONFLICT":
            errors.append(
                "unavailable fallback cannot silence DECISIVE_SOURCE_CONFLICT"
            )
        if observation.get("final_expiry_status") != "PENDING":
            errors.append("unavailable fallback cannot hide a completed final event")
        if observation.get("event_discriminator_status") == "FINAL_EVENT_REACHED":
            errors.append("unavailable fallback cannot use FINAL_EVENT_REACHED")
        if observation.get("event_discriminator_status") != "DEEP_REVIEW_CANDIDATE":
            errors.append(
                "unavailable fallback requires an actual DEEP_REVIEW_CANDIDATE"
            )
        raw_prices = observation.get("raw_prices")
        relations = observation.get("quote_relation_by_venue")
        if not isinstance(raw_prices, Mapping) or not raw_prices:
            errors.append("unavailable observation.raw_prices must be a non-empty object")
        else:
            for venue, raw in raw_prices.items():
                try:
                    if _decimal(raw, f"unavailable observation.raw_prices.{venue}") <= 0:
                        errors.append(f"unavailable observation.raw_prices.{venue} must be positive")
                except GuardError as exc:
                    errors.append(str(exc))
        if not isinstance(relations, Mapping) or set(relations) != set(raw_prices or {}):
            errors.append("unavailable observation quote venues must equal raw price venues")
        elif len({_source_identity(key) for key in relations}) != len(relations):
            errors.append("unavailable observation venues must be normalized-unique")
        observed_at = _parse_iso8601(
            observation.get("observed_at"),
            "unavailable observation.observed_at", errors,
        )
        if (
            observed_at is not None
            and evaluation_as_of is not None
            and observed_at > evaluation_as_of
        ):
            errors.append(
                "unavailable observation.observed_at cannot exceed evaluation_as_of"
            )
        _validate_closed_bar_sequence(
            observation.get("closed_bar_relation_sequence"),
            "unavailable observation.closed_bar_relation_sequence", errors,
            observed_at=observation.get("observed_at"),
        )
    _validate_unavailable_quote_evidence(
        value.get("price_quote_evidence"), observation, errors
    )
    zones = value.get("value_zones")
    zone_map: dict[Any, Mapping[str, Any]] = {}
    if not isinstance(zones, list) or not zones:
        errors.append("unavailable observation.value_zones must be non-empty")
    else:
        for index, zone in enumerate(zones):
            path = f"unavailable observation.value_zones[{index}]"
            if not _reject_unknown_keys(zone, ZONE_KEYS, path, errors):
                continue
            if set(zone) != ZONE_KEYS:
                errors.append(f"{path} must contain exactly zone_id/role/band/lifecycle")
            band = zone.get("band")
            if _reject_unknown_keys(band, ZONE_BAND_KEYS, f"{path}.band", errors):
                try:
                    lower = _decimal(band.get("lower_u"), f"{path}.band.lower_u")
                    upper = _decimal(band.get("upper_u"), f"{path}.band.upper_u")
                    if lower <= 0 or upper <= 0 or lower > upper:
                        errors.append(f"{path}.band must be positive and ordered")
                except GuardError as exc:
                    errors.append(str(exc))
            lifecycle = zone.get("lifecycle")
            if not isinstance(lifecycle, str) or lifecycle not in LIFECYCLES:
                errors.append(f"{path}.lifecycle is invalid")
            if _nonempty(zone.get("zone_id")):
                if zone.get("zone_id") in zone_map:
                    errors.append(f"{path}.zone_id must be unique")
                zone_map[zone.get("zone_id")] = zone
    anchors = value.get("anchor_map_semantics")
    persistent: Any = None
    if _reject_unknown_keys(anchors, ANCHOR_KEYS, "unavailable observation.anchor_map_semantics", errors):
        if set(anchors) != ANCHOR_KEYS:
            errors.append("unavailable observation.anchor_map_semantics must contain both lists")
        for field in ANCHOR_KEYS:
            _validate_string_list(anchors.get(field), f"unavailable observation.anchor_map_semantics.{field}", errors)
        persistent = anchors.get("persistent_anchor_ids")
    if baseline_semantics is not None:
        if zones != baseline_semantics.get("value_zones"):
            errors.append(
                "unavailable observation.value_zones must equal the actual Owner baseline"
            )
        if anchors != baseline_semantics.get("anchor_map_semantics"):
            errors.append(
                "unavailable observation.anchor_map_semantics must equal the actual Owner baseline"
            )
        baseline_observation = baseline_semantics.get("observation")
        if isinstance(observation, Mapping) and isinstance(
            baseline_observation, Mapping
        ):
            baseline_observed_at = _parse_iso8601(
                baseline_observation.get("observed_at"),
                "baseline observation.observed_at", [],
            )
            if (
                observed_at is not None
                and baseline_observed_at is not None
                and observed_at < baseline_observed_at
            ):
                errors.append(
                    "unavailable observation cannot predate the current Owner baseline"
                )
            current_candidate_basis = observation_semantics(
                {"observation": observation}
            )
            baseline_candidate_basis = observation_semantics(
                {"observation": baseline_observation}
            )
            for transient in ("event_discriminator_status", "final_expiry_status"):
                current_candidate_basis.pop(transient, None)
                baseline_candidate_basis.pop(transient, None)
            # Hourly interpretation can warrant deep review while quote-zone
            # relations and source coverage remain unchanged. The database
            # validator proves the candidate; an arbitrary ID is insufficient.
            hourly_candidate = False
            if "hourly_review_binding" in observation:
                from pattern_semantics import load_hourly_review
                try:
                    bound_review = load_hourly_review(observation["hourly_review_binding"])
                    hourly_candidate = bound_review.get("deep_eligible") is True
                    if not hourly_candidate:
                        errors.append("unavailable fallback needs a real persisted hourly deep-review candidate")
                    hourly_cutoff = _parse_iso8601(bound_review.get("evidence", {}).get("cutoff"), "unavailable hourly evidence cutoff", errors)
                    if hourly_cutoff and observed_at and hourly_cutoff > observed_at:
                        errors.append("unavailable complete hourly evidence cutoff cannot postdate observation.observed_at")
                except (ValueError, OSError, ImportError) as exc:
                    errors.append(f"unavailable hourly review binding is invalid: {exc}")
            current_candidate_basis.pop("hourly_review_binding", None)
            baseline_candidate_basis.pop("hourly_review_binding", None)
            if current_candidate_basis == baseline_candidate_basis and not hourly_candidate:
                errors.append(
                    "unavailable fallback requires a changed normalized observation, "
                    "not a self-declared deep-review status"
                )
    fact_fingerprints = value.get("fact_owner_fingerprints")
    if _reject_unknown_keys(
        fact_fingerprints, set(FACT_OWNER_PATHS),
        "unavailable observation.fact_owner_fingerprints", errors,
    ):
        if set(fact_fingerprints) != set(FACT_OWNER_PATHS):
            errors.append(
                "unavailable observation.fact_owner_fingerprints must contain exactly all fact Owners"
            )
        for owner_id, fingerprint in fact_fingerprints.items():
            if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
                errors.append(
                    f"unavailable observation.fact_owner_fingerprints.{owner_id} must be sha256"
                )
    if actual_fact_fingerprints is not None and fact_fingerprints != dict(
        actual_fact_fingerprints
    ):
        errors.append(
            "unavailable observation fact Owner fingerprints must equal actual local Owners"
        )
    if baseline_semantics is not None:
        baseline_account = baseline_semantics.get("account_facts")
        baseline_fingerprints = baseline_account.get(
            "fact_owner_fingerprints"
        ) if isinstance(baseline_account, Mapping) else None
        if fact_fingerprints != baseline_fingerprints:
            errors.append("unavailable fallback cannot hide a fact Owner version change")
    if isinstance(observation, Mapping) and isinstance(persistent, list) and persistent:
        primary = zone_map.get(persistent[0])
        if primary is None:
            errors.append("unavailable observation primary persistent zone is missing")
        else:
            closed = observation.get("closed_bar_relation_sequence")
            if isinstance(closed, list):
                primary_band = primary.get("band")
                if isinstance(primary_band, Mapping):
                    for index, bar in enumerate(closed):
                        if not isinstance(bar, Mapping):
                            continue
                        role = bar.get("boundary_role")
                        if role not in {"LOWER", "UPPER"}:
                            continue
                        key = "lower_u" if role == "LOWER" else "upper_u"
                        try:
                            actual_boundary = _decimal(
                                bar.get("boundary_u"),
                                f"unavailable observation.closed_bar_relation_sequence[{index}].boundary_u",
                            )
                            expected_boundary = _decimal(
                                primary_band.get(key), f"primary band.{key}"
                            )
                        except GuardError as exc:
                            errors.append(str(exc))
                        else:
                            if actual_boundary != expected_boundary:
                                errors.append(
                                    "unavailable observation closed-bar boundary must bind the primary Owner zone"
                                )
            try:
                computed = classify_zone_observation(
                    observation.get("raw_prices"),
                    primary["band"]["lower_u"], primary["band"]["upper_u"],
                    observation.get("prior_price_relation"),
                )
            except (GuardError, KeyError, TypeError) as exc:
                errors.append(str(exc))
            else:
                for field in (
                    "price_relation", "quote_relation_by_venue", "relation_transition"
                ):
                    if observation.get(field) != computed.get(field):
                        errors.append(f"unavailable observation.{field} conflicts with actual prices")
    if errors or not isinstance(observation, Mapping):
        return None
    return _fingerprint({
        "observation": observation_semantics({"observation": observation}),
        "evaluation_as_of": value.get("evaluation_as_of"),
    })


def validate_unavailable_receipt(
    receipt: Mapping[str, Any],
    previous_baseline: Revision3Baseline | None = None,
    observation_input: Mapping[str, Any] | None = None,
    fact_owner_snapshot: FactOwnerSnapshot | None = None,
    *,
    require_owner_baseline: bool = True,
    require_fact_owner_paths: bool = True,
) -> list[str]:
    """Validate the only allowed silent fallback when Sol/max is unavailable."""

    errors: list[str] = []
    if not isinstance(receipt, Mapping):
        return ["unavailable receipt must be an object"]
    expected = {
        "schema": UNAVAILABLE_RECEIPT_SCHEMA,
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
    }
    allowed = set(expected) | {
        "observation_fingerprint",
        "reused_decision_fingerprint",
        "reused_report_fingerprint",
    }
    _reject_unknown_keys(receipt, allowed, "unavailable receipt", errors)
    if set(receipt) != allowed:
        errors.append("unavailable receipt must contain exactly its final fallback fields")
    for field, value in expected.items():
        if receipt.get(field) != value:
            errors.append(f"{field} must equal {value!r}")
    for field in (
        "observation_fingerprint",
        "reused_decision_fingerprint",
        "reused_report_fingerprint",
    ):
        value = receipt.get(field)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            errors.append(f"{field} must be a previously validated non-empty sha256 value")
    forbidden = {
        "new_decision_fingerprint",
        "new_report_fingerprint",
        "strategy_decision",
        "portfolio_decision",
        "trade_candidates",
    }
    present = sorted(field for field in forbidden if field in receipt)
    if present:
        errors.append("unavailable fallback cannot synthesize decision fields: " + ", ".join(present))
    baseline_semantics: Mapping[str, Any] | None = None
    if previous_baseline is None:
        errors.append("unavailable receipt requires an actual revision-3 Owner baseline")
    else:
        if require_owner_baseline and (
            previous_baseline.state_path.resolve() != OWNER_STATE_PATH.resolve()
            or previous_baseline.plan_path.resolve() != OWNER_PLAN_PATH.resolve()
        ):
            errors.append("unavailable receipt baseline must use fixed Owner paths")
        try:
            actual_state = _load_text(str(previous_baseline.state_path.resolve()))
            actual_plan = _load_text(str(previous_baseline.plan_path.resolve()))
        except OSError as exc:
            errors.append(f"unavailable receipt baseline cannot be reloaded: {exc}")
            binding = None
        else:
            binding, baseline_errors = _derive_revision3_baseline_binding(
                actual_state, actual_plan
            )
            errors.extend(baseline_errors)
            try:
                baseline_sidecar = extract_projection_sidecar(actual_state)
            except GuardError as exc:
                errors.append(str(exc))
            else:
                candidate_semantics = baseline_sidecar.get("canonical_semantics")
                if isinstance(candidate_semantics, Mapping):
                    baseline_semantics = candidate_semantics
            if (
                previous_baseline.state_text != actual_state
                or previous_baseline.plan_text != actual_plan
                or previous_baseline.binding != binding
            ):
                errors.append("unavailable receipt baseline changed after loading")
        if binding is not None:
            if receipt.get("reused_decision_fingerprint") != binding.get(
                "decision_fingerprint"
            ):
                errors.append("reused_decision_fingerprint must equal actual Owner baseline")
            if receipt.get("reused_report_fingerprint") != binding.get(
                "report_fingerprint"
            ):
                errors.append("reused_report_fingerprint must equal actual Owner baseline")
    actual_fact_fingerprints, fact_errors = _validate_fact_owner_snapshot(
        fact_owner_snapshot, require_owner_paths=require_fact_owner_paths
    )
    errors.extend(fact_errors)
    observation_errors: list[str] = []
    observation_fingerprint = _validate_unavailable_observation(
        observation_input, observation_errors, baseline_semantics,
        actual_fact_fingerprints,
    )
    errors.extend(observation_errors)
    if observation_fingerprint is None:
        errors.append("unavailable receipt requires a valid lightweight observation input")
    elif receipt.get("observation_fingerprint") != observation_fingerprint:
        errors.append("observation_fingerprint must equal actual lightweight observation")
    return errors


def _report_projection_for_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    if _is_expressive_report(bundle.get("report_semantics")):
        from decision_report import render_report
        envelope = render_report(bundle["report_semantics"], bundle)
        if not envelope.get("ok"):
            raise GuardError("; ".join(envelope.get("errors", [])))
        return envelope["validation_projection"]
    projection = dict(report_semantics(bundle))
    projection["rendered_evidence"] = generate_report_witnesses(projection)
    projection["authorization_disclaimer_excerpt"] = REPORT_AUTHORIZATION_DISCLAIMER
    return projection


def _validate_previous_delivery(
    value: Any, path: str, errors: list[str]
) -> None:
    """Validate the isolated, immutable identity of the prior delivery."""

    if not _reject_unknown_keys(value, PREVIOUS_DELIVERY_KEYS, path, errors):
        return
    state = value.get("cloud_delivery_state")
    keys_by_state = {
        "NOT_REQUIRED": {"cloud_delivery_state", "report_visible"},
        "PENDING_RETRY": {
            "cloud_delivery_state", "report_visible", "report_body_fingerprint",
        },
        "DELIVERY_DEGRADED": {
            "cloud_delivery_state", "report_visible", "report_body_fingerprint",
        },
        "DELIVERED": set(PREVIOUS_DELIVERY_KEYS),
    }
    expected_keys = keys_by_state.get(state)
    if expected_keys is None:
        errors.append(f"{path}.cloud_delivery_state is invalid")
        return
    if set(value) != expected_keys:
        errors.append(f"{path} fields must exactly match the {state} branch")
    visible = value.get("report_visible")
    if visible is not (state == "DELIVERED"):
        errors.append(f"{path}.report_visible conflicts with {state}")
    if "report_body_fingerprint" in expected_keys and not SHA256_RE.fullmatch(
        str(value.get("report_body_fingerprint"))
    ):
        errors.append(f"{path}.report_body_fingerprint must be sha256")
    if state == "DELIVERED":
        local_hash = value.get("report_body_fingerprint")
        cloud_hash = value.get("cloud_visible_body_fingerprint")
        if not isinstance(cloud_hash, str) or not SHA256_RE.fullmatch(cloud_hash):
            errors.append(f"{path}.cloud_visible_body_fingerprint must be sha256")
        elif cloud_hash != local_hash:
            errors.append(f"{path} local/cloud report fingerprints must match")
        if not isinstance(value.get("cloud_readback_fingerprint"), str) or not SHA256_RE.fullmatch(
            value["cloud_readback_fingerprint"]
        ):
            errors.append(f"{path}.cloud_readback_fingerprint must be sha256")


def _validate_cloud_readback(
    value: Any, report_text: str | None, errors: list[str]
) -> str | None:
    path = "cloud readback"
    if not _reject_unknown_keys(value, CLOUD_READBACK_KEYS, path, errors):
        return None
    if set(value) != CLOUD_READBACK_KEYS:
        errors.append(f"{path} must contain exactly schema/visible_report_text/assistant_responses")
    if value.get("schema") != CLOUD_READBACK_SCHEMA:
        errors.append(f"{path}.schema must equal {CLOUD_READBACK_SCHEMA}")
    if not isinstance(value.get("visible_report_text"), str):
        errors.append(f"{path}.visible_report_text must be a string")
    elif report_text is not None and value["visible_report_text"] != report_text:
        errors.append("DELIVERED cloud-visible report must exactly equal local report")
    responses = value.get("assistant_responses")
    if responses != ["已收录"]:
        errors.append(
            "DELIVERED cloud assistant responses must be exactly one full response: 已收录"
        )
    if errors:
        return None
    return _fingerprint(value)


def validate_receipt(
    receipt: Mapping[str, Any],
    bundle: Mapping[str, Any] | None = None,
    report_text: str | None = None,
    state_text: str | None = None,
    plan_text: str | None = None,
    cloud_readback: Mapping[str, Any] | None = None,
    previous_baseline: Revision3Baseline | None = None,
    fact_owner_snapshot: FactOwnerSnapshot | None = None,
    unavailable_observation: Mapping[str, Any] | None = None,
    state_artifact_path: str | Path | None = None,
    plan_artifact_path: str | Path | None = None,
    *,
    require_owner_readback: bool = True,
    require_fact_owner_paths: bool = True,
) -> list[str]:
    """Cross-bind a FINAL receipt to actual PRE_WRITE/report/projection artifacts."""

    if not isinstance(receipt, Mapping):
        return ["receipt must be an object"]
    if receipt.get("schema") == UNAVAILABLE_RECEIPT_SCHEMA:
        errors = validate_unavailable_receipt(
            receipt, previous_baseline, unavailable_observation, fact_owner_snapshot,
            require_owner_baseline=require_owner_readback,
            require_fact_owner_paths=require_fact_owner_paths,
        )
        if any(
            value is not None
            for value in (
                bundle, report_text, state_text, plan_text,
                cloud_readback,
            )
        ):
            errors.append("unavailable receipt cannot be mixed with decision artifacts")
        return errors
    errors: list[str] = []
    if bundle is None:
        errors.append("FINAL receipt validation requires --bundle")
        return errors
    migration_baseline = (
        load_immutable_migration_baseline()
        if bundle.get("write_transition") == "MIGRATION"
        else None
    )
    bundle_errors = validate_bundle(
        bundle, migration_baseline, previous_baseline, fact_owner_snapshot,
        require_owner_baseline=False,
        require_fact_owner_paths=require_fact_owner_paths,
    )
    if bundle_errors:
        errors.extend(f"receipt bundle invalid: {error}" for error in bundle_errors)
    try:
        derived = _derive_control_unchecked(bundle, previous_baseline)
        computed_fingerprints = compute_fingerprints(bundle)
        expected_bundle_identity = compute_bundle_identity(bundle)
    except GuardError as exc:
        errors.append(str(exc))
        return errors

    report_required = derived["report_required"]
    patch_required = derived["project_patch_required"]
    delivery_state = receipt.get("cloud_delivery_state")
    common_keys = {
        "schema", "receipt_phase", "bundle_identity", "decision_class", "attention_level",
        "monitor_state", "write_transition", "project_patch_required", "report_required",
        "attention_planned", "local_readback_state", "report_validation_state",
        "cloud_delivery_state", "report_visible", *FINGERPRINT_FIELDS,
    }
    if "previous_delivery" in bundle:
        common_keys.add("previous_delivery")
    expected_keys = set(common_keys)
    if report_required:
        expected_keys.add("report_body_fingerprint")
        if delivery_state == "DELIVERED":
            expected_keys.update(
                {"cloud_visible_body_fingerprint", "cloud_readback_fingerprint"}
            )
    _reject_unknown_keys(receipt, expected_keys, "receipt", errors)
    if set(receipt) != expected_keys:
        errors.append(
            "receipt fields must exactly match the derived silent/pending/delivered branch"
        )

    expected_control = {
        "schema": RUN_RECEIPT_SCHEMA,
        "receipt_phase": "FINAL",
        "bundle_identity": expected_bundle_identity,
        "decision_class": derived["decision_class"],
        "attention_level": derived["attention_level"],
        "monitor_state": derived["monitor_state"],
        "write_transition": derived["write_transition"],
        "project_patch_required": patch_required,
        "report_required": report_required,
        "attention_planned": report_required,
        **computed_fingerprints,
    }
    for field, expected in expected_control.items():
        if receipt.get(field) != expected:
            errors.append(f"receipt.{field} must equal actual bundle value {expected!r}")

    major_risk = bundle.get("major_risk", {})
    expected_attention = (
        "NONE"
        if not report_required
        else "MAJOR_RISK" if major_risk.get("active") is True else "CHANGE_NOTICE"
    )
    if receipt.get("attention_level") != expected_attention:
        errors.append(f"receipt.attention_level must equal {expected_attention}")

    if patch_required:
        if receipt.get("local_readback_state") != "READBACK_VERIFIED":
            errors.append("patched receipt requires local_readback_state=READBACK_VERIFIED")
        if state_text is None or plan_text is None:
            errors.append("READBACK_VERIFIED requires actual --state and --plan")
        else:
            if require_owner_readback:
                if state_artifact_path is None or plan_artifact_path is None:
                    errors.append(
                        "READBACK_VERIFIED library validation requires explicit Owner artifact paths"
                    )
                else:
                    state_path = Path(state_artifact_path).resolve()
                    plan_path = Path(plan_artifact_path).resolve()
                    if (
                        state_path != OWNER_STATE_PATH.resolve()
                        or plan_path != OWNER_PLAN_PATH.resolve()
                    ):
                        errors.append(
                            "FINAL receipt readback must use the fixed state/plan Owner paths"
                        )
                    try:
                        reloaded_state = _load_text(str(state_path))
                        reloaded_plan = _load_text(str(plan_path))
                    except OSError as exc:
                        errors.append(f"FINAL Owner readback cannot be reloaded: {exc}")
                    else:
                        if reloaded_state != state_text or reloaded_plan != plan_text:
                            errors.append(
                                "FINAL Owner readback text changed or differs from loaded artifacts"
                            )
            errors.extend(
                validate_projection_texts(
                    state_text, plan_text, bundle, previous_baseline,
                    fact_owner_snapshot,
                    require_fact_owner_paths=require_fact_owner_paths,
                )
            )
    else:
        if receipt.get("local_readback_state") != "NOT_REQUIRED":
            errors.append("A receipt requires local_readback_state=NOT_REQUIRED")
        if state_text is not None or plan_text is not None:
            errors.append("A receipt must not supply state/plan readback artifacts")

    if report_required:
        if report_text is None:
            errors.append("report-required receipt validation requires --report")
        else:
            projection = _report_projection_for_bundle(bundle)
            errors.extend(validate_report_text(report_text, projection))
            expected_report_hash = content_fingerprint(report_text)
            if receipt.get("report_body_fingerprint") != expected_report_hash:
                errors.append("receipt.report_body_fingerprint must bind actual --report")
        if receipt.get("report_validation_state") != "VALIDATED":
            errors.append("report-required receipt requires report_validation_state=VALIDATED")
        if delivery_state not in {"DELIVERED", "PENDING_RETRY", "DELIVERY_DEGRADED"}:
            errors.append("report receipt has invalid cloud_delivery_state")
        if delivery_state == "DELIVERED":
            body_hash = receipt.get("report_body_fingerprint")
            if cloud_readback is None:
                errors.append(
                    "DELIVERED validation requires actual --cloud-readback"
                )
            else:
                readback_errors: list[str] = []
                readback_fingerprint = _validate_cloud_readback(
                    cloud_readback, report_text, readback_errors
                )
                errors.extend(readback_errors)
                visible_text = cloud_readback.get("visible_report_text") if isinstance(
                    cloud_readback, Mapping
                ) else None
                actual_cloud_hash = (
                    content_fingerprint(visible_text)
                    if isinstance(visible_text, str) else None
                )
                if receipt.get("cloud_visible_body_fingerprint") != actual_cloud_hash:
                    errors.append(
                        "receipt.cloud_visible_body_fingerprint must bind actual cloud-visible report"
                    )
                if receipt.get("cloud_readback_fingerprint") != readback_fingerprint:
                    errors.append(
                        "receipt.cloud_readback_fingerprint must bind actual cloud transcript"
                    )
            if receipt.get("cloud_visible_body_fingerprint") != body_hash:
                errors.append("DELIVERED requires cloud-visible body equal actual report")
            if receipt.get("report_visible") is not True:
                errors.append("DELIVERED requires report_visible=true")
        elif delivery_state in {"PENDING_RETRY", "DELIVERY_DEGRADED"}:
            if receipt.get("report_visible") is not False:
                errors.append(f"{delivery_state} cannot claim report_visible=true")
            if cloud_readback is not None:
                errors.append(f"{delivery_state} must not supply delivered cloud artifacts")
    else:
        expected = {
            "report_validation_state": "NOT_REQUIRED",
            "cloud_delivery_state": "NOT_REQUIRED",
            "report_visible": False,
        }
        for field, value in expected.items():
            if receipt.get(field) != value:
                errors.append(f"silent receipt.{field} must equal {value!r}")
        if report_text is not None:
            errors.append("silent receipt must not supply --report")
        if cloud_readback is not None:
            errors.append("silent receipt must not supply cloud delivery artifacts")
    if "previous_delivery" in bundle:
        previous_delivery = receipt.get("previous_delivery")
        _validate_previous_delivery(
            previous_delivery, "receipt.previous_delivery", errors
        )
        if previous_delivery != bundle.get("previous_delivery"):
            errors.append(
                "receipt.previous_delivery must exactly equal the validated PRE_WRITE history"
            )
    return errors


def accepted_market_assessment(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Project the deep result, never the entrance's competing candidates.

    Call only after full bundle/FINAL verification. The observation store must
    not become a second portfolio, fact, report-body or reasoning repository.
    """
    from decision_report import causal_mechanism_semantics
    from pattern_semantics import ACCEPTED_SCHEMA, MARKET_DIMENSIONS, semantic as pattern_semantic, validate_accepted_market_assessment
    causal = bundle["causal_attribution"]
    selected = next(item for item in causal["hypotheses"] if item["hypothesis_id"] == causal["selected_hypothesis_id"])
    used = {(item["evidence_dimension"], item["evidence_ref"])
            for item in selected["causal_timeline"] if item["evidence_dimension"] in MARKET_DIMENSIONS}
    material = causal.get("material_pattern")
    if isinstance(material, Mapping):
        for item in material["hypotheses"]:
            used.update((ref["dimension"], ref["ref_id"]) for ref in item["support_refs"] + item["counter_refs"])
    evidence = {}
    for dimension in sorted(MARKET_DIMENSIONS):
        item = bundle["evidence_matrix"][dimension]
        sources = {source["source_id"]: source for source in item["sources"]}
        claims = {claim["claim_id"]: claim for claim in item.get("evidence_claims", [])}
        references = []
        for dim, ref_id in sorted(used):
            if dim != dimension:
                continue
            if ref_id in claims:
                claim = claims[ref_id]
                source_ids = claim["source_ids"]
                time_range = claim["time_range"]
                reference_type = "CLAIM"
            elif ref_id in sources:
                source_ids = [ref_id]
                time_range = item["time_range"]
                reference_type = "SOURCE"
            else:
                raise GuardError("accepted market projection reference does not exist in the verified bundle")
            references.append({
                "ref_id": ref_id, "reference_type": reference_type,
                "source_ids": sorted(source_ids),
                "source_urls": sorted({sources[source_id]["endpoint_url"] for source_id in source_ids}),
                "time_range": dict(time_range),
            })
        evidence[dimension] = {"status": item["status"], "references": references}
    result = {
        "schema": ACCEPTED_SCHEMA,
        "causal_semantics": {
            "driver_regime": causal["driver_regime"], "driver_components": sorted(causal["driver_components"]),
            "selected_hypothesis_id": causal["selected_hypothesis_id"],
            "mechanism": causal_mechanism_semantics(bundle),
            "decision_evidence_status": bundle["decision_evidence_status"],
            "next_evidence": sorted(selected["next_discriminating_evidence"]),
            "falsification_conditions": sorted(selected["falsification_conditions"]),
        },
        "material_pattern": pattern_semantic(material), "evidence_refs": evidence,
    }
    errors = validate_accepted_market_assessment(result)
    if errors:
        raise GuardError("invalid accepted market projection: " + "; ".join(errors))
    return result


def record_validated_hourly_completion(
    receipt: Mapping[str, Any], bundle: Mapping[str, Any], kind: str,
    **validation_artifacts: Any,
) -> dict[str, Any]:
    """Advance only from a verified FINAL artifact set; no success boolean.

    This deliberately repeats the read-only contract check at the mutation
    boundary. The SQLite store owns transaction ordering and idempotency.
    DEEP may complete while delivery is pending; REPORT needs actual readback.
    """
    if any(validation_artifacts.get(key, True) is not True for key in ("require_owner_readback", "require_fact_owner_paths")):
        raise GuardError("hourly completion cannot disable actual Owner/fact artifact validation")
    errors = _production_contract_errors(bundle) + validate_receipt(
        receipt, bundle, **validation_artifacts,
    )
    if receipt.get("project_patch_required") is False:
        baseline = validation_artifacts.get("previous_baseline")
        if baseline is None:
            errors.append("silent deep completion requires an actual current Owner baseline")
        else:
            errors.extend(validate_revision3_baseline(bundle, baseline, require_owner_paths=True))
    if kind not in {"DEEP", "REPORT", "BOTH"}:
        errors.append("hourly completion kind must be DEEP, REPORT or BOTH")
    if receipt.get("schema") != RUN_RECEIPT_SCHEMA:
        errors.append("hourly success completion requires the normal FINAL receipt")
    if kind in {"REPORT", "BOTH"} and receipt.get("cloud_delivery_state") != "DELIVERED":
        errors.append("REPORT baseline advances only after validated DELIVERED cloud readback")
    binding = bundle.get("observation", {}).get("hourly_review_binding")
    from pattern_semantics import load_hourly_review
    try:
        bound = load_hourly_review(binding)
        if bound.get("deep_eligible") is not True:
            errors.append("hourly completion requires the persisted deep-review candidate")
    except (ValueError, OSError, ImportError) as exc:
        errors.append(f"hourly completion has no valid bound review: {exc}")
    if errors:
        raise GuardError("; ".join(errors))
    from market_observation import DEFAULT_DB, record_completion
    proof = _fingerprint({
        "schema": "BTC_VALIDATED_HOURLY_COMPLETION_V1",
        "bundle_identity": compute_bundle_identity(bundle), "receipt": receipt,
    })
    accepted = accepted_market_assessment(bundle)
    results = {}
    for selected in (("DEEP", "REPORT") if kind == "BOTH" else (kind,)):
        completion_payload = {"accepted_market_assessment": accepted}
        if selected == "REPORT":
            completion_payload["report_fingerprint"] = compute_fingerprints(bundle)["report_fingerprint"]
        results[selected] = record_completion(
            DEFAULT_DB, selected, dict(binding), proof,
            datetime.now(timezone.utc).isoformat(),
            **completion_payload,
        )
    return {"proof_identity": proof, "completion": results}


def _prewrite_future_fact_paths(value: Any, path: str = "") -> list[str]:
    """Find final delivery facts illegally placed in a PRE_WRITE decision bundle."""

    forbidden = {
        "attention_emitted",
        "cloud_delivery_state",
        "readback",
        "local_readback_state",
        "report_validation_state",
        "report_visible",
        "report_body_fingerprint",
        "cloud_visible_body_fingerprint",
        "cloud_readback_fingerprint",
        "cloud_readback",
        "cloud_receipt_text",
        "no_second_analysis",
    }
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}" if path else key_text
            # Previous delivery is historical audit context, never this run's
            # completion fact, so its receipt-shaped contents are permitted.
            if key_text == "previous_delivery":
                continue
            if key_text in forbidden:
                paths.append(nested_path)
            paths.extend(_prewrite_future_fact_paths(nested, nested_path))
        return paths
    if isinstance(value, list):
        return [
            child
            for index, nested in enumerate(value)
            for child in _prewrite_future_fact_paths(nested, f"{path}[{index}]")
        ]
    return []


def _validate_account_fact_schema(account: Any, errors: list[str]) -> None:
    if not _reject_unknown_keys(account, ACCOUNT_FACT_KEYS, "account_facts", errors):
        return
    allowed_account = {"account_fact_changed_known", "fact_owner_fingerprints"}
    if account.get("account_fact_changed_known") is True or any(
        key in account for key in ("known_change_evidence", "local_record_status")
    ):
        allowed_account.update({"known_change_evidence", "local_record_status"})
    for key in account:
        if key not in allowed_account:
            errors.append(f"account_facts field is not valid for this branch: {key}")
    fingerprints = account.get("fact_owner_fingerprints")
    if _reject_unknown_keys(
        fingerprints, set(FACT_OWNER_PATHS),
        "account_facts.fact_owner_fingerprints", errors,
    ) and set(fingerprints) != set(FACT_OWNER_PATHS):
        errors.append("account_facts.fact_owner_fingerprints must contain exactly all fact Owners")
    if isinstance(fingerprints, Mapping):
        for owner_id, value in fingerprints.items():
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                errors.append(f"account_facts.fact_owner_fingerprints.{owner_id} must be sha256")
    if "known_change_evidence" in account:
        confirmations = account.get("known_change_evidence")
        if not isinstance(confirmations, list) or not confirmations:
            errors.append("account_facts.known_change_evidence must be non-empty")
        else:
            for index, confirmation in enumerate(confirmations):
                path = f"account_facts.known_change_evidence[{index}]"
                if _reject_unknown_keys(confirmation, FACT_CONFIRMATION_KEYS, path, errors):
                    if set(confirmation) != FACT_CONFIRMATION_KEYS:
                        errors.append(f"{path} must contain exactly confirmation_type/owner_id")
                    confirmation_types = (
                        FACT_CONFIRMATION_TYPES if account.get("account_fact_changed_known") is True
                        else {PREFERENCE_CONFIRMATION_TYPE}
                    )
                    if confirmation.get("confirmation_type") not in confirmation_types:
                        errors.append(f"{path}.confirmation_type is invalid")
                    if confirmation.get("owner_id") not in FACT_OWNER_PATHS:
                        errors.append(f"{path}.owner_id is invalid")


def _validate_bundle_schema(bundle: Mapping[str, Any], errors: list[str], *, model_market: bool = False) -> None:
    """Apply recursive exact-key allowlists to every declared object node."""

    _reject_unknown_keys(bundle, BUNDLE_KEYS, "PRE_WRITE bundle", errors)

    observation = bundle.get("observation")
    observed_at = (
        _parse_iso8601(observation.get("observed_at"), "observation.observed_at", [])
        if isinstance(observation, Mapping)
        else None
    )
    if _reject_unknown_keys(observation, OBSERVATION_KEYS, "observation", errors):
        relations = observation.get("quote_relation_by_venue")
        if isinstance(relations, Mapping):
            if any(not isinstance(key, str) or not key.strip() for key in relations):
                errors.append("observation.quote_relation_by_venue keys must be venue names")
            normalized_relation_venues = {
                _source_identity(key) for key in relations
                if _source_identity(key) is not None
            }
            if len(normalized_relation_venues) != len(relations):
                errors.append("observation.quote_relation_by_venue venues must be normalized-unique")
            if any(not isinstance(value, str) for value in relations.values()):
                errors.append("observation.quote_relation_by_venue values must be strings")
        availability = observation.get("evidence_availability_by_dimension")
        if _reject_unknown_keys(
            availability,
            set(REQUIRED_EVIDENCE_DIMENSIONS),
            "observation.evidence_availability_by_dimension",
            errors,
        ) and set(availability) != set(REQUIRED_EVIDENCE_DIMENSIONS):
            errors.append(
                "observation.evidence_availability_by_dimension must contain exactly all dimensions"
            )
        raw_prices = observation.get("raw_prices")
        if isinstance(raw_prices, Mapping):
            if not raw_prices:
                errors.append("observation.raw_prices must not be empty")
            if isinstance(relations, Mapping) and set(raw_prices) != set(relations):
                errors.append("observation.raw_prices venues must equal quote_relation_by_venue")
            normalized_raw_venues = {
                _source_identity(key) for key in raw_prices
                if _source_identity(key) is not None
            }
            if len(normalized_raw_venues) != len(raw_prices):
                errors.append("observation.raw_prices venues must be normalized-unique")
            for venue, price in raw_prices.items():
                try:
                    normalized_price = _decimal(price, f"observation.raw_prices.{venue}")
                    if normalized_price <= 0:
                        errors.append(f"observation.raw_prices.{venue} must be positive")
                except GuardError as exc:
                    errors.append(str(exc))
        else:
            errors.append("observation.raw_prices must be an object")
        _parse_iso8601(observation.get("observed_at"), "observation.observed_at", errors)
        closed = observation.get("closed_bar_relation_sequence")
        if isinstance(closed, list):
            for index, bar in enumerate(closed):
                path = f"observation.closed_bar_relation_sequence[{index}]"
                if _reject_unknown_keys(bar, CLOSED_BAR_KEYS, path, errors):
                    if set(bar) != CLOSED_BAR_KEYS:
                        errors.append(f"{path} must contain exact numeric OHLC and relations")

    matrix = bundle.get("evidence_matrix")
    if _reject_unknown_keys(
        matrix, set(REQUIRED_EVIDENCE_DIMENSIONS), "evidence_matrix", errors
    ):
        seen_claim_ids: set[str] = set()
        if set(matrix) != set(REQUIRED_EVIDENCE_DIMENSIONS):
            errors.append("evidence_matrix must contain exactly all required dimensions")
        for dimension in REQUIRED_EVIDENCE_DIMENSIONS:
            item = matrix.get(dimension)
            item_path = f"evidence_matrix.{dimension}"
            if not _reject_unknown_keys(item, EVIDENCE_KEYS, item_path, errors):
                continue
            allowed_evidence_keys = {
                "status", "sources", "time_range", "supporting", "conflicts", "missing"
            }
            if dimension == "price_quotes":
                allowed_evidence_keys.update({"independence_groups", "access_scope"})
            if item.get("evidence_claims") is not None:
                allowed_evidence_keys.add("evidence_claims")
            for key in item:
                if key not in allowed_evidence_keys:
                    errors.append(f"{item_path} field is not valid for this dimension: {key}")
            _validate_time_range(
                item.get("time_range"), f"{item_path}.time_range", errors,
                not_after=observed_at,
            )
            for list_field in ("supporting", "conflicts", "missing"):
                _validate_string_list(item.get(list_field), f"{item_path}.{list_field}", errors)
            sources = item.get("sources")
            if not isinstance(sources, list):
                errors.append(f"{item_path}.sources must be an array")
                sources = []
            for index, source in enumerate(sources):
                _validate_https_source(
                    source, dimension, index, errors, observed_at=observed_at
                )
                if isinstance(source, Mapping) and isinstance(item.get("time_range"), Mapping):
                    parent_range = item["time_range"]
                    if parent_range.get("kind") != "NOT_ESTABLISHED":
                        comparison_errors: list[str] = []
                        range_end = _parse_iso8601(
                            parent_range.get("end"), "range.end", comparison_errors
                        )
                        source_as_of = _parse_iso8601(
                            source.get("as_of"), "source.as_of", comparison_errors
                        )
                        if (
                            not comparison_errors and range_end is not None
                            and source_as_of is not None and range_end > source_as_of
                        ):
                            errors.append(
                                f"{item_path}.time_range.end cannot exceed sources[{index}].as_of"
                            )
            source_ids = [_source_identity(source) for source in sources]
            if any(source_id is None for source_id in source_ids):
                errors.append(f"{item_path}.sources require source_id")
            elif len(source_ids) != len(set(source_ids)):
                errors.append(f"{item_path}.sources source_id values must be unique")
            if dimension == "cross_exchange_acceptance" and item.get("status") == "VERIFIED":
                origins = {
                    _canonical_source_origin(source) for source in sources
                    if _canonical_source_origin(source) is not None
                }
                venues = {
                    _source_venue(source) for source in sources
                    if _source_venue(source) is not None
                }
                range_kind = (
                    item.get("time_range", {}).get("kind")
                    if isinstance(item.get("time_range"), Mapping)
                    else None
                )
                if len(origins) < 2 or len(venues) < 2:
                    errors.append(
                        f"{item_path} VERIFIED requires at least two independent origins and venues"
                    )
                if range_kind != "MULTI_PERIOD":
                    errors.append(f"{item_path} VERIFIED requires MULTI_PERIOD time_range")
            claims = item.get("evidence_claims")
            if claims is not None:
                if item.get("status") != "VERIFIED":
                    errors.append(
                        f"{item_path}.evidence_claims are allowed only under a VERIFIED parent"
                    )
                if not isinstance(claims, list) or not claims:
                    errors.append(f"{item_path}.evidence_claims must be a non-empty array")
                else:
                    for index, claim in enumerate(claims):
                        claim_path = f"{item_path}.evidence_claims[{index}]"
                        if not _reject_unknown_keys(claim, CLAIM_KEYS, claim_path, errors):
                            continue
                        claim_type = _claim_type(claim)
                        expected_dimension = CLAIM_DIMENSIONS.get(claim_type or "")
                        if expected_dimension != dimension:
                            errors.append(
                                f"{claim_path}.claim_type is not legal for dimension {dimension}"
                            )
                        required_claim_keys = {
                            "claim_id", "claim_type", "status", "source_ids",
                            "time_range", "metric",
                        }
                        spot_directional = claim_type in {
                            "NATIVE_SPOT_FLOW", "NATIVE_SPOT_BUY_FLOW", "NATIVE_SPOT_BUY",
                            "NATIVE_SPOT_SELL_FLOW", "NATIVE_SPOT_SELL",
                        }
                        if spot_directional:
                            required_claim_keys.add("direction")
                        zone_scoped_claim = claim_type in ZONE_SCOPED_CLAIM_TYPES
                        if zone_scoped_claim:
                            required_claim_keys.add("subject_zone_id")
                        if set(claim) != required_claim_keys:
                            errors.append(
                                f"{claim_path} must contain exactly {sorted(required_claim_keys)}"
                            )
                        if claim.get("status") != "VERIFIED":
                            errors.append(f"{claim_path}.status must equal VERIFIED")
                        if not isinstance(claim.get("claim_id"), str) or not claim[
                            "claim_id"
                        ].strip():
                            errors.append(f"{claim_path}.claim_id must be a non-empty string")
                        else:
                            normalized_claim_id = claim["claim_id"].strip().casefold()
                            if normalized_claim_id in seen_claim_ids:
                                errors.append(f"{claim_path}.claim_id must be globally unique")
                            seen_claim_ids.add(normalized_claim_id)
                        if zone_scoped_claim and (
                            not isinstance(claim.get("subject_zone_id"), str)
                            or not claim["subject_zone_id"].strip()
                        ):
                            errors.append(f"{claim_path}.subject_zone_id is required")
                        _validate_string_list(claim.get("source_ids"), f"{claim_path}.source_ids", errors)
                        normalized_claim_sources = {
                            _source_identity(value) for value in claim.get("source_ids", [])
                            if _source_identity(value) is not None
                        } if isinstance(claim.get("source_ids"), list) else set()
                        if normalized_claim_sources != set(source_ids):
                            errors.append(f"{claim_path}.source_ids must equal parent sources")
                        if claim.get("time_range") != item.get("time_range"):
                            errors.append(f"{claim_path}.time_range must equal parent time_range")
                        _validate_time_range(
                            claim.get("time_range"), f"{claim_path}.time_range", errors,
                            not_after=observed_at,
                        )
                        _validate_metric(claim.get("metric"), f"{claim_path}.metric", errors)
                        if spot_directional and claim.get("direction") not in {"BUY", "SELL"}:
                            errors.append(f"{claim_path}.direction must be BUY or SELL")
                        intrinsic_direction = (
                            "BUY" if claim_type in {"NATIVE_SPOT_BUY_FLOW", "NATIVE_SPOT_BUY"}
                            else "SELL" if claim_type in {"NATIVE_SPOT_SELL_FLOW", "NATIVE_SPOT_SELL"}
                            else None
                        )
                        if intrinsic_direction is not None and claim.get("direction") != intrinsic_direction:
                            errors.append(
                                f"{claim_path}.direction conflicts with claim_type {claim_type}"
                            )
                        range_kind = (
                            claim.get("time_range", {}).get("kind")
                            if isinstance(claim.get("time_range"), Mapping)
                            else None
                        )
                        periods = (
                            claim.get("metric", {}).get("periods")
                            if isinstance(claim.get("metric"), Mapping)
                            else None
                        )
                        if range_kind == "MULTI_PERIOD" and (
                            not isinstance(periods, int) or periods < 2
                        ):
                            errors.append(f"{claim_path}.metric.periods must be >=2 for MULTI_PERIOD")
                        if range_kind == "POINT_IN_TIME" and periods != 1:
                            errors.append(f"{claim_path}.metric.periods must equal 1 for POINT_IN_TIME")

    causal = bundle.get("causal_attribution")
    if _reject_unknown_keys(causal, CAUSAL_KEYS, "causal_attribution", errors):
        if set(causal) - {"material_pattern"} != CAUSAL_REQUIRED_KEYS:
            errors.append("causal_attribution must contain exactly all declared fields")
        if "material_pattern" in causal:
            from pattern_semantics import validate_material_pattern
            errors.extend(validate_material_pattern(causal["material_pattern"], bundle, model_market=model_market))
        _validate_string_list(causal.get("driver_components"), "causal_attribution.driver_components", errors)
        _validate_string_list(causal.get("rejected_hypothesis_ids"), "causal_attribution.rejected_hypothesis_ids", errors)
        hypotheses = causal.get("hypotheses")
        if isinstance(hypotheses, list):
            for index, hypothesis in enumerate(hypotheses):
                path = f"causal_attribution.hypotheses[{index}]"
                if _reject_unknown_keys(hypothesis, set(HYPOTHESIS_FIELDS), path, errors):
                    if set(hypothesis) != set(HYPOTHESIS_FIELDS):
                        errors.append(f"{path} must contain exactly all hypothesis fields")
                    for field in (
                        "supporting_evidence", "counter_evidence", "missing_evidence",
                        "falsification_conditions", "next_discriminating_evidence",
                    ):
                        _validate_string_list(hypothesis.get(field), f"{path}.{field}", errors)
                    _validate_causal_timeline(
                        hypothesis.get("causal_timeline"), f"{path}.causal_timeline",
                        matrix, observed_at, errors,
                        require_resolved_sequence=(
                            not model_market and hypothesis.get("hypothesis_id") == causal.get(
                                "selected_hypothesis_id"
                            )
                            and causal.get("driver_regime") != "UNRESOLVED"
                        ),
                    )

    zones = bundle.get("value_zones")
    if isinstance(zones, list):
        for index, zone in enumerate(zones):
            path = f"value_zones[{index}]"
            if _reject_unknown_keys(zone, ZONE_KEYS, path, errors):
                if set(zone) != ZONE_KEYS:
                    errors.append(f"{path} must contain exactly zone_id/role/band/lifecycle")
                _reject_unknown_keys(zone.get("band"), ZONE_BAND_KEYS, f"{path}.band", errors)

    anchor_map = bundle.get("anchor_map_semantics")
    if _reject_unknown_keys(anchor_map, ANCHOR_KEYS, "anchor_map_semantics", errors):
        if set(anchor_map) != ANCHOR_KEYS:
            errors.append("anchor_map_semantics must contain exactly both anchor lists")
        for field in ANCHOR_KEYS:
            _validate_string_list(anchor_map.get(field), f"anchor_map_semantics.{field}", errors)
    forward_band = bundle.get("forward_value_band_semantics")
    if _reject_unknown_keys(
        forward_band, FORWARD_BAND_KEYS,
        "forward_value_band_semantics", errors,
    ):
        if set(forward_band) != FORWARD_BAND_KEYS:
            errors.append("forward_value_band_semantics must contain exactly both fields")
        _validate_string_list(
            forward_band.get("primary_zone_ids"),
            "forward_value_band_semantics.primary_zone_ids", errors,
        )
        if not isinstance(forward_band.get("migration_state"), str) or not forward_band[
            "migration_state"
        ].strip():
            errors.append("forward_value_band_semantics.migration_state must be a string")

    portfolio = bundle.get("portfolio_decision")
    if _reject_unknown_keys(portfolio, set(PORTFOLIO_MODULES), "portfolio_decision", errors):
        if set(portfolio) != set(PORTFOLIO_MODULES):
            errors.append("portfolio_decision must contain exactly five modules")
        for module in PORTFOLIO_MODULES:
            item = portfolio.get(module)
            path = f"portfolio_decision.{module}"
            if _reject_unknown_keys(item, PORTFOLIO_ITEM_KEYS, path, errors):
                allowed = set(PORTFOLIO_ITEM_KEYS)
                try:
                    is_zero_amount = _decimal(item.get("amount_u"), f"{path}.amount_u") == 0
                except GuardError:
                    is_zero_amount = False
                if not is_zero_amount:
                    allowed.discard("zero_amount_reason_class")
                for key in item:
                    if key not in allowed:
                        errors.append(f"{path} field is not valid for this amount: {key}")
                for field in ("formation_conditions", "invalidation_conditions", "evidence_considered"):
                    _validate_string_list(item.get(field), f"{path}.{field}", errors)

    candidates = bundle.get("trade_candidates")
    if isinstance(candidates, list):
        for candidate_index, candidate in enumerate(candidates):
            path = f"trade_candidates[{candidate_index}]"
            if not _reject_unknown_keys(candidate, CANDIDATE_KEYS, path, errors):
                continue
            if set(candidate) != CANDIDATE_KEYS:
                errors.append(f"{path} must contain exactly all candidate fields")
            entries = candidate.get("entries")
            if isinstance(entries, list):
                for entry_index, entry in enumerate(entries):
                    entry_path = f"{path}.entries[{entry_index}]"
                    if not _reject_unknown_keys(entry, ENTRY_KEYS, entry_path, errors):
                        continue
                    allowed_entry_keys = set(ENTRY_KEYS)
                    allowed_entry_keys.discard(
                        "price_band_u" if "price_u" in entry else "price_u"
                    )
                    if entry.get("carried_forward") is not True:
                        allowed_entry_keys.discard("carried_forward")
                    for key in entry:
                        if key not in allowed_entry_keys:
                            errors.append(f"{entry_path} field is not valid in this entry form: {key}")
                    if "price_band_u" in entry:
                        _reject_unknown_keys(
                            entry.get("price_band_u"), ZONE_BAND_KEYS,
                            f"{entry_path}.price_band_u", errors,
                        )
                    _reject_unknown_keys(
                        entry.get("amount_basis"), AMOUNT_BASIS_KEYS,
                        f"{entry_path}.amount_basis", errors,
                    )
                    for field in ("formation_conditions", "invalidation_conditions"):
                        _validate_string_list(entry.get(field), f"{entry_path}.{field}", errors)

    _reject_unknown_keys(bundle.get("major_risk"), MAJOR_RISK_KEYS, "major_risk", errors)
    _validate_account_fact_schema(bundle.get("account_facts"), errors)
    _reject_unknown_keys(bundle.get("event"), EVENT_KEYS, "event", errors)
    _validate_report_semantics_shape(bundle.get("report_semantics"), "report_semantics", errors)
    runtime = bundle.get("runtime")
    if _reject_unknown_keys(runtime, RUNTIME_KEYS, "runtime", errors):
        if set(runtime) != RUNTIME_KEYS:
            errors.append(
                "runtime must contain exactly run_id/state_id/plan_id/"
                "pricing_as_of/trigger_type"
            )
        for field in RUNTIME_KEYS:
            if not isinstance(runtime.get(field), str) or not runtime[field].strip():
                errors.append(f"runtime.{field} must be a non-empty string")
        if runtime.get("trigger_type") not in TRIGGER_TYPES:
            errors.append("runtime.trigger_type is invalid")
        identifiers = {
            "run_id": re.compile(r"BTC-CYCLE-[A-Za-z0-9-]+\Z"),
            "state_id": re.compile(r"BTC-STATE-[A-Za-z0-9-]+\Z"),
            "plan_id": re.compile(r"BTC-[A-Za-z0-9-]+\Z"),
        }
        for field, pattern in identifiers.items():
            if isinstance(runtime.get(field), str) and not pattern.fullmatch(runtime[field]):
                errors.append(f"runtime.{field} has an invalid project identity format")
        pricing_as_of = _parse_project_jst_time(
            runtime.get("pricing_as_of"), "runtime.pricing_as_of", errors
        )
        if observed_at is not None and pricing_as_of is not None:
            pricing_as_of_aware = pricing_as_of.replace(
                tzinfo=timezone(timedelta(hours=9))
            )
            observed_jst = observed_at.astimezone(
                timezone(timedelta(hours=9))
            ).replace(tzinfo=None)
            if observed_jst > pricing_as_of:
                errors.append(
                    "observation.observed_at cannot exceed runtime.pricing_as_of"
                )
            if pricing_as_of_aware > datetime.now(timezone.utc) + MAX_FUTURE_CLOCK_SKEW:
                errors.append("runtime.pricing_as_of cannot be in the future")
    previous_fingerprints = bundle.get("previous_fingerprints")
    if _reject_unknown_keys(
        previous_fingerprints, PREVIOUS_FINGERPRINT_KEYS,
        "previous_fingerprints", errors,
    ) and set(previous_fingerprints) != PREVIOUS_FINGERPRINT_KEYS:
        errors.append("previous_fingerprints must contain exactly decision and report")
    if "previous_projection_baseline" in bundle:
        previous_projection = bundle.get("previous_projection_baseline")
        if _reject_unknown_keys(
            previous_projection, PREVIOUS_PROJECTION_BASELINE_KEYS,
            "previous_projection_baseline", errors,
        ) and set(previous_projection) != PREVIOUS_PROJECTION_BASELINE_KEYS:
            errors.append(
                "previous_projection_baseline must contain exactly the bound revision-3 baseline identity"
            )
    fingerprints = bundle.get("fingerprints")
    if _reject_unknown_keys(fingerprints, set(FINGERPRINT_FIELDS), "fingerprints", errors):
        if set(fingerprints) != set(FINGERPRINT_FIELDS):
            errors.append("fingerprints must contain exactly all three fingerprints")
    control = bundle.get("control")
    if _reject_unknown_keys(control, CONTROL_KEYS, "control", errors):
        if set(control) != CONTROL_KEYS:
            errors.append("control must contain exactly all derived control fields")
    if "previous_delivery" in bundle:
        _validate_previous_delivery(
            bundle.get("previous_delivery"), "previous_delivery", errors
        )
    if "previous_report_semantics" in bundle:
        _validate_report_semantics_shape(
            bundle.get("previous_report_semantics"), "previous_report_semantics", errors
        )


def _validate_bundle_internal(
    bundle: Mapping[str, Any],
    migration_baseline: MigrationBaseline | None = None,
    previous_baseline: Revision3Baseline | None = None,
    fact_owner_snapshot: FactOwnerSnapshot | None = None,
    *,
    require_owner_baseline: bool = True,
    require_fact_owner_paths: bool = True,
    _baseline_semantics_mode: bool = False,
    _event_replay_clock: datetime | None = None,
    _cycle_recheck_at: str | None = None,
    _model_market_assessment: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(bundle, Mapping):
        return ["decision bundle must be an object"]
    if bundle.get("schema") == UNAVAILABLE_RECEIPT_SCHEMA:
        return [
            "unavailable fallback must use validate_unavailable_receipt with an actual rev3 baseline and observation input"
        ]
    if bundle.get("schema") != SCHEMA_VERSION:
        errors.append(f"schema must be {SCHEMA_VERSION}")
    if bundle.get("bundle_phase") != "PRE_WRITE":
        errors.append("bundle_phase must be PRE_WRITE")
    if _is_expressive_report(bundle.get("report_semantics")):
        errors.extend(_production_contract_errors(bundle))
    elif previous_baseline is not None:
        try:
            prior_report = extract_projection_sidecar(previous_baseline.state_text)["canonical_semantics"]["report_semantics"]
        except (GuardError, KeyError, TypeError):
            prior_report = None
        if _is_expressive_report(prior_report):
            errors.append("production baseline cannot downgrade to replay-only report/event contracts")
    _validate_bundle_schema(bundle, errors, model_market=_model_market_assessment)
    _validate_hourly_integration(bundle, previous_baseline, errors, baseline_mode=_baseline_semantics_mode,
                                 allow_pattern_retirement=_cycle_recheck_at is not None)
    for future_path in _prewrite_future_fact_paths(bundle):
        errors.append(f"PRE_WRITE bundle cannot contain final-run fact {future_path}")
    if bundle.get("strategy_revision") != 3:
        errors.append("strategy_revision must equal 3")
    if bundle.get("write_transition") not in {None, "MIGRATION"}:
        errors.append("write_transition may only be MIGRATION or absent")
    runtime = bundle.get("runtime")
    runtime_trigger = (
        runtime.get("trigger_type") if isinstance(runtime, Mapping) else None
    )
    declared_transition = bundle.get("write_transition")
    persisted_control = bundle.get("control")
    trigger_transition = (
        persisted_control.get("write_transition")
        if _baseline_semantics_mode and isinstance(persisted_control, Mapping)
        else declared_transition
    )
    if trigger_transition == "MIGRATION":
        if runtime_trigger != "IMPLEMENTATION_MIGRATION":
            errors.append(
                "MIGRATION requires runtime.trigger_type=IMPLEMENTATION_MIGRATION"
            )
    elif runtime_trigger == "IMPLEMENTATION_MIGRATION":
        errors.append(
            "non-MIGRATION runtime.trigger_type cannot be IMPLEMENTATION_MIGRATION"
        )
    migration_only_fields = {
        "previous_strategy_revision", "previous_report_semantics",
        "previous_revision2_state_fingerprint", "previous_revision2_plan_fingerprint",
        "previous_revision2_report_fingerprint",
        "previous_revision2_report_projection_fingerprint",
    }
    if _baseline_semantics_mode:
        if migration_baseline is not None or previous_baseline is not None:
            errors.append("baseline semantics validation cannot accept another baseline")
        if bundle.get("write_transition") is not None:
            errors.append("baseline canonical semantics cannot contain write_transition")
        stray_migration = sorted(migration_only_fields.intersection(bundle))
        if stray_migration:
            errors.append(
                "baseline canonical semantics cannot contain migration artifacts: "
                + ", ".join(stray_migration)
            )
        if "previous_projection_baseline" in bundle:
            errors.append("baseline canonical semantics cannot contain previous baseline data")
    elif bundle.get("write_transition") == "MIGRATION":
        missing_migration = sorted(migration_only_fields.difference(bundle))
        if missing_migration:
            errors.append(
                "MIGRATION is missing fixed baseline bindings: " + ", ".join(missing_migration)
            )
    else:
        stray_migration = sorted(migration_only_fields.intersection(bundle))
        if stray_migration:
            errors.append(
                "non-MIGRATION bundle cannot contain migration-only fields: "
                + ", ".join(stray_migration)
            )
    if not _baseline_semantics_mode and bundle.get("write_transition") == "MIGRATION" and bundle.get(
        "previous_strategy_revision"
    ) != 2:
        errors.append("MIGRATION requires previous_strategy_revision=2")
    if _baseline_semantics_mode:
        pass
    elif bundle.get("write_transition") == "MIGRATION":
        if migration_baseline is None:
            errors.append(
                "MIGRATION requires CLI-bound Owner state/plan and revision-2 report baseline"
            )
        else:
            errors.extend(validate_revision2_migration_baseline(bundle, migration_baseline))
        if previous_baseline is not None:
            errors.append("MIGRATION must not supply a revision-3 previous baseline")
        if "previous_projection_baseline" in bundle:
            errors.append("MIGRATION cannot contain previous_projection_baseline")
    else:
        if migration_baseline is not None:
            errors.append("non-MIGRATION bundle cannot use a revision-2 baseline")
        if "previous_projection_baseline" not in bundle:
            errors.append("non-MIGRATION requires previous_projection_baseline")
        if previous_baseline is None:
            errors.append(
                "non-MIGRATION requires an actual revision-3 previous state/plan baseline"
            )
        else:
            errors.extend(
                validate_revision3_baseline(
                    bundle, previous_baseline,
                    require_owner_paths=require_owner_baseline,
                    model_market=_model_market_assessment,
                )
            )

    actual_fact_fingerprints: Mapping[str, str] | None = None
    if not _baseline_semantics_mode:
        actual_fact_fingerprints, fact_snapshot_errors = _validate_fact_owner_snapshot(
            fact_owner_snapshot, require_owner_paths=require_fact_owner_paths
        )
        errors.extend(fact_snapshot_errors)

    observation = bundle.get("observation")
    if not isinstance(observation, Mapping):
        errors.append("observation must be an object")
    else:
        _validate_observation_controlled_fields(observation, "observation", errors)
        _validate_closed_bar_sequence(
            observation.get("closed_bar_relation_sequence"),
            "observation.closed_bar_relation_sequence", errors,
            observed_at=observation.get("observed_at"),
        )
        relation = observation.get("price_relation")
        if relation not in ZONE_RELATIONS:
            errors.append("observation.price_relation is invalid")
        for field in OBSERVATION_SEMANTIC_FIELDS:
            if not _nonempty(observation.get(field)):
                errors.append(f"observation.{field} is required for its fingerprint")
        relations = observation.get("quote_relation_by_venue")
        if isinstance(relations, Mapping):
            for venue, venue_relation in relations.items():
                if not _nonempty(venue) or venue_relation not in {
                    "ABOVE_ZONE",
                    "INSIDE_ZONE",
                    "BELOW_ZONE",
                }:
                    errors.append("observation.quote_relation_by_venue is invalid")
            relation_values = set(relations.values())
            expected_relation = (
                next(iter(relation_values))
                if len(relation_values) == 1
                else "CROSS_VENUE_DIVERGENCE"
            )
            if relation_values and relation != expected_relation:
                errors.append(
                    "observation.price_relation conflicts with quote_relation_by_venue"
                )
            expected_divergence = (
                "ACTIVE"
                if expected_relation == "CROSS_VENUE_DIVERGENCE"
                else "NONE"
            )
            if observation.get("cross_venue_divergence_class") != expected_divergence:
                errors.append(
                    "observation.cross_venue_divergence_class conflicts with venue relations"
                )
        else:
            errors.append("observation.quote_relation_by_venue must be an object")
        if observation.get("relation_transition") in LIFECYCLES:
            errors.append("observation transition must not be a value-zone lifecycle")
        if isinstance(relation, str) and relation.startswith("REENTERED"):
            errors.append("REENTERED is a transition, never a price relation")
        transition = observation.get("relation_transition")
        prior_relation = observation.get("prior_price_relation")
        if isinstance(transition, str) and transition.startswith("REENTERED") and prior_relation is None:
            errors.append("REENTERED transition requires a prior_price_relation")
        if prior_relation is not None:
            try:
                expected_transition = relation_transition(prior_relation, relation)
                if transition != expected_transition:
                    errors.append(
                        "observation.relation_transition conflicts with prior/current relation"
                    )
            except GuardError as exc:
                errors.append(str(exc))
        else:
            allowed_current_by_transition = {
                "REENTERED_FROM_BELOW": "INSIDE_ZONE",
                "REENTERED_FROM_ABOVE": "INSIDE_ZONE",
                "EXITED_ABOVE": "ABOVE_ZONE",
                "EXITED_BELOW": "BELOW_ZONE",
                "DIVERGED": "CROSS_VENUE_DIVERGENCE",
                "CONVERGED_ABOVE": "ABOVE_ZONE",
                "CONVERGED_BELOW": "BELOW_ZONE",
                "CONVERGED_INSIDE": "INSIDE_ZONE",
            }
            expected_current = allowed_current_by_transition.get(transition)
            if expected_current is not None and relation != expected_current:
                errors.append(
                    "observation.relation_transition conflicts with current relation"
                )
        closed_bars = observation.get("closed_bar_relation_sequence")
        if closed_bars == "NO_CLOSED_BAR_EVIDENCE":
            pass
        elif not isinstance(closed_bars, list) or not closed_bars:
            errors.append(
                "observation.closed_bar_relation_sequence must be closed-bar records or NO_CLOSED_BAR_EVIDENCE"
            )
        else:
            boundary_relations = {"BELOW_BOUNDARY", "AT_OR_ABOVE_BOUNDARY"}
            for index, bar in enumerate(closed_bars):
                path = f"observation.closed_bar_relation_sequence[{index}]"
                if not isinstance(bar, Mapping):
                    errors.append(f"{path} must be an object")
                    continue
                numeric: dict[str, Decimal] = {}
                for field in ("open", "high", "low", "close", "boundary"):
                    key = f"{field}_u"
                    try:
                        numeric[field] = _decimal(bar.get(key), f"{path}.{key}")
                    except GuardError as exc:
                        errors.append(str(exc))
                if len(numeric) != 5:
                    continue
                if any(value <= 0 for value in numeric.values()):
                    errors.append(f"{path} OHLC and boundary must be positive")
                if numeric["low"] > min(
                    numeric["open"], numeric["close"], numeric["high"]
                ):
                    errors.append(f"{path}.low_u is inconsistent with OHLC")
                if numeric["high"] < max(
                    numeric["open"], numeric["close"], numeric["low"]
                ):
                    errors.append(f"{path}.high_u is inconsistent with OHLC")
                for field in ("open", "high", "low", "close"):
                    relation_field = f"{field}_relation"
                    relation_value = bar.get(relation_field)
                    if relation_value not in boundary_relations:
                        errors.append(f"{path}.{relation_field} is invalid")
                        continue
                    expected = (
                        "BELOW_BOUNDARY"
                        if numeric[field] < numeric["boundary"]
                        else "AT_OR_ABOVE_BOUNDARY"
                    )
                    if relation_value != expected:
                        errors.append(
                            f"{path}.{relation_field} conflicts with numeric OHLC/boundary"
                        )

    if bundle.get("quote_verification_status") not in {
        "VERIFIED",
        "CONFLICTED",
        "UNAVAILABLE",
    }:
        errors.append("quote_verification_status is invalid")

    zones = bundle.get("value_zones")
    seen_zone_ids: set[str] = set()
    if not isinstance(zones, list) or not zones:
        errors.append("value_zones must be a non-empty array")
    else:
        for index, zone in enumerate(zones):
            if not isinstance(zone, Mapping):
                errors.append(f"value_zones[{index}] must be an object")
                continue
            zone_id = zone.get("zone_id")
            if not _nonempty(zone_id):
                errors.append(f"value_zones[{index}].zone_id is required")
            elif not isinstance(zone_id, str):
                errors.append(f"value_zones[{index}].zone_id must be a string")
            elif zone_id in seen_zone_ids:
                errors.append(f"value_zones[{index}].zone_id must be unique")
            if isinstance(zone_id, str):
                seen_zone_ids.add(zone_id)
            if zone.get("lifecycle") not in LIFECYCLES:
                errors.append(
                    f"value_zones[{index}].lifecycle must be one of {sorted(LIFECYCLES)}"
                )
            if zone.get("role") not in ZONE_ROLES:
                errors.append(f"value_zones[{index}].role is invalid")
            if not isinstance(zone.get("band"), Mapping) or not _nonempty(zone.get("band")):
                errors.append(f"value_zones[{index}].band must be structured")
            else:
                try:
                    lower = _decimal(
                        zone["band"].get("lower_u"),
                        f"value_zones[{index}].band.lower_u",
                    )
                    upper = _decimal(
                        zone["band"].get("upper_u"),
                        f"value_zones[{index}].band.upper_u",
                    )
                    if lower <= 0 or upper <= 0:
                        errors.append(f"value_zones[{index}].band bounds must be positive")
                    if lower > upper:
                        errors.append(
                            f"value_zones[{index}].band requires lower_u <= upper_u"
                        )
                except GuardError as exc:
                    errors.append(str(exc))

    anchor_map_value = bundle.get("anchor_map_semantics")
    if isinstance(anchor_map_value, Mapping):
        anchor_sets: dict[str, set[str]] = {}
        for field in ANCHOR_KEYS:
            ids = anchor_map_value.get(field)
            if isinstance(ids, list) and all(isinstance(item, str) for item in ids):
                if len(set(ids)) != len(ids):
                    errors.append(f"anchor_map_semantics.{field} must be unique")
                anchor_sets[field] = set(ids)
                missing_ids = sorted(set(ids).difference(seen_zone_ids))
                if missing_ids:
                    errors.append(
                        f"anchor_map_semantics.{field} references unknown value_zones: "
                        + ", ".join(missing_ids)
                    )
        if len(anchor_sets) == len(ANCHOR_KEYS) and anchor_sets[
            "persistent_anchor_ids"
        ].intersection(anchor_sets["unretested_anchor_ids"]):
            errors.append("persistent and unretested anchor ids must be disjoint")
    forward_value = bundle.get("forward_value_band_semantics")
    if isinstance(forward_value, Mapping):
        forward_ids = forward_value.get("primary_zone_ids")
        if isinstance(forward_ids, list) and all(
            isinstance(item, str) for item in forward_ids
        ):
            if not forward_ids or len(set(forward_ids)) != len(forward_ids):
                errors.append(
                    "forward_value_band_semantics.primary_zone_ids must be non-empty and unique"
                )
            unknown = sorted(set(forward_ids).difference(seen_zone_ids))
            if unknown:
                errors.append(
                    "forward_value_band_semantics.primary_zone_ids references unknown zones: "
                    + ", ".join(unknown)
                )
        migration_state = forward_value.get("migration_state")
        if not isinstance(migration_state, str) or migration_state not in MIGRATION_STATES:
            errors.append("forward_value_band_semantics.migration_state is invalid")

    # Observation relations are computed facts, not caller assertions.  The
    # first persistent anchor is the single primary structure for this run.
    anchor_map_for_observation = bundle.get("anchor_map_semantics")
    persistent_ids = (
        anchor_map_for_observation.get("persistent_anchor_ids")
        if isinstance(anchor_map_for_observation, Mapping)
        else None
    )
    if isinstance(observation, Mapping) and isinstance(zones, list):
        if not isinstance(persistent_ids, list) or not persistent_ids:
            errors.append(
                "anchor_map_semantics.persistent_anchor_ids must identify a primary zone"
            )
        else:
            primary_id = persistent_ids[0]
            matching = [
                zone for zone in zones
                if isinstance(zone, Mapping) and zone.get("zone_id") == primary_id
            ]
            if len(matching) != 1:
                errors.append("primary persistent anchor must match exactly one value_zone")
            else:
                band = matching[0].get("band")
                raw_prices = observation.get("raw_prices")
                relations = observation.get("quote_relation_by_venue")
                if isinstance(band, Mapping) and isinstance(raw_prices, Mapping):
                    try:
                        normalized_observation = classify_zone_observation(
                            raw_prices, band.get("lower_u"), band.get("upper_u"),
                            observation.get("prior_price_relation"),
                        )
                    except GuardError as exc:
                        errors.append(str(exc))
                    else:
                        if relations != normalized_observation["quote_relation_by_venue"]:
                            errors.append(
                                "observation.quote_relation_by_venue conflicts with raw_prices and primary persistent zone"
                            )
                        if observation.get("price_relation") != normalized_observation["price_relation"]:
                            errors.append(
                                "observation.price_relation conflicts with raw_prices and primary persistent zone"
                            )
                        if observation.get("relation_transition") != normalized_observation[
                            "relation_transition"
                        ]:
                            errors.append(
                                "observation.relation_transition conflicts with raw_prices, prior relation, and primary persistent zone"
                            )
                    if isinstance(observation.get("closed_bar_relation_sequence"), list):
                        for index, bar in enumerate(
                            observation["closed_bar_relation_sequence"]
                        ):
                            if not isinstance(bar, Mapping):
                                continue
                            role = bar.get("boundary_role")
                            if role not in {"LOWER", "UPPER"}:
                                continue
                            key = "lower_u" if role == "LOWER" else "upper_u"
                            try:
                                actual_boundary = _decimal(
                                    bar.get("boundary_u"),
                                    f"observation.closed_bar_relation_sequence[{index}].boundary_u",
                                )
                                expected_boundary = _decimal(
                                    band.get(key), f"primary band.{key}"
                                )
                            except GuardError as exc:
                                errors.append(str(exc))
                            else:
                                if actual_boundary != expected_boundary:
                                    errors.append(
                                        "closed-bar boundary must equal the selected primary zone bound"
                                    )

    matrix = bundle.get("evidence_matrix")
    if not isinstance(matrix, Mapping):
        errors.append("evidence_matrix must be an object")
        matrix = {}
    for dimension in REQUIRED_EVIDENCE_DIMENSIONS:
        evidence = matrix.get(dimension) if isinstance(matrix, Mapping) else None
        if not isinstance(evidence, Mapping):
            errors.append(f"evidence_matrix.{dimension} must be an object")
            continue
        status = evidence.get("status")
        if status not in EVIDENCE_STATUSES:
            errors.append(f"evidence_matrix.{dimension}.status is invalid")
        for field in ("sources", "time_range", "supporting", "conflicts", "missing"):
            if field not in evidence:
                errors.append(f"evidence_matrix.{dimension}.{field} is required")
        if not isinstance(evidence.get("sources"), list):
            errors.append(f"evidence_matrix.{dimension}.sources must be an array")
        if status == "VERIFIED" and not _nonempty(evidence.get("sources")):
            errors.append(f"evidence_matrix.{dimension}.sources cannot be empty when VERIFIED")
        if status == "VERIFIED" and dimension != "account_impact":
            sources = evidence.get("sources")
            if not isinstance(sources, list) or not all(
                _structured_public_source(source, dimension) for source in sources
            ):
                errors.append(
                    f"VERIFIED {dimension} requires structured PUBLIC_UNAUTHENTICATED sources"
                )
            time_range = evidence.get("time_range")
            if not isinstance(time_range, Mapping) or time_range.get("kind") == "NOT_ESTABLISHED":
                errors.append(f"VERIFIED {dimension} requires an established structured time_range")
        if status != "VERIFIED" and not (
            _nonempty(evidence.get("missing")) or _nonempty(evidence.get("conflicts"))
        ):
            errors.append(
                f"evidence_matrix.{dimension} must explain non-VERIFIED evidence"
            )
    availability = observation.get("evidence_availability_by_dimension") if isinstance(
        observation, Mapping
    ) else None
    if not isinstance(availability, Mapping):
        errors.append("observation.evidence_availability_by_dimension must be an object")
    else:
        for dimension in REQUIRED_EVIDENCE_DIMENSIONS:
            matrix_item = matrix.get(dimension) if isinstance(matrix, Mapping) else None
            matrix_status = matrix_item.get("status") if isinstance(matrix_item, Mapping) else None
            if availability.get(dimension) != matrix_status:
                errors.append(
                    f"observation evidence availability conflicts with {dimension} matrix status"
                )
    quote_status_map = {
        "VERIFIED": "VERIFIED",
        "CONFLICTED": "CONTRADICTED",
        "UNAVAILABLE": "UNAVAILABLE",
    }
    quote_status = bundle.get("quote_verification_status")
    quote_dimension = matrix.get("price_quotes") if isinstance(matrix, Mapping) else None
    if (
        quote_status in quote_status_map
        and isinstance(quote_dimension, Mapping)
        and quote_dimension.get("status") != quote_status_map[quote_status]
    ):
        errors.append("quote_verification_status conflicts with evidence_matrix.price_quotes")
    if quote_status == "VERIFIED" and isinstance(quote_dimension, Mapping):
        quote_sources = quote_dimension.get("sources")
        if isinstance(quote_sources, list):
            public_source_ids = {
                source_id
                for source in quote_sources
                if (source_id := _source_identity(source)) is not None
                and _source_is_public(source, quote_dimension.get("access_scope"))
            }
            if len(public_source_ids) < 2:
                errors.append(
                    "VERIFIED quotes require at least two deduplicated public sources"
                )
            provenance = [_registered_quote_provenance(source) for source in quote_sources]
            if any(item is None for item in provenance):
                errors.append(
                    "VERIFIED quotes require endpoint_url hosts in the fixed provenance registry"
                )
            origins = {item[0].casefold() for item in provenance if item is not None}
            venues = {item[1].casefold() for item in provenance if item is not None}
            endpoint_urls = {item[2] for item in provenance if item is not None}
            if len(endpoint_urls) != len(quote_sources):
                errors.append("VERIFIED quotes cannot count the same endpoint_url twice")
            if len(origins) < 2:
                errors.append(
                    "VERIFIED quotes require at least two independent canonical origins"
                )
            raw_groups = quote_dimension.get("independence_groups")
            if isinstance(raw_groups, list):
                independence_groups = {
                    group_id
                    for group in raw_groups
                    if (group_id := _source_identity(group)) is not None
                }
            else:
                independence_groups = set()
            if independence_groups != origins:
                errors.append(
                    "VERIFIED quote independence_groups must cross-validate source canonical_origin values"
                )
        relations = observation.get("quote_relation_by_venue") if isinstance(
            observation, Mapping
        ) else None
        if not isinstance(relations, Mapping) or len(relations) < 2:
            errors.append("VERIFIED quotes require relations from at least two venues")
        elif isinstance(quote_sources, list):
            observed_venues = {_source_identity(venue) for venue in relations}
            raw_prices = observation.get("raw_prices") if isinstance(
                observation, Mapping
            ) else None
            raw_price_venues = (
                {_source_identity(venue) for venue in raw_prices}
                if isinstance(raw_prices, Mapping)
                else set()
            )
            if (
                len(venues) < 2
                or venues != observed_venues
                or venues != raw_price_venues
                or len(observed_venues) != len(relations)
                or not isinstance(raw_prices, Mapping)
                or len(raw_price_venues) != len(raw_prices)
                or None in observed_venues
                or None in raw_price_venues
            ):
                errors.append(
                    "VERIFIED quote source, raw-price, and relation venues must match exactly"
                )

    causal = bundle.get("causal_attribution")
    if not isinstance(causal, Mapping):
        errors.append("causal_attribution must be an object")
        causal = {}
    driver = causal.get("driver_regime") if isinstance(causal, Mapping) else None
    allowed_drivers = {
        "CRYPTO_NATIVE_SPOT",
        "MACRO_BETA",
        "DERIVATIVES_LEVERAGE",
        "MIXED",
        "UNRESOLVED",
    }
    if driver not in allowed_drivers:
        errors.append("causal_attribution.driver_regime is invalid")
    decision_evidence_status = bundle.get("decision_evidence_status")
    if decision_evidence_status not in {
        "RESOLVED",
        "EVIDENCE_LIMITED",
        "UNRESOLVED",
    }:
        errors.append("decision_evidence_status is invalid")
    if not _model_market_assessment:
        incomplete_decisive = [
            dimension
            for dimension in DECISIVE_COMPLETENESS_DIMENSIONS
            if not isinstance(matrix.get(dimension), Mapping)
            or matrix[dimension].get("status") != "VERIFIED"
        ]
        if incomplete_decisive and decision_evidence_status == "RESOLVED":
            errors.append(
                "decision_evidence_status cannot be RESOLVED while decisive evidence is unresolved: "
                + ", ".join(incomplete_decisive)
            )
        if incomplete_decisive and driver == "MIXED":
            verified_components = [
                dimension
                for dimension in CAUSAL_CORE_DIMENSIONS
                if isinstance(matrix.get(dimension), Mapping)
                and matrix[dimension].get("status") == "VERIFIED"
            ]
            if len(verified_components) < 2:
                errors.append(
                    "MIXED requires at least two independently VERIFIED causal components"
                )

        driver_requirements = {
            "CRYPTO_NATIVE_SPOT": (
                "spot_flow",
                "cross_exchange_acceptance",
                "capital_source",
            ),
            "DERIVATIVES_LEVERAGE": ("derivatives_flow", "oi_timeline"),
            "MACRO_BETA": ("macro",),
        }
        for dimension in driver_requirements.get(driver, ()):
            item = matrix.get(dimension) if isinstance(matrix, Mapping) else None
            if not isinstance(item, Mapping) or item.get("status") != "VERIFIED":
                errors.append(
                    f"driver_regime={driver} requires VERIFIED {dimension} evidence"
                )
        raw_driver_components = causal.get("driver_components", []) if isinstance(causal, Mapping) else []
        required_driver_components = (
            set(raw_driver_components)
            if driver == "MIXED" and isinstance(raw_driver_components, list)
            else {driver}
        )
        for component in required_driver_components:
            for dimension, claim_types, _ in DRIVER_CAUSAL_EVIDENCE.get(component, ()):
                if not _has_bound_verified_claim(
                    matrix.get(dimension), claim_types, dimension=dimension
                ):
                    errors.append(
                        f"driver_regime={driver} requires bound VERIFIED {component} {dimension} evidence_claim"
                    )
    if driver == "UNRESOLVED" and decision_evidence_status == "RESOLVED":
        errors.append("driver_regime=UNRESOLVED cannot use decision_evidence_status=RESOLVED")

    path = bundle.get("path_decision")
    if not _model_market_assessment and path in {"MOVE_UP", "MOVE_DOWN"}:
        for dimension in (
            "cross_exchange_acceptance",
            "spot_flow",
            "capital_source",
        ):
            item = matrix.get(dimension) if isinstance(matrix, Mapping) else None
            if not isinstance(item, Mapping) or item.get("status") != "VERIFIED":
                errors.append(
                    f"path_decision={path} requires VERIFIED structural {dimension} evidence"
                )
                continue
            time_range = item.get("time_range")
            if not isinstance(time_range, Mapping) or time_range.get("kind") != "MULTI_PERIOD":
                errors.append(
                    f"path_decision={path} requires multi-period {dimension} evidence"
                )
        acceptance = matrix.get("cross_exchange_acceptance")
        for claim_type in (
            "CROSS_PERIOD_ACCEPTANCE",
            "RETENTION",
            "TWO_WAY_EXECUTION",
            "EFFECTIVE_RETEST",
        ):
            if not _has_bound_verified_claim(
                acceptance, {claim_type}, dimension="cross_exchange_acceptance"
            ):
                errors.append(
                    f"path_decision={path} requires VERIFIED evidence_claims.{claim_type}"
                )
        expected_flow = "BUY" if path == "MOVE_UP" else "SELL"
        if not _has_bound_verified_claim(
            matrix.get("spot_flow"),
            {f"NATIVE_SPOT_{expected_flow}_FLOW", f"NATIVE_SPOT_{expected_flow}", "NATIVE_SPOT_FLOW"},
            expected_flow,
            dimension="spot_flow",
        ):
            errors.append(
                f"path_decision={path} requires VERIFIED directional native spot {expected_flow} flow claim"
            )
    if not _model_market_assessment and path == "WIDEN":
        widening = matrix.get("cross_exchange_acceptance") if isinstance(matrix, Mapping) else None
        widening_range = widening.get("time_range") if isinstance(widening, Mapping) else None
        widening_sources = widening.get("sources") if isinstance(widening, Mapping) else None
        origins = {
            _canonical_source_origin(source)
            for source in widening_sources
            if _canonical_source_origin(source) is not None
        } if isinstance(widening_sources, list) else set()
        metric_periods = [
            claim.get("metric", {}).get("periods")
            for claim in widening.get("evidence_claims", [])
            if isinstance(claim, Mapping) and _claim_type(claim) == "DISTRIBUTION_EXPANSION"
        ] if isinstance(widening, Mapping) else []
        if not (
            isinstance(widening, Mapping)
            and widening.get("status") == "VERIFIED"
            and isinstance(widening_range, Mapping)
            and widening_range.get("kind") == "MULTI_PERIOD"
            and len(origins) >= 2
            and any(isinstance(periods, int) and periods >= 2 for periods in metric_periods)
            and _has_bound_verified_claim(
                widening,
                {"DISTRIBUTION_EXPANSION"},
                dimension="cross_exchange_acceptance",
            )
        ):
            errors.append(
                "path_decision=WIDEN requires cross-venue, multi-period structural distribution_expansion evidence"
            )

    components = causal.get("driver_components", []) if isinstance(causal, Mapping) else []
    if driver == "MIXED" and (
        not isinstance(components, list)
        or not all(isinstance(component, str) for component in components)
        or len(set(components)) < 2
    ):
        errors.append("MIXED requires at least two explicit driver_components")
    if driver == "MIXED" and isinstance(components, list) and all(
        isinstance(component, str) for component in components
    ):
        mixed_allowed = {
            "CRYPTO_NATIVE_SPOT",
            "DERIVATIVES_LEVERAGE",
            "MACRO_BETA",
        }
        invalid_components = sorted(
            {component for component in components if component not in mixed_allowed},
            key=str,
        )
        if invalid_components:
            errors.append("MIXED has invalid driver_components")
        for component in ([] if _model_market_assessment else components):
            for dimension in driver_requirements.get(component, ()):
                item = matrix.get(dimension) if isinstance(matrix, Mapping) else None
                if not isinstance(item, Mapping) or item.get("status") != "VERIFIED":
                    errors.append(
                        f"MIXED component {component} requires VERIFIED {dimension} evidence"
                    )
    if driver != "MIXED" and components != []:
        errors.append("non-MIXED driver_regime requires an empty driver_components list")
    hypotheses = causal.get("hypotheses") if isinstance(causal, Mapping) else None
    if not _nonempty(causal.get("reasoning_summary")):
        errors.append("causal_attribution.reasoning_summary is required")
    hypothesis_ids: set[Any] = set()
    if not isinstance(hypotheses, list) or len(hypotheses) < 2:
        errors.append("causal_attribution.hypotheses must contain competing hypotheses")
    else:
        for index, hypothesis in enumerate(hypotheses):
            if not isinstance(hypothesis, Mapping):
                errors.append(f"causal_attribution.hypotheses[{index}] must be an object")
                continue
            for field in HYPOTHESIS_FIELDS:
                if field not in hypothesis:
                    errors.append(
                        f"causal_attribution.hypotheses[{index}].{field} is required"
                    )
            hypothesis_id = hypothesis.get("hypothesis_id")
            if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
                errors.append(
                    f"causal_attribution.hypotheses[{index}].hypothesis_id is empty"
                )
            else:
                if hypothesis_id in hypothesis_ids:
                    errors.append("hypothesis_id values must be unique")
                hypothesis_ids.add(hypothesis_id)
            production = _is_expressive_report(bundle.get("report_semantics"))
            valid_identity = isinstance(hypothesis_id, str) and (
                bool(re.fullmatch(r"H_[A-Z][A-Z0-9_]*", hypothesis_id))
                if production else hypothesis_id in HYPOTHESIS_IDS
            )
            if not valid_identity:
                errors.append(
                    f"causal_attribution.hypotheses[{index}].hypothesis_id is not controlled"
                )
            if not _nonempty(hypothesis.get("thesis")):
                errors.append(
                    f"causal_attribution.hypotheses[{index}].thesis must be non-empty"
                )
            for field in (
                "supporting_evidence",
                "counter_evidence",
                "causal_timeline",
                "missing_evidence",
                "falsification_conditions",
                "next_discriminating_evidence",
            ):
                if field in hypothesis and not isinstance(hypothesis[field], list):
                    errors.append(
                        f"causal_attribution.hypotheses[{index}].{field} must be an array"
                    )
                elif field in hypothesis and not _nonempty(hypothesis[field]):
                    errors.append(
                        f"causal_attribution.hypotheses[{index}].{field} must be non-empty"
                    )
            if not _nonempty(hypothesis.get("falsification_conditions")):
                errors.append(
                    f"causal_attribution.hypotheses[{index}] needs a falsification condition"
                )
            if not _nonempty(hypothesis.get("next_discriminating_evidence")):
                errors.append(
                    f"causal_attribution.hypotheses[{index}] needs next discriminating evidence"
                )
    selected = causal.get("selected_hypothesis_id") if isinstance(causal, Mapping) else None
    if not isinstance(selected, str) or selected not in hypothesis_ids:
        errors.append("selected_hypothesis_id must reference a competing hypothesis")
    expected_selected = {
        "UNRESOLVED": "H_UNRESOLVED",
        "CRYPTO_NATIVE_SPOT": "H_SPOT",
        "DERIVATIVES_LEVERAGE": "H_LEVERAGE",
        "MACRO_BETA": "H_MACRO",
        "MIXED": "H_MIXED",
    }.get(driver)
    production = _is_expressive_report(bundle.get("report_semantics"))
    if not production and expected_selected is not None and selected != expected_selected:
        errors.append("selected_hypothesis_id conflicts with driver_regime")
    if not _model_market_assessment:
        _validate_selected_driver_causal_binding(
            causal, matrix, driver, components, errors
        )
    rejected = causal.get("rejected_hypothesis_ids") if isinstance(causal, Mapping) else None
    if not isinstance(rejected, list) or not rejected:
        errors.append("rejected_hypothesis_ids must be a non-empty controlled list")
    else:
        rejected_are_strings = all(isinstance(item, str) for item in rejected)
        if not rejected_are_strings or any(
            item not in (hypothesis_ids if production else HYPOTHESIS_IDS)
            for item in rejected if isinstance(item, str)
        ):
            errors.append("rejected_hypothesis_ids contains an uncontrolled hypothesis")
        if rejected_are_strings and len(set(rejected)) != len(rejected):
            errors.append("rejected_hypothesis_ids must be unique")
        if rejected_are_strings and selected in rejected:
            errors.append("selected hypothesis cannot also be rejected")
        component_hypotheses = {
            {
                "CRYPTO_NATIVE_SPOT": "H_SPOT",
                "DERIVATIVES_LEVERAGE": "H_LEVERAGE",
                "MACRO_BETA": "H_MACRO",
            }[component]
            for component in (components if isinstance(components, list) else [])
            if component in {
                "CRYPTO_NATIVE_SPOT", "DERIVATIVES_LEVERAGE", "MACRO_BETA"
            }
        } if driver == "MIXED" and not production else set()
        expected_rejected = hypothesis_ids.difference(
            {selected}, component_hypotheses
        )
        if rejected_are_strings and set(rejected) != expected_rejected:
            errors.append(
                "rejected_hypothesis_ids must equal non-selected, non-component hypotheses"
            )
    if bundle.get("path_decision") not in {"MAINTAIN", "MOVE_UP", "MOVE_DOWN", "WIDEN"}:
        errors.append("path_decision is invalid")
    expected_migration_state = {
        "MAINTAIN": "NO_MIGRATION",
        "MOVE_UP": "UPWARD_CONFIRMED",
        "MOVE_DOWN": "DOWNWARD_CONFIRMED",
        "WIDEN": "DISTRIBUTION_WIDENED",
    }.get(bundle.get("path_decision"))
    if isinstance(bundle.get("forward_value_band_semantics"), Mapping) and (
        bundle["forward_value_band_semantics"].get("migration_state")
        != expected_migration_state
    ):
        errors.append("forward value migration_state conflicts with path_decision")
    path_decision = bundle.get("path_decision")
    zone_by_id = {
        zone.get("zone_id"): zone
        for zone in (zones if isinstance(zones, list) else [])
        if isinstance(zone, Mapping) and isinstance(zone.get("zone_id"), str)
    }
    persistent_ids_for_path = (
        anchor_map_value.get("persistent_anchor_ids")
        if isinstance(anchor_map_value, Mapping)
        else None
    )
    unretested_ids_for_path = (
        anchor_map_value.get("unretested_anchor_ids")
        if isinstance(anchor_map_value, Mapping)
        else None
    )
    forward_ids_for_path = (
        forward_value.get("primary_zone_ids")
        if isinstance(forward_value, Mapping)
        else None
    )
    if path_decision == "MOVE_DOWN":
        primary_persistent = (
            persistent_ids_for_path[0]
            if isinstance(persistent_ids_for_path, list) and persistent_ids_for_path
            else None
        )
        primary_zone = zone_by_id.get(primary_persistent)
        if not isinstance(primary_zone, Mapping) or primary_zone.get(
            "lifecycle"
        ) != "INVALIDATED":
            errors.append(
                "MOVE_DOWN path migration requires the primary persistent anchor to be INVALIDATED"
            )
    elif path_decision == "MOVE_UP":
        upward_targets = (
            set(unretested_ids_for_path).intersection(forward_ids_for_path)
            if isinstance(unretested_ids_for_path, list)
            and isinstance(forward_ids_for_path, list)
            and all(isinstance(item, str) for item in unretested_ids_for_path)
            and all(isinstance(item, str) for item in forward_ids_for_path)
            else set()
        )
        if not any(
            isinstance(zone_by_id.get(zone_id), Mapping)
            and zone_by_id[zone_id].get("lifecycle") == "RETEST_CONFIRMED"
            for zone_id in upward_targets
        ):
            errors.append(
                "MOVE_UP path migration requires a forward unretested anchor to be RETEST_CONFIRMED"
            )

    # A lifecycle is persistent decision state.  Every non-migration delta is
    # therefore proven against the actual write-before projection, even when
    # path_decision remains MAINTAIN.
    if (
        not _baseline_semantics_mode
        and bundle.get("write_transition") != "MIGRATION"
        and previous_baseline is not None
    ):
        try:
            previous_sidecar = extract_projection_sidecar(previous_baseline.state_text)
        except GuardError as exc:
            errors.append(f"cannot derive previous lifecycle baseline: {exc}")
        else:
            previous_semantics = previous_sidecar.get("canonical_semantics")
            previous_zones = (
                previous_semantics.get("value_zones")
                if isinstance(previous_semantics, Mapping)
                else None
            )
            previous_lifecycles = {
                zone.get("zone_id"): zone.get("lifecycle")
                for zone in (previous_zones if isinstance(previous_zones, list) else [])
                if isinstance(zone, Mapping) and isinstance(zone.get("zone_id"), str)
            }
            acceptance = matrix.get("cross_exchange_acceptance") if isinstance(
                matrix, Mapping
            ) else None
            removed_zone_ids = sorted(set(previous_lifecycles).difference(zone_by_id))
            if removed_zone_ids:
                errors.append(
                    "previous value_zones cannot disappear; retain them with an explicit "
                    "typed lifecycle: " + ", ".join(removed_zone_ids)
                )
            for zone_id, zone in zone_by_id.items():
                prior_lifecycle = previous_lifecycles.get(zone_id)
                current_lifecycle = zone.get("lifecycle")
                if current_lifecycle == prior_lifecycle:
                    continue
                claim_type = LIFECYCLE_CLAIM_BY_STATE.get(current_lifecycle)
                if claim_type is None or not _has_bound_verified_lifecycle_claim(
                    acceptance, claim_type, zone_id
                ):
                    errors.append(
                        f"value_zone {zone_id} lifecycle {prior_lifecycle}->{current_lifecycle} "
                        "requires a bound typed multi-period structural evidence_claim"
                    )
    for field in ("anchor_map_semantics", "forward_value_band_semantics"):
        if not isinstance(bundle.get(field), Mapping) or not _nonempty(bundle.get(field)):
            errors.append(f"{field} must be a non-empty structured object")

    report_projection = bundle.get("report_semantics")
    if not isinstance(report_projection, Mapping):
        errors.append("report_semantics must be an object")
    else:
        for field in REPORT_SEMANTIC_FIELDS:
            if field not in report_projection or not _nonempty(report_projection.get(field)):
                errors.append(f"report_semantics.{field} is required for its fingerprint")
        if isinstance(bundle.get("major_risk"), Mapping) and not _is_expressive_report(report_projection):
            risk_active = bundle["major_risk"].get("active") is True
            visible_risk = report_projection.get("user_visible_major_risk")
            if risk_active and visible_risk == "NONE":
                errors.append("active major_risk must be user-visible in report_semantics")
            if not risk_active and visible_risk != "NONE":
                errors.append("inactive major_risk requires user_visible_major_risk=NONE")

    portfolio = bundle.get("portfolio_decision")
    if not isinstance(portfolio, Mapping):
        errors.append("portfolio_decision must be an object")
        portfolio = {}
    for module in PORTFOLIO_MODULES:
        decision = portfolio.get(module) if isinstance(portfolio, Mapping) else None
        if not isinstance(decision, Mapping):
            errors.append(f"portfolio_decision.{module} must be an object")
            continue
        for field in (
            "local_fact_basis",
            "portfolio_role",
            "decision",
            "amount_u",
            "reason_class",
            "formation_conditions",
            "invalidation_conditions",
            "net_exposure_impact",
            "capital_pool_impact",
            "evidence_considered",
        ):
            if not _nonempty(decision.get(field)):
                errors.append(f"portfolio_decision.{module}.{field} is required")
        if decision.get("local_fact_basis") != "LOCAL_CONFIRMED_RECORDS":
            errors.append(
                f"portfolio_decision.{module}.local_fact_basis must equal LOCAL_CONFIRMED_RECORDS"
            )
        portfolio_decision = decision.get("decision")
        if not isinstance(portfolio_decision, str) or portfolio_decision not in PORTFOLIO_DECISIONS:
            errors.append(f"portfolio_decision.{module}.decision is invalid")
        elif portfolio_decision not in PORTFOLIO_DECISIONS_BY_MODULE[module]:
            errors.append(
                f"portfolio_decision.{module}.decision is not valid for this portfolio module"
            )
        amount = decision.get("amount_u")
        try:
            normalized_amount = _decimal(amount, f"portfolio_decision.{module}.amount_u")
            if normalized_amount < 0:
                errors.append(f"portfolio_decision.{module}.amount_u must not be negative")
            if normalized_amount == 0 and not _nonempty(
                decision.get("zero_amount_reason_class")
            ):
                errors.append(
                    f"portfolio_decision.{module}.zero_amount_reason_class is required for 0U"
                )
            if portfolio_decision in {
                "MAINTAIN", "REVIEW_AFTER_KNOWN_FACT_CHANGE",
                "FREEZE_INCREMENTAL_RISK", "REDUCE_REVIEW", "EXIT_REVIEW",
                "SAFETY_OVERRIDE",
            } and normalized_amount != 0:
                errors.append(
                    f"portfolio_decision.{module}.{portfolio_decision} requires amount_u=0"
                )
            if portfolio_decision == "CONDITIONAL_ADD":
                if module not in {"new_spot", "new_short"} or normalized_amount <= 0:
                    errors.append(
                        "CONDITIONAL_ADD requires a positive same-module evaluation amount"
                    )
        except GuardError as exc:
            errors.append(str(exc))
        if not isinstance(decision.get("evidence_considered"), list):
            errors.append(f"portfolio_decision.{module}.evidence_considered must be an array")

    candidates = bundle.get("trade_candidates")
    if not isinstance(candidates, list) or not candidates:
        errors.append("trade_candidates must be a non-empty array")
    else:
        seen_entry_ids: set[Any] = set()
        for candidate_index, candidate in enumerate(candidates):
            if not isinstance(candidate, Mapping):
                errors.append(f"trade_candidates[{candidate_index}] must be an object")
                continue
            for field in ("candidate_id", "kind", "eligibility", "entries"):
                if not _nonempty(candidate.get(field)):
                    errors.append(f"trade_candidates[{candidate_index}].{field} is required")
            if candidate.get("kind") not in CANDIDATE_KINDS:
                errors.append(f"trade_candidates[{candidate_index}].kind is invalid")
            if candidate.get("eligibility") not in CANDIDATE_ELIGIBILITIES:
                errors.append(f"trade_candidates[{candidate_index}].eligibility is invalid")
            if not isinstance(candidate.get("report_relevant"), bool):
                errors.append(
                    f"trade_candidates[{candidate_index}].report_relevant must be boolean"
                )
            entries = candidate.get("entries")
            if not isinstance(entries, list) or not entries:
                errors.append(f"trade_candidates[{candidate_index}].entries must be non-empty")
                continue
            for entry_index, entry in enumerate(entries):
                if not isinstance(entry, Mapping):
                    errors.append(
                        f"trade_candidates[{candidate_index}].entries[{entry_index}] must be an object"
                    )
                    continue
                for field in (
                    "entry_id",
                    "amount_u",
                    "amount_basis",
                    "value_role",
                    "eligibility",
                    "formation_conditions",
                    "evidence_confidence",
                    "account_precondition",
                    "invalidation_conditions",
                ):
                    if not _nonempty(entry.get(field)):
                        errors.append(
                            f"trade_candidates[{candidate_index}].entries[{entry_index}].{field} is required"
                        )
                entry_id = entry.get("entry_id")
                if _nonempty(entry_id):
                    if not isinstance(entry_id, str):
                        errors.append("entry_id must be a stable non-empty string")
                    else:
                        normalized_entry_id = entry_id.strip().casefold()
                        if normalized_entry_id in seen_entry_ids:
                            errors.append(f"entry_id must be globally unique: {entry_id}")
                        seen_entry_ids.add(normalized_entry_id)
                if entry.get("eligibility") not in ENTRY_ELIGIBILITIES:
                    errors.append(
                        f"trade_candidates[{candidate_index}].entries[{entry_index}].eligibility is invalid"
                    )
                try:
                    candidate_amount = _decimal(
                        entry.get("amount_u"),
                        f"trade_candidates[{candidate_index}].entries[{entry_index}].amount_u",
                    )
                    if candidate_amount < 0:
                        errors.append(
                            f"trade_candidates[{candidate_index}].entries[{entry_index}].amount_u must not be negative"
                        )
                except GuardError as exc:
                    errors.append(str(exc))
                price_present = _nonempty(entry.get("price_u"))
                band_present = _nonempty(entry.get("price_band_u"))
                if not (price_present or band_present):
                    errors.append(
                        f"trade_candidates[{candidate_index}].entries[{entry_index}] needs price_u or price_band_u"
                    )
                if price_present and band_present:
                    errors.append(
                        f"trade_candidates[{candidate_index}].entries[{entry_index}] must use exactly one of price_u or price_band_u"
                    )
                if price_present:
                    try:
                        price = _decimal(
                            entry.get("price_u"),
                            f"trade_candidates[{candidate_index}].entries[{entry_index}].price_u",
                        )
                        if price <= 0:
                            errors.append(
                                f"trade_candidates[{candidate_index}].entries[{entry_index}].price_u must be positive"
                            )
                    except GuardError as exc:
                        errors.append(str(exc))
                if band_present:
                    band = entry.get("price_band_u")
                    if not isinstance(band, Mapping):
                        errors.append(
                            f"trade_candidates[{candidate_index}].entries[{entry_index}].price_band_u must be an object"
                        )
                    else:
                        try:
                            lower = _decimal(
                                band.get("lower_u"),
                                f"trade_candidates[{candidate_index}].entries[{entry_index}].price_band_u.lower_u",
                            )
                            upper = _decimal(
                                band.get("upper_u"),
                                f"trade_candidates[{candidate_index}].entries[{entry_index}].price_band_u.upper_u",
                            )
                            if lower <= 0 or upper <= 0:
                                errors.append(
                                    f"trade_candidates[{candidate_index}].entries[{entry_index}].price_band_u bounds must be positive"
                                )
                            if lower >= upper:
                                errors.append(
                                    f"trade_candidates[{candidate_index}].entries[{entry_index}].price_band_u requires lower_u < upper_u"
                                )
                        except GuardError as exc:
                            errors.append(str(exc))
                amount_basis = entry.get("amount_basis")
                if not isinstance(amount_basis, Mapping):
                    errors.append(
                        f"trade_candidates[{candidate_index}].entries[{entry_index}].amount_basis must be structured"
                    )
                else:
                    for basis_field in (
                        "budget_source",
                        "net_exposure_constraint",
                        "value_gap_rationale",
                        "risk_assumption",
                    ):
                        if not _nonempty(amount_basis.get(basis_field)):
                            errors.append(
                                f"trade_candidates[{candidate_index}].entries[{entry_index}].amount_basis.{basis_field} is required"
                            )
                    if entry.get("carried_forward") is True and not _nonempty(
                        amount_basis.get("continuation_reason")
                    ):
                        errors.append(
                            f"trade_candidates[{candidate_index}].entries[{entry_index}] carried-forward amount needs continuation_reason"
                        )

            raw_entry_states = [
                entry.get("eligibility") for entry in entries
                if isinstance(entry, Mapping)
            ]
            entry_states = (
                set(raw_entry_states)
                if all(isinstance(state, str) for state in raw_entry_states)
                else set()
            )
            expected_candidate_state = (
                "ELIGIBLE_FOR_REVIEW"
                if "ELIGIBLE_FOR_REVIEW" in entry_states
                else "INVALIDATED"
                if entry_states == {"INVALIDATED"}
                else "CONDITIONAL_INACTIVE"
            )
            if candidate.get("eligibility") != expected_candidate_state:
                errors.append(
                    f"trade_candidates[{candidate_index}].eligibility conflicts with entry eligibility"
                )
            if candidate.get("report_relevant") is True and expected_candidate_state != "ELIGIBLE_FOR_REVIEW":
                errors.append(
                    f"trade_candidates[{candidate_index}].report_relevant requires an eligible entry"
                )
            if expected_candidate_state == "ELIGIBLE_FOR_REVIEW" and candidate.get(
                "report_relevant"
            ) is not True:
                errors.append(
                    f"trade_candidates[{candidate_index}] eligible entries must be report_relevant"
                )

    _validate_candidate_kind_contract(candidates, zones, errors)
    _validate_candidate_eligibility_evidence(
        bundle, candidates, matrix, causal, errors
    )

    # Amounts are decision outputs, not constants.  Direction and the available
    # proposal total are bound to the same module's evidence-qualified entries.
    for module, candidate_kind in (
        ("new_spot", "DISCOUNT_SPOT"), ("new_short", "PREMIUM_SHORT")
    ):
        proposal = portfolio.get(module) if isinstance(portfolio, Mapping) else None
        if not isinstance(proposal, Mapping) or proposal.get("decision") != "CONDITIONAL_ADD":
            continue
        try:
            portfolio_amount = _decimal(
                proposal.get("amount_u"), f"portfolio_decision.{module}.amount_u"
            )
        except GuardError:
            portfolio_amount = None
        eligible_amounts: list[Decimal] = []
        if isinstance(candidates, list):
            for candidate in candidates:
                if not isinstance(candidate, Mapping) or candidate.get(
                    "kind"
                ) != candidate_kind or candidate.get(
                    "eligibility"
                ) != "ELIGIBLE_FOR_REVIEW":
                    continue
                for entry in candidate.get("entries", []):
                    if not isinstance(entry, Mapping) or entry.get(
                        "eligibility"
                    ) != "ELIGIBLE_FOR_REVIEW":
                        continue
                    try:
                        eligible_amounts.append(
                            _decimal(entry.get("amount_u"), "eligible candidate amount_u")
                        )
                    except GuardError:
                        pass
        if portfolio_amount is None or sum(eligible_amounts, Decimal("0")) != portfolio_amount:
            errors.append(
                f"CONDITIONAL_ADD amount must equal same-direction eligible {candidate_kind} candidate amount"
            )
        if bundle.get("decision_evidence_status") != "RESOLVED":
            errors.append("CONDITIONAL_ADD requires RESOLVED decision evidence")
        # _validate_candidate_eligibility_evidence above validates every selected
        # entry's full same-direction causal sequence, including capital/flow.

    major_risk = bundle.get("major_risk")
    if not isinstance(major_risk, Mapping):
        errors.append("major_risk must be an object")
    else:
        if not isinstance(major_risk.get("active"), bool):
            errors.append("major_risk.active must be boolean")
        if not isinstance(major_risk.get("kinds"), list) or not _nonempty(
            major_risk.get("kinds")
        ):
            errors.append("major_risk.kinds must explicitly describe NONE or active risks")
        elif (
            any(
                not isinstance(kind, str)
                or kind not in {"NONE", "DECISIVE_SOURCE_CONFLICT", "SOURCE_CONFLICT"}
                for kind in major_risk["kinds"]
            )
            or len(set(major_risk["kinds"])) != len(major_risk["kinds"])
        ):
            errors.append("major_risk.kinds must be unique controlled risk codes")
        if not _nonempty(major_risk.get("portfolio_effect")):
            errors.append("major_risk.portfolio_effect is required")
        if major_risk.get("active") is False and (
            major_risk.get("kinds") != ["NONE"]
            or major_risk.get("portfolio_effect") != "NONE"
        ):
            errors.append("inactive major_risk must be exactly kinds=[NONE], portfolio_effect=NONE")
        if major_risk.get("active") is True and (
            "NONE" in (
                major_risk.get("kinds")
                if isinstance(major_risk.get("kinds"), list) else []
            )
            or major_risk.get("portfolio_effect") == "NONE"
        ):
            errors.append("active major_risk requires non-NONE kinds and portfolio effect")
        if major_risk.get("active") is True and major_risk.get(
            "portfolio_effect"
        ) != "FREEZE_INCREMENTAL_RISK":
            errors.append("active major_risk must freeze incremental risk")
        decisive_health = (
            isinstance(observation, Mapping)
            and observation.get("source_health_class") == "DECISIVE_SOURCE_CONFLICT"
        )
        decisive_risk = (
            major_risk.get("active") is True
            and isinstance(major_risk.get("kinds"), list)
            and "DECISIVE_SOURCE_CONFLICT" in major_risk["kinds"]
        )
        if decisive_health != decisive_risk:
            errors.append(
                "DECISIVE_SOURCE_CONFLICT source health and major_risk must agree"
            )

    account_changed = _validate_account_fact_transition(
        bundle, previous_baseline, actual_fact_fingerprints, errors,
    )
    if bundle.get("projection_mode") not in {"CURRENT_DERIVATION", "LAST_VALID_ANCHOR"}:
        errors.append("projection_mode is invalid")

    event = bundle.get("event")
    if isinstance(event, Mapping) and ("cycle" in event or "trigger" in event):
        from event_contract import validate_event_contract
        baseline_event = None
        if previous_baseline is not None:
            try:
                baseline_event = extract_projection_sidecar(previous_baseline.state_text)[
                    "canonical_semantics"
                ]["event"]
            except (GuardError, KeyError, TypeError) as exc:
                errors.append(f"cannot bind previous event chain: {exc}")
        event_clock = _event_replay_clock or datetime.now(timezone.utc)
        historical_event = False
        if (_model_market_assessment and _cycle_recheck_at is not None
                and baseline_event == event):
            _parse_iso8601(_cycle_recheck_at, "approved cycle review deadline", errors)
            cycle = event.get("cycle")
            chain = cycle.get("events") if isinstance(cycle, Mapping) else None
            if isinstance(chain, list) and chain and isinstance(chain[-1], Mapping):
                old_due = _parse_iso8601(chain[-1].get("due_at"), "historical event final due_at", errors)
                historical_event = old_due is not None and old_due <= event_clock
        # An unchanged expired carrier is history under the existing v4 cycle
        # review owner. Validate its complete structure and sources, without
        # claiming a missed review ran or inventing a replacement event chain.
        # Changed events and legacy callers retain the actual deadline gate.
        errors.extend(validate_event_contract(
            event,
            pricing_as_of=runtime.get("pricing_as_of") if isinstance(runtime, Mapping) else None,
            observed_at=observation.get("observed_at") if isinstance(observation, Mapping) else None,
            now=event_clock,
            hypothesis_ids=hypothesis_ids,
            baseline_event=None if historical_event else baseline_event,
            migration=trigger_transition == "MIGRATION",
            baseline_mode=_baseline_semantics_mode or historical_event,
            reassessment_deadline=_cycle_recheck_at,
        ))
        if isinstance(event.get("cycle"), Mapping) and isinstance(runtime, Mapping):
            if event["cycle"].get("cycle_id") != runtime.get("run_id"):
                errors.append("event cycle_id must equal the persisted runtime cycle identity")
    if not isinstance(event, Mapping) or not isinstance(
        event.get("final_event_reached") if isinstance(event, Mapping) else None, bool
    ):
        errors.append("event.final_event_reached must be boolean")
    elif isinstance(observation, Mapping):
        normalized_final = observation.get("final_expiry_status") == "COMPLETED"
        if event.get("final_event_reached") != normalized_final:
            errors.append("event final status conflicts with normalized observation")

    required_visible_candidate_entry_ids: set[str] = set()
    if (
        not _baseline_semantics_mode
        and bundle.get("write_transition") != "MIGRATION"
        and previous_baseline is not None
    ):
        try:
            previous_sidecar_for_candidates = extract_projection_sidecar(
                previous_baseline.state_text
            )
        except GuardError as exc:
            errors.append(f"cannot derive previous candidate baseline: {exc}")
        else:
            previous_semantics_for_candidates = previous_sidecar_for_candidates.get(
                "canonical_semantics"
            )
            if isinstance(previous_semantics_for_candidates, Mapping):
                required_visible_candidate_entry_ids = _validate_candidate_delta_visibility(
                    bundle, previous_semantics_for_candidates, errors
                )
            else:
                errors.append("previous projection lacks candidate decision semantics")
    _validate_report_semantics_against_bundle(
        bundle,
        errors,
        required_visible_candidate_entry_ids,
        allow_persisted_candidate_disclosures=_baseline_semantics_mode,
    )

    stored_fingerprints = bundle.get("fingerprints")
    expected_fingerprints: dict[str, str] | None = None
    try:
        expected_fingerprints = compute_fingerprints(bundle)
    except GuardError as exc:
        errors.append(str(exc))
    if not isinstance(stored_fingerprints, Mapping):
        errors.append("fingerprints must be an object")
    else:
        for field in FINGERPRINT_FIELDS:
            value = stored_fingerprints.get(field)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                errors.append(f"fingerprints.{field} must be a non-empty sha256 value")
            elif expected_fingerprints and value != expected_fingerprints[field]:
                errors.append(f"fingerprints.{field} does not match deterministic recomputation")

    previous = bundle.get("previous_fingerprints")
    if not isinstance(previous, Mapping):
        errors.append("previous_fingerprints must be an object")
    else:
        for field in ("decision_fingerprint", "report_fingerprint"):
            value = previous.get(field)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                errors.append(f"previous_fingerprints.{field} must be a sha256 value")
        if bundle.get("write_transition") == "MIGRATION":
            try:
                reprojected_previous_report = previous_report_fingerprint_from_revision2(
                    bundle
                )
            except GuardError as exc:
                errors.append(str(exc))
            else:
                if previous.get("report_fingerprint") != reprojected_previous_report:
                    errors.append(
                        "MIGRATION previous_fingerprints.report_fingerprint must equal the revision-2 report_semantics reprojected fingerprint"
                    )

    derived: Mapping[str, Any] | None
    if _baseline_semantics_mode:
        derived = None
    else:
        try:
            derived = _derive_control_unchecked(bundle, previous_baseline)
        except GuardError as exc:
            errors.append(str(exc))
            derived = None
    if account_changed and derived is not None and not derived["decision_changed"]:
        errors.append(
            "known account fact change must be reflected in decision_fingerprint semantics"
        )
    if account_changed and derived is not None and not derived["report_changed"]:
        errors.append(
            "known account fact change must be reflected in report_fingerprint semantics"
        )
    control = bundle.get("control")
    if not isinstance(control, Mapping):
        errors.append("control must be an object")
    elif _baseline_semantics_mode:
        transition = control.get("write_transition")
        identity_by_transition = {
            "D": ("STRATEGY_SEMANTIC_CHANGE", "D"),
            "E": ("FINAL_EVENT", "E"),
            "MIGRATION": ("REVISION_MIGRATION", "NONE"),
        }
        expected_identity = identity_by_transition.get(transition)
        if expected_identity is None:
            errors.append("persisted baseline control must come from D, E, or MIGRATION")
        else:
            if control.get("decision_class") != expected_identity[0]:
                errors.append("persisted baseline control.decision_class conflicts with transition")
            if control.get("monitor_state") != expected_identity[1]:
                errors.append("persisted baseline control.monitor_state conflicts with transition")
        for field in (
            "project_patch_required", "report_required", "attention_planned",
            "decision_changed", "report_changed",
        ):
            if not isinstance(control.get(field), bool):
                errors.append(f"persisted baseline control.{field} must be boolean")
        if control.get("project_patch_required") is not True:
            errors.append("persisted baseline control.project_patch_required must be true")
        if control.get("attention_planned") is not control.get("report_required"):
            errors.append("persisted baseline attention_planned must equal report_required")
        expected_report_required = bool(
            transition in {"D", "E", "MIGRATION"}
            and control.get("report_changed") is True
        )
        if control.get("report_required") is not expected_report_required:
            errors.append(
                "persisted baseline report_required conflicts with transition/report_changed"
            )
        if transition == "D" and control.get("decision_changed") is not True:
            errors.append("persisted D baseline requires decision_changed=true")
        expected_attention = (
            "NONE"
            if control.get("report_required") is not True
            else "MAJOR_RISK"
            if isinstance(major_risk, Mapping) and major_risk.get("active") is True
            else "CHANGE_NOTICE"
        )
        if control.get("attention_level") != expected_attention:
            errors.append(
                "persisted baseline control.attention_level conflicts with report/risk semantics"
            )
    elif derived is not None:
        for field in (
            "write_transition",
            "decision_class",
            "monitor_state",
            "project_patch_required",
            "report_required",
            "attention_planned",
            "attention_level",
            "decision_changed",
            "report_changed",
        ):
            if control.get(field) != derived[field]:
                errors.append(
                    f"control.{field} must equal derived value {derived[field]!r}"
                )
        if control.get("attention_planned") is not derived["report_required"]:
            errors.append("control.attention_planned must equal report_required")
        control_allowlist = set(derived)
        for field in control:
            if field not in control_allowlist:
                errors.append(f"control field is not allowed: {field}")
    return errors


def validate_bundle(
    bundle: Mapping[str, Any],
    migration_baseline: MigrationBaseline | None = None,
    previous_baseline: Revision3Baseline | None = None,
    fact_owner_snapshot: FactOwnerSnapshot | None = None,
    *,
    require_owner_baseline: bool = True,
    require_fact_owner_paths: bool = True,
) -> list[str]:
    """Validate a caller bundle; previous-baseline validation cannot be bypassed."""

    return _validate_bundle_internal(
        bundle,
        migration_baseline,
        previous_baseline,
        fact_owner_snapshot,
        require_owner_baseline=require_owner_baseline,
        require_fact_owner_paths=require_fact_owner_paths,
        _baseline_semantics_mode=False,
    )


def _strip_markdown_value(value: str) -> str:
    value = value.strip().strip("`").strip()
    return value.strip("*").strip()


def parse_markdown_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    yaml_pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$")
    bullet_pattern = re.compile(
        r"^\s*-\s*(?:\*\*)?([A-Za-z_][A-Za-z0-9_-]*)(?:\*\*)?\s*[：:]\s*(.*?)\s*$"
    )
    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    in_fence = False
    for line in without_comments.splitlines():
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = yaml_pattern.match(line) or bullet_pattern.match(line)
        if match:
            fields[match.group(1)] = _strip_markdown_value(match.group(2))
    return fields


def _markdown_sections(text: str) -> dict[str, str]:
    """Parse real Markdown headings while ignoring comments and fenced blocks."""

    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    sections: dict[str, list[str]] = {}
    current: str | None = None
    in_fence = False
    for raw_line in without_comments.splitlines():
        line = raw_line.rstrip()
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^\s*##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip().casefold()
            sections.setdefault(current, [])
        elif current is not None and line.strip():
            sections[current].append(line.strip())
    return {heading: "\n".join(lines) for heading, lines in sections.items()}


def _validate_revision2_documents(state_text: str, plan_text: str) -> list[str]:
    errors: list[str] = []
    if len(state_text.strip()) < 1500 or len(plan_text.strip()) < 2000:
        errors.append("MIGRATION revision-2 state/plan must be complete Owner documents")
    state = parse_markdown_fields(state_text)
    plan = parse_markdown_fields(plan_text)
    state_required = {
        "strategy_id", "strategy_revision", "state_id", "derived_at",
        "state_recorded_at", "trigger_type", "trigger_fingerprint", "pricing_as_of",
        "account_fact_source", "account_fact_as_of", "derivation_status", "driver_regime",
        "path_decision", "evidence_status", "monitor_state", "attention_level",
        "projection_mode", "user_input_requirement", "project_patch_required",
        "monitor_state_as_of", "projection_plan_id", "projection_fingerprint",
    }
    plan_required = {
        "plan_id", "source_strategy_id", "source_strategy_revision", "source_state_id",
        "state_as_of", "projection_fingerprint", "monitoring_revision",
        "final_expiry_event", "expires_at", "scheduled_task_ref",
    }
    for field in sorted(state_required):
        if not _nonempty(state.get(field)):
            errors.append(f"MIGRATION revision-2 state.{field} is missing")
    for field in sorted(plan_required):
        if not _nonempty(plan.get(field)):
            errors.append(f"MIGRATION revision-2 plan.{field} is missing")
    if state.get("strategy_revision") != "2" or plan.get("source_strategy_revision") != "2":
        errors.append("MIGRATION supplied state/plan must be strategy revision 2")
    comparisons = (
        ("strategy identity", state.get("strategy_id"), plan.get("source_strategy_id")),
        ("state identity", state.get("state_id"), plan.get("source_state_id")),
        ("plan identity", state.get("projection_plan_id"), plan.get("plan_id")),
        ("projection fingerprint", state.get("projection_fingerprint"), plan.get("projection_fingerprint")),
    )
    for label, left, right in comparisons:
        if not left or left != right:
            errors.append(f"MIGRATION revision-2 {label} mismatch")
    state_sections = _markdown_sections(state_text)
    plan_sections = _markdown_sections(plan_text)
    required_state_sections = {
        "本轮边界与数据验证", "当前派生状态",
        "anchor_map 与 value_zone_lifecycle", "本地账户事实与限制",
        "证据状态与下一边界",
    }
    required_plan_sections = {
        "计划元数据与角色边界", "数据验证、归因与策略结论",
        "未来价值路径与切换条件", "溢价空单评估阶梯（仅供用户评估）",
        "折价现货评估阶梯（仅供用户评估）", "持仓复核与账户影响",
        "失效证据与事件链", "scheduled 与云端展示收据",
    }
    for heading in sorted(required_state_sections):
        if not state_sections.get(heading):
            errors.append(f"MIGRATION revision-2 state section is missing/empty: {heading}")
    for heading in sorted(required_plan_sections):
        if not plan_sections.get(heading):
            errors.append(f"MIGRATION revision-2 plan section is missing/empty: {heading}")
    return errors


def _validate_revision2_report_baseline(
    report_text: str, projection: Any
) -> list[str]:
    errors: list[str] = []
    allowed = {
        "schema", "report_semantics", "rendered_evidence",
        "authorization_disclaimer_excerpt",
    }
    if not _reject_unknown_keys(projection, allowed, "revision2_report_baseline", errors):
        return errors
    if set(projection) != allowed:
        errors.append("revision2_report_baseline must contain exactly all baseline fields")
    if projection.get("schema") != REVISION2_REPORT_BASELINE_SCHEMA:
        errors.append(f"revision2_report_baseline.schema must be {REVISION2_REPORT_BASELINE_SCHEMA}")
    semantics = projection.get("report_semantics")
    _validate_report_semantics_shape(semantics, "revision2_report_baseline.report_semantics", errors)
    witnesses = projection.get("rendered_evidence")
    if _reject_unknown_keys(
        witnesses, set(REPORT_SEMANTIC_FIELDS),
        "revision2_report_baseline.rendered_evidence", errors,
    ):
        if set(witnesses) != set(REPORT_SEMANTIC_FIELDS):
            errors.append("revision2 report witnesses must cover exactly eight fields")
        spans: list[tuple[int, int, str]] = []
        excerpts: set[str] = set()
        for field in REPORT_SEMANTIC_FIELDS:
            path = f"revision2_report_baseline.rendered_evidence.{field}"
            witness = witnesses.get(field)
            if not _reject_unknown_keys(
                witness, {"visible_excerpt", "semantic_fingerprint"}, path, errors
            ):
                continue
            if set(witness) != {"visible_excerpt", "semantic_fingerprint"}:
                errors.append(f"{path} requires exactly excerpt and semantic fingerprint")
            excerpt = witness.get("visible_excerpt")
            if not isinstance(excerpt, str) or len(excerpt.strip()) < 8:
                errors.append(f"{path}.visible_excerpt is too short")
                continue
            if excerpt in excerpts:
                errors.append("revision2 report witnesses must be unique")
            excerpts.add(excerpt)
            if isinstance(semantics, Mapping):
                expected = _fingerprint({field: semantics.get(field)})
                if witness.get("semantic_fingerprint") != expected:
                    errors.append(f"{path}.semantic_fingerprint mismatch")
            occurrences = [match.start() for match in re.finditer(re.escape(excerpt), report_text)]
            if len(occurrences) != 1:
                errors.append(f"{path}.visible_excerpt must occur exactly once in report")
            elif occurrences:
                spans.append((occurrences[0], occurrences[0] + len(excerpt), field))
        spans.sort()
        for left, right in zip(spans, spans[1:]):
            if right[0] < left[1]:
                errors.append(f"revision2 report witness overlap: {left[2]}, {right[2]}")
    disclaimer = projection.get("authorization_disclaimer_excerpt")
    if not isinstance(disclaimer, str) or len(disclaimer.strip()) < 8:
        errors.append("revision2 report authorization disclaimer is required")
    elif disclaimer not in report_text:
        errors.append("revision2 report authorization disclaimer is absent")
    return errors


def load_migration_baseline(
    state_path: str, plan_path: str, report_path: str, report_projection_path: str
) -> MigrationBaseline:
    _verify_immutable_revision2_fixtures()
    state = Path(state_path).resolve()
    plan = Path(plan_path).resolve()
    report = Path(report_path).resolve()
    report_projection = Path(report_projection_path).resolve()
    if (
        state != OWNER_STATE_PATH.resolve()
        or plan != OWNER_PLAN_PATH.resolve()
        or report != REVISION2_REPORT_PATH.resolve()
        or report_projection != REVISION2_REPORT_PROJECTION_PATH.resolve()
    ):
        raise GuardError(
            "MIGRATION baseline must use the fixed Owner state/plan and immutable 13:46 report fixtures"
        )
    projection = _load_json(report_projection_path)
    if not isinstance(projection, Mapping):
        raise GuardError("MIGRATION report projection must be an object")
    state_text = _load_text(str(state))
    plan_text = _load_text(str(plan))
    if (
        state_text != _load_text(str(REVISION2_STATE_FIXTURE_PATH))
        or plan_text != _load_text(str(REVISION2_PLAN_FIXTURE_PATH))
    ):
        raise GuardError(
            "MIGRATION Owner state/plan no longer equal the immutable 13:46 revision-2 baseline"
        )
    return MigrationBaseline(
        state_path=state,
        plan_path=plan,
        report_path=report,
        report_projection_path=report_projection,
        state_text=state_text,
        plan_text=plan_text,
        report_text=_load_text(report_path),
        report_projection=projection,
    )


def load_immutable_migration_baseline() -> MigrationBaseline:
    """Load the fixed audit copy after the two Owner files have been migrated."""

    _verify_immutable_revision2_fixtures()
    projection = _load_json(str(REVISION2_REPORT_PROJECTION_PATH))
    if not isinstance(projection, Mapping):
        raise GuardError("immutable revision-2 report projection must be an object")
    return MigrationBaseline(
        state_path=REVISION2_STATE_FIXTURE_PATH,
        plan_path=REVISION2_PLAN_FIXTURE_PATH,
        report_path=REVISION2_REPORT_PATH,
        report_projection_path=REVISION2_REPORT_PROJECTION_PATH,
        state_text=_load_text(str(REVISION2_STATE_FIXTURE_PATH)),
        plan_text=_load_text(str(REVISION2_PLAN_FIXTURE_PATH)),
        report_text=_load_text(str(REVISION2_REPORT_PATH)),
        report_projection=projection,
    )


def _verify_immutable_revision2_fixtures() -> None:
    if len(IMMUTABLE_REVISION2_ARTIFACT_HASHES) != 4:
        raise GuardError("local revision-2 archive is not configured")
    for path, expected in IMMUTABLE_REVISION2_ARTIFACT_HASHES.items():
        try:
            actual = content_fingerprint(_load_text(str(path)))
        except OSError as exc:
            raise GuardError(f"immutable revision-2 artifact is unavailable: {path}") from exc
        if actual != expected:
            raise GuardError(f"immutable revision-2 artifact hash mismatch: {path.name}")


def validate_revision2_migration_baseline(
    bundle: Mapping[str, Any], baseline: MigrationBaseline
) -> list[str]:
    errors: list[str] = []
    try:
        _verify_immutable_revision2_fixtures()
    except GuardError as exc:
        errors.append(str(exc))
    state_path = baseline.state_path.resolve()
    plan_path = baseline.plan_path.resolve()
    allowed_document_pair = (
        (state_path == OWNER_STATE_PATH.resolve() and plan_path == OWNER_PLAN_PATH.resolve())
        or (
            state_path == REVISION2_STATE_FIXTURE_PATH.resolve()
            and plan_path == REVISION2_PLAN_FIXTURE_PATH.resolve()
        )
    )
    if not allowed_document_pair:
        errors.append("MIGRATION baseline state/plan paths are not trusted")
    if (
        baseline.report_path.resolve() != REVISION2_REPORT_PATH.resolve()
        or baseline.report_projection_path.resolve()
        != REVISION2_REPORT_PROJECTION_PATH.resolve()
    ):
        errors.append("MIGRATION report baseline paths are not immutable fixtures")
    fixed_contents = {
        "state": (
            baseline.state_text,
            IMMUTABLE_REVISION2_ARTIFACT_HASHES[REVISION2_STATE_FIXTURE_PATH],
        ),
        "plan": (
            baseline.plan_text,
            IMMUTABLE_REVISION2_ARTIFACT_HASHES[REVISION2_PLAN_FIXTURE_PATH],
        ),
        "report": (
            baseline.report_text,
            IMMUTABLE_REVISION2_ARTIFACT_HASHES[REVISION2_REPORT_PATH],
        ),
    }
    for label, (text_value, expected_hash) in fixed_contents.items():
        if content_fingerprint(text_value) != expected_hash:
            errors.append(f"MIGRATION {label} content is not the immutable 13:46 baseline")
    expected_projection = _load_json(str(REVISION2_REPORT_PROJECTION_PATH))
    if baseline.report_projection != expected_projection:
        errors.append("MIGRATION report projection is not the immutable 13:46 baseline")
    errors.extend(_validate_revision2_documents(baseline.state_text, baseline.plan_text))
    errors.extend(
        _validate_revision2_report_baseline(
            baseline.report_text, baseline.report_projection
        )
    )
    bindings = (
        ("previous_revision2_state_fingerprint", content_fingerprint(baseline.state_text)),
        ("previous_revision2_plan_fingerprint", content_fingerprint(baseline.plan_text)),
        ("previous_revision2_report_fingerprint", content_fingerprint(baseline.report_text)),
        (
            "previous_revision2_report_projection_fingerprint",
            _fingerprint(baseline.report_projection),
        ),
    )
    for field, expected in bindings:
        if bundle.get(field) != expected:
            errors.append(f"MIGRATION {field} must bind the loaded baseline artifact")
    previous = bundle.get("previous_fingerprints")
    expected_decision = previous_decision_fingerprint_from_revision2(baseline)
    if not isinstance(previous, Mapping) or previous.get(
        "decision_fingerprint"
    ) != expected_decision:
        errors.append(
            "MIGRATION previous_fingerprints.decision_fingerprint must bind the immutable revision-2 state/plan baseline"
        )
    if baseline.report_projection.get("report_semantics") != bundle.get(
        "previous_report_semantics"
    ):
        errors.append("MIGRATION previous_report_semantics must equal loaded report baseline")
    return errors


STATE_PROJECTION_METADATA_FIELDS = (
    "strategy_id", "strategy_revision", "state_id", "cycle_id",
    "created_by_transition", "projection_plan_id", "derived_at", "state_recorded_at",
    "trigger_type", "pricing_as_of", "account_fact_source", "account_fact_as_of",
    "derivation_status", "quote_verification_status", "decision_evidence_status",
    "driver_regime", "path_decision", "projection_mode", "user_input_requirement",
    *FINGERPRINT_FIELDS,
)
PLAN_PROJECTION_METADATA_FIELDS = (
    "source_strategy_id", "source_strategy_revision", "source_state_id", "cycle_id",
    "plan_id", "state_as_of", *FINGERPRINT_FIELDS,
)
STATE_PROJECTION_SECTIONS = (
    "observation", "evidence_matrix", "hypothesis_competition", "anchor_map",
    "forward_value_band", "trade_candidates", "portfolio_decision", "event",
)
PLAN_PROJECTION_SECTIONS = (
    "evidence", "hypothesis", "lifecycle", "path", "portfolio", "candidates", "event",
)
PROJECTION_SIDECAR_KEYS = {
    "schema", "document_role", "bundle_identity", "document_body_fingerprint",
    "metadata", "canonical_semantics",
}
PROJECTION_SEMANTICS_KEYS = {
    "strategy_revision", "observation", "quote_verification_status",
    "decision_evidence_status", "evidence_matrix", "causal_attribution",
    "value_zones", "anchor_map_semantics", "path_decision",
    "forward_value_band_semantics", "portfolio_decision", "trade_candidates",
    "major_risk", "account_facts", "user_input_requirement", "projection_mode",
    "event", "runtime", "report_semantics", "fingerprints", "control",
}


def _projection_metadata_occurrences(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    yaml_pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$")
    bullet_pattern = re.compile(
        r"^\s*-\s*(?:\*\*)?([A-Za-z_][A-Za-z0-9_-]*)(?:\*\*)?\s*[：:]\s*(.*?)\s*$"
    )
    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    in_fence = False
    for line in without_comments.splitlines():
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = yaml_pattern.match(line) or bullet_pattern.match(line)
        if match:
            key = match.group(1)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _validate_projection_metadata_values(
    fields: Mapping[str, Any], role: str, text: str, errors: list[str]
) -> None:
    required = (
        STATE_PROJECTION_METADATA_FIELDS
        if role == "STATE"
        else PLAN_PROJECTION_METADATA_FIELDS
    )
    counts = _projection_metadata_occurrences(text)
    for field in required:
        if counts.get(field, 0) != 1:
            errors.append(
                f"{role.lower()}.{field} must occur exactly once in the projection body"
            )
    if role == "STATE":
        if fields.get("strategy_id") != "BTC-CORE":
            errors.append("state.strategy_id must equal BTC-CORE")
        if fields.get("strategy_revision") != "3":
            errors.append("state.strategy_revision must equal 3")
        if fields.get("created_by_transition") not in CREATED_BY_TRANSITIONS:
            errors.append("state.created_by_transition must be D, E, or MIGRATION")
        if fields.get("trigger_type") not in TRIGGER_TYPES:
            errors.append("state.trigger_type is invalid")
        if fields.get("account_fact_source") not in ACCOUNT_FACT_SOURCES:
            errors.append("state.account_fact_source must be LOCAL_CONFIRMED_RECORDS")
        if fields.get("derivation_status") not in DERIVATION_STATUSES:
            errors.append("state.derivation_status must equal READY")
        if fields.get("quote_verification_status") not in {
            "VERIFIED", "CONFLICTED", "UNAVAILABLE",
        }:
            errors.append("state.quote_verification_status is invalid")
        if fields.get("decision_evidence_status") not in {
            "RESOLVED", "EVIDENCE_LIMITED", "UNRESOLVED",
        }:
            errors.append("state.decision_evidence_status is invalid")
        if fields.get("driver_regime") not in {
            "CRYPTO_NATIVE_SPOT", "MACRO_BETA", "DERIVATIVES_LEVERAGE",
            "MIXED", "UNRESOLVED",
        }:
            errors.append("state.driver_regime is invalid")
        if fields.get("path_decision") not in {"MAINTAIN", "MOVE_UP", "MOVE_DOWN", "WIDEN"}:
            errors.append("state.path_decision is invalid")
        if fields.get("projection_mode") not in {"CURRENT_DERIVATION", "LAST_VALID_ANCHOR"}:
            errors.append("state.projection_mode is invalid")
        if fields.get("user_input_requirement") not in {"NONE", "FACT_SYNC"}:
            errors.append("state.user_input_requirement is invalid")
        for field in ("derived_at", "state_recorded_at", "pricing_as_of", "account_fact_as_of"):
            _parse_project_jst_time(fields.get(field), f"state.{field}", errors)
    else:
        if fields.get("source_strategy_id") != "BTC-CORE":
            errors.append("plan.source_strategy_id must equal BTC-CORE")
        if fields.get("source_strategy_revision") != "3":
            errors.append("plan.source_strategy_revision must equal 3")
        _parse_project_jst_time(fields.get("state_as_of"), "plan.state_as_of", errors)


def _projection_semantics(bundle: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "strategy_revision": bundle.get("strategy_revision"),
        "observation": bundle.get("observation"),
        "quote_verification_status": bundle.get("quote_verification_status"),
        "decision_evidence_status": bundle.get("decision_evidence_status"),
        "evidence_matrix": bundle.get("evidence_matrix"),
        "causal_attribution": bundle.get("causal_attribution"),
        "value_zones": bundle.get("value_zones"),
        "anchor_map_semantics": bundle.get("anchor_map_semantics"),
        "path_decision": bundle.get("path_decision"),
        "forward_value_band_semantics": bundle.get("forward_value_band_semantics"),
        "portfolio_decision": bundle.get("portfolio_decision"),
        "trade_candidates": bundle.get("trade_candidates"),
        "major_risk": bundle.get("major_risk"),
        "account_facts": bundle.get("account_facts"),
        "user_input_requirement": bundle.get("user_input_requirement"),
        "projection_mode": bundle.get("projection_mode"),
        "event": bundle.get("event"),
        "runtime": bundle.get("runtime"),
        # Persist the reviewed narrative as well as typed semantics. Only the
        # report fingerprint strips wording; the exact Owner must remain usable
        # as the next run's full baseline and receipt/report evidence.
        "report_semantics": bundle.get("report_semantics"),
        "fingerprints": compute_fingerprints(bundle),
        "control": bundle.get("control"),
    }


def _projection_metadata_for_bundle(
    bundle: Mapping[str, Any], role: str
) -> dict[str, str]:
    if role not in {"STATE", "PLAN"}:
        raise GuardError("projection role must be STATE or PLAN")
    runtime = bundle.get("runtime")
    control = bundle.get("control")
    causal = bundle.get("causal_attribution")
    if not isinstance(runtime, Mapping) or not isinstance(control, Mapping) or not isinstance(
        causal, Mapping
    ):
        raise GuardError("projection renderer requires structured runtime/control/causal data")
    pricing_as_of = str(runtime.get("pricing_as_of", ""))
    trigger_type = runtime.get("trigger_type")
    transition = str(control.get("write_transition", ""))
    if trigger_type not in TRIGGER_TYPES:
        raise GuardError("projection renderer requires a valid runtime.trigger_type")
    if transition == "MIGRATION" and trigger_type != "IMPLEMENTATION_MIGRATION":
        raise GuardError(
            "MIGRATION projection requires runtime.trigger_type=IMPLEMENTATION_MIGRATION"
        )
    if transition != "MIGRATION" and trigger_type == "IMPLEMENTATION_MIGRATION":
        raise GuardError(
            "non-MIGRATION projection cannot use IMPLEMENTATION_MIGRATION trigger_type"
        )
    fingerprints = compute_fingerprints(bundle)
    if role == "STATE":
        return {
            "strategy_id": "BTC-CORE",
            "strategy_revision": "3",
            "state_id": str(runtime.get("state_id", "")),
            "cycle_id": str(runtime.get("run_id", "")),
            "created_by_transition": transition,
            "projection_plan_id": str(runtime.get("plan_id", "")),
            "derived_at": pricing_as_of,
            "state_recorded_at": pricing_as_of,
            "trigger_type": str(trigger_type),
            "pricing_as_of": pricing_as_of,
            "account_fact_source": "LOCAL_CONFIRMED_RECORDS",
            "account_fact_as_of": pricing_as_of,
            "derivation_status": "READY",
            "quote_verification_status": str(bundle.get("quote_verification_status", "")),
            "decision_evidence_status": str(bundle.get("decision_evidence_status", "")),
            "driver_regime": str(causal.get("driver_regime", "")),
            "path_decision": str(bundle.get("path_decision", "")),
            "projection_mode": str(bundle.get("projection_mode", "")),
            "user_input_requirement": str(bundle.get("user_input_requirement", "")),
            **fingerprints,
        }
    return {
        "source_strategy_id": "BTC-CORE",
        "source_strategy_revision": "3",
        "source_state_id": str(runtime.get("state_id", "")),
        "cycle_id": str(runtime.get("run_id", "")),
        "plan_id": str(runtime.get("plan_id", "")),
        "state_as_of": pricing_as_of,
        **fingerprints,
    }


def _projection_section_value(
    bundle: Mapping[str, Any], role: str, section: str
) -> Any:
    causal = bundle.get("causal_attribution")
    if not isinstance(causal, Mapping):
        causal = {}
    by_role = {
        "STATE": {
            "observation": bundle.get("observation"),
            "evidence_matrix": bundle.get("evidence_matrix"),
            "hypothesis_competition": causal,
            "anchor_map": {
                "value_zones": bundle.get("value_zones"),
                "anchor_map_semantics": bundle.get("anchor_map_semantics"),
            },
            "forward_value_band": {
                "path_decision": bundle.get("path_decision"),
                "forward_value_band_semantics": bundle.get(
                    "forward_value_band_semantics"
                ),
            },
            "trade_candidates": bundle.get("trade_candidates"),
            "portfolio_decision": bundle.get("portfolio_decision"),
            "event": {
                "event": bundle.get("event"),
                "major_risk": bundle.get("major_risk"),
                "account_facts": bundle.get("account_facts"),
                "user_input_requirement": bundle.get("user_input_requirement"),
            },
        },
        "PLAN": {
            "evidence": bundle.get("evidence_matrix"),
            "hypothesis": causal,
            "lifecycle": bundle.get("value_zones"),
            "path": {
                "anchor_map_semantics": bundle.get("anchor_map_semantics"),
                "path_decision": bundle.get("path_decision"),
                "forward_value_band_semantics": bundle.get(
                    "forward_value_band_semantics"
                ),
            },
            "portfolio": bundle.get("portfolio_decision"),
            "candidates": bundle.get("trade_candidates"),
            "event": {
                "event": bundle.get("event"),
                "major_risk": bundle.get("major_risk"),
                "projection_mode": bundle.get("projection_mode"),
            },
        },
    }
    return by_role[role][section]


def render_projection_body(bundle: Mapping[str, Any], role: str) -> str:
    """Render the only permitted Owner Markdown body for a validated bundle."""

    metadata = _projection_metadata_for_bundle(bundle, role)
    sections = STATE_PROJECTION_SECTIONS if role == "STATE" else PLAN_PROJECTION_SECTIONS
    lines = ["---"] if role == "STATE" else ["# plan"]
    for field in (
        STATE_PROJECTION_METADATA_FIELDS if role == "STATE" else PLAN_PROJECTION_METADATA_FIELDS
    ):
        value = metadata[field]
        if role == "STATE":
            lines.append(f"{field}: {value}")
        else:
            lines.append(f"- **{field}**：{value}")
    if role == "STATE":
        lines.append("---")
    for section in sections:
        payload = json.dumps(
            _projection_section_value(bundle, role, section), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        )
        lines.extend((f"## {section}", f"- canonical: {payload}"))
    return "\n".join(lines) + "\n"


def render_projection_document(bundle: Mapping[str, Any], role: str) -> str:
    body = render_projection_body(bundle, role)
    return body + render_projection_sidecar(bundle, body, role) + "\n"


def _projection_metadata(text: str, role: str) -> dict[str, str]:
    fields = parse_markdown_fields(text)
    required = (
        STATE_PROJECTION_METADATA_FIELDS if role == "STATE" else PLAN_PROJECTION_METADATA_FIELDS
    )
    return {field: fields.get(field, "") for field in required}


def build_projection_sidecar(
    bundle: Mapping[str, Any], document_text: str, role: str
) -> dict[str, Any]:
    if role not in {"STATE", "PLAN"}:
        raise GuardError("projection sidecar role must be STATE or PLAN")
    body_text = _remove_projection_sidecar(document_text)
    semantics = _projection_semantics(bundle)
    return {
        "schema": PROJECTION_SIDECAR_SCHEMA,
        "document_role": role,
        # Projection identity is intentionally recomputable from the embedded
        # canonical semantics.  The FINAL receipt separately binds the full
        # PRE_WRITE bundle, including its previous-baseline context.
        "bundle_identity": _fingerprint(
            {"schema": PROJECTION_SIDECAR_SCHEMA, "canonical_semantics": semantics}
        ),
        "document_body_fingerprint": content_fingerprint(body_text),
        "metadata": _projection_metadata(body_text, role),
        "canonical_semantics": semantics,
    }


def render_projection_sidecar(
    bundle: Mapping[str, Any], document_text: str, role: str
) -> str:
    sidecar = build_projection_sidecar(bundle, document_text, role)
    payload = json.dumps(sidecar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{PROJECTION_SIDECAR_START}\n{payload}\n{PROJECTION_SIDECAR_END}"


def extract_projection_sidecar(document_text: str) -> Mapping[str, Any]:
    pattern = re.compile(
        re.escape(PROJECTION_SIDECAR_START) + r"\n([^\r\n]*)\n"
        + re.escape(PROJECTION_SIDECAR_END),
    )
    matches = pattern.findall(document_text)
    if len(matches) != 1:
        raise GuardError("projection document must contain exactly one canonical embedded sidecar")
    try:
        parsed = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise GuardError(f"projection embedded sidecar is invalid JSON: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise GuardError("projection embedded sidecar must be an object")
    return parsed


def _remove_projection_sidecar(document_text: str) -> str:
    start = document_text.find(PROJECTION_SIDECAR_START)
    if start < 0:
        return document_text
    end = document_text.find(PROJECTION_SIDECAR_END, start)
    if end < 0:
        return document_text
    suffix = document_text[end + len(PROJECTION_SIDECAR_END):]
    if suffix not in {"", "\n"}:
        return document_text
    return document_text[:start]


def _derive_revision3_baseline_binding(
    state_text: str, plan_text: str, *, model_market: bool = False
) -> tuple[dict[str, str] | None, list[str]]:
    """Derive the old decision/report identity from two actual rev3 projections."""

    errors: list[str] = []
    documents = {"STATE": state_text, "PLAN": plan_text}
    sidecars: dict[str, Mapping[str, Any]] = {}
    metadata_by_role: dict[str, Mapping[str, Any]] = {}
    semantics_by_role: dict[str, Mapping[str, Any]] = {}
    for role, text_value in documents.items():
        try:
            sidecar = extract_projection_sidecar(text_value)
        except GuardError as exc:
            errors.append(f"revision3 baseline {role.lower()}: {exc}")
            continue
        sidecars[role] = sidecar
        path = f"revision3 baseline {role.lower()} sidecar"
        if not _reject_unknown_keys(sidecar, PROJECTION_SIDECAR_KEYS, path, errors):
            continue
        if set(sidecar) != PROJECTION_SIDECAR_KEYS:
            errors.append(f"{path} must contain exactly all canonical fields")
        if sidecar.get("schema") != PROJECTION_SIDECAR_SCHEMA:
            errors.append(f"{path}.schema is invalid")
        if sidecar.get("document_role") != role:
            errors.append(f"{path}.document_role must equal {role}")
        if not isinstance(sidecar.get("bundle_identity"), str) or not SHA256_RE.fullmatch(
            str(sidecar.get("bundle_identity"))
        ):
            errors.append(f"{path}.bundle_identity must be sha256")
        body = _remove_projection_sidecar(text_value)
        if sidecar.get("document_body_fingerprint") != content_fingerprint(body):
            errors.append(f"{path}.document_body_fingerprint does not bind actual body")
        expected_metadata = _projection_metadata(body, role)
        metadata = sidecar.get("metadata")
        allowed_metadata = set(
            STATE_PROJECTION_METADATA_FIELDS
            if role == "STATE"
            else PLAN_PROJECTION_METADATA_FIELDS
        )
        if _reject_unknown_keys(metadata, allowed_metadata, f"{path}.metadata", errors):
            if set(metadata) != allowed_metadata:
                errors.append(f"{path}.metadata must contain exactly the role fields")
            if metadata != expected_metadata:
                errors.append(f"{path}.metadata does not equal actual Markdown metadata")
            metadata_by_role[role] = metadata
            _validate_projection_metadata_values(metadata, role, body, errors)
        semantics = sidecar.get("canonical_semantics")
        if _reject_unknown_keys(
            semantics, PROJECTION_SEMANTICS_KEYS,
            f"{path}.canonical_semantics", errors,
        ):
            if set(semantics) != PROJECTION_SEMANTICS_KEYS:
                errors.append(f"{path}.canonical_semantics must contain exactly all fields")
            else:
                semantics_by_role[role] = semantics
                expected_projection_identity = _fingerprint(
                    {
                        "schema": PROJECTION_SIDECAR_SCHEMA,
                        "canonical_semantics": semantics,
                    }
                )
                if sidecar.get("bundle_identity") != expected_projection_identity:
                    errors.append(
                        f"{path}.bundle_identity does not bind canonical_semantics"
                    )

    if set(sidecars) == {"STATE", "PLAN"}:
        if sidecars["STATE"].get("bundle_identity") != sidecars["PLAN"].get(
            "bundle_identity"
        ):
            errors.append("revision3 baseline state/plan bundle_identity mismatch")
    if set(semantics_by_role) == {"STATE", "PLAN"}:
        if semantics_by_role["STATE"] != semantics_by_role["PLAN"]:
            errors.append("revision3 baseline state/plan canonical_semantics mismatch")
        semantics = semantics_by_role["STATE"]
        synthetic = {
            "schema": SCHEMA_VERSION,
            "bundle_phase": "PRE_WRITE",
            **semantics,
            "previous_fingerprints": {
                "decision_fingerprint": (
                    semantics.get("fingerprints", {}).get("decision_fingerprint")
                    if isinstance(semantics.get("fingerprints"), Mapping) else None
                ),
                "report_fingerprint": (
                    semantics.get("fingerprints", {}).get("report_fingerprint")
                    if isinstance(semantics.get("fingerprints"), Mapping) else None
                ),
            },
        }
        errors.extend(
            _validate_bundle_internal(
                synthetic,
                require_owner_baseline=False,
                require_fact_owner_paths=False,
                _baseline_semantics_mode=True,
                _model_market_assessment=model_market,
            )
        )
        try:
            expected_documents = {
                "STATE": render_projection_document(synthetic, "STATE"),
                "PLAN": render_projection_document(synthetic, "PLAN"),
            }
        except (GuardError, TypeError, ValueError) as exc:
            errors.append(f"revision3 baseline deterministic render failed: {exc}")
        else:
            for role, text_value in documents.items():
                if text_value != expected_documents[role]:
                    errors.append(
                        f"revision3 baseline {role.lower()} is not the deterministic projection document"
                    )
        if semantics.get("strategy_revision") != 3:
            errors.append("revision3 baseline canonical strategy_revision must equal 3")
        try:
            recomputed = compute_fingerprints(semantics)
        except GuardError as exc:
            errors.append(str(exc))
            recomputed = None
        stored = semantics.get("fingerprints")
        if not isinstance(stored, Mapping) or recomputed != stored:
            errors.append("revision3 baseline embedded fingerprints do not recompute")
        if recomputed is not None and set(metadata_by_role) == {"STATE", "PLAN"}:
            for role, metadata in metadata_by_role.items():
                for field in FINGERPRINT_FIELDS:
                    if metadata.get(field) != recomputed[field]:
                        errors.append(
                            f"revision3 baseline {role.lower()}.{field} does not bind semantics"
                        )
        if set(metadata_by_role) == {"STATE", "PLAN"}:
            state_meta = metadata_by_role["STATE"]
            plan_meta = metadata_by_role["PLAN"]
            comparisons = (
                ("strategy identity", state_meta.get("strategy_id"), plan_meta.get("source_strategy_id")),
                ("strategy revision", state_meta.get("strategy_revision"), plan_meta.get("source_strategy_revision")),
                ("state identity", state_meta.get("state_id"), plan_meta.get("source_state_id")),
                ("plan identity", state_meta.get("projection_plan_id"), plan_meta.get("plan_id")),
                ("cycle identity", state_meta.get("cycle_id"), plan_meta.get("cycle_id")),
            )
            for label, left, right in comparisons:
                if not left or left != right:
                    errors.append(f"revision3 baseline state/plan {label} mismatch")
            runtime = semantics.get("runtime")
            control = semantics.get("control")
            if isinstance(runtime, Mapping):
                runtime_bindings = (
                    ("state_id", state_meta.get("state_id"), runtime.get("state_id")),
                    ("plan_id", plan_meta.get("plan_id"), runtime.get("plan_id")),
                    ("cycle_id", state_meta.get("cycle_id"), runtime.get("run_id")),
                    ("pricing_as_of", state_meta.get("pricing_as_of"), runtime.get("pricing_as_of")),
                    ("trigger_type", state_meta.get("trigger_type"), runtime.get("trigger_type")),
                )
                for label, actual, expected in runtime_bindings:
                    if actual != str(expected):
                        errors.append(f"revision3 baseline {label} does not bind semantics runtime")
            if isinstance(control, Mapping) and state_meta.get(
                "created_by_transition"
            ) != control.get("write_transition"):
                errors.append(
                    "revision3 baseline created_by_transition does not bind semantics control"
                )
            if plan_meta.get("state_as_of") != state_meta.get("state_recorded_at"):
                errors.append("revision3 baseline plan.state_as_of must equal state_recorded_at")

    if errors or set(sidecars) != {"STATE", "PLAN"} or set(metadata_by_role) != {
        "STATE", "PLAN"
    } or set(semantics_by_role) != {"STATE", "PLAN"}:
        return None, errors
    semantics = semantics_by_role["STATE"]
    metadata = metadata_by_role["STATE"]
    fingerprints = compute_fingerprints(semantics)
    return {
        "schema": REVISION3_BASELINE_SCHEMA,
        "state_content_fingerprint": content_fingerprint(state_text),
        "plan_content_fingerprint": content_fingerprint(plan_text),
        "baseline_bundle_identity": str(sidecars["STATE"]["bundle_identity"]),
        "canonical_semantics_fingerprint": _fingerprint(semantics),
        "strategy_id": str(metadata["strategy_id"]),
        "strategy_revision": str(metadata["strategy_revision"]),
        "state_id": str(metadata["state_id"]),
        "plan_id": str(metadata["projection_plan_id"]),
        "cycle_id": str(metadata["cycle_id"]),
        **fingerprints,
    }, []


def load_revision3_baseline(
    state_path: str, plan_path: str, *, require_owner_paths: bool, model_market: bool = False
) -> Revision3Baseline:
    state = Path(state_path).resolve()
    plan = Path(plan_path).resolve()
    if require_owner_paths and (
        state != OWNER_STATE_PATH.resolve() or plan != OWNER_PLAN_PATH.resolve()
    ):
        raise GuardError(
            "current revision-3 baseline must use the fixed state/plan Owner paths"
        )
    state_text = _load_text(str(state))
    plan_text = _load_text(str(plan))
    binding, errors = _derive_revision3_baseline_binding(state_text, plan_text, model_market=model_market)
    if errors or binding is None:
        raise GuardError("invalid revision-3 baseline: " + "; ".join(errors))
    return Revision3Baseline(state, plan, state_text, plan_text, binding)


def validate_revision3_baseline(
    bundle: Mapping[str, Any], baseline: Revision3Baseline,
    *, require_owner_paths: bool = False, model_market: bool = False,
) -> list[str]:
    errors: list[str] = []
    state = baseline.state_path.resolve()
    plan = baseline.plan_path.resolve()
    if require_owner_paths and (
        state != OWNER_STATE_PATH.resolve() or plan != OWNER_PLAN_PATH.resolve()
    ):
        errors.append("revision3 current baseline paths are not the fixed Owner paths")
    try:
        actual_state = _load_text(str(state))
        actual_plan = _load_text(str(plan))
    except OSError as exc:
        return [f"revision3 baseline cannot be reloaded: {exc}"]
    binding, binding_errors = _derive_revision3_baseline_binding(
        actual_state, actual_plan, model_market=model_market
    )
    errors.extend(binding_errors)
    if binding is None:
        return errors
    if baseline.state_text != actual_state or baseline.plan_text != actual_plan:
        errors.append("revision3 baseline changed after it was loaded")
    if baseline.binding != binding:
        errors.append("revision3 baseline loaded binding does not match actual files")
    declared = bundle.get("previous_projection_baseline")
    if declared != binding:
        errors.append(
            "previous_projection_baseline must equal the actual revision-3 projection binding"
        )
    previous = bundle.get("previous_fingerprints")
    if not isinstance(previous, Mapping) or previous != {
        "decision_fingerprint": binding["decision_fingerprint"],
        "report_fingerprint": binding["report_fingerprint"],
    }:
        errors.append(
            "previous_fingerprints must equal the actual revision-3 projection fingerprints"
        )
    return errors


def _validate_projection_sidecar(
    document_text: str,
    role: str,
    bundle: Mapping[str, Any],
    errors: list[str],
) -> None:
    try:
        sidecar = extract_projection_sidecar(document_text)
    except GuardError as exc:
        errors.append(str(exc))
        return
    path = f"{role.lower()} embedded sidecar"
    if not _reject_unknown_keys(sidecar, PROJECTION_SIDECAR_KEYS, path, errors):
        return
    if set(sidecar) != PROJECTION_SIDECAR_KEYS:
        errors.append(f"{path} must contain exactly all canonical fields")
    expected = build_projection_sidecar(bundle, document_text, role)
    if sidecar != expected:
        errors.append(f"{path} does not equal deterministic bundle recomputation")


def _validate_projection_body(
    text: str, role: str, errors: list[str]
) -> None:
    sections = _markdown_sections(text)
    required = STATE_PROJECTION_SECTIONS if role == "STATE" else PLAN_PROJECTION_SECTIONS
    for section in required:
        body = sections.get(section)
        if body is None:
            errors.append(f"{role.lower()} body is missing real section {section}")
        elif len(re.sub(r"\s+", "", body)) < 12:
            errors.append(f"{role.lower()} body section {section} lacks substantive content")


def validate_projection_texts(
    state_text: str,
    plan_text: str,
    bundle: Mapping[str, Any] | None = None,
    previous_baseline: Revision3Baseline | None = None,
    fact_owner_snapshot: FactOwnerSnapshot | None = None,
    *,
    require_fact_owner_paths: bool = True,
) -> list[str]:
    errors: list[str] = []
    state = parse_markdown_fields(state_text)
    plan = parse_markdown_fields(plan_text)
    for field in STATE_PROJECTION_METADATA_FIELDS:
        if not _nonempty(state.get(field)):
            errors.append(f"state.{field} is missing or empty")
    for field in PLAN_PROJECTION_METADATA_FIELDS:
        if not _nonempty(plan.get(field)):
            errors.append(f"plan.{field} is missing or empty")
    _validate_projection_metadata_values(state, "STATE", state_text, errors)
    _validate_projection_metadata_values(plan, "PLAN", plan_text, errors)
    _validate_projection_body(state_text, "STATE", errors)
    _validate_projection_body(plan_text, "PLAN", errors)
    for side, fields in (("state", state), ("plan", plan)):
        if "projection_fingerprint" in fields:
            errors.append(f"{side}.projection_fingerprint is a legacy revision-2 field")
        for field in FINGERPRINT_FIELDS:
            value = fields.get(field)
            if value and not SHA256_RE.fullmatch(value):
                errors.append(f"{side}.{field} is not a canonical sha256 value")
    comparisons = (
        ("strategy identity", state.get("strategy_id"), plan.get("source_strategy_id")),
        (
            "strategy revision",
            state.get("strategy_revision"),
            plan.get("source_strategy_revision"),
        ),
        ("state identity", state.get("state_id"), plan.get("source_state_id")),
        ("plan identity", state.get("projection_plan_id"), plan.get("plan_id")),
        ("cycle identity", state.get("cycle_id"), plan.get("cycle_id")),
        *(
            (
                field.replace("_", " "),
                state.get(field),
                plan.get(field),
            )
            for field in FINGERPRINT_FIELDS
        ),
    )
    for label, left, right in comparisons:
        if left and right and left != right:
            errors.append(f"state/plan {label} mismatch")
    if state.get("strategy_revision") and state.get("strategy_revision") != "3":
        errors.append("state.strategy_revision must equal 3")
    if state.get("state_recorded_at") and plan.get("state_as_of") != state.get(
        "state_recorded_at"
    ):
        errors.append("plan.state_as_of must equal state.state_recorded_at")
    if bundle is None:
        errors.append("projection validation requires the actual PRE_WRITE bundle")
        return errors
    migration_baseline = (
        load_immutable_migration_baseline()
        if bundle.get("write_transition") == "MIGRATION"
        else None
    )
    bundle_errors = validate_bundle(
        bundle, migration_baseline, previous_baseline, fact_owner_snapshot,
        require_owner_baseline=False,
        require_fact_owner_paths=require_fact_owner_paths,
    )
    if bundle_errors:
        errors.extend(f"projection bundle invalid: {error}" for error in bundle_errors)
    try:
        computed = compute_fingerprints(bundle)
        derived = _derive_control_unchecked(bundle, previous_baseline)
        runtime = bundle.get("runtime", {})
        expected_bindings = {
            "strategy_id": "BTC-CORE",
            "strategy_revision": "3",
            "state_id": str(runtime.get("state_id", "")),
            "cycle_id": str(runtime.get("run_id", "")),
            "created_by_transition": derived["write_transition"],
            "projection_plan_id": str(runtime.get("plan_id", "")),
            "pricing_as_of": str(runtime.get("pricing_as_of", "")),
            "trigger_type": str(runtime.get("trigger_type", "")),
            "quote_verification_status": str(bundle.get("quote_verification_status", "")),
            "decision_evidence_status": str(bundle.get("decision_evidence_status", "")),
            "driver_regime": str(bundle.get("causal_attribution", {}).get("driver_regime", "")),
            "path_decision": str(bundle.get("path_decision", "")),
            "projection_mode": str(bundle.get("projection_mode", "")),
            "user_input_requirement": str(bundle.get("user_input_requirement", "")),
            "source_strategy_id": "BTC-CORE",
            "source_strategy_revision": "3",
            "source_state_id": str(runtime.get("state_id", "")),
            "plan_id": str(runtime.get("plan_id", "")),
            **computed,
        }
        for field, expected in expected_bindings.items():
            actual = state.get(field) if field in state else plan.get(field)
            if field in state and state.get(field) != expected:
                errors.append(f"state.{field} does not bind PRE_WRITE bundle")
            if field in plan and plan.get(field) != expected:
                errors.append(f"plan.{field} does not bind PRE_WRITE bundle")
    except (GuardError, AttributeError) as exc:
        errors.append(str(exc))
    _validate_projection_sidecar(state_text, "STATE", bundle, errors)
    _validate_projection_sidecar(plan_text, "PLAN", bundle, errors)
    try:
        expected_state = render_projection_document(bundle, "STATE")
        expected_plan = render_projection_document(bundle, "PLAN")
    except (GuardError, TypeError, ValueError) as exc:
        errors.append(f"deterministic projection render failed: {exc}")
    else:
        if state_text != expected_state:
            errors.append("state document must exactly equal deterministic bundle rendering")
        if plan_text != expected_plan:
            errors.append("plan document must exactly equal deterministic bundle rendering")
    return errors


def content_fingerprint(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_fact_owner_snapshot(
    paths: Mapping[str, str | Path] | None = None,
    *,
    require_owner_paths: bool = True,
) -> FactOwnerSnapshot:
    """Load the complete local account-fact baseline without normalizing bytes."""

    selected = FACT_OWNER_PATHS if paths is None else paths
    if set(selected) != set(FACT_OWNER_PATHS):
        raise GuardError("fact Owner paths must contain exactly the three controlled owners")
    resolved = {owner_id: Path(selected[owner_id]).resolve() for owner_id in FACT_OWNER_PATHS}
    if require_owner_paths and any(
        resolved[owner_id] != FACT_OWNER_PATHS[owner_id].resolve()
        for owner_id in FACT_OWNER_PATHS
    ):
        raise GuardError("fact Owner snapshot must use the fixed local fact Owner paths")
    texts = {owner_id: _load_text(str(path)) for owner_id, path in resolved.items()}
    return FactOwnerSnapshot(
        paths=resolved,
        texts=texts,
        fingerprints={
            owner_id: content_fingerprint(texts[owner_id])
            for owner_id in FACT_OWNER_PATHS
        },
    )


def _validate_fact_owner_snapshot(
    snapshot: FactOwnerSnapshot | None,
    *,
    require_owner_paths: bool,
) -> tuple[Mapping[str, str] | None, list[str]]:
    errors: list[str] = []
    if snapshot is None:
        return None, ["validation requires an explicit local fact Owner snapshot"]
    if set(snapshot.paths) != set(FACT_OWNER_PATHS) or set(snapshot.texts) != set(
        FACT_OWNER_PATHS
    ) or set(snapshot.fingerprints) != set(FACT_OWNER_PATHS):
        return None, ["fact Owner snapshot must contain exactly the three controlled owners"]
    if require_owner_paths and any(
        snapshot.paths[owner_id].resolve() != FACT_OWNER_PATHS[owner_id].resolve()
        for owner_id in FACT_OWNER_PATHS
    ):
        errors.append("fact Owner snapshot must use the fixed local fact Owner paths")
    actual_texts: dict[str, str] = {}
    for owner_id, path in snapshot.paths.items():
        try:
            actual_texts[owner_id] = _load_text(str(path.resolve()))
        except (OSError, UnicodeError) as exc:
            errors.append(f"fact Owner {owner_id} cannot be reloaded: {exc}")
    if set(actual_texts) == set(FACT_OWNER_PATHS):
        actual_fingerprints = {
            owner_id: content_fingerprint(actual_texts[owner_id])
            for owner_id in FACT_OWNER_PATHS
        }
        if snapshot.texts != actual_texts or snapshot.fingerprints != actual_fingerprints:
            errors.append("fact Owner snapshot changed after loading")
        return actual_fingerprints, errors
    return None, errors


def validate_baseline_hashes(
    state_text: str,
    plan_text: str,
    expected_state_fingerprint: str,
    expected_plan_fingerprint: str,
) -> list[str]:
    """Reject a write when either Owner file changed after baseline capture."""

    errors: list[str] = []
    for label, value in (
        ("expected_state_fingerprint", expected_state_fingerprint),
        ("expected_plan_fingerprint", expected_plan_fingerprint),
    ):
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            errors.append(f"{label} must be a canonical sha256 value")
    if errors:
        return errors
    if content_fingerprint(state_text) != expected_state_fingerprint:
        errors.append("state baseline changed; atomic write must abort")
    if content_fingerprint(plan_text) != expected_plan_fingerprint:
        errors.append("plan baseline changed; atomic write must abort")
    return errors


INTERNAL_REPORT_PATTERNS = (
    re.compile(r"\btask_role\b", re.IGNORECASE),
    re.compile(r"\bmonitor_state\b", re.IGNORECASE),
    re.compile(r"\battention_level\b", re.IGNORECASE),
    re.compile(r"\battention_planned\b", re.IGNORECASE),
    re.compile(r"\bevidence_status\b", re.IGNORECASE),
    re.compile(r"\bdecision_evidence_status\b", re.IGNORECASE),
    re.compile(r"\bprojection_mode\b", re.IGNORECASE),
    re.compile(r"\bproject_patch_required\b", re.IGNORECASE),
    re.compile(r"\buser_input_requirement\b", re.IGNORECASE),
    re.compile(r"\b(?:state|plan)_id\b", re.IGNORECASE),
    re.compile(r"\bcycle_id\b", re.IGNORECASE),
    re.compile(r"\b(?:write|created_by)_transition\b", re.IGNORECASE),
    re.compile(r"\bdecision_class\b", re.IGNORECASE),
    re.compile(r"\breport_required\b", re.IGNORECASE),
    re.compile(r"\bdeep_eval_status\b", re.IGNORECASE),
    re.compile(r"\bstrategy_revision\b", re.IGNORECASE),
    re.compile(r"\bfingerprint\b", re.IGNORECASE),
    re.compile(r"\bdedupe(?:_key)?\b", re.IGNORECASE),
    re.compile(r"sha256:[0-9a-f]{16,}", re.IGNORECASE),
    re.compile(r"BTC-STATE-\d{8}", re.IGNORECASE),
    re.compile(r"\bgpt-5(?:\.[0-9]+)?(?:-[a-z0-9-]+)?\b", re.IGNORECASE),
    re.compile(r"\b(?:terra|sol)\s*(?:/|-)\s*(?:low|max)\b", re.IGNORECASE),
    re.compile(r"\breasoning_effort\b", re.IGNORECASE),
    re.compile(r"推理等级|模型分配"),
    re.compile(r"\[\[BTC-R3-REPORT-WITNESS\|", re.IGNORECASE),
    re.compile(r"<!--|-->", re.IGNORECASE),
    re.compile(r"\{\s*\""),
    re.compile(r'"'),
    *(re.compile(rf"\b{field}\b", re.IGNORECASE) for field in REPORT_SEMANTIC_FIELDS),
)


def generate_report_witnesses(projection: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Create an internal sidecar and a deterministic, readable body excerpt."""

    if _is_expressive_report(projection):
        from decision_report import render_report
        envelope = render_report({field: projection[field] for field in REPORT_SEMANTIC_FIELDS})
        if not envelope.get("ok"):
            raise GuardError("; ".join(envelope.get("errors", [])))
        return envelope["validation_projection"]["rendered_evidence"]
    if not isinstance(projection, Mapping):
        raise GuardError("report witnesses require a projection object")
    semantic_projection = {
        field: projection.get(field) for field in REPORT_SEMANTIC_FIELDS
    }
    shape_errors: list[str] = []
    _validate_report_semantics_shape(
        semantic_projection, "report_semantics", shape_errors
    )
    if shape_errors:
        raise GuardError("; ".join(shape_errors))
    witnesses: dict[str, dict[str, str]] = {}
    for field in REPORT_SEMANTIC_FIELDS:
        value = projection.get(field)
        if not _nonempty(value):
            raise GuardError(f"report witness cannot bind empty {field}")
        visible_value = _render_visible_semantic(field, value)
        witnesses[field] = {
            "visible_excerpt": f"{REPORT_VISIBLE_LABELS[field]}：{visible_value}。",
            "semantic_fingerprint": _fingerprint({field: value}),
        }
    return witnesses


def _visible_code(value: Any, context: str) -> str:
    allowed = REPORT_CODE_ALLOWLIST.get(context, set())
    if not isinstance(value, str) or value not in allowed:
        raise GuardError(f"{context} uses an unrecognized report semantic code: {value!r}")
    return REPORT_CODE_DISPLAY[value]


def _visible_commitment_code(
    value: Any, allowed: set[str], context: str
) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise GuardError(
            f"{context} uses an unrecognized commitment semantic code: {value!r}"
        )
    return COMMITMENT_VALUE_DISPLAY[value]


def _canonical_decimal_text(value: Any, context: str, *, positive: bool = False) -> str:
    number = _decimal(value, context)
    if positive and number <= 0:
        raise GuardError(f"{context} must be positive")
    if not positive and number < 0:
        raise GuardError(f"{context} must be non-negative")
    return _canonical_number(number)[1]


def _render_visible_semantic(field: str, value: Any) -> str:
    if field in {"overall_user_decision", "primary_causal_explanation", "user_visible_major_risk"}:
        return _visible_code(value, field)
    if field == "rejected_competing_explanations":
        return "；".join(
            f"{_visible_code(item['hypothesis_id'], 'rejected_hypothesis_id')}（{_visible_code(item['reason_class'], 'rejected_reason_class')}）"
            for item in sorted(
                value,
                key=lambda item: json.dumps(
                    _canonicalize(item), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    if field == "portfolio_action_by_module":
        return "；".join(
            f"{PORTFOLIO_VISIBLE_LABELS[module]}：{_visible_code(value[module], 'portfolio_action')}"
            for module in PORTFOLIO_MODULES
        )
    if field == "decision_relevant_levels_and_sizes":
        rendered: list[str] = []
        for item in sorted(
            value,
            key=lambda item: json.dumps(
                _canonicalize(item), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ),
        ):
            parts = [f"档位：{_visible_code(item['level_id'], 'level_id')}"]
            if "price_u" in item:
                parts.append(f"价格：{_canonical_decimal_text(item['price_u'], 'price_u', positive=True)}U")
            if "price_band_u" in item:
                band = item["price_band_u"]
                parts.append(
                    f"价格区间：{_canonical_decimal_text(band['lower_u'], 'lower_u', positive=True)}–{_canonical_decimal_text(band['upper_u'], 'upper_u', positive=True)}U"
                )
            parts.append(f"规模：{_canonical_decimal_text(item['amount_u'], 'amount_u')}U")
            if "commitment_change_fields" in item:
                changes = item["commitment_change_fields"]
                parts.append(
                    "承诺披露范围：" + "、".join(
                        COMMITMENT_CHANGE_FIELD_DISPLAY[code]
                        for code in sorted(changes)
                    )
                )
                snapshot = item["commitment_snapshot"]
                for snapshot_field in (
                    "candidate_kind", "value_role", "entry_eligibility",
                ):
                    parts.append(
                        f"{COMMITMENT_SNAPSHOT_VISIBLE_LABELS[snapshot_field]}："
                        + _visible_commitment_code(
                            snapshot[snapshot_field],
                            COMMITMENT_SNAPSHOT_CODE_ALLOWLIST[snapshot_field],
                            snapshot_field,
                        )
                    )
                amount_basis_parts = [
                    f"{COMMITMENT_AMOUNT_BASIS_VISIBLE_LABELS[basis_field]}："
                    + _visible_commitment_code(
                        snapshot["amount_basis_classes"][basis_field],
                        COMMITMENT_AMOUNT_BASIS_CODE_ALLOWLIST[basis_field],
                        basis_field,
                    )
                    for basis_field in (
                        "budget_source", "net_exposure_constraint",
                        "value_gap_rationale", "risk_assumption",
                        "continuation_reason",
                    )
                ]
                parts.append("规模依据：" + "、".join(amount_basis_parts))
                for condition_field, label in (
                    ("formation_condition_classes", "形成条件"),
                    ("invalidation_condition_classes", "失效条件"),
                ):
                    parts.append(
                        f"{label}：" + "、".join(
                            _visible_commitment_code(
                                code,
                                COMMITMENT_CONDITION_CODE_ALLOWLIST[
                                    condition_field
                                ],
                                condition_field,
                            )
                            for code in sorted(snapshot[condition_field])
                        )
                    )
                for snapshot_field in (
                    "evidence_confidence", "account_precondition",
                ):
                    parts.append(
                        f"{COMMITMENT_SNAPSHOT_VISIBLE_LABELS[snapshot_field]}："
                        + _visible_commitment_code(
                            snapshot[snapshot_field],
                            COMMITMENT_SNAPSHOT_CODE_ALLOWLIST[snapshot_field],
                            snapshot_field,
                        )
                    )
                parts.append(
                    "沿用状态："
                    + (
                        "继续沿用既有候选承诺"
                        if snapshot["carried_forward"]
                        else "不再沿用既有候选承诺"
                    )
                )
            rendered.append("（" + "，".join(parts) + "）")
        return "；".join(rendered)
    if field in {"material_uncertainties", "next_decision_changing_evidence"}:
        return "【" + "，".join(
            _visible_code(item, field) for item in sorted(value)
        ) + "】"
    raise GuardError(f"unsupported report semantic field {field}")


def render_report_body(projection: Mapping[str, Any]) -> str:
    if _is_expressive_report(projection):
        from decision_report import render_report
        envelope = render_report({field: projection[field] for field in REPORT_SEMANTIC_FIELDS})
        if not envelope.get("ok"):
            raise GuardError("; ".join(envelope.get("errors", [])))
        return envelope["body"]
    """The sole user-visible report body; sidecar fields never enter it."""

    witnesses = generate_report_witnesses(projection)
    return "\n\n".join(
        witnesses[field]["visible_excerpt"] for field in REPORT_SEMANTIC_FIELDS
    ) + "\n\n" + REPORT_AUTHORIZATION_DISCLAIMER


def validate_report_text(report: str, projection: Mapping[str, Any] | None = None) -> list[str]:
    """Validate leakage and a structured-to-rendered semantic witness.

    This intentionally does not grade prose by fixed headings or magic words.
    It validates an internal sidecar against the exact eight semantics while
    requiring the final user-visible body to contain only readable excerpts.
    """

    if _is_expressive_report(projection):
        from decision_report import validate_report_text as validate_expressive_text
        return validate_expressive_text(report, projection)
    errors: list[str] = []
    if not report.strip():
        return ["report is empty"]
    for pattern in INTERNAL_REPORT_PATTERNS:
        if pattern.search(report):
            errors.append(f"report leaks internal control field matching {pattern.pattern!r}")
    if not isinstance(projection, Mapping):
        errors.append("a structured report projection is required")
        return errors
    expected_projection_keys = set(REPORT_SEMANTIC_FIELDS) | {
        "rendered_evidence", "authorization_disclaimer_excerpt"
    }
    _reject_unknown_keys(projection, expected_projection_keys, "report projection", errors)
    if set(projection) != expected_projection_keys:
        errors.append("report projection must contain exactly semantics, witnesses and disclaimer")
    _validate_report_semantics_shape(
        {field: projection.get(field) for field in REPORT_SEMANTIC_FIELDS},
        "report projection semantics",
        errors,
    )
    try:
        expected_body = render_report_body(projection)
    except GuardError as exc:
        errors.append(str(exc))
        expected_body = None
    if expected_body is not None and report != expected_body:
        errors.append("report body must exactly equal the deterministic renderer output")
    for field in REPORT_SEMANTIC_FIELDS:
        if field not in projection or not _nonempty(projection.get(field)):
            errors.append(f"report projection is missing {field}")
    structured_types = {
        "rejected_competing_explanations": list,
        "portfolio_action_by_module": Mapping,
        "decision_relevant_levels_and_sizes": list,
        "material_uncertainties": list,
        "next_decision_changing_evidence": list,
    }
    for field, expected_type in structured_types.items():
        if field in projection and not isinstance(projection.get(field), expected_type):
            errors.append(f"report projection {field} has the wrong structured type")
    portfolio_actions = projection.get("portfolio_action_by_module")
    if isinstance(portfolio_actions, Mapping):
        missing_modules = [
            module for module in PORTFOLIO_MODULES if not _nonempty(portfolio_actions.get(module))
        ]
        if missing_modules:
            errors.append(
                "report projection omits portfolio modules: " + ", ".join(missing_modules)
            )
    witnesses = projection.get("rendered_evidence")
    positions: dict[str, int] = {}
    spans: dict[str, tuple[int, int]] = {}
    try:
        expected_witnesses = generate_report_witnesses(projection)
    except GuardError as exc:
        errors.append(str(exc))
        expected_witnesses = {}
    if not isinstance(witnesses, Mapping):
        errors.append("report projection needs rendered_evidence")
    else:
        _reject_unknown_keys(
            witnesses, set(REPORT_SEMANTIC_FIELDS),
            "report projection.rendered_evidence", errors,
        )
        for field in REPORT_SEMANTIC_FIELDS:
            witness = witnesses.get(field)
            expected = expected_witnesses.get(field)
            _reject_unknown_keys(
                witness, {"visible_excerpt", "semantic_fingerprint"},
                f"report projection.rendered_evidence.{field}", errors,
            )
            if witness != expected:
                errors.append(
                    f"rendered_evidence.{field} must equal the deterministic semantic sidecar"
                )
                continue
            excerpt = expected["visible_excerpt"]
            position = report.find(excerpt)
            if position < 0:
                errors.append(f"rendered_evidence.{field} is absent from report")
            else:
                positions[field] = position
                spans[field] = (position, position + len(excerpt))
        ordered_spans = sorted(spans.items(), key=lambda item: item[1][0])
        for (left_field, left), (right_field, right) in zip(
            ordered_spans, ordered_spans[1:]
        ):
            if right[0] < left[1]:
                errors.append(
                    f"rendered_evidence excerpts overlap: {left_field}, {right_field}"
                )
    overall_position = positions.get("overall_user_decision")
    later_positions = [
        position
        for field, position in positions.items()
        if field != "overall_user_decision"
    ]
    if overall_position is not None and later_positions and overall_position > min(later_positions):
        errors.append("overall user decision must be rendered before supporting sections")
    if re.search(r"不适用|相反|推翻上述|否定上述|重新解释", report):
        errors.append("report body cannot negate or reinterpret a deterministic visible excerpt")
    disclaimer = projection.get("authorization_disclaimer_excerpt")
    if disclaimer != REPORT_AUTHORIZATION_DISCLAIMER:
        errors.append("authorization_disclaimer_excerpt must equal the fixed disclaimer")
    elif disclaimer not in report:
        errors.append("authorization disclaimer excerpt is absent from report")
    elif isinstance(witnesses, Mapping) and any(
        isinstance(witness, Mapping)
        and disclaimer == witness.get("visible_excerpt")
        for witness in witnesses.values()
    ):
        errors.append("authorization disclaimer cannot double as a semantic witness")
    return errors


def _load_json(path: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise GuardError(f"JSON object contains duplicate key: {key!r}")
            result[key] = value
        return result

    load_options = {
        "parse_float": Decimal,
        "object_pairs_hook": unique_object,
    }
    if path == "-":
        return json.load(sys.stdin, **load_options)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle, **load_options)


def _load_text(path: str) -> str:
    if path == "-":
        return sys.stdin.buffer.read().decode("utf-8", errors="strict")
    return Path(path).read_bytes().decode("utf-8", errors="strict")


def _emit(payload: Any) -> None:
    def encode_extra(value: Any) -> Any:
        if isinstance(value, Decimal):
            return _canonical_number(value)[1]
        raise TypeError(f"cannot JSON encode {type(value).__name__}")

    # Build a complete ASCII-safe document before touching stdout.  This keeps
    # even malformed Unicode inputs on the structured hard-failure path rather
    # than leaving a half-written first JSON object followed by another one.
    rendered = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, indent=2, default=encode_extra
    )
    sys.stdout.write(rendered + "\n")


def _validation_result(errors: Iterable[str], **extra: Any) -> int:
    materialized = list(errors)
    _emit(
        {
            "ok": not materialized,
            "hard_failure": bool(materialized),
            "errors": materialized,
            **extra,
        }
    )
    return 0 if not materialized else 2


def _collector_review_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Read an optional local export; never merge collector data into PRE_WRITE.

    The extra result is an untrusted observation/review context, not another
    decision bundle.  In particular it cannot replace price evidence or any
    of the three canonical fingerprints below.
    """
    current_path = getattr(args, "collector_observations", None)
    previous_path = getattr(args, "previous_collector_observations", None)
    if previous_path is not None and current_path is None:
        raise GuardError("--previous-collector-observations requires --collector-observations")
    if current_path is None:
        return {}
    from collector_observation import load_export, review_exports
    current = load_export(current_path)
    previous = load_export(previous_path) if previous_path is not None else None
    return {"collector_review": review_exports(current, previous)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    zone = subparsers.add_parser("classify-zone")
    zone.add_argument("--input", required=True, help="JSON object/array of venue prices")
    zone.add_argument("--lower", required=True)
    zone.add_argument("--upper", required=True)
    zone.add_argument("--prior-relation")

    candles = subparsers.add_parser("inspect-candles")
    candles.add_argument("--input", required=True, help="JSON candle array")
    candles.add_argument("--threshold", required=True)
    candles.add_argument("--as-of-ms", required=True)
    candles.add_argument(
        "--boundary-role", required=True, choices=("LOWER", "UPPER"),
        help="which primary value-zone bound --threshold represents",
    )

    fingerprints = subparsers.add_parser("fingerprints")
    fingerprints.add_argument("--input", required=True, help="JSON decision bundle")

    bundle = subparsers.add_parser(
        "validate-bundle",
        description=(
            "Validate PRE_WRITE. Daily rev3 runs require --current-state/--current-plan; "
            "one-time MIGRATION requires all four --previous-* revision2 artifacts instead."
        ),
    )
    bundle.add_argument("--input", required=True, help="JSON decision bundle")
    bundle.add_argument("--current-state", help="actual current rev3 state Owner Markdown")
    bundle.add_argument("--current-plan", help="actual current rev3 plan Owner Markdown")
    bundle.add_argument("--previous-state", help="fixed revision2 state Owner, MIGRATION only")
    bundle.add_argument("--previous-plan", help="fixed revision2 plan Owner, MIGRATION only")
    bundle.add_argument("--previous-report", help="immutable revision2 13:46 report, MIGRATION only")
    bundle.add_argument("--previous-report-projection", help="immutable revision2 report semantics, MIGRATION only")

    receipt = subparsers.add_parser("validate-receipt")
    receipt.add_argument("--input", required=True, help="JSON final run receipt")
    receipt.add_argument("--bundle", required=True, help="actual validated PRE_WRITE JSON bundle")
    receipt.add_argument("--report", help="actual deterministic report body")
    receipt.add_argument("--state", help="actual post-write state Markdown")
    receipt.add_argument("--plan", help="actual post-write plan Markdown")
    receipt.add_argument(
        "--cloud-readback",
        help=(
            "DELIVERED-only strict JSON transcript: exact visible report plus the sole "
            "complete assistant response 已收录"
        ),
    )
    receipt.add_argument("--current-state", help="A-only actual current rev3 state Owner baseline")
    receipt.add_argument("--current-plan", help="A-only actual current rev3 plan Owner baseline")
    receipt.add_argument("--previous-state-snapshot", help="D/E write-before rev3 state snapshot")
    receipt.add_argument("--previous-plan-snapshot", help="D/E write-before rev3 plan snapshot")
    receipt.add_argument(
        "--record-hourly-completion", choices=("DEEP", "REPORT", "BOTH"),
        help="after full FINAL validation, persist successful hourly deep/report baseline; REPORT needs DELIVERED",
    )

    unavailable_receipt = subparsers.add_parser(
        "validate-unavailable-receipt",
        description="Validate fail-closed fallback against actual current rev3 Owners and a lightweight observation JSON.",
    )
    unavailable_receipt.add_argument("--input", required=True)
    unavailable_receipt.add_argument("--current-state", required=True)
    unavailable_receipt.add_argument("--current-plan", required=True)
    unavailable_receipt.add_argument(
        "--observation", required=True,
        help=(
            "lightweight observation JSON including evaluation_as_of; the cutoff "
            "is fingerprint-bound and cannot exceed the local scheduler clock"
        ),
    )

    projection = subparsers.add_parser("validate-projection")
    projection.add_argument("--state", required=True)
    projection.add_argument("--plan", required=True)
    projection.add_argument("--bundle", required=True)
    projection.add_argument("--previous-state-snapshot", help="required non-MIGRATION write-before state snapshot")
    projection.add_argument("--previous-plan-snapshot", help="required non-MIGRATION write-before plan snapshot")

    report = subparsers.add_parser("validate-report")
    report.add_argument("--input", required=True, help="Markdown report or - for stdin")
    report.add_argument(
        "--projection", required=True,
        help="JSON containing either exactly eight semantics or render-report.validation_projection",
    )

    render_report = subparsers.add_parser("render-report")
    render_report.add_argument(
        "--projection", required=True,
        help="JSON with exactly eight report semantics; stdout is a JSON envelope and body is the report text",
    )

    render_sidecar = subparsers.add_parser("render-projection-sidecar")
    render_sidecar.add_argument("--bundle", required=True)
    render_sidecar.add_argument("--input", required=True, help="state or plan Markdown")
    render_sidecar.add_argument("--role", required=True, choices=("STATE", "PLAN"))
    render_sidecar.add_argument("--previous-state-snapshot", help="required non-MIGRATION old state projection")
    render_sidecar.add_argument("--previous-plan-snapshot", help="required non-MIGRATION old plan projection")
    render_sidecar.epilog = (
        "Input must already equal the deterministic body; stdout is a JSON envelope and only "
        "sidecar_block may be appended. Prefer render-projection-document."
    )

    render_document = subparsers.add_parser(
        "render-projection-document",
        description="Render the complete byte-exact Owner document from a validated bundle.",
    )
    render_document.add_argument("--bundle", required=True)
    render_document.add_argument("--role", required=True, choices=("STATE", "PLAN"))
    render_document.add_argument("--previous-state-snapshot", help="required non-MIGRATION old state projection")
    render_document.add_argument("--previous-plan-snapshot", help="required non-MIGRATION old plan projection")
    for replay_renderer in (report, render_report, render_sidecar, render_document):
        replay_renderer.add_argument(
            "--replay-only", action="store_true",
            help="explicit legacy fixture rendering only; never authorizes production Owner writes or receipts",
        )

    inspect_baseline = subparsers.add_parser(
        "inspect-revision3-baseline",
        description="Read-only derivation of the binding that a daily bundle must copy verbatim.",
    )
    inspect_baseline.add_argument("--current-state", required=True)
    inspect_baseline.add_argument("--current-plan", required=True)
    for observation_entry in (zone, fingerprints, bundle):
        observation_entry.add_argument(
            "--collector-observations",
            help="local JSON array exported by reader.py collector-observations; never fetches data",
        )
        observation_entry.add_argument(
            "--previous-collector-observations",
            help="optional previous local export for auxiliary quality/candidate comparison only",
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "classify-zone":
            collector_review = _collector_review_from_args(args)
            result = classify_zone_observation(
                _load_json(args.input), args.lower, args.upper, args.prior_relation
            )
            _emit({"ok": True, **result, **collector_review})
            return 0
        if args.command == "inspect-candles":
            result = inspect_candles(
                _load_json(args.input), args.threshold, args.as_of_ms,
                args.boundary_role,
            )
            _emit({"ok": True, **result})
            return 0
        if args.command == "fingerprints":
            collector_review = _collector_review_from_args(args)
            _emit({"ok": True, **compute_fingerprints(_load_json(args.input)), **collector_review})
            return 0
        if args.command == "validate-bundle":
            collector_review = _collector_review_from_args(args)
            data = _load_json(args.input)
            if not isinstance(data, Mapping):
                return _validation_result(["--input must contain a JSON object"])
            if isinstance(data, Mapping) and data.get("schema") == UNAVAILABLE_RECEIPT_SCHEMA:
                return _validation_result([
                    "unavailable receipt must use validate-unavailable-receipt with Owner and observation inputs"
                ])
            migration_args = (
                args.previous_state,
                args.previous_plan,
                args.previous_report,
                args.previous_report_projection,
            )
            current_args = (args.current_state, args.current_plan)
            migration_baseline: MigrationBaseline | None = None
            previous_baseline: Revision3Baseline | None = None
            is_migration = isinstance(data, Mapping) and data.get(
                "write_transition"
            ) == "MIGRATION"
            if is_migration:
                if any(value is not None for value in current_args):
                    return _validation_result(
                        ["MIGRATION must not supply --current-state/--current-plan"]
                    )
                if not all(value is not None for value in migration_args):
                    return _validation_result(
                        ["all four MIGRATION baseline arguments are required together"]
                    )
                migration_baseline = load_migration_baseline(*migration_args)
            else:
                if any(value is not None for value in migration_args):
                    return _validation_result([
                        "non-MIGRATION bundle must not supply revision-2 baseline artifacts"
                    ])
                if not all(value is not None for value in current_args):
                    return _validation_result([
                        "non-MIGRATION validate-bundle requires --current-state and --current-plan"
                    ])
                previous_baseline = load_revision3_baseline(
                    *current_args, require_owner_paths=True
                )
            fact_snapshot = load_fact_owner_snapshot()
            production_errors = _production_contract_errors(data)
            if production_errors:
                return _validation_result(production_errors)
            errors = validate_bundle(
                data, migration_baseline, previous_baseline, fact_snapshot,
                require_owner_baseline=not is_migration,
            )
            if errors:
                return _validation_result(errors)
            return _validation_result(
                [],
                **collector_review,
                fingerprints=compute_fingerprints(data),
                decision_comparison=decision_comparison(data, previous_baseline),
                derived_control=derive_control(
                    data, migration_baseline, previous_baseline, fact_snapshot,
                    require_owner_baseline=not is_migration,
                ),
            )
        if args.command == "validate-receipt":
            receipt_data = _load_json(args.input)
            bundle_data = _load_json(args.bundle) if args.bundle else None
            if not isinstance(bundle_data, Mapping):
                return _validation_result(["--bundle must contain a JSON object"])
            production_errors = _production_contract_errors(bundle_data)
            current_pair = (args.current_state, args.current_plan)
            snapshot_pair = (
                args.previous_state_snapshot, args.previous_plan_snapshot
            )
            for values, label in (
                (current_pair, "--current-state/--current-plan"),
                (snapshot_pair, "--previous-state-snapshot/--previous-plan-snapshot"),
            ):
                if any(value is not None for value in values) and not all(
                    value is not None for value in values
                ):
                    return _validation_result([f"{label} are required together"])
            if all(value is not None for value in current_pair) and all(
                value is not None for value in snapshot_pair
            ):
                return _validation_result(["receipt must supply exactly one rev3 baseline pair"])
            previous_baseline = None
            fact_snapshot = load_fact_owner_snapshot()
            if bundle_data.get("write_transition") == "MIGRATION":
                if any(value is not None for value in current_pair + snapshot_pair):
                    return _validation_result(["MIGRATION receipt cannot use a rev3 baseline"])
            else:
                # The bound semantic comparison needs the actual baseline
                # before choosing the A versus D/E artifact branch.  Selecting
                # that branch from raw hashes first recreates the cleanup bug.
                if all(value is not None for value in current_pair):
                    previous_baseline = load_revision3_baseline(
                        *current_pair, require_owner_paths=True
                    )
                elif all(value is not None for value in snapshot_pair):
                    previous_baseline = load_revision3_baseline(
                        *snapshot_pair, require_owner_paths=False
                    )
                else:
                    return _validation_result(["receipt requires an actual revision3 baseline pair"])
                declared_control = derive_control(
                    bundle_data, previous_baseline=previous_baseline,
                    fact_owner_snapshot=fact_snapshot, require_owner_baseline=False,
                )
                if declared_control["project_patch_required"]:
                    if not all(value is not None for value in snapshot_pair):
                        return _validation_result([
                            "D/E receipt requires both write-before revision3 snapshot files"
                        ])
                else:
                    if not all(value is not None for value in current_pair):
                        return _validation_result([
                            "A receipt requires --current-state and --current-plan"
                        ])
            validation_artifacts = dict(
                    report_text=_load_text(args.report) if args.report else None,
                    state_text=_load_text(args.state) if args.state else None,
                    plan_text=_load_text(args.plan) if args.plan else None,
                    cloud_readback=(
                        _load_json(args.cloud_readback) if args.cloud_readback else None
                    ),
                    previous_baseline=previous_baseline,
                    fact_owner_snapshot=fact_snapshot,
                    state_artifact_path=args.state,
                    plan_artifact_path=args.plan,
                    require_owner_readback=True,
            )
            errors = production_errors + validate_receipt(receipt_data, bundle_data, **validation_artifacts)
            completion = {}
            if not errors and args.record_hourly_completion:
                completion = record_validated_hourly_completion(
                    receipt_data, bundle_data, args.record_hourly_completion,
                    **validation_artifacts,
                )
            return _validation_result(errors, **completion)
        if args.command == "validate-unavailable-receipt":
            baseline = load_revision3_baseline(
                args.current_state, args.current_plan, require_owner_paths=True
            )
            return _validation_result(
                validate_unavailable_receipt(
                    _load_json(args.input), baseline, _load_json(args.observation),
                    load_fact_owner_snapshot(),
                    require_owner_baseline=True,
                )
            )
        if args.command == "validate-projection":
            bundle_data = _load_json(args.bundle)
            if not isinstance(bundle_data, Mapping):
                return _validation_result(["--bundle must contain a JSON object"])
            if Path(args.state).resolve() == OWNER_STATE_PATH or Path(args.plan).resolve() == OWNER_PLAN_PATH:
                production_errors = _production_contract_errors(bundle_data)
                if production_errors:
                    return _validation_result(production_errors)
            snapshot_pair = (
                args.previous_state_snapshot, args.previous_plan_snapshot
            )
            previous_baseline = None
            if isinstance(bundle_data, Mapping) and bundle_data.get(
                "write_transition"
            ) != "MIGRATION":
                if not all(value is not None for value in snapshot_pair):
                    return _validation_result([
                        "non-MIGRATION projection requires both write-before revision3 snapshots"
                    ])
                previous_baseline = load_revision3_baseline(
                    *snapshot_pair, require_owner_paths=False
                )
            elif any(value is not None for value in snapshot_pair):
                return _validation_result(["MIGRATION projection cannot use rev3 snapshots"])
            errors = validate_projection_texts(
                _load_text(args.state), _load_text(args.plan), bundle_data,
                previous_baseline, load_fact_owner_snapshot(),
            )
            return _validation_result(errors)
        if args.command == "validate-report":
            projection_data = _load_json(args.projection)
            if _is_expressive_report(projection_data):
                from decision_report import validate_report_text as validate_expressive_text
                return _validation_result(validate_expressive_text(_load_text(args.input), projection_data))
            if not args.replay_only:
                return _validation_result(["fixed-code reports require --replay-only and cannot be production reports"])
            if isinstance(projection_data, Mapping) and set(projection_data) == set(
                REPORT_SEMANTIC_FIELDS
            ):
                projection_data = dict(projection_data)
                projection_data["rendered_evidence"] = generate_report_witnesses(
                    projection_data
                )
                projection_data[
                    "authorization_disclaimer_excerpt"
                ] = REPORT_AUTHORIZATION_DISCLAIMER
            return _validation_result(
                validate_report_text(_load_text(args.input), projection_data)
            )
        if args.command == "render-report":
            projection_data = _load_json(args.projection)
            if _is_expressive_report(projection_data):
                from decision_report import render_report as render_expressive_report
                envelope = render_expressive_report(projection_data)
                _emit(envelope)
                return 0 if envelope.get("ok") else 2
            if not args.replay_only:
                return _validation_result(["fixed-code reports require --replay-only and cannot be production reports"])
            errors: list[str] = []
            _validate_report_semantics_shape(projection_data, "report_semantics", errors)
            if errors:
                return _validation_result(errors)
            body = render_report_body(projection_data)
            validation_projection = dict(projection_data)
            validation_projection["rendered_evidence"] = generate_report_witnesses(
                validation_projection
            )
            validation_projection[
                "authorization_disclaimer_excerpt"
            ] = REPORT_AUTHORIZATION_DISCLAIMER
            return _validation_result(
                [], body=body, report_body_fingerprint=content_fingerprint(body),
                validation_projection=validation_projection,
            )
        if args.command in {"render-projection-sidecar", "render-projection-document"}:
            bundle_data = _load_json(args.bundle)
            if not isinstance(bundle_data, Mapping):
                return _validation_result(["--bundle must contain a JSON object"])
            if not args.replay_only:
                production_errors = _production_contract_errors(bundle_data)
                if production_errors:
                    return _validation_result(production_errors)
            migration_baseline = (
                load_immutable_migration_baseline()
                if bundle_data.get("write_transition") == "MIGRATION"
                else None
            )
            snapshot_pair = (
                args.previous_state_snapshot, args.previous_plan_snapshot
            )
            previous_baseline = None
            if migration_baseline is not None:
                if any(value is not None for value in snapshot_pair):
                    return _validation_result(["MIGRATION sidecar cannot use rev3 snapshots"])
            else:
                if not all(value is not None for value in snapshot_pair):
                    return _validation_result([
                        "non-MIGRATION sidecar requires both write-before revision3 snapshots"
                    ])
                previous_baseline = load_revision3_baseline(
                    *snapshot_pair, require_owner_paths=False
                )
            errors = validate_bundle(
                bundle_data, migration_baseline, previous_baseline,
                load_fact_owner_snapshot(),
                # D/E projection rendering intentionally consumes the
                # write-before snapshot, whose temporary path is not an Owner
                # path.  Its complete identity is still cross-bound inside the
                # candidate bundle and reloaded by validate_revision3_baseline.
                require_owner_baseline=False,
            )
            if errors:
                return _validation_result(errors)
            if args.command == "render-projection-document":
                document = render_projection_document(bundle_data, args.role)
                return _validation_result(
                    [], document=document,
                    document_content_fingerprint=content_fingerprint(document),
                )
            input_text = _load_text(args.input)
            expected_body = render_projection_body(bundle_data, args.role)
            if input_text != expected_body:
                return _validation_result([
                    "render-projection-sidecar input must equal deterministic projection body"
                ])
            block = render_projection_sidecar(
                bundle_data, input_text, args.role
            )
            return _validation_result([], sidecar_block=block)
        if args.command == "inspect-revision3-baseline":
            baseline = load_revision3_baseline(
                args.current_state, args.current_plan, require_owner_paths=True
            )
            return _validation_result(
                [],
                previous_projection_baseline=baseline.binding,
                previous_fingerprints={
                    "decision_fingerprint": baseline.binding["decision_fingerprint"],
                    "report_fingerprint": baseline.binding["report_fingerprint"],
                },
            )
    except (
        GuardError, json.JSONDecodeError, OSError, UnicodeError, TypeError,
        AttributeError,
        ValueError, OverflowError, RecursionError, InvalidOperation,
    ) as exc:
        return _validation_result([str(exc)])
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
