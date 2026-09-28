"""Pure Decision Engine over completed canonical analytical outputs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.confidence_assessment import ConfidenceAssessment
from pumpagent.runtime.domain.decision import (
    DecisionAssessment,
    DecisionReasonCode,
    DecisionReference,
    DecisionStatus,
    DecisionType,
    canonical_decision_id,
)
from pumpagent.runtime.domain.enums import (
    ConfidenceLevel,
    ProcessDirection,
    UncertaintyLevel,
)
from pumpagent.runtime.domain.hypothesis_package import HypothesisPackage
from pumpagent.runtime.domain.process_evidence import (
    ProcessEvidence,
    ProcessState,
    ProcessTransition,
)
from pumpagent.runtime.domain.process_quality import (
    DiagnosticOutcome,
    HealthyBaselineReference,
    ProcessQualityAssessment,
)
from pumpagent.runtime.domain.scenario_probability import (
    ScenarioIdentifier,
    ScenarioProbability,
)


DECISION_ENGINE_INPUT_SCHEMA_VERSION = "decision_engine_input_v1"
_CONFIDENCE_THRESHOLD = frozenset(
    (ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH, ConfidenceLevel.VERY_HIGH)
)
_BLOCKING_UNCERTAINTY = frozenset(
    (UncertaintyLevel.HIGH, UncertaintyLevel.UNKNOWN)
)
_LONG_HYPOTHESIS_LABELS = frozenset(
    ("continuation remains active", "bullish continuation", "recovery")
)
_SHORT_HYPOTHESIS_LABELS = frozenset(
    ("move is weakening", "bearish continuation", "transition toward dump")
)
_BEARISH_SCENARIOS = frozenset(
    (
        ScenarioIdentifier.FAILURE_CANDIDATE_PERSISTS,
        ScenarioIdentifier.FIRST_FAILURE_CONFIRMS,
    )
)


class DecisionValidationError(ValueError):
    """Raised when completed analytical outputs do not form one valid chain."""


@dataclass(frozen=True)
class DecisionEngineInput(SerializableMixin):
    process_quality_assessment: ProcessQualityAssessment
    process_evidence: ProcessEvidence
    hypothesis: HypothesisPackage
    scenario_probability: ScenarioProbability
    confidence_assessment: ConfidenceAssessment
    healthy_baseline_reference: HealthyBaselineReference | None = None
    previous_decision_reference: DecisionReference | None = None
    schema_version: str = DECISION_ENGINE_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _validate_input(self)


def build_decision_assessment(value: DecisionEngineInput) -> DecisionAssessment:
    if not isinstance(value, DecisionEngineInput):
        raise DecisionValidationError("value must be a DecisionEngineInput.")

    decision_type, reasons = _select_decision(value)
    episode_id = value.process_evidence.episode_id
    event_id = value.process_evidence.runtime_event_id
    return DecisionAssessment(
        episode_id=episode_id,
        decision_id=canonical_decision_id(episode_id, event_id),
        runtime_event_id=event_id,
        decision_type=decision_type,
        decision_status=DecisionStatus.COMPLETED,
        reason_codes=reasons,
        confidence_reference=f"confidence:{episode_id}:{event_id}",
        hypothesis_reference=value.hypothesis.hypothesis_id,
        scenario_probability_reference=(
            value.scenario_probability.scenario_probability_id
        ),
        process_quality_reference=value.process_quality_assessment.to_reference(),
        process_evidence_reference=f"process-evidence:{episode_id}:{event_id}",
        healthy_baseline_reference=value.healthy_baseline_reference,
        previous_decision_reference=value.previous_decision_reference,
        created_at=value.process_quality_assessment.current_observation.observation_timestamp,
        provenance=(
            value.process_quality_assessment.assessment_id,
            value.process_evidence.runtime_event_id,
            value.hypothesis.hypothesis_id,
            value.scenario_probability.primary_scenario.value,
            value.confidence_assessment.final_confidence_level.value,
        ),
    )


def _validate_input(value: DecisionEngineInput) -> None:
    required = (
        ("process_quality_assessment", value.process_quality_assessment, ProcessQualityAssessment),
        ("process_evidence", value.process_evidence, ProcessEvidence),
        ("hypothesis", value.hypothesis, HypothesisPackage),
        ("scenario_probability", value.scenario_probability, ScenarioProbability),
        ("confidence_assessment", value.confidence_assessment, ConfidenceAssessment),
    )
    for name, item, expected in required:
        if not isinstance(item, expected):
            raise DecisionValidationError(f"{name} must be a {expected.__name__}.")

    process = value.process_evidence
    quality = value.process_quality_assessment
    hypothesis = value.hypothesis
    scenario = value.scenario_probability
    confidence = value.confidence_assessment
    episode_id = process.episode_id
    event_id = process.runtime_event_id

    if any(
        item_episode != episode_id
        for item_episode in (
            quality.episode_id,
            hypothesis.episode_id,
            scenario.episode_id,
            confidence.episode_id,
        )
    ):
        raise DecisionValidationError("All Decision inputs must share one Episode.")
    if any(
        item_event != event_id
        for item_event in (
            quality.runtime_event_id,
            hypothesis.event_id,
            scenario.runtime_event_id,
            confidence.event_id,
        )
    ):
        raise DecisionValidationError("All Decision inputs must share one Runtime event.")
    if quality.current_observation.observation_timestamp != process.observation_timestamp:
        raise DecisionValidationError(
            "Process Quality and Process Evidence observation timestamps must match."
        )
    canonical_initial_process = (
        process.previous_process_state is None
        and process.detected_transition is ProcessTransition.INITIAL
    )
    if (
        process.current_process_state is ProcessState.UNKNOWN
        and not canonical_initial_process
    ):
        raise DecisionValidationError(
            "Decision accepts UNKNOWN only for the canonical INITIAL lifecycle."
        )
    if (
        process.current_process_state is not ProcessState.UNKNOWN
        and canonical_initial_process
    ):
        raise DecisionValidationError(
            "The canonical INITIAL lifecycle requires ProcessState.UNKNOWN."
        )
    if scenario.source_hypothesis_id != hypothesis.hypothesis_id:
        raise DecisionValidationError(
            "Scenario Probability must reference the active Hypothesis."
        )
    if confidence.source_hypothesis_id != hypothesis.hypothesis_id:
        raise DecisionValidationError(
            "Confidence must reference the active Hypothesis."
        )
    if not hypothesis.supporting_evidence:
        raise DecisionValidationError("Hypothesis mandatory provenance is missing.")
    if not process.supporting_evidence:
        raise DecisionValidationError("Process Evidence mandatory provenance is missing.")
    if not scenario.supporting_provenance:
        raise DecisionValidationError(
            "Scenario Probability mandatory provenance is missing."
        )
    _validate_scenario_probabilities(scenario)

    baseline = quality.loss_of_efficiency.healthy_baseline_reference
    if value.healthy_baseline_reference != baseline:
        raise DecisionValidationError(
            "Decision Healthy Baseline must match Process Quality exactly."
        )
    if value.previous_decision_reference is not None:
        previous = value.previous_decision_reference
        if not isinstance(previous, DecisionReference):
            raise DecisionValidationError(
                "previous_decision_reference must be a DecisionReference or None."
            )
        if previous.episode_id != episode_id:
            raise DecisionValidationError("Previous Decision cannot cross Episodes.")
        if previous.created_at >= process.observation_timestamp:
            raise DecisionValidationError(
                "Previous Decision must precede the current analytical cycle."
            )


def _validate_scenario_probabilities(value: ScenarioProbability) -> None:
    probabilities = {
        item.scenario: item.probability for item in value.distribution
    }
    if set(probabilities) != set(ScenarioIdentifier):
        raise DecisionValidationError(
            "Scenario probabilities must cover the complete scenario set."
        )
    if not probabilities or any(
        not isinstance(item, Decimal)
        or not item.is_finite()
        or not Decimal("0.000000") <= item <= Decimal("1.000000")
        for item in probabilities.values()
    ):
        raise DecisionValidationError("Scenario probabilities are invalid.")
    if sum(
        probabilities.values(),
        start=Decimal("0.000000"),
    ) != Decimal("1.000000"):
        raise DecisionValidationError(
            "Scenario probabilities must sum to 1.000000."
        )
    if value.primary_scenario not in probabilities:
        raise DecisionValidationError(
            "Scenario Probability must contain one primary scenario."
        )


def _select_decision(
    value: DecisionEngineInput,
) -> tuple[DecisionType, tuple[DecisionReasonCode, ...]]:
    quality = value.process_quality_assessment
    process = value.process_evidence
    hypothesis = value.hypothesis
    scenario = value.scenario_probability
    confidence = value.confidence_assessment

    if (
        quality.healthy_active_process.outcome is DiagnosticOutcome.INHIBITED
        or quality.loss_of_efficiency.outcome is DiagnosticOutcome.INHIBITED
    ):
        return _stay(DecisionReasonCode.UPSTREAM_INHIBITION)
    if any(
        item in _BLOCKING_UNCERTAINTY
        for item in (
            quality.uncertainty_level,
            process.uncertainty_level,
            hypothesis.uncertainty,
            scenario.uncertainty,
            confidence.uncertainty_level,
        )
    ):
        return _stay(DecisionReasonCode.BLOCKING_UNCERTAINTY)
    if (
        quality.healthy_active_process.missing_evidence
        or quality.loss_of_efficiency.missing_evidence
        or process.insufficiency_reasons
    ):
        return _stay(DecisionReasonCode.MISSING_REQUIRED_EVIDENCE)
    if confidence.final_confidence_level not in _CONFIDENCE_THRESHOLD:
        return _stay(DecisionReasonCode.CONFIDENCE_BELOW_THRESHOLD)
    if (
        process.contradicting_evidence
        or hypothesis.contradicting_evidence
        or scenario.contradicting_provenance
    ):
        return _stay(DecisionReasonCode.MIXED_EVIDENCE)
    if not _has_unique_primary_dominance(scenario):
        return _stay(DecisionReasonCode.MIXED_EVIDENCE)

    scenario_direction = _scenario_direction(
        scenario.primary_scenario,
        process.process_direction,
    )
    if scenario_direction is None:
        return _stay(DecisionReasonCode.ANALYTICAL_STATE_NOT_DIRECTIONAL)
    if (
        scenario_direction is ProcessDirection.UP
        and process.process_direction is not ProcessDirection.UP
    ) or (
        scenario_direction is ProcessDirection.DOWN
        and process.process_direction is not ProcessDirection.DOWN
    ):
        return _stay(DecisionReasonCode.SCENARIO_PROCESS_CONFLICT)

    label = hypothesis.hypothesis_label.strip().lower()
    if (
        scenario_direction is ProcessDirection.UP
        and process.current_process_state is ProcessState.CONTINUATION_ALIVE
        and quality.healthy_active_process.outcome is DiagnosticOutcome.SUPPORTED
        and quality.loss_of_efficiency.outcome is DiagnosticOutcome.NOT_ESTABLISHED
        and label in _LONG_HYPOTHESIS_LABELS
    ):
        return (
            DecisionType.LOOK_FOR_LONG,
            (
                DecisionReasonCode.BULLISH_SCENARIO_CONFIRMED,
                DecisionReasonCode.PROCESS_QUALITY_ALIGNED,
                DecisionReasonCode.HYPOTHESIS_ALIGNED,
                DecisionReasonCode.CONFIDENCE_THRESHOLD_MET,
            ),
        )
    if (
        scenario_direction is ProcessDirection.DOWN
        and process.current_process_state is ProcessState.WEAKENING
        and quality.loss_of_efficiency.outcome is DiagnosticOutcome.SUPPORTED
        and quality.healthy_active_process.outcome is DiagnosticOutcome.NOT_ESTABLISHED
        and label in _SHORT_HYPOTHESIS_LABELS
    ):
        return (
            DecisionType.LOOK_FOR_SHORT,
            (
                DecisionReasonCode.BEARISH_SCENARIO_CONFIRMED,
                DecisionReasonCode.PROCESS_QUALITY_ALIGNED,
                DecisionReasonCode.HYPOTHESIS_ALIGNED,
                DecisionReasonCode.CONFIDENCE_THRESHOLD_MET,
            ),
        )
    return _stay(DecisionReasonCode.SCENARIO_PROCESS_CONFLICT)


def _scenario_direction(
    primary_scenario: ScenarioIdentifier,
    process_direction: ProcessDirection,
) -> ProcessDirection | None:
    if primary_scenario in _BEARISH_SCENARIOS:
        return ProcessDirection.DOWN
    if (
        primary_scenario is ScenarioIdentifier.CONTINUATION_PERSISTS
        and process_direction in (
        ProcessDirection.UP,
        ProcessDirection.DOWN,
        )
    ):
        return process_direction
    return None


def _has_unique_primary_dominance(value: ScenarioProbability) -> bool:
    probabilities = {
        item.scenario: item.probability for item in value.distribution
    }
    primary = probabilities[value.primary_scenario]
    return all(
        primary > probability
        for scenario, probability in probabilities.items()
        if scenario is not value.primary_scenario
    )


def _stay(
    reason: DecisionReasonCode,
) -> tuple[DecisionType, tuple[DecisionReasonCode, ...]]:
    return DecisionType.STAY_OUT, (reason,)
