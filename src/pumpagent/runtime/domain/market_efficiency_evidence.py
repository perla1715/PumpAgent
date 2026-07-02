"""MarketEfficiencyEvidence domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import EvidenceStrength, UncertaintyLevel


@dataclass(frozen=True)
class MarketEfficiencyEvidence(SerializableMixin):
    event_id: str
    participation_summary: str
    participation_direction: str
    efficiency_summary: str
    efficiency_status: str
    supporting_evidence: tuple[str, ...]
    evidence_against: tuple[str, ...]
    evidence_strength: EvidenceStrength
    uncertainty: UncertaintyLevel
    schema_version: str = "1.0"
    participation_score: float | None = None
    market_mechanics_context: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
