"""Process Quality assessment from canonical Process Classification evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import DataQualityStatus, UncertaintyLevel
from pumpagent.runtime.domain.market_efficiency_evidence import MarketEfficiencyEvidence
from pumpagent.runtime.domain.process_evidence import (
    ProcessEvidence,
    ProcessEvidenceAvailability,
    ProcessEvidenceItem,
    ProcessState,
)
from pumpagent.runtime.domain.process_quality import (
    canonical_healthy_baseline_id,
    DiagnosticOutcome,
    HealthyActiveProcessAssessment,
    HealthyBaselineReference,
    LossOfEfficiencyAssessment,
    ProcessQualityAssessment,
    ProcessQualityEvidenceReference,
    ProcessQualityObservationReference,
)
from pumpagent.runtime.domain.structural_evidence import StructuralEvidence


PROCESS_QUALITY_INPUT_SCHEMA_VERSION = "process_quality_assessment_input_v1"


@dataclass(frozen=True)
class ProcessQualityAssessmentInput(SerializableMixin):
    process_evidence: ProcessEvidence
    structural_evidence: StructuralEvidence
    market_efficiency_evidence: MarketEfficiencyEvidence
    data_quality_status: DataQualityStatus
    previous_assessments: tuple[ProcessQualityAssessment, ...] = ()
    healthy_baseline: HealthyBaselineReference | None = None
    schema_version: str = PROCESS_QUALITY_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.process_evidence, ProcessEvidence):
            raise ValueError("process_evidence must be ProcessEvidence.")
        if not isinstance(self.structural_evidence, StructuralEvidence):
            raise ValueError("structural_evidence must be StructuralEvidence.")
        if not isinstance(self.market_efficiency_evidence, MarketEfficiencyEvidence):
            raise ValueError(
                "market_efficiency_evidence must be MarketEfficiencyEvidence."
            )
        if not isinstance(self.data_quality_status, DataQualityStatus):
            raise ValueError("data_quality_status must be DataQualityStatus.")
        if (
            self.structural_evidence.event_id
            != self.process_evidence.runtime_event_id
            or self.market_efficiency_evidence.event_id
            != self.process_evidence.runtime_event_id
        ):
            raise ValueError("Process Quality source Runtime event IDs must align.")
        _validate_history(self)


def build_process_quality_assessment(
    value: ProcessQualityAssessmentInput,
) -> ProcessQualityAssessment:
    """Build exactly one immutable assessment without reclassifying the market."""
    if not isinstance(value, ProcessQualityAssessmentInput):
        raise ValueError("value must be ProcessQualityAssessmentInput.")

    process = value.process_evidence
    observation = ProcessQualityObservationReference(
        episode_id=process.episode_id,
        runtime_event_id=process.runtime_event_id,
        observation_id=f"process-quality-observation:{process.runtime_event_id}",
        observation_timestamp=process.observation_timestamp,
    )
    available = tuple(
        _process_reference(observation, item)
        for item in (
            process.supporting_evidence
            + process.contradicting_evidence
            + process.neutral_evidence
        )
        if item.availability_status is ProcessEvidenceAvailability.AVAILABLE
    )
    missing = tuple(
        _process_reference(observation, item)
        for item in process.neutral_evidence
        if item.availability_status is ProcessEvidenceAvailability.UNAVAILABLE
    )
    structure_reference = _reference(
        observation,
        source_section="structural_evidence",
        evidence_key="structure_analysis",
        description=value.structural_evidence.structure_summary,
    )
    market_reference = _reference(
        observation,
        source_section="market_efficiency_evidence",
        evidence_key="market_efficiency_analysis",
        description=value.market_efficiency_evidence.efficiency_summary,
    )
    quality_reference = _reference(
        observation,
        source_section="process_evidence",
        evidence_key="data_quality",
        description=f"Runtime data quality is {value.data_quality_status.value}.",
    )

    healthy = _healthy_assessment(
        value,
        available=available + (structure_reference, market_reference, quality_reference),
        missing=missing,
    )
    loss = _loss_assessment(
        value,
        observation=observation,
        available=available + (structure_reference, market_reference),
        missing=missing,
    )
    return ProcessQualityAssessment(
        assessment_id=(
            f"process-quality-assessment:{process.episode_id}:"
            f"{process.runtime_event_id}"
        ),
        episode_id=process.episode_id,
        runtime_event_id=process.runtime_event_id,
        current_observation=observation,
        healthy_active_process=healthy,
        loss_of_efficiency=loss,
        uncertainty_level=_uncertainty(value),
    )


def _healthy_assessment(
    value: ProcessQualityAssessmentInput,
    *,
    available: tuple[ProcessQualityEvidenceReference, ...],
    missing: tuple[ProcessQualityEvidenceReference, ...],
) -> HealthyActiveProcessAssessment:
    process = value.process_evidence
    if (
        value.data_quality_status is not DataQualityStatus.VALID
        or process.current_process_state is ProcessState.UNKNOWN
    ):
        inhibiting = (
            ()
            if missing
            else (
                _reference(
                    available[0].source_observation,
                    source_section="process_evidence",
                    evidence_key="process_classification",
                    description="Process Classification is UNKNOWN.",
                ),
            )
        )
        return HealthyActiveProcessAssessment(
            outcome=DiagnosticOutcome.INHIBITED,
            supporting_evidence=(),
            contradicting_evidence=(),
            missing_evidence=missing,
            inhibiting_evidence=inhibiting,
            explanation_summary=(
                "Healthy Active Process cannot be established from the current "
                "Process Classification and data quality."
            ),
        )
    if process.current_process_state is ProcessState.CONTINUATION_ALIVE:
        return HealthyActiveProcessAssessment(
            outcome=DiagnosticOutcome.SUPPORTED,
            supporting_evidence=available,
            contradicting_evidence=(),
            missing_evidence=missing,
            inhibiting_evidence=(),
            explanation_summary=(
                "Canonical Process Classification supports a healthy active process."
            ),
        )
    return HealthyActiveProcessAssessment(
        outcome=DiagnosticOutcome.NOT_ESTABLISHED,
        supporting_evidence=(),
        contradicting_evidence=available,
        missing_evidence=missing,
        inhibiting_evidence=(),
        explanation_summary=(
            "Canonical Process Classification does not support a healthy active process."
        ),
    )


def _loss_assessment(
    value: ProcessQualityAssessmentInput,
    *,
    observation: ProcessQualityObservationReference,
    available: tuple[ProcessQualityEvidenceReference, ...],
    missing: tuple[ProcessQualityEvidenceReference, ...],
) -> LossOfEfficiencyAssessment:
    baseline = value.healthy_baseline
    if baseline is None:
        baseline_missing = _reference(
            observation,
            source_section="process_quality_history",
            evidence_key="healthy_baseline",
            description="No authenticated Healthy Baseline is available.",
        )
        return LossOfEfficiencyAssessment(
            outcome=DiagnosticOutcome.INHIBITED,
            healthy_baseline_reference=None,
            supporting_evidence=(),
            contradicting_evidence=(),
            missing_evidence=missing + (baseline_missing,),
            inhibiting_evidence=(),
            explanation_summary=(
                "Loss of Efficiency requires a prior authenticated Healthy Baseline."
            ),
        )
    if (
        value.data_quality_status is not DataQualityStatus.VALID
        or value.process_evidence.current_process_state is ProcessState.UNKNOWN
    ):
        inhibited = _reference(
            observation,
            source_section="process_evidence",
            evidence_key="comparison_inhibited",
            description="Current Process Classification cannot support comparison.",
        )
        return LossOfEfficiencyAssessment(
            outcome=DiagnosticOutcome.INHIBITED,
            healthy_baseline_reference=baseline,
            supporting_evidence=(),
            contradicting_evidence=(),
            missing_evidence=missing,
            inhibiting_evidence=(inhibited,),
            explanation_summary="Loss-of-Efficiency comparison is inhibited.",
        )
    if value.process_evidence.current_process_state is ProcessState.WEAKENING:
        return LossOfEfficiencyAssessment(
            outcome=DiagnosticOutcome.SUPPORTED,
            healthy_baseline_reference=baseline,
            supporting_evidence=available,
            contradicting_evidence=(),
            missing_evidence=missing,
            inhibiting_evidence=(),
            explanation_summary=(
                "Current weakening is established relative to the authenticated "
                "Healthy Baseline."
            ),
        )
    return LossOfEfficiencyAssessment(
        outcome=DiagnosticOutcome.NOT_ESTABLISHED,
        healthy_baseline_reference=baseline,
        supporting_evidence=(),
        contradicting_evidence=available,
        missing_evidence=missing,
        inhibiting_evidence=(),
        explanation_summary=(
            "Current Process Classification does not establish loss of efficiency."
        ),
    )


def _validate_history(value: ProcessQualityAssessmentInput) -> None:
    process = value.process_evidence
    previous_timestamp: datetime | None = None
    assessment_ids: set[str] = set()
    for assessment in value.previous_assessments:
        if not isinstance(assessment, ProcessQualityAssessment):
            raise ValueError(
                "previous_assessments must contain ProcessQualityAssessment values."
            )
        if assessment.episode_id != process.episode_id:
            raise ValueError("Process Quality history cannot cross Episode boundaries.")
        if assessment.current_observation.observation_timestamp >= process.observation_timestamp:
            raise ValueError("Previous Process Quality history must precede the current observation.")
        if previous_timestamp is not None and (
            assessment.current_observation.observation_timestamp <= previous_timestamp
        ):
            raise ValueError("Process Quality history must be in strict temporal order.")
        if assessment.assessment_id in assessment_ids:
            raise ValueError("Process Quality history identities must be unique.")
        assessment_ids.add(assessment.assessment_id)
        previous_timestamp = assessment.current_observation.observation_timestamp
    baseline = value.healthy_baseline
    if baseline is None:
        return
    if baseline.episode_id != process.episode_id:
        raise ValueError("Healthy Baseline cannot cross Episode boundaries.")
    expected_baseline_id = canonical_healthy_baseline_id(
        baseline.episode_id,
        baseline.source_assessment.assessment_id,
    )
    if baseline.baseline_id != expected_baseline_id:
        raise ValueError(
            "Healthy Baseline reference identity does not match the canonical "
            "MVP formula."
        )
    if not any(
        assessment.to_reference() == baseline.source_assessment
        for assessment in value.previous_assessments
    ):
        raise ValueError(
            "Healthy Baseline must reference an authenticated previous assessment."
        )


def _process_reference(
    observation: ProcessQualityObservationReference,
    item: ProcessEvidenceItem,
) -> ProcessQualityEvidenceReference:
    return _reference(
        observation,
        source_section="process_evidence",
        evidence_key=item.evidence_key,
        description=f"{item.description} Source: {item.source_module}.{item.source_field}.",
    )


def _reference(
    observation: ProcessQualityObservationReference,
    *,
    source_section: str,
    evidence_key: str,
    description: str,
) -> ProcessQualityEvidenceReference:
    return ProcessQualityEvidenceReference(
        source_observation=observation,
        source_section=source_section,
        evidence_key=evidence_key,
        description=description or "Evidence was produced without a summary.",
    )


def _uncertainty(value: ProcessQualityAssessmentInput) -> UncertaintyLevel:
    levels = (
        value.process_evidence.uncertainty_level,
        value.structural_evidence.uncertainty,
        value.market_efficiency_evidence.uncertainty,
    )
    if value.data_quality_status is not DataQualityStatus.VALID:
        return UncertaintyLevel.HIGH
    rank = {
        UncertaintyLevel.UNKNOWN: 3,
        UncertaintyLevel.HIGH: 3,
        UncertaintyLevel.MEDIUM: 2,
        UncertaintyLevel.LOW: 1,
    }
    return max(levels, key=rank.__getitem__)
