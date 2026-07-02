"""ScenarioProbability domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import UncertaintyLevel


@dataclass(frozen=True)
class ScenarioProbability(SerializableMixin):
    event_id: str
    scenario_set: tuple[str, ...]
    scenario_probabilities: dict[str, float]
    primary_scenario: str
    alternative_scenarios: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    uncertainty: UncertaintyLevel
    monitoring_focus: tuple[str, ...]
    schema_version: str = "1.0"
    historical_priors_reference: str | None = None
    scenario_change_reason: str | None = None
    rejected_scenarios: tuple[str, ...] = ()
    scenario_time_horizon: str | None = None
    scenario_notes: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
