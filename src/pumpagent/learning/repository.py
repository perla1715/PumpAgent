"""Repository abstraction and transactional SQLite learning-case store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pumpagent.learning.domain import (
    CaseStatus,
    CompletenessStatus,
    DatasetEligibility,
    LearningCase,
    OutcomeRecord,
    OutcomeStatus,
    ReviewRecord,
)
from pumpagent.runtime.domain.enums import ReviewStatus
from pumpagent.runtime.domain.learning_metadata import LearningMetadata


STORAGE_SCHEMA_VERSION = "learning_store_v1"


class LearningCaseStorageError(RuntimeError):
    pass


class LearningCaseConflictError(LearningCaseStorageError):
    pass


class LearningCaseRepository(Protocol):
    def store_case(self, case: LearningCase) -> LearningCase: ...
    def get_case(self, case_id: str) -> LearningCase | None: ...
    def get_case_by_runtime_event_id(self, runtime_event_id: str) -> LearningCase | None: ...
    def attach_outcome(self, outcome: OutcomeRecord) -> OutcomeRecord: ...
    def list_cases(self, status: CaseStatus | None = None) -> tuple[LearningCase, ...]: ...
    def list_dataset_eligible(self) -> tuple[LearningCase, ...]: ...
    def record_review(self, review: ReviewRecord) -> ReviewRecord: ...


class SQLiteLearningCaseRepository:
    """Durable idempotent SQLite implementation with immutable payloads."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    def initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
                connection.execute(
                    "INSERT OR IGNORE INTO schema_metadata(key, value) VALUES (?, ?)",
                    ("schema_version", STORAGE_SCHEMA_VERSION),
                )
                stored_version = connection.execute(
                    "SELECT value FROM schema_metadata WHERE key = ?",
                    ("schema_version",),
                ).fetchone()[0]
                if stored_version != STORAGE_SCHEMA_VERSION:
                    raise LearningCaseStorageError(
                        "Unsupported learning-store schema version."
                    )
        except LearningCaseStorageError:
            raise
        except sqlite3.Error as exc:
            raise LearningCaseStorageError(str(exc)) from exc

    def store_case(self, case: LearningCase) -> LearningCase:
        payload = _canonical_json(case.to_dict())
        digest = _digest(payload)
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT payload_digest FROM learning_cases WHERE case_id = ? "
                    "OR runtime_event_id = ?",
                    (case.case_id, case.runtime_event_id),
                ).fetchone()
                if existing is not None:
                    stored = self._get_case(connection, case.case_id)
                    if stored is None:
                        stored = self._get_case_by_event(
                            connection, case.runtime_event_id
                        )
                    if stored is None or not _same_case_origin(stored, case):
                        raise LearningCaseConflictError(
                            "Conflicting LearningCase identity."
                        )
                    return stored
                connection.execute(
                    """
                    INSERT INTO learning_cases(
                        case_id, runtime_event_id, payload, payload_digest,
                        case_status, outcome_status, dataset_eligibility,
                        cycle_timestamp, ingestion_timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case.case_id,
                        case.runtime_event_id,
                        payload,
                        digest,
                        case.case_status.value,
                        case.outcome_status.value,
                        case.dataset_eligibility.value,
                        case.cycle_timestamp.isoformat(),
                        case.ingestion_timestamp.isoformat(),
                    ),
                )
                return case
        except LearningCaseConflictError:
            raise
        except sqlite3.Error as exc:
            raise LearningCaseStorageError(str(exc)) from exc

    def get_case(self, case_id: str) -> LearningCase | None:
        with self._connect() as connection:
            return self._get_case(connection, case_id)

    def get_case_by_runtime_event_id(
        self, runtime_event_id: str
    ) -> LearningCase | None:
        with self._connect() as connection:
            return self._get_case_by_event(connection, runtime_event_id)

    def attach_outcome(self, outcome: OutcomeRecord) -> OutcomeRecord:
        payload = _canonical_json(outcome.to_dict())
        digest = _digest(payload)
        try:
            with self._connect() as connection:
                case = self._get_case(connection, outcome.source_case_id)
                if case is None:
                    raise LearningCaseStorageError("Outcome source case does not exist.")
                if case.runtime_event_id != outcome.source_runtime_event_id:
                    raise LearningCaseConflictError(
                        "Outcome Runtime event identity conflicts with its case."
                    )
                existing = connection.execute(
                    "SELECT payload_digest FROM outcome_records "
                    "WHERE outcome_id = ?",
                    (outcome.outcome_id,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != digest:
                        raise LearningCaseConflictError(
                            "Conflicting OutcomeRecord identity."
                        )
                    return outcome
                connection.execute(
                    """
                    INSERT INTO outcome_records(
                        outcome_id, source_case_id, horizon_minutes, payload,
                        payload_digest, completeness_status, creation_timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome.outcome_id,
                        outcome.source_case_id,
                        outcome.horizon_minutes,
                        payload,
                        digest,
                        outcome.completeness_status.value,
                        outcome.creation_timestamp.isoformat(),
                    ),
                )
                self._refresh_case_status(connection, case)
                return outcome
        except (LearningCaseConflictError, LearningCaseStorageError):
            raise
        except sqlite3.Error as exc:
            raise LearningCaseStorageError(str(exc)) from exc

    def list_outcomes(self, case_id: str) -> tuple[OutcomeRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM outcome_records WHERE source_case_id = ? "
                "ORDER BY horizon_minutes, creation_timestamp, outcome_id",
                (case_id,),
            ).fetchall()
        selected: dict[int, OutcomeRecord] = {}
        for row in rows:
            record = _outcome_from_dict(json.loads(row[0]))
            current = selected.get(record.horizon_minutes)
            if current is None or _outcome_rank(record) > _outcome_rank(current):
                selected[record.horizon_minutes] = record
        return tuple(selected[horizon] for horizon in sorted(selected))

    def list_cases(
        self, status: CaseStatus | None = None
    ) -> tuple[LearningCase, ...]:
        query = "SELECT payload FROM learning_cases"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE case_status = ?"
            parameters = (status.value,)
        query += " ORDER BY cycle_timestamp, case_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_case_from_dict(json.loads(row[0])) for row in rows)

    def list_dataset_eligible(self) -> tuple[LearningCase, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM learning_cases "
                "WHERE dataset_eligibility = ? ORDER BY cycle_timestamp, case_id",
                (DatasetEligibility.ELIGIBLE.value,),
            ).fetchall()
        return tuple(_case_from_dict(json.loads(row[0])) for row in rows)

    def record_review(self, review: ReviewRecord) -> ReviewRecord:
        payload = _canonical_json(review.to_dict())
        digest = _digest(payload)
        try:
            with self._connect() as connection:
                if self._get_case(connection, review.case_id) is None:
                    raise LearningCaseStorageError("Review source case does not exist.")
                existing = connection.execute(
                    "SELECT payload_digest FROM review_records WHERE review_id = ?",
                    (review.review_id,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != digest:
                        raise LearningCaseConflictError("Conflicting review identity.")
                    return review
                connection.execute(
                    "INSERT INTO review_records(review_id, case_id, payload, "
                    "payload_digest, reviewed_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        review.review_id,
                        review.case_id,
                        payload,
                        digest,
                        review.reviewed_at.isoformat(),
                    ),
                )
                connection.execute(
                    "UPDATE learning_cases SET case_status = ? WHERE case_id = ?",
                    (CaseStatus.REVIEWED.value, review.case_id),
                )
                raw = json.loads(
                    connection.execute(
                        "SELECT payload FROM learning_cases WHERE case_id = ?",
                        (review.case_id,),
                    ).fetchone()[0]
                )
                raw["case_status"] = CaseStatus.REVIEWED.value
                case_payload = _canonical_json(raw)
                connection.execute(
                    "UPDATE learning_cases SET payload = ?, payload_digest = ? "
                    "WHERE case_id = ?",
                    (case_payload, _digest(case_payload), review.case_id),
                )
                return review
        except (LearningCaseConflictError, LearningCaseStorageError):
            raise
        except sqlite3.Error as exc:
            raise LearningCaseStorageError(str(exc)) from exc

    def integrity_check(self) -> tuple[bool, tuple[str, ...]]:
        errors: list[str] = []
        with self._connect() as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                errors.append(result)
            for payload, digest in connection.execute(
                "SELECT payload, payload_digest FROM learning_cases"
            ):
                if _digest(payload) != digest:
                    errors.append("LearningCase payload digest mismatch.")
            for payload, digest in connection.execute(
                "SELECT payload, payload_digest FROM outcome_records"
            ):
                if _digest(payload) != digest:
                    errors.append("OutcomeRecord payload digest mismatch.")
        return not errors, tuple(errors)

    def _refresh_case_status(
        self, connection: sqlite3.Connection, case: LearningCase
    ) -> None:
        rows = connection.execute(
            "SELECT horizon_minutes, completeness_status FROM outcome_records "
            "WHERE source_case_id = ?",
            (case.case_id,),
        ).fetchall()
        complete = {
            int(row[0])
            for row in rows
            if row[1] == CompletenessStatus.COMPLETE.value
        }
        all_horizons = {5, 15, 30, 60}
        if complete == all_horizons:
            status = CaseStatus.OUTCOME_COMPLETE
            outcome = OutcomeStatus.COMPLETE
            eligibility = DatasetEligibility.ELIGIBLE
        elif rows:
            status = CaseStatus.OUTCOME_PARTIAL
            outcome = OutcomeStatus.PARTIAL
            eligibility = DatasetEligibility.PENDING
        else:
            return
        raw = json.loads(
            connection.execute(
                "SELECT payload FROM learning_cases WHERE case_id = ?",
                (case.case_id,),
            ).fetchone()[0]
        )
        raw["case_status"] = status.value
        raw["outcome_status"] = outcome.value
        raw["dataset_eligibility"] = eligibility.value
        payload = _canonical_json(raw)
        connection.execute(
            "UPDATE learning_cases SET payload = ?, payload_digest = ?, "
            "case_status = ?, outcome_status = ?, dataset_eligibility = ? "
            "WHERE case_id = ?",
            (
                payload,
                _digest(payload),
                status.value,
                outcome.value,
                eligibility.value,
                case.case_id,
            ),
        )

    def _get_case(
        self, connection: sqlite3.Connection, case_id: str
    ) -> LearningCase | None:
        row = connection.execute(
            "SELECT payload FROM learning_cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return None if row is None else _case_from_dict(json.loads(row[0]))

    def _get_case_by_event(
        self, connection: sqlite3.Connection, event_id: str
    ) -> LearningCase | None:
        row = connection.execute(
            "SELECT payload FROM learning_cases WHERE runtime_event_id = ?",
            (event_id,),
        ).fetchone()
        return None if row is None else _case_from_dict(json.loads(row[0]))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _same_case_origin(stored: LearningCase, candidate: LearningCase) -> bool:
    fields = (
        "case_id",
        "runtime_event_id",
        "runtime_event_schema_version",
        "runtime_event_payload",
        "symbol",
        "exchange",
        "timeframe",
        "cycle_timestamp",
        "episode_id",
        "ingestion_timestamp",
        "learning_metadata",
        "exclusion_reasons",
        "provenance",
        "schema_version",
    )
    return all(getattr(stored, name) == getattr(candidate, name) for name in fields)


def _outcome_rank(record: OutcomeRecord) -> tuple[int, datetime, str]:
    completeness = {
        CompletenessStatus.UNAVAILABLE: 0,
        CompletenessStatus.INCOMPLETE: 1,
        CompletenessStatus.COMPLETE: 2,
    }[record.completeness_status]
    boundary = record.observation_end_timestamp or record.source_cycle_timestamp
    return completeness, boundary, record.outcome_id


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _case_from_dict(value: dict[str, object]) -> LearningCase:
    metadata = value["learning_metadata"]
    assert isinstance(metadata, dict)
    return LearningCase(
        case_id=str(value["case_id"]),
        runtime_event_id=str(value["runtime_event_id"]),
        runtime_event_schema_version=str(value["runtime_event_schema_version"]),
        runtime_event_payload=value["runtime_event_payload"],  # type: ignore[arg-type]
        symbol=str(value["symbol"]),
        exchange=str(value["exchange"]),
        timeframe=str(value["timeframe"]),
        cycle_timestamp=datetime.fromisoformat(str(value["cycle_timestamp"])),
        episode_id=str(value["episode_id"]),
        ingestion_timestamp=datetime.fromisoformat(str(value["ingestion_timestamp"])),
        case_status=CaseStatus(str(value["case_status"])),
        learning_metadata=_metadata_from_dict(metadata),
        outcome_status=OutcomeStatus(str(value["outcome_status"])),
        dataset_eligibility=DatasetEligibility(str(value["dataset_eligibility"])),
        exclusion_reasons=tuple(value["exclusion_reasons"]),  # type: ignore[arg-type]
        provenance=value["provenance"],  # type: ignore[arg-type]
        schema_version=str(value["schema_version"]),
    )


def _metadata_from_dict(value: dict[str, object]) -> LearningMetadata:
    return LearningMetadata(
        event_id=str(value["event_id"]),
        case_id=str(value["case_id"]),
        should_store=bool(value["should_store"]),
        storage_reason=str(value["storage_reason"]),
        review_status=ReviewStatus(str(value["review_status"])),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        schema_version=str(value["schema_version"]),
        outcome_pending=bool(value["outcome_pending"]),
        outcome_summary=value.get("outcome_summary"),  # type: ignore[arg-type]
        human_annotation=value.get("human_annotation"),  # type: ignore[arg-type]
        research_tags=tuple(value.get("research_tags", ())),  # type: ignore[arg-type]
        similarity_tags=tuple(value.get("similarity_tags", ())),  # type: ignore[arg-type]
        lesson_learned=value.get("lesson_learned"),  # type: ignore[arg-type]
        follow_up_event_id=value.get("follow_up_event_id"),  # type: ignore[arg-type]
        linked_cases=tuple(value.get("linked_cases", ())),  # type: ignore[arg-type]
        reviewed_by=value.get("reviewed_by"),  # type: ignore[arg-type]
        review_timestamp=(
            datetime.fromisoformat(str(value["review_timestamp"]))
            if value.get("review_timestamp")
            else None
        ),
    )


def _outcome_from_dict(value: dict[str, object]) -> OutcomeRecord:
    optional_times = {}
    for name in ("observation_start_timestamp", "observation_end_timestamp"):
        optional_times[name] = (
            datetime.fromisoformat(str(value[name])) if value.get(name) else None
        )
    return OutcomeRecord(
        outcome_id=str(value["outcome_id"]),
        source_case_id=str(value["source_case_id"]),
        source_runtime_event_id=str(value["source_runtime_event_id"]),
        source_cycle_timestamp=datetime.fromisoformat(
            str(value["source_cycle_timestamp"])
        ),
        horizon_minutes=int(value["horizon_minutes"]),
        source_data_identity=value["source_data_identity"],  # type: ignore[arg-type]
        close_to_close_return=value.get("close_to_close_return"),  # type: ignore[arg-type]
        maximum_favorable_excursion=value.get("maximum_favorable_excursion"),  # type: ignore[arg-type]
        maximum_adverse_excursion=value.get("maximum_adverse_excursion"),  # type: ignore[arg-type]
        maximum_high_return=value.get("maximum_high_return"),  # type: ignore[arg-type]
        minimum_low_return=value.get("minimum_low_return"),  # type: ignore[arg-type]
        time_to_maximum_favorable_excursion_seconds=value.get("time_to_maximum_favorable_excursion_seconds"),  # type: ignore[arg-type]
        time_to_maximum_adverse_excursion_seconds=value.get("time_to_maximum_adverse_excursion_seconds"),  # type: ignore[arg-type]
        realized_volatility=value.get("realized_volatility"),  # type: ignore[arg-type]
        volume_change=value.get("volume_change"),  # type: ignore[arg-type]
        window_complete=bool(value["window_complete"]),
        completeness_status=CompletenessStatus(str(value["completeness_status"])),
        missing_reasons=tuple(value["missing_reasons"]),  # type: ignore[arg-type]
        creation_timestamp=datetime.fromisoformat(str(value["creation_timestamp"])),
        computation_version=str(value["computation_version"]),
        schema_version=str(value["schema_version"]),
        **optional_times,
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_cases (
    case_id TEXT PRIMARY KEY,
    runtime_event_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    case_status TEXT NOT NULL,
    outcome_status TEXT NOT NULL,
    dataset_eligibility TEXT NOT NULL,
    cycle_timestamp TEXT NOT NULL,
    ingestion_timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcome_records (
    outcome_id TEXT PRIMARY KEY,
    source_case_id TEXT NOT NULL,
    horizon_minutes INTEGER NOT NULL,
    payload TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    completeness_status TEXT NOT NULL,
    creation_timestamp TEXT NOT NULL,
    FOREIGN KEY(source_case_id) REFERENCES learning_cases(case_id)
);
CREATE TABLE IF NOT EXISTS review_records (
    review_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES learning_cases(case_id)
);
"""
