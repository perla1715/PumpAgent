"""Immutable canonical MVP Decision contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.process_quality import (
    HealthyBaselineReference,
    ProcessQualityAssessmentReference,
)


DECISION_ASSESSMENT_SCHEMA_VERSION = "decision_assessment_v1"
DECISION_REFERENCE_SCHEMA_VERSION = "decision_reference_v1"


class DecisionType(str, Enum):
    LOOK_FOR_LONG = "look_for_long"
    LOOK_FOR_SHORT = "look_for_short"
    STAY_OUT = "stay_out"


class DecisionStatus(str, Enum):
    COMPLETED = "completed"


class DecisionReasonCode(str, Enum):
    BULLISH_SCENARIO_CONFIRMED = "bullish_scenario_confirmed"
    BEARISH_SCENARIO_CONFIRMED = "bearish_scenario_confirmed"
    PROCESS_QUALITY_ALIGNED = "process_quality_aligned"
    HYPOTHESIS_ALIGNED = "hypothesis_aligned"
    CONFIDENCE_THRESHOLD_MET = "confidence_threshold_met"
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    BLOCKING_UNCERTAINTY = "blocking_uncertainty"
    MIXED_EVIDENCE = "mixed_evidence"
    UPSTREAM_INHIBITION = "upstream_inhibition"
    SCENARIO_PROCESS_CONFLICT = "scenario_process_conflict"
    MISSING_REQUIRED_EVIDENCE = "missing_required_evidence"
    ANALYTICAL_STATE_NOT_DIRECTIONAL = "analytical_state_not_directional"


@dataclass(frozen=True)
class DecisionReference(SerializableMixin):
    decision_id: str
    episode_id: str
    runtime_event_id: str
    decision_type: DecisionType
    created_at: datetime
    schema_version: str = DECISION_REFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("decision_id", "episode_id", "runtime_event_id", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        if not isinstance(self.decision_type, DecisionType):
            raise ValueError("decision_type must be a DecisionType.")
        _require_aware("created_at", self.created_at)
        expected = canonical_decision_id(self.episode_id, self.runtime_event_id)
        if self.decision_id != expected:
            raise ValueError("Decision identity does not match the canonical MVP formula.")


@dataclass(frozen=True)
class DecisionAssessment(SerializableMixin):
    episode_id: str
    decision_id: str
    runtime_event_id: str
    decision_type: DecisionType
    decision_status: DecisionStatus
    reason_codes: tuple[DecisionReasonCode, ...]
    confidence_reference: str
    hypothesis_reference: str
    scenario_probability_reference: str
    process_quality_reference: ProcessQualityAssessmentReference
    process_evidence_reference: str
    created_at: datetime
    healthy_baseline_reference: HealthyBaselineReference | None = None
    previous_decision_reference: DecisionReference | None = None
    provenance: tuple[str, ...] = ()
    non_execution_confirmation: bool = True
    schema_version: str = DECISION_ASSESSMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in (
            "episode_id",
            "decision_id",
            "runtime_event_id",
            "confidence_reference",
            "hypothesis_reference",
            "scenario_probability_reference",
            "process_evidence_reference",
            "schema_version",
        ):
            _require_non_empty(name, getattr(self, name))
        if self.decision_id != canonical_decision_id(
            self.episode_id, self.runtime_event_id
        ):
            raise ValueError("Decision identity does not match the canonical MVP formula.")
        if not isinstance(self.decision_type, DecisionType):
            raise ValueError("decision_type must be a DecisionType.")
        if self.decision_status is not DecisionStatus.COMPLETED:
            raise ValueError("MVP Decision status must be COMPLETED.")
        if not self.reason_codes:
            raise ValueError("reason_codes must contain at least one reason.")
        if any(not isinstance(code, DecisionReasonCode) for code in self.reason_codes):
            raise ValueError("reason_codes must contain DecisionReasonCode values.")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must be unique.")
        if not isinstance(
            self.process_quality_reference, ProcessQualityAssessmentReference
        ):
            raise ValueError(
                "process_quality_reference must be a ProcessQualityAssessmentReference."
            )
        if self.process_quality_reference.episode_id != self.episode_id:
            raise ValueError("Decision and Process Quality must share an Episode.")
        if self.process_quality_reference.runtime_event_id != self.runtime_event_id:
            raise ValueError("Decision and Process Quality Runtime IDs must match.")
        if self.healthy_baseline_reference is not None and not isinstance(
            self.healthy_baseline_reference, HealthyBaselineReference
        ):
            raise ValueError(
                "healthy_baseline_reference must be a HealthyBaselineReference or None."
            )
        if (
            self.healthy_baseline_reference is not None
            and self.healthy_baseline_reference.episode_id != self.episode_id
        ):
            raise ValueError("Decision Healthy Baseline cannot cross Episodes.")
        if self.previous_decision_reference is not None:
            if not isinstance(self.previous_decision_reference, DecisionReference):
                raise ValueError(
                    "previous_decision_reference must be a DecisionReference or None."
                )
            if self.previous_decision_reference.episode_id != self.episode_id:
                raise ValueError("Previous Decision cannot cross Episodes.")
            if self.previous_decision_reference.created_at >= self.created_at:
                raise ValueError("Previous Decision must precede the current Decision.")
        for item in self.provenance:
            _require_non_empty("provenance", item)
        if self.non_execution_confirmation is not True:
            raise ValueError("MVP Decision must confirm non-execution.")
        _require_aware("created_at", self.created_at)

    def to_reference(self) -> DecisionReference:
        return DecisionReference(
            decision_id=self.decision_id,
            episode_id=self.episode_id,
            runtime_event_id=self.runtime_event_id,
            decision_type=self.decision_type,
            created_at=self.created_at,
        )


def canonical_decision_id(episode_id: str, runtime_event_id: str) -> str:
    _require_non_empty("episode_id", episode_id)
    _require_non_empty("runtime_event_id", runtime_event_id)
    return f"decision:{episode_id}:{runtime_event_id}"


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _require_aware(name: str, value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be timezone-aware.")
