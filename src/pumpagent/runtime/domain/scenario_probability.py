"""Canonical immutable Scenario Probability domain contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import UncertaintyLevel
from pumpagent.runtime.domain.hypothesis_package import HypothesisSemanticCode


SCENARIO_PROBABILITY_POLICY_VERSION = "scenario_probability_policy_v1"
SCENARIO_PROBABILITY_SCHEMA_VERSION = "scenario_probability_v1"
SCENARIO_PROVENANCE_SCHEMA_VERSION = "scenario_provenance_v1"
SCENARIO_PROBABILITY_SCALE = Decimal("0.000001")
SCENARIO_DOMINANCE_MARGIN = Decimal("0.150000")


class ScenarioIdentifier(str, Enum):
    CONTINUE_OBSERVATION = "continue_observation"
    CONTINUATION_PERSISTS = "continuation_persists"
    SATURATION_PERSISTS = "saturation_persists"
    FAILURE_CANDIDATE_PERSISTS = "failure_candidate_persists"
    FIRST_FAILURE_CONFIRMS = "first_failure_confirms"


CANONICAL_SCENARIO_ORDER = tuple(ScenarioIdentifier)


class ScenarioAssessmentStatus(str, Enum):
    COMPLETED = "completed"
    INHIBITED = "inhibited"


class ScenarioArtifactType(str, Enum):
    PROCESS_EVIDENCE = "process_evidence"
    PROCESS_QUALITY = "process_quality"
    HYPOTHESIS = "hypothesis"
    HEALTHY_BASELINE = "healthy_baseline"
    PREVIOUS_SCENARIO_PROBABILITY = "previous_scenario_probability"


class ScenarioReasonCode(str, Enum):
    PROCESS_CONTINUATION_ALIVE = "process_continuation_alive"
    PROCESS_WEAKENING = "process_weakening"
    PROCESS_RECOVERED = "process_recovered"
    HEALTHY_ACTIVE_PROCESS_SUPPORTED = "healthy_active_process_supported"
    HEALTHY_ACTIVE_PROCESS_NOT_ESTABLISHED = (
        "healthy_active_process_not_established"
    )
    LOSS_OF_EFFICIENCY_SUPPORTED = "loss_of_efficiency_supported"
    LOSS_OF_EFFICIENCY_NOT_ESTABLISHED = (
        "loss_of_efficiency_not_established"
    )
    CONTINUATION_HYPOTHESIS_ALIGNED = "continuation_hypothesis_aligned"
    WEAKENING_HYPOTHESIS_ALIGNED = "weakening_hypothesis_aligned"
    RECOVERY_HYPOTHESIS_ALIGNED = "recovery_hypothesis_aligned"
    PROCESS_DIRECTION_DOWN = "process_direction_down"
    HEALTHY_BASELINE_AUTHENTICATED = "healthy_baseline_authenticated"
    HEALTHY_BASELINE_NOT_REQUIRED = "healthy_baseline_not_required"
    MISSING_HEALTHY_BASELINE = "missing_healthy_baseline"
    BASELINE_REFERENCE_MISMATCH = "baseline_reference_mismatch"
    FIRST_BEARISH_TRANSITION = "first_bearish_transition"
    PERSISTENT_BEARISH_TRANSITION = "persistent_bearish_transition"
    PREVIOUS_FAILURE_CANDIDATE_AUTHENTICATED = (
        "previous_failure_candidate_authenticated"
    )
    BEARISH_CONTINUATION_CONFIRMED = "bearish_continuation_confirmed"
    MISSING_REQUIRED_SCENARIO_CONTINUITY = (
        "missing_required_scenario_continuity"
    )
    INVALID_TEMPORAL_CONTINUITY = "invalid_temporal_continuity"
    CURRENT_CANDIDATE_USED_AS_HISTORY = "current_candidate_used_as_history"
    HYPOTHESIS_UNRESOLVED = "hypothesis_unresolved"
    HYPOTHESIS_MISALIGNED = "hypothesis_misaligned"
    HYPOTHESIS_CONFIDENCE_BELOW_THRESHOLD = (
        "hypothesis_confidence_below_threshold"
    )
    PROCESS_UNCERTAINTY_BLOCKING = "process_uncertainty_blocking"
    PROCESS_QUALITY_UNCERTAINTY_BLOCKING = (
        "process_quality_uncertainty_blocking"
    )
    HYPOTHESIS_UNCERTAINTY_BLOCKING = "hypothesis_uncertainty_blocking"
    PROCESS_EVIDENCE_STRENGTH_INSUFFICIENT = (
        "process_evidence_strength_insufficient"
    )
    UPSTREAM_INHIBITION = "upstream_inhibition"
    MISSING_REQUIRED_PREREQUISITE = "missing_required_prerequisite"
    CONFLICTING_AUTHENTICATED_CONCLUSIONS = (
        "conflicting_authenticated_conclusions"
    )
    PRIMARY_SCENARIO_QUALIFIED = "primary_scenario_qualified"
    SCENARIO_NOT_UNIQUELY_DOMINANT = "scenario_not_uniquely_dominant"
    SCENARIO_HIGHEST_WEIGHT_TIE = "scenario_highest_weight_tie"
    CONTINUE_OBSERVATION_FALLBACK = "continue_observation_fallback"


class ScenarioValidationCode(str, Enum):
    INVALID_EPISODE_IDENTITY = "invalid_episode_identity"
    INVALID_RUNTIME_EVENT_IDENTITY = "invalid_runtime_event_identity"
    INVALID_SOURCE_IDENTITY = "invalid_source_identity"
    CROSS_EPISODE_HISTORY = "cross_episode_history"
    FUTURE_HISTORY = "future_history"
    STALE_HISTORY = "stale_history"
    DUPLICATE_PROVENANCE = "duplicate_provenance"
    MISSING_PROVENANCE = "missing_provenance"
    INVALID_SCENARIO_IDENTIFIER = "invalid_scenario_identifier"
    DUPLICATE_SCENARIO_IDENTIFIER = "duplicate_scenario_identifier"
    INVALID_PROBABILITY_RANGE = "invalid_probability_range"
    INVALID_PROBABILITY_SUM = "invalid_probability_sum"
    INVALID_POLICY_VERSION = "invalid_policy_version"


def canonical_scenario_probability_id(
    episode_id: str,
    runtime_event_id: str,
    source_hypothesis_id: str,
) -> str:
    """Return the sole canonical Scenario Probability identity."""

    for name, value in (
        ("episode_id", episode_id),
        ("runtime_event_id", runtime_event_id),
        ("source_hypothesis_id", source_hypothesis_id),
    ):
        _require_non_empty(name, value)
    return (
        f"scenario-probability:{episode_id}:{runtime_event_id}:"
        f"{source_hypothesis_id}"
    )


def canonical_process_evidence_id(episode_id: str, runtime_event_id: str) -> str:
    """Return the canonical Process Evidence reference identity."""

    _require_non_empty("episode_id", episode_id)
    _require_non_empty("runtime_event_id", runtime_event_id)
    return f"process-evidence:{episode_id}:{runtime_event_id}"


@dataclass(frozen=True)
class ScenarioProvenanceReference(SerializableMixin):
    artifact_type: ScenarioArtifactType
    artifact_id: str
    episode_id: str
    runtime_event_id: str
    observation_timestamp: datetime
    schema_version: str = SCENARIO_PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.artifact_type, ScenarioArtifactType):
            raise ValueError("artifact_type must be a ScenarioArtifactType.")
        for name in (
            "artifact_id",
            "episode_id",
            "runtime_event_id",
            "schema_version",
        ):
            _require_non_empty(name, getattr(self, name))
        _require_aware("observation_timestamp", self.observation_timestamp)
        if self.schema_version != SCENARIO_PROVENANCE_SCHEMA_VERSION:
            raise ValueError("Unsupported Scenario provenance schema_version.")


@dataclass(frozen=True)
class ScenarioWeight(SerializableMixin):
    scenario: ScenarioIdentifier
    probability: Decimal

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.scenario, ScenarioIdentifier):
            raise ValueError("scenario must be a ScenarioIdentifier.")
        if not isinstance(self.probability, Decimal):
            raise ValueError("probability must be a Decimal.")
        if not self.probability.is_finite():
            raise ValueError("probability must be finite.")
        if self.probability.as_tuple().exponent != -6:
            raise ValueError("probability must contain exactly six fractional digits.")
        if not Decimal("0.000000") <= self.probability <= Decimal("1.000000"):
            raise ValueError("probability must be between 0.000000 and 1.000000.")


@dataclass(frozen=True)
class ScenarioProbability(SerializableMixin):
    scenario_probability_id: str
    episode_id: str
    runtime_event_id: str
    observation_timestamp: datetime
    created_at: datetime
    source_process_evidence_id: str
    source_process_quality_assessment_id: str
    source_hypothesis_id: str
    source_healthy_baseline_id: str | None
    previous_scenario_probability_id: str | None
    hypothesis_semantic_code: HypothesisSemanticCode
    status: ScenarioAssessmentStatus
    distribution: tuple[ScenarioWeight, ...]
    primary_scenario: ScenarioIdentifier
    uncertainty: UncertaintyLevel
    reason_codes: tuple[ScenarioReasonCode, ...]
    supporting_provenance: tuple[ScenarioProvenanceReference, ...]
    contradicting_provenance: tuple[ScenarioProvenanceReference, ...]
    missing_prerequisites: tuple[ScenarioReasonCode, ...]
    policy_version: str = SCENARIO_PROBABILITY_POLICY_VERSION
    schema_version: str = SCENARIO_PROBABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in (
            "scenario_probability_id",
            "episode_id",
            "runtime_event_id",
            "source_process_evidence_id",
            "source_process_quality_assessment_id",
            "source_hypothesis_id",
            "policy_version",
            "schema_version",
        ):
            _require_non_empty(name, getattr(self, name))
        for name in (
            "source_healthy_baseline_id",
            "previous_scenario_probability_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_non_empty(name, value)
        _require_aware("observation_timestamp", self.observation_timestamp)
        _require_aware("created_at", self.created_at)
        if self.created_at != self.observation_timestamp:
            raise ValueError("created_at must equal observation_timestamp.")
        if not isinstance(self.hypothesis_semantic_code, HypothesisSemanticCode):
            raise ValueError(
                "hypothesis_semantic_code must be a HypothesisSemanticCode."
            )
        if not isinstance(self.status, ScenarioAssessmentStatus):
            raise ValueError("status must be a ScenarioAssessmentStatus.")
        if not isinstance(self.primary_scenario, ScenarioIdentifier):
            raise ValueError("primary_scenario must be a ScenarioIdentifier.")
        if not isinstance(self.uncertainty, UncertaintyLevel):
            raise ValueError("uncertainty must be an UncertaintyLevel.")
        if self.policy_version != SCENARIO_PROBABILITY_POLICY_VERSION:
            raise ValueError("Unsupported Scenario Probability policy_version.")
        if self.schema_version != SCENARIO_PROBABILITY_SCHEMA_VERSION:
            raise ValueError("Unsupported Scenario Probability schema_version.")
        _validate_identity(self)
        _validate_distribution(self)
        _validate_reason_codes(self)
        _validate_provenance(self)


def _validate_identity(value: ScenarioProbability) -> None:
    expected_id = canonical_scenario_probability_id(
        value.episode_id,
        value.runtime_event_id,
        value.source_hypothesis_id,
    )
    if value.scenario_probability_id != expected_id:
        raise ValueError(
            "Scenario Probability identity does not match the canonical formula."
        )
    expected_process_id = canonical_process_evidence_id(
        value.episode_id,
        value.runtime_event_id,
    )
    if value.source_process_evidence_id != expected_process_id:
        raise ValueError(
            "Process Evidence identity does not match the canonical formula."
        )
    if value.previous_scenario_probability_id == value.scenario_probability_id:
        raise ValueError(
            "Current Scenario Probability cannot reference itself as history."
        )


def _validate_distribution(value: ScenarioProbability) -> None:
    if not isinstance(value.distribution, tuple):
        raise ValueError("distribution must freeze to a tuple.")
    if any(not isinstance(item, ScenarioWeight) for item in value.distribution):
        raise ValueError("distribution must contain ScenarioWeight values.")
    scenarios = tuple(item.scenario for item in value.distribution)
    if len(set(scenarios)) != len(scenarios):
        raise ValueError("distribution contains duplicate scenario identifiers.")
    if scenarios != CANONICAL_SCENARIO_ORDER:
        raise ValueError(
            "distribution must contain the complete canonical scenario set in order."
        )
    if sum(
        (item.probability for item in value.distribution),
        start=Decimal("0.000000"),
    ) != Decimal("1.000000"):
        raise ValueError("Scenario probabilities must sum exactly to 1.000000.")
    probabilities = {
        item.scenario: item.probability for item in value.distribution
    }
    primary_probability = probabilities[value.primary_scenario]
    alternatives = tuple(
        probability
        for scenario, probability in probabilities.items()
        if scenario is not value.primary_scenario
    )
    second_highest = max(alternatives)
    if primary_probability <= second_highest:
        raise ValueError("primary_scenario must have the unique highest probability.")
    if primary_probability - second_highest < SCENARIO_DOMINANCE_MARGIN:
        raise ValueError(
            "primary_scenario must satisfy the canonical dominance margin."
        )


def _validate_reason_codes(value: ScenarioProbability) -> None:
    if not value.reason_codes:
        raise ValueError("reason_codes must be non-empty.")
    _validate_enum_tuple(
        "reason_codes",
        value.reason_codes,
        ScenarioReasonCode,
    )
    _validate_enum_tuple(
        "missing_prerequisites",
        value.missing_prerequisites,
        ScenarioReasonCode,
    )


def _validate_provenance(value: ScenarioProbability) -> None:
    references = value.supporting_provenance + value.contradicting_provenance
    if any(not isinstance(item, ScenarioProvenanceReference) for item in references):
        raise ValueError(
            "Scenario provenance collections must contain "
            "ScenarioProvenanceReference values."
        )
    identities = tuple(
        (
            item.artifact_type,
            item.artifact_id,
            item.episode_id,
            item.runtime_event_id,
        )
        for item in references
    )
    if len(set(identities)) != len(identities):
        raise ValueError("Scenario provenance identities must be unique.")
    if any(item.episode_id != value.episode_id for item in references):
        raise ValueError("Scenario provenance cannot cross Episode boundaries.")

    expected_current = {
        ScenarioArtifactType.PROCESS_EVIDENCE: value.source_process_evidence_id,
        ScenarioArtifactType.PROCESS_QUALITY: (
            value.source_process_quality_assessment_id
        ),
        ScenarioArtifactType.HYPOTHESIS: value.source_hypothesis_id,
    }
    for artifact_type, artifact_id in expected_current.items():
        matches = tuple(
            item
            for item in references
            if item.artifact_type is artifact_type
        )
        if len(matches) != 1:
            raise ValueError(
                f"Exactly one {artifact_type.value} provenance reference is required."
            )
        reference = matches[0]
        if reference.artifact_id != artifact_id:
            raise ValueError(
                f"{artifact_type.value} provenance identity does not match its source."
            )
        if reference.runtime_event_id != value.runtime_event_id:
            raise ValueError(
                f"{artifact_type.value} provenance must use the current Runtime event."
            )
        if reference.observation_timestamp != value.observation_timestamp:
            raise ValueError(
                f"{artifact_type.value} provenance must use the current observation."
            )

    _validate_optional_historical_provenance(
        value,
        references,
        artifact_type=ScenarioArtifactType.HEALTHY_BASELINE,
        artifact_id=value.source_healthy_baseline_id,
    )
    _validate_optional_historical_provenance(
        value,
        references,
        artifact_type=ScenarioArtifactType.PREVIOUS_SCENARIO_PROBABILITY,
        artifact_id=value.previous_scenario_probability_id,
    )
    if (
        value.primary_scenario is not ScenarioIdentifier.CONTINUE_OBSERVATION
        and not value.supporting_provenance
    ):
        raise ValueError(
            "A directional primary scenario requires supporting provenance."
        )


def _validate_optional_historical_provenance(
    value: ScenarioProbability,
    references: tuple[ScenarioProvenanceReference, ...],
    *,
    artifact_type: ScenarioArtifactType,
    artifact_id: str | None,
) -> None:
    matches = tuple(
        item for item in references if item.artifact_type is artifact_type
    )
    if artifact_id is None:
        if matches:
            raise ValueError(
                f"{artifact_type.value} provenance requires its source identity."
            )
        return
    if len(matches) != 1:
        raise ValueError(
            f"Exactly one {artifact_type.value} provenance reference is required."
        )
    reference = matches[0]
    if reference.artifact_id != artifact_id:
        raise ValueError(
            f"{artifact_type.value} provenance identity does not match its source."
        )
    if reference.runtime_event_id == value.runtime_event_id:
        raise ValueError(
            f"{artifact_type.value} provenance must precede the current Runtime event."
        )
    if reference.observation_timestamp >= value.observation_timestamp:
        raise ValueError(
            f"{artifact_type.value} provenance must precede the current observation."
        )


def _validate_enum_tuple(
    name: str,
    values: tuple[ScenarioReasonCode, ...],
    enum_type: type[ScenarioReasonCode],
) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{name} must freeze to a tuple.")
    if any(not isinstance(item, enum_type) for item in values):
        raise ValueError(f"{name} must contain {enum_type.__name__} values.")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must contain unique values.")


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _require_aware(name: str, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime.")
