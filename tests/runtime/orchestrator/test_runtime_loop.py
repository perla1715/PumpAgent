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
from pumpagent.runtime.modules.hypothesis import HypothesisHistory
from pumpagent.runtime.modules.temporal_confidence import (
    CONFIDENCE_TREND_IMPROVING,
    CONFIDENCE_TREND_UNKNOWN,
    CONFIDENCE_TREND_WEAKENING,
)
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
        self.assertEqual(
            result.event_id,
            "agent-cycle:binance:BTCUSDT:1m:snapshot-1:2026-07-01T12:00:00+00:00",
        )
        self.assertEqual(result.structure_result.trend_structure, "rising_close_sequence")
        self.assertIn("volume_available", result.market_result.supporting_evidence)
        self.assertEqual(result.hypothesis.label, "Ignition attempt")
        self.assertEqual(result.previous_state, "UNKNOWN")
        self.assertEqual(result.new_state, "IGNITION")
        self.assertEqual(result.agent_state.current_state, AgentStateType.IGNITION)
        self.assertEqual(result.agent_state.previous_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.agent_state.event_id, result.event_id)
        self.assertEqual(result.confidence, 50)
        self.assertEqual(len(result.evidence), 3)
        self.assertEqual(result.timestamp, result.snapshot.timestamp)
        self.assertEqual(result.watchlist_action, "REGISTERED")
        self.assertEqual(result.watchlist_observation_count, 1)
        self.assertIsNotNone(result.temporal_confidence)
        self.assertTrue(result.evidence_summary.has_structural_evidence)
        self.assertTrue(result.evidence_summary.has_market_evidence)
        self.assertTrue(result.evidence_summary.has_temporal_evidence)
        self.assertEqual(result.evidence_summary.evidence_count, 3)
        self.assertEqual(result.evidence_summary.strongest_evidence_type, "structural")
        self.assertIsNotNone(result.hypothesis_snapshot)
        self.assertEqual(result.hypothesis_snapshot.state, "IGNITION")
        self.assertEqual(result.hypothesis_snapshot.confidence, 50)
        self.assertEqual(result.hypothesis_snapshot.confidence_trend, "UNKNOWN")
        self.assertEqual(result.hypothesis_snapshot.label, "mixed_evidence")
        self.assertEqual(result.hypothesis_snapshot.created_at, result.timestamp)
        self.assertEqual(result.hypothesis_history_size, 1)
        self.assertIsNotNone(result.history_trend_summary)
        self.assertEqual(result.history_trend_summary.confidence_trend, "UNKNOWN")
        self.assertEqual(result.history_trend_summary.evidence_score_trend, "UNKNOWN")
        self.assertEqual(result.history_trend_summary.label_stability, "UNKNOWN")
        self.assertEqual(result.history_trend_summary.sample_size, 1)
        self.assertEqual(result.confidence_trend, CONFIDENCE_TREND_UNKNOWN)
        self.assertIsNone(result.confidence_delta)

    def test_missing_data(self) -> None:
        snapshot = make_snapshot(include_market_metrics=False)

        result = RuntimeOrchestrator().process_market_update(snapshot)

        self.assertEqual(result.new_state, "UNKNOWN")
        self.assertEqual(result.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.confidence, 0)
        self.assertEqual(result.hypothesis.label, "No clear hypothesis")
        self.assertEqual(len(result.hypothesis.contradicting_evidence), 3)
        self.assertEqual(result.watchlist_action, "NONE")
        self.assertEqual(result.watchlist_observation_count, 0)
        self.assertIsNone(result.temporal_confidence)
        self.assertTrue(result.evidence_summary.has_structural_evidence)
        self.assertTrue(result.evidence_summary.has_market_evidence)
        self.assertFalse(result.evidence_summary.has_temporal_evidence)
        self.assertEqual(result.evidence_summary.evidence_count, 2)
        self.assertIsNotNone(result.hypothesis_snapshot)
        self.assertEqual(result.hypothesis_snapshot.state, "UNKNOWN")
        self.assertEqual(result.hypothesis_snapshot.confidence, 0)
        self.assertEqual(result.hypothesis_snapshot.label, "mixed_evidence")
        self.assertEqual(result.hypothesis_history_size, 1)
        self.assertEqual(result.confidence_trend, CONFIDENCE_TREND_UNKNOWN)
        self.assertIsNone(result.confidence_delta)

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
        self.assertEqual(result.agent_state.event_id, result.event_id)
        self.assertEqual(result.new_state, result.agent_state.current_state.name)
        self.assertEqual(result.previous_state, result.agent_state.previous_state.name)

    def test_runtime_event_id_is_stable_for_same_snapshot(self) -> None:
        snapshot = make_snapshot()

        first = run_agent_cycle(snapshot)
        second = run_agent_cycle(snapshot)

        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.agent_state.event_id, second.agent_state.event_id)

    def test_agent_cycle_result_has_snapshot_compatibility_defaults(self) -> None:
        self.assertIsNone(
            AgentCycleResult.__dataclass_fields__["hypothesis_snapshot"].default
        )
        self.assertEqual(
            AgentCycleResult.__dataclass_fields__["hypothesis_history_size"].default,
            0,
        )
        self.assertIsNone(
            AgentCycleResult.__dataclass_fields__["history_trend_summary"].default
        )

    def test_runtime_updates_dynamic_watchlist(self) -> None:
        orchestrator = RuntimeOrchestrator()
        snapshot = make_snapshot()

        first = orchestrator.process_market_update(snapshot)
        second = orchestrator.process_market_update(
            snapshot,
            previous_state=first.new_state,
            previous_hypothesis=first.hypothesis,
        )
        entry = orchestrator.watchlist.get(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        self.assertEqual(first.watchlist_action, "REGISTERED")
        self.assertEqual(second.watchlist_action, "UPDATED")
        self.assertEqual(second.watchlist_observation_count, 2)
        self.assertIsNotNone(second.temporal_confidence)
        self.assertEqual(second.temporal_confidence.update_count, 2)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.observation_count, 2)
        self.assertEqual(entry.event_id, second.event_id)
        self.assertEqual(second.hypothesis_history_size, 2)
        self.assertIsNotNone(second.history_trend_summary)
        self.assertEqual(second.history_trend_summary.confidence_trend, "STABLE")
        self.assertEqual(second.history_trend_summary.evidence_score_trend, "IMPROVING")
        self.assertEqual(second.history_trend_summary.label_stability, "STABLE")
        self.assertEqual(second.history_trend_summary.sample_size, 2)

    def test_runtime_hypothesis_history_respects_limit(self) -> None:
        orchestrator = RuntimeOrchestrator(hypothesis_history=HypothesisHistory(max_length=1))
        first = orchestrator.process_market_update(make_snapshot())
        second = orchestrator.process_market_update(
            make_snapshot(),
            previous_state=first.new_state,
            previous_hypothesis=first.hypothesis,
        )

        self.assertEqual(first.hypothesis_history_size, 1)
        self.assertEqual(second.hypothesis_history_size, 1)
        self.assertEqual(orchestrator.hypothesis_history.latest(), second.hypothesis_snapshot)
        self.assertIsNone(orchestrator.hypothesis_history.previous())

    def test_runtime_temporal_confidence_improves(self) -> None:
        orchestrator = RuntimeOrchestrator()
        first = orchestrator.process_market_update(make_snapshot())

        second = orchestrator.process_market_update(
            make_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            ),
            previous_state=first.new_state,
            previous_hypothesis=first.hypothesis,
        )

        self.assertEqual(second.confidence, 90)
        self.assertEqual(second.confidence_delta, 40)
        self.assertEqual(second.confidence_trend, CONFIDENCE_TREND_IMPROVING)

    def test_runtime_temporal_confidence_weakens(self) -> None:
        orchestrator = RuntimeOrchestrator()
        first = orchestrator.process_market_update(
            make_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            )
        )

        second = orchestrator.process_market_update(
            make_snapshot(),
            previous_state=first.new_state,
            previous_hypothesis=first.hypothesis,
        )

        self.assertEqual(second.confidence, 50)
        self.assertEqual(second.confidence_delta, -40)
        self.assertEqual(second.confidence_trend, CONFIDENCE_TREND_WEAKENING)

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
