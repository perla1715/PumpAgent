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
from pumpagent.runtime.modules.temporal_confidence import (
    CONFIDENCE_TREND_IMPROVING,
    CONFIDENCE_TREND_STABLE,
    CONFIDENCE_TREND_UNKNOWN,
    CONFIDENCE_TREND_WEAKENING,
    TemporalConfidenceManager,
)
from pumpagent.runtime.modules.watchlist import WatchlistEntry


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=1)


def make_entry(
    *,
    confidence: int,
    timestamp: datetime = NOW,
    event_id: str = "event-1",
    observation_count: int = 1,
) -> WatchlistEntry:
    return WatchlistEntry(
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        first_seen=NOW,
        last_updated=timestamp,
        current_agent_state=AgentStateType.IGNITION,
        hypothesis_id="hypothesis-1",
        confidence=confidence,
        observation_count=observation_count,
        event_id=event_id,
    )


class TemporalConfidenceManagerTests(unittest.TestCase):
    def test_initialization(self) -> None:
        manager = TemporalConfidenceManager()

        state = manager.initialize(make_entry(confidence=50))

        self.assertEqual(state.current_confidence, 50)
        self.assertIsNone(state.previous_confidence)
        self.assertIsNone(state.confidence_delta)
        self.assertEqual(state.trend, CONFIDENCE_TREND_UNKNOWN)
        self.assertEqual(state.update_count, 1)
        self.assertEqual(state.last_updated, NOW)

    def test_improving_confidence(self) -> None:
        manager = TemporalConfidenceManager()
        manager.initialize(make_entry(confidence=50))

        state = manager.update(
            make_entry(
                confidence=90,
                timestamp=LATER,
                event_id="event-2",
                observation_count=2,
            )
        )

        self.assertEqual(state.previous_confidence, 50)
        self.assertEqual(state.current_confidence, 90)
        self.assertEqual(state.confidence_delta, 40)
        self.assertEqual(state.trend, CONFIDENCE_TREND_IMPROVING)
        self.assertEqual(state.update_count, 2)

    def test_stable_confidence(self) -> None:
        manager = TemporalConfidenceManager()
        manager.initialize(make_entry(confidence=50))

        state = manager.update(make_entry(confidence=50, timestamp=LATER))

        self.assertEqual(state.confidence_delta, 0)
        self.assertEqual(state.trend, CONFIDENCE_TREND_STABLE)

    def test_weakening_confidence(self) -> None:
        manager = TemporalConfidenceManager()
        manager.initialize(make_entry(confidence=90))

        state = manager.update(make_entry(confidence=50, timestamp=LATER))

        self.assertEqual(state.confidence_delta, -40)
        self.assertEqual(state.trend, CONFIDENCE_TREND_WEAKENING)

    def test_multiple_updates(self) -> None:
        manager = TemporalConfidenceManager()
        manager.initialize(make_entry(confidence=50))
        manager.update(make_entry(confidence=60, timestamp=LATER))

        state = manager.update(
            make_entry(
                confidence=70,
                timestamp=LATER + timedelta(minutes=1),
                event_id="event-3",
                observation_count=3,
            )
        )

        self.assertEqual(state.previous_confidence, 60)
        self.assertEqual(state.current_confidence, 70)
        self.assertEqual(state.confidence_delta, 10)
        self.assertEqual(state.update_count, 3)

    def test_missing_history_initializes_unknown(self) -> None:
        manager = TemporalConfidenceManager()

        state = manager.update(make_entry(confidence=50))

        self.assertEqual(state.trend, CONFIDENCE_TREND_UNKNOWN)
        self.assertEqual(state.update_count, 1)
        self.assertIsNone(state.confidence_delta)

    def test_deterministic_behavior(self) -> None:
        first = TemporalConfidenceManager()
        second = TemporalConfidenceManager()

        first.initialize(make_entry(confidence=50))
        second.initialize(make_entry(confidence=50))

        self.assertEqual(
            first.update(make_entry(confidence=90, timestamp=LATER)),
            second.update(make_entry(confidence=90, timestamp=LATER)),
        )

    def test_reset(self) -> None:
        manager = TemporalConfidenceManager()
        state = manager.initialize(make_entry(confidence=50))

        removed = manager.reset(symbol="BTCUSDT", exchange="binance", timeframe="1m")

        self.assertEqual(removed, state)
        self.assertIsNone(
            manager.get(symbol="BTCUSDT", exchange="binance", timeframe="1m")
        )


if __name__ == "__main__":
    unittest.main()
