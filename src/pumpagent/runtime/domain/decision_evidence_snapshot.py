"""Canonical immutable Decision Evidence Snapshot domain contracts.

The contracts in this module describe Decision audit data only.  They do not
construct Decisions, calculate digests, persist snapshots, or determine
canonical commit disposition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.decision import (
    DecisionAssessment,
    canonical_decision_id,
)

if TYPE_CHECKING:
    from pumpagent.runtime.modules.decision.engine import DecisionEngineInput


DECISION_EVIDENCE_SNAPSHOT_SCHEMA_VERSION = "decision_evidence_snapshot_v1"
SNAPSHOT_INTEGRITY_METADATA_SCHEMA_VERSION = "snapshot_integrity_metadata_v1"


class DecisionEvidenceRelationship(str, Enum):
    AVAILABLE = "available"
    ADMITTED = "admitted"
    CONSIDERED = "considered"
    CONTRIBUTING = "contributing"
    CONTRADICTING = "contradicting"
    INHIBITING = "inhibiting"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    AVAILABILITY_NOT_PROVEN = "availability_not_proven"
    CONTEXT_ONLY = "context_only"
    POST_DECISION_DIAGNOSTIC = "post_decision_diagnostic"


class DecisionEvidenceArtifactType(str, Enum):
    MARKET_SNAPSHOT = "market_snapshot"
    STRUCTURAL_EVIDENCE = "structural_evidence"
    MARKET_EFFICIENCY_EVIDENCE = "market_efficiency_evidence"
    PROCESS_EVIDENCE = "process_evidence"
    PROCESS_QUALITY_ASSESSMENT = "process_quality_assessment"
    HYPOTHESIS_PACKAGE = "hypothesis_package"
    AGENT_STATE = "agent_state"
    SCENARIO_PROBABILITY = "scenario_probability"
    CONFIDENCE_ASSESSMENT = "confidence_assessment"
    HEALTHY_BASELINE = "healthy_baseline"
    PREVIOUS_DECISION = "previous_decision"
    DECISION_ASSESSMENT = "decision_assessment"
    OTHER_CONTEXT = "other_context"


class SnapshotIntegrityStatus(str, Enum):
    PRE_DIGEST = "pre_digest"
    DIGEST_RECORDED = "digest_recorded"


@dataclass(frozen=True)
class DecisionEvidenceDescriptor(SerializableMixin):
    """Typed evidence coordinates and decision-time relationship semantics."""

    artifact_type: DecisionEvidenceArtifactType
    artifact_id: str
    episode_id: str
    runtime_event_id: str
    relationships: frozenset[DecisionEvidenceRelationship]
    observation_timestamp: datetime | None = None
    available_at: datetime | None = None
    payload_present: bool = False
    payload: Any | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.artifact_type, DecisionEvidenceArtifactType):
            raise ValueError("artifact_type must be a DecisionEvidenceArtifactType.")
        for name in ("artifact_id", "episode_id", "runtime_event_id"):
            _require_non_empty(name, getattr(self, name))
        relationships = self.relationships
        if not isinstance(relationships, frozenset) or not relationships:
            raise ValueError("relationships must be a non-empty frozen set.")
        if any(not isinstance(item, DecisionEvidenceRelationship) for item in relationships):
            raise ValueError(
                "relationships must contain DecisionEvidenceRelationship values."
            )
        _require_optional_aware("observation_timestamp", self.observation_timestamp)
        _require_optional_aware("available_at", self.available_at)
        if not isinstance(self.payload_present, bool):
            raise ValueError("payload_present must be a bool.")
        if not self.payload_present and self.payload is not None:
            raise ValueError("payload requires payload_present=True.")
        _require_immutable_payload("payload", self.payload)
        _validate_relationships(self)


@dataclass(frozen=True)
class DecisionEvidenceSnapshotContent(SerializableMixin):
    """Immutable content covered by future canonical integrity serialization."""

    snapshot_id: str
    episode_id: str
    runtime_event_id: str
    decision_id: str
    decision_policy_version: str
    observation_timestamp: datetime
    snapshot_created_at: datetime
    decision_input: DecisionEngineInput
    decision_assessment: DecisionAssessment
    evidence: tuple[DecisionEvidenceDescriptor, ...]
    schema_version: str = DECISION_EVIDENCE_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in (
            "snapshot_id",
            "episode_id",
            "runtime_event_id",
            "decision_id",
            "decision_policy_version",
            "schema_version",
        ):
            _require_non_empty(name, getattr(self, name))
        if self.schema_version != DECISION_EVIDENCE_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("Unsupported Decision Evidence Snapshot schema_version.")
        _require_aware("observation_timestamp", self.observation_timestamp)
        _require_aware("snapshot_created_at", self.snapshot_created_at)
        _validate_input_output(self)
        if self.snapshot_created_at < self.decision_assessment.created_at:
            raise ValueError(
                "snapshot_created_at cannot precede Decision creation."
            )
        _validate_evidence(self)


@dataclass(frozen=True)
class SnapshotIntegrityMetadata(SerializableMixin):
    """Integrity envelope shape; digest calculation is intentionally external."""

    integrity_status: SnapshotIntegrityStatus
    canonicalization_version: str | None = None
    digest_algorithm: str | None = None
    digest_value: str | None = None
    schema_version: str = SNAPSHOT_INTEGRITY_METADATA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _require_non_empty("schema_version", self.schema_version)
        if self.schema_version != SNAPSHOT_INTEGRITY_METADATA_SCHEMA_VERSION:
            raise ValueError("Unsupported Snapshot Integrity Metadata schema_version.")
        if not isinstance(self.integrity_status, SnapshotIntegrityStatus):
            raise ValueError("integrity_status must be a SnapshotIntegrityStatus.")
        optional_values = (
            self.canonicalization_version,
            self.digest_algorithm,
            self.digest_value,
        )
        for name, value in zip(
            ("canonicalization_version", "digest_algorithm", "digest_value"),
            optional_values,
        ):
            if value is not None:
                _require_non_empty(name, value)
        if self.integrity_status is SnapshotIntegrityStatus.PRE_DIGEST:
            if any(value is not None for value in optional_values):
                raise ValueError(
                    "PRE_DIGEST integrity metadata cannot contain digest fields."
                )
        elif any(value is None for value in optional_values):
            raise ValueError(
                "DIGEST_RECORDED requires canonicalization, algorithm, and value."
            )


@dataclass(frozen=True)
class DecisionEvidenceSnapshot(SerializableMixin):
    """Canonical audit envelope for one completed Decision."""

    content: DecisionEvidenceSnapshotContent
    integrity: SnapshotIntegrityMetadata

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.content, DecisionEvidenceSnapshotContent):
            raise ValueError("content must be DecisionEvidenceSnapshotContent.")
        if not isinstance(self.integrity, SnapshotIntegrityMetadata):
            raise ValueError("integrity must be SnapshotIntegrityMetadata.")


def canonical_decision_evidence_snapshot_id(decision_id: str) -> str:
    _require_non_empty("decision_id", decision_id)
    return f"decision-evidence-snapshot:{decision_id}"


def _validate_input_output(content: DecisionEvidenceSnapshotContent) -> None:
    # Local import avoids making the domain package initialization depend on a
    # Runtime module while the approved operational contract remains there.
    from pumpagent.runtime.modules.decision.engine import DecisionEngineInput

    value = content.decision_input
    decision = content.decision_assessment
    if not isinstance(value, DecisionEngineInput):
        raise ValueError("decision_input must be a DecisionEngineInput.")
    if not isinstance(decision, DecisionAssessment):
        raise ValueError("decision_assessment must be a DecisionAssessment.")
    process = value.process_evidence
    expected_decision_id = canonical_decision_id(
        process.episode_id,
        process.runtime_event_id,
    )
    expected_snapshot_id = canonical_decision_evidence_snapshot_id(
        expected_decision_id
    )
    if content.episode_id != process.episode_id:
        raise ValueError("Snapshot and Decision input must share one Episode.")
    if content.runtime_event_id != process.runtime_event_id:
        raise ValueError("Snapshot and Decision input must share one Runtime event.")
    if content.decision_id != expected_decision_id or decision.decision_id != expected_decision_id:
        raise ValueError("Decision ID does not align with the embedded input.")
    if content.snapshot_id != expected_snapshot_id:
        raise ValueError("Snapshot ID does not match the canonical Decision-derived ID.")
    if decision.episode_id != content.episode_id:
        raise ValueError("Embedded Decision and snapshot must share one Episode.")
    if decision.runtime_event_id != content.runtime_event_id:
        raise ValueError("Embedded Decision and snapshot must share one Runtime event.")
    expected_references = (
        ("hypothesis_reference", decision.hypothesis_reference, value.hypothesis.hypothesis_id),
        (
            "scenario_probability_reference",
            decision.scenario_probability_reference,
            value.scenario_probability.scenario_probability_id,
        ),
        (
            "process_quality_reference",
            decision.process_quality_reference,
            value.process_quality_assessment.to_reference(),
        ),
        (
            "process_evidence_reference",
            decision.process_evidence_reference,
            f"process-evidence:{content.episode_id}:{content.runtime_event_id}",
        ),
        (
            "confidence_reference",
            decision.confidence_reference,
            f"confidence:{content.episode_id}:{content.runtime_event_id}",
        ),
        (
            "healthy_baseline_reference",
            decision.healthy_baseline_reference,
            value.healthy_baseline_reference,
        ),
        (
            "previous_decision_reference",
            decision.previous_decision_reference,
            value.previous_decision_reference,
        ),
    )
    for name, actual, expected in expected_references:
        if actual != expected:
            raise ValueError(
                f"Decision {name} does not align with the embedded input."
            )
    if content.observation_timestamp != process.observation_timestamp:
        raise ValueError(
            "Snapshot observation time must match Decision input observation time."
        )


def _validate_evidence(content: DecisionEvidenceSnapshotContent) -> None:
    if not isinstance(content.evidence, tuple):
        raise ValueError("evidence must freeze to a tuple.")
    seen: set[tuple[DecisionEvidenceArtifactType, str]] = set()
    for descriptor in content.evidence:
        if not isinstance(descriptor, DecisionEvidenceDescriptor):
            raise ValueError("evidence must contain DecisionEvidenceDescriptor values.")
        if descriptor.episode_id != content.episode_id:
            raise ValueError("Evidence cannot cross the snapshot Episode boundary.")
        if descriptor.runtime_event_id != content.runtime_event_id:
            raise ValueError("Evidence cannot cross the snapshot Runtime event boundary.")
        identity = (descriptor.artifact_type, descriptor.artifact_id)
        if identity in seen:
            raise ValueError("Duplicate evidence descriptors are not permitted.")
        seen.add(identity)


def _validate_relationships(descriptor: DecisionEvidenceDescriptor) -> None:
    relationships = descriptor.relationships
    available = DecisionEvidenceRelationship.AVAILABLE
    admitted = DecisionEvidenceRelationship.ADMITTED
    considered = DecisionEvidenceRelationship.CONSIDERED
    contributing = DecisionEvidenceRelationship.CONTRIBUTING
    contradicting = DecisionEvidenceRelationship.CONTRADICTING
    inhibiting = DecisionEvidenceRelationship.INHIBITING
    missing = DecisionEvidenceRelationship.MISSING
    unavailable = DecisionEvidenceRelationship.UNAVAILABLE
    not_proven = DecisionEvidenceRelationship.AVAILABILITY_NOT_PROVEN
    context_only = DecisionEvidenceRelationship.CONTEXT_ONLY
    post_decision = DecisionEvidenceRelationship.POST_DECISION_DIAGNOSTIC

    if admitted in relationships and available not in relationships:
        raise ValueError("ADMITTED evidence must also be AVAILABLE.")
    if considered in relationships and admitted not in relationships:
        raise ValueError("CONSIDERED evidence must also be ADMITTED.")
    if relationships & {contributing, contradicting, inhibiting} and considered not in relationships:
        raise ValueError(
            "Contributing, contradicting, or inhibiting evidence must be CONSIDERED."
        )
    terminal_unavailability = relationships & {missing, unavailable, not_proven}
    if len(terminal_unavailability) > 1:
        raise ValueError("Evidence cannot have multiple unavailable states.")
    if terminal_unavailability and relationships & {
        available,
        admitted,
        considered,
        contributing,
        contradicting,
        inhibiting,
    }:
        raise ValueError("Unavailable evidence cannot be treated as available or used.")
    if relationships & {missing, unavailable} and descriptor.payload_present:
        raise ValueError("MISSING or UNAVAILABLE evidence cannot contain a payload.")
    if context_only in relationships and relationships & {
        admitted,
        considered,
        contributing,
        contradicting,
        inhibiting,
    }:
        raise ValueError("CONTEXT_ONLY evidence cannot be admitted or used by Decision.")
    if post_decision in relationships and relationships & {
        admitted,
        considered,
        contributing,
        contradicting,
        inhibiting,
    }:
        raise ValueError(
            "POST_DECISION_DIAGNOSTIC cannot be admitted, considered, or used."
        )


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _require_aware(name: str, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


def _require_optional_aware(name: str, value: object) -> None:
    if value is not None:
        _require_aware(name, value)


def _require_immutable_payload(name: str, value: object) -> None:
    if value is None or isinstance(value, (str, int, float, bool, Decimal, Enum)):
        return
    if isinstance(value, datetime):
        _require_aware(name, value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, (str, int, float, bool)):
                raise ValueError(f"{name} mapping keys must be primitive values.")
            _require_immutable_payload(name, item)
        return
    if isinstance(value, (tuple, frozenset)):
        for item in value:
            _require_immutable_payload(name, item)
        return
    raise ValueError(f"{name} must contain only immutable primitive values.")
