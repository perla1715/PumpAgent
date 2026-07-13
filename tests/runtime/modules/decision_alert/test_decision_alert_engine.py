from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
DECISION_ALERT_ENGINE = (
    SRC / "pumpagent" / "runtime" / "modules" / "decision_alert" / "engine.py"
)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import (
    AgentState,
    ConfidenceAssessment,
    DecisionAlert,
    HypothesisPackage,
    RuntimeEvent,
    ScenarioProbability,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    AlertCategory,
    AlertLevel,
    ConfidenceLevel,
    DecisionType,
    StateTransitionStatus,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.agent_state import add_agent_state
from pumpagent.runtime.modules.confidence import add_confidence_assessment
from pumpagent.runtime.modules.decision_alert import (
    DecisionAlertError,
    add_decision_alert,
    build_decision_alert,
)
from pumpagent.runtime.modules.hypothesis import add_hypothesis_package
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.scenario_probability import add_scenario_probability
from pumpagent.runtime.modules.structure import add_structural_evidence


def make_event_with_confidence() -> RuntimeEvent:
    event = make_base_event()
    event = add_market_snapshot_from_fixture(event, FIXTURE)
    event = add_observation_package(event)
    event = add_structural_evidence(event)
    event = add_market_efficiency_evidence(event)
    event = add_hypothesis_package(event)
    event = add_agent_state(event)
    event = add_scenario_probability(event)
    return add_confidence_assessment(event)


def make_base_event() -> RuntimeEvent:
    return RuntimeEvent(
        event_id="runtime-evt-1",
        schema_version="1.0",
        cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
    )


def make_event_through_market_efficiency() -> RuntimeEvent:
    event = make_base_event()
    event = add_market_snapshot_from_fixture(event, FIXTURE)
    event = add_observation_package(event)
    event = add_structural_evidence(event)
    return add_market_efficiency_evidence(event)


def make_event_through_hypothesis() -> RuntimeEvent:
    event = make_event_through_market_efficiency()
    event = add_hypothesis_package(event)
    return event


def make_event_through_agent_state() -> RuntimeEvent:
    event = make_event_through_hypothesis()
    event = add_agent_state(event)
    return event


def make_event_through_scenario_probability() -> RuntimeEvent:
    event = make_event_through_agent_state()
    event = add_scenario_probability(event)
    return event


def make_hypothesis_package(*, event_id: str = "runtime-evt-1") -> HypothesisPackage:
    return HypothesisPackage(
        event_id=event_id,
        hypothesis_label="current_condition_explanation",
        hypothesis_summary="Mock current-condition explanation.",
        supporting_evidence=("structure:mock",),
        contradicting_evidence=(),
        competing_hypotheses=(),
        current_hypothesis_confidence_context=ConfidenceLevel.MEDIUM,
        reasoning_notes="Mock hypothesis for Decision / Alert tests.",
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
        transition_reason="Mock official state for Decision / Alert tests.",
        supporting_evidence=("state:mock",),
        blocking_evidence=(),
        state_confidence_context=ConfidenceLevel.MEDIUM,
    )


def make_scenario_probability(
    *,
    event_id: str = "runtime-evt-1",
    primary_scenario: str = "continuation_persists",
) -> ScenarioProbability:
    return ScenarioProbability(
        event_id=event_id,
        scenario_set=(primary_scenario, "alternative_path"),
        scenario_probabilities={primary_scenario: 0.55, "alternative_path": 0.45},
        primary_scenario=primary_scenario,
        alternative_scenarios=("alternative_path",),
        supporting_evidence=("scenario:mock",),
        contradicting_evidence=(),
        uncertainty=UncertaintyLevel.MEDIUM,
        monitoring_focus=("runtime_attention",),
    )


def make_confidence_assessment(
    level: ConfidenceLevel,
    *,
    event_id: str = "runtime-evt-1",
) -> ConfidenceAssessment:
    return ConfidenceAssessment(
        event_id=event_id,
        final_confidence_level=level,
        confidence_summary="Mock confidence assessment.",
        confidence_drivers=("mock_driver",),
        confidence_reducers=(),
        data_quality_impact="market_snapshot_data_quality:valid",
        contradiction_impact="no_contradicting_evidence_reported",
        uncertainty_level=UncertaintyLevel.MEDIUM,
    )


def make_event_for_policy(
    *,
    current_state: AgentStateType,
    confidence_level: ConfidenceLevel,
    scenario_probability: ScenarioProbability | None = None,
) -> RuntimeEvent:
    event = make_base_event()
    return event.with_sections(
        hypothesis_package=make_hypothesis_package(),
        agent_state=make_agent_state(current_state),
        scenario_probability=scenario_probability
        if scenario_probability is not None
        else make_scenario_probability(),
        confidence_assessment=make_confidence_assessment(confidence_level),
    )


class DecisionAlertEngineTests(unittest.TestCase):
    def test_decision_alert_reads_required_reasoning_sections(self) -> None:
        event = make_event_with_confidence()

        decision_alert = build_decision_alert(
            event.hypothesis_package,
            event.agent_state,
            event.scenario_probability,
            event.confidence_assessment,
        )

        # v0.1 reads hypothesis for RuntimeEvent identity/explanation context only.
        # It must not turn hypothesis content into an autonomous decision rule.
        self.assertEqual(decision_alert.event_id, event.hypothesis_package.event_id)
        self.assertEqual(decision_alert.event_id, event.confidence_assessment.event_id)
        self.assertIn(
            event.scenario_probability.primary_scenario,
            decision_alert.monitoring_instructions[-1],
        )

    def test_decision_alert_produces_valid_decision_alert(self) -> None:
        event = make_event_with_confidence()

        decision_alert = build_decision_alert(
            event.hypothesis_package,
            event.agent_state,
            event.scenario_probability,
            event.confidence_assessment,
        )

        self.assertIsInstance(decision_alert, DecisionAlert)
        self.assertEqual(decision_alert.decision_type, DecisionType.REVIEW_REQUIRED)
        self.assertEqual(decision_alert.alert_level, AlertLevel.INFO)
        self.assertEqual(decision_alert.alert_category, AlertCategory.WATCH)
        self.assertTrue(decision_alert.non_execution_confirmation)
        self.assertIn("human review", decision_alert.display_message)

    def test_decision_alert_writes_only_decision_alert(self) -> None:
        event = make_event_with_confidence()

        updated = add_decision_alert(event)

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
        self.assertIs(event.confidence_assessment, updated.confidence_assessment)
        self.assertIsNotNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_decision_alert_does_not_modify_previous_sections(self) -> None:
        event = make_event_with_confidence()
        snapshot_before = event.market_snapshot.to_dict()
        observations_before = event.observation_package.to_dict()
        structure_before = event.structural_evidence.to_dict()
        efficiency_before = event.market_efficiency_evidence.to_dict()
        hypothesis_before = event.hypothesis_package.to_dict()
        agent_state_before = event.agent_state.to_dict()
        scenario_before = event.scenario_probability.to_dict()
        confidence_before = event.confidence_assessment.to_dict()

        updated = add_decision_alert(event)

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
        self.assertEqual(updated.confidence_assessment.to_dict(), confidence_before)

    def test_decision_alert_requires_hypothesis_package(self) -> None:
        event = make_event_through_market_efficiency()

        with self.assertRaisesRegex(DecisionAlertError, "hypothesis_package"):
            add_decision_alert(event)

    def test_decision_alert_requires_agent_state(self) -> None:
        event = make_event_through_hypothesis()

        with self.assertRaisesRegex(DecisionAlertError, "agent_state"):
            add_decision_alert(event)

    def test_missing_scenario_probability_returns_review_required(self) -> None:
        event = make_event_through_agent_state()
        event = add_confidence_assessment(event)

        updated = add_decision_alert(event)

        self.assertIsNone(updated.scenario_probability)
        self.assertEqual(updated.decision_alert.decision_type, DecisionType.REVIEW_REQUIRED)
        self.assertEqual(updated.decision_alert.alert_level, AlertLevel.INFO)
        self.assertEqual(updated.decision_alert.alert_category, AlertCategory.WATCH)
        self.assertIn("Scenario Probability is missing", updated.decision_alert.reason)

    def test_decision_alert_requires_confidence(self) -> None:
        event = make_event_through_scenario_probability()

        with self.assertRaisesRegex(DecisionAlertError, "confidence_assessment"):
            add_decision_alert(event)

    def test_decision_alert_stays_conservative_for_low_confidence_or_unknown_state(
        self,
    ) -> None:
        event = make_event_with_confidence()

        updated = add_decision_alert(event)

        self.assertEqual(event.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(updated.decision_alert.decision_type, DecisionType.REVIEW_REQUIRED)
        self.assertTrue(updated.decision_alert.follow_up_required)
        self.assertIn("Review reasoning chain", updated.decision_alert.reason)
        self.assertIn("No order", updated.decision_alert.notification_context)

    def test_low_confidence_returns_review_required(self) -> None:
        event = make_event_for_policy(
            current_state=AgentStateType.CONTINUATION_ALIVE,
            confidence_level=ConfidenceLevel.LOW,
        )

        updated = add_decision_alert(event)

        self.assertEqual(updated.decision_alert.decision_type, DecisionType.REVIEW_REQUIRED)
        self.assertEqual(updated.decision_alert.alert_level, AlertLevel.INFO)
        self.assertEqual(updated.decision_alert.alert_category, AlertCategory.WATCH)
        self.assertIn("Confidence is LOW", updated.decision_alert.reason)

    def test_medium_confidence_continuation_alive_returns_observe(self) -> None:
        event = make_event_for_policy(
            current_state=AgentStateType.CONTINUATION_ALIVE,
            confidence_level=ConfidenceLevel.MEDIUM,
        )

        updated = add_decision_alert(event)

        self.assertEqual(updated.decision_alert.decision_type, DecisionType.OBSERVE)
        self.assertEqual(updated.decision_alert.alert_level, AlertLevel.NONE)
        self.assertEqual(updated.decision_alert.alert_category, AlertCategory.NO_ACTION)
        self.assertFalse(updated.decision_alert.follow_up_required)
        self.assertIn("Continue observation", updated.decision_alert.reason)

    def test_medium_confidence_continuation_saturation_returns_warning(self) -> None:
        event = make_event_for_policy(
            current_state=AgentStateType.CONTINUATION_SATURATION,
            confidence_level=ConfidenceLevel.MEDIUM,
        )

        updated = add_decision_alert(event)

        self.assertEqual(updated.decision_alert.decision_type, DecisionType.WARNING)
        self.assertEqual(updated.decision_alert.alert_level, AlertLevel.WARNING)
        self.assertEqual(updated.decision_alert.alert_category, AlertCategory.WARNING)
        self.assertIn("saturation", updated.decision_alert.reason)

    def test_medium_confidence_first_failure_candidate_returns_high_attention_warning(
        self,
    ) -> None:
        event = make_event_for_policy(
            current_state=AgentStateType.FIRST_FAILURE_CANDIDATE,
            confidence_level=ConfidenceLevel.MEDIUM,
        )

        updated = add_decision_alert(event)

        self.assertEqual(updated.decision_alert.decision_type, DecisionType.WARNING)
        self.assertEqual(updated.decision_alert.alert_level, AlertLevel.WARNING)
        self.assertEqual(
            updated.decision_alert.alert_category,
            AlertCategory.HIGH_ATTENTION,
        )
        self.assertIn("First failure candidate", updated.decision_alert.reason)

    def test_unsupported_future_state_returns_review_required(self) -> None:
        event = make_event_for_policy(
            current_state=AgentStateType.FIRST_FAILURE,
            confidence_level=ConfidenceLevel.MEDIUM,
        )

        updated = add_decision_alert(event)

        self.assertEqual(updated.decision_alert.decision_type, DecisionType.REVIEW_REQUIRED)
        self.assertEqual(updated.decision_alert.alert_level, AlertLevel.INFO)
        self.assertEqual(updated.decision_alert.alert_category, AlertCategory.WATCH)
        self.assertIn("no approved MVP rule", updated.decision_alert.reason)

    def test_medium_policy_does_not_use_human_decision_required_or_critical(
        self,
    ) -> None:
        for state in (
            AgentStateType.CONTINUATION_ALIVE,
            AgentStateType.CONTINUATION_SATURATION,
            AgentStateType.FIRST_FAILURE_CANDIDATE,
        ):
            with self.subTest(state=state):
                event = make_event_for_policy(
                    current_state=state,
                    confidence_level=ConfidenceLevel.MEDIUM,
                )

                updated = add_decision_alert(event)

                self.assertNotEqual(
                    updated.decision_alert.decision_type,
                    DecisionType.HUMAN_DECISION_REQUIRED,
                )
                self.assertNotEqual(
                    updated.decision_alert.alert_level,
                    AlertLevel.CRITICAL,
                )

    def test_decision_alert_categories_are_runtime_attention_only(self) -> None:
        self.assertEqual(AlertCategory.NO_ACTION.value, "no_action")
        self.assertEqual(AlertCategory.WATCH.value, "watch")
        self.assertEqual(AlertCategory.WARNING.value, "warning")
        self.assertEqual(AlertCategory.HIGH_ATTENTION.value, "high_attention")

    def test_decision_alert_does_not_recommend_trading_instructions(self) -> None:
        event = make_event_with_confidence()

        decision_alert = build_decision_alert(
            event.hypothesis_package,
            event.agent_state,
            event.scenario_probability,
            event.confidence_assessment,
        )
        output_text = " ".join(_flatten_text(decision_alert.to_dict())).lower()

        forbidden_terms = (
            "enter long",
            "enter short",
            "buy",
            "sell",
            "entry price",
            "stop loss",
            "take profit",
            "position size",
            "execute",
            "execute order",
            "place order",
        )
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, output_text)

    def test_decision_alert_does_not_import_forbidden_layers(self) -> None:
        tree = ast.parse(DECISION_ALERT_ENGINE.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        imported_names = _imported_names_from(tree)
        forbidden_modules = (
            "pumpagent.live_data",
            "pumpagent.runtime.modules.learning_memory",
            "pumpagent.runtime.modules.market_data",
            "pumpagent.runtime.orchestrator",
            "pumpagent.runtime.modules.trading",
        )
        forbidden_names = {
            "LearningMetadata",
            "MarketSnapshot",
            "RuntimeEventOrchestrator",
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


def _flatten_text(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        items: list[str] = []
        for key, item in value.items():
            items.extend(_flatten_text(key))
            items.extend(_flatten_text(item))
        return tuple(items)
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            items.extend(_flatten_text(item))
        return tuple(items)
    if value is None:
        return ()
    return (str(value),)


if __name__ == "__main__":
    unittest.main()
