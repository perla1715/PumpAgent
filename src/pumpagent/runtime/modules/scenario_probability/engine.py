"""Scenario Probability Engine contract migration.

This checkpoint preserves the existing Agent-State policy selection while
constructing only the canonical Scenario Probability v1 domain contract. It
does not implement the frozen analytical qualification policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pumpagent.runtime.domain import (
    AgentState,
    HealthyBaselineReference,
    HypothesisPackage,
    ProcessEvidence,
    ProcessQualityAssessment,
    RuntimeEvent,
    ScenarioArtifactType,
    ScenarioAssessmentStatus,
    ScenarioIdentifier,
    ScenarioProbability,
    ScenarioProvenanceReference,
    ScenarioReasonCode,
    ScenarioWeight,
    canonical_process_evidence_id,
    canonical_scenario_probability_id,
)
from pumpagent.runtime.domain.enums import AgentStateType, UncertaintyLevel


@dataclass(frozen=True)
class _ScenarioPolicy:
    probabilities: tuple[Decimal, ...]
    primary: ScenarioIdentifier
    uncertainty: UncertaintyLevel
    reason_codes: tuple[ScenarioReasonCode, ...]


_OBSERVATION_POLICY = _ScenarioPolicy(
    probabilities=(
        Decimal("0.700000"),
        Decimal("0.100000"),
        Decimal("0.100000"),
        Decimal("0.050000"),
        Decimal("0.050000"),
    ),
    primary=ScenarioIdentifier.CONTINUE_OBSERVATION,
    uncertainty=UncertaintyLevel.HIGH,
    reason_codes=(
        ScenarioReasonCode.CONTINUE_OBSERVATION_FALLBACK,
    ),
)


SCENARIO_POLICIES = {
    AgentStateType.UNKNOWN: _OBSERVATION_POLICY,
    AgentStateType.CONTINUATION_ALIVE: _ScenarioPolicy(
        probabilities=(
            Decimal("0.100000"),
            Decimal("0.650000"),
            Decimal("0.150000"),
            Decimal("0.070000"),
            Decimal("0.030000"),
        ),
        primary=ScenarioIdentifier.CONTINUATION_PERSISTS,
        uncertainty=UncertaintyLevel.MEDIUM,
        reason_codes=(ScenarioReasonCode.PRIMARY_SCENARIO_QUALIFIED,),
    ),
    AgentStateType.CONTINUATION_SATURATION: _ScenarioPolicy(
        probabilities=(
            Decimal("0.150000"),
            Decimal("0.150000"),
            Decimal("0.550000"),
            Decimal("0.120000"),
            Decimal("0.030000"),
        ),
        primary=ScenarioIdentifier.SATURATION_PERSISTS,
        uncertainty=UncertaintyLevel.MEDIUM,
        reason_codes=(ScenarioReasonCode.PRIMARY_SCENARIO_QUALIFIED,),
    ),
    AgentStateType.FIRST_FAILURE_CANDIDATE: _ScenarioPolicy(
        probabilities=(
            Decimal("0.100000"),
            Decimal("0.080000"),
            Decimal("0.120000"),
            Decimal("0.650000"),
            Decimal("0.050000"),
        ),
        primary=ScenarioIdentifier.FAILURE_CANDIDATE_PERSISTS,
        uncertainty=UncertaintyLevel.MEDIUM,
        reason_codes=(ScenarioReasonCode.PRIMARY_SCENARIO_QUALIFIED,),
    ),
}


class ScenarioProbabilityError(ValueError):
    """Raised when Scenario Probability cannot construct a canonical package."""


def build_scenario_probability(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    process_evidence: ProcessEvidence,
    process_quality_assessment: ProcessQualityAssessment,
    *,
    healthy_baseline_reference: HealthyBaselineReference | None = None,
    previous_scenario_probability: ScenarioProbability | None = None,
    runtime_event_id: str | None = None,
    active_episode_id: str | None = None,
) -> ScenarioProbability:
    """Construct one canonical Scenario Probability from completed artifacts."""

    event_id = runtime_event_id or hypothesis.event_id
    _validate_inputs(
        hypothesis,
        agent_state,
        process_evidence,
        process_quality_assessment,
        healthy_baseline_reference=healthy_baseline_reference,
        previous_scenario_probability=previous_scenario_probability,
        runtime_event_id=event_id,
        active_episode_id=active_episode_id,
    )
    policy = SCENARIO_POLICIES.get(
        agent_state.current_state,
        _OBSERVATION_POLICY,
    )
    distribution = tuple(
        ScenarioWeight(scenario=scenario, probability=probability)
        for scenario, probability in zip(
            tuple(ScenarioIdentifier),
            policy.probabilities,
        )
    )
    provenance = _provenance(
        hypothesis,
        process_evidence,
        process_quality_assessment,
        healthy_baseline_reference=healthy_baseline_reference,
        previous_scenario_probability=previous_scenario_probability,
    )
    return ScenarioProbability(
        scenario_probability_id=canonical_scenario_probability_id(
            hypothesis.episode_id,
            event_id,
            hypothesis.hypothesis_id,
        ),
        episode_id=hypothesis.episode_id,
        runtime_event_id=event_id,
        observation_timestamp=process_evidence.observation_timestamp,
        created_at=process_evidence.observation_timestamp,
        source_process_evidence_id=canonical_process_evidence_id(
            hypothesis.episode_id,
            event_id,
        ),
        source_process_quality_assessment_id=(
            process_quality_assessment.assessment_id
        ),
        source_hypothesis_id=hypothesis.hypothesis_id,
        source_healthy_baseline_id=(
            healthy_baseline_reference.baseline_id
            if healthy_baseline_reference is not None
            else None
        ),
        previous_scenario_probability_id=(
            previous_scenario_probability.scenario_probability_id
            if previous_scenario_probability is not None
            else None
        ),
        hypothesis_semantic_code=hypothesis.semantic_code,
        status=ScenarioAssessmentStatus.COMPLETED,
        distribution=distribution,
        primary_scenario=policy.primary,
        uncertainty=policy.uncertainty,
        reason_codes=policy.reason_codes,
        supporting_provenance=provenance,
        contradicting_provenance=(),
        missing_prerequisites=(),
    )


def add_scenario_probability(
    event: RuntimeEvent,
    *,
    process_evidence: ProcessEvidence,
    process_quality_assessment: ProcessQualityAssessment,
    healthy_baseline_reference: HealthyBaselineReference | None = None,
    previous_scenario_probability: ScenarioProbability | None = None,
) -> RuntimeEvent:
    """Return a new event with only canonical Scenario Probability added."""

    if event.hypothesis_package is None:
        raise ScenarioProbabilityError(
            "RuntimeEvent.hypothesis_package is required."
        )
    if event.agent_state is None:
        raise ScenarioProbabilityError("RuntimeEvent.agent_state is required.")
    scenario_probability = build_scenario_probability(
        event.hypothesis_package,
        event.agent_state,
        process_evidence,
        process_quality_assessment,
        healthy_baseline_reference=healthy_baseline_reference,
        previous_scenario_probability=previous_scenario_probability,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(scenario_probability=scenario_probability)


def _validate_inputs(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    process_evidence: ProcessEvidence,
    process_quality_assessment: ProcessQualityAssessment,
    *,
    healthy_baseline_reference: HealthyBaselineReference | None,
    previous_scenario_probability: ScenarioProbability | None,
    runtime_event_id: str,
    active_episode_id: str | None,
) -> None:
    if not isinstance(hypothesis, HypothesisPackage):
        raise ScenarioProbabilityError("hypothesis must be a HypothesisPackage.")
    if not isinstance(agent_state, AgentState):
        raise ScenarioProbabilityError("agent_state must be an AgentState.")
    if not isinstance(process_evidence, ProcessEvidence):
        raise ScenarioProbabilityError(
            "process_evidence must be ProcessEvidence."
        )
    if not isinstance(process_quality_assessment, ProcessQualityAssessment):
        raise ScenarioProbabilityError(
            "process_quality_assessment must be ProcessQualityAssessment."
        )
    if hypothesis.event_id != runtime_event_id:
        raise ScenarioProbabilityError(
            "HypothesisPackage.event_id must match the RuntimeEvent.event_id."
        )
    if agent_state.event_id != runtime_event_id:
        raise ScenarioProbabilityError(
            "AgentState.event_id must match the RuntimeEvent.event_id."
        )
    if process_evidence.runtime_event_id != runtime_event_id:
        raise ScenarioProbabilityError(
            "ProcessEvidence.runtime_event_id must match the RuntimeEvent.event_id."
        )
    if process_quality_assessment.runtime_event_id != runtime_event_id:
        raise ScenarioProbabilityError(
            "ProcessQualityAssessment.runtime_event_id must match the "
            "RuntimeEvent.event_id."
        )
    episode_id = hypothesis.episode_id
    if process_evidence.episode_id != episode_id:
        raise ScenarioProbabilityError(
            "ProcessEvidence.episode_id must match HypothesisPackage.episode_id."
        )
    if process_quality_assessment.episode_id != episode_id:
        raise ScenarioProbabilityError(
            "ProcessQualityAssessment.episode_id must match "
            "HypothesisPackage.episode_id."
        )
    if active_episode_id is not None and active_episode_id != episode_id:
        raise ScenarioProbabilityError(
            "Active episode ID must match HypothesisPackage.episode_id."
        )
    if (
        process_quality_assessment.current_observation.observation_timestamp
        != process_evidence.observation_timestamp
    ):
        raise ScenarioProbabilityError(
            "Process and Process Quality observation timestamps must match."
        )
    if healthy_baseline_reference is not None:
        if not isinstance(healthy_baseline_reference, HealthyBaselineReference):
            raise ScenarioProbabilityError(
                "healthy_baseline_reference must be a HealthyBaselineReference."
            )
        if healthy_baseline_reference.episode_id != episode_id:
            raise ScenarioProbabilityError(
                "Healthy Baseline cannot cross Episode boundaries."
            )
        if (
            healthy_baseline_reference.source_assessment.observation
            .observation_timestamp
            >= process_evidence.observation_timestamp
        ):
            raise ScenarioProbabilityError(
                "Healthy Baseline must precede the current observation."
            )
    if previous_scenario_probability is not None:
        if not isinstance(previous_scenario_probability, ScenarioProbability):
            raise ScenarioProbabilityError(
                "previous_scenario_probability must be ScenarioProbability."
            )
        if previous_scenario_probability.episode_id != episode_id:
            raise ScenarioProbabilityError(
                "Previous Scenario Probability cannot cross Episode boundaries."
            )
        if (
            previous_scenario_probability.observation_timestamp
            >= process_evidence.observation_timestamp
        ):
            raise ScenarioProbabilityError(
                "Previous Scenario Probability must precede the current observation."
            )


def _provenance(
    hypothesis: HypothesisPackage,
    process_evidence: ProcessEvidence,
    process_quality_assessment: ProcessQualityAssessment,
    *,
    healthy_baseline_reference: HealthyBaselineReference | None,
    previous_scenario_probability: ScenarioProbability | None,
) -> tuple[ScenarioProvenanceReference, ...]:
    current = (
        ScenarioProvenanceReference(
            artifact_type=ScenarioArtifactType.PROCESS_EVIDENCE,
            artifact_id=canonical_process_evidence_id(
                process_evidence.episode_id,
                process_evidence.runtime_event_id,
            ),
            episode_id=process_evidence.episode_id,
            runtime_event_id=process_evidence.runtime_event_id,
            observation_timestamp=process_evidence.observation_timestamp,
        ),
        ScenarioProvenanceReference(
            artifact_type=ScenarioArtifactType.PROCESS_QUALITY,
            artifact_id=process_quality_assessment.assessment_id,
            episode_id=process_quality_assessment.episode_id,
            runtime_event_id=process_quality_assessment.runtime_event_id,
            observation_timestamp=(
                process_quality_assessment.current_observation
                .observation_timestamp
            ),
        ),
        ScenarioProvenanceReference(
            artifact_type=ScenarioArtifactType.HYPOTHESIS,
            artifact_id=hypothesis.hypothesis_id,
            episode_id=hypothesis.episode_id,
            runtime_event_id=hypothesis.event_id,
            observation_timestamp=process_evidence.observation_timestamp,
        ),
    )
    historical: list[ScenarioProvenanceReference] = []
    if healthy_baseline_reference is not None:
        source = healthy_baseline_reference.source_assessment
        historical.append(
            ScenarioProvenanceReference(
                artifact_type=ScenarioArtifactType.HEALTHY_BASELINE,
                artifact_id=healthy_baseline_reference.baseline_id,
                episode_id=healthy_baseline_reference.episode_id,
                runtime_event_id=source.runtime_event_id,
                observation_timestamp=(
                    source.observation.observation_timestamp
                ),
            )
        )
    if previous_scenario_probability is not None:
        historical.append(
            ScenarioProvenanceReference(
                artifact_type=(
                    ScenarioArtifactType.PREVIOUS_SCENARIO_PROBABILITY
                ),
                artifact_id=(
                    previous_scenario_probability.scenario_probability_id
                ),
                episode_id=previous_scenario_probability.episode_id,
                runtime_event_id=previous_scenario_probability.runtime_event_id,
                observation_timestamp=(
                    previous_scenario_probability.observation_timestamp
                ),
            )
        )
    return current + tuple(historical)
