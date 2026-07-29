from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
from unittest import TestCase, mock

from pumpagent.runtime.domain.enums import AgentStateType, ObservationEpisodeStatus
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.modules.observation_lifecycle.cycle_admission import (
    ClosedObservationCycleAdmissionResult,
    CycleAdmissionDecision,
)
from pumpagent.runtime.modules.observation_lifecycle.cycle_completion import (
    CycleCompletionStatus,
    ObservationCycleCompletionInput,
    prepare_completed_observation_cycle,
)
from pumpagent.runtime.modules.watchlist import WatchlistEntry, WatchlistManager


OPENED = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
CANDLE = OPENED + timedelta(minutes=5)
COMPLETED = CANDLE + timedelta(seconds=10)


def episode(**changes: object) -> ObservationEpisode:
    values: dict[str, object] = {
        "episode_id": "episode-1", "exchange": "bybit", "symbol": "BTCUSDT",
        "timeframe": "5m", "opening_timestamp": OPENED,
        "status": ObservationEpisodeStatus.ACTIVE,
        "scanner_trigger_timestamp": OPENED, "trigger_reasons": ("scanner",),
    }
    values.update(changes)
    return ObservationEpisode(**values)  # type: ignore[arg-type]


def entry(active: ObservationEpisode, **changes: object) -> WatchlistEntry:
    values: dict[str, object] = {
        "symbol": active.symbol, "exchange": active.exchange, "timeframe": active.timeframe,
        "first_seen": OPENED, "last_updated": OPENED,
        "current_agent_state": AgentStateType.UNKNOWN, "hypothesis_id": "keep-hypothesis",
        "confidence": 17, "observation_count": active.observation_cycle_count,
        "event_id": None, "active_episode": active, "active_episode_id": active.episode_id,
        "lifecycle_status": ObservationEpisodeStatus.ACTIVE,
        "latest_accepted_trigger_timestamp": active.scanner_trigger_timestamp,
        "latest_accepted_closed_candle_timestamp": active.latest_accepted_candle_timestamp,
        "diagnostic_metadata": {"keep": True},
    }
    values.update(changes)
    return WatchlistEntry(**values)  # type: ignore[arg-type]


def admission(active: ObservationEpisode, **changes: object) -> ClosedObservationCycleAdmissionResult:
    values: dict[str, object] = {
        "decision": CycleAdmissionDecision.ADMIT, "admitted": True,
        "episode_id": active.episode_id, "exchange": active.exchange,
        "symbol": active.symbol, "timeframe": active.timeframe,
        "candidate_closed_candle_timestamp": CANDLE,
        "previously_accepted_closed_candle_timestamp": active.latest_accepted_candle_timestamp,
        "admission_reason": "admitted", "runtime_allowed": True,
        "cycle_count_increment_allowed_after_runtime_success": True,
        "request_timestamp": CANDLE,
    }
    values.update(changes)
    return ClosedObservationCycleAdmissionResult(**values)  # type: ignore[arg-type]


def completion(active: ObservationEpisode | None = None, **changes: object) -> ObservationCycleCompletionInput:
    active = active or episode()
    values: dict[str, object] = {
        "admission_result": admission(active), "active_episode": active,
        "watchlist_entry": entry(active), "runtime_event_id": "runtime-1",
        "runtime_completion_timestamp": COMPLETED,
        "accepted_closed_candle_timestamp": CANDLE,
        "runtime_diagnostics": {"duration_ms": 8},
    }
    values.update(changes)
    return ObservationCycleCompletionInput(**values)  # type: ignore[arg-type]


class CycleCompletionTests(TestCase):
    def test_valid_completion_prepares_and_atomically_applies_one_cycle(self) -> None:
        source = completion()
        before_episode = source.active_episode.to_dict()
        before_entry = source.watchlist_entry.to_dict()
        result = prepare_completed_observation_cycle(source)

        self.assertIs(result.status, CycleCompletionStatus.COMPLETED)
        self.assertTrue(result.completed)
        self.assertEqual((result.previous_observation_cycle_count, result.resulting_observation_cycle_count), (0, 1))
        self.assertEqual(result.updated_active_episode.episode_id, source.active_episode.episode_id)
        self.assertEqual(result.updated_active_episode.opening_timestamp, OPENED)
        self.assertEqual(result.updated_active_episode.latest_accepted_candle_timestamp, CANDLE)
        self.assertEqual(result.resulting_watchlist_entry.latest_runtime_event_id, "runtime-1")
        self.assertIs(result.resulting_watchlist_entry.current_agent_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.resulting_watchlist_entry.hypothesis_id, "keep-hypothesis")
        self.assertEqual(result.resulting_watchlist_entry.confidence, 17)
        self.assertEqual(source.active_episode.to_dict(), before_episode)
        self.assertEqual(source.watchlist_entry.to_dict(), before_entry)

        manager = WatchlistManager()
        manager._entries[("bybit", "BTCUSDT", "5m")] = source.watchlist_entry  # noqa: SLF001
        stored = manager.apply_completed_observation_cycle(result)
        self.assertEqual(stored, result.resulting_watchlist_entry)

    def test_later_cycle_increments_one_to_two(self) -> None:
        previous = episode(latest_accepted_candle_timestamp=CANDLE, observation_cycle_count=1)
        next_candle = CANDLE + timedelta(minutes=5)
        value = completion(
            previous,
            admission_result=admission(previous, candidate_closed_candle_timestamp=next_candle),
            watchlist_entry=entry(previous, event_id="runtime-1"),
            runtime_event_id="runtime-2", accepted_closed_candle_timestamp=next_candle,
            runtime_completion_timestamp=next_candle + timedelta(seconds=1),
        )
        result = prepare_completed_observation_cycle(value)
        self.assertEqual(result.resulting_observation_cycle_count, 2)
        self.assertEqual(result.resulting_latest_accepted_candle_timestamp, next_candle)

    def test_rejections_are_precise_and_never_prepare_mutation(self) -> None:
        active = episode()
        cases = (
            (completion(active, admission_result=admission(active, decision=CycleAdmissionDecision.DUPLICATE, admitted=False, runtime_allowed=False, cycle_count_increment_allowed_after_runtime_success=False)), CycleCompletionStatus.NOT_ADMITTED),
            (completion(active, admission_result=admission(active, episode_id="other")), CycleCompletionStatus.EPISODE_MISMATCH),
            (completion(active, admission_result=admission(active, exchange="binance")), CycleCompletionStatus.IDENTITY_MISMATCH),
            (completion(active, admission_result=admission(active, symbol="ETHUSDT")), CycleCompletionStatus.IDENTITY_MISMATCH),
            (completion(active, admission_result=admission(active, timeframe="15m")), CycleCompletionStatus.IDENTITY_MISMATCH),
            (completion(active, runtime_event_id="  "), CycleCompletionStatus.INVALID_RUNTIME_RESULT),
            (completion(active, runtime_completion_timestamp=CANDLE - timedelta(seconds=1)), CycleCompletionStatus.INVALID_CONTEXT),
            (completion(active, watchlist_entry=entry(replace(active, episode_id="other"))), CycleCompletionStatus.INVALID_CONTEXT),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                result = prepare_completed_observation_cycle(value)
                self.assertIs(result.status, expected)
                self.assertFalse(result.completed)
                self.assertIsNone(result.updated_active_episode)
                self.assertIsNone(result.resulting_watchlist_entry)

    def test_duplicate_older_and_reused_event_are_rejected(self) -> None:
        current = episode(latest_accepted_candle_timestamp=CANDLE, observation_cycle_count=1)
        duplicate = completion(current, watchlist_entry=entry(current, event_id="runtime-1"))
        older_candle = CANDLE - timedelta(minutes=5)
        older = completion(
            current, admission_result=admission(current, candidate_closed_candle_timestamp=older_candle),
            watchlist_entry=entry(current, event_id="runtime-1"),
            accepted_closed_candle_timestamp=older_candle, runtime_event_id="runtime-old",
        )
        new_candle = CANDLE + timedelta(minutes=5)
        reused = completion(
            current, admission_result=admission(current, candidate_closed_candle_timestamp=new_candle),
            watchlist_entry=entry(current, event_id="runtime-1"), runtime_event_id="runtime-1",
            accepted_closed_candle_timestamp=new_candle,
            runtime_completion_timestamp=new_candle + timedelta(seconds=1),
        )
        self.assertIs(prepare_completed_observation_cycle(duplicate).status, CycleCompletionStatus.DUPLICATE_COMPLETION)
        self.assertIs(prepare_completed_observation_cycle(older).status, CycleCompletionStatus.OLDER_COMPLETION)
        self.assertIs(prepare_completed_observation_cycle(reused).status, CycleCompletionStatus.INVALID_RUNTIME_RESULT)

    def test_application_detects_stale_storage_and_preserves_other_markets(self) -> None:
        value = completion()
        result = prepare_completed_observation_cycle(value)
        eth_episode = episode(episode_id="episode-eth", symbol="ETHUSDT")
        eth = entry(eth_episode)
        manager = WatchlistManager()
        manager._entries[("bybit", "BTCUSDT", "5m")] = replace(value.watchlist_entry, confidence=18)  # noqa: SLF001
        manager._entries[("bybit", "ETHUSDT", "5m")] = eth  # noqa: SLF001
        with self.assertRaisesRegex(ValueError, "state changed"):
            manager.apply_completed_observation_cycle(result)
        self.assertEqual(manager.get(symbol="ETHUSDT", exchange="bybit", timeframe="5m"), eth)

    def test_deterministic_immutable_serializable_and_no_runtime_or_analytics(self) -> None:
        value = completion()
        with mock.patch("pumpagent.runtime.orchestrator.runtime_loop.RuntimeOrchestrator") as runtime, \
             mock.patch("pumpagent.runtime.modules.hypothesis.engine.build_hypothesis_package") as hypothesis:
            first = prepare_completed_observation_cycle(value)
            second = prepare_completed_observation_cycle(value)
        self.assertEqual(first, second)
        runtime.assert_not_called()
        hypothesis.assert_not_called()
        json.dumps(value.to_dict())
        json.dumps(first.to_dict())
        with self.assertRaises(FrozenInstanceError):
            first.completed = False  # type: ignore[misc]
