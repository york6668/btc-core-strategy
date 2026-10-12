"""Pure event-chain validation; no network, account access, writes or report gate.

Calendar records are evidence supplied by the caller, not fetched or verified by
this module.  The registry proves the public provenance shape, not that the
remote page actually contained the supplied date.  Acquisition must verify that
fact and resolve holiday/exception schedules before constructing the record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


EVENT_KEYS = {"final_event_reached", "cycle", "trigger"}
CYCLE_KEYS = {"cycle_id", "opened_at", "events", "final_expiry_event_id"}
CHAIN_EVENT_KEYS = {
    "event_id", "sequence", "due_at", "timezone", "timing_source",
    "completion_rule", "hypothesis_refs", "expected_observation",
    "falsification_outcomes", "next_discriminating_observable",
}
SOURCE_KEYS = {
    "source_id", "endpoint_url", "authority", "access_scope", "published_at",
    "retrieved_at", "scheduled_at", "event_reference",
}
COMPLETION_RULE_KEYS = {"kind", "window_start"}
TRIGGER_KEYS = {"kind", "baseline_cycle_id", "completed_previous_event"}
COMPLETED_EVENT_KEYS = {"cycle_id", "event_id", "due_at", "completed_at"}
OBSERVABLE_KEYS = {"evidence_dimension", "observable", "comparison_basis"}
OUTCOME_KEYS = {"hypothesis_id", "observable_outcome"}
EVIDENCE_DIMENSIONS = {
    "cross_exchange_acceptance", "spot_flow", "derivatives_flow", "oi_timeline",
    "funding_basis", "capital_source", "macro", "relative_strength",
}
COMPARISON_BASES = {
    "BASELINE_CAUSAL_SEQUENCE", "CROSS_VENUE_SEQUENCE",
    "OFFICIAL_RELEASE_VS_EXPECTATION", "POST_EVENT_RETENTION", "FLOW_VS_PRICE",
}
RULE_KINDS = {
    "SCHEDULED_RELEASE_TIME_REACHED", "OFFICIAL_WINDOW_ENDED",
    "PUBLIC_OBSERVATION_WINDOW_ENDED",
}
TRIGGER_KINDS = {
    "FIRST_CYCLE", "MIGRATION", "BASELINE_CHECK", "PREVIOUS_FINAL_COMPLETED",
}
# Explicit provenance scopes: arbitrary pages on an official host are not
# calendars, and private/order paths on an exchange are never accepted.
OFFICIAL_CALENDAR_REGISTRY = {
    "BLS": (("www.bls.gov", "/schedule/"),),
    "FEDERAL_RESERVE": (("www.federalreserve.gov", "/monetarypolicy/fomccalendars.htm"),),
    "BEA": (("www.bea.gov", "/news/schedule"),),
    "NYSE": (
        ("www.nyse.com", "/markets/hours-calendars"),
        ("www.nyse.com", "/trade/hours-calendars"),
    ),
    "CME": (("www.cmegroup.com", "/tools-information/holiday-calendar"),),
    "ECB": (("www.ecb.europa.eu", "/press/calendars/"),),
    "BOJ": (("www.boj.or.jp", "/mopo/mpmsche_minu/"),),
}
EXCHANGE_SCHEDULE_REGISTRY = {
    "BINANCE": ("fapi.binance.com", "/fapi/v1/premiumIndex", "symbol=BTCUSDT"),
}
PUBLIC_OBSERVATION_REGISTRY = {
    "BINANCE": ("api.binance.com", "/api/v3/ticker/price", "symbol=BTCUSDT"),
    "KRAKEN": ("api.kraken.com", "/0/public/Ticker", "pair=XBTUSD"),
    "COINBASE": ("api.exchange.coinbase.com", "/products/BTC-USD/ticker", ""),
}
PUBLIC_CANDLE_REGISTRY = {
    "data-api.binance.vision": (
        "BINANCE", "binance", "/api/v3/klines",
        {"symbol": {"BTCUSDT"}, "interval": {"1h", "1d", "1w"}},
        {"startTime", "endTime", "limit"},
    ),
    "api.kraken.com": (
        "KRAKEN", "kraken", "/0/public/OHLC",
        {"pair": {"XBTUSD"}, "interval": {"60", "1440", "10080"}}, {"since"},
    ),
    "api.exchange.coinbase.com": (
        "COINBASE", "coinbase", "/products/BTC-USD/candles",
        {"granularity": {"3600", "86400"}}, {"start", "end"},
    ),
}
AUTHORITY_TIMEZONES = {
    "BLS": {"America/New_York"}, "FEDERAL_RESERVE": {"America/New_York"},
    "BEA": {"America/New_York"}, "NYSE": {"America/New_York"},
    "CME": {"America/Chicago"}, "ECB": {"Europe/Frankfurt"},
    "BOJ": {"Asia/Tokyo"}, "BINANCE": {"UTC", "Etc/UTC"},
    "KRAKEN": {"UTC", "Etc/UTC"}, "COINBASE": {"UTC", "Etc/UTC"},
}
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z")


def _exact(value: Any, keys: set[str], path: str, errors: list[str]) -> bool:
    if not isinstance(value, Mapping) or set(value) != keys:
        errors.append(f"{path} must contain exactly {','.join(sorted(keys))}")
        return False
    return True


def _text(value: Any, path: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        errors.append(f"{path} must be a nonempty trimmed string")
        return False
    return True


def _instant(value: Any, path: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not _ISO.fullmatch(value):
        errors.append(f"{path} must be timezone-aware ISO-8601 with seconds")
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path} is not a valid ISO-8601 instant")
        return None
    if result.utcoffset() is None:
        errors.append(f"{path} must have a timezone offset")
        return None
    return result


def _context_instant(value: Any, path: str, errors: list[str]) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            errors.append(f"{path} must have a timezone offset")
            return None
        return value
    if isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)? JST",
        value,
    ):
        try:
            return datetime.fromisoformat(value.removesuffix(" JST")).replace(
                tzinfo=timezone(timedelta(hours=9))
            )
        except ValueError:
            pass
    return _instant(value, path, errors)


def public_candle_provenance(endpoint: Any) -> tuple[str, str, str] | None:
    """Recognize anonymous BTC candle requests, not prove their returned data.

    Keep the real request parameters; the frozen source owns the completed
    candle, price and as_of. This does not turn OHLC into a live ticker.
    """
    if not isinstance(endpoint, str) or not endpoint.isascii() or any(c.isspace() for c in endpoint):
        return None
    try:
        parsed = urlsplit(endpoint)
        registered = PUBLIC_CANDLE_REGISTRY.get(parsed.hostname)
        if registered is None:
            return None
        origin, venue, path, required, optional = registered
        if (parsed.netloc != parsed.hostname or parsed.path != path or parsed.fragment
                or not endpoint.startswith("https://" + parsed.hostname + path + "?")):
            return None
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
    except ValueError:
        return None
    params = dict(pairs)
    if len(params) != len(pairs) or not set(required) <= set(params) <= set(required) | optional:
        return None
    if any(params[key] not in values for key, values in required.items()):
        return None
    for key in {"startTime", "endTime", "since", "limit"} & set(params):
        if not re.fullmatch(r"[0-9]{1,16}", params[key]):
            return None
    if "limit" in params and not 1 <= int(params["limit"]) <= 1000:
        return None
    if {"startTime", "endTime"} <= set(params) and int(params["startTime"]) > int(params["endTime"]):
        return None
    times = {}
    for key in {"start", "end"} & set(params):
        errors: list[str] = []
        times[key] = _instant(params[key], key, errors)
        if errors:
            return None
    if len(times) == 2 and times["start"] >= times["end"]:
        return None
    return origin, venue, endpoint


def _source_scope(source: Mapping[str, Any], rule: Any) -> bool:
    url = source.get("endpoint_url")
    if not isinstance(url, str) or not url.isascii() or any(c.isspace() for c in url):
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or port not in (None, 443) or parsed.fragment or "%" in parsed.path
        or ".." in parsed.path.split("/") or "\\" in url
    ):
        return False
    authority = source.get("authority")
    if not isinstance(authority, str):
        return False
    if rule == "PUBLIC_OBSERVATION_WINDOW_ENDED":
        candle = public_candle_provenance(url)
        return (candle is not None and candle[0] == authority) or PUBLIC_OBSERVATION_REGISTRY.get(authority) == (
            parsed.hostname, parsed.path, parsed.query
        )
    if EXCHANGE_SCHEDULE_REGISTRY.get(authority) == (
        parsed.hostname, parsed.path, parsed.query
    ):
        return True
    if parsed.query:
        return False
    for host, path in OFFICIAL_CALENDAR_REGISTRY.get(authority, ()):
        if parsed.hostname == host and (
            parsed.path.startswith(path) if path.endswith("/")
            else parsed.path == path or parsed.path.startswith(path + ".")
        ):
            return True
    return False


def _validate_source(
    source: Any, rule: Any, due: datetime | None, cutoff: datetime | None,
    path: str, errors: list[str],
) -> None:
    if not _exact(source, SOURCE_KEYS, path, errors):
        return
    for key in ("source_id", "event_reference", "authority"):
        _text(source.get(key), f"{path}.{key}", errors)
    if source.get("access_scope") != "PUBLIC_UNAUTHENTICATED":
        errors.append(f"{path}.access_scope must be PUBLIC_UNAUTHENTICATED")
    if not _source_scope(source, rule):
        errors.append(f"{path}.endpoint_url is not a registered official public timing source")
    # Public calendars often expose no precise publication instant. Unknown is
    # explicit; never manufacture midnight or reuse retrieval as publication.
    published = (_instant(source.get("published_at"), f"{path}.published_at", errors)
                 if source.get("published_at") is not None else None)
    retrieved = _instant(source.get("retrieved_at"), f"{path}.retrieved_at", errors)
    if published is not None and retrieved is not None and published > retrieved:
        errors.append(f"{path} cannot be retrieved before publication")
    if cutoff is not None and any(t is not None and t > cutoff for t in (published, retrieved)):
        errors.append(f"{path} evidence cannot be later than the observation/pricing cutoff")
    if rule == "PUBLIC_OBSERVATION_WINDOW_ENDED":
        if source.get("scheduled_at") is not None:
            errors.append(f"{path}.scheduled_at must be null for a chosen public observation window")
    else:
        scheduled = _instant(source.get("scheduled_at"), f"{path}.scheduled_at", errors)
        if scheduled is not None and due is not None and scheduled != due:
            errors.append(f"{path}.scheduled_at must equal the official event due_at")


def _validate_chain_event(
    item: Any, index: int, hypothesis_ids: set[str], cutoff: datetime | None,
    path: str, errors: list[str],
) -> datetime | None:
    if not _exact(item, CHAIN_EVENT_KEYS, path, errors):
        return None
    _text(item.get("event_id"), f"{path}.event_id", errors)
    if type(item.get("sequence")) is not int or item["sequence"] != index + 1:
        errors.append(f"{path}.sequence must be consecutive from 1")
    due = _instant(item.get("due_at"), f"{path}.due_at", errors)
    zone = None
    if _text(item.get("timezone"), f"{path}.timezone", errors):
        try:
            zone = ZoneInfo(item["timezone"])
        except (ValueError, ZoneInfoNotFoundError):
            errors.append(f"{path}.timezone must be an available IANA timezone")
    if due is not None and zone is not None and due.utcoffset() != due.astimezone(zone).utcoffset():
        errors.append(f"{path}.due_at offset conflicts with the calendar timezone (including DST)")
    rule = item.get("completion_rule")
    rule_kind = None
    if _exact(rule, COMPLETION_RULE_KEYS, f"{path}.completion_rule", errors):
        rule_kind = rule.get("kind")
        if not isinstance(rule_kind, str) or rule_kind not in RULE_KINDS:
            errors.append(f"{path}.completion_rule.kind is invalid")
        if rule_kind == "SCHEDULED_RELEASE_TIME_REACHED":
            if rule.get("window_start") is not None:
                errors.append(f"{path}.completion_rule.window_start must be null for a release")
        else:
            start = _instant(rule.get("window_start"), f"{path}.completion_rule.window_start", errors)
            if start is not None and due is not None and start >= due:
                errors.append(f"{path} observation window must end after its start")
            if start is not None and zone is not None and start.utcoffset() != start.astimezone(zone).utcoffset():
                errors.append(f"{path}.window_start offset conflicts with its IANA timezone")
    _validate_source(item.get("timing_source"), rule_kind, due, cutoff, f"{path}.timing_source", errors)
    source = item.get("timing_source")
    if isinstance(source, Mapping) and isinstance(source.get("authority"), str):
        allowed_zones = AUTHORITY_TIMEZONES.get(source["authority"], set())
        if not isinstance(item.get("timezone"), str) or item["timezone"] not in allowed_zones:
            errors.append(f"{path}.timezone must match the official timing authority")
    refs = item.get("hypothesis_refs")
    valid_refs = (
        isinstance(refs, list) and 2 <= len(refs)
        and all(isinstance(ref, str) and ref and ref == ref.strip() for ref in refs)
        and len(refs) == len(set(refs)) and set(refs) <= hypothesis_ids
    )
    if not valid_refs:
        errors.append(f"{path}.hypothesis_refs must distinguish at least two unique current hypotheses")
    _text(item.get("expected_observation"), f"{path}.expected_observation", errors)
    outcomes = item.get("falsification_outcomes")
    outcome_ids: list[str] = []
    outcome_text: list[str] = []
    if not isinstance(outcomes, list) or len(outcomes) < 2:
        errors.append(f"{path}.falsification_outcomes must cover the competing hypotheses")
    else:
        for i, outcome in enumerate(outcomes):
            opath = f"{path}.falsification_outcomes[{i}]"
            if _exact(outcome, OUTCOME_KEYS, opath, errors):
                if _text(outcome.get("hypothesis_id"), f"{opath}.hypothesis_id", errors):
                    outcome_ids.append(outcome["hypothesis_id"])
                if _text(outcome.get("observable_outcome"), f"{opath}.observable_outcome", errors):
                    outcome_text.append(outcome["observable_outcome"])
        if valid_refs and (len(outcome_ids) != len(refs) or set(outcome_ids) != set(refs)):
            errors.append(f"{path}.falsification_outcomes must map each hypothesis_ref exactly once")
        if len(outcome_text) != len(set(outcome_text)):
            errors.append(f"{path}.falsification_outcomes must identify distinct observable outcomes")
    observable = item.get("next_discriminating_observable")
    if _exact(observable, OBSERVABLE_KEYS, f"{path}.next_discriminating_observable", errors):
        dimension = observable.get("evidence_dimension")
        comparison = observable.get("comparison_basis")
        if not isinstance(dimension, str) or dimension not in EVIDENCE_DIMENSIONS:
            errors.append(f"{path}.next_discriminating_observable.evidence_dimension is invalid")
        if not isinstance(comparison, str) or comparison not in COMPARISON_BASES:
            errors.append(f"{path}.next_discriminating_observable.comparison_basis is invalid")
        _text(observable.get("observable"), f"{path}.next_discriminating_observable.observable", errors)
    return due


def validate_event_contract(
    event: Any, pricing_as_of: Any, observed_at: Any, now: Any,
    hypothesis_ids: Iterable[str], baseline_event: Any = None,
    migration: bool = False, baseline_mode: bool = False,
    reassessment_deadline: Any = None,
) -> list[str]:
    """Validate one event section against its immutable write-before baseline.

    ``now`` is supplied by the caller's local clock; it is not an input artifact.
    Context times accept aware datetimes or ISO strings; pricing also accepts
    the project's minute-precision JST representation.  All persisted event
    times require timezone-aware ISO strings. ``opened_at`` is the actual plan
    formation instant, bounded by the trusted clock, independently of the
    older locked observation/pricing cutoff used for source evidence.
    A baseline already persisted in
    a past run is structurally checked with ``baseline_mode=True`` and its own
    original cutoff; reaching its final deadline later is not baseline damage.

    The compatibility boolean means *this run consumed the previous final
    event*, never that the newly persisted cycle is already completed.  The
    function validates it, but deliberately does not derive report necessity.
    """
    errors: list[str] = []
    pricing = _context_instant(pricing_as_of, "event context pricing_as_of", errors)
    observed = _context_instant(observed_at, "event context observed_at", errors)
    clock = _context_instant(now, "event context now", errors)
    if pricing is not None and clock is not None and pricing > clock + MAX_FUTURE_CLOCK_SKEW:
        errors.append("event pricing cutoff is later than the trusted local clock")
    if observed is not None and pricing is not None and observed > pricing:
        errors.append("event observed_at exceeds the pricing cutoff")
    cutoff = min(pricing, observed) if pricing is not None and observed is not None else None
    try:
        ids = set(hypothesis_ids) if not isinstance(hypothesis_ids, str) else set()
    except TypeError:
        ids = set()
    if len(ids) < 2 or not all(isinstance(i, str) and i and i == i.strip() for i in ids):
        errors.append("event context requires at least two valid current hypothesis IDs")
        ids = {i for i in ids if isinstance(i, str)}
    if not _exact(event, EVENT_KEYS, "event", errors):
        return errors
    if type(event.get("final_event_reached")) is not bool:
        errors.append("event.final_event_reached must be boolean")
    cycle = event.get("cycle")
    if not _exact(cycle, CYCLE_KEYS, "event.cycle", errors):
        return errors
    _text(cycle.get("cycle_id"), "event.cycle.cycle_id", errors)
    _text(cycle.get("final_expiry_event_id"), "event.cycle.final_expiry_event_id", errors)
    opened = _instant(cycle.get("opened_at"), "event.cycle.opened_at", errors)
    if opened is not None and clock is not None and opened > clock:
        errors.append("event cycle cannot open after the trusted local clock")
    chain = cycle.get("events")
    if not isinstance(chain, list) or not 2 <= len(chain) <= 4:
        errors.append("event.cycle.events must contain 2 to 4 sequential events")
        return errors
    due_times = [
        _validate_chain_event(item, i, ids, cutoff, f"event.cycle.events[{i}]", errors)
        for i, item in enumerate(chain)
    ]
    event_ids = [item.get("event_id") for item in chain if isinstance(item, Mapping)]
    if all(isinstance(i, str) for i in event_ids) and len(set(event_ids)) != len(event_ids):
        errors.append("event chain event_id values must be unique")
    if not isinstance(chain[-1], Mapping) or cycle.get("final_expiry_event_id") != chain[-1].get("event_id"):
        errors.append("event final_expiry_event_id must be the unique last event")
    for index, due in enumerate(due_times):
        if due is not None and opened is not None and due <= opened:
            errors.append("event due_at must be after cycle opened_at")
        if index and due is not None and due_times[index - 1] is not None and due <= due_times[index - 1]:
            errors.append("event due_at values must be strictly increasing")
    trigger = event.get("trigger")
    if not _exact(trigger, TRIGGER_KEYS, "event.trigger", errors):
        return errors
    kind = trigger.get("kind")
    if not isinstance(kind, str) or kind not in TRIGGER_KINDS:
        errors.append("event.trigger.kind is invalid")
        kind = None
    expected_final = kind == "PREVIOUS_FINAL_COMPLETED"
    if event.get("final_event_reached") is not expected_final:
        errors.append("event.final_event_reached must equal the validated trigger result")
    completed = trigger.get("completed_previous_event")
    completed_due = None
    if expected_final:
        if _exact(completed, COMPLETED_EVENT_KEYS, "event.trigger.completed_previous_event", errors):
            for key in ("cycle_id", "event_id"):
                _text(completed.get(key), f"event.trigger.completed_previous_event.{key}", errors)
            completed_due = _instant(completed.get("due_at"), "event.trigger.completed_previous_event.due_at", errors)
            completion = _instant(completed.get("completed_at"), "event.trigger.completed_previous_event.completed_at", errors)
            if completed_due is not None and completion is not None and completed_due != completion:
                errors.append("event completion must equal the scheduled final-event instant")
            if completion is not None and cutoff is not None and completion > cutoff:
                errors.append("event final completion cannot be backfilled from after the cutoff")
            if completion is not None and clock is not None and completion > clock:
                errors.append("event final completion cannot precede the trusted local clock reaching it")
            if completion is not None and opened is not None and completion > opened:
                errors.append("event new cycle cannot open before the previous event completed")
            if completed.get("cycle_id") != trigger.get("baseline_cycle_id"):
                errors.append("event completed previous event must bind the trigger baseline_cycle_id")
            if completed.get("cycle_id") == cycle.get("cycle_id"):
                errors.append("event E must establish a distinct new cycle")
            if completed.get("event_id") in event_ids:
                errors.append("event E cannot reuse the consumed final event identity")
    elif completed is not None:
        errors.append("event non-final trigger must not carry a completed previous event")
    if kind in {"FIRST_CYCLE", "MIGRATION"}:
        if trigger.get("baseline_cycle_id") is not None:
            errors.append("event initialization must not claim a revision-3 baseline cycle")
    else:
        _text(trigger.get("baseline_cycle_id"), "event.trigger.baseline_cycle_id", errors)
    if kind == "BASELINE_CHECK" and trigger.get("baseline_cycle_id") != cycle.get("cycle_id"):
        errors.append("event BASELINE_CHECK trigger must point to its unchanged current cycle")
    if baseline_mode:
        if baseline_event is not None:
            errors.append("event baseline_mode does not accept another baseline_event")
        # Historical trigger describes its creation; it is not today's E gate.
        return errors
    if migration:
        if baseline_event is not None or kind != "MIGRATION":
            errors.append("event migration requires a fresh MIGRATION chain without a revision-3 baseline")
    elif baseline_event is None:
        if kind != "FIRST_CYCLE":
            errors.append("event without a baseline requires FIRST_CYCLE and cannot claim E")
    else:
        if not isinstance(baseline_event, Mapping) or not isinstance(baseline_event.get("cycle"), Mapping):
            errors.append("event requires a validated revision-3 baseline event chain")
            return errors
        old = baseline_event["cycle"]
        old_chain = old.get("events")
        if not isinstance(old_chain, list) or not old_chain or not isinstance(old_chain[-1], Mapping):
            errors.append("event baseline has no final event")
            return errors
        old_refs = {
            ref for item in old_chain if isinstance(item, Mapping)
            for ref in (item.get("hypothesis_refs") if isinstance(item.get("hypothesis_refs"), list) else [])
            if isinstance(ref, str)
        }
        baseline_errors = validate_event_contract(
            baseline_event, pricing_as_of, observed_at, now, old_refs,
            baseline_mode=True,
        )
        if baseline_errors:
            errors.extend("event baseline invalid: " + error for error in baseline_errors)
            return errors
        old_final = old_chain[-1]
        old_due = _instant(old_final.get("due_at"), "event baseline final due_at", errors)
        if trigger.get("baseline_cycle_id") != old.get("cycle_id"):
            errors.append("event trigger must bind the actual current baseline cycle")
        if kind not in {"BASELINE_CHECK", "PREVIOUS_FINAL_COMPLETED"}:
            errors.append("event daily run must check or complete the current baseline")
        if old_due is not None and clock is not None:
            deadline_reached = old_due <= clock
            if expected_final != deadline_reached:
                errors.append("event final trigger must be derived from the current baseline deadline and clock")
        if expected_final:
            if isinstance(completed, Mapping) and (
                completed.get("event_id") != old_final.get("event_id")
                or completed.get("cycle_id") != old.get("cycle_id")
                or completed_due != old_due
            ):
                errors.append("event completed previous event must exactly match the baseline final event")
            old_ids = {i.get("event_id") for i in old_chain if isinstance(i, Mapping) and isinstance(i.get("event_id"), str)}
            if any(i in old_ids for i in event_ids if isinstance(i, str)):
                errors.append("event new cycle must use fresh event identities")
        elif cycle != old:
            approved_due = (_instant(reassessment_deadline, "approved cycle review deadline", errors)
                            if reassessment_deadline is not None else None)
            # A verified v4 cycle review can bring an existing deadline forward;
            # it cannot invent completion, reset the cycle, or postpone a promise.
            shortened = (kind == "BASELINE_CHECK" and approved_due is not None
                         and old_due is not None and clock is not None
                         and cycle.get("cycle_id") == old.get("cycle_id")
                         and cycle.get("opened_at") == old.get("opened_at")
                         and due_times[-1] == approved_due < old_due
                         and approved_due > max(t for t in (clock, pricing, observed) if t is not None))
            if not shortened:
                errors.append("event BASELINE_CHECK must preserve the current cycle and event definitions unless an approved review shortens its deadline")
    if kind in {"FIRST_CYCLE", "MIGRATION", "PREVIOUS_FINAL_COMPLETED"}:
        pending_cutoffs = [t for t in (pricing, observed, clock) if t is not None]
        if pending_cutoffs and any(d is not None and d <= max(pending_cutoffs) for d in due_times):
            errors.append("event newly established cycle must contain only future pending events")
    return errors
