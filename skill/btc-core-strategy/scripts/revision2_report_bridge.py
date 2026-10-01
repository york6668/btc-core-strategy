"""A one-time, evidence-audited codec for the four immutable revision-2 files.

This is not a market inference engine and accepts no current decision bundle.
Every call rechecks the historical bytes before recovering comparison semantics.
The audit envelope is internal and never part of a report or its fingerprint.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

import decision_report


import node_config
FIXTURE_DIRECTORY = node_config.legacy_directory()
_PROFILE = node_config.legacy_profile()
ARTIFACTS = _PROFILE.get("artifacts", {})
CODEC = _PROFILE.get("codec", "BTC_REVISION2_1346_REPORT_COMPARISON_V1")
UNRESOLVED_DIMENSIONS = (
    "cross_exchange_acceptance", "spot_flow", "derivatives_flow", "oi_timeline",
    "funding_basis", "capital_source", "macro", "relative_strength",
)
# This mask is fixed by the historical audit, never by the current report.
# Changes to it require reviewing this one-time codec's source witnesses.
HISTORICAL_UNKNOWN_PATHS = tuple(sorted(
    [f"portfolio_action_by_module.{module}.portfolio_role" for module in (
        "existing_digital_beta", "existing_alpha_short", "new_short", "optionality",
    )]
    + [f"portfolio_action_by_module.{module}.{field}" for module in decision_report.MODULES
       for field in ("zero_amount_reason_class", "invalidation_conditions")]
))
CODEC_COMPARISON_LIMITATIONS = (
    "旧反证与缺失证据使用聚合codec，不能机械判定任意新语义键与其同义。",
    "旧下一鉴别条件使用跨所现货和持仓量时序codec；仅改成另一命名也会报告差异，须由主任务审阅而非自动忽略。",
    "比较只覆盖旧已知语义；历史缺失字段的补齐不是已证实相同，也不得用其当前值反填历史指纹。",
)


class BridgeError(ValueError):
    """The historical evidence no longer matches this one-time codec."""


def _read_checked() -> dict[str, str]:
    texts = {}
    for artifact, (filename, expected) in ARTIFACTS.items():
        raw = (FIXTURE_DIRECTORY / filename).read_bytes()
        actual = "sha256:" + hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise BridgeError("immutable revision2 artifact changed: " + artifact)
        texts[artifact] = raw.decode("utf-8")
    return texts


def _source(texts: dict[str, str], artifact: str, quote: str) -> dict[str, Any]:
    text = texts[artifact]
    start = text.find(quote)
    if start < 0:
        raise BridgeError("audit quote is absent from immutable " + artifact)
    return {
        "artifact": ARTIFACTS[artifact][0],
        "line_start": text[:start].count("\n") + 1,
        "line_end": text[:start + len(quote)].count("\n") + 1,
        "quote": quote,
    }


def _witness(texts: dict[str, str], sources: list[tuple[str, str]], interpretation: str, *, inference_kind: str = "HISTORICAL_SEMANTIC_CODEC") -> dict[str, Any]:
    return {
        "sources": [_source(texts, artifact, quote) for artifact, quote in sources],
        "inference_kind": inference_kind,
        "interpretation": interpretation,
    }


def bridge_artifact() -> dict[str, Any]:
    """Return pure eight-dimensional semantics plus byte-bound audit witnesses.

    Zero is *suggested incremental adjustment*, never actual position, cash,
    available budget or account delta. The old public comparison projection
    selects V0, although the old prose also contained other conditional levels.
    Missing purpose/conditions stay explicit legacy unknowns, not fabricated
    agreements with a new bundle. A caller must review a real change in such
    information rather than silently declaring it equivalent.
    """
    if set(ARTIFACTS) != {"state", "plan", "report", "report_projection"}:
        raise BridgeError("local revision-2 archive is not configured")
    texts = _read_checked()
    artifact = deepcopy(_PROFILE["bridge"])
    if artifact["codec"] != CODEC or artifact["artifact_fingerprints"] != {
        name: entry[1] for name, entry in ARTIFACTS.items()
    }:
        raise BridgeError("legacy profile artifact binding mismatch")
    # The node preserves the audited interpretation and its exact source spans.
    # A code upgrade never reconstructs historical meaning from a current report.
    if set(artifact["audit_witness"]) != set(decision_report.FIELDS):
        raise BridgeError("legacy audit must cover exactly eight fields")
    filenames = {entry[0]: name for name, entry in ARTIFACTS.items()}
    for witness in artifact["audit_witness"].values():
        if not witness.get("sources") or not witness.get("interpretation"):
            raise BridgeError("legacy source witness is missing")
        for source in witness["sources"]:
            text = texts[filenames[source["artifact"]]]
            span = "\n".join(text.splitlines()[source["line_start"] - 1:source["line_end"]])
            if not source["quote"] or source["quote"] not in span:
                raise BridgeError("legacy source witness mismatch")
    artifact["semantic_projection"] = _canonical_pure_semantics(artifact["semantic_projection"])
    return artifact


def bridge_semantics() -> dict[str, Any]:
    """Return only the eight canonical values; no metadata or audit in the hash."""
    return deepcopy(bridge_artifact()["semantic_projection"])


def _canonical_pure_semantics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(decision_report.FIELDS):
        raise BridgeError("migration comparison requires exactly eight pure semantic fields")
    wrappers = {field: {"semantic": value[field], "narrative": []} for field in decision_report.FIELDS}
    try:
        canonical = decision_report.semantic_projection(wrappers)
        errors: list[str] = []
        # These checks inspect typed semantics, not the absent historical prose.
        decision_report._shape(canonical, errors)
        decision_report._semantic_atoms(canonical, errors)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise BridgeError("invalid migration comparison semantics: " + str(exc)) from exc
    if errors:
        raise BridgeError("invalid migration comparison semantics: " + "; ".join(errors))
    return canonical


def _without_historical_unknowns(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    for path in HISTORICAL_UNKNOWN_PATHS:
        parts = path.split(".")
        parent = result
        for part in parts[:-1]:
            parent = parent[part]
        # Delete on both sides; never copy the new value into the old side.
        del parent[parts[-1]]
    return result


def _known_differences(previous: Any, current: Any, path: str = "") -> list[dict[str, Any]]:
    if previous == current:
        return []
    if isinstance(previous, Mapping) and isinstance(current, Mapping):
        differences = []
        for key in sorted(set(previous) | set(current)):
            child_path = path + "." + key if path else key
            if key not in previous or key not in current:
                differences.append({"path": child_path, "previous": previous.get(key), "current": current.get(key)})
            else:
                differences.extend(_known_differences(previous[key], current[key], child_path))
        return differences
    # Lists are canonical unordered semantic sets; report their complete change
    # instead of suggesting that their sorted array indexes are stable identities.
    return [{"path": path, "previous": deepcopy(previous), "current": deepcopy(current)}]


def _comparison_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def migration_compare(current_pure_semantics: Mapping[str, Any]) -> dict[str, Any]:
    """Compare known historical decisions for this one migration only.

    Both returned fingerprints hash the same fixed *masked comparison view*.
    They are not the complete production report fingerprints and must not be
    persisted as such after migration. ``changed=False`` means no change in the
    recoverable old meaning, not proof of equivalence for its missing fields.

    Stable-key spelling changes in rejected/next evidence are deliberately not
    guessed to be synonyms. See CODEC_COMPARISON_LIMITATIONS and the raw witnesses
    in bridge_artifact(); differences in those fields require semantic review.
    """
    historical = bridge_artifact()  # reads/checks four immutable files first
    if tuple(historical["explicit_unknowns"]) != HISTORICAL_UNKNOWN_PATHS:
        raise BridgeError("historical unknown mask no longer matches the audited codec")
    previous = _canonical_pure_semantics(historical["semantic_projection"])
    current = _canonical_pure_semantics(current_pure_semantics)
    previous_masked = _without_historical_unknowns(previous)
    current_masked = _without_historical_unknowns(current)
    differences = _known_differences(previous_masked, current_masked)
    return {
        "changed": bool(differences),
        "known_differences": differences,
        "historical_unknown_paths": list(HISTORICAL_UNKNOWN_PATHS),
        "previous_report_fingerprint": _comparison_fingerprint(previous_masked),
        "current_report_fingerprint": _comparison_fingerprint(current_masked),
    }
