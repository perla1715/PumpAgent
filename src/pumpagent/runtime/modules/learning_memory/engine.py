"""Learning Memory v0.1.

Learning Memory prepares a completed RuntimeEvent for future storage and human
review. It does not persist files, trigger Research Agent, learn
automatically, or modify Runtime behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pumpagent.runtime.domain import LearningMetadata, RuntimeEvent
from pumpagent.runtime.domain.enums import ReviewStatus


class LearningMemoryError(ValueError):
    """Raised when Learning Memory cannot prepare metadata."""


class LearningMemoryExportCategory(str, Enum):
    """Readiness categories for the standalone Learning Memory boundary."""

    CASE_READY = "case_ready"
    REVIEW_ONLY = "review_only"
    REJECTED = "rejected"


REQUIRED_EXPORT_EVENT_SECTIONS = (
    "market_snapshot",
    "structural_evidence",
    "market_efficiency_evidence",
    "hypothesis_package",
    "agent_state",
    "confidence_assessment",
    "decision_alert",
)

RUNTIME_OWNED_EVENT_ID_SECTIONS = (
    "structural_evidence",
    "market_efficiency_evidence",
    "hypothesis_package",
    "agent_state",
    "confidence_assessment",
    "decision_alert",
)


def build_learning_metadata(
    event: RuntimeEvent,
    *,
    created_at: datetime | None = None,
) -> LearningMetadata:
    """Build storage/review metadata without persistence or learning side effects."""

    category = classify_runtime_event(event)
    timestamp = created_at or datetime.now(timezone.utc)

    return LearningMetadata(
        event_id=event.event_id,
        case_id=_case_id(event),
        should_store=category is LearningMemoryExportCategory.CASE_READY,
        storage_reason=_storage_reason(event, category),
        review_status=ReviewStatus.PENDING,
        created_at=timestamp,
        schema_version=event.schema_version,
        outcome_pending=True,
        outcome_summary=None,
        human_annotation=None,
        research_tags=(),
        similarity_tags=(),
        lesson_learned=None,
        follow_up_event_id=None,
        linked_cases=(),
        reviewed_by=None,
        review_timestamp=None,
    )


def add_learning_metadata(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only learning_metadata added."""

    metadata = build_learning_metadata(event)
    return event.with_sections(learning_metadata=metadata)


def classify_runtime_event(event: RuntimeEvent) -> LearningMemoryExportCategory:
    """Validate and classify an event without persistence or Runtime side effects."""

    if event.learning_metadata is not None:
        raise LearningMemoryError(
            "RuntimeEvent.learning_metadata must be absent before export."
        )

    for section in REQUIRED_EXPORT_EVENT_SECTIONS:
        if getattr(event, section) is None:
            raise LearningMemoryError(f"RuntimeEvent.{section} is required.")

    _validate_market_snapshot_identity(event)
    _validate_runtime_owned_event_ids(event)
    _validate_confidence_source_identity(event)

    if event.observation_package is not None:
        _validate_section_event_id(event, "observation_package")

    if event.scenario_probability is None:
        return LearningMemoryExportCategory.REVIEW_ONLY

    _validate_section_event_id(event, "scenario_probability")
    _validate_scenario_source_identity(event)
    return LearningMemoryExportCategory.CASE_READY


def _validate_market_snapshot_identity(event: RuntimeEvent) -> None:
    snapshot = event.market_snapshot
    mismatches = []
    if snapshot.symbol != event.symbol:
        mismatches.append("symbol")
    if snapshot.exchange != event.exchange:
        mismatches.append("exchange")
    if snapshot.timeframe != event.timeframe:
        mismatches.append("timeframe")

    if mismatches:
        raise LearningMemoryError(
            "MarketSnapshot identity does not match RuntimeEvent: "
            + ", ".join(mismatches)
        )


def _validate_runtime_owned_event_ids(event: RuntimeEvent) -> None:
    for section_name in RUNTIME_OWNED_EVENT_ID_SECTIONS:
        _validate_section_event_id(event, section_name)


def _validate_section_event_id(event: RuntimeEvent, section_name: str) -> None:
    section = getattr(event, section_name)
    section_event_id = (
        section.runtime_event_id
        if section_name == "scenario_probability"
        else section.event_id
    )
    if section_event_id != event.event_id:
        raise LearningMemoryError(
            f"RuntimeEvent.{section_name} event ID must match "
            "RuntimeEvent.event_id."
        )


def _validate_scenario_source_identity(event: RuntimeEvent) -> None:
    scenario = event.scenario_probability
    hypothesis = event.hypothesis_package
    if scenario.episode_id != hypothesis.episode_id:
        raise LearningMemoryError(
            "RuntimeEvent.scenario_probability.episode_id must match "
            "RuntimeEvent.hypothesis_package.episode_id."
        )
    if scenario.source_hypothesis_id != hypothesis.hypothesis_id:
        raise LearningMemoryError(
            "RuntimeEvent.scenario_probability.source_hypothesis_id must match "
            "RuntimeEvent.hypothesis_package.hypothesis_id."
        )


def _validate_confidence_source_identity(event: RuntimeEvent) -> None:
    confidence = event.confidence_assessment
    hypothesis = event.hypothesis_package
    if confidence.episode_id != hypothesis.episode_id:
        raise LearningMemoryError(
            "RuntimeEvent.confidence_assessment.episode_id must match "
            "RuntimeEvent.hypothesis_package.episode_id."
        )
    if confidence.source_hypothesis_id != hypothesis.hypothesis_id:
        raise LearningMemoryError(
            "RuntimeEvent.confidence_assessment.source_hypothesis_id must match "
            "RuntimeEvent.hypothesis_package.hypothesis_id."
        )


def _case_id(event: RuntimeEvent) -> str:
    return f"case-{event.event_id}"


def _storage_reason(
    event: RuntimeEvent,
    category: LearningMemoryExportCategory,
) -> str:
    if category is LearningMemoryExportCategory.REVIEW_ONLY:
        return (
            "RuntimeEvent is available for human review but is not eligible "
            "for future case storage because Scenario Probability is missing. "
            "No persistence, automatic learning, Research Agent trigger, or "
            "Runtime behavior change is performed."
        )

    decision_type = event.decision_alert.decision_type.value
    return (
        "Completed RuntimeEvent is eligible for future case storage after "
        "human review; "
        f"Decision / Alert output was {decision_type}. "
        "No persistence, automatic learning, Research Agent trigger, or "
        "Runtime behavior change is performed."
    )
