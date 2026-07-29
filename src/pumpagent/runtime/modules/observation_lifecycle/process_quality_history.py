"""Append-only Process Quality history for one Observation Episode.

This component authenticates already-created immutable Process Quality
contracts.  It does not classify a process, interpret evidence, select a
baseline, or produce downstream analytical conclusions.
"""

from __future__ import annotations

from dataclasses import dataclass

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import ObservationEpisodeStatus
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.domain.process_quality import (
    DiagnosticOutcome,
    HealthyBaselineDesignation,
    HealthyBaselineReference,
    ProcessQualityAssessment,
    ProcessQualityAssessmentReference,
    ProcessQualityConcept,
    ProcessQualityEvidenceReference,
    ProcessQualityLifecycleRelation,
    ProcessQualityObservationReference,
)


EPISODE_PROCESS_QUALITY_HISTORY_SCHEMA_VERSION = (
    "episode_process_quality_history_v2"
)


@dataclass(frozen=True)
class EpisodeProcessQualityHistory(SerializableMixin):
    """Immutable append-only accepted Process Quality history for one Episode."""

    episode_id: str
    assessments: tuple[ProcessQualityAssessment, ...] = ()
    baseline_designations: tuple[HealthyBaselineDesignation, ...] = ()
    lifecycle_relations: tuple[ProcessQualityLifecycleRelation, ...] = ()
    schema_version: str = EPISODE_PROCESS_QUALITY_HISTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _require_non_empty("episode_id", self.episode_id)
        _require_non_empty("schema_version", self.schema_version)
        _require_tuple_of("assessments", self.assessments, ProcessQualityAssessment)
        _require_tuple_of(
            "baseline_designations",
            self.baseline_designations,
            HealthyBaselineDesignation,
        )
        _require_tuple_of(
            "lifecycle_relations",
            self.lifecycle_relations,
            ProcessQualityLifecycleRelation,
        )
        _validate_stored_history(self)

    @property
    def applicable_baseline(self) -> HealthyBaselineDesignation | None:
        """Return the Episode's one immutable MVP designation."""

        return self.baseline_designations[0] if self.baseline_designations else None

    def resolve_assessment(
        self,
        reference: ProcessQualityAssessmentReference,
    ) -> ProcessQualityAssessment:
        """Resolve and authenticate an assessment reference."""

        if not isinstance(reference, ProcessQualityAssessmentReference):
            raise ValueError(
                "reference must be a ProcessQualityAssessmentReference."
            )
        _require_episode(self.episode_id, reference.episode_id)
        for assessment in self.assessments:
            if assessment.assessment_id == reference.assessment_id:
                if assessment.to_reference() != reference:
                    raise ValueError(
                        "Assessment reference does not match canonical stored coordinates."
                    )
                return assessment
        raise ValueError("Referenced Process Quality assessment does not exist.")

    def resolve_baseline(
        self,
        reference: HealthyBaselineReference,
    ) -> HealthyBaselineDesignation:
        """Resolve and authenticate a Healthy Baseline reference."""

        if not isinstance(reference, HealthyBaselineReference):
            raise ValueError("reference must be a HealthyBaselineReference.")
        _require_episode(self.episode_id, reference.episode_id)
        for designation in self.baseline_designations:
            if designation.baseline_id == reference.baseline_id:
                if designation.to_reference() != reference:
                    raise ValueError(
                        "Baseline reference does not match the canonical designation."
                    )
                return designation
        raise ValueError("Referenced Healthy Baseline designation does not exist.")

    def resolve_lifecycle_relation(
        self,
        relation_id: str,
    ) -> ProcessQualityLifecycleRelation:
        """Resolve one accepted lifecycle relation by immutable identity."""

        _require_non_empty("relation_id", relation_id)
        for relation in self.lifecycle_relations:
            if relation.relation_id == relation_id:
                return relation
        raise ValueError("Referenced Process Quality lifecycle relation does not exist.")

    def accept_assessment(
        self,
        episode: ObservationEpisode,
        assessment: ProcessQualityAssessment,
    ) -> EpisodeProcessQualityHistory:
        """Append one authenticated assessment to an active Episode history."""

        _require_active_episode(self.episode_id, episode)
        if not isinstance(assessment, ProcessQualityAssessment):
            raise ValueError("assessment must be a ProcessQualityAssessment.")
        _require_episode(self.episode_id, assessment.episode_id)
        _reject_repeated_assessment_coordinates(self.assessments, assessment)
        if (
            self.assessments
            and assessment.current_observation.observation_timestamp
            <= self.assessments[-1].current_observation.observation_timestamp
        ):
            raise ValueError(
                "Accepted Process Quality assessments must be appended in temporal order."
            )
        _authenticate_assessment_evidence(self.assessments, assessment)

        baseline = assessment.loss_of_efficiency.healthy_baseline_reference
        if baseline is not None:
            resolved = self.resolve_baseline(baseline)
            if resolved is not self.applicable_baseline:
                raise ValueError("Assessment must use the Episode's canonical baseline.")

        return EpisodeProcessQualityHistory(
            episode_id=self.episode_id,
            assessments=self.assessments + (assessment,),
            baseline_designations=self.baseline_designations,
            lifecycle_relations=self.lifecycle_relations,
        )

    def accept_baseline_designation(
        self,
        episode: ObservationEpisode,
        designation: HealthyBaselineDesignation,
    ) -> EpisodeProcessQualityHistory:
        """Accept the one authenticated MVP baseline designation."""

        _require_active_episode(self.episode_id, episode)
        if not isinstance(designation, HealthyBaselineDesignation):
            raise ValueError(
                "designation must be a HealthyBaselineDesignation."
            )
        _require_episode(self.episode_id, designation.episode_id)
        if any(
            stored.baseline_id == designation.baseline_id
            for stored in self.baseline_designations
        ):
            raise ValueError("Healthy Baseline identity is already accepted.")
        if self.baseline_designations:
            raise ValueError("MVP Healthy Baseline replacement is forbidden.")

        source = self.resolve_assessment(designation.source_assessment)
        effective_after = self.resolve_assessment(
            designation.effective_after_assessment
        )
        if not self.assessments or effective_after is not self.assessments[-1]:
            raise ValueError(
                "A baseline designation effective boundary must be the latest "
                "accepted assessment."
            )
        supported = tuple(
            assessment
            for assessment in self.assessments
            if assessment.healthy_active_process.outcome
            is DiagnosticOutcome.SUPPORTED
        )
        if source not in supported:
            raise ValueError(
                "Baseline source must be an accepted supported Healthy assessment."
            )

        if designation.predecessor_baseline is not None:
            raise ValueError("MVP Healthy Baseline replacement is forbidden.")
        if not supported or source is not supported[0]:
            raise ValueError(
                "The initial baseline must use the first accepted supported "
                "Healthy assessment."
            )

        return EpisodeProcessQualityHistory(
            episode_id=self.episode_id,
            assessments=self.assessments,
            baseline_designations=self.baseline_designations + (designation,),
            lifecycle_relations=self.lifecycle_relations,
        )

    def accept_lifecycle_relation(
        self,
        episode: ObservationEpisode,
        relation: ProcessQualityLifecycleRelation,
    ) -> EpisodeProcessQualityHistory:
        """Append a relation after authenticating both canonical assessments."""

        _require_active_episode(self.episode_id, episode)
        if not isinstance(relation, ProcessQualityLifecycleRelation):
            raise ValueError(
                "relation must be a ProcessQualityLifecycleRelation."
            )
        _require_episode(self.episode_id, relation.episode_id)
        if any(
            stored.relation_id == relation.relation_id
            for stored in self.lifecycle_relations
        ):
            raise ValueError("Lifecycle relation identity is already accepted.")

        self.resolve_assessment(relation.earlier_assessment)
        self.resolve_assessment(relation.later_assessment)
        _authenticate_evidence_references(
            self.assessments,
            relation.justification_evidence,
        )

        key = _relation_coordinate_key(relation)
        for stored in self.lifecycle_relations:
            if _relation_coordinate_key(stored) != key:
                continue
            if stored.relation_type is relation.relation_type:
                raise ValueError("Duplicate lifecycle relation is not appendable.")
            raise ValueError("Conflicting lifecycle relation is not appendable.")

        return EpisodeProcessQualityHistory(
            episode_id=self.episode_id,
            assessments=self.assessments,
            baseline_designations=self.baseline_designations,
            lifecycle_relations=self.lifecycle_relations + (relation,),
        )


def _validate_stored_history(history: EpisodeProcessQualityHistory) -> None:
    for values in (
        history.assessments,
        history.baseline_designations,
        history.lifecycle_relations,
    ):
        for value in values:
            _require_episode(history.episode_id, value.episode_id)

    _require_unique(
        "assessment identity",
        (value.assessment_id for value in history.assessments),
    )
    _require_unique(
        "baseline identity",
        (value.baseline_id for value in history.baseline_designations),
    )
    if len(history.baseline_designations) > 1:
        raise ValueError("MVP Healthy Baseline replacement is forbidden.")
    _require_unique(
        "lifecycle relation identity",
        (value.relation_id for value in history.lifecycle_relations),
    )

    accepted_assessments: tuple[ProcessQualityAssessment, ...] = ()
    for assessment in history.assessments:
        _reject_repeated_assessment_coordinates(accepted_assessments, assessment)
        if (
            accepted_assessments
            and assessment.current_observation.observation_timestamp
            <= accepted_assessments[-1].current_observation.observation_timestamp
        ):
            raise ValueError(
                "Stored Process Quality assessments must preserve append order."
            )
        _authenticate_assessment_evidence(accepted_assessments, assessment)
        accepted_assessments += (assessment,)

    supported = tuple(
        value
        for value in history.assessments
        if value.healthy_active_process.outcome is DiagnosticOutcome.SUPPORTED
    )
    for designation in history.baseline_designations:
        source = _resolve_assessment_value(
            history.assessments,
            designation.source_assessment,
        )
        effective_after = _resolve_assessment_value(
            history.assessments,
            designation.effective_after_assessment,
        )
        source_index = history.assessments.index(source)
        effective_index = history.assessments.index(effective_after)
        if effective_index < source_index:
            raise ValueError(
                "Stored baseline effective boundary cannot precede its source."
            )
        if source not in supported:
            raise ValueError(
                "Stored baseline source is not an accepted supported Healthy assessment."
            )
        if designation.predecessor_baseline is not None:
            raise ValueError("MVP Healthy Baseline replacement is forbidden.")
        if not supported or source is not supported[0]:
            raise ValueError(
                "Stored initial baseline must use the first supported Healthy assessment."
            )

    for assessment_index, assessment in enumerate(history.assessments):
        baseline = assessment.loss_of_efficiency.healthy_baseline_reference
        if baseline is None:
            continue
        resolved = _resolve_baseline_value(history.baseline_designations, baseline)
        applicable = tuple(
            designation
            for designation in history.baseline_designations
            if history.assessments.index(
                _resolve_assessment_value(
                    history.assessments,
                    designation.effective_after_assessment,
                )
            ) < assessment_index
        )
        if not applicable or resolved is not applicable[-1]:
            raise ValueError(
                "Stored assessment does not reference its applicable Healthy Baseline."
            )

    relation_keys: dict[
        tuple[str, ProcessQualityConcept, str, ProcessQualityConcept],
        ProcessQualityLifecycleRelation,
    ] = {}
    for relation in history.lifecycle_relations:
        _resolve_assessment_value(history.assessments, relation.earlier_assessment)
        _resolve_assessment_value(history.assessments, relation.later_assessment)
        _authenticate_evidence_references(
            history.assessments,
            relation.justification_evidence,
        )
        key = _relation_coordinate_key(relation)
        existing = relation_keys.get(key)
        if existing is not None:
            if existing.relation_type is relation.relation_type:
                raise ValueError("Stored lifecycle relations cannot be duplicated.")
            raise ValueError("Stored lifecycle relations cannot conflict.")
        relation_keys[key] = relation


def _reject_repeated_assessment_coordinates(
    assessments: tuple[ProcessQualityAssessment, ...],
    candidate: ProcessQualityAssessment,
) -> None:
    for stored in assessments:
        if stored.assessment_id == candidate.assessment_id:
            raise ValueError("Process Quality assessment identity is already accepted.")
        if (
            stored.current_observation.observation_id
            == candidate.current_observation.observation_id
            and stored.current_observation != candidate.current_observation
        ):
            raise ValueError("Observation identity has inconsistent coordinates.")
        if stored.runtime_event_id == candidate.runtime_event_id:
            if stored.current_observation != candidate.current_observation:
                raise ValueError("Runtime-event identity has inconsistent coordinates.")
            raise ValueError("Runtime-event identity is already accepted.")


def _authenticate_assessment_evidence(
    accepted: tuple[ProcessQualityAssessment, ...],
    candidate: ProcessQualityAssessment,
) -> None:
    references = tuple(
        reference
        for concept in (
            candidate.healthy_active_process,
            candidate.loss_of_efficiency,
        )
        for field_name in (
            "supporting_evidence",
            "contradicting_evidence",
            "missing_evidence",
            "inhibiting_evidence",
        )
        for reference in getattr(concept, field_name)
    )
    _authenticate_evidence_references(accepted + (candidate,), references)


def _authenticate_evidence_references(
    assessments: tuple[ProcessQualityAssessment, ...],
    references: tuple[ProcessQualityEvidenceReference, ...],
) -> None:
    for reference in references:
        _resolve_observation(assessments, reference.source_observation)


def _resolve_observation(
    assessments: tuple[ProcessQualityAssessment, ...],
    reference: ProcessQualityObservationReference,
) -> ProcessQualityObservationReference:
    for assessment in assessments:
        stored = assessment.current_observation
        if stored.observation_id == reference.observation_id:
            if stored != reference:
                raise ValueError(
                    "Observation reference has inconsistent canonical coordinates."
                )
            if assessment.runtime_event_id != reference.runtime_event_id:
                raise ValueError(
                    "Observation and Runtime-event coordinates are inconsistent."
                )
            return stored
    raise ValueError("Referenced Process Quality observation does not exist.")


def _resolve_assessment_value(
    assessments: tuple[ProcessQualityAssessment, ...],
    reference: ProcessQualityAssessmentReference,
) -> ProcessQualityAssessment:
    for assessment in assessments:
        if assessment.assessment_id == reference.assessment_id:
            if assessment.to_reference() != reference:
                raise ValueError(
                    "Stored assessment reference does not match canonical coordinates."
                )
            return assessment
    raise ValueError("Stored assessment reference target does not exist.")


def _resolve_baseline_value(
    designations: tuple[HealthyBaselineDesignation, ...],
    reference: HealthyBaselineReference,
) -> HealthyBaselineDesignation:
    for designation in designations:
        if designation.baseline_id == reference.baseline_id:
            if designation.to_reference() != reference:
                raise ValueError(
                    "Stored baseline reference does not match canonical coordinates."
                )
            return designation
    raise ValueError("Stored baseline reference target does not exist.")


def _relation_coordinate_key(
    relation: ProcessQualityLifecycleRelation,
) -> tuple[str, ProcessQualityConcept, str, ProcessQualityConcept]:
    return (
        relation.earlier_assessment.assessment_id,
        relation.earlier_concept,
        relation.later_assessment.assessment_id,
        relation.later_concept,
    )


def _require_active_episode(
    history_episode_id: str,
    episode: ObservationEpisode,
) -> None:
    if not isinstance(episode, ObservationEpisode):
        raise ValueError("episode must be an ObservationEpisode.")
    _require_episode(history_episode_id, episode.episode_id)
    if episode.status is not ObservationEpisodeStatus.ACTIVE:
        raise ValueError("Process Quality history cannot accept after Episode closure.")


def _require_episode(expected: str, actual: str) -> None:
    if actual != expected:
        raise ValueError("Process Quality history cannot cross Episode boundaries.")


def _require_unique(name: str, values: object) -> None:
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise ValueError(f"Stored {name} values must be unique.")


def _require_tuple_of(name: str, values: object, expected_type: type) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{name} must be a tuple.")
    if any(not isinstance(value, expected_type) for value in values):
        raise ValueError(f"{name} contains an invalid value.")


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
