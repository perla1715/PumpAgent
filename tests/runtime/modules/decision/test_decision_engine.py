from __future__ import annotations

import ast
import copy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
from unittest import TestCase

from pumpagent.runtime.domain import (
    ConfidenceAssessment,
    DiagnosticOutcome,
    HypothesisEvidenceReference,
    HypothesisSemanticCode,
    ProcessQualityEvidenceReference,
    ScenarioArtifactType,
    ScenarioIdentifier,
    ScenarioWeight,
)
from pumpagent.runtime.domain.decision import (
    DecisionReasonCode,
    DecisionReference,
    DecisionType,
    canonical_decision_id,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    ProcessDirection,
    UncertaintyLevel,
)
from pumpagent.runtime.domain.process_evidence import (
    ProcessState,
    ProcessTransition,
)
from pumpagent.runtime.modules.decision import (
    DecisionEngineInput,
    DecisionValidationError,
    build_decision_assessment,
)
from pumpagent.runtime.modules.scenario_probability import (
    build_scenario_probability,
)
from tests.runtime.modules.scenario_probability.test_scenario_probability_engine import (
    NOW,
    make_agent_state,
    make_baseline,
    make_hypothesis,
    make_process_evidence,
    make_process_quality,
)


DECISION_ENGINE = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "pumpagent"
    / "runtime"
    / "modules"
    / "decision"
    / "engine.py"
)


def directional_input(*, long: bool) -> DecisionEngineInput:
    baseline = make_baseline()
    process = make_process_evidence()
    if not long:
        process = replace(
            process,
            current_process_state=ProcessState.WEAKENING,
            process_direction=ProcessDirection.DOWN,
            previous_process_state=ProcessState.CONTINUATION_ALIVE,
            detected_transition=ProcessTransition.CHANGED,
            process_summary="The active process is weakening.",
        )

    hypothesis = make_hypothesis(
        semantic_code=(
            HypothesisSemanticCode.CONTINUATION_EXPLANATION
            if long
            else HypothesisSemanticCode.WEAKENING_EXPLANATION
        )
    )
    hypothesis = replace(
        hypothesis,
        hypothesis_label=(
            "Continuation remains active" if long else "Move is weakening"
        ),
        contradicting_evidence=(),
        uncertainty=UncertaintyLevel.MEDIUM,
    )

    quality = make_process_quality()
    observation = quality.current_observation
    healthy_reference = ProcessQualityEvidenceReference(
        source_observation=observation,
        source_section="process_evidence",
        evidence_key="decision_healthy",
        description="Current Process supports the health conclusion.",
    )
    loss_reference = ProcessQualityEvidenceReference(
        source_observation=observation,
        source_section="process_evidence",
        evidence_key="decision_efficiency",
        description="Current Process supports the efficiency conclusion.",
    )
    if long:
        healthy = replace(
            quality.healthy_active_process,
            supporting_evidence=(healthy_reference,),
            contradicting_evidence=(),
            missing_evidence=(),
            inhibiting_evidence=(),
        )
        loss = replace(
            quality.loss_of_efficiency,
            outcome=DiagnosticOutcome.NOT_ESTABLISHED,
            healthy_baseline_reference=baseline,
            supporting_evidence=(),
            contradicting_evidence=(loss_reference,),
            missing_evidence=(),
            inhibiting_evidence=(),
        )
        scenario_state = AgentStateType.CONTINUATION_ALIVE
    else:
        healthy = replace(
            quality.healthy_active_process,
            outcome=DiagnosticOutcome.NOT_ESTABLISHED,
            supporting_evidence=(),
            contradicting_evidence=(healthy_reference,),
            missing_evidence=(),
            inhibiting_evidence=(),
        )
        loss = replace(
            quality.loss_of_efficiency,
            outcome=DiagnosticOutcome.SUPPORTED,
            healthy_baseline_reference=baseline,
            supporting_evidence=(loss_reference,),
            contradicting_evidence=(),
            missing_evidence=(),
            inhibiting_evidence=(),
        )
        scenario_state = AgentStateType.FIRST_FAILURE_CANDIDATE
    quality = replace(
        quality,
        healthy_active_process=healthy,
        loss_of_efficiency=loss,
        uncertainty_level=UncertaintyLevel.MEDIUM,
    )

    scenario = build_scenario_probability(
        hypothesis,
        make_agent_state(scenario_state),
        process,
        quality,
        healthy_baseline_reference=baseline,
        active_episode_id=process.episode_id,
    )
    confidence = ConfidenceAssessment(
        event_id=process.runtime_event_id,
        episode_id=process.episode_id,
        source_hypothesis_id=hypothesis.hypothesis_id,
        final_confidence_level=ConfidenceLevel.MEDIUM,
        confidence_summary="Completed chain has medium reliability.",
        confidence_drivers=("canonical_scenario_available",),
        confidence_reducers=(),
        data_quality_impact="market_snapshot_data_quality:valid",
        contradiction_impact="no_contradicting_evidence_reported",
        uncertainty_level=UncertaintyLevel.MEDIUM,
    )
    return DecisionEngineInput(
        process_quality_assessment=quality,
        process_evidence=process,
        hypothesis=hypothesis,
        scenario_probability=scenario,
        confidence_assessment=confidence,
        healthy_baseline_reference=baseline,
    )


def with_scenario(value: DecisionEngineInput, scenario) -> DecisionEngineInput:
    return replace(value, scenario_probability=scenario)


class DecisionEngineTests(TestCase):
    def test_valid_directional_inputs_preserve_approved_outputs(self) -> None:
        long_result = build_decision_assessment(directional_input(long=True))
        short_result = build_decision_assessment(directional_input(long=False))

        self.assertIs(long_result.decision_type, DecisionType.LOOK_FOR_LONG)
        self.assertIs(short_result.decision_type, DecisionType.LOOK_FOR_SHORT)
        self.assertIn(
            DecisionReasonCode.BULLISH_SCENARIO_CONFIRMED,
            long_result.reason_codes,
        )
        self.assertIn(
            DecisionReasonCode.BEARISH_SCENARIO_CONFIRMED,
            short_result.reason_codes,
        )
        self.assertTrue(long_result.non_execution_confirmation)
        with self.assertRaises(FrozenInstanceError):
            long_result.decision_type = DecisionType.STAY_OUT

    def test_non_initial_unknown_remains_invalid(self) -> None:
        value = directional_input(long=True)
        process = copy.copy(value.process_evidence)
        object.__setattr__(
            process,
            "current_process_state",
            ProcessState.UNKNOWN,
        )
        object.__setattr__(
            process,
            "previous_process_state",
            ProcessState.CONTINUATION_ALIVE,
        )
        object.__setattr__(
            process,
            "detected_transition",
            ProcessTransition.BECAME_UNKNOWN,
        )
        with self.assertRaisesRegex(
            DecisionValidationError,
            "canonical INITIAL lifecycle",
        ):
            replace(value, process_evidence=process)

    def test_fabricated_initial_lifecycle_remains_invalid(self) -> None:
        value = directional_input(long=True)
        process = copy.copy(value.process_evidence)
        object.__setattr__(process, "previous_process_state", None)
        object.__setattr__(
            process,
            "detected_transition",
            ProcessTransition.INITIAL,
        )
        with self.assertRaisesRegex(
            DecisionValidationError,
            "INITIAL lifecycle requires ProcessState.UNKNOWN",
        ):
            replace(value, process_evidence=process)

    def test_mixed_low_confidence_uncertainty_and_inhibition_stay_out(self) -> None:
        base = directional_input(long=True)
        mixed_reference = HypothesisEvidenceReference(
            source_event_id=base.hypothesis.event_id,
            source_section="market_efficiency_evidence",
            evidence_key="mixed_fixture",
            description="Completed upstream evidence is mixed.",
        )
        cases = (
            replace(
                base,
                hypothesis=replace(
                    base.hypothesis,
                    contradicting_evidence=(mixed_reference,),
                ),
            ),
            replace(
                base,
                confidence_assessment=replace(
                    base.confidence_assessment,
                    final_confidence_level=ConfidenceLevel.LOW,
                ),
            ),
            replace(
                base,
                confidence_assessment=replace(
                    base.confidence_assessment,
                    uncertainty_level=UncertaintyLevel.HIGH,
                ),
            ),
            replace(
                base,
                process_quality_assessment=replace(
                    base.process_quality_assessment,
                    loss_of_efficiency=replace(
                        base.process_quality_assessment.loss_of_efficiency,
                        outcome=DiagnosticOutcome.INHIBITED,
                        contradicting_evidence=(),
                        inhibiting_evidence=(
                            base.process_quality_assessment.loss_of_efficiency
                            .contradicting_evidence[0],
                        ),
                    ),
                ),
            ),
        )
        expected = (
            DecisionReasonCode.MIXED_EVIDENCE,
            DecisionReasonCode.CONFIDENCE_BELOW_THRESHOLD,
            DecisionReasonCode.BLOCKING_UNCERTAINTY,
            DecisionReasonCode.UPSTREAM_INHIBITION,
        )
        for value, reason in zip(cases, expected):
            result = build_decision_assessment(value)
            self.assertIs(result.decision_type, DecisionType.STAY_OUT)
            self.assertEqual(result.reason_codes, (reason,))

    def test_canonical_scenario_contradiction_preserves_mixed_evidence_rule(
        self,
    ) -> None:
        base = directional_input(long=True)
        hypothesis_reference = next(
            item
            for item in base.scenario_probability.supporting_provenance
            if item.artifact_type is ScenarioArtifactType.HYPOTHESIS
        )
        scenario = replace(
            base.scenario_probability,
            supporting_provenance=tuple(
                item
                for item in base.scenario_probability.supporting_provenance
                if item is not hypothesis_reference
            ),
            contradicting_provenance=(hypothesis_reference,),
        )
        result = build_decision_assessment(with_scenario(base, scenario))
        self.assertIs(result.decision_type, DecisionType.STAY_OUT)
        self.assertEqual(
            result.reason_codes,
            (DecisionReasonCode.MIXED_EVIDENCE,),
        )

    def test_missing_required_input_and_identity_mismatches_fail(self) -> None:
        base = directional_input(long=True)
        with self.assertRaisesRegex(
            DecisionValidationError,
            "process_quality_assessment",
        ):
            DecisionEngineInput(
                process_quality_assessment=None,
                process_evidence=base.process_evidence,
                hypothesis=base.hypothesis,
                scenario_probability=base.scenario_probability,
                confidence_assessment=base.confidence_assessment,
            )
        with self.assertRaisesRegex(DecisionValidationError, "share one Episode"):
            replace(
                base,
                hypothesis=replace(base.hypothesis, episode_id="other-episode"),
            )
        forged = copy.copy(base.scenario_probability)
        object.__setattr__(forged, "source_hypothesis_id", "other-hypothesis")
        with self.assertRaisesRegex(
            DecisionValidationError,
            "active Hypothesis",
        ):
            with_scenario(base, forged)

    def test_invalid_probability_and_primary_scenario_fail_at_input(self) -> None:
        base = directional_input(long=True)
        bad_sum = copy.copy(base.scenario_probability)
        weights = list(bad_sum.distribution)
        weights[0] = ScenarioWeight(
            ScenarioIdentifier.CONTINUE_OBSERVATION,
            Decimal("0.200000"),
        )
        object.__setattr__(bad_sum, "distribution", tuple(weights))
        with self.assertRaisesRegex(DecisionValidationError, "sum to 1.000000"):
            with_scenario(base, bad_sum)

        missing_primary = copy.copy(base.scenario_probability)
        object.__setattr__(missing_primary, "primary_scenario", "")
        with self.assertRaisesRegex(
            DecisionValidationError,
            "primary scenario",
        ):
            with_scenario(base, missing_primary)

    def test_identifiers_provenance_and_serialization_are_preserved(self) -> None:
        base = directional_input(long=True)
        result = build_decision_assessment(base)
        self.assertEqual(
            result.decision_id,
            canonical_decision_id(
                base.process_evidence.episode_id,
                base.process_evidence.runtime_event_id,
            ),
        )
        self.assertEqual(
            result.scenario_probability_reference,
            base.scenario_probability.scenario_probability_id,
        )
        self.assertIn(
            base.scenario_probability.primary_scenario.value,
            result.provenance,
        )
        json.dumps(result.to_dict())

    def test_previous_decision_is_provenance_only_and_output_is_deterministic(
        self,
    ) -> None:
        base = directional_input(long=True)
        first = build_decision_assessment(base)
        previous = DecisionReference(
            decision_id=canonical_decision_id(
                base.process_evidence.episode_id,
                "previous-event",
            ),
            episode_id=base.process_evidence.episode_id,
            runtime_event_id="previous-event",
            decision_type=DecisionType.STAY_OUT,
            created_at=base.process_evidence.observation_timestamp
            - timedelta(minutes=5),
        )
        with_previous = replace(base, previous_decision_reference=previous)
        second = build_decision_assessment(with_previous)
        repeated = build_decision_assessment(with_previous)

        self.assertEqual(second.decision_type, first.decision_type)
        self.assertEqual(second.reason_codes, first.reason_codes)
        self.assertEqual(second.decision_type, repeated.decision_type)
        self.assertEqual(second.reason_codes, repeated.reason_codes)
        self.assertEqual(second.previous_decision_reference, previous)

    def test_decision_has_no_legacy_scenario_field_access(self) -> None:
        source = DECISION_ENGINE.read_text(encoding="utf-8")
        ast.parse(source)
        for legacy_access in (
            "scenario.event_id",
            "scenario.supporting_evidence",
            "scenario.contradicting_evidence",
            "value.scenario_probabilities",
            "value.scenario_set",
            "value.alternative_scenarios",
            "value.monitoring_focus",
            "value.metadata",
        ):
            with self.subTest(legacy_access=legacy_access):
                self.assertNotIn(legacy_access, source)
