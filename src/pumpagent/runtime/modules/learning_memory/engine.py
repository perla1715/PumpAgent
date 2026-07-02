"""Learning Memory v0.1.

Learning Memory prepares a completed RuntimeEvent for future storage and human
review. It does not persist files, trigger Research Agent, learn
automatically, or modify Runtime behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pumpagent.runtime.domain import LearningMetadata, RuntimeEvent
from pumpagent.runtime.domain.enums import ReviewStatus


class LearningMemoryError(ValueError):
    """Raised when Learning Memory cannot prepare metadata."""


REQUIRED_COMPLETED_EVENT_SECTIONS = (
    "market_snapshot",
    "observation_package",
    "structural_evidence",
    "market_efficiency_evidence",
    "hypothesis_package",
    "agent_state",
    "scenario_probability",
    "confidence_assessment",
    "decision_alert",
)

RUNTIME_OWNED_EVENT_ID_SECTIONS = (
    "observation_package",
    "structural_evidence",
    "market_efficiency_evidence",
    "hypothesis_package",
    "agent_state",
    "scenario_probability",
    "confidence_assessment",
    "decision_alert",
)


def build_learning_metadata(
    event: RuntimeEvent,
    *,
    created_at: datetime | None = None,
) -> LearningMetadata:
    """Build storage/review metadata without persistence or learning side effects."""

    _validate_event(event)
    timestamp = created_at or datetime.now(timezone.utc)

    return LearningMetadata(
        event_id=event.event_id,
        case_id=_case_id(event),
        should_store=True,
        storage_reason=_storage_reason(event),
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


def _validate_event(event: RuntimeEvent) -> None:
    for section in REQUIRED_COMPLETED_EVENT_SECTIONS:
        if getattr(event, section) is None:
            raise LearningMemoryError(f"RuntimeEvent.{section} is required.")

    _validate_market_snapshot_identity(event)
    _validate_runtime_owned_event_ids(event)


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
        section = getattr(event, section_name)
        if section.event_id != event.event_id:
            raise LearningMemoryError(
                f"RuntimeEvent.{section_name}.event_id must match "
                "RuntimeEvent.event_id."
            )


def _case_id(event: RuntimeEvent) -> str:
    return f"case-{event.event_id}"


def _storage_reason(event: RuntimeEvent) -> str:
    decision_type = event.decision_alert.decision_type.value
    return (
        "Store completed RuntimeEvent for future human review; "
        f"Decision / Alert output was {decision_type}. "
        "No automatic learning or Runtime behavior change is performed."
    )
