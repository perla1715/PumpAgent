"""Immutable Process Engine domain contracts.

This module represents a Process interpretation; it intentionally contains no
classification, aggregation, lifecycle, confidence, or trading logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import (
    EvidenceStrength,
    ProcessDirection,
    UncertaintyLevel,
)


PROCESS_EVIDENCE_SCHEMA_VERSION = "process_evidence_v2"
PROCESS_EVIDENCE_ITEM_SCHEMA_VERSION = "process_evidence_item_v1"


class ProcessState(str, Enum):
    UNKNOWN = "unknown"
    CONTINUATION_ALIVE = "continuation_alive"
    WEAKENING = "weakening"


class ProcessTransition(str, Enum):
    INITIAL = "initial"
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    RECOVERED = "recovered"
    BECAME_UNKNOWN = "became_unknown"


class ProcessEvidenceFamily(str, Enum):
    PRICE = "price"
    VOLUME = "volume"
    OPEN_INTEREST = "open_interest"
    STRUCTURE = "structure"
    CVD = "cvd"
    FUNDING = "funding"
    LIQUIDATIONS = "liquidations"
    DATA_QUALITY = "data_quality"


class ProcessEvidenceRelationship(str, Enum):
    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"
    NEUTRAL = "neutral"
    UNAVAILABLE = "unavailable"


class ProcessEvidenceAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProcessEvidenceItem(SerializableMixin):
    evidence_family: ProcessEvidenceFamily
    evidence_key: str
    description: str
    relationship: ProcessEvidenceRelationship
    source_module: str
    source_field: str
    observation_timestamp: datetime
    availability_status: ProcessEvidenceAvailability
    normalized_value: Any | None = None
    unit: str | None = None
    timeframe: str | None = None
    schema_version: str = PROCESS_EVIDENCE_ITEM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.evidence_family, ProcessEvidenceFamily):
            raise ValueError("evidence_family must be a ProcessEvidenceFamily.")
        if not isinstance(self.relationship, ProcessEvidenceRelationship):
            raise ValueError("relationship must be a ProcessEvidenceRelationship.")
        if not isinstance(self.availability_status, ProcessEvidenceAvailability):
            raise ValueError("availability_status must be a ProcessEvidenceAvailability.")
        for name in ("evidence_key", "description", "source_module", "source_field", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        _require_aware("observation_timestamp", self.observation_timestamp)
        for name in ("unit", "timeframe"):
            value = getattr(self, name)
            if value is not None:
                _require_non_empty(name, value)
        if self.availability_status is ProcessEvidenceAvailability.UNAVAILABLE:
            if self.relationship is not ProcessEvidenceRelationship.UNAVAILABLE:
                raise ValueError("Unavailable evidence must have an unavailable relationship.")
            if self.normalized_value is not None:
                raise ValueError("Unavailable evidence cannot have a normalized value.")
        elif self.relationship is ProcessEvidenceRelationship.UNAVAILABLE:
            raise ValueError("Available evidence cannot have an unavailable relationship.")
        _require_serializable_value("normalized_value", self.normalized_value)


@dataclass(frozen=True)
class ProcessEvidence(SerializableMixin):
    episode_id: str
    runtime_event_id: str
    exchange: str
    symbol: str
    timeframe: str
    observation_timestamp: datetime
    current_process_state: ProcessState
    process_direction: ProcessDirection
    previous_process_state: ProcessState | None
    detected_transition: ProcessTransition
    process_summary: str
    supporting_evidence: tuple[ProcessEvidenceItem, ...]
    contradicting_evidence: tuple[ProcessEvidenceItem, ...]
    neutral_evidence: tuple[ProcessEvidenceItem, ...]
    available_evidence_families: frozenset[ProcessEvidenceFamily]
    missing_evidence_families: frozenset[ProcessEvidenceFamily]
    insufficiency_reasons: tuple[str, ...]
    evidence_strength: EvidenceStrength
    uncertainty_level: UncertaintyLevel
    schema_version: str = PROCESS_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        # freeze_value deliberately preserves sets, so make family collections immutable here.
        object.__setattr__(self, "available_evidence_families", frozenset(self.available_evidence_families))
        object.__setattr__(self, "missing_evidence_families", frozenset(self.missing_evidence_families))
        for name in ("episode_id", "runtime_event_id", "exchange", "symbol", "timeframe",
                     "process_summary", "schema_version"):
            _require_non_empty(name, getattr(self, name))
        _require_aware("observation_timestamp", self.observation_timestamp)
        if not isinstance(self.current_process_state, ProcessState):
            raise ValueError("current_process_state must be a ProcessState.")
        if not isinstance(self.process_direction, ProcessDirection):
            raise ValueError("process_direction must be a ProcessDirection.")
        if self.previous_process_state is not None and not isinstance(self.previous_process_state, ProcessState):
            raise ValueError("previous_process_state must be a ProcessState or None.")
        if not isinstance(self.detected_transition, ProcessTransition):
            raise ValueError("detected_transition must be a ProcessTransition.")
        if not isinstance(self.evidence_strength, EvidenceStrength):
            raise ValueError("evidence_strength must describe evidence sufficiency, not final confidence.")
        if not isinstance(self.uncertainty_level, UncertaintyLevel):
            raise ValueError("uncertainty_level must be an UncertaintyLevel.")
        _validate_transition(self.previous_process_state, self.current_process_state,
                             self.detected_transition)
        _validate_items(self)
        _validate_families(self.available_evidence_families, self.missing_evidence_families)
        reasons = tuple(self.insufficiency_reasons)
        if any(not isinstance(reason, str) or not reason.strip() for reason in reasons):
            raise ValueError("insufficiency_reasons must contain non-empty strings.")
        if self.current_process_state is ProcessState.UNKNOWN and not reasons:
            raise ValueError("UNKNOWN requires at least one insufficiency reason.")
        if (self.current_process_state is ProcessState.UNKNOWN
                and not self.missing_evidence_families
                and not self.contradicting_evidence
                and not any(item.relationship is ProcessEvidenceRelationship.UNAVAILABLE
                            for item in self.neutral_evidence)):
            raise ValueError("UNKNOWN must make missing, unavailable, or contradictory evidence explicit.")
        if self.current_process_state is not ProcessState.UNKNOWN and not self.supporting_evidence:
            raise ValueError("A non-UNKNOWN Process state requires supporting evidence.")

    def validate_previous_evidence(self, previous: "ProcessEvidence | None") -> None:
        """Validate explicitly supplied previous context without storing history."""
        if previous is None:
            if self.previous_process_state is not None:
                raise ValueError("Previous Process state requires previous Process evidence.")
            return
        if not isinstance(previous, ProcessEvidence):
            raise ValueError("previous must be ProcessEvidence or None.")
        if previous.episode_id != self.episode_id:
            raise ValueError("Previous Process evidence cannot cross an Episode boundary.")
        if _market_identity(previous) != _market_identity(self):
            raise ValueError("Previous Process evidence market identity must match.")
        if self.previous_process_state is not previous.current_process_state:
            raise ValueError("previous_process_state must match the previous evidence result.")


def _validate_transition(previous: ProcessState | None, current: ProcessState,
                         transition: ProcessTransition) -> None:
    if previous is None:
        expected = ProcessTransition.INITIAL
        if current is not ProcessState.UNKNOWN:
            raise ValueError("An initial Process result must be UNKNOWN.")
    elif previous is current:
        expected = ProcessTransition.UNCHANGED
    elif current is ProcessState.UNKNOWN:
        expected = ProcessTransition.BECAME_UNKNOWN
    elif previous is ProcessState.WEAKENING and current is ProcessState.CONTINUATION_ALIVE:
        expected = ProcessTransition.RECOVERED
    else:
        expected = ProcessTransition.CHANGED
    if transition is not expected:
        raise ValueError(f"detected_transition must be {expected.value} for the supplied states.")


def _validate_items(result: ProcessEvidence) -> None:
    seen: set[tuple[Any, ...]] = set()
    groups = (
        (result.supporting_evidence, ProcessEvidenceRelationship.SUPPORTING, "supporting_evidence"),
        (result.contradicting_evidence, ProcessEvidenceRelationship.CONTRADICTING, "contradicting_evidence"),
        (result.neutral_evidence, None, "neutral_evidence"),
    )
    for items, expected, name in groups:
        if not isinstance(items, tuple):
            raise ValueError(f"{name} must freeze to a tuple.")
        for item in items:
            if not isinstance(item, ProcessEvidenceItem):
                raise ValueError(f"{name} must contain ProcessEvidenceItem values.")
            if expected is not None and item.relationship is not expected:
                raise ValueError(f"{name} contains an item with the wrong relationship.")
            if expected is None and item.relationship not in (
                ProcessEvidenceRelationship.NEUTRAL, ProcessEvidenceRelationship.UNAVAILABLE
            ):
                raise ValueError("neutral_evidence may contain only neutral or unavailable items.")
            identity = (item.evidence_family, item.evidence_key, item.source_module,
                        item.source_field, item.observation_timestamp)
            if identity in seen:
                raise ValueError("Duplicate evidence identity/provenance is not independent evidence.")
            seen.add(identity)


def _validate_families(available: frozenset[ProcessEvidenceFamily],
                       missing: frozenset[ProcessEvidenceFamily]) -> None:
    if any(not isinstance(item, ProcessEvidenceFamily) for item in available | missing):
        raise ValueError("Evidence family collections must contain ProcessEvidenceFamily values.")
    if available & missing:
        raise ValueError("Available and missing evidence families cannot overlap.")


def _market_identity(value: ProcessEvidence) -> tuple[str, str, str]:
    return value.exchange.strip().lower(), value.symbol.strip().upper(), value.timeframe.strip().lower()


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _require_aware(name: str, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


def _require_serializable_value(name: str, value: object) -> None:
    if value is None or isinstance(value, (str, int, float, bool, datetime, Enum)):
        return
    if isinstance(value, tuple):
        for item in value:
            _require_serializable_value(name, item)
        return
    if hasattr(value, "items"):
        for key, item in value.items():
            if not isinstance(key, (str, int, float, bool)):
                raise ValueError(f"{name} mapping keys must be primitive values.")
            _require_serializable_value(name, item)
        return
    raise ValueError(f"{name} must contain only serializable primitive values.")
