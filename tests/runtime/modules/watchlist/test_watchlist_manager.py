from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain.enums import AgentStateType
from pumpagent.runtime.modules.agent_state import build_agent_state_from_market_hypothesis
from pumpagent.runtime.modules.hypothesis import build_hypothesis
from pumpagent.runtime.modules.watchlist import (
    WATCHLIST_ACTION_NONE,
    WATCHLIST_ACTION_REGISTERED,
    WATCHLIST_ACTION_UPDATED,
    WatchlistManager,
)


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=1)


def make_hypothesis(**overrides: float):
    data = {
        "price_change_1m": 1.1,
        "price_change_3m": 1.5,
        "volume_spike_ratio": 8.1,
        "oi_change_1m": 0.1,
    }
    data.update(overrides)
    return build_hypothesis(data)


class WatchlistManagerTests(unittest.TestCase):
    def test_first_registration(self) -> None:
        manager = WatchlistManager()
        entry = manager.register(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=NOW,
            current_agent_state=AgentStateType.IGNITION,
            hypothesis_id="hypothesis-1",
            confidence=50,
            event_id="event-1",
        )

        self.assertEqual(entry.symbol, "BTCUSDT")
        self.assertEqual(entry.first_seen, NOW)
        self.assertEqual(entry.last_updated, NOW)
        self.assertEqual(entry.observation_count, 1)
        self.assertEqual(
            manager.get(symbol="BTCUSDT", exchange="binance", timeframe="1m"),
            entry,
        )

    def test_repeated_updates_increment_observation_count(self) -> None:
        manager = WatchlistManager()
        manager.register(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=NOW,
            current_agent_state=AgentStateType.IGNITION,
            hypothesis_id="hypothesis-1",
            confidence=50,
            event_id="event-1",
        )

        entry = manager.update(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=LATER,
            current_agent_state=AgentStateType.CONTINUATION_ALIVE,
            hypothesis_id="hypothesis-2",
            confidence=80,
            event_id="event-2",
        )

        self.assertEqual(entry.observation_count, 2)
        self.assertEqual(entry.first_seen, NOW)
        self.assertEqual(entry.last_updated, LATER)
        self.assertEqual(entry.current_agent_state, AgentStateType.CONTINUATION_ALIVE)

    def test_unknown_states_are_ignored(self) -> None:
        manager = WatchlistManager()
        hypothesis = build_hypothesis(
            {
                "price_change_1m": 0.0,
                "price_change_3m": 0.0,
                "volume_spike_ratio": 1.0,
                "oi_change_1m": 0.0,
            }
        )
        agent_state = build_agent_state_from_market_hypothesis(
            hypothesis,
            event_id="event-1",
        )

        action, count = manager.track_cycle(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=NOW,
            agent_state=agent_state,
            hypothesis=hypothesis,
            confidence=0,
            event_id="event-1",
        )

        self.assertEqual(action, WATCHLIST_ACTION_NONE)
        self.assertEqual(count, 0)
        self.assertEqual(manager.list_active(), ())

    def test_updating_confidence_hypothesis_and_event_id(self) -> None:
        manager = WatchlistManager()
        manager.register(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=NOW,
            current_agent_state=AgentStateType.IGNITION,
            hypothesis_id="hypothesis-1",
            confidence=50,
            event_id="event-1",
        )

        entry = manager.update(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=LATER,
            current_agent_state=AgentStateType.IGNITION,
            hypothesis_id="hypothesis-2",
            confidence=90,
            event_id="event-2",
        )

        self.assertEqual(entry.confidence, 90)
        self.assertEqual(entry.hypothesis_id, "hypothesis-2")
        self.assertEqual(entry.event_id, "event-2")

    def test_explicit_removal(self) -> None:
        manager = WatchlistManager()
        entry = manager.register(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=NOW,
            current_agent_state=AgentStateType.IGNITION,
            hypothesis_id="hypothesis-1",
            confidence=50,
            event_id="event-1",
        )

        removed = manager.remove(symbol="BTCUSDT", exchange="binance", timeframe="1m")

        self.assertEqual(removed, entry)
        self.assertIsNone(
            manager.get(symbol="BTCUSDT", exchange="binance", timeframe="1m")
        )

    def test_track_cycle_registers_then_updates(self) -> None:
        manager = WatchlistManager()
        hypothesis = make_hypothesis()
        agent_state = build_agent_state_from_market_hypothesis(
            hypothesis,
            event_id="event-1",
        )

        action, count = manager.track_cycle(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=NOW,
            agent_state=agent_state,
            hypothesis=hypothesis,
            confidence=50,
            event_id="event-1",
        )
        second_action, second_count = manager.track_cycle(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            timestamp=LATER,
            agent_state=agent_state,
            hypothesis=hypothesis,
            confidence=50,
            event_id="event-2",
        )

        self.assertEqual(action, WATCHLIST_ACTION_REGISTERED)
        self.assertEqual(count, 1)
        self.assertEqual(second_action, WATCHLIST_ACTION_UPDATED)
        self.assertEqual(second_count, 2)


if __name__ == "__main__":
    unittest.main()
