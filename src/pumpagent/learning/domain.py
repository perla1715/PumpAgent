"""Versioned immutable contracts for offline learning data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from math import isfinite
from typing import Any, Mapping

from pumpagent.runtime.domain.base import (
    SerializableMixin,
    freeze_dataclass_fields,
)
from pumpagent.runtime.domain.learning_metadata import LearningMetadata


LEARNING_CASE_SCHEMA_VERSION = "learning_case_v1"
OUTCOME_RECORD_SCHEMA_VERSION = "outcome_record_v1"
OUTCOME_COMPUTATION_VERSION = "outcome_metrics_v1"
REVIEW_RECORD_SCHEMA_VERSION = "learning_review_v1"
READINESS_ASSESSMENT_SCHEMA_VERSION = "learning_readiness_assessment_v1"
READINESS_VALIDATOR_VERSION = "learning_readiness_validator_v1"
SUPPORTED_HORIZONS_MINUTES = (5, 15, 30, 60)


class CaseStatus(str, Enum):
    PENDING_OUTCOME = "pending_outcome"
    OUTCOME_PARTIAL = "outcome_partial"
    OUTCOME_COMPLETE = "outcome_complete"
    REVIEWED = "reviewed"
    EXCLUDED = "excluded"


class OutcomeStatus(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    COMPLETE = "complete"


class DatasetEligibility(str, Enum):
    PENDING = "pending"
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"


class CompletenessStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"


class OutcomeLabel(str, Enum):
    PUMP_CONTINUATION = "pump_continuation"
    PUMP_FAILURE = "pump_failure"
    DUMP_CONTINUATION = "dump_continuation"
    DUMP_RECOVERY = "dump_recovery"
    RANGE_OR_CONTROL = "range_or_control"
    INSUFFICIENT_OUTCOME = "insufficient_outcome"


class LearningReadinessStatus(str, Enum):
    PENDING = "pending"
    NOT_READY = "not_ready"
    LEARNING_READY = "learning_ready"
    INVALID = "invalid"


@dataclass(frozen=True)
class ReadinessCheck(SerializableMixin):
    check_id: str
    passed: bool
    detail: str

    def __post_init__(self) -> None:
        _non_empty("check_id", self.check_id)
        _non_empty("detail", self.detail)
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be bool.")


@dataclass(frozen=True)
class LearningReadinessAssessment(SerializableMixin):
    assessment_id: str
    case_id: str
    runtime_event_id: str
    assessment_version: str
    assessment_timestamp: datetime
    readiness_status: LearningReadinessStatus
    evaluated_outcome_horizon: int | None
    canonical_payload_digest: str
    outcome_record_id: str | None
    label_policy_version: str
    checks_performed: tuple[ReadinessCheck, ...]
    failure_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    validator_version: str
    review_status: str
    technically_ready: bool
    approved_for_evaluation: bool
    approved_for_training: bool
    manually_excluded: bool
    administratively_blocked: bool
    provenance: Mapping[str, Any]
    schema_version: str = READINESS_ASSESSMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in (
            "assessment_id",
            "case_id",
            "runtime_event_id",
            "assessment_version",
            "canonical_payload_digest",
            "label_policy_version",
            "validator_version",
            "review_status",
            "schema_version",
        ):
            _non_empty(name, getattr(self, name))
        _aware("assessment_timestamp", self.assessment_timestamp)
        if not isinstance(self.readiness_status, LearningReadinessStatus):
            raise ValueError(
                "readiness_status must be LearningReadinessStatus."
            )
        if (
            self.evaluated_outcome_horizon is not None
            and self.evaluated_outcome_horizon
            not in SUPPORTED_HORIZONS_MINUTES
        ):
            raise ValueError("Unsupported readiness outcome horizon.")
        if self.outcome_record_id is not None:
            _non_empty("outcome_record_id", self.outcome_record_id)
        if any(not isinstance(item, ReadinessCheck) for item in self.checks_performed):
            raise ValueError("checks_performed must contain ReadinessCheck values.")
        _strings("failure_reasons", self.failure_reasons)
        _strings("warnings", self.warnings)
        if self.technically_ready != (
            self.readiness_status is LearningReadinessStatus.LEARNING_READY
        ):
            raise ValueError("technically_ready disagrees with readiness_status.")
        if self.approved_for_training and not self.approved_for_evaluation:
            raise ValueError("Training approval requires evaluation approval.")
        if (self.manually_excluded or self.administratively_blocked) and (
            self.approved_for_evaluation or self.approved_for_training
        ):
            raise ValueError("Excluded or blocked cases cannot be approved.")
        if self.schema_version != READINESS_ASSESSMENT_SCHEMA_VERSION:
            raise ValueError("Unsupported readiness assessment schema.")
        if self.assessment_version != self.validator_version:
            raise ValueError(
                "assessment_version must equal validator_version."
            )
        if self.assessment_id != canonical_readiness_assessment_id(self):
            raise ValueError("Non-canonical readiness assessment identity.")


@dataclass(frozen=True)
class LearningCase(SerializableMixin):
    case_id: str
    runtime_event_id: str
    runtime_event_schema_version: str
    runtime_event_payload: Mapping[str, Any]
    symbol: str
    exchange: str
    timeframe: str
    cycle_timestamp: datetime
    episode_id: str
    ingestion_timestamp: datetime
    case_status: CaseStatus
    learning_metadata: LearningMetadata
    outcome_status: OutcomeStatus = OutcomeStatus.PENDING
    dataset_eligibility: DatasetEligibility = DatasetEligibility.PENDING
    exclusion_reasons: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = None  # type: ignore[assignment]
    schema_version: str = LEARNING_CASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.provenance is None:
            object.__setattr__(self, "provenance", {})
        freeze_dataclass_fields(self)
        for name in (
            "case_id",
            "runtime_event_id",
            "runtime_event_schema_version",
            "symbol",
            "exchange",
            "timeframe",
            "episode_id",
            "schema_version",
        ):
            _non_empty(name, getattr(self, name))
        _aware("cycle_timestamp", self.cycle_timestamp)
        _aware("ingestion_timestamp", self.ingestion_timestamp)
        if not isinstance(self.case_status, CaseStatus):
            raise ValueError("case_status must be a CaseStatus.")
        if not isinstance(self.outcome_status, OutcomeStatus):
            raise ValueError("outcome_status must be an OutcomeStatus.")
        if not isinstance(self.dataset_eligibility, DatasetEligibility):
            raise ValueError("dataset_eligibility must be a DatasetEligibility.")
        if not isinstance(self.learning_metadata, LearningMetadata):
            raise ValueError("learning_metadata must be LearningMetadata.")
        if self.learning_metadata.case_id != self.case_id:
            raise ValueError("LearningMetadata case identity must match.")
        if self.learning_metadata.event_id != self.runtime_event_id:
            raise ValueError("LearningMetadata Runtime event identity must match.")
        if self.schema_version != LEARNING_CASE_SCHEMA_VERSION:
            raise ValueError("Unsupported LearningCase schema version.")
        _strings("exclusion_reasons", self.exclusion_reasons)


@dataclass(frozen=True)
class OutcomeRecord(SerializableMixin):
    outcome_id: str
    source_case_id: str
    source_runtime_event_id: str
    source_cycle_timestamp: datetime
    horizon_minutes: int
    observation_start_timestamp: datetime | None
    observation_end_timestamp: datetime | None
    source_data_identity: Mapping[str, str]
    close_to_close_return: float | None
    maximum_favorable_excursion: float | None
    maximum_adverse_excursion: float | None
    maximum_high_return: float | None
    minimum_low_return: float | None
    time_to_maximum_favorable_excursion_seconds: int | None
    time_to_maximum_adverse_excursion_seconds: int | None
    realized_volatility: float | None
    volume_change: float | None
    window_complete: bool
    completeness_status: CompletenessStatus
    missing_reasons: tuple[str, ...]
    creation_timestamp: datetime
    computation_version: str = OUTCOME_COMPUTATION_VERSION
    schema_version: str = OUTCOME_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in (
            "outcome_id",
            "source_case_id",
            "source_runtime_event_id",
            "computation_version",
            "schema_version",
        ):
            _non_empty(name, getattr(self, name))
        _aware("source_cycle_timestamp", self.source_cycle_timestamp)
        _aware("creation_timestamp", self.creation_timestamp)
        for name in ("observation_start_timestamp", "observation_end_timestamp"):
            value = getattr(self, name)
            if value is not None:
                _aware(name, value)
                if value <= self.source_cycle_timestamp:
                    raise ValueError(
                        f"{name} must be strictly after the source cycle."
                    )
        if self.horizon_minutes not in SUPPORTED_HORIZONS_MINUTES:
            raise ValueError("Unsupported outcome horizon.")
        if not isinstance(self.completeness_status, CompletenessStatus):
            raise ValueError("completeness_status must be CompletenessStatus.")
        if self.window_complete != (
            self.completeness_status is CompletenessStatus.COMPLETE
        ):
            raise ValueError("window_complete and completeness_status disagree.")
        _strings("missing_reasons", self.missing_reasons)
        for name in (
            "close_to_close_return",
            "maximum_favorable_excursion",
            "maximum_adverse_excursion",
            "maximum_high_return",
            "minimum_low_return",
            "realized_volatility",
            "volume_change",
        ):
            value = getattr(self, name)
            if value is not None and not isfinite(value):
                raise ValueError(f"{name} must be finite.")
        if self.schema_version != OUTCOME_RECORD_SCHEMA_VERSION:
            raise ValueError("Unsupported OutcomeRecord schema version.")


@dataclass(frozen=True)
class ReviewRecord(SerializableMixin):
    review_id: str
    case_id: str
    review_status: str
    annotation: str | None
    tags: tuple[str, ...]
    reviewed_by: str
    reviewed_at: datetime
    schema_version: str = REVIEW_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in (
            "review_id",
            "case_id",
            "review_status",
            "reviewed_by",
            "schema_version",
        ):
            _non_empty(name, getattr(self, name))
        _aware("reviewed_at", self.reviewed_at)
        _strings("tags", self.tags)
        if self.annotation is not None:
            _non_empty("annotation", self.annotation)


def _non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _aware(name: str, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime.")


def _strings(name: str, values: tuple[str, ...]) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{name} must contain non-empty strings.")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique values.")


def canonical_readiness_assessment_id(
    assessment: LearningReadinessAssessment,
) -> str:
    return build_readiness_assessment_id(
        case_id=assessment.case_id,
        runtime_event_id=assessment.runtime_event_id,
        validator_version=assessment.validator_version,
        canonical_payload_digest=assessment.canonical_payload_digest,
        outcome_record_id=assessment.outcome_record_id,
        label_policy_version=assessment.label_policy_version,
        review_status=assessment.review_status,
        manually_excluded=assessment.manually_excluded,
        administratively_blocked=assessment.administratively_blocked,
        provenance=assessment.provenance,
    )


def build_readiness_assessment_id(
    *,
    case_id: str,
    runtime_event_id: str,
    validator_version: str,
    canonical_payload_digest: str,
    outcome_record_id: str | None,
    label_policy_version: str,
    review_status: str,
    manually_excluded: bool,
    administratively_blocked: bool,
    provenance: Mapping[str, Any],
) -> str:
    material = {
        "case_id": case_id,
        "runtime_event_id": runtime_event_id,
        "validator_version": validator_version,
        "runtime_schema": provenance.get("runtime_schema_version"),
        "payload_digest": canonical_payload_digest,
        "outcome_id": outcome_record_id,
        "outcome_computation_version": provenance.get(
            "outcome_computation_version"
        ),
        "label_policy_version": label_policy_version,
        "review_status": review_status,
        "manually_excluded": manually_excluded,
        "administratively_blocked": administratively_blocked,
    }
    payload = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"learning-readiness:{case_id}:{digest}"
