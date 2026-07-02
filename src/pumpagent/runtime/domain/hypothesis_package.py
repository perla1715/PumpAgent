"""HypothesisPackage domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import ConfidenceLevel, UncertaintyLevel


@dataclass(frozen=True)
class HypothesisPackage(SerializableMixin):
    event_id: str
    hypothesis_label: str
    hypothesis_summary: str
    supporting_evidence: tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    competing_hypotheses: tuple[dict[str, Any], ...]
    current_hypothesis_confidence_context: ConfidenceLevel
    reasoning_notes: str
    schema_version: str = "1.0"
    previous_hypothesis: str | None = None
    hypothesis_change_reason: str | None = None
    invalidated_hypotheses: tuple[str, ...] = ()
    historical_similarity_notes: str | None = None
    uncertainty: UncertaintyLevel = UncertaintyLevel.UNKNOWN
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
