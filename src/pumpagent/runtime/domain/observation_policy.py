"""Pure lifecycle contracts and transition policy for Observation Episodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import (
    DataQualityStatus,
    ObservationLifecycleDecision,
    ObservationTriggerRelation,
)
from pumpagent.runtime.domain.observation_episode import ObservationEpisodeIdentity


OBSERVATION_REQUEST_SCHEMA_VERSION = "observation_request_v1"
OBSERVATION_POLICY_DECISION_SCHEMA_VERSION = "observation_policy_decision_v1"


@dataclass(frozen=True)
class ObservationMarketIdentity(SerializableMixin):
    """Market identity used by lifecycle policy without analytical state."""

    exchange: str
    symbol: str
    timeframe: str

    def __post_init__(self) -> None:
        _validate_market_identity(self.exchange, self.symbol, self.timeframe)


@dataclass(frozen=True)
class ObservationRequest(SerializableMixin):
    """Immutable attention request submitted to Observation Policy."""

    exchange: str
    symbol: str
    timeframe: str
    request_timestamp: datetime
    trigger_timestamp: datetime
    trigger_reasons: tuple[str, ...]
    trigger_metrics: Mapping[str, Any] = field(default_factory=dict)
    data_quality_status: DataQualityStatus = DataQualityStatus.VALID
    eligible: bool = True
    triggering_closed_candle_timestamp: datetime | None = None
    schema_version: str = OBSERVATION_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _validate_market_identity(self.exchange, self.symbol, self.timeframe)
        _validate_aware_timestamp("request_timestamp", self.request_timestamp)
        _validate_aware_timestamp("trigger_timestamp", self.trigger_timestamp)
        if self.triggering_closed_candle_timestamp is not None:
            _validate_aware_timestamp(
                "triggering_closed_candle_timestamp",
                self.triggering_closed_candle_timestamp,
            )
        if not self.trigger_reasons:
            raise ValueError("trigger_reasons must contain at least one reason.")
        for reason in self.trigger_reasons:
            _validate_non_empty("trigger reason", reason)
        if not isinstance(self.data_quality_status, DataQualityStatus):
            raise ValueError("data_quality_status must be a DataQualityStatus.")
        if not isinstance(self.eligible, bool):
            raise ValueError("eligible must be a bool.")
        _validate_non_empty("schema_version", self.schema_version)

    @property
    def market_identity(self) -> ObservationMarketIdentity:
        return ObservationMarketIdentity(self.exchange, self.symbol, self.timeframe)

    @property
    def is_valid_and_eligible(self) -> bool:
        return self.eligible and self.data_quality_status is DataQualityStatus.VALID


@dataclass(frozen=True)
class ObservationPolicyContext(SerializableMixin):
    """Minimal lifecycle-only state supplied to the pure policy function."""

    active_episode: ObservationEpisodeIdentity | None = None
    trigger_relation: ObservationTriggerRelation | None = None
    replacement_requested: bool = False
    closure_requested: bool = False
    closure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.active_episode is None and self.trigger_relation is not None:
            raise ValueError("trigger_relation requires an active Episode.")
        if self.active_episode is not None and self.trigger_relation is None:
            raise ValueError("An active Episode requires trigger_relation.")
        if self.trigger_relation is not None and not isinstance(
            self.trigger_relation, ObservationTriggerRelation
        ):
            raise ValueError("trigger_relation must be an ObservationTriggerRelation.")
        if self.replacement_requested and self.closure_requested:
            raise ValueError("Replacement and closure cannot both be requested.")
        if (self.replacement_requested or self.closure_requested) and (
            self.active_episode is None
        ):
            raise ValueError("Replacement or closure requires an active Episode.")
        if self.replacement_requested or self.closure_requested:
            _validate_non_empty("closure_reason", self.closure_reason)
        elif self.closure_reason is not None:
            raise ValueError("closure_reason requires replacement or closure.")


@dataclass(frozen=True)
class ObservationPolicyDecision(SerializableMixin):
    """Serializable description of a lifecycle decision, with no side effects."""

    decision: ObservationLifecycleDecision
    decision_reason: str
    incoming_market_identity: ObservationMarketIdentity
    request_timestamp: datetime
    active_episode_id: str | None = None
    create_new_episode: bool = False
    close_active_episode_first: bool = False
    associate_with_active_episode: bool = False
    closure_reason: str | None = None
    schema_version: str = OBSERVATION_POLICY_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ObservationLifecycleDecision):
            raise ValueError("decision must be an ObservationLifecycleDecision.")
        _validate_non_empty("decision_reason", self.decision_reason)
        if not isinstance(self.incoming_market_identity, ObservationMarketIdentity):
            raise ValueError(
                "incoming_market_identity must be an ObservationMarketIdentity."
            )
        _validate_aware_timestamp("request_timestamp", self.request_timestamp)
        _validate_non_empty("schema_version", self.schema_version)

        has_active = self.active_episode_id is not None
        if has_active:
            _validate_non_empty("active_episode_id", self.active_episode_id)

        if self.decision is ObservationLifecycleDecision.OPEN:
            if not self.create_new_episode or self.close_active_episode_first:
                raise ValueError("OPEN must create a new Episode without closing one.")
            if has_active or self.associate_with_active_episode:
                raise ValueError("OPEN cannot reference or continue an active Episode.")
        elif self.decision is ObservationLifecycleDecision.CONTINUE:
            if not has_active or not self.associate_with_active_episode:
                raise ValueError("CONTINUE requires association with an active Episode.")
            if self.create_new_episode or self.close_active_episode_first:
                raise ValueError("CONTINUE cannot create or close an Episode.")
        elif self.decision is ObservationLifecycleDecision.CLOSE:
            if not has_active or not self.close_active_episode_first:
                raise ValueError("CLOSE requires an active Episode to close.")
            if self.create_new_episode or self.associate_with_active_episode:
                raise ValueError("CLOSE cannot create or associate incoming evidence.")
            _validate_non_empty("closure_reason", self.closure_reason)
        elif self.decision is ObservationLifecycleDecision.REPLACE:
            if not has_active or not self.close_active_episode_first:
                raise ValueError("REPLACE requires an active Episode to close first.")
            if not self.create_new_episode:
                raise ValueError("REPLACE must create a new Episode.")
            if self.associate_with_active_episode:
                raise ValueError("A replacement trigger cannot belong to the old Episode.")
            _validate_non_empty("closure_reason", self.closure_reason)
        else:
            if self.create_new_episode or self.close_active_episode_first:
                raise ValueError("NO_ACTION cannot create or close an Episode.")
            if self.associate_with_active_episode:
                raise ValueError("NO_ACTION cannot associate incoming evidence.")

        if self.decision not in (
            ObservationLifecycleDecision.CLOSE,
            ObservationLifecycleDecision.REPLACE,
        ) and self.closure_reason is not None:
            raise ValueError("closure_reason is valid only for CLOSE or REPLACE.")


def evaluate_observation_policy(
    request: ObservationRequest,
    context: ObservationPolicyContext,
) -> ObservationPolicyDecision:
    """Return the deterministic lifecycle decision for the supplied inputs."""

    identity = request.market_identity
    active = context.active_episode

    if active is None:
        if request.is_valid_and_eligible:
            return _decision(
                request,
                ObservationLifecycleDecision.OPEN,
                "Eligible request has no active Episode.",
                create_new_episode=True,
            )
        return _decision(
            request,
            ObservationLifecycleDecision.NO_ACTION,
            "Request is invalid or ineligible.",
        )

    same_market = _same_market(identity, active)
    if not same_market:
        return _decision(
            request,
            ObservationLifecycleDecision.NO_ACTION,
            "Incoming market differs from the active Episode.",
            active_episode_id=active.episode_id,
        )

    if context.closure_requested:
        return _decision(
            request,
            ObservationLifecycleDecision.CLOSE,
            "Explicit lifecycle closure requested.",
            active_episode_id=active.episode_id,
            close_active_episode_first=True,
            closure_reason=context.closure_reason,
        )

    if not request.is_valid_and_eligible:
        return _decision(
            request,
            ObservationLifecycleDecision.NO_ACTION,
            "Request is invalid or ineligible.",
            active_episode_id=active.episode_id,
        )

    if context.replacement_requested:
        return _decision(
            request,
            ObservationLifecycleDecision.REPLACE,
            "Explicit lifecycle replacement requested.",
            active_episode_id=active.episode_id,
            create_new_episode=True,
            close_active_episode_first=True,
            closure_reason=context.closure_reason,
        )

    if context.trigger_relation is ObservationTriggerRelation.NEWER:
        return _decision(
            request,
            ObservationLifecycleDecision.CONTINUE,
            "Valid newer request continues the active Episode.",
            active_episode_id=active.episode_id,
            associate_with_active_episode=True,
        )

    return _decision(
        request,
        ObservationLifecycleDecision.NO_ACTION,
        "Duplicate or older request cannot add evidence.",
        active_episode_id=active.episode_id,
    )


def _decision(
    request: ObservationRequest,
    decision: ObservationLifecycleDecision,
    decision_reason: str,
    **values: object,
) -> ObservationPolicyDecision:
    return ObservationPolicyDecision(
        decision=decision,
        decision_reason=decision_reason,
        incoming_market_identity=request.market_identity,
        request_timestamp=request.request_timestamp,
        **values,  # type: ignore[arg-type]
    )


def _same_market(
    incoming: ObservationMarketIdentity,
    active: ObservationEpisodeIdentity,
) -> bool:
    return (
        incoming.exchange.strip().lower() == active.exchange.strip().lower()
        and incoming.symbol.strip().upper() == active.symbol.strip().upper()
        and incoming.timeframe.strip().lower() == active.timeframe.strip().lower()
    )


def _validate_market_identity(exchange: str, symbol: str, timeframe: str) -> None:
    _validate_non_empty("exchange", exchange)
    _validate_non_empty("symbol", symbol)
    _validate_non_empty("timeframe", timeframe)


def _validate_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _validate_aware_timestamp(name: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
