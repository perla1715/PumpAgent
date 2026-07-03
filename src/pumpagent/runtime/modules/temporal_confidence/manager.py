"""Temporal Confidence Engine MVP.

Temporal confidence is diagnostic only. It tracks confidence evolution for
watchlisted markets in memory and does not change AgentState transitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.runtime.modules.watchlist import WatchlistEntry


CONFIDENCE_TREND_IMPROVING = "IMPROVING"
CONFIDENCE_TREND_STABLE = "STABLE"
CONFIDENCE_TREND_WEAKENING = "WEAKENING"
CONFIDENCE_TREND_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TemporalConfidenceState:
    current_confidence: int
    previous_confidence: int | None
    confidence_delta: int | None
    trend: str
    update_count: int
    last_updated: datetime


class TemporalConfidenceManager:
    """In-memory manager for temporal confidence state."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str, str], TemporalConfidenceState] = {}

    def initialize(self, entry: WatchlistEntry) -> TemporalConfidenceState:
        state = TemporalConfidenceState(
            current_confidence=entry.confidence,
            previous_confidence=None,
            confidence_delta=None,
            trend=CONFIDENCE_TREND_UNKNOWN,
            update_count=1,
            last_updated=entry.last_updated,
        )
        self._states[_key_from_entry(entry)] = state
        return state

    def update(self, entry: WatchlistEntry) -> TemporalConfidenceState:
        key = _key_from_entry(entry)
        previous = self._states.get(key)
        if previous is None:
            return self.initialize(entry)

        delta = entry.confidence - previous.current_confidence
        state = TemporalConfidenceState(
            current_confidence=entry.confidence,
            previous_confidence=previous.current_confidence,
            confidence_delta=delta,
            trend=_trend_from_delta(delta),
            update_count=previous.update_count + 1,
            last_updated=entry.last_updated,
        )
        self._states[key] = state
        return state

    def get(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> TemporalConfidenceState | None:
        return self._states.get(_key(symbol=symbol, exchange=exchange, timeframe=timeframe))

    def reset(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> TemporalConfidenceState | None:
        return self._states.pop(
            _key(symbol=symbol, exchange=exchange, timeframe=timeframe),
            None,
        )


def _trend_from_delta(delta: int) -> str:
    if delta > 0:
        return CONFIDENCE_TREND_IMPROVING
    if delta < 0:
        return CONFIDENCE_TREND_WEAKENING
    return CONFIDENCE_TREND_STABLE


def _key_from_entry(entry: WatchlistEntry) -> tuple[str, str, str]:
    return _key(symbol=entry.symbol, exchange=entry.exchange, timeframe=entry.timeframe)


def _key(*, symbol: str, exchange: str, timeframe: str) -> tuple[str, str, str]:
    return exchange, symbol, timeframe
