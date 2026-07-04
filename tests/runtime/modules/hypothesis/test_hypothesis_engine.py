from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
HYPOTHESIS_ENGINE = SRC / "pumpagent" / "runtime" / "modules" / "hypothesis" / "engine.py"
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import AgentState, HypothesisPackage, RuntimeEvent
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    StateTransitionStatus,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.evidence import EvidenceSummary
from pumpagent.runtime.modules.hypothesis import (
    HypothesisError,
    HypothesisHistory,
    HypothesisSnapshot,
    MarketHypothesis,
    add_hypothesis_package,
    build_hypothesis,
    build_hypothesis_package,
    build_hypothesis_snapshot,
)
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.structure import add_structural_evidence


def make_event_with_evidence() -> RuntimeEvent:
    event = RuntimeEvent(
        event_id="runtime-evt-1",
        schema_version="1.0",
        cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
    )
    event = add_market_snapshot_from_fixture(event, FIXTURE)
    event = add_observation_package(event)
    event = add_structural_evidence(event)
    return add_market_efficiency_evidence(event)


def make_agent_state(state: AgentStateType = AgentStateType.UNKNOWN) -> AgentState:
    return AgentState(
        event_id="event-1",
        current_state=state,
        previous_state=AgentStateType.UNKNOWN,
        state_transition_status=StateTransitionStatus.UNKNOWN,
        transition_reason="unit test",
        supporting_evidence=(),
        blocking_evidence=(),
        state_confidence_context=ConfidenceLevel.UNKNOWN,
    )


def make_evidence_summary(
    *,
    structural: bool = False,
    market: bool = False,
    temporal: bool = False,
    total_score: float = 0.5,
) -> EvidenceSummary:
    evidence_count = sum((structural, market, temporal))
    return EvidenceSummary(
        structural_score=total_score if structural else None,
        market_score=total_score if market else None,
        temporal_score=total_score if temporal else None,
        total_score=total_score if evidence_count else 0.0,
        evidence_count=evidence_count,
        strongest_evidence_type=None,
        weakest_evidence_type=None,
        has_structural_evidence=structural,
        has_market_evidence=market,
        has_temporal_evidence=temporal,
    )


def make_hypothesis_snapshot(
    *,
    created_at: datetime = NOW,
    state: AgentStateType = AgentStateType.UNKNOWN,
    confidence: int = 0,
) -> HypothesisSnapshot:
    return build_hypothesis_snapshot(
        agent_state=make_agent_state(state),
        confidence=confidence,
        confidence_trend="UNKNOWN",
        evidence_summary=make_evidence_summary(structural=True),
        created_at=created_at,
    )


class HypothesisEngineTests(unittest.TestCase):
    def test_created_hypothesis(self) -> None:
        hypothesis = build_hypothesis(
            {
                "price_change_1m": 1.1,
                "price_change_3m": 1.5,
                "volume_spike_ratio": 8.1,
                "oi_change_1m": 0.1,
            }
        )

        self.assertIsInstance(hypothesis, MarketHypothesis)
        self.assertEqual(hypothesis.label, "Ignition attempt")
        self.assertEqual(hypothesis.market_state, "IGNITION")
        self.assertEqual(hypothesis.confidence_score, 50)
        self.assertEqual(hypothesis.status, "CREATED")
        self.assertIsNone(hypothesis.previous_hypothesis_id)
        self.assertIn("Ignition attempt", hypothesis.summary)

    def test_updated_same_label_higher_confidence(self) -> None:
        previous = build_hypothesis(
            {
                "price_change_1m": 1.1,
                "price_change_3m": 1.5,
                "volume_spike_ratio": 8.1,
                "oi_change_1m": 0.1,
            }
        )

        hypothesis = build_hypothesis(
            {
                "price_change_1m": 2.1,
                "price_change_3m": 2.5,
                "volume_spike_ratio": 10.1,
                "oi_change_1m": 2.1,
            },
            previous=previous,
        )

        self.assertEqual(hypothesis.label, previous.label)
        self.assertEqual(hypothesis.confidence_score, 90)
        self.assertEqual(hypothesis.status, "UPDATED")

    def test_weakened_same_label_lower_confidence(self) -> None:
        previous = build_hypothesis(
            {
                "price_change_1m": 2.1,
                "price_change_3m": 2.5,
                "volume_spike_ratio": 10.1,
                "oi_change_1m": 2.1,
            }
        )

        hypothesis = build_hypothesis(
            {
                "price_change_1m": 1.1,
                "price_change_3m": 1.5,
                "volume_spike_ratio": 8.1,
                "oi_change_1m": 0.1,
            },
            previous=previous,
        )

        self.assertEqual(hypothesis.label, previous.label)
        self.assertEqual(hypothesis.confidence_score, 50)
        self.assertEqual(hypothesis.status, "WEAKENED")

    def test_replaced_different_label(self) -> None:
        previous = build_hypothesis(
            {
                "price_change_1m": 1.1,
                "price_change_3m": 1.5,
                "volume_spike_ratio": 8.1,
                "oi_change_1m": 0.1,
            }
        )

        hypothesis = build_hypothesis(
            {
                "price_change_1m": 0.1,
                "price_change_3m": 0.5,
                "volume_spike_ratio": 1.0,
                "oi_change_1m": 0.0,
            },
            previous=previous,
        )

        self.assertEqual(hypothesis.label, "Move is weakening")
        self.assertEqual(hypothesis.status, "REPLACED")
        self.assertEqual(hypothesis.previous_hypothesis_id, previous.id)

    def test_unknown_hypothesis(self) -> None:
        hypothesis = build_hypothesis(
            {
                "price_change_1m": 0.0,
                "price_change_3m": 0.0,
                "volume_spike_ratio": 1.0,
                "oi_change_1m": 0.0,
            }
        )

        self.assertEqual(hypothesis.label, "No clear hypothesis")
        self.assertEqual(hypothesis.market_state, "UNKNOWN")
        self.assertEqual(hypothesis.confidence_score, 0)
        self.assertEqual(hypothesis.status, "CREATED")

    def test_empty_summary_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=0,
            confidence_trend="UNKNOWN",
            evidence_summary=make_evidence_summary(),
            created_at=NOW,
        )

        self.assertIsInstance(snapshot, HypothesisSnapshot)
        self.assertEqual(snapshot.state, "UNKNOWN")
        self.assertEqual(snapshot.confidence, 0)
        self.assertEqual(snapshot.confidence_trend, "UNKNOWN")
        self.assertEqual(snapshot.created_at, NOW)
        self.assertEqual(snapshot.label, "unknown")

    def test_structural_only_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(AgentStateType.IGNITION),
            confidence=50,
            confidence_trend="STABLE",
            evidence_summary=make_evidence_summary(structural=True),
            created_at=NOW,
        )

        self.assertEqual(snapshot.state, "IGNITION")
        self.assertEqual(snapshot.label, "structural_only")

    def test_market_only_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=50,
            confidence_trend="STABLE",
            evidence_summary=make_evidence_summary(market=True),
            created_at=NOW,
        )

        self.assertEqual(snapshot.label, "market_only")

    def test_temporal_only_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=50,
            confidence_trend="IMPROVING",
            evidence_summary=make_evidence_summary(temporal=True),
            created_at=NOW,
        )

        self.assertEqual(snapshot.label, "temporal_only")

    def test_mixed_evidence_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=50,
            confidence_trend="STABLE",
            evidence_summary=make_evidence_summary(structural=True, market=True),
            created_at=NOW,
        )

        self.assertEqual(snapshot.label, "mixed_evidence")

    def test_low_evidence_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=0,
            confidence_trend="WEAKENING",
            evidence_summary=make_evidence_summary(structural=True, total_score=0.0),
            created_at=NOW,
        )

        self.assertEqual(snapshot.label, "low_evidence")

    def test_hypothesis_snapshot_label_selection_is_deterministic(self) -> None:
        summary = make_evidence_summary(
            structural=True,
            market=True,
            temporal=True,
            total_score=0.5,
        )

        first = build_hypothesis_snapshot(
            agent_state=make_agent_state(AgentStateType.IGNITION),
            confidence=50,
            confidence_trend="UNKNOWN",
            evidence_summary=summary,
            created_at=NOW,
        )
        second = build_hypothesis_snapshot(
            agent_state=make_agent_state(AgentStateType.IGNITION),
            confidence=50,
            confidence_trend="UNKNOWN",
            evidence_summary=summary,
            created_at=NOW,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.label, "mixed_evidence")

    def test_hypothesis_snapshot_accepts_missing_summary(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=0,
            confidence_trend="UNKNOWN",
            evidence_summary=None,
            created_at=NOW,
        )

        self.assertIsNone(snapshot.evidence_summary)
        self.assertEqual(snapshot.label, "unknown")

    def test_empty_hypothesis_history(self) -> None:
        history = HypothesisHistory()

        self.assertIsNone(history.latest())
        self.assertIsNone(history.previous())
        self.assertEqual(history.size(), 0)

    def test_hypothesis_history_append(self) -> None:
        history = HypothesisHistory()
        snapshot = make_hypothesis_snapshot()

        history.append(snapshot)

        self.assertEqual(history.size(), 1)

    def test_hypothesis_history_latest(self) -> None:
        history = HypothesisHistory()
        first = make_hypothesis_snapshot(confidence=10)
        second = make_hypothesis_snapshot(confidence=20)

        history.append(first)
        history.append(second)

        self.assertEqual(history.latest(), second)

    def test_hypothesis_history_previous(self) -> None:
        history = HypothesisHistory()
        first = make_hypothesis_snapshot(confidence=10)
        second = make_hypothesis_snapshot(confidence=20)

        history.append(first)
        history.append(second)

        self.assertEqual(history.previous(), first)

    def test_hypothesis_history_limit_discards_oldest_snapshots(self) -> None:
        history = HypothesisHistory(max_length=2)
        first = make_hypothesis_snapshot(confidence=10)
        second = make_hypothesis_snapshot(confidence=20)
        third = make_hypothesis_snapshot(confidence=30)

        history.append(first)
        history.append(second)
        history.append(third)

        self.assertEqual(history.size(), 2)
        self.assertEqual(history.previous(), second)
        self.assertEqual(history.latest(), third)

    def test_hypothesis_history_ordering_is_deterministic(self) -> None:
        history = HypothesisHistory(max_length=3)
        first = make_hypothesis_snapshot(confidence=10)
        second = make_hypothesis_snapshot(confidence=20)
        third = make_hypothesis_snapshot(confidence=30)

        for snapshot in (first, second, third):
            history.append(snapshot)

        self.assertEqual(history.previous(), second)
        self.assertEqual(history.latest(), third)

    def test_hypothesis_history_clear(self) -> None:
        history = HypothesisHistory()
        history.append(make_hypothesis_snapshot())

        history.clear()

        self.assertEqual(history.size(), 0)
        self.assertIsNone(history.latest())
        self.assertIsNone(history.previous())

    def test_supporting_and_contradicting_evidence_split(self) -> None:
        hypothesis = build_hypothesis(
            {
                "price_change_1m": 0.1,
                "price_change_3m": 0.0,
                "volume_spike_ratio": 1.0,
                "oi_change_1m": 0.0,
            }
        )

        self.assertEqual(hypothesis.supporting_evidence, ("Price increasing",))
        self.assertEqual(
            hypothesis.contradicting_evidence,
            ("Volume not above average", "OI not increasing"),
        )
        self.assertIn("Supports: Price increasing.", hypothesis.summary)
        self.assertIn(
            "Contradicts: Volume not above average, OI not increasing.",
            hypothesis.summary,
        )

    def test_hypothesis_reads_structural_and_market_efficiency_evidence(self) -> None:
        event = make_event_with_evidence()

        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
        )

        self.assertEqual(hypothesis.event_id, event.structural_evidence.event_id)
        self.assertIn(
            "structure:insufficient_ohlcv_sequence",
            hypothesis.supporting_evidence,
        )
        self.assertIn(
            "market_efficiency:volume_available",
            hypothesis.supporting_evidence,
        )

    def test_hypothesis_produces_valid_hypothesis_package(self) -> None:
        event = make_event_with_evidence()

        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
        )

        self.assertIsInstance(hypothesis, HypothesisPackage)
        self.assertEqual(
            hypothesis.hypothesis_label,
            "current_condition_explanation",
        )
        self.assertEqual(
            hypothesis.current_hypothesis_confidence_context,
            ConfidenceLevel.LOW,
        )
        self.assertEqual(hypothesis.uncertainty, UncertaintyLevel.HIGH)
        self.assertIn("does not decide official state", hypothesis.reasoning_notes)

    def test_hypothesis_writes_only_hypothesis_package_not_confidence_assessment(
        self,
    ) -> None:
        event = make_event_with_evidence()

        updated = add_hypothesis_package(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIs(event.observation_package, updated.observation_package)
        self.assertIs(event.structural_evidence, updated.structural_evidence)
        self.assertIs(
            event.market_efficiency_evidence,
            updated.market_efficiency_evidence,
        )
        self.assertIsNotNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)
        self.assertIsNotNone(
            updated.hypothesis_package.current_hypothesis_confidence_context
        )

    def test_hypothesis_does_not_modify_existing_sections(self) -> None:
        event = make_event_with_evidence()
        snapshot_before = event.market_snapshot.to_dict()
        observations_before = event.observation_package.to_dict()
        structure_before = event.structural_evidence.to_dict()
        efficiency_before = event.market_efficiency_evidence.to_dict()

        updated = add_hypothesis_package(event)

        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertEqual(updated.observation_package.to_dict(), observations_before)
        self.assertEqual(updated.structural_evidence.to_dict(), structure_before)
        self.assertEqual(
            updated.market_efficiency_evidence.to_dict(),
            efficiency_before,
        )

    def test_hypothesis_requires_structural_evidence(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )
        event = add_market_snapshot_from_fixture(event, FIXTURE)
        event = add_observation_package(event)
        event = add_market_efficiency_evidence(event)

        with self.assertRaisesRegex(HypothesisError, "structural_evidence"):
            add_hypothesis_package(event)

    def test_hypothesis_requires_market_efficiency_evidence(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )
        event = add_market_snapshot_from_fixture(event, FIXTURE)
        event = add_observation_package(event)
        event = add_structural_evidence(event)

        with self.assertRaisesRegex(HypothesisError, "market_efficiency_evidence"):
            add_hypothesis_package(event)

    def test_hypothesis_does_not_import_downstream_runtime_contracts(self) -> None:
        tree = ast.parse(HYPOTHESIS_ENGINE.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        imported_names = _imported_names_from(tree)
        forbidden_modules = (
            "pumpagent.runtime.modules.agent_state",
            "pumpagent.runtime.modules.scenario_probability",
            "pumpagent.runtime.modules.confidence",
            "pumpagent.runtime.modules.decision_alert",
            "pumpagent.runtime.modules.trading",
        )
        forbidden_names = {
            "AgentState",
            "AgentStateType",
            "ScenarioProbability",
            "ConfidenceAssessment",
            "DecisionAlert",
            "DecisionType",
            "AlertLevel",
        }

        self.assertFalse(
            any(
                imported == module or imported.startswith(f"{module}.")
                for imported in imports
                for module in forbidden_modules
            )
        )
        self.assertTrue(forbidden_names.isdisjoint(imported_names))


def _imports_from(tree: ast.AST) -> tuple[str, ...]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(imports)


def _imported_names_from(tree: ast.AST) -> set[str]:
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


if __name__ == "__main__":
    unittest.main()
