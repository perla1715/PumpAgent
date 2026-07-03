from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import AgentStateType, DataQualityStatus
from pumpagent.runtime.orchestrator import (
    AgentCycleResult,
    RuntimeOrchestrator,
    run_agent_cycle,
)


def make_snapshot(
    *,
    price_change_1m: float = 1.1,
    price_change_3m: float = 1.5,
    volume_spike_ratio: float = 8.1,
    oi_change_1m: float = 0.1,
    include_market_metrics: bool = True,
    optional_metrics: dict[str, object] | None = None,
) -> MarketSnapshot:
    metrics = {}
    if include_market_metrics:
        metrics.update(
            {
                "price_change_1m": price_change_1m,
                "price_change_3m": price_change_3m,
                "volume_spike_ratio": volume_spike_ratio,
                "oi_change_1m": oi_change_1m,
            }
        )
    if optional_metrics is not None:
        metrics.update(optional_metrics)

    return MarketSnapshot(
        event_id="snapshot-1",
        timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        price=101.0,
        ohlcv=(
            {
                "timestamp": "2026-07-01T11:59:00+00:00",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 40.0,
            },
            {
                "timestamp": "2026-07-01T12:00:00+00:00",
                "open": 100.0,
                "high": 103.0,
                "low": 100.0,
                "close": 101.0,
                "volume": 60.0,
            },
        ),
        volume=60.0,
        data_source="unit-test",
        data_quality_status=DataQualityStatus.VALID,
        optional_market_metrics=metrics,
    )


class RuntimeLoopTests(unittest.TestCase):
    def test_normal_processing(self) -> None:
        result = run_agent_cycle(make_snapshot(), previous_state="UNKNOWN")

        self.assertIsInstance(result, AgentCycleResult)
        self.assertEqual(result.snapshot.symbol, "BTCUSDT")
        self.assertEqual(result.structure_result.trend_structure, "rising_close_sequence")
        self.assertIn("volume_available", result.market_result.supporting_evidence)
        self.assertEqual(result.hypothesis.label, "Ignition attempt")
        self.assertEqual(result.previous_state, "UNKNOWN")
        self.assertEqual(result.new_state, "IGNITION")
        self.assertEqual(result.agent_state.current_state, AgentStateType.IGNITION)
        self.assertEqual(result.agent_state.previous_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.confidence, 50)
        self.assertEqual(len(result.evidence), 3)
        self.assertEqual(result.timestamp, result.snapshot.timestamp)

    def test_missing_data(self) -> None:
        snapshot = make_snapshot(include_market_metrics=False)

        result = RuntimeOrchestrator().process_market_update(snapshot)

        self.assertEqual(result.new_state, "UNKNOWN")
        self.assertEqual(result.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.confidence, 0)
        self.assertEqual(result.hypothesis.label, "No clear hypothesis")
        self.assertEqual(len(result.hypothesis.contradicting_evidence), 3)

    def test_unchanged_hypothesis(self) -> None:
        snapshot = make_snapshot()
        previous = run_agent_cycle(snapshot).hypothesis

        result = run_agent_cycle(
            snapshot,
            previous_state="IGNITION",
            previous_hypothesis=previous,
        )

        self.assertEqual(result.hypothesis.label, previous.label)
        self.assertEqual(result.hypothesis.confidence_score, previous.confidence_score)
        self.assertEqual(result.hypothesis.status, "UPDATED")
        self.assertEqual(result.previous_state, "IGNITION")
        self.assertEqual(result.new_state, "IGNITION")
        self.assertEqual(result.agent_state.previous_state, AgentStateType.IGNITION)
        self.assertEqual(result.agent_state.current_state, AgentStateType.IGNITION)

    def test_runtime_returns_canonical_agent_state(self) -> None:
        result = run_agent_cycle(make_snapshot(), previous_state="unknown")

        self.assertEqual(result.agent_state.current_state, AgentStateType.IGNITION)
        self.assertEqual(result.new_state, result.agent_state.current_state.name)
        self.assertEqual(result.previous_state, result.agent_state.previous_state.name)

    def test_hypothesis_update(self) -> None:
        previous = run_agent_cycle(make_snapshot()).hypothesis

        result = run_agent_cycle(
            make_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            ),
            previous_hypothesis=previous,
        )

        self.assertEqual(result.hypothesis.label, previous.label)
        self.assertEqual(result.hypothesis.status, "UPDATED")
        self.assertGreater(result.confidence, previous.confidence_score)

    def test_confidence_increase(self) -> None:
        previous = run_agent_cycle(make_snapshot()).hypothesis

        result = run_agent_cycle(
            make_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            ),
            previous_hypothesis=previous,
        )

        self.assertEqual(previous.confidence_score, 50)
        self.assertEqual(result.confidence, 90)
        self.assertEqual(result.hypothesis.status, "UPDATED")

    def test_confidence_decrease(self) -> None:
        previous = run_agent_cycle(
            make_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            )
        ).hypothesis

        result = run_agent_cycle(make_snapshot(), previous_hypothesis=previous)

        self.assertEqual(previous.confidence_score, 90)
        self.assertEqual(result.confidence, 50)
        self.assertEqual(result.hypothesis.status, "WEAKENED")


if __name__ == "__main__":
    unittest.main()
