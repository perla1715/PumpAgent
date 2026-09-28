"""Canonical immutable Process Quality domain contracts.

These contracts record Process Classification conclusions and their provenance.
They contain no classification, confidence, hypothesis, decision, alert, or
Observation Episode orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import UncertaintyLevel


PROCESS_QUALITY_ASSESSMENT_SCHEMA_VERSION = "process_quality_assessment_v1"
PROCESS_QUALITY_REFERENCE_SCHEMA_VERSION = "process_quality_reference_v1"
HEALTHY_BASELINE_DESIGNATION_SCHEMA_VERSION = "healthy_baseline_designation_v4"
PROCESS_QUALITY_LIFECYCLE_RELATION_SCHEMA_VERSION = (
    "process_quality_lifecycle_relation_v1"
)
_HEALTHY_BASELINE_EVIDENCE_KEY = "healthy_baseline"


def canonical_healthy_baseline_id(
    episode_id: str,
    assessment_id: str,
) -> str:
    """Return the only valid MVP identity for an Episode baseline."""

    _require_non_empty("episode_id", episode_id)
    _require_non_empty("assessment_id", assessment_id)
    return f"healthy-baseline:{episode_id}:{assessment_id}"


class DiagnosticOutcome(str, Enum):
    """Result of evaluating one concept at one observation."""

    SUPPORTED = "supported"
    NOT_ESTABLISHED = "not_established"
    INHIBITED = "inhibited"


class ProcessQualityConcept(str, Enum):
    """The two approved Process Quality concepts."""

    HEALTHY_ACTIVE_PROCESS = "healthy_active_process"
    LOSS_OF_EFFICIENCY = "loss_of_efficiency"


class ProcessQualityLifecycleRelationType(str, Enum):
    """Comparative meaning between two immutable Process Quality assessments."""

    CONTRADICTED = "contradicted"
    INVALIDATED = "invalidated"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class ProcessQualityObservationReference(SerializableMixin):
    """Immutable coordinates of one observation inside an Episode."""

    episode_id: str
    runtime_event_id: str
    observation_id: str
    observation_timestamp: datetime
    schema_version: str = PROCESS_QUALITY_REFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("episode_id", "runtime_event_id", "observation_id", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        _require_aware("observation_timestamp", self.observation_timestamp)


@dataclass(frozen=True)
class ProcessQualityEvidenceReference(SerializableMixin):
    """Traceable reference to evidence without copying the evidence payload."""

    source_observation: ProcessQualityObservationReference
    source_section: str
    evidence_key: str
    description: str
    schema_version: str = PROCESS_QUALITY_REFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.source_observation, ProcessQualityObservationReference):
            raise ValueError(
                "source_observation must be a ProcessQualityObservationReference."
            )
        for name in ("source_section", "evidence_key", "description", "schema_version"):
            _require_non_empty(name, getattr(self, name))


@dataclass(frozen=True)
class ProcessQualityAssessmentReference(SerializableMixin):
    """Immutable coordinates of one Process Quality Assessment."""

    assessment_id: str
    episode_id: str
    runtime_event_id: str
    observation: ProcessQualityObservationReference
    healthy_active_process_outcome: DiagnosticOutcome
    loss_of_efficiency_outcome: DiagnosticOutcome
    schema_version: str = PROCESS_QUALITY_REFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("assessment_id", "episode_id", "runtime_event_id", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.observation, ProcessQualityObservationReference):
            raise ValueError("observation must be a ProcessQualityObservationReference.")
        if self.observation.episode_id != self.episode_id:
            raise ValueError("Assessment and observation must belong to the same Episode.")
        if self.observation.runtime_event_id != self.runtime_event_id:
            raise ValueError("Assessment and observation Runtime event IDs must match.")
        if not isinstance(self.healthy_active_process_outcome, DiagnosticOutcome):
            raise ValueError(
                "healthy_active_process_outcome must be a DiagnosticOutcome."
            )
        if not isinstance(self.loss_of_efficiency_outcome, DiagnosticOutcome):
            raise ValueError("loss_of_efficiency_outcome must be a DiagnosticOutcome.")
        if (
            self.healthy_active_process_outcome is DiagnosticOutcome.SUPPORTED
            and self.loss_of_efficiency_outcome is DiagnosticOutcome.SUPPORTED
        ):
            raise ValueError(
                "A referenced assessment cannot support both Process Quality concepts."
            )


@dataclass(frozen=True)
class HealthyBaselineReference(SerializableMixin):
    """Reference to an accepted Healthy Baseline Designation."""

    baseline_id: str
    episode_id: str
    source_assessment: ProcessQualityAssessmentReference
    schema_version: str = PROCESS_QUALITY_REFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("baseline_id", "episode_id", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.source_assessment, ProcessQualityAssessmentReference):
            raise ValueError(
                "source_assessment must be a ProcessQualityAssessmentReference."
            )
        if self.source_assessment.episode_id != self.episode_id:
            raise ValueError("Baseline source must belong to the same Episode.")
        expected_baseline_id = canonical_healthy_baseline_id(
            self.episode_id,
            self.source_assessment.assessment_id,
        )
        if self.baseline_id != expected_baseline_id:
            raise ValueError(
                "Healthy Baseline reference identity does not match the canonical "
                "MVP formula."
            )
        if (
            self.source_assessment.healthy_active_process_outcome
            is not DiagnosticOutcome.SUPPORTED
        ):
            raise ValueError(
                "A Healthy Baseline must reference Healthy Active Process SUPPORTED."
            )


@dataclass(frozen=True)
class HealthyActiveProcessAssessment(SerializableMixin):
    """Observation-time evaluation of Healthy Active Process."""

    outcome: DiagnosticOutcome
    supporting_evidence: tuple[ProcessQualityEvidenceReference, ...]
    contradicting_evidence: tuple[ProcessQualityEvidenceReference, ...]
    missing_evidence: tuple[ProcessQualityEvidenceReference, ...]
    inhibiting_evidence: tuple[ProcessQualityEvidenceReference, ...]
    explanation_summary: str | None = None
    schema_version: str = PROCESS_QUALITY_ASSESSMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _validate_concept_assessment(self)


@dataclass(frozen=True)
class LossOfEfficiencyAssessment(SerializableMixin):
    """Observation-time evaluation of Loss of Efficiency."""

    outcome: DiagnosticOutcome
    healthy_baseline_reference: HealthyBaselineReference | None
    supporting_evidence: tuple[ProcessQualityEvidenceReference, ...]
    contradicting_evidence: tuple[ProcessQualityEvidenceReference, ...]
    missing_evidence: tuple[ProcessQualityEvidenceReference, ...]
    inhibiting_evidence: tuple[ProcessQualityEvidenceReference, ...]
    explanation_summary: str | None = None
    schema_version: str = PROCESS_QUALITY_ASSESSMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _validate_concept_assessment(self)
        if (
            self.healthy_baseline_reference is not None
            and not isinstance(self.healthy_baseline_reference, HealthyBaselineReference)
        ):
            raise ValueError(
                "healthy_baseline_reference must be a HealthyBaselineReference or None."
            )
        if self.outcome in (
            DiagnosticOutcome.SUPPORTED,
            DiagnosticOutcome.NOT_ESTABLISHED,
        ) and self.healthy_baseline_reference is None:
            raise ValueError(
                "A completed Loss of Efficiency evaluation requires a Healthy "
                "Baseline Reference."
            )
        if (
            self.outcome is DiagnosticOutcome.INHIBITED
            and self.healthy_baseline_reference is None
            and not any(
                reference.evidence_key == _HEALTHY_BASELINE_EVIDENCE_KEY
                for reference in self.missing_evidence
            )
        ):
            raise ValueError(
                "Loss of Efficiency INHIBITED without a baseline must explicitly "
                "identify the missing Healthy Baseline prerequisite."
            )


@dataclass(frozen=True)
class ProcessQualityAssessment(SerializableMixin):
    """Episode-bound Process Quality conclusions for one current observation."""

    assessment_id: str
    episode_id: str
    runtime_event_id: str
    current_observation: ProcessQualityObservationReference
    healthy_active_process: HealthyActiveProcessAssessment
    loss_of_efficiency: LossOfEfficiencyAssessment
    uncertainty_level: UncertaintyLevel = UncertaintyLevel.UNKNOWN
    schema_version: str = PROCESS_QUALITY_ASSESSMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("assessment_id", "episode_id", "runtime_event_id", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.current_observation, ProcessQualityObservationReference):
            raise ValueError(
                "current_observation must be a ProcessQualityObservationReference."
            )
        if self.current_observation.episode_id != self.episode_id:
            raise ValueError("Assessment and current observation must share an Episode.")
        if self.current_observation.runtime_event_id != self.runtime_event_id:
            raise ValueError(
                "Assessment and current observation Runtime event IDs must match."
            )
        if not isinstance(self.healthy_active_process, HealthyActiveProcessAssessment):
            raise ValueError(
                "healthy_active_process must be a HealthyActiveProcessAssessment."
            )
        if not isinstance(self.loss_of_efficiency, LossOfEfficiencyAssessment):
            raise ValueError(
                "loss_of_efficiency must be a LossOfEfficiencyAssessment."
            )
        if not isinstance(self.uncertainty_level, UncertaintyLevel):
            raise ValueError("uncertainty_level must be an UncertaintyLevel.")
        if (
            self.healthy_active_process.outcome is DiagnosticOutcome.SUPPORTED
            and self.loss_of_efficiency.outcome is DiagnosticOutcome.SUPPORTED
        ):
            raise ValueError(
                "Healthy Active Process and Loss of Efficiency cannot both be SUPPORTED."
            )
        _validate_assessment_evidence(self)
        baseline = self.loss_of_efficiency.healthy_baseline_reference
        if baseline is not None:
            if baseline.episode_id != self.episode_id:
                raise ValueError("Healthy Baseline must belong to the same Episode.")
            if (
                baseline.source_assessment.observation.observation_timestamp
                >= self.current_observation.observation_timestamp
            ):
                raise ValueError(
                    "Healthy Baseline observation must precede the current observation."
                )

    def to_reference(self) -> ProcessQualityAssessmentReference:
        return ProcessQualityAssessmentReference(
            assessment_id=self.assessment_id,
            episode_id=self.episode_id,
            runtime_event_id=self.runtime_event_id,
            observation=self.current_observation,
            healthy_active_process_outcome=self.healthy_active_process.outcome,
            loss_of_efficiency_outcome=self.loss_of_efficiency.outcome,
        )


@dataclass(frozen=True)
class HealthyBaselineDesignation(SerializableMixin):
    """Immutable designation of a supported Healthy assessment as baseline."""

    baseline_id: str
    episode_id: str
    source_assessment: ProcessQualityAssessmentReference
    effective_after_assessment: ProcessQualityAssessmentReference
    creation_timestamp: datetime
    designation_reason: str
    predecessor_baseline: HealthyBaselineReference | None = None
    schema_version: str = HEALTHY_BASELINE_DESIGNATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("baseline_id", "episode_id", "designation_reason", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.source_assessment, ProcessQualityAssessmentReference):
            raise ValueError(
                "source_assessment must be a ProcessQualityAssessmentReference."
            )
        if not isinstance(
            self.effective_after_assessment,
            ProcessQualityAssessmentReference,
        ):
            raise ValueError(
                "effective_after_assessment must be a "
                "ProcessQualityAssessmentReference."
            )
        _require_aware("creation_timestamp", self.creation_timestamp)
        if self.source_assessment.episode_id != self.episode_id:
            raise ValueError("Baseline designation must remain inside one Episode.")
        if self.effective_after_assessment.episode_id != self.episode_id:
            raise ValueError(
                "Baseline effective order must remain inside one Episode."
            )
        if (
            self.effective_after_assessment.observation.observation_timestamp
            < self.source_assessment.observation.observation_timestamp
        ):
            raise ValueError(
                "Baseline effective order cannot precede its source assessment."
            )
        if (
            self.creation_timestamp
            != self.source_assessment.observation.observation_timestamp
        ):
            raise ValueError(
                "Baseline creation timestamp must equal its source observation timestamp."
            )
        expected_baseline_id = canonical_healthy_baseline_id(
            self.episode_id,
            self.source_assessment.assessment_id,
        )
        if self.baseline_id != expected_baseline_id:
            raise ValueError(
                "Healthy Baseline identity does not match the canonical MVP formula."
            )
        if (
            self.source_assessment.healthy_active_process_outcome
            is not DiagnosticOutcome.SUPPORTED
        ):
            raise ValueError(
                "Baseline designation requires Healthy Active Process SUPPORTED."
            )
        if self.predecessor_baseline is not None:
            raise ValueError("MVP Healthy Baseline replacement is forbidden.")

    def to_reference(self) -> HealthyBaselineReference:
        return HealthyBaselineReference(
            baseline_id=self.baseline_id,
            episode_id=self.episode_id,
            source_assessment=self.source_assessment,
        )


@dataclass(frozen=True)
class ProcessQualityLifecycleRelation(SerializableMixin):
    """Immutable comparative relation between two Process Quality assessments."""

    relation_id: str
    episode_id: str
    relation_type: ProcessQualityLifecycleRelationType
    earlier_assessment: ProcessQualityAssessmentReference
    earlier_concept: ProcessQualityConcept
    earlier_outcome: DiagnosticOutcome
    later_assessment: ProcessQualityAssessmentReference
    later_concept: ProcessQualityConcept
    later_outcome: DiagnosticOutcome
    justification_evidence: tuple[ProcessQualityEvidenceReference, ...]
    relation_explanation: str
    schema_version: str = PROCESS_QUALITY_LIFECYCLE_RELATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("relation_id", "episode_id", "relation_explanation", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.relation_type, ProcessQualityLifecycleRelationType):
            raise ValueError(
                "relation_type must be a ProcessQualityLifecycleRelationType."
            )
        for name in ("earlier_assessment", "later_assessment"):
            if not isinstance(getattr(self, name), ProcessQualityAssessmentReference):
                raise ValueError(
                    f"{name} must be a ProcessQualityAssessmentReference."
                )
        if not isinstance(self.earlier_concept, ProcessQualityConcept):
            raise ValueError("earlier_concept must be a ProcessQualityConcept.")
        if not isinstance(self.later_concept, ProcessQualityConcept):
            raise ValueError("later_concept must be a ProcessQualityConcept.")
        if not isinstance(self.earlier_outcome, DiagnosticOutcome):
            raise ValueError("earlier_outcome must be a DiagnosticOutcome.")
        if not isinstance(self.later_outcome, DiagnosticOutcome):
            raise ValueError("later_outcome must be a DiagnosticOutcome.")
        if (
            self.earlier_assessment.episode_id != self.episode_id
            or self.later_assessment.episode_id != self.episode_id
        ):
            raise ValueError("Lifecycle relations cannot cross Episode boundaries.")
        if self.earlier_assessment.assessment_id == self.later_assessment.assessment_id:
            raise ValueError("Lifecycle relations require two distinct assessments.")
        if (
            self.earlier_assessment.observation.observation_timestamp
            >= self.later_assessment.observation.observation_timestamp
        ):
            raise ValueError(
                "Lifecycle relation later assessment must follow the earlier assessment."
            )
        if self.earlier_outcome is not DiagnosticOutcome.SUPPORTED:
            raise ValueError(
                "Lifecycle relations must qualify an earlier SUPPORTED diagnosis."
            )
        if self.earlier_outcome is not _referenced_outcome(
            self.earlier_assessment, self.earlier_concept
        ):
            raise ValueError(
                "earlier_outcome must match the referenced concept assessment."
            )
        if self.later_outcome is not _referenced_outcome(
            self.later_assessment, self.later_concept
        ):
            raise ValueError(
                "later_outcome must match the referenced concept assessment."
            )
        _validate_reference_tuple("justification_evidence", self.justification_evidence)
        if not self.justification_evidence:
            raise ValueError("Lifecycle relations require justification evidence.")
        for reference in self.justification_evidence:
            if reference.source_observation.episode_id != self.episode_id:
                raise ValueError(
                    "Lifecycle relation evidence cannot cross Episode boundaries."
                )
            if (
                reference.source_observation.observation_timestamp
                > self.later_assessment.observation.observation_timestamp
            ):
                raise ValueError("Lifecycle relation evidence cannot come from the future.")
        if self.relation_type is ProcessQualityLifecycleRelationType.RECOVERED:
            if not (
                self.earlier_concept is ProcessQualityConcept.LOSS_OF_EFFICIENCY
                and self.earlier_outcome is DiagnosticOutcome.SUPPORTED
                and self.later_concept is ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS
                and self.later_outcome is DiagnosticOutcome.SUPPORTED
            ):
                raise ValueError(
                    "RECOVERED requires earlier Loss of Efficiency SUPPORTED and "
                    "later Healthy Active Process SUPPORTED."
                )
        else:
            if self.earlier_concept is not self.later_concept:
                raise ValueError(
                    f"{self.relation_type.name} requires the same earlier and "
                    "later concept."
                )
            if self.later_outcome is not DiagnosticOutcome.NOT_ESTABLISHED:
                raise ValueError(
                    f"{self.relation_type.name} requires later NOT_ESTABLISHED."
                )


def _validate_concept_assessment(value: object) -> None:
    outcome = getattr(value, "outcome")
    if not isinstance(outcome, DiagnosticOutcome):
        raise ValueError("outcome must be a DiagnosticOutcome.")
    _require_non_empty("schema_version", getattr(value, "schema_version"))
    summary = getattr(value, "explanation_summary")
    if summary is not None:
        _require_non_empty("explanation_summary", summary)
    collections = (
        "supporting_evidence",
        "contradicting_evidence",
        "missing_evidence",
        "inhibiting_evidence",
    )
    for name in collections:
        _validate_reference_tuple(name, getattr(value, name))
    all_references = tuple(
        reference
        for name in collections
        for reference in getattr(value, name)
    )
    identities = [_evidence_identity(reference) for reference in all_references]
    if len(set(identities)) != len(identities):
        raise ValueError(
            "Evidence references must be unique across diagnostic relationships."
        )
    if outcome is DiagnosticOutcome.SUPPORTED and not getattr(value, "supporting_evidence"):
        raise ValueError("SUPPORTED requires supporting evidence.")
    if outcome is DiagnosticOutcome.INHIBITED and not (
        getattr(value, "inhibiting_evidence") or getattr(value, "missing_evidence")
    ):
        raise ValueError("INHIBITED requires missing or inhibiting evidence.")
    if not all_references:
        raise ValueError("A concept assessment requires structured evidence references.")


def _referenced_outcome(
    assessment: ProcessQualityAssessmentReference,
    concept: ProcessQualityConcept,
) -> DiagnosticOutcome:
    if concept is ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS:
        return assessment.healthy_active_process_outcome
    return assessment.loss_of_efficiency_outcome


def _validate_assessment_evidence(assessment: ProcessQualityAssessment) -> None:
    for concept in (
        assessment.healthy_active_process,
        assessment.loss_of_efficiency,
    ):
        for name in (
            "supporting_evidence",
            "contradicting_evidence",
            "missing_evidence",
            "inhibiting_evidence",
        ):
            for reference in getattr(concept, name):
                source = reference.source_observation
                if source.episode_id != assessment.episode_id:
                    raise ValueError(
                        "Process Quality evidence cannot cross Episode boundaries."
                    )
                if source.observation_timestamp > assessment.current_observation.observation_timestamp:
                    raise ValueError("Process Quality evidence cannot come from the future.")


def _validate_reference_tuple(name: str, values: object) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{name} must be a tuple.")
    if any(not isinstance(value, ProcessQualityEvidenceReference) for value in values):
        raise ValueError(
            f"{name} must contain ProcessQualityEvidenceReference values."
        )


def _evidence_identity(reference: ProcessQualityEvidenceReference) -> tuple[str, ...]:
    source = reference.source_observation
    return (
        source.episode_id,
        source.runtime_event_id,
        source.observation_id,
        reference.source_section,
        reference.evidence_key,
    )


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _require_aware(name: str, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
