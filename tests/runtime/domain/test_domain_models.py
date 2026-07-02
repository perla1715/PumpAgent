from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import (
    AgentState,
    ConfidenceAssessment,
    DecisionAlert,
    HypothesisPackage,
    LearningMetadata,
    MarketEfficiencyEvidence,
    MarketSnapshot,
    ObservationPackage,
    RuntimeEvent,
    ScenarioProbability,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    AlertLevel,
    ConfidenceLevel,
    DataQualityStatus,
    DecisionType,
    EvidenceStrength,
    ReviewStatus,
    RuntimeStatus,
    StateTransitionStatus,
    UncertaintyLevel,
)


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def make_market_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        event_id="evt-1",
        timestamp=NOW,
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        price=100.0,
        ohlcv=(
            {
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 10.0,
            },
        ),
        volume=10.0,
        data_source="fixture",
        data_quality_status=DataQualityStatus.VALID,
        optional_market_metrics={"open_interest": 1200, "nested": {"quality": "ok"}},
    )


def make_observation_package() -> ObservationPackage:
    return ObservationPackage(
        event_id="evt-1",
        observation_timestamp=NOW,
        normalized_price=100.0,
        normalized_ohlcv=make_market_snapshot().ohlcv,
        normalized_volume=10.0,
        available_metrics=("price", "ohlcv", "volume"),
        missing_metrics=(),
        data_quality_status=DataQualityStatus.VALID,
        normalized_metrics={"funding": 0.01, "flags": ["clean"]},
    )


def make_structural_evidence() -> StructuralEvidence:
    return StructuralEvidence(
        event_id="evt-1",
        structure_summary="Structure remains intact.",
        trend_structure="higher_highs",
        structural_bias="continuation",
        key_levels=({"name": "support", "price": 98.0},),
        structural_events=("reclaim",),
        evidence_strength=EvidenceStrength.MODERATE,
        evidence_against=("momentum slowing",),
        uncertainty=UncertaintyLevel.MEDIUM,
        technical_context={"ema": {"spread": "expanding"}},
    )


def make_market_efficiency_evidence() -> MarketEfficiencyEvidence:
    return MarketEfficiencyEvidence(
        event_id="evt-1",
        participation_summary="Participation is present but weakening.",
        participation_direction="weakening",
        efficiency_summary="Move is less efficient.",
        efficiency_status="degrading",
        supporting_evidence=("volume present",),
        evidence_against=("CVD divergence",),
        evidence_strength=EvidenceStrength.MODERATE,
        uncertainty=UncertaintyLevel.MEDIUM,
        market_mechanics_context={"cvd": {"direction": "diverging"}},
    )


def make_hypothesis_package() -> HypothesisPackage:
    return HypothesisPackage(
        event_id="evt-1",
        hypothesis_label="continuation_alive",
        hypothesis_summary="Continuation remains valid but quality is weakening.",
        supporting_evidence=("structure intact",),
        contradicting_evidence=("participation weakening",),
        competing_hypotheses=({"label": "saturation", "confidence_context": "medium"},),
        current_hypothesis_confidence_context=ConfidenceLevel.MEDIUM,
        reasoning_notes="Current explanation only.",
    )


def make_agent_state() -> AgentState:
    return AgentState(
        event_id="evt-1",
        current_state=AgentStateType.CONTINUATION_ALIVE,
        previous_state=AgentStateType.IGNITION,
        state_transition_status=StateTransitionStatus.CHANGED,
        transition_reason="Structure and participation support continuation.",
        supporting_evidence=("structure intact",),
        blocking_evidence=("participation weakening",),
        state_confidence_context=ConfidenceLevel.MEDIUM,
    )


def make_scenario_probability() -> ScenarioProbability:
    return ScenarioProbability(
        event_id="evt-1",
        scenario_set=("continuation_persists", "saturation_develops"),
        scenario_probabilities={
            "continuation_persists": 0.55,
            "saturation_develops": 0.45,
        },
        primary_scenario="continuation_persists",
        alternative_scenarios=("saturation_develops",),
        supporting_evidence=("state continuation_alive",),
        contradicting_evidence=("participation weakening",),
        uncertainty=UncertaintyLevel.MEDIUM,
        monitoring_focus=("failed reclaim",),
        metadata={"source": "fixture", "weights": [0.55, 0.45]},
    )


def make_confidence_assessment() -> ConfidenceAssessment:
    return ConfidenceAssessment(
        event_id="evt-1",
        final_confidence_level=ConfidenceLevel.MEDIUM,
        confidence_summary="Moderate reliability.",
        confidence_drivers=("structure intact",),
        confidence_reducers=("participation weakening",),
        data_quality_impact="valid data",
        contradiction_impact="moderate contradiction",
        uncertainty_level=UncertaintyLevel.MEDIUM,
    )


def make_decision_alert() -> DecisionAlert:
    return DecisionAlert(
        event_id="evt-1",
        decision_type=DecisionType.WARNING,
        alert_level=AlertLevel.WARNING,
        decision_summary="Warning mode.",
        reason="Continuation quality is weakening.",
        required_human_action="Review market context.",
        non_execution_confirmation=True,
    )


def make_learning_metadata() -> LearningMetadata:
    return LearningMetadata(
        event_id="evt-1",
        case_id="case-1",
        should_store=True,
        storage_reason="Interesting continuation degradation.",
        review_status=ReviewStatus.PENDING,
        created_at=NOW,
    )


def make_runtime_event() -> RuntimeEvent:
    return RuntimeEvent(
        event_id="evt-1",
        schema_version="1.0",
        cycle_timestamp=NOW,
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        runtime_status=RuntimeStatus.FINALIZED,
        market_snapshot=make_market_snapshot(),
        observation_package=make_observation_package(),
        structural_evidence=make_structural_evidence(),
        market_efficiency_evidence=make_market_efficiency_evidence(),
        hypothesis_package=make_hypothesis_package(),
        agent_state=make_agent_state(),
        scenario_probability=make_scenario_probability(),
        confidence_assessment=make_confidence_assessment(),
        decision_alert=make_decision_alert(),
        learning_metadata=make_learning_metadata(),
    )


class RuntimeDomainModelTests(unittest.TestCase):
    def test_runtime_event_can_hold_complete_reasoning_cycle(self) -> None:
        event = make_runtime_event()

        self.assertEqual(event.market_snapshot.symbol, "BTCUSDT")
        self.assertEqual(event.hypothesis_package.hypothesis_label, "continuation_alive")
        self.assertTrue(event.decision_alert.non_execution_confirmation)
        self.assertTrue(event.learning_metadata.should_store)

    def test_all_domain_models_are_frozen_after_creation(self) -> None:
        models = (
            make_market_snapshot(),
            make_observation_package(),
            make_structural_evidence(),
            make_market_efficiency_evidence(),
            make_hypothesis_package(),
            make_agent_state(),
            make_scenario_probability(),
            make_confidence_assessment(),
            make_decision_alert(),
            make_learning_metadata(),
            make_runtime_event(),
        )

        for model in models:
            with self.subTest(model=type(model).__name__):
                with self.assertRaises(FrozenInstanceError):
                    model.event_id = "changed"  # type: ignore[misc]

    def test_nested_payloads_are_defensively_frozen(self) -> None:
        snapshot = make_market_snapshot()
        scenario = make_scenario_probability()

        with self.assertRaises(TypeError):
            snapshot.ohlcv[0]["close"] = 101.0

        with self.assertRaises(TypeError):
            snapshot.optional_market_metrics["open_interest"] = 1300

        with self.assertRaises(TypeError):
            scenario.metadata["source"] = "changed"

        self.assertIsInstance(scenario.metadata["weights"], tuple)

    def test_runtime_event_replacement_returns_new_instance(self) -> None:
        event = RuntimeEvent(
            event_id="evt-1",
            schema_version="1.0",
            cycle_timestamp=NOW,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        updated = event.with_sections(market_snapshot=make_market_snapshot())

        self.assertIsNot(updated, event)
        self.assertIsNone(event.market_snapshot)
        self.assertIsNotNone(updated.market_snapshot)

    def test_runtime_event_rejects_unknown_field_replacement(self) -> None:
        event = RuntimeEvent(
            event_id="evt-1",
            schema_version="1.0",
            cycle_timestamp=NOW,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaisesRegex(ValueError, "Unknown RuntimeEvent field"):
            event.with_sections(not_a_runtime_section="invalid")

    def test_runtime_event_rejects_duplicate_section_update(self) -> None:
        event = RuntimeEvent(
            event_id="evt-1",
            schema_version="1.0",
            cycle_timestamp=NOW,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaisesRegex(ValueError, "Duplicate RuntimeEvent section"):
            event.with_sections(
                (
                    ("market_snapshot", make_market_snapshot()),
                    ("market_snapshot", make_market_snapshot()),
                )
            )

    def test_runtime_event_replacement_keeps_previous_sections_unchanged(self) -> None:
        event = RuntimeEvent(
            event_id="evt-1",
            schema_version="1.0",
            cycle_timestamp=NOW,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            market_snapshot=make_market_snapshot(),
        )
        snapshot_before = event.market_snapshot.to_dict()

        updated = event.with_sections(observation_package=make_observation_package())

        self.assertIsNot(updated, event)
        self.assertEqual(event.market_snapshot.to_dict(), snapshot_before)
        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertIsNone(event.observation_package)
        self.assertIsNotNone(updated.observation_package)

    def test_runtime_event_defensively_freezes_nested_lists(self) -> None:
        event = RuntimeEvent(
            event_id="evt-1",
            schema_version="1.0",
            cycle_timestamp=NOW,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            errors_or_warnings=["warning", ["nested-warning"]],  # type: ignore[list-item]
        )

        self.assertIsInstance(event.errors_or_warnings, tuple)
        self.assertIsInstance(event.errors_or_warnings[1], tuple)
        with self.assertRaises(TypeError):
            event.errors_or_warnings[1][0] = "changed"  # type: ignore[index]

    def test_runtime_event_defensively_freezes_nested_dictionaries(self) -> None:
        event = RuntimeEvent(
            event_id="evt-1",
            schema_version="1.0",
            cycle_timestamp=NOW,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            errors_or_warnings=[{"code": "warning", "details": ["nested"]}],  # type: ignore[list-item]
        )

        with self.assertRaises(TypeError):
            event.errors_or_warnings[0]["code"] = "changed"  # type: ignore[index]

    def test_learning_metadata_review_update_returns_new_instance(self) -> None:
        metadata = make_learning_metadata()

        reviewed = metadata.with_review_update(
            review_status=ReviewStatus.REVIEWED,
            outcome_pending=False,
            outcome_summary="Continuation degraded into saturation.",
            reviewed_by="human",
            review_timestamp=NOW,
        )

        self.assertIsNot(reviewed, metadata)
        self.assertEqual(metadata.review_status, ReviewStatus.PENDING)
        self.assertEqual(reviewed.review_status, ReviewStatus.REVIEWED)
        self.assertFalse(reviewed.outcome_pending)

    def test_runtime_event_serializes_nested_domain_objects_to_primitives(self) -> None:
        serialized = make_runtime_event().to_dict()

        self.assertEqual(serialized["cycle_timestamp"], "2026-07-01T12:00:00+00:00")
        self.assertEqual(serialized["runtime_status"], "finalized")
        self.assertEqual(serialized["market_snapshot"]["data_quality_status"], "valid")
        self.assertEqual(
            serialized["market_snapshot"]["timestamp"], "2026-07-01T12:00:00+00:00"
        )
        self.assertEqual(
            serialized["market_snapshot"]["optional_market_metrics"]["nested"]["quality"],
            "ok",
        )
        self.assertEqual(
            serialized["structural_evidence"]["technical_context"]["ema"]["spread"],
            "expanding",
        )
        self.assertEqual(
            serialized["scenario_probability"]["metadata"]["weights"], [0.55, 0.45]
        )


if __name__ == "__main__":
    unittest.main()
