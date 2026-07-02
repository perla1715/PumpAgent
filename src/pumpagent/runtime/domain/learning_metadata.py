"""LearningMetadata domain model."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from pumpagent.runtime.domain.base import SerializableMixin
from pumpagent.runtime.domain.enums import ReviewStatus


@dataclass(frozen=True)
class LearningMetadata(SerializableMixin):
    """Learning metadata with immutable replacement-style updates."""

    event_id: str
    case_id: str
    should_store: bool
    storage_reason: str
    review_status: ReviewStatus
    created_at: datetime
    schema_version: str = "1.0"
    outcome_pending: bool = True
    outcome_summary: str | None = None
    human_annotation: str | None = None
    research_tags: tuple[str, ...] = ()
    similarity_tags: tuple[str, ...] = ()
    lesson_learned: str | None = None
    follow_up_event_id: str | None = None
    linked_cases: tuple[str, ...] = ()
    reviewed_by: str | None = None
    review_timestamp: datetime | None = None

    def with_review_update(self, **changes: object) -> "LearningMetadata":
        """Return a new metadata instance with review/outcome fields replaced."""

        return replace(self, **changes)
