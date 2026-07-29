"""Canonical Hypothesis domain contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import ConfidenceLevel, UncertaintyLevel


class HypothesisLifecycleStatus(str, Enum):
    """Evolution of an explanation, never evolution of the market itself."""

    CREATED = "created"
    UPDATED = "updated"
    WEAKENED = "weakened"
    REPLACED = "replaced"


class HypothesisSemanticCode(str, Enum):
    """Typed meaning of the current-condition explanation."""

    UNRESOLVED = "unresolved"
    CONTINUATION_EXPLANATION = "continuation_explanation"
    WEAKENING_EXPLANATION = "weakening_explanation"
    RECOVERY_EXPLANATION = "recovery_explanation"


@dataclass(frozen=True)
class HypothesisEvidenceReference(SerializableMixin):
    """Minimal traceable reference to canonical upstream evidence."""

    source_event_id: str
    source_section: str
    evidence_key: str
    description: str

    def __post_init__(self) -> None:
        for name in (
            "source_event_id",
            "source_section",
            "evidence_key",
            "description",
        ):
            _require_non_empty(name, getattr(self, name))


@dataclass(frozen=True)
class HypothesisPackage(SerializableMixin):
    """One episode-bound explanation and its explicit evolution metadata.

    CREATED packages have no predecessor. UPDATED and WEAKENED packages retain
    their hypothesis identity, while REPLACED packages receive a new identity.
    Every evidence reference belongs to the package's Runtime event. Explanation
    confidence is the Hypothesis Engine's 0-100 strength score; it is not the
    final Runtime ConfidenceAssessment.
    """

    event_id: str
    episode_id: str
    hypothesis_id: str
    hypothesis_label: str
    hypothesis_summary: str
    supporting_evidence: tuple[HypothesisEvidenceReference, ...]
    contradicting_evidence: tuple[HypothesisEvidenceReference, ...]
    explanation_confidence_score: int
    current_hypothesis_confidence_context: ConfidenceLevel
    reasoning_notes: str
    uncertainty: UncertaintyLevel
    semantic_code: HypothesisSemanticCode
    lifecycle_status: HypothesisLifecycleStatus
    previous_hypothesis_id: str | None
    previous_runtime_event_id: str | None
    hypothesis_change_reason: str
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in (
            "event_id",
            "episode_id",
            "hypothesis_id",
            "hypothesis_label",
            "hypothesis_summary",
            "reasoning_notes",
            "hypothesis_change_reason",
            "schema_version",
        ):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.current_hypothesis_confidence_context, ConfidenceLevel):
            raise ValueError(
                "current_hypothesis_confidence_context must be a ConfidenceLevel."
            )
        _validate_explanation_confidence(
            self.explanation_confidence_score,
            self.current_hypothesis_confidence_context,
        )
        if not isinstance(self.uncertainty, UncertaintyLevel):
            raise ValueError("uncertainty must be an UncertaintyLevel.")
        if not isinstance(self.semantic_code, HypothesisSemanticCode):
            raise ValueError("semantic_code must be a HypothesisSemanticCode.")
        if not isinstance(self.lifecycle_status, HypothesisLifecycleStatus):
            raise ValueError("lifecycle_status must be a HypothesisLifecycleStatus.")
        _validate_evidence(self)
        _validate_lifecycle(self)


def _validate_evidence(package: HypothesisPackage) -> None:
    seen: set[tuple[str, str, str]] = set()
    for collection_name in ("supporting_evidence", "contradicting_evidence"):
        collection = getattr(package, collection_name)
        for reference in collection:
            if not isinstance(reference, HypothesisEvidenceReference):
                raise ValueError(
                    f"{collection_name} must contain HypothesisEvidenceReference values."
                )
            if reference.source_event_id != package.event_id:
                raise ValueError(
                    "Hypothesis evidence must align with the Runtime event ID."
                )
            identity = (
                reference.source_event_id,
                reference.source_section,
                reference.evidence_key,
            )
            if identity in seen:
                raise ValueError("Hypothesis evidence references must be unique.")
            seen.add(identity)


def _validate_explanation_confidence(
    score: object,
    context: ConfidenceLevel,
) -> None:
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("explanation_confidence_score must be numeric.")
    if not isfinite(score):
        raise ValueError("explanation_confidence_score must be finite.")
    if not isinstance(score, int):
        raise ValueError("explanation_confidence_score must be an integer.")
    if not 0 <= score <= 100:
        raise ValueError("explanation_confidence_score must be between 0 and 100.")
    expected = _confidence_context_for_score(score)
    if context is not expected:
        raise ValueError(
            "current_hypothesis_confidence_context must match "
            "explanation_confidence_score."
        )


def _confidence_context_for_score(score: int) -> ConfidenceLevel:
    if score >= 80:
        return ConfidenceLevel.HIGH
    if score >= 50:
        return ConfidenceLevel.MEDIUM
    if score > 0:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.UNKNOWN


def _validate_lifecycle(package: HypothesisPackage) -> None:
    status = package.lifecycle_status
    previous_hypothesis_id = package.previous_hypothesis_id
    previous_runtime_event_id = package.previous_runtime_event_id

    if status is HypothesisLifecycleStatus.CREATED:
        if previous_hypothesis_id is not None or previous_runtime_event_id is not None:
            raise ValueError("CREATED cannot reference a previous hypothesis or event.")
        return

    _require_non_empty("previous_hypothesis_id", previous_hypothesis_id)
    _require_non_empty("previous_runtime_event_id", previous_runtime_event_id)
    if previous_runtime_event_id == package.event_id:
        raise ValueError("Previous Runtime event ID must differ from the current event ID.")

    if status in (
        HypothesisLifecycleStatus.UPDATED,
        HypothesisLifecycleStatus.WEAKENED,
    ):
        if previous_hypothesis_id != package.hypothesis_id:
            raise ValueError(f"{status.name} must retain the hypothesis ID.")
    elif previous_hypothesis_id == package.hypothesis_id:
        raise ValueError("REPLACED must receive a new hypothesis ID.")


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
