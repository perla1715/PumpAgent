"""StructuralEvidence domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import EvidenceStrength, UncertaintyLevel


@dataclass(frozen=True)
class StructuralEvidence(SerializableMixin):
    event_id: str
    structure_summary: str
    trend_structure: str
    structural_bias: str
    key_levels: tuple[dict[str, Any], ...]
    structural_events: tuple[str, ...]
    evidence_strength: EvidenceStrength
    evidence_against: tuple[str, ...]
    uncertainty: UncertaintyLevel
    schema_version: str = "1.0"
    structural_score: float | None = None
    technical_context: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
