"""ConfidenceAssessment domain model."""

from __future__ import annotations

from dataclasses import dataclass

from pumpagent.runtime.domain.base import SerializableMixin
from pumpagent.runtime.domain.enums import ConfidenceLevel, UncertaintyLevel


@dataclass(frozen=True)
class ConfidenceAssessment(SerializableMixin):
    event_id: str
    final_confidence_level: ConfidenceLevel
    confidence_summary: str
    confidence_drivers: tuple[str, ...]
    confidence_reducers: tuple[str, ...]
    data_quality_impact: str
    contradiction_impact: str
    uncertainty_level: UncertaintyLevel
    schema_version: str = "1.0"
    numeric_confidence_score: float | None = None
    confidence_change_from_previous_event: str | None = None
    reliability_notes: str | None = None
    calibration_notes: str | None = None
    confidence_history_reference: str | None = None
