"""RuntimeEvent aggregate domain model."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from typing import Any, Optional

from pumpagent.runtime.domain.agent_state import AgentState
from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.confidence_assessment import ConfidenceAssessment
from pumpagent.runtime.domain.decision import DecisionAssessment
from pumpagent.runtime.domain.decision_alert import DecisionAlert
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.domain.hypothesis_package import HypothesisPackage
from pumpagent.runtime.domain.learning_metadata import LearningMetadata
from pumpagent.runtime.domain.market_efficiency_evidence import (
    MarketEfficiencyEvidence,
)
from pumpagent.runtime.domain.market_snapshot import MarketSnapshot
from pumpagent.runtime.domain.observation_package import ObservationPackage
from pumpagent.runtime.domain.process_evidence import ProcessEvidence
from pumpagent.runtime.domain.process_quality import (
    HealthyBaselineDesignation,
    HealthyBaselineReference,
    ProcessQualityAssessment,
    ProcessQualityAssessmentReference,
)
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
    episode_id: Optional[str] = None
    runtime_status: RuntimeStatus = RuntimeStatus.CREATED
    market_snapshot: Optional[MarketSnapshot] = None
    observation_package: Optional[ObservationPackage] = None
    structural_evidence: Optional[StructuralEvidence] = None
    market_efficiency_evidence: Optional[MarketEfficiencyEvidence] = None
    process_evidence: Optional[ProcessEvidence] = None
    process_quality_assessment: Optional[ProcessQualityAssessment] = None
    previous_process_quality_reference: Optional[ProcessQualityAssessmentReference] = None
    process_quality_history: tuple[ProcessQualityAssessment, ...] = ()
    healthy_baseline_reference: Optional[HealthyBaselineReference] = None
    healthy_baseline_designation: Optional[HealthyBaselineDesignation] = None
    hypothesis_package: Optional[HypothesisPackage] = None
    agent_state: Optional[AgentState] = None
    scenario_probability: Optional[ScenarioProbability] = None
    confidence_assessment: Optional[ConfidenceAssessment] = None
    decision_assessment: Optional[DecisionAssessment] = None
    decision_alert: Optional[DecisionAlert] = None
    learning_metadata: Optional[LearningMetadata] = None
    compatibility_context: Mapping[str, Any] = field(default_factory=dict)
    errors_or_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _validate_runtime_event(self)

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


def _validate_runtime_event(event: RuntimeEvent) -> None:
    for name in ("event_id", "schema_version", "symbol", "exchange", "timeframe"):
        value = getattr(event, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"RuntimeEvent.{name} must be a non-empty string.")
    if not isinstance(event.runtime_status, RuntimeStatus):
        raise ValueError("RuntimeEvent.runtime_status must be a RuntimeStatus.")
    if event.runtime_status is RuntimeStatus.COMPLETED:
        required = (
            "market_snapshot",
            "observation_package",
            "structural_evidence",
            "market_efficiency_evidence",
            "process_evidence",
            "process_quality_assessment",
            "hypothesis_package",
            "agent_state",
            "scenario_probability",
            "confidence_assessment",
            "decision_assessment",
        )
        missing = tuple(name for name in required if getattr(event, name) is None)
        if missing:
            raise ValueError(
                "Completed RuntimeEvent is missing canonical sections: "
                + ", ".join(missing)
            )
        if not isinstance(event.episode_id, str) or not event.episode_id.strip():
            raise ValueError("Completed RuntimeEvent requires episode_id.")
    if event.runtime_status in {RuntimeStatus.REJECTED, RuntimeStatus.FAILED}:
        if not event.errors_or_warnings:
            raise ValueError(
                "Rejected or failed RuntimeEvent requires errors_or_warnings."
            )
        if event.decision_assessment is not None:
            raise ValueError(
                "Rejected or failed RuntimeEvent cannot contain DecisionAssessment."
            )
