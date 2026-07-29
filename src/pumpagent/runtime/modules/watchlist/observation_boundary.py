"""Pure transport boundary between Watchlist context and Observation Policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import (
    ObservationEpisodeStatus,
    ObservationLifecycleDecision,
    ObservationTriggerRelation,
)
from pumpagent.runtime.domain.observation_episode import ObservationEpisodeIdentity
from pumpagent.runtime.domain.observation_policy import (
    ObservationPolicyContext,
    ObservationPolicyDecision,
    ObservationRequest,
    evaluate_observation_policy,
)

if TYPE_CHECKING:
    from pumpagent.runtime.modules.watchlist.manager import WatchlistEntry


WATCHLIST_OBSERVATION_CONTEXT_SCHEMA_VERSION = "watchlist_observation_context_v1"
OBSERVATION_BOUNDARY_INPUT_SCHEMA_VERSION = "observation_boundary_input_v1"
OBSERVATION_BOUNDARY_RESULT_SCHEMA_VERSION = "observation_boundary_result_v1"


@dataclass(frozen=True)
class WatchlistObservationContext(SerializableMixin):
    """Lifecycle-only observation state visible at the Watchlist boundary."""

    exchange: str
    symbol: str
    timeframe: str
    has_active_episode: bool
    active_episode_id: str | None = None
    active_episode_opening_timestamp: datetime | None = None
    latest_accepted_trigger_timestamp: datetime | None = None
    latest_accepted_closed_candle_timestamp: datetime | None = None
    observation_count: int = 0
    lifecycle_status: ObservationEpisodeStatus | None = None
    latest_runtime_event_id: str | None = None
    diagnostic_metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = WATCHLIST_OBSERVATION_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _validate_market_identity(self.exchange, self.symbol, self.timeframe)
        if not isinstance(self.has_active_episode, bool):
            raise ValueError("has_active_episode must be a bool.")
        if self.observation_count < 0:
            raise ValueError("observation_count cannot be negative.")
        if self.lifecycle_status is not None and not isinstance(
            self.lifecycle_status, ObservationEpisodeStatus
        ):
            raise ValueError("lifecycle_status must be an ObservationEpisodeStatus.")
        for name, value in (
            ("active_episode_opening_timestamp", self.active_episode_opening_timestamp),
            ("latest_accepted_trigger_timestamp", self.latest_accepted_trigger_timestamp),
            (
                "latest_accepted_closed_candle_timestamp",
                self.latest_accepted_closed_candle_timestamp,
            ),
        ):
            if value is not None:
                _validate_aware_timestamp(name, value)
        if self.latest_runtime_event_id is not None:
            _validate_non_empty("latest_runtime_event_id", self.latest_runtime_event_id)
        _validate_non_empty("schema_version", self.schema_version)

        active_values = (
            self.active_episode_id,
            self.active_episode_opening_timestamp,
            self.latest_accepted_trigger_timestamp,
            self.latest_accepted_closed_candle_timestamp,
            self.lifecycle_status,
        )
        if self.has_active_episode:
            _validate_non_empty("active_episode_id", self.active_episode_id)
            if self.active_episode_opening_timestamp is None:
                raise ValueError("An active Episode requires an opening timestamp.")
            if self.lifecycle_status not in (None, ObservationEpisodeStatus.ACTIVE):
                raise ValueError("An active Episode cannot have a closed lifecycle status.")
        elif any(value is not None for value in active_values):
            raise ValueError("Absent active Episode cannot contain active Episode fields.")


def build_watchlist_observation_context(
    entry: WatchlistEntry | None,
    *,
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> WatchlistObservationContext:
    """Translate stored Watchlist lifecycle state into its pure boundary context."""

    # The local import avoids coupling the domain boundary's import graph back to
    # the Watchlist manager and lifecycle executor.
    from pumpagent.runtime.modules.watchlist.manager import WatchlistEntry

    if entry is None:
        _validate_market_identity(exchange, symbol, timeframe)
        return WatchlistObservationContext(
            exchange=exchange,  # type: ignore[arg-type]
            symbol=symbol,  # type: ignore[arg-type]
            timeframe=timeframe,  # type: ignore[arg-type]
            has_active_episode=False,
        )
    if not isinstance(entry, WatchlistEntry):
        raise ValueError("entry must be a WatchlistEntry or None.")

    episode = entry.active_episode
    if episode is None:
        if entry.active_episode_id is not None:
            raise ValueError("Inactive Watchlist entry cannot contain an active Episode ID.")
        if entry.lifecycle_status is ObservationEpisodeStatus.ACTIVE:
            raise ValueError("Inactive Watchlist entry cannot have ACTIVE lifecycle status.")
        # Completed-Episode and analytical references are intentionally excluded.
        return WatchlistObservationContext(
            exchange=entry.exchange,
            symbol=entry.symbol,
            timeframe=entry.timeframe,
            has_active_episode=False,
        )

    if episode.status is not ObservationEpisodeStatus.ACTIVE:
        raise ValueError("A closed Episode cannot be mapped as active.")
    if not _same_market(entry, episode):
        raise ValueError("Active Episode market identity must match the Watchlist entry.")
    if entry.active_episode_id != episode.episode_id:
        raise ValueError("Stored active Episode ID must match the Episode contract.")
    if entry.lifecycle_status is not episode.status:
        raise ValueError("Stored lifecycle status must match the active Episode.")
    if entry.latest_accepted_trigger_timestamp is None:
        raise ValueError("An active Episode requires an accepted trigger timestamp.")
    if entry.latest_accepted_trigger_timestamp < episode.scanner_trigger_timestamp:
        raise ValueError("Stored trigger timestamp cannot precede the Episode trigger.")
    if (
        entry.latest_accepted_closed_candle_timestamp
        != episode.latest_accepted_candle_timestamp
    ):
        raise ValueError("Stored closed-candle timestamp must match the active Episode.")
    if entry.observation_count != episode.observation_cycle_count:
        raise ValueError("Stored observation count must match the active Episode.")

    context = WatchlistObservationContext(
        exchange=entry.exchange,
        symbol=entry.symbol,
        timeframe=entry.timeframe,
        has_active_episode=True,
        active_episode_id=episode.episode_id,
        active_episode_opening_timestamp=episode.opening_timestamp,
        latest_accepted_trigger_timestamp=entry.latest_accepted_trigger_timestamp,
        latest_accepted_closed_candle_timestamp=(
            entry.latest_accepted_closed_candle_timestamp
        ),
        observation_count=entry.observation_count,
        lifecycle_status=entry.lifecycle_status,
        latest_runtime_event_id=entry.latest_runtime_event_id,
        diagnostic_metadata=entry.diagnostic_metadata,
    )
    try:
        json.dumps(context.to_dict())
    except (TypeError, ValueError) as exc:
        raise ValueError("Diagnostic metadata must be serializable.") from exc
    return context


@dataclass(frozen=True)
class ObservationBoundaryInput(SerializableMixin):
    """Immutable inputs needed to prepare Observation Policy context."""

    request: ObservationRequest
    watchlist_context: WatchlistObservationContext
    trigger_relation: ObservationTriggerRelation | None = None
    replacement_requested: bool = False
    closure_requested: bool = False
    closure_reason: str | None = None
    schema_version: str = OBSERVATION_BOUNDARY_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.request, ObservationRequest):
            raise ValueError("request must be an ObservationRequest.")
        if not isinstance(self.watchlist_context, WatchlistObservationContext):
            raise ValueError("watchlist_context must be a WatchlistObservationContext.")
        if not isinstance(self.replacement_requested, bool):
            raise ValueError("replacement_requested must be a bool.")
        if not isinstance(self.closure_requested, bool):
            raise ValueError("closure_requested must be a bool.")
        _validate_non_empty("schema_version", self.schema_version)
        # Reuse the policy contract as the single authority for flag/reason rules.
        _policy_context_from_input(self)


@dataclass(frozen=True)
class ObservationBoundaryResult(SerializableMixin):
    """Description of future lifecycle work; it never performs that work."""

    request: ObservationRequest
    policy_context: ObservationPolicyContext
    policy_decision: ObservationPolicyDecision | None
    active_episode_id: str | None
    proposed_lifecycle_action: ObservationLifecycleDecision | None
    create_episode_required: bool = False
    associate_with_active_episode_required: bool = False
    close_episode_required: bool = False
    close_then_open_replacement_required: bool = False
    do_nothing: bool = True
    decision_reason: str = "Policy evaluation not requested."
    schema_version: str = OBSERVATION_BOUNDARY_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.request, ObservationRequest):
            raise ValueError("request must be an ObservationRequest.")
        if not isinstance(self.policy_context, ObservationPolicyContext):
            raise ValueError("policy_context must be an ObservationPolicyContext.")
        _validate_non_empty("decision_reason", self.decision_reason)
        _validate_non_empty("schema_version", self.schema_version)
        if self.active_episode_id is not None:
            _validate_non_empty("active_episode_id", self.active_episode_id)
        flags = (
            self.create_episode_required,
            self.associate_with_active_episode_required,
            self.close_episode_required,
            self.close_then_open_replacement_required,
            self.do_nothing,
        )
        if not all(isinstance(value, bool) for value in flags):
            raise ValueError("Lifecycle requirement flags must be bool values.")
        if self.policy_decision is None:
            if self.proposed_lifecycle_action is not None or any(flags[:-1]):
                raise ValueError("An unevaluated result cannot propose lifecycle work.")
            if not self.do_nothing:
                raise ValueError("An unevaluated result must perform no work.")
            return
        if not isinstance(self.policy_decision, ObservationPolicyDecision):
            raise ValueError("policy_decision must be an ObservationPolicyDecision.")
        decision = self.policy_decision
        expected = (
            decision.create_new_episode,
            decision.associate_with_active_episode,
            decision.decision is ObservationLifecycleDecision.CLOSE,
            decision.decision is ObservationLifecycleDecision.REPLACE,
            decision.decision is ObservationLifecycleDecision.NO_ACTION,
        )
        if self.proposed_lifecycle_action is not decision.decision or flags != expected:
            raise ValueError("Boundary lifecycle requirements must match policy decision.")
        if self.active_episode_id != decision.active_episode_id:
            raise ValueError("active_episode_id must match the policy decision.")
        if self.decision_reason != decision.decision_reason:
            raise ValueError("decision_reason must match the policy decision.")


def prepare_observation_policy_context(
    boundary_input: ObservationBoundaryInput,
) -> ObservationPolicyContext:
    """Prepare policy context without evaluating policy or changing any state."""

    if not isinstance(boundary_input, ObservationBoundaryInput):
        raise ValueError("boundary_input must be an ObservationBoundaryInput.")
    return _policy_context_from_input(boundary_input)


def prepare_observation_boundary(
    boundary_input: ObservationBoundaryInput,
) -> ObservationBoundaryResult:
    """Prepare a serializable boundary result without requesting a decision."""

    context = prepare_observation_policy_context(boundary_input)
    active_id = context.active_episode.episode_id if context.active_episode else None
    return ObservationBoundaryResult(
        request=boundary_input.request,
        policy_context=context,
        policy_decision=None,
        active_episode_id=active_id,
        proposed_lifecycle_action=None,
    )


def evaluate_observation_boundary(
    boundary_input: ObservationBoundaryInput,
) -> ObservationBoundaryResult:
    """Explicitly evaluate policy and describe future executor obligations."""

    context = prepare_observation_policy_context(boundary_input)
    decision = evaluate_observation_policy(boundary_input.request, context)
    return ObservationBoundaryResult(
        request=boundary_input.request,
        policy_context=context,
        policy_decision=decision,
        active_episode_id=decision.active_episode_id,
        proposed_lifecycle_action=decision.decision,
        create_episode_required=decision.create_new_episode,
        associate_with_active_episode_required=decision.associate_with_active_episode,
        close_episode_required=(
            decision.decision is ObservationLifecycleDecision.CLOSE
        ),
        close_then_open_replacement_required=(
            decision.decision is ObservationLifecycleDecision.REPLACE
        ),
        do_nothing=decision.decision is ObservationLifecycleDecision.NO_ACTION,
        decision_reason=decision.decision_reason,
    )


def _policy_context_from_input(
    boundary_input: ObservationBoundaryInput,
) -> ObservationPolicyContext:
    watchlist = boundary_input.watchlist_context
    active = None
    if watchlist.has_active_episode:
        active = ObservationEpisodeIdentity(
            episode_id=watchlist.active_episode_id,  # type: ignore[arg-type]
            exchange=watchlist.exchange,
            symbol=watchlist.symbol,
            timeframe=watchlist.timeframe,
            opening_timestamp=watchlist.active_episode_opening_timestamp,  # type: ignore[arg-type]
        )
    return ObservationPolicyContext(
        active_episode=active,
        trigger_relation=boundary_input.trigger_relation,
        replacement_requested=boundary_input.replacement_requested,
        closure_requested=boundary_input.closure_requested,
        closure_reason=boundary_input.closure_reason,
    )


def _same_market(left: object, right: object) -> bool:
    return (
        getattr(left, "exchange").strip().lower(),
        getattr(left, "symbol").strip().upper(),
        getattr(left, "timeframe").strip().lower(),
    ) == (
        getattr(right, "exchange").strip().lower(),
        getattr(right, "symbol").strip().upper(),
        getattr(right, "timeframe").strip().lower(),
    )


def _validate_market_identity(
    exchange: object, symbol: object, timeframe: object
) -> None:
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
