from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
SCENARIO_ENGINE = (
    SRC / "pumpagent" / "runtime" / "modules" / "scenario_probability" / "engine.py"
)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from pumpagent.runtime.domain import (
    AgentState,
    DiagnosticOutcome,
    HealthyActiveProcessAssessment,
    HealthyBaselineReference,
    HypothesisEvidenceReference,
    HypothesisLifecycleStatus,
    HypothesisPackage,
    HypothesisSemanticCode,
    LossOfEfficiencyAssessment,
    ProcessEvidence,
    ProcessEvidenceAvailability,
    ProcessEvidenceFamily,
    ProcessEvidenceItem,
    ProcessEvidenceRelationship,
    ProcessQualityAssessment,
    ProcessQualityAssessmentReference,
    ProcessQualityEvidenceReference,
    ProcessQualityObservationReference,
    ProcessState,
    ProcessTransition,
    RuntimeEvent,
    ScenarioArtifactType,
    ScenarioIdentifier,
    ScenarioProbability,
    ScenarioReasonCode,
    canonical_healthy_baseline_id,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    EvidenceStrength,
    ProcessDirection,
    StateTransitionStatus,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.scenario_probability import (
    ScenarioProbabilityError,
    add_scenario_probability,
    build_scenario_probability,
)


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def make_hypothesis(
    *,
    event_id: str = "event-2",
    semantic_code: HypothesisSemanticCode = (
        HypothesisSemanticCode.CONTINUATION_EXPLANATION
    ),
) -> HypothesisPackage:
    return HypothesisPackage(
        event_id=event_id,
        episode_id="episode-1",
        hypothesis_id=f"hypothesis-{event_id}",
        hypothesis_label="Human-readable current explanation.",
        hypothesis_summary="Current condition explanation.",
        supporting_evidence=(
            HypothesisEvidenceReference(
                source_event_id=event_id,
                source_section="structural_evidence",
                evidence_key="structure",
                description="Structure evidence.",
            ),
        ),
        contradicting_evidence=(),
        explanation_confidence_score=50,
        current_hypothesis_confidence_context=ConfidenceLevel.MEDIUM,
        reasoning_notes="Current explanation only.",
        uncertainty=UncertaintyLevel.LOW,
        semantic_code=semantic_code,
        lifecycle_status=HypothesisLifecycleStatus.CREATED,
        previous_hypothesis_id=None,
        previous_runtime_event_id=None,
        hypothesis_change_reason="Initial hypothesis.",
    )


def make_agent_state(
    state: AgentStateType,
    *,
    event_id: str = "event-2",
) -> AgentState:
    return AgentState(
        event_id=event_id,
        current_state=state,
        process_direction=ProcessDirection.UP,
        previous_state=AgentStateType.UNKNOWN,
        state_transition_status=(
            StateTransitionStatus.UNCHANGED
            if state is AgentStateType.UNKNOWN
            else StateTransitionStatus.CHANGED
        ),
        transition_reason="Legacy Scenario policy input.",
        supporting_evidence=("state",),
        blocking_evidence=(),
        state_confidence_context=ConfidenceLevel.MEDIUM,
    )


def make_process_evidence(
    *,
    event_id: str = "event-2",
    timestamp: datetime = NOW,
) -> ProcessEvidence:
    item = ProcessEvidenceItem(
        evidence_family=ProcessEvidenceFamily.PRICE,
        evidence_key="price_process",
        description="Price supports the classified process.",
        relationship=ProcessEvidenceRelationship.SUPPORTING,
        source_module="process_classification",
        source_field="current_process_state",
        observation_timestamp=timestamp,
        availability_status=ProcessEvidenceAvailability.AVAILABLE,
        normalized_value="continuation_alive",
        timeframe="5m",
    )
    return ProcessEvidence(
        episode_id="episode-1",
        runtime_event_id=event_id,
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="5m",
        observation_timestamp=timestamp,
        current_process_state=ProcessState.CONTINUATION_ALIVE,
        process_direction=ProcessDirection.UP,
        previous_process_state=ProcessState.UNKNOWN,
        detected_transition=ProcessTransition.CHANGED,
        process_summary="Continuation is active.",
        supporting_evidence=(item,),
        contradicting_evidence=(),
        neutral_evidence=(),
        available_evidence_families=frozenset((ProcessEvidenceFamily.PRICE,)),
        missing_evidence_families=frozenset(),
        insufficiency_reasons=(),
        evidence_strength=EvidenceStrength.MODERATE,
        uncertainty_level=UncertaintyLevel.LOW,
    )


def make_process_quality(
    *,
    event_id: str = "event-2",
    timestamp: datetime = NOW,
) -> ProcessQualityAssessment:
    observation = ProcessQualityObservationReference(
        episode_id="episode-1",
        runtime_event_id=event_id,
        observation_id=f"process-quality-observation:{event_id}",
        observation_timestamp=timestamp,
    )
    support = ProcessQualityEvidenceReference(
        source_observation=observation,
        source_section="process_evidence",
        evidence_key="healthy_process",
        description="Healthy process is supported.",
    )
    missing_baseline = ProcessQualityEvidenceReference(
        source_observation=observation,
        source_section="process_quality_history",
        evidence_key="healthy_baseline",
        description="No authenticated baseline is available.",
    )
    return ProcessQualityAssessment(
        assessment_id=f"process-quality-assessment:episode-1:{event_id}",
        episode_id="episode-1",
        runtime_event_id=event_id,
        current_observation=observation,
        healthy_active_process=HealthyActiveProcessAssessment(
            outcome=DiagnosticOutcome.SUPPORTED,
            supporting_evidence=(support,),
            contradicting_evidence=(),
            missing_evidence=(),
            inhibiting_evidence=(),
        ),
        loss_of_efficiency=LossOfEfficiencyAssessment(
            outcome=DiagnosticOutcome.INHIBITED,
            healthy_baseline_reference=None,
            supporting_evidence=(),
            contradicting_evidence=(),
            missing_evidence=(missing_baseline,),
            inhibiting_evidence=(),
        ),
        uncertainty_level=UncertaintyLevel.LOW,
    )


def make_baseline() -> HealthyBaselineReference:
    timestamp = NOW - timedelta(minutes=5)
    observation = ProcessQualityObservationReference(
        episode_id="episode-1",
        runtime_event_id="event-1",
        observation_id="process-quality-observation:event-1",
        observation_timestamp=timestamp,
    )
    assessment = ProcessQualityAssessmentReference(
        assessment_id="process-quality-assessment:episode-1:event-1",
        episode_id="episode-1",
        runtime_event_id="event-1",
        observation=observation,
        healthy_active_process_outcome=DiagnosticOutcome.SUPPORTED,
        loss_of_efficiency_outcome=DiagnosticOutcome.INHIBITED,
    )
    return HealthyBaselineReference(
        baseline_id=canonical_healthy_baseline_id(
            "episode-1",
            assessment.assessment_id,
        ),
        episode_id="episode-1",
        source_assessment=assessment,
    )


def build(
    state: AgentStateType = AgentStateType.CONTINUATION_ALIVE,
    *,
    event_id: str = "event-2",
    timestamp: datetime = NOW,
    semantic_code: HypothesisSemanticCode = (
        HypothesisSemanticCode.CONTINUATION_EXPLANATION
    ),
    baseline: HealthyBaselineReference | None = None,
    previous: ScenarioProbability | None = None,
) -> ScenarioProbability:
    return build_scenario_probability(
        make_hypothesis(event_id=event_id, semantic_code=semantic_code),
        make_agent_state(state, event_id=event_id),
        make_process_evidence(event_id=event_id, timestamp=timestamp),
        make_process_quality(event_id=event_id, timestamp=timestamp),
        healthy_baseline_reference=baseline,
        previous_scenario_probability=previous,
        active_episode_id="episode-1",
    )


class ScenarioProbabilityEngineMigrationTests(unittest.TestCase):
    def test_produces_valid_canonical_contract_and_serialization(self) -> None:
        scenario = build()
        self.assertIsInstance(scenario, ScenarioProbability)
        self.assertEqual(
            scenario.scenario_probability_id,
            "scenario-probability:episode-1:event-2:hypothesis-event-2",
        )
        self.assertEqual(
            scenario.source_process_evidence_id,
            "process-evidence:episode-1:event-2",
        )
        self.assertEqual(
            scenario.source_process_quality_assessment_id,
            "process-quality-assessment:episode-1:event-2",
        )
        self.assertEqual(scenario.source_hypothesis_id, "hypothesis-event-2")
        serialized = scenario.to_dict()
        self.assertEqual(len(serialized["distribution"]), 5)
        self.assertEqual(
            serialized["distribution"][1],
            {
                "scenario": "continuation_persists",
                "probability": "0.650000",
            },
        )
        json.dumps(serialized)

    def test_preserves_hypothesis_semantic_code_without_inference(self) -> None:
        for semantic_code in HypothesisSemanticCode:
            with self.subTest(semantic_code=semantic_code):
                scenario = build(semantic_code=semantic_code)
                self.assertIs(
                    scenario.hypothesis_semantic_code,
                    semantic_code,
                )

    def test_every_existing_policy_produces_complete_frozen_distribution(self) -> None:
        cases = (
            (
                AgentStateType.UNKNOWN,
                ScenarioIdentifier.CONTINUE_OBSERVATION,
                (
                    "0.700000",
                    "0.100000",
                    "0.100000",
                    "0.050000",
                    "0.050000",
                ),
            ),
            (
                AgentStateType.CONTINUATION_ALIVE,
                ScenarioIdentifier.CONTINUATION_PERSISTS,
                (
                    "0.100000",
                    "0.650000",
                    "0.150000",
                    "0.070000",
                    "0.030000",
                ),
            ),
            (
                AgentStateType.CONTINUATION_SATURATION,
                ScenarioIdentifier.SATURATION_PERSISTS,
                (
                    "0.150000",
                    "0.150000",
                    "0.550000",
                    "0.120000",
                    "0.030000",
                ),
            ),
            (
                AgentStateType.FIRST_FAILURE_CANDIDATE,
                ScenarioIdentifier.FAILURE_CANDIDATE_PERSISTS,
                (
                    "0.100000",
                    "0.080000",
                    "0.120000",
                    "0.650000",
                    "0.050000",
                ),
            ),
        )
        for state, primary, expected in cases:
            with self.subTest(state=state):
                scenario = build(state)
                self.assertIs(scenario.primary_scenario, primary)
                self.assertEqual(
                    tuple(format(item.probability, "f") for item in scenario.distribution),
                    expected,
                )
                self.assertEqual(
                    sum(
                        (item.probability for item in scenario.distribution),
                        start=Decimal("0.000000"),
                    ),
                    Decimal("1.000000"),
                )

    def test_unknown_legacy_state_uses_observation_fallback_metadata(self) -> None:
        scenario = build(AgentStateType.UNKNOWN)
        self.assertEqual(
            scenario.reason_codes,
            (ScenarioReasonCode.CONTINUE_OBSERVATION_FALLBACK,),
        )
        self.assertIs(scenario.uncertainty, UncertaintyLevel.HIGH)

    def test_populates_current_provenance(self) -> None:
        scenario = build()
        self.assertEqual(
            tuple(item.artifact_type for item in scenario.supporting_provenance),
            (
                ScenarioArtifactType.PROCESS_EVIDENCE,
                ScenarioArtifactType.PROCESS_QUALITY,
                ScenarioArtifactType.HYPOTHESIS,
            ),
        )
        self.assertTrue(
            all(
                item.runtime_event_id == "event-2"
                for item in scenario.supporting_provenance
            )
        )

    def test_populates_optional_historical_provenance(self) -> None:
        previous = build(
            event_id="event-1",
            timestamp=NOW - timedelta(minutes=5),
        )
        baseline = make_baseline()
        scenario = build(baseline=baseline, previous=previous)
        self.assertEqual(
            scenario.source_healthy_baseline_id,
            baseline.baseline_id,
        )
        self.assertEqual(
            scenario.previous_scenario_probability_id,
            previous.scenario_probability_id,
        )
        self.assertEqual(len(scenario.supporting_provenance), 5)

    def test_rejects_current_identity_mismatches(self) -> None:
        with self.assertRaisesRegex(
            ScenarioProbabilityError,
            "ProcessEvidence.runtime_event_id",
        ):
            build_scenario_probability(
                make_hypothesis(),
                make_agent_state(AgentStateType.CONTINUATION_ALIVE),
                make_process_evidence(event_id="other-event"),
                make_process_quality(),
            )
        with self.assertRaisesRegex(
            ScenarioProbabilityError,
            "ProcessQualityAssessment.runtime_event_id",
        ):
            build_scenario_probability(
                make_hypothesis(),
                make_agent_state(AgentStateType.CONTINUATION_ALIVE),
                make_process_evidence(),
                make_process_quality(event_id="other-event"),
            )

    def test_rejects_cross_episode_and_future_history(self) -> None:
        previous = build(
            event_id="event-1",
            timestamp=NOW - timedelta(minutes=5),
        )
        object.__setattr__(previous, "episode_id", "episode-2")
        with self.assertRaisesRegex(ScenarioProbabilityError, "cross Episode"):
            build(previous=previous)

        future = build(
            event_id="event-3",
            timestamp=NOW + timedelta(minutes=5),
        )
        with self.assertRaisesRegex(ScenarioProbabilityError, "precede"):
            build(previous=future)

    def test_adds_only_scenario_section_when_required_artifacts_are_supplied(
        self,
    ) -> None:
        hypothesis = make_hypothesis()
        agent_state = make_agent_state(AgentStateType.CONTINUATION_ALIVE)
        event = RuntimeEvent(
            event_id="event-2",
            schema_version="1.0",
            cycle_timestamp=NOW,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="5m",
            hypothesis_package=hypothesis,
            agent_state=agent_state,
        )
        updated = add_scenario_probability(
            event,
            process_evidence=make_process_evidence(),
            process_quality_assessment=make_process_quality(),
        )
        self.assertIsNone(event.scenario_probability)
        self.assertIsInstance(updated.scenario_probability, ScenarioProbability)
        self.assertIs(updated.hypothesis_package, hypothesis)
        self.assertIs(updated.agent_state, agent_state)

    def test_does_not_import_confidence_decision_or_raw_market_modules(self) -> None:
        tree = ast.parse(SCENARIO_ENGINE.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        forbidden = (
            "pumpagent.runtime.modules.confidence",
            "pumpagent.runtime.modules.decision",
            "pumpagent.runtime.modules.decision_alert",
            "pumpagent.runtime.modules.market_data",
            "pumpagent.live_data",
        )
        self.assertFalse(
            any(
                imported == module or imported.startswith(f"{module}.")
                for imported in imports
                for module in forbidden
            )
        )


def _imports_from(tree: ast.AST) -> tuple[str, ...]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(imports)


if __name__ == "__main__":
    unittest.main()
