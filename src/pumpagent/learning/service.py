"""Explicit caller-controlled Learning Memory persistence boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from pumpagent.learning.domain import (
    CaseStatus,
    DatasetEligibility,
    LearningCase,
    OutcomeStatus,
)
from pumpagent.learning.repository import LearningCaseRepository
from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.domain.learning_metadata import LearningMetadata
from pumpagent.runtime.modules.learning_memory import build_learning_metadata
from pumpagent.runtime.orchestrator import serialize_runtime_event


class LearningCasePersistenceError(ValueError):
    pass


class LearningCasePersistenceService:
    """Persist validated canonical events without Runtime integration or reasoning."""

    def __init__(self, repository: LearningCaseRepository) -> None:
        self.repository = repository

    def persist(
        self,
        event: RuntimeEvent,
        *,
        ingestion_timestamp: datetime | None = None,
        provenance: Mapping[str, object] | None = None,
    ) -> LearningCase:
        if not isinstance(event, RuntimeEvent):
            raise LearningCasePersistenceError("event must be RuntimeEvent.")
        event.validate()
        if event.runtime_status is not RuntimeStatus.COMPLETED:
            raise LearningCasePersistenceError(
                "Only a completed canonical RuntimeEvent can be persisted."
            )
        metadata = event.learning_metadata or build_learning_metadata(
            event, created_at=ingestion_timestamp
        )
        _validate_metadata(metadata, event)
        if not metadata.should_store:
            raise LearningCasePersistenceError(
                "LearningMetadata does not authorize case storage."
            )
        serialized = serialize_runtime_event(event)
        canonical_event = dict(serialized["runtime_event"])
        # Learning metadata is stored as a separate versioned case field and is
        # never used to rewrite the immutable analytical payload.
        canonical_event["learning_metadata"] = None
        payload = {
            "persistence_schema_version": serialized[
                "persistence_schema_version"
            ],
            "runtime_event": canonical_event,
        }
        case = LearningCase(
            case_id=metadata.case_id,
            runtime_event_id=event.event_id,
            runtime_event_schema_version=event.schema_version,
            runtime_event_payload=payload,
            symbol=event.symbol,
            exchange=event.exchange,
            timeframe=event.timeframe,
            cycle_timestamp=event.cycle_timestamp,
            episode_id=event.episode_id,
            ingestion_timestamp=ingestion_timestamp
            or datetime.now(timezone.utc),
            case_status=CaseStatus.PENDING_OUTCOME,
            learning_metadata=metadata,
            outcome_status=OutcomeStatus.PENDING,
            dataset_eligibility=DatasetEligibility.PENDING,
            provenance={
                "runtime_event_id": event.event_id,
                "runtime_schema_version": event.schema_version,
                **dict(provenance or {}),
            },
        )
        return self.repository.store_case(case)


def _validate_metadata(metadata: LearningMetadata, event: RuntimeEvent) -> None:
    if metadata.event_id != event.event_id:
        raise LearningCasePersistenceError(
            "LearningMetadata Runtime event identity mismatch."
        )
    if metadata.case_id != f"case-{event.event_id}":
        raise LearningCasePersistenceError(
            "LearningMetadata case identity mismatch."
        )
