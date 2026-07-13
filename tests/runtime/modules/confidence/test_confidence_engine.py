from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
CONFIDENCE_ENGINE = SRC / "pumpagent" / "runtime" / "modules" / "confidence" / "engine.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import (
    AgentState,
    ConfidenceAssessment,
    HypothesisPackage,
    RuntimeEvent,
    ScenarioProbability,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    DataQualityStatus,
    StateTransitionStatus,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.agent_state import add_agent_state
from pumpagent.runtime.modules.confidence import (
    ConfidenceError,
    add_confidence_assessment,
    build_confidence_assessment,
    calculate_confidence,
)
from pumpagent.runtime.modules.hypothesis import add_hypothesis_package
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.scenario_probability import add_scenario_probability
from pumpagent.runtime.modules.structure import add_structural_evidence


def make_event_with_scenario_probability() -> RuntimeEvent:
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
    event = add_market_efficiency_evidence(event)
    event = add_hypothesis_package(event)
    event = add_agent_state(event)
    return add_scenario_probability(event)


def make_hypothesis_package(
    *,
    event_id: str = "runtime-evt-1",
    supporting_evidence: tuple[str, ...] = ("structure:mock",),
    contradicting_evidence: tuple[str, ...] = (),
    uncertainty: UncertaintyLevel = UncertaintyLevel.MEDIUM,
    confidence_context: ConfidenceLevel = ConfidenceLevel.MEDIUM,
) -> HypothesisPackage:
    return HypothesisPackage(
        event_id=event_id,
        hypothesis_label="current_condition_explanation",
        hypothesis_summary="Mock current-condition explanation.",
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        competing_hypotheses=(),
        current_hypothesis_confidence_context=confidence_context,
        reasoning_notes="Mock hypothesis for confidence tests.",
        uncertainty=uncertainty,
    )


def make_agent_state(
    current_state: AgentStateType,
    *,
    event_id: str = "runtime-evt-1",
) -> AgentState:
    return AgentState(
        event_id=event_id,
        current_state=current_state,
        previous_state=AgentStateType.UNKNOWN,
        state_transition_status=StateTransitionStatus.CHANGED
        if current_state != AgentStateType.UNKNOWN
        else StateTransitionStatus.UNCHANGED,
        transition_reason="Mock official state for confidence tests.",
        supporting_evidence=("state:mock",),
        blocking_evidence=(),
        state_confidence_context=ConfidenceLevel.MEDIUM,
    )


def make_scenario_probability(
    *,
    event_id: str = "runtime-evt-1",
    uncertainty: UncertaintyLevel = UncertaintyLevel.MEDIUM,
    contradicting_evidence: tuple[str, ...] = (),
) -> ScenarioProbability:
    return ScenarioProbability(
        event_id=event_id,
        scenario_set=("continuation_persists", "first_failure_candidate_emerges"),
        scenario_probabilities={
            "continuation_persists": 0.55,
            "first_failure_candidate_emerges": 0.45,
        },
        primary_scenario="continuation_persists",
        alternative_scenarios=("first_failure_candidate_emerges",),
        supporting_evidence=("scenario:mock",),
        contradicting_evidence=contradicting_evidence,
        uncertainty=uncertainty,
        monitoring_focus=("continuation_quality",),
    )


class ConfidenceEngineTests(unittest.TestCase):
    def test_very_strong_signal(self) -> None:
        confidence = calculate_confidence(
            {
                "price_change_1m": 2.1,
                "volume_spike_ratio": 10.1,
                "oi_change_1m": 2.1,
            }
        )

        self.assertEqual(confidence, 90)

    def test_medium_signal(self) -> None:
        confidence = calculate_confidence(
            {
                "price_change_1m": 1.1,
                "volume_spike_ratio": 5.1,
                "oi_change_1m": 0.6,
            }
        )

        self.assertEqual(confidence, 60)

    def test_weak_signal(self) -> None:
        confidence = calculate_confidence(
            {
                "price_change_1m": 0.1,
                "volume_spike_ratio": 2.1,
                "oi_change_1m": 0.1,
            }
        )

        self.assertEqual(confidence, 30)

    def test_zero_signal(self) -> None:
        confidence = calculate_confidence(
            {
                "price_change_1m": 0.0,
                "volume_spike_ratio": 1.0,
                "oi_change_1m": 0.0,
            }
        )

        self.assertEqual(confidence, 0)

    def test_confidence_never_exceeds_100(self) -> None:
        confidence = calculate_confidence(
            {
                "price_change_1m": 100.0,
                "volume_spike_ratio": 100.0,
                "oi_change_1m": 100.0,
            }
        )

        self.assertLessEqual(confidence, 100)

    def test_confidence_reads_hypothesis_agent_state_and_scenario(self) -> None:
        event = make_event_with_scenario_probability()

        assessment = build_confidence_assessment(
            event.hypothesis_package,
            event.agent_state,
            event.scenario_probability,
        )

        self.assertEqual(assessment.event_id, event.hypothesis_package.event_id)
        self.assertIn("hypothesis_has_supporting_evidence", assessment.confidence_drivers)
        self.assertIn("agent_state_unknown", assessment.confidence_reducers)

    def test_confidence_produces_valid_assessment(self) -> None:
        event = make_event_with_scenario_probability()

        assessment = build_confidence_assessment(
            event.hypothesis_package,
            event.agent_state,
            event.scenario_probability,
        )

        self.assertIsInstance(assessment, ConfidenceAssessment)
        self.assertEqual(assessment.final_confidence_level, ConfidenceLevel.LOW)
        self.assertEqual(assessment.uncertainty_level, UncertaintyLevel.HIGH)
        self.assertIsNone(assessment.numeric_confidence_score)
        self.assertIn("evaluates reliability only", assessment.reliability_notes)
        self.assertIn("does not decide", assessment.reliability_notes)
        self.assertIn("generate alerts", assessment.reliability_notes)
        self.assertIn("HIGH confidence is not allowed", assessment.reliability_notes)

    def test_confidence_writes_only_confidence_assessment(self) -> None:
        event = make_event_with_scenario_probability()

        updated = add_confidence_assessment(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIs(event.observation_package, updated.observation_package)
        self.assertIs(event.structural_evidence, updated.structural_evidence)
        self.assertIs(
            event.market_efficiency_evidence,
            updated.market_efficiency_evidence,
        )
        self.assertIs(event.hypothesis_package, updated.hypothesis_package)
        self.assertIs(event.agent_state, updated.agent_state)
        self.assertIs(event.scenario_probability, updated.scenario_probability)
        self.assertIsNotNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_confidence_does_not_modify_previous_sections(self) -> None:
        event = make_event_with_scenario_probability()
        snapshot_before = event.market_snapshot.to_dict()
        observations_before = event.observation_package.to_dict()
        structure_before = event.structural_evidence.to_dict()
        efficiency_before = event.market_efficiency_evidence.to_dict()
        hypothesis_before = event.hypothesis_package.to_dict()
        agent_state_before = event.agent_state.to_dict()
        scenario_before = event.scenario_probability.to_dict()

        updated = add_confidence_assessment(event)

        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertEqual(updated.observation_package.to_dict(), observations_before)
        self.assertEqual(updated.structural_evidence.to_dict(), structure_before)
        self.assertEqual(
            updated.market_efficiency_evidence.to_dict(),
            efficiency_before,
        )
        self.assertEqual(updated.hypothesis_package.to_dict(), hypothesis_before)
        self.assertEqual(updated.agent_state.to_dict(), agent_state_before)
        self.assertEqual(updated.scenario_probability.to_dict(), scenario_before)

    def test_confidence_requires_hypothesis(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaisesRegex(ConfidenceError, "hypothesis_package"):
            add_confidence_assessment(event)

    def test_confidence_requires_agent_state(self) -> None:
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
        event = add_market_efficiency_evidence(event)
        event = add_hypothesis_package(event)

        with self.assertRaisesRegex(ConfidenceError, "agent_state"):
            add_confidence_assessment(event)

    def test_missing_scenario_probability_forces_low_confidence(self) -> None:
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
        event = add_market_efficiency_evidence(event)
        event = add_hypothesis_package(event)
        event = add_agent_state(event)

        updated = add_confidence_assessment(event)

        self.assertEqual(
            updated.confidence_assessment.final_confidence_level,
            ConfidenceLevel.LOW,
        )
        self.assertIn(
            "scenario_probability_missing",
            updated.confidence_assessment.confidence_reducers,
        )
        self.assertIsNone(updated.scenario_probability)

    def test_confidence_is_lower_when_uncertainty_is_high(self) -> None:
        event = make_event_with_scenario_probability()

        updated = add_confidence_assessment(event)

        self.assertEqual(event.scenario_probability.uncertainty, UncertaintyLevel.HIGH)
        self.assertEqual(
            updated.confidence_assessment.final_confidence_level,
            ConfidenceLevel.LOW,
        )
        self.assertIn(
            "scenario_uncertainty_high",
            updated.confidence_assessment.confidence_reducers,
        )

    def test_confidence_is_low_when_scenario_uncertainty_is_unknown(self) -> None:
        assessment = build_confidence_assessment(
            make_hypothesis_package(),
            make_agent_state(AgentStateType.CONTINUATION_ALIVE),
            make_scenario_probability(uncertainty=UncertaintyLevel.UNKNOWN),
            data_quality_impact="market_snapshot_data_quality:valid",
        )

        self.assertEqual(assessment.final_confidence_level, ConfidenceLevel.LOW)
        self.assertIn("scenario_uncertainty_unknown", assessment.confidence_reducers)

    def test_confidence_preserves_uncertainty_when_agent_state_unknown(self) -> None:
        event = make_event_with_scenario_probability()

        updated = add_confidence_assessment(event)

        self.assertEqual(event.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(
            updated.confidence_assessment.uncertainty_level,
            UncertaintyLevel.HIGH,
        )
        self.assertIn(
            "agent_state_unknown",
            updated.confidence_assessment.confidence_reducers,
        )

    def test_known_state_valid_scenario_support_and_data_quality_returns_medium(
        self,
    ) -> None:
        assessment = build_confidence_assessment(
            make_hypothesis_package(),
            make_agent_state(AgentStateType.CONTINUATION_ALIVE),
            make_scenario_probability(),
            data_quality_impact="market_snapshot_data_quality:valid",
        )

        self.assertEqual(assessment.final_confidence_level, ConfidenceLevel.MEDIUM)
        self.assertEqual(assessment.uncertainty_level, UncertaintyLevel.MEDIUM)
        self.assertIn("agent_state_known", assessment.confidence_drivers)
        self.assertIn(
            "scenario_probability_available",
            assessment.confidence_drivers,
        )
        self.assertIn("scenario_uncertainty_not_high", assessment.confidence_drivers)
        self.assertIn("scenario_weights_sum_valid", assessment.confidence_drivers)
        self.assertIn(
            "hypothesis_has_supporting_evidence",
            assessment.confidence_drivers,
        )
        self.assertIn("data_quality_acceptable", assessment.confidence_drivers)
        self.assertEqual(assessment.confidence_reducers, ())

    def test_contradictions_reduce_confidence_to_low(self) -> None:
        assessment = build_confidence_assessment(
            make_hypothesis_package(contradicting_evidence=("structure:mixed",)),
            make_agent_state(AgentStateType.CONTINUATION_ALIVE),
            make_scenario_probability(),
            data_quality_impact="market_snapshot_data_quality:valid",
        )

        self.assertEqual(assessment.final_confidence_level, ConfidenceLevel.LOW)
        self.assertIn(
            "hypothesis_has_contradicting_evidence",
            assessment.confidence_reducers,
        )

    def test_scenario_contradictions_reduce_confidence_to_low(self) -> None:
        assessment = build_confidence_assessment(
            make_hypothesis_package(),
            make_agent_state(AgentStateType.CONTINUATION_ALIVE),
            make_scenario_probability(contradicting_evidence=("scenario:mixed",)),
            data_quality_impact="market_snapshot_data_quality:valid",
        )

        self.assertEqual(assessment.final_confidence_level, ConfidenceLevel.LOW)
        self.assertIn(
            "scenario_has_contradicting_evidence",
            assessment.confidence_reducers,
        )

    def test_missing_hypothesis_support_reduces_confidence_to_low(self) -> None:
        assessment = build_confidence_assessment(
            make_hypothesis_package(supporting_evidence=()),
            make_agent_state(AgentStateType.CONTINUATION_ALIVE),
            make_scenario_probability(),
            data_quality_impact="market_snapshot_data_quality:valid",
        )

        self.assertEqual(assessment.final_confidence_level, ConfidenceLevel.LOW)
        self.assertIn(
            "hypothesis_context_missing_or_generic",
            assessment.confidence_reducers,
        )

    def test_poor_data_quality_reduces_confidence_to_low(self) -> None:
        event = make_event_with_scenario_probability()
        delayed_snapshot = replace(
            event.market_snapshot,
            data_quality_status=DataQualityStatus.DELAYED,
        )
        event = event.with_sections(market_snapshot=delayed_snapshot)

        updated = add_confidence_assessment(event)

        self.assertEqual(
            updated.confidence_assessment.final_confidence_level,
            ConfidenceLevel.LOW,
        )
        self.assertIn(
            "data_quality_incomplete_or_poor",
            updated.confidence_assessment.confidence_reducers,
        )

    def test_high_confidence_is_never_produced_in_mvp(self) -> None:
        assessment = build_confidence_assessment(
            make_hypothesis_package(
                uncertainty=UncertaintyLevel.LOW,
                confidence_context=ConfidenceLevel.VERY_HIGH,
            ),
            make_agent_state(AgentStateType.CONTINUATION_ALIVE),
            make_scenario_probability(uncertainty=UncertaintyLevel.LOW),
            data_quality_impact="market_snapshot_data_quality:valid",
        )

        self.assertEqual(assessment.final_confidence_level, ConfidenceLevel.MEDIUM)
        self.assertNotEqual(assessment.final_confidence_level, ConfidenceLevel.HIGH)
        self.assertIn("capped at MEDIUM", assessment.calibration_notes)

    def test_confidence_assessment_remains_final_reliability_not_pre_state(
        self,
    ) -> None:
        event = make_event_with_scenario_probability()

        assessment = build_confidence_assessment(
            event.hypothesis_package,
            event.agent_state,
            event.scenario_probability,
        )

        self.assertIn("agent_state_unknown", assessment.confidence_reducers)
        self.assertIn("scenario_uncertainty_high", assessment.confidence_reducers)
        self.assertIn("scenario uncertainty", assessment.confidence_summary)
        self.assertEqual(assessment.final_confidence_level, ConfidenceLevel.LOW)

    def test_runtime_event_confidence_does_not_use_legacy_numeric_confidence(
        self,
    ) -> None:
        assessment = build_confidence_assessment(
            make_hypothesis_package(),
            make_agent_state(AgentStateType.CONTINUATION_ALIVE),
            make_scenario_probability(),
            data_quality_impact="market_snapshot_data_quality:valid",
        )

        self.assertIsNone(assessment.numeric_confidence_score)
        self.assertEqual(calculate_confidence({"price_change_1m": 100.0}), 30)

    def test_confidence_imports_required_final_reliability_inputs_only(self) -> None:
        tree = ast.parse(CONFIDENCE_ENGINE.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        imported_names = _imported_names_from(tree)

        self.assertIn("AgentState", imported_names)
        self.assertIn("HypothesisPackage", imported_names)
        self.assertIn("ScenarioProbability", imported_names)
        self.assertIn("ConfidenceAssessment", imported_names)
        self.assertFalse(
            any(
                imported == "pumpagent.runtime.modules.decision_alert"
                or imported.startswith("pumpagent.runtime.modules.decision_alert.")
                or imported == "pumpagent.live_data"
                or imported.startswith("pumpagent.live_data.")
                or imported == "pumpagent.runtime.modules.market_data"
                or imported.startswith("pumpagent.runtime.modules.market_data.")
                or imported == "pumpagent.runtime.modules.trading"
                or imported.startswith("pumpagent.runtime.modules.trading.")
                for imported in imports
            )
        )
        self.assertTrue(
            {"DecisionAlert", "DecisionType", "AlertLevel"}.isdisjoint(imported_names)
        )


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
