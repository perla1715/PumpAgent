"""Post-Runtime completion boundary for one admitted Observation Cycle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
import json
from typing import Any

from pumpagent.runtime.domain.base import (
    SerializableMixin,
    freeze_dataclass_fields,
    to_primitive,
)
from pumpagent.runtime.domain.enums import ObservationEpisodeStatus
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.domain.episode_analytical_context import EpisodeAnalyticalContext
from pumpagent.runtime.modules.observation_lifecycle.cycle_admission import (
    ClosedObservationCycleAdmissionResult,
    CycleAdmissionDecision,
)
from pumpagent.runtime.modules.watchlist.manager import WatchlistEntry


CYCLE_COMPLETION_INPUT_SCHEMA_VERSION = "observation_cycle_completion_input_v1"
CYCLE_COMPLETION_RESULT_SCHEMA_VERSION = "observation_cycle_completion_result_v1"


class CycleCompletionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    NOT_ADMITTED = "NOT_ADMITTED"
    EPISODE_MISMATCH = "EPISODE_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    DUPLICATE_COMPLETION = "DUPLICATE_COMPLETION"
    OLDER_COMPLETION = "OLDER_COMPLETION"
    INVALID_RUNTIME_RESULT = "INVALID_RUNTIME_RESULT"
    INVALID_CONTEXT = "INVALID_CONTEXT"


@dataclass(frozen=True)
class ObservationCycleCompletionInput(SerializableMixin):
    """Immutable facts available after Runtime successfully completes."""

    admission_result: ClosedObservationCycleAdmissionResult
    active_episode: ObservationEpisode
    watchlist_entry: WatchlistEntry
    runtime_event_id: str
    runtime_completion_timestamp: datetime
    accepted_closed_candle_timestamp: datetime
    runtime_diagnostics: Mapping[str, Any] = field(default_factory=dict)
    analytical_context: EpisodeAnalyticalContext | None = None
    schema_version: str = CYCLE_COMPLETION_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.admission_result, ClosedObservationCycleAdmissionResult):
            raise ValueError("admission_result must be a ClosedObservationCycleAdmissionResult.")
        if not isinstance(self.active_episode, ObservationEpisode):
            raise ValueError("active_episode must be an ObservationEpisode.")
        if not isinstance(self.watchlist_entry, WatchlistEntry):
            raise ValueError("watchlist_entry must be a WatchlistEntry.")
        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise ValueError("schema_version must be a non-empty string.")
        try:
            json.dumps(to_primitive(self.runtime_diagnostics))
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime_diagnostics must be serializable.") from exc
        if self.analytical_context is not None and not isinstance(
            self.analytical_context, EpisodeAnalyticalContext
        ):
            raise ValueError("analytical_context must be an EpisodeAnalyticalContext.")


@dataclass(frozen=True)
class ObservationCycleCompletionResult(SerializableMixin):
    status: CycleCompletionStatus
    completed: bool
    episode_id: str | None
    previous_episode: ObservationEpisode
    updated_active_episode: ObservationEpisode | None
    previous_watchlist_entry: WatchlistEntry
    resulting_watchlist_entry: WatchlistEntry | None
    accepted_closed_candle_timestamp: datetime
    runtime_event_id: str
    previous_observation_cycle_count: int
    resulting_observation_cycle_count: int
    previous_latest_accepted_candle_timestamp: datetime | None
    resulting_latest_accepted_candle_timestamp: datetime | None
    completion_reason: str
    completion_timestamp: datetime
    watchlist_state_changed: bool
    schema_version: str = CYCLE_COMPLETION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        expected = self.status is CycleCompletionStatus.COMPLETED
        if self.completed is not expected or self.watchlist_state_changed is not expected:
            raise ValueError("Completion flags must match completion status.")
        if expected:
            if self.updated_active_episode is None or self.resulting_watchlist_entry is None:
                raise ValueError("A completed result requires updated state.")
            if self.resulting_observation_cycle_count != self.previous_observation_cycle_count + 1:
                raise ValueError("A completed cycle must increment the count exactly once.")
        else:
            if self.updated_active_episode is not None or self.resulting_watchlist_entry is not None:
                raise ValueError("A rejected result cannot contain resulting state.")
            if self.resulting_observation_cycle_count != self.previous_observation_cycle_count:
                raise ValueError("A rejected cycle cannot increment the count.")


def prepare_completed_observation_cycle(
    completion_input: ObservationCycleCompletionInput,
) -> ObservationCycleCompletionResult:
    """Validate and immutably prepare storage state without mutating Watchlist."""

    if not isinstance(completion_input, ObservationCycleCompletionInput):
        raise ValueError("completion_input must be an ObservationCycleCompletionInput.")
    value = completion_input
    admission = value.admission_result
    episode = value.active_episode
    entry = value.watchlist_entry
    candle = value.accepted_closed_candle_timestamp

    def reject(status: CycleCompletionStatus, reason: str) -> ObservationCycleCompletionResult:
        return _result(value, status, reason)

    if admission.decision is not CycleAdmissionDecision.ADMIT or not admission.runtime_allowed:
        return reject(CycleCompletionStatus.NOT_ADMITTED, "Only an ADMIT result authorized for Runtime may complete.")
    if episode.status is not ObservationEpisodeStatus.ACTIVE or admission.episode_id != episode.episode_id:
        return reject(CycleCompletionStatus.EPISODE_MISMATCH, "Admission and active Episode do not identify the same ACTIVE Episode.")
    if not _same_identity(admission, episode) or not _same_identity(episode, entry):
        return reject(CycleCompletionStatus.IDENTITY_MISMATCH, "Market identity does not match end to end.")
    if admission.candidate_closed_candle_timestamp != candle:
        return reject(CycleCompletionStatus.INVALID_CONTEXT, "Accepted candle does not match the admitted candle.")
    if not _aware(candle) or not _aware(value.runtime_completion_timestamp):
        return reject(CycleCompletionStatus.INVALID_CONTEXT, "Completion and candle timestamps must be timezone-aware.")
    if value.runtime_completion_timestamp < candle:
        return reject(CycleCompletionStatus.INVALID_CONTEXT, "Runtime completion cannot precede the accepted candle.")
    if value.runtime_completion_timestamp < entry.last_updated:
        return reject(CycleCompletionStatus.INVALID_CONTEXT, "Runtime completion cannot precede the current Watchlist update.")
    if not isinstance(value.runtime_event_id, str) or not value.runtime_event_id.strip():
        return reject(CycleCompletionStatus.INVALID_RUNTIME_RESULT, "Runtime event ID must be non-empty.")
    if not _entry_matches_episode(entry, episode):
        return reject(CycleCompletionStatus.INVALID_CONTEXT, "Watchlist no longer contains the supplied active Episode state.")
    context = value.analytical_context
    if context is not None and (
        context.episode_id != episode.episode_id
        or not _same_identity(context, episode)
        or context.latest_runtime_event_id != value.runtime_event_id
        or context.latest_completed_closed_candle_timestamp != candle
        or context.completed_analytical_cycle_count != episode.observation_cycle_count + 1
    ):
        return reject(CycleCompletionStatus.INVALID_CONTEXT, "Analytical context does not match this completed cycle.")

    previous = episode.latest_accepted_candle_timestamp
    if previous is not None and candle == previous:
        return reject(CycleCompletionStatus.DUPLICATE_COMPLETION, "This candle was already completed for the Episode.")
    if previous is not None and candle < previous:
        return reject(CycleCompletionStatus.OLDER_COMPLETION, "This candle is older than the latest completed candle.")
    if entry.latest_runtime_event_id == value.runtime_event_id:
        return reject(CycleCompletionStatus.INVALID_RUNTIME_RESULT, "Runtime event ID was already used by this Episode.")

    updated_episode = replace(
        episode,
        latest_accepted_candle_timestamp=candle,
        observation_cycle_count=episode.observation_cycle_count + 1,
    )
    updated_entry = replace(
        entry,
        last_updated=value.runtime_completion_timestamp,
        observation_count=updated_episode.observation_cycle_count,
        event_id=value.runtime_event_id,
        hypothesis_id=(
            context.latest_hypothesis.hypothesis_id
            if context is not None and context.latest_hypothesis is not None
            else entry.hypothesis_id
        ),
        active_episode=updated_episode,
        active_episode_id=updated_episode.episode_id,
        lifecycle_status=ObservationEpisodeStatus.ACTIVE,
        latest_accepted_closed_candle_timestamp=candle,
        active_episode_analytical_context=context,
    )
    return _result(
        value,
        CycleCompletionStatus.COMPLETED,
        "The admitted Runtime cycle was recorded for the active Episode.",
        updated_episode=updated_episode,
        updated_entry=updated_entry,
    )


def _result(
    value: ObservationCycleCompletionInput,
    status: CycleCompletionStatus,
    reason: str,
    *,
    updated_episode: ObservationEpisode | None = None,
    updated_entry: WatchlistEntry | None = None,
) -> ObservationCycleCompletionResult:
    episode = value.active_episode
    completed = status is CycleCompletionStatus.COMPLETED
    return ObservationCycleCompletionResult(
        status=status,
        completed=completed,
        episode_id=episode.episode_id,
        previous_episode=episode,
        updated_active_episode=updated_episode,
        previous_watchlist_entry=value.watchlist_entry,
        resulting_watchlist_entry=updated_entry,
        accepted_closed_candle_timestamp=value.accepted_closed_candle_timestamp,
        runtime_event_id=value.runtime_event_id,
        previous_observation_cycle_count=episode.observation_cycle_count,
        resulting_observation_cycle_count=(
            updated_episode.observation_cycle_count if updated_episode else episode.observation_cycle_count
        ),
        previous_latest_accepted_candle_timestamp=episode.latest_accepted_candle_timestamp,
        resulting_latest_accepted_candle_timestamp=(
            updated_episode.latest_accepted_candle_timestamp if updated_episode else episode.latest_accepted_candle_timestamp
        ),
        completion_reason=reason,
        completion_timestamp=value.runtime_completion_timestamp,
        watchlist_state_changed=completed,
    )


def _entry_matches_episode(entry: WatchlistEntry, episode: ObservationEpisode) -> bool:
    return (
        entry.active_episode == episode
        and entry.active_episode_id == episode.episode_id
        and entry.lifecycle_status is ObservationEpisodeStatus.ACTIVE
        and entry.observation_count == episode.observation_cycle_count
        and entry.latest_accepted_closed_candle_timestamp == episode.latest_accepted_candle_timestamp
    )


def _same_identity(left: object, right: object) -> bool:
    return (
        getattr(left, "exchange").strip().lower(),
        getattr(left, "symbol").strip().upper(),
        getattr(left, "timeframe").strip().lower(),
    ) == (
        getattr(right, "exchange").strip().lower(),
        getattr(right, "symbol").strip().upper(),
        getattr(right, "timeframe").strip().lower(),
    )


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
