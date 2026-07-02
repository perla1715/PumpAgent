"""RuntimeEvent aggregate domain model."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any

from pumpagent.runtime.domain.agent_state import AgentState
from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.confidence_assessment import ConfidenceAssessment
from pumpagent.runtime.domain.decision_alert import DecisionAlert
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.domain.hypothesis_package import HypothesisPackage
from pumpagent.runtime.domain.learning_metadata import LearningMetadata
from pumpagent.runtime.domain.market_efficiency_evidence import (
    MarketEfficiencyEvidence,
)
from pumpagent.runtime.domain.market_snapshot import MarketSnapshot
from pumpagent.runtime.domain.observation_package import ObservationPackage
from pumpagent.runtime.domain.scenario_probability import ScenarioProbability
from pumpagent.runtime.domain.structural_evidence import StructuralEvidence


@dataclass(frozen=True)
class RuntimeEvent(SerializableMixin):
    """One immutable reasoning-cycle aggregate.

    Runtime modules should not mutate an existing event. Each module should
    return a new RuntimeEvent instance with its owned section added.
    """

    event_id: str
    schema_version: str
    cycle_timestamp: datetime
    symbol: str
    exchange: str
    timeframe: str
    runtime_status: RuntimeStatus = RuntimeStatus.CREATED
    market_snapshot: MarketSnapshot | None = None
    observation_package: ObservationPackage | None = None
    structural_evidence: StructuralEvidence | None = None
    market_efficiency_evidence: MarketEfficiencyEvidence | None = None
    hypothesis_package: HypothesisPackage | None = None
    agent_state: AgentState | None = None
    scenario_probability: ScenarioProbability | None = None
    confidence_assessment: ConfidenceAssessment | None = None
    decision_alert: DecisionAlert | None = None
    learning_metadata: LearningMetadata | None = None
    errors_or_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)

    def with_sections(
        self,
        *section_updates: Mapping[str, object] | Iterable[tuple[str, object]],
        **sections: object,
    ) -> "RuntimeEvent":
        """Return a new event with validated section fields replaced."""

        normalized_sections = _normalize_section_updates(section_updates, sections)
        return replace(self, **normalized_sections)


def _normalize_section_updates(
    section_updates: tuple[Mapping[str, object] | Iterable[tuple[str, object]], ...],
    sections: dict[str, object],
) -> dict[str, object]:
    valid_fields = {field.name for field in fields(RuntimeEvent)}
    normalized: dict[str, object] = {}

    for update in section_updates:
        items: Iterable[tuple[str, object]]
        if isinstance(update, Mapping):
            items = update.items()
        else:
            items = update

        for name, value in items:
            _add_section_update(normalized, valid_fields, name, value)

    for name, value in sections.items():
        _add_section_update(normalized, valid_fields, name, value)

    return normalized


def _add_section_update(
    normalized: dict[str, object],
    valid_fields: set[str],
    name: Any,
    value: object,
) -> None:
    if not isinstance(name, str):
        raise ValueError("RuntimeEvent field names must be strings.")

    if name not in valid_fields:
        raise ValueError(f"Unknown RuntimeEvent field: {name}")

    if name in normalized:
        raise ValueError(f"Duplicate RuntimeEvent section update: {name}")

    normalized[name] = value
