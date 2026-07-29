"""ConfidenceAssessment domain model."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import ConfidenceLevel, UncertaintyLevel


@dataclass(frozen=True)
class ConfidenceAssessment(SerializableMixin):
    """Episode-bound final reliability of one complete analytical chain."""

    event_id: str
    episode_id: str
    source_hypothesis_id: str
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

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in (
            "event_id",
            "episode_id",
            "source_hypothesis_id",
            "confidence_summary",
            "data_quality_impact",
            "contradiction_impact",
            "schema_version",
        ):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.final_confidence_level, ConfidenceLevel):
            raise ValueError("final_confidence_level must be a ConfidenceLevel.")
        if not isinstance(self.uncertainty_level, UncertaintyLevel):
            raise ValueError("uncertainty_level must be an UncertaintyLevel.")
        _validate_entries("confidence_drivers", self.confidence_drivers)
        _validate_entries("confidence_reducers", self.confidence_reducers)
        _validate_optional_score(self.numeric_confidence_score)
        for name in (
            "confidence_change_from_previous_event",
            "reliability_notes",
            "calibration_notes",
            "confidence_history_reference",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_non_empty(name, value)


def _validate_entries(name: str, values: object) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{name} must be a tuple.")
    for value in values:
        _require_non_empty(name, value)
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique entries.")


def _validate_optional_score(value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("numeric_confidence_score must be numeric when present.")
    if not isfinite(value):
        raise ValueError("numeric_confidence_score must be finite when present.")
    if not 0.0 <= value <= 100.0:
        raise ValueError(
            "numeric_confidence_score must be between 0 and 100 when present."
        )


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
