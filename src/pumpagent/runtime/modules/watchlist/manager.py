"""Dynamic Watchlist MVP.

The watchlist tracks interesting markets in memory only. It does not persist
data, call external services, or expire entries automatically.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pumpagent.runtime.domain import AgentState, HypothesisPackage
from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ObservationEpisodeStatus,
    ObservationLifecycleDecision,
)
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.domain.episode_analytical_context import EpisodeAnalyticalContext

if TYPE_CHECKING:
    from pumpagent.runtime.modules.observation_lifecycle.cycle_completion import (
        ObservationCycleCompletionResult,
    )
    from pumpagent.runtime.modules.observation_lifecycle.executor import (
        ObservationLifecycleExecutionResult,
    )


WATCHLIST_ACTION_REGISTERED = "REGISTERED"
WATCHLIST_ACTION_UPDATED = "UPDATED"
WATCHLIST_ACTION_NONE = "NONE"


@dataclass(frozen=True)
class WatchlistEntry(SerializableMixin):
    symbol: str
    exchange: str
    timeframe: str
    first_seen: datetime
    last_updated: datetime
    current_agent_state: AgentStateType = AgentStateType.UNKNOWN
    hypothesis_id: str | None = None
    confidence: int = 0
    observation_count: int = 0
    event_id: str | None = None
    active_episode: ObservationEpisode | None = None
    latest_completed_episode: ObservationEpisode | None = None
    active_episode_id: str | None = None
    lifecycle_status: ObservationEpisodeStatus | None = None
    latest_accepted_trigger_timestamp: datetime | None = None
    latest_accepted_closed_candle_timestamp: datetime | None = None
    diagnostic_metadata: Mapping[str, Any] = field(default_factory=dict)
    active_episode_analytical_context: EpisodeAnalyticalContext | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        _validate_market_identity(self.exchange, self.symbol, self.timeframe)
        _validate_aware_timestamp("first_seen", self.first_seen)
        _validate_aware_timestamp("last_updated", self.last_updated)
        if self.last_updated < self.first_seen:
            raise ValueError("last_updated cannot precede first_seen.")
        if self.observation_count < 0:
            raise ValueError("observation_count cannot be negative.")
        for name, value in (
            ("latest_accepted_trigger_timestamp", self.latest_accepted_trigger_timestamp),
            (
                "latest_accepted_closed_candle_timestamp",
                self.latest_accepted_closed_candle_timestamp,
            ),
        ):
            if value is not None:
                _validate_aware_timestamp(name, value)
        if self.active_episode is None:
            if self.active_episode_id is not None:
                raise ValueError("active_episode_id requires an active Episode.")
            if self.lifecycle_status is ObservationEpisodeStatus.ACTIVE:
                raise ValueError("ACTIVE lifecycle status requires an active Episode.")
        else:
            if self.active_episode.status is not ObservationEpisodeStatus.ACTIVE:
                raise ValueError("A closed Episode cannot be stored as active.")
            if self.active_episode_id != self.active_episode.episode_id:
                raise ValueError("active_episode_id must match the active Episode.")
            if self.lifecycle_status is not ObservationEpisodeStatus.ACTIVE:
                raise ValueError("An active Episode requires ACTIVE lifecycle status.")
            _validate_episode_market(self, self.active_episode)
            if self.active_episode_analytical_context is not None:
                context = self.active_episode_analytical_context
                if context.episode_id != self.active_episode.episode_id:
                    raise ValueError("Analytical context must match the active Episode ID.")
                if not _same_market(self, context):
                    raise ValueError("Analytical context market identity must match the Watchlist entry.")
                if context.completed_analytical_cycle_count != self.observation_count:
                    raise ValueError("Analytical context count must match observation_count.")
        if self.active_episode is None and self.active_episode_analytical_context is not None:
            raise ValueError("Analytical context requires an active Episode.")
        if self.latest_completed_episode is not None:
            if self.latest_completed_episode.status is not ObservationEpisodeStatus.CLOSED:
                raise ValueError("latest_completed_episode must be closed.")
            _validate_episode_market(self, self.latest_completed_episode)

    @property
    def latest_runtime_event_id(self) -> str | None:
        return self.event_id


class WatchlistManager:
    """In-memory dynamic watchlist manager."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], WatchlistEntry] = {}

    def register(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        timestamp: datetime,
        current_agent_state: AgentStateType,
        hypothesis_id: str,
        confidence: int,
        event_id: str,
    ) -> WatchlistEntry:
        entry = WatchlistEntry(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            first_seen=timestamp,
            last_updated=timestamp,
            current_agent_state=current_agent_state,
            hypothesis_id=hypothesis_id,
            confidence=confidence,
            observation_count=1,
            event_id=event_id,
        )
        self._entries[_key(symbol, exchange, timeframe)] = entry
        return entry

    def update(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        timestamp: datetime,
        current_agent_state: AgentStateType,
        hypothesis_id: str,
        confidence: int,
        event_id: str,
    ) -> WatchlistEntry:
        key = _key(symbol, exchange, timeframe)
        existing = self._entries[key]
        entry = replace(
            existing,
            last_updated=timestamp,
            current_agent_state=current_agent_state,
            hypothesis_id=hypothesis_id,
            confidence=confidence,
            observation_count=existing.observation_count + 1,
            event_id=event_id,
        )
        self._entries[key] = entry
        return entry

    def remove(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> WatchlistEntry | None:
        return self._entries.pop(_key(symbol, exchange, timeframe), None)

    def get(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> WatchlistEntry | None:
        return self._entries.get(_key(symbol, exchange, timeframe))

    def list_active(self) -> tuple[WatchlistEntry, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    def apply_observation_lifecycle_result(
        self,
        result: ObservationLifecycleExecutionResult,
    ) -> WatchlistEntry | None:
        """Store one already-executed lifecycle transition without re-deciding it."""

        from pumpagent.runtime.modules.observation_lifecycle.executor import (
            ObservationLifecycleExecutionResult,
        )

        if not isinstance(result, ObservationLifecycleExecutionResult):
            raise ValueError("result must be an ObservationLifecycleExecutionResult.")
        identity_source = (
            result.resulting_active_episode
            or result.closed_episode
            or result.previous_episode
        )
        if identity_source is None:
            # An absent NO_ACTION contains no market identity and therefore cannot
            # address (or change) any manager entry.
            return None

        key = _key(
            identity_source.symbol,
            identity_source.exchange,
            identity_source.timeframe,
        )
        existing = self._entries.get(key)
        _validate_result_markets(result, identity_source)
        action = result.executed_decision

        if action is ObservationLifecycleDecision.NO_ACTION:
            _require_existing_matches(existing, result.previous_episode, action)
            return existing

        if action is ObservationLifecycleDecision.OPEN:
            if existing is not None and existing.active_episode is not None:
                raise ValueError("OPEN cannot overwrite an active Episode.")
            opened = result.resulting_active_episode
            assert opened is not None
            entry = _entry_for_new_episode(existing, opened, result.execution_timestamp)
        elif action is ObservationLifecycleDecision.CONTINUE:
            _require_existing_matches(existing, result.previous_episode, action)
            assert existing is not None and result.resulting_active_episode is not None
            entry = replace(
                existing,
                last_updated=result.execution_timestamp,
                active_episode=result.resulting_active_episode,
                active_episode_id=result.resulting_active_episode.episode_id,
                lifecycle_status=ObservationEpisodeStatus.ACTIVE,
                latest_accepted_trigger_timestamp=(
                    result.accepted_trigger_timestamp
                ),
                latest_accepted_closed_candle_timestamp=(
                    result.resulting_active_episode.latest_accepted_candle_timestamp
                ),
            )
        elif action is ObservationLifecycleDecision.CLOSE:
            _require_existing_matches(existing, result.previous_episode, action)
            assert existing is not None and result.closed_episode is not None
            entry = replace(
                existing,
                last_updated=result.execution_timestamp,
                active_episode=None,
                active_episode_id=None,
                lifecycle_status=ObservationEpisodeStatus.CLOSED,
                latest_completed_episode=result.closed_episode,
                active_episode_analytical_context=None,
            )
        else:
            _require_existing_matches(existing, result.previous_episode, action)
            assert existing is not None and result.closed_episode is not None
            assert result.resulting_active_episode is not None
            if result.closed_episode.episode_id == result.resulting_active_episode.episode_id:
                raise ValueError("REPLACE requires different old and new Episode IDs.")
            entry = _entry_for_new_episode(
                existing,
                result.resulting_active_episode,
                result.execution_timestamp,
                latest_completed_episode=result.closed_episode,
            )

        self._entries[key] = entry
        return entry

    def apply_completed_observation_cycle(
        self,
        result: ObservationCycleCompletionResult,
    ) -> WatchlistEntry:
        """Atomically store one already-validated successful cycle completion."""

        from pumpagent.runtime.modules.observation_lifecycle.cycle_completion import (
            CycleCompletionStatus,
            ObservationCycleCompletionResult,
        )

        if not isinstance(result, ObservationCycleCompletionResult):
            raise ValueError("result must be an ObservationCycleCompletionResult.")
        if result.status is not CycleCompletionStatus.COMPLETED or not result.completed:
            raise ValueError("Only a completed Observation Cycle result may be applied.")
        previous = result.previous_watchlist_entry
        resulting = result.resulting_watchlist_entry
        if resulting is None or result.updated_active_episode is None:
            raise ValueError("Completed result must contain resulting Watchlist state.")
        key = _key(previous.symbol, previous.exchange, previous.timeframe)
        if self._entries.get(key) != previous:
            raise ValueError("Watchlist state changed after completion was prepared.")
        if not _same_market(previous, resulting):
            raise ValueError("Completion cannot change market identity.")
        if resulting.active_episode != result.updated_active_episode:
            raise ValueError("Resulting Watchlist Episode must match the prepared Episode.")

        self._entries[key] = resulting
        return resulting

    def track_cycle(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        timestamp: datetime,
        agent_state: AgentState,
        hypothesis: HypothesisPackage,
        confidence: int,
        event_id: str,
    ) -> tuple[str, int]:
        # Legacy Runtime-facing analytical tracking remains unchanged. Lifecycle
        # membership is owned exclusively by apply_observation_lifecycle_result.
        if agent_state.current_state == AgentStateType.UNKNOWN:
            return WATCHLIST_ACTION_NONE, 0

        existing = self.get(symbol=symbol, exchange=exchange, timeframe=timeframe)
        if existing is None:
            entry = self.register(
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe,
                timestamp=timestamp,
                current_agent_state=agent_state.current_state,
                hypothesis_id=hypothesis.hypothesis_id,
                confidence=confidence,
                event_id=event_id,
            )
            return WATCHLIST_ACTION_REGISTERED, entry.observation_count

        entry = self.update(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            timestamp=timestamp,
            current_agent_state=agent_state.current_state,
            hypothesis_id=hypothesis.hypothesis_id,
            confidence=confidence,
            event_id=event_id,
        )
        return WATCHLIST_ACTION_UPDATED, entry.observation_count


def _key(symbol: str, exchange: str, timeframe: str) -> tuple[str, str, str]:
    return exchange.strip().lower(), symbol.strip().upper(), timeframe.strip().lower()


def _entry_for_new_episode(
    existing: WatchlistEntry | None,
    episode: ObservationEpisode,
    timestamp: datetime,
    *,
    latest_completed_episode: ObservationEpisode | None = None,
) -> WatchlistEntry:
    """Create a new Episode scope while retaining only market-level identity."""

    first_seen = existing.first_seen if existing is not None else timestamp
    return WatchlistEntry(
        symbol=episode.symbol,
        exchange=episode.exchange,
        timeframe=episode.timeframe,
        first_seen=first_seen,
        last_updated=timestamp,
        current_agent_state=AgentStateType.UNKNOWN,
        hypothesis_id=None,
        confidence=0,
        observation_count=0,
        event_id=None,
        active_episode=episode,
        latest_completed_episode=latest_completed_episode,
        active_episode_id=episode.episode_id,
        lifecycle_status=ObservationEpisodeStatus.ACTIVE,
        latest_accepted_trigger_timestamp=episode.scanner_trigger_timestamp,
        latest_accepted_closed_candle_timestamp=(
            episode.latest_accepted_candle_timestamp
        ),
        diagnostic_metadata={},
        active_episode_analytical_context=None,
    )


def _require_existing_matches(
    existing: WatchlistEntry | None,
    previous: ObservationEpisode | None,
    action: ObservationLifecycleDecision,
) -> None:
    if existing is None or existing.active_episode is None or previous is None:
        raise ValueError(f"{action.value.upper()} requires the stored active Episode.")
    if existing.active_episode_id != previous.episode_id:
        raise ValueError(f"{action.value.upper()} requires the same active Episode.")
    if existing.active_episode != previous:
        raise ValueError(f"{action.value.upper()} previous Episode does not match storage.")


def _validate_result_markets(
    result: ObservationLifecycleExecutionResult,
    identity_source: ObservationEpisode,
) -> None:
    for episode in (
        result.previous_episode,
        result.resulting_active_episode,
        result.closed_episode,
        result.newly_opened_episode,
    ):
        if episode is not None and not _same_market(identity_source, episode):
            raise ValueError("Lifecycle result contains mismatched market identities.")


def _validate_episode_market(entry: WatchlistEntry, episode: ObservationEpisode) -> None:
    if not _same_market(entry, episode):
        raise ValueError("Episode market identity must match the Watchlist entry.")


def _same_market(left: object, right: object) -> bool:
    return _key(
        getattr(left, "symbol"), getattr(left, "exchange"), getattr(left, "timeframe")
    ) == _key(
        getattr(right, "symbol"), getattr(right, "exchange"), getattr(right, "timeframe")
    )


def _validate_market_identity(exchange: str, symbol: str, timeframe: str) -> None:
    for name, value in (("exchange", exchange), ("symbol", symbol), ("timeframe", timeframe)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string.")


def _validate_aware_timestamp(name: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
