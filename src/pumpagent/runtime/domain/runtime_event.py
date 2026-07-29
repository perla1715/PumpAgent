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
from pumpagent.runtime.domain.scenario_probability import (
    canonical_process_evidence_id,
)
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
        if self.runtime_status is RuntimeStatus.COMPLETED:
            changed = {
                name
                for name, value in normalized_sections.items()
                if value != getattr(self, name)
            }
            canonical_changes = changed - {
                "learning_metadata",
                "decision_alert",
            }
            if canonical_changes:
                raise ValueError(
                    "Completed RuntimeEvent canonical sections cannot be replaced: "
                    + ", ".join(sorted(canonical_changes))
                )
            if (
                "learning_metadata" in changed
                and self.learning_metadata is not None
            ):
                raise ValueError(
                    "Completed RuntimeEvent learning_metadata cannot be replaced."
                )
            if "decision_alert" in changed and self.decision_alert is not None:
                raise ValueError(
                    "Completed RuntimeEvent legacy decision_alert cannot be replaced."
                )
        return replace(self, **normalized_sections)

    def validate(self) -> None:
        """Re-authenticate this aggregate at public boundaries."""

        _validate_runtime_event(self)


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
        _validate_completed_identity(event)
    if event.runtime_status in {RuntimeStatus.REJECTED, RuntimeStatus.FAILED}:
        if not event.errors_or_warnings:
            raise ValueError(
                "Rejected or failed RuntimeEvent requires errors_or_warnings."
            )
        if event.decision_assessment is not None:
            raise ValueError(
                "Rejected or failed RuntimeEvent cannot contain DecisionAssessment."
            )


def _validate_completed_identity(event: RuntimeEvent) -> None:
    episode_id = event.episode_id
    event_id = event.event_id
    snapshot = event.market_snapshot
    observation = event.observation_package
    structure = event.structural_evidence
    market = event.market_efficiency_evidence
    process = event.process_evidence
    quality = event.process_quality_assessment
    hypothesis = event.hypothesis_package
    agent_state = event.agent_state
    scenario = event.scenario_probability
    confidence = event.confidence_assessment
    decision = event.decision_assessment

    if (
        snapshot.symbol != event.symbol
        or snapshot.exchange != event.exchange
        or snapshot.timeframe != event.timeframe
    ):
        raise ValueError("RuntimeEvent MarketSnapshot market identity must match.")
    if snapshot.timestamp != event.cycle_timestamp:
        raise ValueError("RuntimeEvent MarketSnapshot timestamp must match the cycle.")
    for name, section in (
        ("ObservationPackage", observation),
        ("StructuralEvidence", structure),
        ("MarketEfficiencyEvidence", market),
        ("HypothesisPackage", hypothesis),
        ("AgentState", agent_state),
        ("ConfidenceAssessment", confidence),
    ):
        if section.event_id != event_id:
            raise ValueError(f"RuntimeEvent {name} event identity must match.")
    if observation.previous_snapshot_reference != snapshot.event_id:
        raise ValueError(
            "RuntimeEvent ObservationPackage snapshot reference must match."
        )
    if (
        process.runtime_event_id != event_id
        or process.episode_id != episode_id
    ):
        raise ValueError("RuntimeEvent ProcessEvidence identity must match.")
    if (
        process.symbol != event.symbol
        or process.exchange != event.exchange
        or process.timeframe != event.timeframe
    ):
        raise ValueError("RuntimeEvent ProcessEvidence market identity must match.")
    if (
        quality.runtime_event_id != event_id
        or quality.episode_id != episode_id
    ):
        raise ValueError("RuntimeEvent ProcessQualityAssessment identity must match.")
    if hypothesis.episode_id != episode_id:
        raise ValueError("RuntimeEvent HypothesisPackage Episode identity must match.")
    if (
        scenario.runtime_event_id != event_id
        or scenario.episode_id != episode_id
        or scenario.source_hypothesis_id != hypothesis.hypothesis_id
        or scenario.source_process_evidence_id
        != canonical_process_evidence_id(episode_id, event_id)
        or scenario.source_process_quality_assessment_id
        != quality.assessment_id
    ):
        raise ValueError("RuntimeEvent ScenarioProbability provenance must match.")
    if (
        confidence.episode_id != episode_id
        or confidence.source_hypothesis_id != hypothesis.hypothesis_id
    ):
        raise ValueError("RuntimeEvent ConfidenceAssessment provenance must match.")
    if (
        decision.runtime_event_id != event_id
        or decision.episode_id != episode_id
        or decision.hypothesis_reference != hypothesis.hypothesis_id
        or decision.scenario_probability_reference
        != scenario.scenario_probability_id
        or decision.process_quality_reference != quality.to_reference()
        or decision.process_evidence_reference
        != canonical_process_evidence_id(episode_id, event_id)
        or decision.confidence_reference
        != f"confidence:{episode_id}:{event_id}"
    ):
        raise ValueError("RuntimeEvent DecisionAssessment provenance must match.")
    if (
        event.decision_alert is not None
        and event.decision_alert.event_id != event_id
    ):
        raise ValueError("RuntimeEvent legacy DecisionAlert identity must match.")
    if (
        event.learning_metadata is not None
        and event.learning_metadata.event_id != event_id
    ):
        raise ValueError("RuntimeEvent LearningMetadata identity must match.")
    _validate_quality_continuity(event)
    _validate_baseline_continuity(event)


def _validate_quality_continuity(event: RuntimeEvent) -> None:
    history = event.process_quality_history
    if not history or history[-1] != event.process_quality_assessment:
        raise ValueError(
            "RuntimeEvent Process Quality history must end with the current assessment."
        )
    if any(item.episode_id != event.episode_id for item in history):
        raise ValueError("RuntimeEvent Process Quality history cannot cross Episodes.")
    expected_previous = history[-2].to_reference() if len(history) > 1 else None
    if event.previous_process_quality_reference != expected_previous:
        raise ValueError(
            "RuntimeEvent previous Process Quality reference must match history."
        )


def _validate_baseline_continuity(event: RuntimeEvent) -> None:
    reference = event.healthy_baseline_reference
    designation = event.healthy_baseline_designation
    if (reference is None) != (designation is None):
        raise ValueError(
            "RuntimeEvent Healthy Baseline reference and designation must agree."
        )
    if reference is None:
        if event.scenario_probability.source_healthy_baseline_id is not None:
            raise ValueError(
                "RuntimeEvent ScenarioProbability baseline provenance must match."
            )
        return
    if (
        reference.episode_id != event.episode_id
        or designation.episode_id != event.episode_id
        or designation.to_reference() != reference
    ):
        raise ValueError("RuntimeEvent Healthy Baseline identity must match.")
    created_by_current_assessment = (
        reference.source_assessment
        == event.process_quality_assessment.to_reference()
    )
    expected_consumed_reference = (
        None if created_by_current_assessment else reference
    )
    expected_scenario_baseline_id = (
        None
        if expected_consumed_reference is None
        else expected_consumed_reference.baseline_id
    )
    if (
        event.scenario_probability.source_healthy_baseline_id
        != expected_scenario_baseline_id
        or event.decision_assessment.healthy_baseline_reference
        != expected_consumed_reference
    ):
        raise ValueError(
            "RuntimeEvent consumed Healthy Baseline provenance must match."
        )
