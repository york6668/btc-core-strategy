from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import event_contract as contract  # noqa: E402


HYPOTHESES = {"H_NATIVE", "H_MACRO"}
INITIAL = "2030-03-01T12:00:00+00:00"
BEFORE_FINAL = "2030-03-01T14:30:00+00:00"
AFTER_FINAL = "2030-03-01T15:05:00+00:00"


def chain_event(event_id: str, sequence: int, due_at: str) -> dict:
    return {
        "event_id": event_id,
        "sequence": sequence,
        "due_at": due_at,
        "timezone": "America/New_York",
        "timing_source": {
            "source_id": "BLS_CALENDAR",
            "endpoint_url": "https://www.bls.gov/schedule/news_release/empsit.htm",
            "authority": "BLS",
            "access_scope": "PUBLIC_UNAUTHENTICATED",
            "published_at": "2030-02-20T12:00:00+00:00",
            "retrieved_at": "2030-03-01T11:55:00+00:00",
            "scheduled_at": due_at,
            "event_reference": event_id,
        },
        "completion_rule": {"kind": "SCHEDULED_RELEASE_TIME_REACHED", "window_start": None},
        "hypothesis_refs": ["H_NATIVE", "H_MACRO"],
        "expected_observation": "比较官方数据公布前后的原生现货与宏观反应时序",
        "falsification_outcomes": [
            {"hypothesis_id": "H_NATIVE", "observable_outcome": "现货流没有领先价格，宏观资产先行"},
            {"hypothesis_id": "H_MACRO", "observable_outcome": "宏观资产没有领先，跨所现货持续先行"},
        ],
        "next_discriminating_observable": {
            "evidence_dimension": "macro",
            "observable": "公布后宏观资产与 BTC 现货响应的先后顺序",
            "comparison_basis": "OFFICIAL_RELEASE_VS_EXPECTATION",
        },
    }


def first_cycle() -> dict:
    return {
        "final_event_reached": False,
        "cycle": {
            "cycle_id": "CYCLE_FIRST",
            "opened_at": INITIAL,
            "events": [
                chain_event("RELEASE_ONE", 1, "2030-03-01T09:00:00-05:00"),
                chain_event("RELEASE_FINAL", 2, "2030-03-01T10:00:00-05:00"),
            ],
            "final_expiry_event_id": "RELEASE_FINAL",
        },
        "trigger": {"kind": "FIRST_CYCLE", "baseline_cycle_id": None, "completed_previous_event": None},
    }


def checked_cycle(baseline: dict | None = None) -> dict:
    result = deepcopy(baseline or first_cycle())
    result["final_event_reached"] = False
    result["trigger"] = {
        "kind": "BASELINE_CHECK",
        "baseline_cycle_id": result["cycle"]["cycle_id"],
        "completed_previous_event": None,
    }
    return result


def next_cycle(baseline: dict | None = None) -> dict:
    old = baseline or first_cycle()
    result = first_cycle()
    result["final_event_reached"] = True
    result["cycle"].update({
        "cycle_id": "CYCLE_SECOND", "opened_at": AFTER_FINAL,
        "events": [
            chain_event("NEXT_ONE", 1, "2030-03-01T11:00:00-05:00"),
            chain_event("NEXT_FINAL", 2, "2030-03-01T12:00:00-05:00"),
        ],
        "final_expiry_event_id": "NEXT_FINAL",
    })
    final = old["cycle"]["events"][-1]
    result["trigger"] = {
        "kind": "PREVIOUS_FINAL_COMPLETED",
        "baseline_cycle_id": old["cycle"]["cycle_id"],
        "completed_previous_event": {
            "cycle_id": old["cycle"]["cycle_id"],
            "event_id": final["event_id"],
            "due_at": final["due_at"], "completed_at": final["due_at"],
        },
    }
    return result


def check(event: dict, at: str = INITIAL, **kwargs) -> list[str]:
    options = dict(pricing_as_of=at, observed_at=at, now=at, hypothesis_ids=HYPOTHESES)
    options.update(kwargs)
    return contract.validate_event_contract(event, **options)


class EventContractTests(unittest.TestCase):
    def test_first_cycle_is_pending_without_report_control(self):
        self.assertEqual(check(first_cycle()), [])
        self.assertNotIn("report_required", contract.EVENT_KEYS)

    def test_unknown_calendar_publication_instant_is_not_fabricated(self):
        value = first_cycle()
        value["cycle"]["events"][0]["timing_source"]["published_at"] = None
        self.assertEqual(check(value), [])
        value["cycle"]["events"][0]["timing_source"].pop("published_at")
        self.assertTrue(check(value))

    def test_migration_never_masquerades_as_e(self):
        value = first_cycle()
        value["trigger"]["kind"] = "MIGRATION"
        self.assertEqual(check(value, migration=True), [])
        value["final_event_reached"] = True
        self.assertTrue(check(value, migration=True))

    def test_past_ordinary_event_is_not_e(self):
        baseline = first_cycle()
        self.assertEqual(check(checked_cycle(baseline), BEFORE_FINAL, baseline_event=baseline), [])

    def test_e_consumes_previous_final_and_establishes_pending_chain(self):
        self.assertEqual(check(next_cycle(), AFTER_FINAL, baseline_event=first_cycle()), [])

    def test_final_due_requires_e_even_when_observation_is_stale(self):
        errors = check(checked_cycle(), INITIAL, now=AFTER_FINAL, baseline_event=first_cycle())
        self.assertTrue(any("deadline" in error for error in errors))

    def test_clock_boundary_is_inclusive(self):
        at = "2030-03-01T15:00:00+00:00"
        value = next_cycle()
        value["cycle"]["opened_at"] = at
        self.assertEqual(check(value, at, baseline_event=first_cycle()), [])
        self.assertTrue(check(checked_cycle(), at, baseline_event=first_cycle()))

    def test_e_cannot_be_claimed_before_trusted_clock_reaches_deadline(self):
        value = next_cycle()
        self.assertTrue(check(value, AFTER_FINAL, now="2030-03-01T14:59:59+00:00", baseline_event=first_cycle()))

    def test_repeat_e_is_rejected_against_new_baseline(self):
        persisted = next_cycle()
        self.assertTrue(check(persisted, AFTER_FINAL, baseline_event=persisted))
        self.assertEqual(check(checked_cycle(persisted), AFTER_FINAL, baseline_event=persisted), [])

    def test_historical_baseline_can_be_expired_today(self):
        self.assertEqual(check(first_cycle(), now="2031-01-01T00:00:00+00:00", baseline_mode=True), [])
        self.assertEqual(check(next_cycle(), AFTER_FINAL, now="2031-01-01T00:00:00+00:00", baseline_mode=True), [])

    def test_new_chain_is_not_already_completed(self):
        self.assertTrue(check(next_cycle(), "2030-03-01T16:10:00+00:00", baseline_event=first_cycle()))

    def test_boolean_only_is_not_evidence(self):
        for value in ({"final_event_reached": True}, {"final_event_reached": False}):
            with self.subTest(value=value):
                self.assertTrue(check(value))

    def test_no_baseline_cannot_claim_e(self):
        self.assertTrue(check(next_cycle(), AFTER_FINAL))

    def test_e_requires_the_actual_final_event(self):
        for key, replacement in (("event_id", "RELEASE_ONE"), ("cycle_id", "SPOOFED"), ("due_at", INITIAL), ("completed_at", AFTER_FINAL)):
            value = next_cycle()
            value["trigger"]["completed_previous_event"][key] = replacement
            with self.subTest(key=key):
                self.assertTrue(check(value, AFTER_FINAL, baseline_event=first_cycle()))

    def test_e_requires_new_cycle_and_event_identity(self):
        for mutation in (
            lambda v: v["cycle"].update(cycle_id="CYCLE_FIRST"),
            lambda v: v["cycle"]["events"][0].update(event_id="RELEASE_ONE"),
            lambda v: v["cycle"].update(opened_at=INITIAL),
            lambda v: v["cycle"].update(events=[]),
        ):
            value = next_cycle()
            mutation(value)
            self.assertTrue(check(value, AFTER_FINAL, baseline_event=first_cycle()))

    def test_daily_chain_cannot_silently_extend_deadline(self):
        value = checked_cycle()
        final = value["cycle"]["events"][-1]
        final["due_at"] = "2030-03-01T11:00:00-05:00"
        final["timing_source"]["scheduled_at"] = final["due_at"]
        self.assertTrue(check(value, BEFORE_FINAL, baseline_event=first_cycle()))

    def test_approved_reassessment_can_shorten_same_pending_cycle(self):
        value = checked_cycle()
        final = value["cycle"]["events"][-1]
        final["due_at"] = "2030-03-01T09:45:00-05:00"
        final["timing_source"]["scheduled_at"] = final["due_at"]
        # New approved hypotheses need not retain the old study's identifiers.
        for item in value["cycle"]["events"]:
            item["hypothesis_refs"] = ["H_NEW_NATIVE", "H_NEW_MACRO"]
            for outcome, identity in zip(item["falsification_outcomes"], item["hypothesis_refs"]):
                outcome["hypothesis_id"] = identity
        options = dict(baseline_event=first_cycle(), hypothesis_ids={"H_NEW_NATIVE", "H_NEW_MACRO"})
        self.assertTrue(check(value, BEFORE_FINAL, **options))
        self.assertEqual([], check(value, BEFORE_FINAL, reassessment_deadline=final["due_at"], **options))
        self.assertFalse(value["final_event_reached"])

    def test_reassessment_cannot_reset_postpone_or_misbind_deadline(self):
        for change in ("postpone", "mismatch", "reset", "past"):
            value = checked_cycle()
            final = value["cycle"]["events"][-1]
            final["due_at"] = "2030-03-01T09:45:00-05:00"
            if change == "postpone":
                final["due_at"] = "2030-03-01T11:00:00-05:00"
            if change == "past":
                final["due_at"] = "2030-03-01T09:15:00-05:00"
            final["timing_source"]["scheduled_at"] = final["due_at"]
            if change == "reset":
                value["cycle"]["opened_at"] = "2030-03-01T12:01:00Z"
            approved = "2030-03-01T14:46:00Z" if change == "mismatch" else final["due_at"]
            with self.subTest(change=change):
                self.assertTrue(check(value, BEFORE_FINAL, baseline_event=first_cycle(), reassessment_deadline=approved))

    def test_unique_final_must_be_last(self):
        value = first_cycle()
        value["cycle"]["final_expiry_event_id"] = "RELEASE_ONE"
        self.assertTrue(check(value))

    def test_two_to_four_events_are_required(self):
        for count in (0, 1, 5):
            value = first_cycle()
            value["cycle"]["events"] = value["cycle"]["events"][:count] if count < 2 else value["cycle"]["events"] * 3
            self.assertTrue(check(value))

    def test_three_and_four_sequential_events_are_allowed(self):
        for count in (3, 4):
            value = first_cycle()
            value["cycle"]["events"] = [chain_event(f"E{i}", i + 1, f"2030-03-01T{9 + i:02d}:00:00-05:00") for i in range(count)]
            value["cycle"]["final_expiry_event_id"] = f"E{count - 1}"
            self.assertEqual(check(value), [])

    def test_order_and_identity_are_strict(self):
        for key, value in (("sequence", True), ("sequence", 7), ("event_id", "RELEASE_ONE"), ("due_at", "2030-03-01T08:00:00-05:00")):
            candidate = first_cycle()
            candidate["cycle"]["events"][-1][key] = value
            self.assertTrue(check(candidate))

    def test_iso_offset_and_iana_dst_are_required(self):
        for due, zone in (
            ("2030-03-01T09:00:00", "America/New_York"),
            ("2030-07-01T09:00:00-05:00", "America/New_York"),
            ("2030-03-01T09:00:00-05:00", "Asia/Tokyo"),
            ("2030-03-01T09:00:00-05:00", "Not/A_Zone"),
        ):
            value = first_cycle()
            value["cycle"]["events"][0].update(due_at=due, timezone=zone)
            self.assertTrue(check(value))

    def test_summer_dst_offset_is_valid(self):
        value = first_cycle()
        for index, item in enumerate(value["cycle"]["events"]):
            item["due_at"] = f"2030-07-01T{9 + index:02d}:00:00-04:00"
            item["timing_source"]["scheduled_at"] = item["due_at"]
        self.assertEqual(check(value), [])

    def test_unknown_private_and_spoofed_sources_fail(self):
        for url in (
            "https://www.bls.gov.attacker.org/schedule/2030/",
            "https://www.bls.gov/account",
            "https://www.bls.gov/schedule/2030/?token=secret",
            "https://user:pass@www.bls.gov/schedule/2030/",
            "http://www.bls.gov/schedule/2030/",
            "https://www.bls.gov:444/schedule/2030/",
            "https://www.bls.gov/schedule/%2e%2e/account",
            "https://127.0.0.1/schedule/",
            "https://fapi.binance.com/fapi/v2/account",
        ):
            value = first_cycle()
            value["cycle"]["events"][0]["timing_source"]["endpoint_url"] = url
            with self.subTest(url=url):
                self.assertTrue(check(value))

    def test_source_schedule_must_match_event_due_time(self):
        value = first_cycle()
        value["cycle"]["events"][0]["timing_source"]["scheduled_at"] = INITIAL
        self.assertTrue(check(value))

    def test_future_source_evidence_and_reverse_publication_fail(self):
        for field, instant in (("published_at", AFTER_FINAL), ("retrieved_at", AFTER_FINAL), ("published_at", "2030-03-01T11:56:00+00:00")):
            value = first_cycle()
            value["cycle"]["events"][0]["timing_source"][field] = instant
            self.assertTrue(check(value))

    def test_exchange_funding_schedule_is_not_cash_window_only(self):
        value = first_cycle()
        item = value["cycle"]["events"][0]
        item.update(timezone="UTC", due_at="2030-03-01T14:00:00+00:00")
        item["timing_source"].update(
            authority="BINANCE", endpoint_url="https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT",
            scheduled_at=item["due_at"], event_reference="BTCUSDT_NEXT_FUNDING_TIME",
        )
        self.assertEqual(check(value), [])

    def test_public_observation_window_need_not_claim_official_calendar_date(self):
        value = first_cycle()
        item = value["cycle"]["events"][0]
        item.update(timezone="UTC", due_at="2030-03-01T14:00:00+00:00")
        item["completion_rule"] = {"kind": "PUBLIC_OBSERVATION_WINDOW_ENDED", "window_start": INITIAL}
        item["timing_source"].update(
            authority="COINBASE", endpoint_url="https://api.exchange.coinbase.com/products/BTC-USD/ticker",
            scheduled_at=None, event_reference="PUBLIC_OBSERVATION_WINDOW",
        )
        self.assertEqual(check(value), [])
        item["timing_source"]["scheduled_at"] = item["due_at"]
        self.assertTrue(check(value))

    def test_public_observation_accepts_real_btc_candle_requests(self):
        endpoints = (
            ("BINANCE", "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=240"),
            ("KRAKEN", "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60&since=1909000000"),
            ("COINBASE", "https://api.exchange.coinbase.com/products/BTC-USD/candles?start=2030-02-28T11%3A00%3A00Z&end=2030-03-01T11%3A00%3A00Z&granularity=3600"),
        )
        for authority, endpoint in endpoints:
            with self.subTest(authority=authority):
                value = first_cycle()
                item = value["cycle"]["events"][0]
                item.update(timezone="UTC", due_at="2030-03-01T14:00:00+00:00")
                item["completion_rule"] = {"kind": "PUBLIC_OBSERVATION_WINDOW_ENDED", "window_start": INITIAL}
                item["timing_source"].update(authority=authority, endpoint_url=endpoint, scheduled_at=None)
                self.assertEqual(check(value), [])
                item["timing_source"]["retrieved_at"] = AFTER_FINAL
                self.assertTrue(any("cutoff" in error for error in check(value)))

    def test_public_candles_do_not_accept_private_or_unscoped_requests(self):
        base = "https://data-api.binance.vision/api/v3/klines"
        for endpoint in (
            base, base + "?symbol=ETHUSDT&interval=1h", base + "?symbol=BTCUSDT",
            base + "?symbol=BTCUSDT&interval=1h&symbol=ETHUSDT",
            base + "?symbol=BTCUSDT&interval=1h&token=secret",
            base + "?symbol=BTCUSDT&interval=1h&limit=0",
            base + "?symbol=BTCUSDT&interval=1h&startTime=20&endTime=10",
            "https://api.kraken.com/0/private/Balance?pair=XBTUSD&interval=60",
            "https://data-api.binance.vision.attacker.org/api/v3/klines?symbol=BTCUSDT&interval=1h",
            "https://user@data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h",
            "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600&start=invalid",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIsNone(contract.public_candle_provenance(endpoint))

    def test_official_window_has_a_real_positive_interval(self):
        value = first_cycle()
        item = value["cycle"]["events"][0]
        item["completion_rule"] = {"kind": "OFFICIAL_WINDOW_ENDED", "window_start": "2030-03-01T08:00:00-05:00"}
        self.assertEqual(check(value), [])
        item["completion_rule"]["window_start"] = item["due_at"]
        self.assertTrue(check(value))

    def test_competing_hypotheses_and_discriminating_outcomes_are_bound(self):
        for mutation in (
            lambda e: e.update(hypothesis_refs=["H_NATIVE"]),
            lambda e: e.update(hypothesis_refs=["H_NATIVE", "H_FAKE"]),
            lambda e: e.update(hypothesis_refs=["H_NATIVE", "H_NATIVE"]),
            lambda e: e.update(expected_observation=""),
            lambda e: e.update(falsification_outcomes=[]),
            lambda e: e["falsification_outcomes"][1].update(hypothesis_id="H_NATIVE"),
            lambda e: e["falsification_outcomes"][1].update(observable_outcome=e["falsification_outcomes"][0]["observable_outcome"]),
            lambda e: e["next_discriminating_observable"].update(evidence_dimension="account_impact"),
            lambda e: e["next_discriminating_observable"].update(observable=""),
            lambda e: e["next_discriminating_observable"].update(comparison_basis="CASH_OPEN_IS_CONFIRMATION"),
        ):
            value = first_cycle()
            mutation(value["cycle"]["events"][0])
            self.assertTrue(check(value))

    def test_each_mapping_rejects_extra_keys(self):
        for path in (
            (), ("cycle",), ("trigger",), ("cycle", "events", 0),
            ("cycle", "events", 0, "timing_source"),
            ("cycle", "events", 0, "completion_rule"),
            ("cycle", "events", 0, "falsification_outcomes", 0),
            ("cycle", "events", 0, "next_discriminating_observable"),
        ):
            value = first_cycle()
            target = value
            for key in path:
                target = target[key]
            target["untrusted_extra"] = True
            with self.subTest(path=path):
                self.assertTrue(check(value))

    def test_each_mapping_rejects_missing_keys(self):
        for path in (
            (), ("cycle",), ("trigger",), ("cycle", "events", 0),
            ("cycle", "events", 0, "timing_source"),
            ("cycle", "events", 0, "completion_rule"),
            ("cycle", "events", 0, "falsification_outcomes", 0),
            ("cycle", "events", 0, "next_discriminating_observable"),
        ):
            value = first_cycle()
            target = value
            for key in path:
                target = target[key]
            target.pop(next(iter(target)))
            self.assertTrue(check(value))

    def test_completed_record_rejects_missing_and_extra_keys(self):
        for mode in ("extra", "missing"):
            value = next_cycle()
            record = value["trigger"]["completed_previous_event"]
            if mode == "extra":
                record["verified"] = True
            else:
                record.pop("due_at")
            self.assertTrue(check(value, AFTER_FINAL, baseline_event=first_cycle()))

    def test_malformed_enum_types_are_rejected_without_exceptions(self):
        for path in (("trigger", "kind"), ("cycle", "events", 0, "completion_rule", "kind"), ("cycle", "events", 0, "timing_source", "authority")):
            for malformed in ([], {}, True, None):
                value = first_cycle()
                target = value
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = malformed
                self.assertTrue(check(value))

    def test_tampered_baseline_final_pointer_is_rejected(self):
        baseline = first_cycle()
        baseline["cycle"]["final_expiry_event_id"] = "RELEASE_ONE"
        self.assertTrue(check(checked_cycle(), baseline_event=baseline))

    def test_historical_baseline_check_cannot_point_to_another_cycle(self):
        value = checked_cycle()
        value["trigger"]["baseline_cycle_id"] = "UNRELATED_CYCLE"
        self.assertTrue(check(value, baseline_mode=True))

    def test_current_and_legacy_nyse_public_calendar_urls_are_allowed(self):
        for url in (
            "https://www.nyse.com/markets/hours-calendars",
            "https://www.nyse.com/trade/hours-calendars",
        ):
            value = first_cycle()
            item = value["cycle"]["events"][0]
            item["timing_source"].update(authority="NYSE", endpoint_url=url)
            item["completion_rule"] = {
                "kind": "OFFICIAL_WINDOW_ENDED", "window_start": "2030-03-01T08:00:00-05:00",
            }
            self.assertEqual(check(value), [])

    def test_context_accepts_project_jst_or_aware_datetime(self):
        self.assertEqual(check(first_cycle(), pricing_as_of="2030-03-01 21:00 JST", now=datetime(2030, 3, 1, 12, tzinfo=timezone.utc)), [])
        self.assertTrue(check(first_cycle(), now=datetime(2030, 3, 1, 12)))

    def test_context_future_cutoff_and_observation_after_pricing_fail(self):
        self.assertTrue(check(first_cycle(), now="2030-03-01T11:50:00+00:00"))
        self.assertTrue(check(first_cycle(), observed_at="2030-03-01T12:01:00+00:00"))

    def test_validator_does_not_mutate_inputs(self):
        value = next_cycle()
        baseline = first_cycle()
        before = deepcopy((value, baseline))
        self.assertEqual(check(value, AFTER_FINAL, baseline_event=baseline), [])
        self.assertEqual((value, baseline), before)


class MaintenanceFormationClockTests(unittest.TestCase):
    """Constructed clocks/windows only; never actual market or author approval."""
    LOCK = "2030-03-01T15:01:00+00:00"
    FORMED = "2030-03-01T15:19:00+00:00"
    CLOCK = "2030-03-01T15:20:00+00:00"

    def plan(self):
        value = next_cycle()
        value['cycle']['opened_at'] = self.FORMED
        for event in value['cycle']['events']:
            event['timezone'] = 'UTC'
            event['due_at'] = datetime.fromisoformat(event['due_at']).astimezone(timezone.utc).isoformat()
            event['completion_rule'] = {'kind':'PUBLIC_OBSERVATION_WINDOW_ENDED', 'window_start':self.FORMED}
            event['timing_source'].update(authority='BINANCE',
                endpoint_url='https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h',
                scheduled_at=None, event_reference='CONSTRUCTED_PENDING_PUBLIC_WINDOW')
        return value

    def validate(self, value, **kwargs):
        return check(value, self.LOCK, now=self.CLOCK, baseline_event=first_cycle(), **kwargs)

    def test_old_locked_sources_new_actual_formation_future_pending_are_compatible(self):
        value = self.plan(); baseline = first_cycle(); original = deepcopy((value, baseline))
        self.assertEqual(check(value, self.LOCK, now=self.CLOCK, baseline_event=baseline), [])
        self.assertEqual(value['cycle']['opened_at'], self.FORMED)
        self.assertEqual(value['trigger']['completed_previous_event']['completed_at'], baseline['cycle']['events'][-1]['due_at'])
        self.assertEqual((value, baseline), original)

    def test_future_opened_at_is_rejected_without_clock_skew_allowance(self):
        value = self.plan(); value['cycle']['opened_at'] = '2030-03-01T15:20:00.000001+00:00'
        self.assertIn('event cycle cannot open after the trusted local clock', self.validate(value))

    def test_sources_cannot_borrow_new_plan_clock_to_exceed_locked_cutoff(self):
        for field in ('published_at', 'retrieved_at'):
            value = self.plan(); value['cycle']['events'][0]['timing_source'][field] = self.FORMED
            with self.subTest(field=field):
                self.assertTrue(any('observation/pricing cutoff' in error for error in self.validate(value)))

    def test_pending_at_clock_or_in_the_past_is_rejected(self):
        for due in ('2030-03-01T15:19:30+00:00', self.CLOCK):
            value = self.plan(); value['cycle']['events'][0]['due_at'] = due
            with self.subTest(due=due):
                self.assertIn('event newly established cycle must contain only future pending events', self.validate(value))

    def test_wrong_original_final_completion_and_deadline_are_rejected(self):
        for field in ('event_id', 'due_at', 'completed_at'):
            value = self.plan(); old = value['trigger']['completed_previous_event']
            old[field] = 'CONSTRUCTED_WRONG_EVENT' if field == 'event_id' else self.LOCK
            with self.subTest(field=field):
                self.assertTrue(self.validate(value))
        value = checked_cycle(); old = value['cycle']['events'][-1]
        old['due_at'] = '2030-03-01T16:00:00+00:00'; old['timing_source']['scheduled_at'] = old['due_at']
        self.assertTrue(self.validate(value))

    def test_historical_formation_is_read_at_original_publication_not_data_clock(self):
        value = self.plan()
        self.assertEqual(check(value, self.LOCK, now=self.CLOCK, baseline_mode=True), [])
        self.assertIn('event cycle cannot open after the trusted local clock',
                      check(value, self.LOCK, now=self.LOCK, baseline_mode=True))


if __name__ == "__main__":
    unittest.main()
