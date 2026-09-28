from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus, ObservationEpisodeStatus
from pumpagent.runtime.modules.observation_lifecycle.cycle_admission import (
    ClosedObservationCycleAdmissionInput,
    CycleAdmissionDecision,
    evaluate_closed_observation_cycle_admission,
)
from pumpagent.runtime.modules.watchlist import WatchlistObservationContext

NOW = datetime(2026, 7, 15, 12, 10, tzinfo=timezone.utc)
PREVIOUS = NOW - timedelta(minutes=10)
CANDIDATE = NOW - timedelta(minutes=5)


def snapshot(**changes: object) -> MarketSnapshot:
    values: dict[str, object] = dict(
        event_id="event-2", timestamp=NOW, symbol="BTCUSDT", exchange="bybit",
        timeframe="5m", price=101.0,
        ohlcv=({"timestamp": CANDIDATE.isoformat(), "open": 100.0, "high": 102.0,
                "low": 99.0, "close": 101.0, "volume": 12.0},),
        volume=12.0, data_source="fixture", data_quality_status=DataQualityStatus.VALID,
    )
    values.update(changes)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def context(active: bool = True, **changes: object) -> WatchlistObservationContext:
    values: dict[str, object] = dict(
        exchange="bybit", symbol="BTCUSDT", timeframe="5m", has_active_episode=active,
        observation_count=4, diagnostic_metadata={"agent_state": "UNKNOWN", "other_market": "ETHUSDT"},
    )
    if active:
        values.update(active_episode_id="episode-1", active_episode_opening_timestamp=PREVIOUS - timedelta(minutes=5),
                      latest_accepted_trigger_timestamp=PREVIOUS, latest_accepted_closed_candle_timestamp=PREVIOUS,
                      lifecycle_status=ObservationEpisodeStatus.ACTIVE, latest_runtime_event_id="runtime-1")
    values.update(changes)
    return WatchlistObservationContext(**values)  # type: ignore[arg-type]


def admission(**changes: object) -> ClosedObservationCycleAdmissionInput:
    values: dict[str, object] = dict(snapshot=snapshot(), watchlist_context=context(),
                                     latest_closed_candle_timestamp=CANDIDATE, request_timestamp=NOW)
    values.update(changes)
    return ClosedObservationCycleAdmissionInput(**values)  # type: ignore[arg-type]


class ClosedCycleAdmissionTests(unittest.TestCase):
    def test_newer_closed_5m_is_admitted_even_with_analytical_unknown(self) -> None:
        result = evaluate_closed_observation_cycle_admission(admission())
        self.assertEqual(result.decision, CycleAdmissionDecision.ADMIT)
        self.assertTrue(result.admitted)
        self.assertTrue(result.runtime_allowed)
        self.assertTrue(result.cycle_count_increment_allowed_after_runtime_success)
        self.assertEqual(result.episode_id, "episode-1")

    def test_duplicate_and_older_are_rejected(self) -> None:
        duplicate_snapshot = snapshot(ohlcv=({"timestamp": PREVIOUS.isoformat(), "open": 100,
                                              "high": 102, "low": 99, "close": 101,
                                              "volume": 12},))
        older_timestamp = PREVIOUS - timedelta(minutes=5)
        older_snapshot = snapshot(ohlcv=({"timestamp": older_timestamp.isoformat(), "open": 100,
                                          "high": 102, "low": 99, "close": 101,
                                          "volume": 12},))
        duplicate = evaluate_closed_observation_cycle_admission(
            admission(snapshot=duplicate_snapshot, latest_closed_candle_timestamp=PREVIOUS))
        older = evaluate_closed_observation_cycle_admission(
            admission(snapshot=older_snapshot, latest_closed_candle_timestamp=older_timestamp))
        self.assertEqual(duplicate.decision, CycleAdmissionDecision.DUPLICATE)
        self.assertEqual(older.decision, CycleAdmissionDecision.OLDER)
        self.assertFalse(duplicate.runtime_allowed)
        self.assertFalse(older.runtime_allowed)

    def test_no_episode_identity_and_timeframe_failures(self) -> None:
        cases = (
            (admission(watchlist_context=context(False)), CycleAdmissionDecision.NO_ACTIVE_EPISODE),
            (admission(snapshot=snapshot(exchange="binance")), CycleAdmissionDecision.IDENTITY_MISMATCH),
            (admission(snapshot=snapshot(symbol="ETHUSDT")), CycleAdmissionDecision.IDENTITY_MISMATCH),
            (admission(snapshot=snapshot(timeframe="1m")), CycleAdmissionDecision.UNSUPPORTED_TIMEFRAME),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(evaluate_closed_observation_cycle_admission(value).decision, expected)

    def test_explicit_or_marked_closed_boundary_is_required(self) -> None:
        open_result = evaluate_closed_observation_cycle_admission(
            admission(latest_closed_candle_timestamp=None))
        marked = snapshot(ohlcv=({"timestamp": CANDIDATE.isoformat(), "open": 100, "high": 102,
                                  "low": 99, "close": 101, "volume": 12, "is_closed": True},))
        closed_result = evaluate_closed_observation_cycle_admission(
            admission(snapshot=marked, latest_closed_candle_timestamp=None))
        self.assertEqual(open_result.decision, CycleAdmissionDecision.NOT_CLOSED)
        self.assertEqual(closed_result.decision, CycleAdmissionDecision.ADMIT)

    def test_invalid_quality_missing_ohlcv_and_naive_timestamp(self) -> None:
        cases = (
            admission(snapshot=snapshot(data_quality_status=DataQualityStatus.CORRUPTED)),
            admission(snapshot=snapshot(ohlcv=())),
            admission(latest_closed_candle_timestamp=CANDIDATE.replace(tzinfo=None)),
        )
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(evaluate_closed_observation_cycle_admission(value).decision,
                                 CycleAdmissionDecision.INVALID)

    def test_is_pure_deterministic_immutable_and_serializable(self) -> None:
        value = admission()
        before_input = value.to_dict()
        before_context = value.watchlist_context.to_dict()
        first = evaluate_closed_observation_cycle_admission(value)
        second = evaluate_closed_observation_cycle_admission(value)
        self.assertEqual(first, second)
        self.assertEqual(value.to_dict(), before_input)
        self.assertEqual(value.watchlist_context.to_dict(), before_context)
        self.assertEqual(value.watchlist_context.observation_count, 4)
        self.assertEqual(value.watchlist_context.latest_accepted_closed_candle_timestamp, PREVIOUS)
        self.assertNotIn("other_market", first.to_dict())
        json.dumps(first.to_dict())
        with self.assertRaises(FrozenInstanceError):
            first.admitted = False  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            value.request_timestamp = PREVIOUS  # type: ignore[misc]

    def test_no_runtime_dependency_or_invocation(self) -> None:
        result = evaluate_closed_observation_cycle_admission(admission())
        self.assertTrue(result.runtime_allowed)  # authorization only; no callable is accepted
        self.assertFalse(hasattr(admission(), "runtime_orchestrator"))


if __name__ == "__main__":
    unittest.main()
