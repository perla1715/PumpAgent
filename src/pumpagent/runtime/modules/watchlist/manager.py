"""Dynamic Watchlist MVP.

The watchlist tracks interesting markets in memory only. It does not persist
data, call external services, or expire entries automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from pumpagent.runtime.domain import AgentState
from pumpagent.runtime.domain.enums import AgentStateType
from pumpagent.runtime.modules.hypothesis import MarketHypothesis


WATCHLIST_ACTION_REGISTERED = "REGISTERED"
WATCHLIST_ACTION_UPDATED = "UPDATED"
WATCHLIST_ACTION_NONE = "NONE"


@dataclass(frozen=True)
class WatchlistEntry:
    symbol: str
    exchange: str
    timeframe: str
    first_seen: datetime
    last_updated: datetime
    current_agent_state: AgentStateType
    hypothesis_id: str
    confidence: int
    observation_count: int
    event_id: str


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

    def track_cycle(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        timestamp: datetime,
        agent_state: AgentState,
        hypothesis: MarketHypothesis,
        confidence: int,
        event_id: str,
    ) -> tuple[str, int]:
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
                hypothesis_id=hypothesis.id,
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
            hypothesis_id=hypothesis.id,
            confidence=confidence,
            event_id=event_id,
        )
        return WATCHLIST_ACTION_UPDATED, entry.observation_count


def _key(symbol: str, exchange: str, timeframe: str) -> tuple[str, str, str]:
    return exchange, symbol, timeframe
