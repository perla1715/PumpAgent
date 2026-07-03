from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
AGENT_STATE_MANAGER = SRC / "pumpagent" / "runtime" / "modules" / "agent_state" / "manager.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import AgentState, HypothesisPackage, RuntimeEvent
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    StateTransitionStatus,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.agent_state import (
    AgentStateError,
    add_agent_state,
    build_agent_state,
    build_agent_state_from_market_hypothesis,
)
from pumpagent.runtime.modules.hypothesis import add_hypothesis_package
from pumpagent.runtime.modules.hypothesis import build_hypothesis
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.structure import add_structural_evidence


def make_event_with_hypothesis() -> RuntimeEvent:
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
    return add_hypothesis_package(event)


class AgentStateManagerTests(unittest.TestCase):
    def test_agent_state_reads_hypothesis_package(self) -> None:
        event = make_event_with_hypothesis()

        agent_state = build_agent_state(event.hypothesis_package)

        self.assertEqual(agent_state.event_id, event.hypothesis_package.event_id)
        self.assertEqual(
            agent_state.supporting_evidence,
            event.hypothesis_package.supporting_evidence,
        )

    def test_agent_state_produces_valid_agent_state(self) -> None:
        event = make_event_with_hypothesis()

        agent_state = build_agent_state(event.hypothesis_package)

        self.assertIsInstance(agent_state, AgentState)
        self.assertEqual(agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(agent_state.previous_state, AgentStateType.UNKNOWN)
        self.assertEqual(
            agent_state.state_transition_status,
            StateTransitionStatus.UNCHANGED,
        )
        self.assertIn("Scenario Probability", agent_state.notes)

    def test_agent_state_writes_only_agent_state(self) -> None:
        event = make_event_with_hypothesis()

        updated = add_agent_state(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIs(event.observation_package, updated.observation_package)
        self.assertIs(event.structural_evidence, updated.structural_evidence)
        self.assertIs(
            event.market_efficiency_evidence,
            updated.market_efficiency_evidence,
        )
        self.assertIs(event.hypothesis_package, updated.hypothesis_package)
        self.assertIsNotNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_agent_state_does_not_modify_existing_sections(self) -> None:
        event = make_event_with_hypothesis()
        snapshot_before = event.market_snapshot.to_dict()
        observations_before = event.observation_package.to_dict()
        structure_before = event.structural_evidence.to_dict()
        efficiency_before = event.market_efficiency_evidence.to_dict()
        hypothesis_before = event.hypothesis_package.to_dict()

        updated = add_agent_state(event)

        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertEqual(updated.observation_package.to_dict(), observations_before)
        self.assertEqual(updated.structural_evidence.to_dict(), structure_before)
        self.assertEqual(
            updated.market_efficiency_evidence.to_dict(),
            efficiency_before,
        )
        self.assertEqual(updated.hypothesis_package.to_dict(), hypothesis_before)

    def test_agent_state_requires_hypothesis_package(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaisesRegex(AgentStateError, "hypothesis_package"):
            add_agent_state(event)

    def test_agent_state_uses_unknown_when_evidence_is_insufficient(self) -> None:
        event = make_event_with_hypothesis()

        updated = add_agent_state(event)

        self.assertEqual(updated.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertIn("insufficient", updated.agent_state.transition_reason)
        self.assertEqual(
            updated.agent_state.rejected_state_transitions,
            (
                AgentStateType.IGNITION,
                AgentStateType.CONTINUATION_ALIVE,
                AgentStateType.CONTINUATION_SATURATION,
                AgentStateType.FIRST_FAILURE_CANDIDATE,
                AgentStateType.FIRST_FAILURE,
                AgentStateType.CONTINUATION_DEATH,
            ),
        )

    def test_agent_state_v01_intentionally_keeps_stronger_hypothesis_unknown(
        self,
    ) -> None:
        hypothesis = HypothesisPackage(
            event_id="runtime-evt-1",
            hypothesis_label="current_condition_explanation",
            hypothesis_summary="Mock stronger current-condition explanation.",
            supporting_evidence=("structure:mock_sequence",),
            contradicting_evidence=(),
            competing_hypotheses=(),
            current_hypothesis_confidence_context=ConfidenceLevel.MEDIUM,
            reasoning_notes="Mock hypothesis for conservative v0.1 policy.",
            uncertainty=UncertaintyLevel.LOW,
        )

        agent_state = build_agent_state(hypothesis)

        self.assertEqual(agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertIn("insufficient", agent_state.transition_reason)

    def test_agent_state_preserves_previous_state_when_provided(self) -> None:
        event = make_event_with_hypothesis()

        updated = add_agent_state(
            event,
            previous_state=AgentStateType.IGNITION,
        )

        self.assertEqual(updated.agent_state.previous_state, AgentStateType.IGNITION)
        self.assertEqual(updated.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(
            updated.agent_state.state_transition_status,
            StateTransitionStatus.CHANGED,
        )

    def test_market_hypothesis_uppercase_state_normalization(self) -> None:
        hypothesis = build_hypothesis(
            {
                "price_change_1m": 1.1,
                "price_change_3m": 1.5,
                "volume_spike_ratio": 8.1,
                "oi_change_1m": 0.1,
            }
        )

        agent_state = build_agent_state_from_market_hypothesis(hypothesis)

        self.assertEqual(agent_state.current_state, AgentStateType.IGNITION)
        self.assertEqual(agent_state.previous_state, AgentStateType.UNKNOWN)
        self.assertEqual(
            agent_state.supporting_evidence,
            hypothesis.supporting_evidence,
        )
        self.assertEqual(
            agent_state.blocking_evidence,
            hypothesis.contradicting_evidence,
        )
        self.assertEqual(agent_state.state_confidence_context, ConfidenceLevel.MEDIUM)

    def test_market_hypothesis_unknown_state_fallback(self) -> None:
        hypothesis = build_hypothesis(
            {
                "price_change_1m": 0.0,
                "price_change_3m": 0.0,
                "volume_spike_ratio": 1.0,
                "oi_change_1m": 0.0,
            }
        )

        agent_state = build_agent_state_from_market_hypothesis(hypothesis)

        self.assertEqual(agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(agent_state.state_confidence_context, ConfidenceLevel.UNKNOWN)
        self.assertIn("conservatively unmapped", agent_state.transition_reason)

    def test_market_hypothesis_weakening_conservative_fallback(self) -> None:
        hypothesis = build_hypothesis(
            {
                "price_change_1m": 0.1,
                "price_change_3m": 0.5,
                "volume_spike_ratio": 1.0,
                "oi_change_1m": 0.0,
            }
        )

        agent_state = build_agent_state_from_market_hypothesis(
            hypothesis,
            previous_state=AgentStateType.CONTINUATION_ALIVE,
        )

        self.assertEqual(hypothesis.market_state, "WEAKENING")
        self.assertEqual(agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(agent_state.previous_state, AgentStateType.CONTINUATION_ALIVE)
        self.assertEqual(
            agent_state.state_transition_status,
            StateTransitionStatus.CHANGED,
        )
        self.assertIn("WEAKENING", agent_state.transition_reason)

    def test_agent_state_does_not_import_later_runtime_contracts(self) -> None:
        tree = ast.parse(AGENT_STATE_MANAGER.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        imported_names = _imported_names_from(tree)
        forbidden_modules = (
            "pumpagent.runtime.modules.scenario_probability",
            "pumpagent.runtime.modules.confidence",
            "pumpagent.runtime.modules.decision_alert",
            "pumpagent.runtime.modules.trading",
        )
        forbidden_names = {
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
