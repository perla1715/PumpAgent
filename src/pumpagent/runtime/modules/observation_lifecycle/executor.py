"""Pure executor for already-authorized Observation Episode transitions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from pumpagent.runtime.domain.base import SerializableMixin
from pumpagent.runtime.domain.enums import (
    ObservationEpisodeStatus,
    ObservationLifecycleDecision,
)
from pumpagent.runtime.domain.observation_episode import (
    ObservationEpisode,
    generate_episode_id,
)
from pumpagent.runtime.modules.watchlist.observation_boundary import (
    ObservationBoundaryResult,
)


OBSERVATION_LIFECYCLE_EXECUTION_INPUT_SCHEMA_VERSION = (
    "observation_lifecycle_execution_input_v1"
)
OBSERVATION_LIFECYCLE_EXECUTION_RESULT_SCHEMA_VERSION = (
    "observation_lifecycle_execution_result_v1"
)


@dataclass(frozen=True)
class ObservationLifecycleExecutionInput(SerializableMixin):
    """Smallest state required to execute an approved boundary result."""

    boundary_result: ObservationBoundaryResult
    active_episode: ObservationEpisode | None
    execution_timestamp: datetime
    schema_version: str = OBSERVATION_LIFECYCLE_EXECUTION_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.boundary_result, ObservationBoundaryResult):
            raise ValueError("boundary_result must be an ObservationBoundaryResult.")
        if self.boundary_result.policy_decision is None:
            raise ValueError("boundary_result must contain an approved policy decision.")
        if self.active_episode is not None and not isinstance(
            self.active_episode, ObservationEpisode
        ):
            raise ValueError("active_episode must be an ObservationEpisode.")
        _validate_aware_timestamp("execution_timestamp", self.execution_timestamp)
        _validate_non_empty("schema_version", self.schema_version)

        boundary = self.boundary_result
        if self.execution_timestamp < boundary.request.request_timestamp:
            raise ValueError(
                "execution_timestamp cannot precede the triggering request timestamp."
            )
        if self.execution_timestamp < boundary.request.trigger_timestamp:
            raise ValueError(
                "execution_timestamp cannot precede the Scanner trigger timestamp."
            )

        action = boundary.proposed_lifecycle_action
        requires_active = action in (
            ObservationLifecycleDecision.CONTINUE,
            ObservationLifecycleDecision.CLOSE,
            ObservationLifecycleDecision.REPLACE,
        )
        if action is ObservationLifecycleDecision.OPEN and self.active_episode is not None:
            raise ValueError("OPEN cannot receive an active Episode.")
        if requires_active and self.active_episode is None:
            raise ValueError(f"{action.value.upper()} requires an active Episode.")

        context_identity = boundary.policy_context.active_episode
        if self.active_episode is None:
            if boundary.active_episode_id is not None:
                raise ValueError("Boundary active Episode requires supplied active state.")
            return

        episode = self.active_episode
        if episode.status is not ObservationEpisodeStatus.ACTIVE:
            raise ValueError("Supplied active Episode must have ACTIVE status.")
        if boundary.active_episode_id != episode.episode_id:
            raise ValueError("Active Episode ID must match the boundary active ID.")
        if context_identity is None or context_identity.episode_id != episode.episode_id:
            raise ValueError("Boundary policy context must identify the active Episode.")
        if not _same_market(episode, context_identity):
            raise ValueError("Active Episode market identity must match the boundary context.")
        if episode.opening_timestamp != context_identity.opening_timestamp:
            raise ValueError("Active Episode opening timestamp must match the boundary context.")


@dataclass(frozen=True)
class ObservationLifecycleExecutionResult(SerializableMixin):
    """Serializable outcome of exactly one authorized lifecycle transition."""

    executed_decision: ObservationLifecycleDecision
    previous_episode: ObservationEpisode | None
    resulting_active_episode: ObservationEpisode | None
    closed_episode: ObservationEpisode | None
    newly_opened_episode: ObservationEpisode | None
    state_changed: bool
    execution_reason: str
    execution_timestamp: datetime
    accepted_trigger_timestamp: datetime | None = None
    replacement_trigger_belongs_only_to_new_episode: bool = False
    schema_version: str = OBSERVATION_LIFECYCLE_EXECUTION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.executed_decision, ObservationLifecycleDecision):
            raise ValueError("executed_decision must be an ObservationLifecycleDecision.")
        _validate_aware_timestamp("execution_timestamp", self.execution_timestamp)
        if self.accepted_trigger_timestamp is not None:
            _validate_aware_timestamp(
                "accepted_trigger_timestamp", self.accepted_trigger_timestamp
            )
        _validate_non_empty("execution_reason", self.execution_reason)
        _validate_non_empty("schema_version", self.schema_version)
        if not isinstance(self.state_changed, bool):
            raise ValueError("state_changed must be a bool.")
        if not isinstance(self.replacement_trigger_belongs_only_to_new_episode, bool):
            raise ValueError("replacement trigger confirmation must be a bool.")
        _validate_result_shape(self)


def execute_observation_lifecycle(
    execution_input: ObservationLifecycleExecutionInput,
) -> ObservationLifecycleExecutionResult:
    """Execute the policy decision verbatim without inspecting market evidence."""

    if not isinstance(execution_input, ObservationLifecycleExecutionInput):
        raise ValueError("execution_input must be an ObservationLifecycleExecutionInput.")

    boundary = execution_input.boundary_result
    action = boundary.proposed_lifecycle_action
    assert action is not None
    previous = execution_input.active_episode
    timestamp = execution_input.execution_timestamp

    if action is ObservationLifecycleDecision.OPEN:
        opened = _open_episode(boundary, timestamp)
        return _result(action, None, opened, None, opened, True, boundary, timestamp)

    if action is ObservationLifecycleDecision.CONTINUE:
        assert previous is not None
        return _result(action, previous, previous, None, None, False, boundary, timestamp)

    if action is ObservationLifecycleDecision.CLOSE:
        assert previous is not None
        closed = _close_episode(previous, boundary.policy_decision.closure_reason, timestamp)  # type: ignore[union-attr]
        return _result(action, previous, None, closed, None, True, boundary, timestamp)

    if action is ObservationLifecycleDecision.REPLACE:
        assert previous is not None
        closed = _close_episode(previous, boundary.policy_decision.closure_reason, timestamp)  # type: ignore[union-attr]
        opened = _open_episode(boundary, timestamp)
        if opened.episode_id == previous.episode_id:
            raise ValueError("REPLACE must generate a new Episode ID.")
        return _result(
            action, previous, opened, closed, opened, True, boundary, timestamp,
            replacement_trigger_belongs_only_to_new_episode=True,
        )

    return _result(action, previous, previous, None, None, False, boundary, timestamp)


def _open_episode(
    boundary: ObservationBoundaryResult, opening_timestamp: datetime
) -> ObservationEpisode:
    request = boundary.request
    return ObservationEpisode(
        episode_id=generate_episode_id(
            request.exchange, request.symbol, request.timeframe, opening_timestamp
        ),
        exchange=request.exchange,
        symbol=request.symbol,
        timeframe=request.timeframe,
        opening_timestamp=opening_timestamp,
        status=ObservationEpisodeStatus.ACTIVE,
        scanner_trigger_timestamp=request.trigger_timestamp,
        trigger_reasons=request.trigger_reasons,
        trigger_metrics=request.trigger_metrics,
        observation_cycle_count=0,
    )


def _close_episode(
    episode: ObservationEpisode,
    closure_reason: str | None,
    closing_timestamp: datetime,
) -> ObservationEpisode:
    if closing_timestamp < episode.opening_timestamp:
        raise ValueError("Closing timestamp cannot precede opening timestamp.")
    return replace(
        episode,
        status=ObservationEpisodeStatus.CLOSED,
        closing_timestamp=closing_timestamp,
        closure_reason=closure_reason,
    )


def _result(
    action: ObservationLifecycleDecision,
    previous: ObservationEpisode | None,
    active: ObservationEpisode | None,
    closed: ObservationEpisode | None,
    opened: ObservationEpisode | None,
    changed: bool,
    boundary: ObservationBoundaryResult,
    timestamp: datetime,
    replacement_trigger_belongs_only_to_new_episode: bool = False,
) -> ObservationLifecycleExecutionResult:
    return ObservationLifecycleExecutionResult(
        executed_decision=action,
        previous_episode=previous,
        resulting_active_episode=active,
        closed_episode=closed,
        newly_opened_episode=opened,
        state_changed=changed,
        execution_reason=boundary.decision_reason,
        execution_timestamp=timestamp,
        accepted_trigger_timestamp=(
            boundary.request.trigger_timestamp
            if action
            in (
                ObservationLifecycleDecision.OPEN,
                ObservationLifecycleDecision.CONTINUE,
                ObservationLifecycleDecision.REPLACE,
            )
            else None
        ),
        replacement_trigger_belongs_only_to_new_episode=(
            replacement_trigger_belongs_only_to_new_episode
        ),
    )


def _validate_result_shape(result: ObservationLifecycleExecutionResult) -> None:
    action = result.executed_decision
    expected_changed = action in (
        ObservationLifecycleDecision.OPEN,
        ObservationLifecycleDecision.CLOSE,
        ObservationLifecycleDecision.REPLACE,
    )
    if result.state_changed is not expected_changed:
        raise ValueError("state_changed must agree with the executed decision.")
    accepts_trigger = action in (
        ObservationLifecycleDecision.OPEN,
        ObservationLifecycleDecision.CONTINUE,
        ObservationLifecycleDecision.REPLACE,
    )
    if accepts_trigger != (result.accepted_trigger_timestamp is not None):
        raise ValueError("accepted_trigger_timestamp must agree with the decision.")
    if action is ObservationLifecycleDecision.OPEN:
        valid = (
            result.previous_episode is None
            and result.closed_episode is None
            and result.newly_opened_episode is result.resulting_active_episode
            and result.resulting_active_episode is not None
        )
    elif action is ObservationLifecycleDecision.CONTINUE:
        valid = (
            result.previous_episode is not None
            and result.resulting_active_episode is result.previous_episode
            and result.closed_episode is None
            and result.newly_opened_episode is None
        )
    elif action is ObservationLifecycleDecision.CLOSE:
        valid = (
            result.previous_episode is not None
            and result.resulting_active_episode is None
            and result.closed_episode is not None
            and result.newly_opened_episode is None
        )
    elif action is ObservationLifecycleDecision.REPLACE:
        valid = (
            result.previous_episode is not None
            and result.resulting_active_episode is result.newly_opened_episode
            and result.closed_episode is not None
            and result.newly_opened_episode is not None
            and result.replacement_trigger_belongs_only_to_new_episode
        )
    else:
        valid = (
            result.resulting_active_episode is result.previous_episode
            and result.closed_episode is None
            and result.newly_opened_episode is None
        )
    if not valid:
        raise ValueError("Episode fields do not match the executed decision.")
    if action is not ObservationLifecycleDecision.REPLACE and (
        result.replacement_trigger_belongs_only_to_new_episode
    ):
        raise ValueError("Replacement trigger confirmation is valid only for REPLACE.")
    for episode in (result.resulting_active_episode, result.newly_opened_episode):
        if episode is not None and episode.status is not ObservationEpisodeStatus.ACTIVE:
            raise ValueError("A resulting or newly opened Episode must be ACTIVE.")
    if result.closed_episode is not None:
        if result.closed_episode.status is not ObservationEpisodeStatus.CLOSED:
            raise ValueError("closed_episode must have CLOSED status.")
        if result.closed_episode.closing_timestamp != result.execution_timestamp:
            raise ValueError("Closed Episode timestamp must equal execution timestamp.")
        previous = result.previous_episode
        if previous is None or (
            result.closed_episode.episode_id != previous.episode_id
            or result.closed_episode.opening_timestamp != previous.opening_timestamp
        ):
            raise ValueError("Closed Episode must preserve its original opening identity.")


def _same_market(left: object, right: object) -> bool:
    return (
        getattr(left, "exchange").strip().lower()
        == getattr(right, "exchange").strip().lower()
        and getattr(left, "symbol").strip().upper()
        == getattr(right, "symbol").strip().upper()
        and getattr(left, "timeframe").strip().lower()
        == getattr(right, "timeframe").strip().lower()
    )


def _validate_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _validate_aware_timestamp(name: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
