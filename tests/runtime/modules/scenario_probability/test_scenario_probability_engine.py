from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
SCENARIO_ENGINE = (
    SRC / "pumpagent" / "runtime" / "modules" / "scenario_probability" / "engine.py"
)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import (
    AgentState,
    HypothesisPackage,
    RuntimeEvent,
    ScenarioProbability,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    StateTransitionStatus,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.agent_state import add_agent_state
from pumpagent.runtime.modules.hypothesis import add_hypothesis_package
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.scenario_probability import (
    ScenarioProbabilityError,
    add_scenario_probability,
    build_scenario_probability,
)
from pumpagent.runtime.modules.structure import add_structural_evidence


def make_event_with_agent_state() -> RuntimeEvent:
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
    return add_agent_state(event)


def make_hypothesis_package(event_id: str = "runtime-evt-1") -> HypothesisPackage:
    return HypothesisPackage(
        event_id=event_id,
        hypothesis_label="current_condition_explanation",
        hypothesis_summary="Mock current-condition explanation.",
        supporting_evidence=("structure:mock",),
        contradicting_evidence=(),
        competing_hypotheses=(),
        current_hypothesis_confidence_context=ConfidenceLevel.MEDIUM,
        reasoning_notes="Mock hypothesis for scenario probability tests.",
        uncertainty=UncertaintyLevel.MEDIUM,
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
        transition_reason="Mock official state for scenario probability tests.",
        supporting_evidence=("state:mock",),
        blocking_evidence=(),
        state_confidence_context=ConfidenceLevel.MEDIUM,
    )


class ScenarioProbabilityEngineTests(unittest.TestCase):
    def test_scenario_probability_reads_hypothesis_and_agent_state(self) -> None:
        event = make_event_with_agent_state()

        scenario_probability = build_scenario_probability(
            event.hypothesis_package,
            event.agent_state,
        )

        self.assertEqual(
            scenario_probability.event_id,
            event.hypothesis_package.event_id,
        )
        self.assertEqual(
            scenario_probability.metadata["source_agent_state"],
            event.agent_state.current_state.value,
        )

    def test_scenario_probability_produces_contextual_package_not_confidence_or_decision(
        self,
    ) -> None:
        event = make_event_with_agent_state()

        scenario_probability = build_scenario_probability(
            event.hypothesis_package,
            event.agent_state,
        )

        self.assertIsInstance(scenario_probability, ScenarioProbability)
        self.assertEqual(
            scenario_probability.primary_scenario,
            "continue_observation",
        )
        self.assertEqual(scenario_probability.uncertainty, UncertaintyLevel.HIGH)
        self.assertIn("deterministic MVP weights", scenario_probability.scenario_notes)
        self.assertIn("produce final confidence", scenario_probability.scenario_notes)
        self.assertIn("make decisions", scenario_probability.scenario_notes)
        self.assertIn("trigger alerts", scenario_probability.scenario_notes)
        self.assertAlmostEqual(
            sum(scenario_probability.scenario_probabilities.values()),
            1.0,
        )

    def test_scenario_probability_writes_only_scenario_probability(self) -> None:
        event = make_event_with_agent_state()

        updated = add_scenario_probability(event)

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
        self.assertIsNotNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_scenario_probability_does_not_modify_existing_sections(self) -> None:
        event = make_event_with_agent_state()
        snapshot_before = event.market_snapshot.to_dict()
        observations_before = event.observation_package.to_dict()
        structure_before = event.structural_evidence.to_dict()
        efficiency_before = event.market_efficiency_evidence.to_dict()
        hypothesis_before = event.hypothesis_package.to_dict()
        agent_state_before = event.agent_state.to_dict()

        updated = add_scenario_probability(event)

        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertEqual(updated.observation_package.to_dict(), observations_before)
        self.assertEqual(updated.structural_evidence.to_dict(), structure_before)
        self.assertEqual(
            updated.market_efficiency_evidence.to_dict(),
            efficiency_before,
        )
        self.assertEqual(updated.hypothesis_package.to_dict(), hypothesis_before)
        self.assertEqual(updated.agent_state.to_dict(), agent_state_before)

    def test_scenario_probability_requires_hypothesis_package(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaisesRegex(
            ScenarioProbabilityError,
            "hypothesis_package",
        ):
            add_scenario_probability(event)

    def test_scenario_probability_requires_agent_state(self) -> None:
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

        with self.assertRaisesRegex(ScenarioProbabilityError, "agent_state"):
            add_scenario_probability(event)

    def test_scenario_probability_handles_unknown_agent_state_conservatively(
        self,
    ) -> None:
        event = make_event_with_agent_state()

        updated = add_scenario_probability(event)

        self.assertEqual(event.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(updated.scenario_probability.uncertainty, UncertaintyLevel.HIGH)
        self.assertEqual(
            updated.scenario_probability.scenario_set,
            (
                "continue_observation",
                "insufficient_evidence_persists",
                "state_clarifies_after_more_data",
            ),
        )
        self.assertIn(
            "UNKNOWN current state",
            updated.scenario_probability.scenario_notes,
        )

    def test_scenario_probability_uses_state_aware_mvp_weights(self) -> None:
        cases = (
            (
                AgentStateType.UNKNOWN,
                {
                    "continue_observation": 0.40,
                    "insufficient_evidence_persists": 0.35,
                    "state_clarifies_after_more_data": 0.25,
                },
                "continue_observation",
                UncertaintyLevel.HIGH,
                (
                    "collect_more_evidence",
                    "wait_for_state_clarity",
                    "monitor_missing_or_contradicting_evidence",
                ),
            ),
            (
                AgentStateType.CONTINUATION_ALIVE,
                {
                    "continuation_persists": 0.55,
                    "continuation_degrades_to_saturation": 0.30,
                    "first_failure_candidate_emerges": 0.15,
                },
                "continuation_persists",
                UncertaintyLevel.MEDIUM,
                (
                    "continuation_quality",
                    "participation_support",
                    "contradiction_emergence",
                ),
            ),
            (
                AgentStateType.CONTINUATION_SATURATION,
                {
                    "saturation_resolves_to_continuation": 0.25,
                    "saturation_persists": 0.45,
                    "first_failure_risk_increases": 0.30,
                },
                "saturation_persists",
                UncertaintyLevel.MEDIUM,
                (
                    "reclaim_quality",
                    "weakening_persistence",
                    "participation_deterioration",
                ),
            ),
            (
                AgentStateType.FIRST_FAILURE_CANDIDATE,
                {
                    "failure_candidate_invalidated": 0.20,
                    "failure_candidate_persists": 0.45,
                    "first_failure_confirms": 0.35,
                },
                "failure_candidate_persists",
                UncertaintyLevel.MEDIUM,
                (
                    "failed_reclaim",
                    "contradiction_persistence",
                    "invalidation_evidence",
                ),
            ),
        )

        for (
            current_state,
            expected_probabilities,
            expected_primary,
            expected_uncertainty,
            expected_monitoring_focus,
        ) in cases:
            with self.subTest(current_state=current_state):
                scenario_probability = build_scenario_probability(
                    make_hypothesis_package(),
                    make_agent_state(current_state),
                )

                self.assertEqual(
                    scenario_probability.scenario_set,
                    tuple(expected_probabilities),
                )
                self.assertEqual(
                    scenario_probability.scenario_probabilities,
                    expected_probabilities,
                )
                self.assertEqual(
                    scenario_probability.primary_scenario,
                    expected_primary,
                )
                self.assertEqual(
                    scenario_probability.alternative_scenarios,
                    tuple(
                        scenario
                        for scenario in expected_probabilities
                        if scenario != expected_primary
                    ),
                )
                self.assertEqual(
                    scenario_probability.uncertainty,
                    expected_uncertainty,
                )
                self.assertEqual(
                    scenario_probability.monitoring_focus,
                    expected_monitoring_focus,
                )
                self.assertAlmostEqual(
                    sum(scenario_probability.scenario_probabilities.values()),
                    1.0,
                )
                self.assertEqual(
                    scenario_probability.metadata["probability_model"],
                    "deterministic_mvp_weights",
                )

    def test_scenario_probability_does_not_import_confidence_or_decision_contracts(
        self,
    ) -> None:
        tree = ast.parse(SCENARIO_ENGINE.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        imported_names = _imported_names_from(tree)
        forbidden_modules = (
            "pumpagent.live_data",
            "pumpagent.runtime.modules.market_data",
            "pumpagent.runtime.modules.confidence",
            "pumpagent.runtime.modules.decision_alert",
            "pumpagent.runtime.modules.trading",
        )
        forbidden_names = {
            "ConfidenceAssessment",
            "ConfidenceLevel",
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
