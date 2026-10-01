"""Decision-bound, freely authored BTC reports; this module does not judge markets.

The eight sections separate stable ``semantic`` values from ``narrative``. The
former are projections of the already reviewed decision bundle, not a second
strategy engine. The latter contain the analyst's Chinese explanation. A change
in wording, evidence retrieval time or quote snapshot cannot change a report's
semantic fingerprint. Meaningful changes must be represented in the decision
bundle; this tool cannot detect Chinese paraphrases or establish causal truth.

Public API: build_report_projection, validate_report_projection,
semantic_projection, report_fingerprint, render_report, validate_report_text.
No file, account, network, state or delivery operations are performed here.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


FIELDS = (
    "overall_user_decision", "primary_causal_explanation",
    "rejected_competing_explanations", "portfolio_action_by_module",
    "material_uncertainties", "next_decision_changing_evidence",
    "user_visible_major_risk", "decision_relevant_levels_and_sizes",
)
MODULES = (
    "existing_digital_beta", "existing_alpha_short", "new_spot", "new_short",
    "optionality",
)
LABELS = dict(zip(FIELDS, (
    "综合判断", "主要解释", "暂未采用的解释", "组合影响", "关键不确定性",
    "会改变判断的下一证据", "重要风险", "当前相关价位与规模",
)))
MODULE_LABELS = dict(zip(MODULES, (
    "既有 Digital Beta", "既有 Alpha 空头", "新增现货", "新增空头", "可选性储备",
)))
DISCLAIMER = "这些判断仅供你评估，不代表成交或交易授权。"
SECTION_KEYS = {"semantic", "narrative"}
PARAGRAPH_KEYS = {"text", "verification", "evidence_refs", "subject"}
REF_KEYS = {"dimension", "ref_id", "source_urls"}
ACTION_KEYS = {
    "decision", "amount_u", "portfolio_role", "net_exposure_impact",
    "capital_pool_impact", "reason_class", "zero_amount_reason_class",
    "formation_conditions", "invalidation_conditions",
}
MONEY_KEYS = {"amount_u", "price_u", "lower_u", "upper_u"}
TOKEN = re.compile(r"\{\{(amount_u|price_u|lower_u|upper_u)\}\}")
LITERAL_MONEY = re.compile(r"(?<![\w}])\d[\d,]*(?:\.\d+)?\s*(?:USDT|USD|U|美元)(?![A-Za-z])", re.I)
SEMANTIC_KEY = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[_:\-][A-Za-z0-9]+)*\Z")
INTERNAL = re.compile(
    r"sha256:|\b(?:fingerprint|validation_projection|report_semantics|"
    r"monitor_state|task_role|attention_level|report_required|bundle_phase|"
    r"decision_class|state_id|plan_id|cycle_id|strategy_revision|dedupe_key|"
    r"reasoning_effort)\b|BTC-(?:STATE|CYCLE|CORE)-|\bBTC-\d{8}-|"
    r"\bgpt-\d[\w.\-]*\b|\b(?:terra|sol)\s*/\s*(?:low|medium|high|max)\b|"
    r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|A/D/E|内部控制面|推理等级|模型分配|"
    r"<!--|-->|```|::inbox-item|\{\s*\"",
)


class ReportError(ValueError):
    """A deterministic report binding, shape or delivery-boundary failure."""


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ReportError("money/price must be a finite non-negative decimal")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ReportError("money/price must be a decimal") from exc
    if not number.is_finite() or number < 0:
        raise ReportError("money/price must be finite and non-negative")
    return format(number.normalize(), "f") if number else "0"


def _canonical(value: Any, key: str = "") -> Any:
    if key in MONEY_KEYS:
        return _number(value)
    if isinstance(value, Mapping):
        return {k: _canonical(v, k) for k, v in sorted(value.items())}
    if isinstance(value, list):
        # All lists in the report's semantic projection are unordered sets.
        return sorted((_canonical(v) for v in value), key=_json)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ReportError("unsupported semantic type")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _body_fingerprint(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _selected(bundle: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(bundle, Mapping):
        return {}
    causal = bundle.get("causal_attribution", {})
    if not isinstance(causal, Mapping):
        return {}
    selected = causal.get("selected_hypothesis_id")
    hypotheses = causal.get("hypotheses", [])
    if not isinstance(hypotheses, list):
        return {}
    return next((h for h in hypotheses if isinstance(h, Mapping) and h.get("hypothesis_id") == selected), {})


def causal_mechanism_semantics(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Retain decision-relevant typed causes, never raw evidence or timing.

    Alias spelling is not a change of mechanism. Direction, subject and causal
    role are. Claim truth/timing validation remains the main bundle's concern.
    """
    if not isinstance(bundle, Mapping):
        return []
    matrix = bundle.get("evidence_matrix", {})
    if not isinstance(matrix, Mapping):
        return []
    mechanisms = {}
    timeline = _selected(bundle).get("causal_timeline", [])
    if not isinstance(timeline, list):
        return []
    for event in timeline:
        if not isinstance(event, Mapping):
            continue
        dimension = event.get("evidence_dimension")
        if not isinstance(dimension, str):
            continue
        item = matrix.get(dimension, {})
        if not isinstance(item, Mapping) or not isinstance(item.get("evidence_claims", []), list):
            continue
        claim = next((c for c in item.get("evidence_claims", []) if isinstance(c, Mapping) and c.get("claim_id") == event.get("evidence_ref")), None)
        if not claim:
            continue
        claim_type = claim.get("claim_type")
        if not isinstance(claim_type, str):
            continue
        mechanism = {"dimension": dimension, "event_role": event.get("event_role"), "claim_type": claim_type}
        if claim_type in {"NATIVE_SPOT_BUY_FLOW", "NATIVE_SPOT_BUY", "NATIVE_SPOT_SELL_FLOW", "NATIVE_SPOT_SELL", "NATIVE_SPOT_FLOW"}:
            mechanism["claim_type"] = "NATIVE_SPOT_FLOW"
            mechanism["direction"] = claim.get("direction") or ("BUY" if "BUY" in claim_type else "SELL" if "SELL" in claim_type else None)
        elif "direction" in claim:
            mechanism["direction"] = claim["direction"]
        if "subject_zone_id" in claim:
            mechanism["subject_zone_id"] = claim["subject_zone_id"]
        mechanisms[_json(mechanism)] = mechanism
    return [mechanisms[key] for key in sorted(mechanisms)]


def _level_semantics(bundle: Mapping[str, Any], level_ids: set[str]) -> list[dict[str, Any]]:
    levels = {}
    for zone in bundle.get("value_zones", []):
        levels[zone["zone_id"]] = {
            "level_id": zone["zone_id"], "kind": "VALUE_ZONE", "value_role": zone["role"],
            "lifecycle": zone["lifecycle"], "price_band_u": deepcopy(zone["band"]), "amount_u": "0",
        }
    for candidate in bundle.get("trade_candidates", []):
        for entry in candidate.get("entries", []):
            entry_id = entry["entry_id"]
            if entry_id in levels:
                raise ReportError("level identities must be globally unique")
            value = {
                "level_id": entry_id, "candidate_id": candidate["candidate_id"], "kind": candidate["kind"],
                "value_role": entry["value_role"], "eligibility": entry["eligibility"],
                "amount_u": entry["amount_u"], "amount_basis": deepcopy(entry["amount_basis"]),
                "formation_conditions": deepcopy(entry["formation_conditions"]),
                "invalidation_conditions": deepcopy(entry["invalidation_conditions"]),
                "evidence_confidence": entry["evidence_confidence"],
                "account_precondition": entry["account_precondition"],
                "carried_forward": entry.get("carried_forward", False),
            }
            value["amount_basis"].setdefault("continuation_reason", "CONTINUATION_NOT_APPLICABLE")
            for price_key in ("price_u", "price_band_u"):
                if price_key in entry:
                    value[price_key] = deepcopy(entry[price_key])
            levels[entry_id] = value
    unknown = level_ids - set(levels)
    if unknown:
        raise ReportError("visible levels do not belong to the decision bundle")
    return [levels[key] for key in sorted(level_ids)]


def _bundle_semantics(bundle: Mapping[str, Any], level_ids: set[str]) -> dict[str, Any]:
    causal = bundle["causal_attribution"]
    selected = _selected(bundle)
    hypotheses = {h["hypothesis_id"]: h for h in causal["hypotheses"]}
    rejected = []
    for identity in causal["rejected_hypothesis_ids"]:
        hypothesis = hypotheses[identity]
        rejected.append({
            "hypothesis_id": identity,
            "counter_evidence": deepcopy(hypothesis["counter_evidence"]),
            "missing_evidence": deepcopy(hypothesis["missing_evidence"]),
        })
    actions = {module: {key: deepcopy(bundle["portfolio_decision"][module].get(key) if key == "zero_amount_reason_class" else bundle["portfolio_decision"][module][key]) for key in ACTION_KEYS} for module in MODULES}
    semantics = {
        "overall_user_decision": {
            "path_decision": bundle["path_decision"],
            "module_decisions": {module: actions[module]["decision"] for module in MODULES},
        },
        "primary_causal_explanation": {
            "hypothesis_id": causal["selected_hypothesis_id"], "driver_regime": causal["driver_regime"],
            "driver_components": deepcopy(causal["driver_components"]),
            "decision_evidence_status": bundle["decision_evidence_status"],
            "mechanism": causal_mechanism_semantics(bundle),
        },
        "rejected_competing_explanations": rejected,
        "portfolio_action_by_module": actions,
        "material_uncertainties": [
            {"dimension": dimension, "status": item["status"]}
            for dimension, item in bundle["evidence_matrix"].items() if item["status"] != "VERIFIED"
        ],
        "next_decision_changing_evidence": {
            "hypothesis_id": causal["selected_hypothesis_id"],
            "conditions": deepcopy(selected["next_discriminating_evidence"]),
            "falsification_conditions": deepcopy(selected["falsification_conditions"]),
        },
        "user_visible_major_risk": deepcopy(bundle["major_risk"]),
        "decision_relevant_levels_and_sizes": _level_semantics(bundle, level_ids),
    }
    # Absence preserves every byte of the legacy semantic projection. Evidence
    # provenance is validated in the bundle but never creates report churn.
    if "material_pattern" in causal:
        from pattern_semantics import semantic as pattern_semantic
        material = pattern_semantic(causal["material_pattern"])
        semantics["primary_causal_explanation"]["material_pattern"] = material
        semantics["material_uncertainties"].extend({
            "pattern_kind": item["kind"], "conditions": item["uncertainties"],
        } for item in material["hypotheses"] if item["uncertainties"])
        semantics["next_decision_changing_evidence"]["material_pattern"] = [
            {"kind": item["kind"], "conditions": item["next_evidence"]}
            for item in material["hypotheses"]
        ]
    return _canonical(semantics)


def _reference(bundle: Mapping[str, Any], dimension: str, ref_id: str) -> dict[str, Any]:
    item = bundle.get("evidence_matrix", {}).get(dimension, {})
    sources = {source.get("source_id"): source for source in item.get("sources", [])}
    if ref_id in sources:
        selected = [sources[ref_id]]
    else:
        claim = next((claim for claim in item.get("evidence_claims", []) if claim.get("claim_id") == ref_id), None)
        if not claim or claim.get("status") != "VERIFIED":
            raise ReportError("narrative evidence reference is absent or not verified")
        selected = [sources[source_id] for source_id in claim["source_ids"]]
    if item.get("status") != "VERIFIED":
        raise ReportError("evidence-linked narrative needs a verified parent dimension")
    return {"dimension": dimension, "ref_id": ref_id, "source_urls": sorted({source["endpoint_url"] for source in selected if "endpoint_url" in source})}


def build_report_projection(
    bundle: Mapping[str, Any], narratives: Mapping[str, Any], *,
    visible_level_ids: set[str] | list[str] | None = None,
) -> dict[str, Any]:
    """Bind reviewed narrative to bundle facts; never invent its explanation.

    Narrative refs may initially contain only dimension/ref_id; public source
    URLs are filled from the bundle. Paragraph ``subject`` binds module, rejected
    hypothesis or level identity and is never displayed. Monetary mentions use
    {{amount_u}}, {{price_u}}, {{lower_u}} or {{upper_u}} in that subject's prose.
    Stable counter/missing/next-condition values in the bundle denote decisions,
    not report wording; an analyst, not this code, owns semantic equivalence.
    """
    if set(narratives) != set(FIELDS):
        raise ReportError("narratives must cover exactly eight report sections")
    if visible_level_ids is None:
        visible_level_ids = {
            entry["entry_id"] for candidate in bundle.get("trade_candidates", [])
            for entry in candidate.get("entries", []) if entry.get("eligibility") == "ELIGIBLE_FOR_REVIEW"
        }
    semantics = _bundle_semantics(bundle, set(visible_level_ids))
    projection = {}
    for field in FIELDS:
        paragraphs = deepcopy(narratives[field])
        for paragraph in paragraphs:
            paragraph.setdefault("evidence_refs", [])
            paragraph["evidence_refs"] = [_reference(bundle, ref["dimension"], ref["ref_id"]) for ref in paragraph["evidence_refs"]]
        projection[field] = {"semantic": semantics[field], "narrative": paragraphs}
    errors = validate_report_projection(projection, bundle)
    if errors:
        raise ReportError("; ".join(errors))
    return projection


def is_natural_projection(value: Any) -> bool:
    return isinstance(value, Mapping) and all(isinstance(value.get(field), Mapping) and set(value[field]) == SECTION_KEYS for field in FIELDS)


def semantic_projection(projection: Mapping[str, Any]) -> dict[str, Any]:
    if not is_natural_projection(projection):
        raise ReportError("natural report projection requires eight semantic/narrative sections")
    return _canonical({field: projection[field]["semantic"] for field in FIELDS})


def report_fingerprint(projection: Mapping[str, Any]) -> str:
    return _fingerprint(semantic_projection(projection))


def _exact(value: Any, keys: set[str], context: str, errors: list[str]) -> bool:
    if not isinstance(value, Mapping) or set(value) != keys:
        errors.append(context + " has invalid keys/type")
        return False
    return True


def _strings(value: Any, context: str, errors: list[str], *, empty: bool = True) -> None:
    if not isinstance(value, list) or (not empty and not value) or any(not isinstance(v, str) or not v.strip() for v in value):
        errors.append(context + " must be a list of stable non-empty semantic keys")
    elif len(set(value)) != len(value):
        errors.append(context + " has duplicated semantic keys")


def _semantic_atoms(value: Any, errors: list[str], key: str = "") -> None:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _semantic_atoms(child, errors, child_key)
    elif isinstance(value, list):
        for child in value:
            _semantic_atoms(child, errors, key)
    elif key in MONEY_KEYS:
        _number(value)
    elif isinstance(value, bool) and key in {"active", "carried_forward"}:
        return
    elif value is None and key == "zero_amount_reason_class":
        return
    elif not isinstance(value, str) or not SEMANTIC_KEY.fullmatch(value) or re.search(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        errors.append("semantic values must use stable keys, not prose, timestamps or quote snapshots")


def _shape(semantics: Mapping[str, Any], errors: list[str]) -> None:
    overall = semantics["overall_user_decision"]
    if _exact(overall, {"path_decision", "module_decisions"}, "overall decision", errors):
        _exact(overall["module_decisions"], set(MODULES), "overall module decisions", errors)
    primary = semantics["primary_causal_explanation"]
    primary_keys = {"hypothesis_id", "driver_regime", "driver_components", "decision_evidence_status", "mechanism"}
    if isinstance(primary, Mapping) and "material_pattern" in primary:
        primary_keys.add("material_pattern")
        from pattern_semantics import validate_semantic
        errors.extend(validate_semantic(primary["material_pattern"]))
    if _exact(primary, primary_keys, "primary explanation", errors):
        _strings(primary["driver_components"], "driver components", errors)
        if not isinstance(primary["mechanism"], list):
            errors.append("causal mechanism must be a typed list")
        else:
            for item in primary["mechanism"]:
                required = {"dimension", "event_role", "claim_type"}
                if not isinstance(item, Mapping) or not required <= set(item) or set(item) - required - {"direction", "subject_zone_id"}:
                    errors.append("invalid typed causal mechanism")
                elif "direction" in item and item["direction"] not in {"BUY", "SELL"}:
                    errors.append("invalid causal direction")
    rejected = semantics["rejected_competing_explanations"]
    if not isinstance(rejected, list):
        errors.append("rejected explanations must be a list")
    else:
        for item in rejected:
            if _exact(item, {"hypothesis_id", "counter_evidence", "missing_evidence"}, "rejected explanation", errors):
                _strings(item["counter_evidence"], "counter evidence", errors)
                _strings(item["missing_evidence"], "missing evidence", errors)
    actions = semantics["portfolio_action_by_module"]
    if _exact(actions, set(MODULES), "portfolio", errors):
        for module, action in actions.items():
            if _exact(action, ACTION_KEYS, module, errors):
                _number(action["amount_u"])
                for key in ("formation_conditions", "invalidation_conditions"):
                    _strings(action[key], module + "." + key, errors, empty=False)
                if isinstance(overall, Mapping) and isinstance(overall.get("module_decisions"), Mapping) and overall["module_decisions"].get(module) != action["decision"]:
                    errors.append("overall decision conflicts with module decision")
    uncertainties = semantics["material_uncertainties"]
    if not isinstance(uncertainties, list):
        errors.append("uncertainties must be a list")
    else:
        for item in uncertainties:
            if isinstance(item, Mapping) and "pattern_kind" in item:
                if _exact(item, {"pattern_kind", "conditions"}, "pattern uncertainty", errors):
                    from pattern_semantics import KINDS
                    if item["pattern_kind"] not in KINDS:
                        errors.append("pattern uncertainty kind is invalid")
                    _strings(item["conditions"], "pattern uncertainty conditions", errors, empty=False)
            elif _exact(item, {"dimension", "status"}, "uncertainty", errors) and item["status"] not in {"UNRESOLVED", "UNAVAILABLE", "CONTRADICTED"}:
                errors.append("uncertainty has invalid evidence status")
    next_evidence = semantics["next_decision_changing_evidence"]
    next_keys = {"hypothesis_id", "conditions", "falsification_conditions"}
    if isinstance(next_evidence, Mapping) and "material_pattern" in next_evidence:
        next_keys.add("material_pattern")
        items = next_evidence["material_pattern"]
        if not isinstance(items, list) or not items:
            errors.append("next pattern evidence must be a non-empty list")
        else:
            from pattern_semantics import KINDS
            for item in items:
                if _exact(item, {"kind", "conditions"}, "next pattern evidence", errors):
                    if item["kind"] not in KINDS:
                        errors.append("next pattern evidence kind is invalid")
                    _strings(item["conditions"], "next pattern conditions", errors, empty=False)
    if _exact(next_evidence, next_keys, "next evidence", errors):
        _strings(next_evidence["conditions"], "next conditions", errors, empty=False)
        _strings(next_evidence["falsification_conditions"], "falsification conditions", errors, empty=False)
    if isinstance(primary, Mapping) and "material_pattern" in primary:
        material = primary["material_pattern"]
        if isinstance(material, Mapping) and isinstance(material.get("hypotheses"), list):
            expected_uncertainties = [{"pattern_kind": item["kind"], "conditions": item["uncertainties"]}
                                      for item in material["hypotheses"] if isinstance(item, Mapping) and item.get("uncertainties")]
            actual_uncertainties = [item for item in uncertainties if isinstance(item, Mapping) and "pattern_kind" in item] if isinstance(uncertainties, list) else []
            expected_next = [{"kind": item.get("kind"), "conditions": item.get("next_evidence")}
                             for item in material["hypotheses"] if isinstance(item, Mapping)]
            if _canonical(expected_uncertainties) != _canonical(actual_uncertainties):
                errors.append("material pattern uncertainties must match its primary interpretation")
            if not isinstance(next_evidence, Mapping) or _canonical(next_evidence.get("material_pattern")) != _canonical(expected_next):
                errors.append("material pattern next evidence must match its primary interpretation")
    elif (isinstance(next_evidence, Mapping) and "material_pattern" in next_evidence) or (isinstance(uncertainties, list) and any(isinstance(item, Mapping) and "pattern_kind" in item for item in uncertainties)):
        errors.append("pattern uncertainty/next evidence requires a primary material interpretation")
    risk = semantics["user_visible_major_risk"]
    if _exact(risk, {"active", "kinds", "portfolio_effect"}, "major risk", errors):
        if not isinstance(risk["active"], bool):
            errors.append("risk active must be boolean")
        _strings(risk["kinds"], "risk kinds", errors)
    levels = semantics["decision_relevant_levels_and_sizes"]
    if not isinstance(levels, list):
        errors.append("levels must be a list")
        return
    seen = set()
    for level in levels:
        if not isinstance(level, Mapping):
            errors.append("level must be a typed object")
            continue
        base = {"level_id", "kind", "value_role", "amount_u"}
        is_zone = level.get("kind") == "VALUE_ZONE"
        expected = base | ({"lifecycle"} if is_zone else {
            "candidate_id", "eligibility", "amount_basis", "formation_conditions", "invalidation_conditions",
            "evidence_confidence", "account_precondition", "carried_forward",
        })
        prices = set(level) & {"price_u", "price_band_u"}
        if len(prices) != 1 or (is_zone and prices != {"price_band_u"}) or set(level) != expected | prices:
            errors.append("level must use its exact typed shape and one price representation")
            continue
        identity = level["level_id"]
        if not isinstance(identity, str) or not identity or identity in seen:
            errors.append("level identities must be non-empty and unique")
        else:
            seen.add(identity)
        if "price_band_u" in level:
            band = level["price_band_u"]
            if _exact(band, {"lower_u", "upper_u"}, "level band", errors) and Decimal(_number(band["lower_u"])) >= Decimal(_number(band["upper_u"])):
                errors.append("level lower bound must be below upper bound")
        elif Decimal(_number(level["price_u"])) <= 0:
            errors.append("price must be positive")
        _number(level["amount_u"])
        if is_zone and Decimal(_number(level["amount_u"])) != 0:
            errors.append("a value zone is not an allocated amount")
        if not is_zone:
            if not isinstance(level["carried_forward"], bool):
                errors.append("carried_forward must be boolean")
            _exact(level["amount_basis"], {"budget_source", "net_exposure_constraint", "value_gap_rationale", "risk_assumption", "continuation_reason"}, "amount basis", errors)
            for key in ("formation_conditions", "invalidation_conditions"):
                _strings(level[key], "level " + key, errors, empty=False)


def _subject_value(field: str, section: Mapping[str, Any], paragraph: Mapping[str, Any]) -> Mapping[str, Any]:
    semantic = section["semantic"]
    if field == "portfolio_action_by_module":
        return semantic.get(paragraph.get("subject"), {})
    if field == "decision_relevant_levels_and_sizes":
        return next((level for level in semantic if level.get("level_id") == paragraph.get("subject")), {})
    return {}


def _paragraph_text(field: str, section: Mapping[str, Any], paragraph: Mapping[str, Any]) -> str:
    subject = _subject_value(field, section, paragraph)
    values = dict(subject)
    values.update(subject.get("price_band_u", {}))
    def substitute(match: re.Match) -> str:
        if match[1] not in values:
            raise ReportError("numeric placeholder is not bound to this module/level")
        return _number(values[match[1]])
    return TOKEN.sub(substitute, paragraph["text"])


def _validate_narrative(field: str, section: Mapping[str, Any], bundle: Mapping[str, Any] | None, errors: list[str]) -> None:
    paragraphs = section["narrative"]
    if not isinstance(paragraphs, list):
        errors.append(field + " narrative must be a list")
        return
    if not paragraphs and section["semantic"] != []:
        errors.append(field + " needs a natural-language explanation")
    subjects = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, Mapping) or not {"text", "verification", "evidence_refs"} <= set(paragraph) or set(paragraph) - PARAGRAPH_KEYS:
            errors.append("paragraph has invalid shape")
            continue
        text = paragraph["text"]
        if not isinstance(text, str) or not text.strip():
            errors.append("paragraph must contain natural-language text")
            continue
        if INTERNAL.search(text) or any(field_name in text for field_name in FIELDS):
            errors.append("narrative leaks internal control data")
        if LITERAL_MONEY.search(text):
            errors.append("monetary amounts/prices must use subject-bound numeric placeholders")
        if field == "portfolio_action_by_module" and "{{amount_u}}" not in text:
            errors.append("each module must disclose its bound suggested amount")
        if field == "decision_relevant_levels_and_sizes" and section["semantic"]:
            subject_value = _subject_value(field, section, paragraph)
            required_tokens = {"lower_u", "upper_u"} if "price_band_u" in subject_value else {"price_u"}
            if subject_value.get("kind") != "VALUE_ZONE":
                required_tokens.add("amount_u")
            if not required_tokens <= set(TOKEN.findall(text)):
                errors.append("each relevant level must disclose its bound price and candidate amount")
        rendered = _paragraph_text(field, section, paragraph)
        if "{{" in rendered or "}}" in rendered:
            errors.append("unknown or malformed numeric placeholder")
        refs = paragraph["evidence_refs"]
        if not isinstance(refs, list):
            errors.append("evidence_refs must be a list")
            continue
        if paragraph["verification"] not in {"EVIDENCE_LINKED", "UNVERIFIED"}:
            errors.append("paragraph must declare evidence-linked or unverified")
        if paragraph["verification"] == "EVIDENCE_LINKED" and not refs:
            errors.append("evidence-linked paragraph requires an actual reference")
        for ref in refs:
            if not _exact(ref, REF_KEYS, "evidence ref", errors):
                continue
            if not all(isinstance(ref[key], str) and ref[key] for key in ("dimension", "ref_id")):
                errors.append("evidence ref needs dimension and identity")
            urls = ref["source_urls"]
            if not isinstance(urls, list):
                errors.append("source_urls must be a list")
                continue
            for url in urls:
                if not isinstance(url, str):
                    errors.append("source URL must be text")
                    continue
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or any(c in url for c in "\n\r<> "):
                    errors.append("source URL must be a plain public HTTPS reference")
            if bundle is not None and ref != _reference(bundle, ref["dimension"], ref["ref_id"]):
                errors.append("narrative reference does not bind actual bundle evidence")
        if "subject" in paragraph:
            subjects.append(paragraph["subject"])
            if isinstance(paragraph["subject"], str) and re.search(r"(?<![\w])" + re.escape(paragraph["subject"]) + r"(?![\w])", text):
                errors.append("narrative must not expose internal subject identity")
    expected = None
    if field == "portfolio_action_by_module":
        expected = set(MODULES)
    elif field == "rejected_competing_explanations":
        expected = {item["hypothesis_id"] for item in section["semantic"]}
    elif field == "decision_relevant_levels_and_sizes" and section["semantic"]:
        expected = {item["level_id"] for item in section["semantic"]}
    if expected is not None and (set(subjects) != expected or len(subjects) != len(set(subjects))):
        errors.append(field + " must explain each bound subject exactly once")
    pattern_subjects = None
    if field == "primary_causal_explanation" and isinstance(section["semantic"], Mapping):
        material = section["semantic"].get("material_pattern")
        if isinstance(material, Mapping):
            pattern_subjects = {item["kind"] for item in material.get("hypotheses", []) if isinstance(item, Mapping) and "kind" in item}
    elif field == "material_uncertainties" and isinstance(section["semantic"], list):
        kinds = {item["pattern_kind"] for item in section["semantic"] if isinstance(item, Mapping) and "pattern_kind" in item}
        if kinds:
            pattern_subjects = kinds
    elif field == "next_decision_changing_evidence" and isinstance(section["semantic"], Mapping) and "material_pattern" in section["semantic"]:
        pattern_subjects = {item["kind"] for item in section["semantic"]["material_pattern"] if isinstance(item, Mapping) and "kind" in item}
    if pattern_subjects is not None:
        if set(subjects) != pattern_subjects or len(subjects) != len(set(subjects)):
            errors.append(field + " must separately explain every material pattern subject exactly once")
        if field == "primary_causal_explanation":
            for paragraph in paragraphs:
                if not isinstance(paragraph, Mapping) or paragraph.get("subject") not in pattern_subjects:
                    continue
                if paragraph.get("verification") != "EVIDENCE_LINKED":
                    errors.append("material pattern explanation must cite its measured evidence and remain an interpretation")
                if bundle is not None:
                    material_items = bundle.get("causal_attribution", {}).get("material_pattern", {}).get("hypotheses", [])
                    hypothesis = next((item for item in material_items if item.get("kind") == paragraph["subject"]), {})
                    permitted = {(ref["dimension"], ref["ref_id"]) for ref in hypothesis.get("support_refs", []) + hypothesis.get("counter_refs", [])}
                    actual = {(ref.get("dimension"), ref.get("ref_id")) for ref in paragraph.get("evidence_refs", []) if isinstance(ref, Mapping)}
                    if not actual or not actual <= permitted:
                        errors.append("material pattern narrative must bind its actual support/counter observations")


def validate_report_projection(
    projection: Any, bundle: Mapping[str, Any] | None = None, *,
    required_visible_level_ids: set[str] | None = None,
) -> list[str]:
    """Check types, identity/number/evidence bindings and output boundaries only.

    A successful result is *not* causal verification, language equivalence,
    narrative completeness in a financial sense, or permission to deliver.
    Main-bundle validation and analyst semantic review remain prerequisites.
    """
    errors: list[str] = []
    if not isinstance(projection, Mapping) or set(projection) != set(FIELDS) or not is_natural_projection(projection):
        return ["report projection must contain exactly eight semantic/narrative sections"]
    try:
        semantics = semantic_projection(projection)
        _shape(semantics, errors)
        _semantic_atoms(semantics, errors)
        if errors:
            return errors
        level_ids = {level["level_id"] for level in semantics["decision_relevant_levels_and_sizes"]}
        if required_visible_level_ids is not None and not required_visible_level_ids <= level_ids:
            errors.append("report omits a required decision-relevant level")
        if bundle is not None and semantics != _bundle_semantics(bundle, level_ids):
            errors.append("report semantic projection does not bind the current decision bundle")
        for field in FIELDS:
            _validate_narrative(field, projection[field], bundle, errors)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        errors.append("invalid report projection: " + str(exc))
    return errors


def _render_body(projection: Mapping[str, Any]) -> str:
    sections = []
    for field in FIELDS:
        paragraphs = []
        for paragraph in projection[field]["narrative"]:
            text = _paragraph_text(field, projection[field], paragraph)
            if field == "portfolio_action_by_module":
                text = MODULE_LABELS[paragraph["subject"]] + "：" + text
            urls = sorted({url for ref in paragraph["evidence_refs"] for url in ref["source_urls"]})
            if urls:
                text += "\n\n依据：" + "、".join(f"[公开来源 {index}](<{url}>)" for index, url in enumerate(urls, 1)) + "。"
            elif paragraph["evidence_refs"]:
                text += "（依据：本地已确认记录。）"
            if paragraph["verification"] == "UNVERIFIED":
                text += "（此项解释尚未验证。）"
            paragraphs.append(text)
        if paragraphs:
            sections.append("## " + LABELS[field] + "\n\n" + "\n\n".join(paragraphs))
    return "\n\n".join(sections) + "\n\n" + DISCLAIMER


def render_report(projection: Mapping[str, Any], bundle: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Freeze authored prose only after structural and optional bundle binding."""
    errors = validate_report_projection(projection, bundle)
    if errors:
        raise ReportError("; ".join(errors))
    body = _render_body(projection)
    validation = deepcopy(dict(projection))
    validation["rendered_evidence"] = {
        "frozen_body": body, "report_body_fingerprint": _body_fingerprint(body),
        "report_fingerprint": report_fingerprint(projection),
    }
    validation["authorization_disclaimer_excerpt"] = DISCLAIMER
    return {"ok": True, "hard_failure": False, "errors": [], "body": body,
            "report_body_fingerprint": _body_fingerprint(body), "validation_projection": validation}


def render_report_body(projection: Mapping[str, Any]) -> str:
    return render_report(projection)["body"]


def validate_report_text(body: Any, projection: Any, bundle: Mapping[str, Any] | None = None) -> list[str]:
    """Verify exact frozen body, including every whitespace and newline byte."""
    if not isinstance(body, str) or not body:
        return ["report body must be non-empty UTF-8 text"]
    if not isinstance(projection, Mapping) or set(projection) != set(FIELDS) | {"rendered_evidence", "authorization_disclaimer_excerpt"}:
        return ["validation projection must be the renderer's frozen envelope"]
    report_projection = {field: projection[field] for field in FIELDS}
    errors = validate_report_projection(report_projection, bundle)
    if errors:
        return errors
    expected = render_report(report_projection, bundle)
    if body != expected["body"]:
        errors.append("report body differs from the exact frozen report")
    if projection != expected["validation_projection"]:
        errors.append("validation projection or frozen body was altered")
    return errors


def normalize_legacy_report_semantics(legacy_projection: Mapping[str, Any]) -> dict[str, Any]:
    """Expose what the immutable legacy report does and does not establish.

    Legacy action labels do not themselves supply typed amount/role/impact
    records, and its reason labels are not automatic synonyms for new evidence
    keys. A separately reviewed bridge from the actual old report/state/plan is
    required. Do not compare the returned value's hash with a new report hash.
    """
    legacy = legacy_projection.get("report_semantics", legacy_projection)
    if not isinstance(legacy, Mapping) or set(legacy) != set(FIELDS):
        raise ReportError("legacy report must expose exactly eight semantic fields")
    if is_natural_projection(legacy):
        return {"comparison_projection": semantic_projection(legacy), "unavailable_fields": [], "complete": True}
    return {
        "comparison_projection": _canonical(deepcopy(dict(legacy))),
        "unavailable_fields": [
            "overall_user_decision.path_decision",
            "primary_causal_explanation.mechanism",
            "rejected_competing_explanations.counter_evidence_and_missing_evidence",
            "portfolio_action_by_module.typed_amount_role_impacts_and_conditions",
            "material_uncertainties.dimension_status",
            "next_decision_changing_evidence.typed_conditions",
            "user_visible_major_risk.typed_effect",
            "decision_relevant_levels_and_sizes.role_and_commitments",
        ],
        "complete": False,
    }
