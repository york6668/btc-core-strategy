"""Versioned, evidence-bound interpretations; never a market classifier.

Hourly model reviews are stored separately. Only a completed deep assessment
may carry a material interpretation into a decision bundle. References prove
the existence and coverage of observations, not accumulation intent, a native
spot cause, or trade eligibility. Those remain distinct strategy contracts.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
import re
from typing import Any
from urllib.parse import urlsplit, parse_qsl


SCHEMA = "BTC_MATERIAL_PATTERN_V1"
KINDS = {
    "ABSORPTION", "POSSIBLE_DISTRIBUTION", "ACTIVE_SELLING",
    "DELEVERAGING", "LEVERAGED_RISE_OR_SHORT_COVER",
}
STAGES = {
    "FORMING", "ACCUMULATING", "STRENGTHENING", "WEAKENING",
    "REVERSED", "FALSIFIED", "UNRESOLVED",
}
CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}
IMPACTS = {"CAUSAL_EXPLANATION", "EXPECTATION_PATH", "RISK_ASSESSMENT"}
BINDING_KEYS = {"evidence_id", "review_id", "observed_hour", "context_id"}
PATTERN_KEYS = {
    "kind", "stage", "confidence", "support_refs", "counter_refs",
    "uncertainties", "next_evidence",
}
ATOM = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*\Z")
HASH = re.compile(r"sha256:[a-f0-9]{64}\Z")
PRICE_DIMENSIONS = {"price_quotes", "cross_exchange_acceptance"}
FLOW_DIMENSIONS = {"spot_flow", "derivatives_flow", "oi_timeline", "funding_basis"}
MARKET_DIMENSIONS = {
    "price_quotes", "cross_exchange_acceptance", "spot_flow", "derivatives_flow",
    "oi_timeline", "funding_basis", "capital_source", "macro", "relative_strength",
}
ACCEPTED_SCHEMA = "BTC_ACCEPTED_MARKET_ASSESSMENT_V1"


def validate_binding(binding: Any) -> list[str]:
    errors = []
    if not isinstance(binding, Mapping) or set(binding) != BINDING_KEYS:
        return ["hourly_review_binding must contain exactly evidence_id/review_id/observed_hour/context_id"]
    for key in BINDING_KEYS - {"observed_hour"}:
        if not isinstance(binding[key], str) or not HASH.fullmatch(binding[key]):
            errors.append(f"hourly_review_binding.{key} must be sha256")
    try:
        hour = datetime.fromisoformat(str(binding["observed_hour"]).replace("Z", "+00:00"))
        if hour.tzinfo is None or hour.minute or hour.second or hour.microsecond:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("hourly_review_binding.observed_hour must be a timezone-aware full hour")
    return errors


def load_hourly_review(binding: Any) -> Mapping[str, Any]:
    errors = validate_binding(binding)
    if errors:
        raise ValueError("; ".join(errors))
    # No import, DB access, or new default fields on the legacy v3 path.
    from market_observation import DEFAULT_DB, load_review_binding
    result = load_review_binding(dict(binding), db_path=DEFAULT_DB)
    if not isinstance(result, Mapping):
        raise ValueError("hourly review loader returned no bound evidence")
    model = result.get("review", {}).get("model", {})
    if model.get("name") not in {"gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol"} or model.get("reasoning_effort") != "high":
        raise ValueError("bound production hourly review requires Sol/high; historical model provenance is preserved")
    return result


def semantic(pattern: Any) -> dict[str, Any] | None:
    """Stable interpretation only: no IDs, timestamps, measurements or prose."""
    if pattern is None:
        return None
    if not isinstance(pattern, Mapping):
        raise ValueError("material_pattern must be an object")
    items = pattern.get("hypotheses")
    if not isinstance(items, list):
        raise ValueError("material_pattern.hypotheses must be a list")
    return {
        "schema": pattern.get("schema"),
        "hypotheses": sorted(({
            key: sorted(deepcopy(item.get(key, [])))
            if key in {"uncertainties", "next_evidence"} else deepcopy(item.get(key))
            for key in ("kind", "stage", "confidence", "uncertainties", "next_evidence")
        } for item in items if isinstance(item, Mapping)), key=lambda item: str(item["kind"])),
        "impact": sorted(deepcopy(pattern.get("impact", []))),
    }


def _atoms(value: Any, path: str, errors: list[str], *, nonempty: bool = False) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        errors.append(f"{path} must be {'non-empty ' if nonempty else ''}semantic code list")
        return
    if any(not isinstance(item, str) or not ATOM.fullmatch(item) for item in value):
        errors.append(f"{path} must contain stable semantic codes, not observations or prose")
    elif len(value) != len(set(value)):
        errors.append(f"{path} contains duplicate codes")


def validate_semantic(value: Any) -> list[str]:
    """Validate the public report's hidden semantic projection, without DB I/O."""
    errors: list[str] = []
    if not isinstance(value, Mapping) or set(value) != {"schema", "hypotheses", "impact"}:
        return ["material pattern semantic has an invalid shape"]
    if value.get("schema") != SCHEMA:
        errors.append("material pattern semantic schema is invalid")
    _atoms(value.get("impact"), "material pattern impact", errors, nonempty=True)
    if isinstance(value.get("impact"), list) and any(item not in IMPACTS for item in value["impact"]):
        errors.append("material pattern impact is invalid")
    items = value.get("hypotheses")
    if not isinstance(items, list) or not items:
        errors.append("material pattern hypotheses must be non-empty")
        return errors
    seen = set()
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {"kind", "stage", "confidence", "uncertainties", "next_evidence"}:
            errors.append("material pattern hypothesis semantic has an invalid shape")
            continue
        for key, allowed in (("kind", KINDS), ("stage", STAGES), ("confidence", CONFIDENCES)):
            if not isinstance(item.get(key), str) or item[key] not in allowed:
                errors.append(f"material pattern {key} is invalid")
        if isinstance(item.get("kind"), str):
            if item["kind"] in seen:
                errors.append("material pattern kind must be unique")
            seen.add(item["kind"])
        _atoms(item.get("uncertainties"), "material pattern uncertainties", errors)
        _atoms(item.get("next_evidence"), "material pattern next_evidence", errors, nonempty=True)
        if item.get("stage") == "UNRESOLVED" and not item.get("uncertainties"):
            errors.append("unresolved pattern must retain its material uncertainties")
    return errors


def validate_material_pattern(pattern: Any, bundle: Mapping[str, Any], *, model_market: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(pattern, Mapping) or set(pattern) != {"schema", "review_binding", "hypotheses", "impact"}:
        return ["causal_attribution.material_pattern must contain exactly schema/review_binding/hypotheses/impact"]
    errors.extend(validate_binding(pattern.get("review_binding")))
    try:
        errors.extend(validate_semantic(semantic(pattern)))
    except (ValueError, TypeError):
        return errors + ["material pattern semantic values are invalid"]
    matrix = bundle.get("evidence_matrix", {})
    for index, item in enumerate(pattern.get("hypotheses", [])):
        path = f"material_pattern.hypotheses[{index}]"
        if not isinstance(item, Mapping) or set(item) != PATTERN_KEYS:
            errors.append(f"{path} must contain exactly its interpretation and evidence fields")
            continue
        dimensions = set()
        for key in ("support_refs", "counter_refs"):
            refs = item.get(key)
            if not isinstance(refs, list) or (key == "support_refs" and not refs):
                errors.append(f"{path}.{key} must be a {'non-empty ' if key == 'support_refs' else ''}list")
                continue
            seen = set()
            for ref in refs:
                if not isinstance(ref, Mapping) or set(ref) != {"dimension", "ref_id"} or not all(isinstance(v, str) and v for v in ref.values()):
                    errors.append(f"{path}.{key} requires dimension-bound source or claim references")
                    continue
                identity = (ref["dimension"], ref["ref_id"])
                if identity in seen:
                    errors.append(f"{path}.{key} contains a duplicate reference")
                seen.add(identity)
                evidence = matrix.get(ref["dimension"], {}) if isinstance(matrix, Mapping) else {}
                if not isinstance(evidence, Mapping) or evidence.get("status") != "VERIFIED":
                    errors.append(f"{path}.{key} parent dimension must be VERIFIED; missing evidence belongs in uncertainties")
                    continue
                source = next((s for s in evidence.get("sources", []) if isinstance(s, Mapping) and s.get("source_id") == ref["ref_id"]), None)
                claim = next((c for c in evidence.get("evidence_claims", []) if isinstance(c, Mapping) and c.get("claim_id") == ref["ref_id"]), None)
                if source is None and (claim is None or claim.get("status") != "VERIFIED"):
                    errors.append(f"{path}.{key} reference is absent or unverified")
                    continue
                # Sources may describe a multiperiod dataset without claiming
                # a typed market cause. A source's as_of is not its coverage.
                window = claim.get("time_range") if claim else evidence.get("time_range")
                if key == "support_refs" and not model_market:
                    if not isinstance(window, Mapping) or window.get("kind") != "MULTI_PERIOD" or not window.get("start") or not window.get("end") or window["start"] == window["end"]:
                        errors.append(f"{path}.support_refs must bind real multiperiod observations")
                    else:
                        dimensions.add(ref["dimension"])
        if not model_market and (not dimensions.intersection(PRICE_DIMENSIONS) or not dimensions.intersection(FLOW_DIMENSIONS)):
            errors.append(f"{path} needs independent price-structure and flow/OI/funding measurement dimensions")
    return errors


def validate_retention(current: Any, previous: Any) -> list[str]:
    """Withdrawal is a FALSIFIED interpretation, not deletion of history."""
    if previous is None:
        return []
    prior_items = previous.get("hypotheses", []) if isinstance(previous, Mapping) else []
    current_items = current.get("hypotheses", []) if isinstance(current, Mapping) else []
    prior_kinds = {item.get("kind") for item in prior_items if isinstance(item, Mapping)}
    current_kinds = {item.get("kind") for item in current_items if isinstance(item, Mapping)}
    return (["previous material patterns cannot be silently removed; retain an explicit FALSIFIED interpretation"]
            if prior_kinds - current_kinds else [])


def validate_accepted_market_assessment(value: Any) -> list[str]:
    """Reject account/report payloads at the observation-store boundary.

    The adapter extracts only these fields *after* full FINAL verification.
    This shape check is not an alternative to validation of the source bundle.
    """
    errors: list[str] = []
    if not isinstance(value, Mapping) or set(value) != {"schema", "causal_semantics", "material_pattern", "evidence_refs"}:
        return ["accepted market assessment must contain exactly its market-only projection"]
    if value.get("schema") != ACCEPTED_SCHEMA:
        errors.append("accepted market assessment schema is invalid")
    causal = value.get("causal_semantics")
    keys = {"driver_regime", "driver_components", "selected_hypothesis_id", "mechanism", "decision_evidence_status", "next_evidence", "falsification_conditions"}
    if not isinstance(causal, Mapping) or set(causal) != keys:
        errors.append("accepted causal semantics has invalid fields")
    else:
        if causal["driver_regime"] not in {"UNRESOLVED", "CRYPTO_NATIVE_SPOT", "DERIVATIVES_LEVERAGE", "MACRO_BETA", "MIXED"}:
            errors.append("accepted driver regime is invalid")
        if causal["decision_evidence_status"] not in {"RESOLVED", "EVIDENCE_LIMITED", "UNRESOLVED"}:
            errors.append("accepted decision evidence status is invalid")
        if not isinstance(causal["selected_hypothesis_id"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]*", causal["selected_hypothesis_id"]):
            errors.append("accepted hypothesis identity must be a stable semantic code")
        _atoms(causal["driver_components"], "accepted driver components", errors)
        if isinstance(causal["driver_components"], list) and any(item not in {"CRYPTO_NATIVE_SPOT", "DERIVATIVES_LEVERAGE", "MACRO_BETA"} for item in causal["driver_components"]):
            errors.append("accepted driver components are invalid")
        for key in ("next_evidence", "falsification_conditions"):
            if not isinstance(causal[key], list) or not causal[key] or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]*", v) for v in causal[key]):
                errors.append("accepted conditions must be non-empty stable semantic codes")
        mechanisms = causal["mechanism"]
        if not isinstance(mechanisms, list):
            errors.append("accepted mechanism must be a list")
        else:
            for item in mechanisms:
                required = {"dimension", "event_role", "claim_type"}
                if not isinstance(item, Mapping) or not required <= set(item) or set(item) - required - {"direction", "subject_zone_id"}:
                    errors.append("accepted mechanism has invalid fields")
                    continue
                if item["dimension"] not in MARKET_DIMENSIONS or any(not isinstance(v, str) or not ATOM.fullmatch(v) for k, v in item.items() if k not in {"dimension", "subject_zone_id"}):
                    errors.append("accepted mechanism must be controlled market semantics")
                if "direction" in item and item["direction"] not in {"BUY", "SELL"}:
                    errors.append("accepted mechanism direction is invalid")
                if "subject_zone_id" in item and (not isinstance(item["subject_zone_id"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]*", item["subject_zone_id"])):
                    errors.append("accepted mechanism subject is invalid")
    if value.get("material_pattern") is not None:
        errors.extend(validate_semantic(value["material_pattern"]))
    evidence = value.get("evidence_refs")
    if not isinstance(evidence, Mapping) or set(evidence) != MARKET_DIMENSIONS:
        errors.append("accepted evidence must contain exactly the nine market dimensions, never account_impact")
        return errors
    for dimension, item in evidence.items():
        if not isinstance(item, Mapping) or set(item) != {"status", "references"}:
            errors.append("accepted evidence dimension has invalid fields")
            continue
        if item["status"] not in {"VERIFIED", "UNRESOLVED", "CONTRADICTED", "UNAVAILABLE"}:
            errors.append("accepted evidence status is invalid")
        refs = item["references"]
        if not isinstance(refs, list) or len(refs) > 100:
            errors.append("accepted evidence references must be a bounded list")
            continue
        for ref in refs:
            if not isinstance(ref, Mapping) or set(ref) != {"ref_id", "reference_type", "source_ids", "source_urls", "time_range"}:
                errors.append("accepted reference has invalid fields")
                continue
            if ref["reference_type"] not in {"SOURCE", "CLAIM"}:
                errors.append("accepted reference type is invalid")
            if not isinstance(ref["ref_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", ref["ref_id"]):
                errors.append("accepted reference identity is invalid")
            for key in ("source_ids", "source_urls"):
                if not isinstance(ref[key], list) or not ref[key] or any(not isinstance(v, str) or not v for v in ref[key]):
                    errors.append("accepted reference must contain public source identities and URLs")
            for url in ref["source_urls"] if isinstance(ref["source_urls"], list) else []:
                try:
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or any(k.lower() in {"token", "api_key", "apikey", "signature", "authorization", "cookie"} for k, _ in parse_qsl(parsed.query)):
                        raise ValueError
                except (ValueError, TypeError):
                    errors.append("accepted references must be anonymous public HTTPS URLs")
            window = ref["time_range"]
            if not isinstance(window, Mapping) or set(window) != {"kind", "start", "end"} or window.get("kind") not in {"POINT_IN_TIME", "MULTI_PERIOD"}:
                errors.append("accepted reference time range is invalid")
            else:
                try:
                    start, end = (datetime.fromisoformat(window[k].replace("Z", "+00:00")) for k in ("start", "end"))
                    if start.tzinfo is None or end.tzinfo is None or end < start or (window["kind"] == "MULTI_PERIOD" and end == start):
                        raise ValueError
                except (ValueError, TypeError, AttributeError):
                    errors.append("accepted reference time range is invalid")
    return errors
