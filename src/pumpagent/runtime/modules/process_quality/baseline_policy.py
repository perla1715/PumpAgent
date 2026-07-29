"""Canonical single-baseline designation policy for one Observation Episode."""

from __future__ import annotations

from dataclasses import dataclass

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import DataQualityStatus, EvidenceStrength
from pumpagent.runtime.domain.process_evidence import ProcessEvidence, ProcessState
from pumpagent.runtime.domain.process_quality import (
    canonical_healthy_baseline_id,
    DiagnosticOutcome,
    HealthyBaselineDesignation,
    ProcessQualityAssessment,
)


HEALTHY_BASELINE_POLICY_INPUT_SCHEMA_VERSION = "healthy_baseline_policy_input_v2"


@dataclass(frozen=True)
class HealthyBaselineDesignationPolicyInput(SerializableMixin):
    current_assessment: ProcessQualityAssessment
    process_evidence: ProcessEvidence
    data_quality_status: DataQualityStatus
    previous_assessments: tuple[ProcessQualityAssessment, ...] = ()
    existing_designation: HealthyBaselineDesignation | None = None
    schema_version: str = HEALTHY_BASELINE_POLICY_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.current_assessment, ProcessQualityAssessment):
            raise ValueError(
                "current_assessment must be a ProcessQualityAssessment."
            )
        if not isinstance(self.process_evidence, ProcessEvidence):
            raise ValueError("process_evidence must be ProcessEvidence.")
        if not isinstance(self.data_quality_status, DataQualityStatus):
            raise ValueError("data_quality_status must be DataQualityStatus.")
        _validate_identity(self)
        _validate_previous_history(self)
        _validate_existing_designation(self)


def designate_healthy_baseline(
    value: HealthyBaselineDesignationPolicyInput,
) -> HealthyBaselineDesignation | None:
    """Return the one preserved baseline or a deterministic first designation.

    A newly returned designation is only a candidate until the controlled
    analytical-context commit succeeds.
    """
    if not isinstance(value, HealthyBaselineDesignationPolicyInput):
        raise ValueError(
            "value must be HealthyBaselineDesignationPolicyInput."
        )
    if value.existing_designation is not None:
        return value.existing_designation
    if not _eligible_for_initial_baseline(value):
        return None

    assessment = value.current_assessment
    reference = assessment.to_reference()
    return HealthyBaselineDesignation(
        baseline_id=canonical_healthy_baseline_id(
            assessment.episode_id,
            assessment.assessment_id,
        ),
        episode_id=assessment.episode_id,
        source_assessment=reference,
        effective_after_assessment=reference,
        creation_timestamp=assessment.current_observation.observation_timestamp,
        designation_reason=(
            "First successfully assessed Healthy Active Process with sufficient "
            "canonical Process Classification evidence."
        ),
    )


def _eligible_for_initial_baseline(
    value: HealthyBaselineDesignationPolicyInput,
) -> bool:
    # Canonical MVP eligibility contract:
    # - Healthy Active Process is the Process Quality interpretation of a
    #   CONTINUATION_ALIVE classification, not a second classification.
    # - UNKNOWN/WEAK classification evidence is insufficient; MODERATE/STRONG
    #   is sufficient.
    # - Process, Structure, and Market Efficiency provenance must all remain
    #   visible in the supported assessment.
    # - uncertainty is preserved on the assessment but is not an independent
    #   veto because it is derived from these already-gated inputs.
    assessment = value.current_assessment
    healthy = assessment.healthy_active_process
    process = value.process_evidence
    if value.data_quality_status is not DataQualityStatus.VALID:
        return False
    if process.current_process_state is not ProcessState.CONTINUATION_ALIVE:
        return False
    if process.evidence_strength in (
        EvidenceStrength.UNKNOWN,
        EvidenceStrength.WEAK,
    ):
        return False
    if healthy.outcome is not DiagnosticOutcome.SUPPORTED:
        return False
    if healthy.inhibiting_evidence:
        return False
    if not healthy.supporting_evidence:
        return False
    sections = {item.source_section for item in healthy.supporting_evidence}
    return {
        "process_evidence",
        "structural_evidence",
        "market_efficiency_evidence",
    }.issubset(sections)


def _validate_identity(value: HealthyBaselineDesignationPolicyInput) -> None:
    assessment = value.current_assessment
    process = value.process_evidence
    if assessment.episode_id != process.episode_id:
        raise ValueError(
            "Process Quality assessment and Process evidence must share an Episode."
        )
    if assessment.runtime_event_id != process.runtime_event_id:
        raise ValueError(
            "Process Quality assessment and Process evidence Runtime IDs must align."
        )
    if (
        assessment.current_observation.observation_timestamp
        != process.observation_timestamp
    ):
        raise ValueError(
            "Process Quality assessment and Process evidence timestamps must align."
        )


def _validate_previous_history(
    value: HealthyBaselineDesignationPolicyInput,
) -> None:
    current = value.current_assessment
    previous_timestamp = None
    identities: set[str] = set()
    for assessment in value.previous_assessments:
        if not isinstance(assessment, ProcessQualityAssessment):
            raise ValueError(
                "previous_assessments must contain ProcessQualityAssessment values."
            )
        if assessment.episode_id != current.episode_id:
            raise ValueError("Healthy Baseline history cannot cross Episode boundaries.")
        timestamp = assessment.current_observation.observation_timestamp
        if timestamp >= current.current_observation.observation_timestamp:
            raise ValueError("Healthy Baseline history must precede the current assessment.")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("Healthy Baseline history must be in strict temporal order.")
        if assessment.assessment_id in identities:
            raise ValueError("Healthy Baseline history identities must be unique.")
        identities.add(assessment.assessment_id)
        previous_timestamp = timestamp


def _validate_existing_designation(
    value: HealthyBaselineDesignationPolicyInput,
) -> None:
    designation = value.existing_designation
    if designation is None:
        return
    current = value.current_assessment
    if designation.episode_id != current.episode_id:
        raise ValueError("Healthy Baseline cannot cross Episode boundaries.")
    if designation.predecessor_baseline is not None:
        raise ValueError("MVP Healthy Baseline replacement is forbidden.")
    matching = tuple(
        assessment
        for assessment in value.previous_assessments
        if assessment.to_reference() == designation.source_assessment
    )
    if len(matching) != 1:
        raise ValueError(
            "Healthy Baseline must reference exactly one authenticated prior assessment."
        )
    source = matching[0]
    if source.healthy_active_process.outcome is not DiagnosticOutcome.SUPPORTED:
        raise ValueError("Healthy Baseline source is not a supported Healthy assessment.")
    if designation.effective_after_assessment != source.to_reference():
        raise ValueError("MVP Healthy Baseline effective identity is corrupted.")
