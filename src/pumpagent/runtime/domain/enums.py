"""Shared enums for runtime domain models."""

from __future__ import annotations

from enum import Enum


class DataQualityStatus(str, Enum):
    VALID = "valid"
    DELAYED = "delayed"
    MISSING = "missing"
    CORRUPTED = "corrupted"


class RuntimeStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ObservationEpisodeStatus(str, Enum):
    """Lifecycle status of an Observation Episode."""

    ACTIVE = "active"
    CLOSED = "closed"


class ObservationLifecycleDecision(str, Enum):
    """Lifecycle-only decisions owned by Observation Policy."""

    OPEN = "open"
    CONTINUE = "continue"
    CLOSE = "close"
    REPLACE = "replace"
    NO_ACTION = "no_action"


class ObservationTriggerRelation(str, Enum):
    """Ordering of an incoming trigger relative to the active Episode."""

    NEWER = "newer"
    DUPLICATE = "duplicate"
    OLDER = "older"


class EvidenceStrength(str, Enum):
    UNKNOWN = "unknown"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class UncertaintyLevel(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentStateType(str, Enum):
    UNKNOWN = "unknown"
    IGNITION = "ignition"
    CONTINUATION_ALIVE = "continuation_alive"
    CONTINUATION_SATURATION = "continuation_saturation"
    FIRST_FAILURE_CANDIDATE = "first_failure_candidate"
    FIRST_FAILURE = "first_failure"
    CONTINUATION_DEATH = "continuation_death"


class ProcessDirection(str, Enum):
    """Observed market-process orientation, never a trading disposition."""

    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class StateTransitionStatus(str, Enum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ConfidenceLevel(str, Enum):
    UNKNOWN = "unknown"
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class DecisionType(str, Enum):
    OBSERVE = "observe"
    WAIT = "wait"
    WARNING = "warning"
    ALERT = "alert"
    REVIEW_REQUIRED = "review_required"
    HUMAN_DECISION_REQUIRED = "human_decision_required"
    UNKNOWN = "unknown"


class AlertLevel(str, Enum):
    NONE = "none"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertCategory(str, Enum):
    NO_ACTION = "no_action"
    WATCH = "watch"
    WARNING = "warning"
    HIGH_ATTENTION = "high_attention"


class ReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    REVIEWED = "reviewed"
