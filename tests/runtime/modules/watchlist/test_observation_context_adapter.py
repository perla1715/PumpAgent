"""Tests for the Watchlist entry to Observation boundary context adapter."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from pumpagent.runtime.domain.base import FrozenDict
from pumpagent.runtime.domain.enums import AgentStateType, ObservationEpisodeStatus
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.modules.watchlist import (
    WatchlistEntry,
    build_watchlist_observation_context,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
OPENED = NOW - timedelta(minutes=20)
CANDLE = NOW - timedelta(minutes=5)


def episode(**overrides: object) -> ObservationEpisode:
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "opening_timestamp": OPENED,
        "status": ObservationEpisodeStatus.ACTIVE,
        "scanner_trigger_timestamp": OPENED,
        "trigger_reasons": ("volume_growth",),
        "latest_accepted_candle_timestamp": CANDLE,
        "observation_cycle_count": 3,
    }
    values.update(overrides)
    return ObservationEpisode(**values)  # type: ignore[arg-type]


def entry(active: ObservationEpisode | None = None, **overrides: object) -> WatchlistEntry:
    active = episode() if active is None and not overrides.pop("inactive", False) else active
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "exchange": "bybit",
        "timeframe": "5m",
        "first_seen": OPENED,
        "last_updated": NOW,
        "current_agent_state": AgentStateType.UNKNOWN,
        "hypothesis_id": "analytical-hypothesis",
        "confidence": 99,
        "observation_count": active.observation_cycle_count if active else 8,
        "event_id": "runtime-event-7",
        "active_episode": active,
        "active_episode_id": active.episode_id if active else None,
        "lifecycle_status": active.status if active else ObservationEpisodeStatus.CLOSED,
        "latest_accepted_trigger_timestamp": (
            active.scanner_trigger_timestamp if active else None
        ),
        "latest_accepted_closed_candle_timestamp": (
            active.latest_accepted_candle_timestamp if active else None
        ),
        "diagnostic_metadata": {"nested": {"values": [1, 2]}},
    }
    values.update(overrides)
    return WatchlistEntry(**values)  # type: ignore[arg-type]


class ObservationContextAdapterTests(unittest.TestCase):
    def test_active_entry_maps_exact_lifecycle_state(self) -> None:
        source = entry()
        context = build_watchlist_observation_context(source)
        self.assertEqual(context.active_episode_id, "episode-1")
        self.assertEqual(context.active_episode_opening_timestamp, OPENED)
        self.assertEqual(context.latest_accepted_trigger_timestamp, OPENED)
        self.assertEqual(context.latest_accepted_closed_candle_timestamp, CANDLE)
        self.assertEqual(context.observation_count, 3)
        self.assertEqual(context.lifecycle_status, ObservationEpisodeStatus.ACTIVE)
        self.assertEqual(context.latest_runtime_event_id, "runtime-event-7")
        self.assertTrue(context.has_active_episode)

    def test_unknown_and_analytical_values_do_not_change_membership(self) -> None:
        first = build_watchlist_observation_context(entry())
        second = build_watchlist_observation_context(
            entry(current_agent_state=AgentStateType.IGNITION, hypothesis_id="other", confidence=0)
        )
        self.assertTrue(first.has_active_episode)
        self.assertEqual(first, second)

    def test_identity_id_status_timestamp_and_count_mismatches_are_rejected(self) -> None:
        corruptions = (
            ("symbol", "ETHUSDT"),
            ("active_episode_id", "wrong"),
            ("lifecycle_status", ObservationEpisodeStatus.CLOSED),
            ("latest_accepted_trigger_timestamp", OPENED - timedelta(seconds=1)),
            ("latest_accepted_closed_candle_timestamp", NOW),
            ("observation_count", 4),
        )
        for name, value in corruptions:
            source = entry()
            object.__setattr__(source, name, value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_watchlist_observation_context(source)

    def test_closed_episode_cannot_be_mapped_as_active(self) -> None:
        source = entry()
        object.__setattr__(source.active_episode, "status", ObservationEpisodeStatus.CLOSED)
        with self.assertRaises(ValueError):
            build_watchlist_observation_context(source)

    def test_inactive_entry_excludes_completed_and_analytical_episode_data(self) -> None:
        completed = replace(
            episode(),
            status=ObservationEpisodeStatus.CLOSED,
            closing_timestamp=NOW,
            closure_reason="done",
        )
        source = entry(active=None, inactive=True, latest_completed_episode=completed)
        context = build_watchlist_observation_context(source)
        self.assertFalse(context.has_active_episode)
        self.assertIsNone(context.active_episode_id)
        self.assertEqual(context.observation_count, 0)
        self.assertIsNone(context.latest_runtime_event_id)
        self.assertEqual(dict(context.diagnostic_metadata), {})
        self.assertNotIn(completed.episode_id, json.dumps(context.to_dict()))

    def test_missing_entry_requires_identity_and_creates_empty_context(self) -> None:
        context = build_watchlist_observation_context(
            None, exchange="bybit", symbol="BTCUSDT", timeframe="5m"
        )
        self.assertFalse(context.has_active_episode)
        self.assertEqual(context.observation_count, 0)
        with self.assertRaises(ValueError):
            build_watchlist_observation_context(None)

    def test_source_is_unchanged_output_is_deterministic_frozen_and_serializable(self) -> None:
        source = entry()
        before = source.to_dict()
        first = build_watchlist_observation_context(source)
        second = build_watchlist_observation_context(source)
        self.assertEqual(first, second)
        self.assertEqual(source.to_dict(), before)
        self.assertIsInstance(first.diagnostic_metadata, FrozenDict)
        self.assertIsInstance(first.diagnostic_metadata["nested"], FrozenDict)
        self.assertIsInstance(first.diagnostic_metadata["nested"]["values"], tuple)
        with self.assertRaises(FrozenInstanceError):
            first.observation_count = 9
        json.dumps(first.to_dict())

    def test_naive_stored_timestamp_is_rejected(self) -> None:
        source = entry()
        naive = datetime(2026, 7, 15, 12, 0)
        object.__setattr__(source, "latest_accepted_trigger_timestamp", naive)
        object.__setattr__(source.active_episode, "scanner_trigger_timestamp", naive)
        with self.assertRaises(ValueError):
            build_watchlist_observation_context(source)

    def test_non_serializable_diagnostics_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_watchlist_observation_context(
                entry(diagnostic_metadata={"invalid": object()})
            )


if __name__ == "__main__":
    unittest.main()
