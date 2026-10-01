"""Consume local collector exports without acquiring market decision authority.

The collector owns evidence validation.  This module only handles local input
and the observation boundary; it never fetches, opens a reader database, imports
credentials, writes a strategy artifact, or grants independent verification.
"""
from __future__ import annotations

import json
import importlib.util
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit


import node_config

MAX_EXPORT_BYTES = 8 * 1024 * 1024
MAX_OBSERVATIONS = 1024
COLLECTOR_CONTRACT_PATH = Path(os.environ.get(
    "BTC_COLLECTOR_CONTRACT_PATH", str(node_config.path("reader_code_dir", node_config.project() / "telegram_reader") / "btc_evidence.py")))

# Strategy-side routing, not evidence quality rules. Mirrors share a family.
DATASET_CONTEXT = {
    "futures_1h": ("derivatives_flow", "futures_1h"),
    "oi_1h": ("oi_timeline", "oi_1h"),
    "spot_cvd": ("spot_flow", "spot_cvd"),
    "funding": ("funding_basis", "funding"),
    "hyperliquid_funding": ("funding_basis", "funding"),
    "binance_funding_shadow": ("funding_basis", "funding"),
    "basis": ("funding_basis", "basis"),
    "hyperliquid_basis": ("funding_basis", "basis"),
    "binance_basis_shadow": ("funding_basis", "basis"),
    "etf": ("capital_source", "etf"),
    "stablecoin": ("capital_source", "stablecoin"),
    "macro": ("macro", "macro"),
    "market_current": ("price_quotes", "market_current"),
    "market_cap": ("relative_strength", "market_cap"),
    "fear_greed": ("relative_strength", "fear_greed"),
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("collector export contains a duplicate object key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("collector export contains a non-finite number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        _reject_constant(value)
    return result


def load_export(path: str) -> list[Any]:
    """Read only a bounded regular local file, never stdin, URLs or a process."""
    try:
        # Symlinks to regular files are allowed. Check the opened descriptor,
        # not an earlier path lookup; O_NONBLOCK prevents a swapped FIFO hang.
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("collector export must be a regular local file")
            raw = handle.read(MAX_EXPORT_BYTES + 1)
        if len(raw) > MAX_EXPORT_BYTES:
            raise ValueError("collector export exceeds the local size limit")
        result = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object, parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("collector export could not be read as local UTF-8 JSON") from None
    if not isinstance(result, list) or len(result) > MAX_OBSERVATIONS:
        raise ValueError("collector export must be a bounded JSON array")
    return result


def _shared_projection():
    """Reuse the collector's field projection; never reimplement its validator."""
    spec = importlib.util.spec_from_file_location("btc_collector_projection", COLLECTOR_CONTRACT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.SCHEMA != "BTC_COLLECTOR_EVIDENCE_V1":
        raise ValueError("unsupported collector contract")
    return module


def _origin_family(source):
    venue = source["venue"]
    return "Binance" if venue.startswith("Binance") else venue


def _snapshot_entries(items):
    """Accept one snapshot, not a historical union; exact duplicates are inert."""
    entries, rejected, seen = [], [], set()
    valid = True
    for index, item in enumerate(items):
        try:
            identity = json.dumps(item, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError, OverflowError, RecursionError):
            rejected.append({"input_index": index, "reasons": ["INVALID_AUXILIARY_SNAPSHOT"]})
            valid = False
            continue
        if identity in seen:
            rejected.append({"input_index": index, "reasons": ["DUPLICATE_AUXILIARY_SNAPSHOT"]})
            continue
        if seen:
            rejected.append({"input_index": index, "reasons": ["MULTIPLE_AUXILIARY_SNAPSHOTS"]})
            valid = False
        seen.add(identity)
        entries.append((index, item))
    return entries, rejected, valid


def _consume(items, shared):
    entries, rejected, snapshot_valid = _snapshot_entries(items)
    accepted, coverage, seen = [], set(), set()
    valid, degraded = snapshot_valid, False
    for index, item in entries:
        def reject(reason, dataset=None, count=None, *, invalid=True):
            nonlocal valid
            if invalid:
                valid = False
            record = {"input_index": index, "reasons": [reason]}
            if dataset is not None:
                record["dataset"] = dataset
            if count is not None:
                record["collector_reason_count"] = count
            rejected.append(record)

        if not isinstance(item, dict) or item.get("role") != "auxiliary_observation":
            reject("INVALID_AUXILIARY_ENVELOPE")
            continue
        quality = item.get("validation")
        if (type(item.get("independent_verified_sources")) is not int
                or item["independent_verified_sources"] != 0
                or item.get("requires_independent_verification") is not True
                or item.get("requires_current_source_check") is not True
                or not isinstance(quality, dict)
                or quality.get("role") != "auxiliary_observation"
                or type(quality.get("independent_verified_sources")) is not int
                or quality["independent_verified_sources"] != 0):
            reject("INVALID_AUXILIARY_TRUST_BOUNDARY")
            continue
        status = quality.get("status")
        problems = quality.get("dataset_reasons")
        if status not in ("valid_auxiliary", "degraded_auxiliary"):
            reject("COLLECTOR_QUALITY_NOT_USABLE")
            continue
        if (quality.get("reasons") != [] or not isinstance(problems, dict)
                or any(not isinstance(reasons, list) or not reasons
                       or any(not isinstance(reason, str) for reason in reasons)
                       for reasons in problems.values())
                or (status == "valid_auxiliary") != (not problems)
                or problems.keys() - DATASET_CONTEXT.keys() - {"__bundle__", "__sources__", "__unsupported__"}):
            reject("INVALID_COLLECTOR_QUALITY_RESULT")
            continue
        datasets, sources = item.get("datasets"), item.get("sources")
        if not isinstance(datasets, dict) or not isinstance(sources, dict):
            reject("INVALID_COLLECTOR_BUNDLE")
            continue
        # Public quality can legitimately describe zero usable datasets. It
        # cannot claim a valid empty bundle or contradict its empty diagnostic.
        if ((not datasets and status == "valid_auxiliary")
                or ("__bundle__" in problems
                    and (datasets or problems["__bundle__"] != ["empty_datasets"]))):
            reject("INVALID_COLLECTOR_QUALITY_RESULT")
            continue
        degraded = degraded or status == "degraded_auxiliary"
        for scope, reason in (
            ("__bundle__", "COLLECTOR_BUNDLE_QUALITY_REJECTED"),
            ("__sources__", "COLLECTOR_SOURCE_QUALITY_REJECTED"),
            ("__unsupported__", "COLLECTOR_UNSUPPORTED_DATASET_FILTERED"),
        ):
            if scope in problems:
                reject(reason, count=len(problems[scope]), invalid=False)
        for name in sorted(problems.keys() & DATASET_CONTEXT.keys()):
            if name not in datasets:
                reject("COLLECTOR_DATASET_MISSING", name, len(problems[name]), invalid=False)
        for name, dataset in datasets.items():
            if name not in DATASET_CONTEXT:
                reject("UNSUPPORTED_DATASET")
                continue
            if name in problems:
                # Reasons stay at the referenced local input, not raw free text
                # in a model-facing result. This is the collector's verdict.
                reject("COLLECTOR_DATASET_QUALITY_REJECTED", name, len(problems[name]), invalid=False)
                continue
            try:
                if not isinstance(dataset, dict):
                    raise ValueError("invalid dataset")
                refs = dataset.get("source_ids")
                if not isinstance(refs, list) or not refs:
                    raise ValueError("missing sources")
                projected_sources = {}
                for sid in refs:
                    source = sources.get(sid) if isinstance(sid, str) else None
                    if not isinstance(source, dict) or not shared._source_identity(sid, source):
                        raise ValueError("invalid source projection")
                    projected = shared._project_source(sid, source)
                    # Query strings are not needed for review routing. Never
                    # echo arbitrary query parameters from a local export.
                    url = urlsplit(projected["endpoint_url"])
                    projected["endpoint_url"] = urlunsplit((url.scheme, url.netloc, url.path, "", ""))
                    projected_sources[sid] = projected
                projected_dataset = shared._project_dataset(name, dataset, sources)
                if not projected_dataset.get("rows") and not projected_dataset.get("values"):
                    raise ValueError("empty measurement projection")
                dimension, family = DATASET_CONTEXT[name]
                origins = sorted({_origin_family(source) for source in projected_sources.values()})
                keys = {(dimension, family, origin) for origin in origins}
                # Deduplicate same-origin measurement families. Retain distinct
                # numeric observations for review, but never count them as new
                # independent sources or let them change review coverage.
                measurements = {key: value for key, value in projected_dataset.items() if key != "source_ids"}
                dedupe = json.dumps([sorted(keys), measurements], sort_keys=True, allow_nan=False)
                coverage.update(keys)
                if dedupe in seen:
                    reject("DUPLICATE_AUXILIARY_MEASUREMENT", name, invalid=False)
                    continue
                seen.add(dedupe)
                accepted.append({
                    "input_index": index, "dataset": name, "dimension_context": dimension,
                    "origin_families": origins, "collector_quality": status,
                    "verification": "UNVERIFIED", "measurements": projected_dataset,
                    "sources": projected_sources,
                })
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
                reject("INVALID_COLLECTOR_PROJECTION", name)
    if not snapshot_valid:
        # Keep every bounded diagnostic, but do not choose/merge history here.
        accepted, coverage = [], set()
    status = ("INVALID" if not valid else "VALID_EMPTY" if not items else
              "VALID_DEGRADED" if degraded else "VALID")
    return accepted, rejected, coverage, status


def _coverage_record(key):
    return dict(zip(("dimension_context", "measurement_family", "origin_family"), key))


def _review_btc_exports(current: list[Any], previous: list[Any] | None = None) -> dict[str, Any]:
    """Quality and new coverage invite review, never establish market evidence.

    Only comparable latest snapshots can produce coverage deltas. The model must
    still decide whether it discriminates an active hypothesis. Raw values,
    arrival, report IDs and timestamps do not enter this comparison. Loss of
    quality is recorded separately and does not force deep evaluation/reporting.
    """
    for value in (current,) + (() if previous is None else (previous,)):
        if not isinstance(value, list) or len(value) > MAX_OBSERVATIONS:
            raise ValueError("collector export must be a bounded JSON array")
    result = {
        "role": "auxiliary_observation_review",
        "independent_verified_sources": 0,
        "requires_independent_verification": True,
        "requires_current_source_check": True,
        "accepted_observations": [],
        "rejected_observations": [],
        "previous_rejected_observations": [],
        "review_candidates": [],
        "review_candidate": False,
        "quality_status": "EMPTY",
        "usable_coverage": [], "lost_coverage": [],
        "deep_review_authorized": False,
        "coverage_current_status": "INVALID" if current else "VALID_EMPTY",
        "coverage_baseline_status": "INVALID" if previous else "VALID_EMPTY" if previous is not None else "NOT_PROVIDED",
        "coverage_comparison_performed": False,
    }
    if not current and not previous:
        result["coverage_comparison_performed"] = previous is not None
        return result
    try:
        shared = _shared_projection()
    except (OSError, ValueError, AttributeError, ImportError):
        result["quality_status"] = "COLLECTOR_CONTRACT_UNAVAILABLE"
        result["rejected_observations"] = [
            {"input_index": index, "reasons": ["COLLECTOR_CONTRACT_UNAVAILABLE"]}
            for index in range(len(current))
        ]
        result["previous_rejected_observations"] = [
            {"input_index": index, "reasons": ["COLLECTOR_CONTRACT_UNAVAILABLE"]}
            for index in range(len(previous or []))
        ]
        return result
    accepted, rejected, coverage, current_status = _consume(current, shared)
    _, previous_rejected, old_coverage, baseline_status = _consume(previous or [], shared)
    if previous is None:
        baseline_status = "NOT_PROVIDED"
    comparable = previous is not None and current_status != "INVALID" and baseline_status != "INVALID"
    new_coverage = coverage - old_coverage if comparable else set()
    lost_coverage = old_coverage - coverage if comparable else set()
    result.update(
        accepted_observations=accepted, rejected_observations=rejected,
        previous_rejected_observations=previous_rejected,
        coverage_current_status=current_status, coverage_baseline_status=baseline_status,
        coverage_comparison_performed=comparable,
        usable_coverage=[_coverage_record(key) for key in sorted(coverage)],
        lost_coverage=[_coverage_record(key) for key in sorted(lost_coverage)],
        quality_status="USABLE_AUXILIARY" if accepted else "NO_USABLE_AUXILIARY" if current else "EMPTY",
        review_candidates=[dict(_coverage_record(key), reason="NEW_USABLE_AUXILIARY_COVERAGE")
                           for key in sorted(new_coverage)],
        review_candidate=bool(new_coverage),
    )
    return result


def _equity_coverage(observation):
    """Coverage identity includes the asset, instrument and economic data domain."""
    keys = set()
    for name, ds in observation['datasets'].items():
        windows = ds.get('settlement_windows', ds.get('windows'))
        if windows is not None:
            scopes = [window for window, state in windows.items()
                      if state.get('complete', state.get('coverage', {}).get('complete')) is True]
        else:
            scopes = ['observed'] if ds.get('rows') or ds.get('values') or name == 'dividend_adjustments' else []
        for scope in scopes:
            keys.add((observation['asset_id'], name, ds.get('instrument', 'MACRO'),
                      ds.get('relation', 'context'), ds.get('rate_type', ''), scope))
    return keys


def _coverage_rows(keys):
    return [dict(zip(('asset_id','dataset','instrument','relation','rate_type','window'), key)) for key in sorted(keys)]


def _sanitize_asset(item):
    if item.get('schema') in ('BTC_COLLECTOR_EVIDENCE_V2', 'MARKET_COLLECTOR_EVIDENCE_V2'):
        from collector_v2_reader import sanitize_observation
    else:
        from market_reader import sanitize_observation
    return sanitize_observation(item)


def review_exports(current: list[Any], previous: list[Any] | None = None) -> dict[str, Any]:
    """Keep the legacy single-BTC result; route multi-asset inputs independently."""
    for rows in (current, previous or []):
        if not isinstance(rows, list) or len(rows) > MAX_OBSERVATIONS:
            raise ValueError("collector export must be a bounded JSON array")
    if all(isinstance(row, dict) and row.get("asset_id", "BTC") == "BTC"
           and row.get("role") != "auxiliary_observation_gap"
           and row.get('schema') != 'BTC_COLLECTOR_EVIDENCE_V2' for row in current + (previous or [])):
        return _review_btc_exports(current, previous)
    path = str(COLLECTOR_CONTRACT_PATH.parent)
    if path not in sys.path:
        sys.path.insert(0, path)
    from market_reader import DATASET_REASONS
    gap_codes = DATASET_REASONS | {'missing_report', 'attachment_unavailable', 'missing_database',
        'invalid_attachment', 'invalid_json', 'unknown_schema', 'text_hash_mismatch', 'text_asset_mismatch',
        'bundle_hash_mismatch', 'invalid_instrument_identity', 'invalid_bundle_values', 'pending_text',
        'invalid_envelope', 'unsupported_asset', 'invalid_text_hash', 'invalid_datasets_or_sources'}
    assets = {}
    for asset in ("BTC", "QQQ", "VOO"):
        rows = [r for r in current if isinstance(r, dict) and r.get("asset_id", "BTC") == asset]
        old = [r for r in previous or [] if isinstance(r, dict) and r.get("asset_id", "BTC") == asset]
        if len(rows) != 1 or rows[0].get("role") == "auxiliary_observation_gap":
            reasons = rows[0].get('reasons', []) if len(rows) == 1 else []
            reasons = [r for r in reasons if isinstance(r, str) and r in gap_codes] if isinstance(reasons, list) else []
            assets[asset] = {"status": "UNAVAILABLE", "reasons": reasons or ["missing_or_invalid_latest_snapshot"]}
        elif asset == "BTC" and rows[0].get('schema') != 'BTC_COLLECTOR_EVIDENCE_V2':
            review = _review_btc_exports(rows, old if previous is not None else None)
            assets[asset] = ({"status": "AUXILIARY", "review": review} if review['accepted_observations'] else
                             {"status": "UNAVAILABLE", "reasons": ["NO_USABLE_AUXILIARY"]})
        else:
            try:
                observation = _sanitize_asset(rows[0])
                coverage = _equity_coverage(observation)
                try:
                    baseline = _sanitize_asset(old[0]) if len(old) == 1 and old[0].get('role') != 'auxiliary_observation_gap' else None
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    baseline = None
                comparable = previous is not None and baseline is not None
                old_coverage = _equity_coverage(baseline) if comparable else set()
                observation['usable_coverage'] = _coverage_rows(coverage)
                assets[asset] = {"status": "AUXILIARY", "observation": observation,
                    "coverage_comparison_performed": comparable,
                    "gained_coverage": _coverage_rows(coverage - old_coverage) if comparable else [],
                    "lost_coverage": _coverage_rows(old_coverage - coverage) if comparable else []}
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                assets[asset] = {"status": "UNAVAILABLE", "reasons": ["invalid_equity_projection"]}
    return {"role": "auxiliary_observation_review", "assets": assets,
            "independent_verified_sources": 0, "requires_independent_verification": True,
            "requires_current_source_check": True, "deep_review_authorized": False,
            "review_candidate": False}
