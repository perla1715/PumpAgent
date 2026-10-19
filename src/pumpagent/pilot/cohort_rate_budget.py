"""Bounded Coinalyze quota pacing for one Pilot discovery cohort."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Callable


@dataclass
class CohortRequestLedgerEntry:
    sequence: int
    quota_units: int
    endpoint_class: str
    symbol: str | None
    timeframe: str | None
    dispatched_at_utc: datetime
    wait_seconds: float
    outcome: str = "DISPATCHED"
    error: str | None = None
    retry_after_seconds: float | None = None


class CoinalyzeCohortRateBudget:
    """Evenly pace one 40-unit/minute provider budget; no cross-cohort state."""

    def __init__(
        self,
        *,
        quota_units: int = 40,
        window_seconds: float = 60.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if quota_units <= 0 or window_seconds <= 0:
            raise ValueError("quota units and window seconds must be positive")
        self.quota_units = quota_units
        self.window_seconds = float(window_seconds)
        self._interval = self.window_seconds / self.quota_units
        self._monotonic = monotonic_clock
        self._utc = utc_clock
        self._sleeper = sleeper
        self._next_dispatch_at: float | None = None
        self._entries: list[CohortRequestLedgerEntry] = []
        self._blocked = False

    @property
    def ledger(self) -> tuple[CohortRequestLedgerEntry, ...]:
        return tuple(self._entries)

    def utc_now(self) -> datetime:
        """UTC provenance clock for the owning discovery cohort."""
        return self._utc().astimezone(timezone.utc)

    def reserve(self, *, endpoint_class: str, symbol: str | None = None, timeframe: str | None = None, quota_units: int = 1) -> CohortRequestLedgerEntry:
        if self._blocked:
            raise RuntimeError("Coinalyze cohort dispatch is blocked after a systemic provider failure")
        if quota_units <= 0 or quota_units > self.quota_units:
            raise ValueError("quota reservation is outside the provider contract")
        now = self._monotonic()
        target = now if self._next_dispatch_at is None else max(now, self._next_dispatch_at)
        wait = target - now
        if wait > 0:
            self._sleeper(wait)
            now = self._monotonic()
            # A test clock may advance exactly by the requested wait; a real
            # monotonic clock may advance more. Never backdate dispatch.
            target = max(now, target)
        self._next_dispatch_at = target + self._interval * quota_units
        entry = CohortRequestLedgerEntry(
            sequence=len(self._entries) + 1,
            quota_units=quota_units,
            endpoint_class=endpoint_class,
            symbol=symbol.upper() if symbol else None,
            timeframe=timeframe,
            dispatched_at_utc=self.utc_now(),
            wait_seconds=wait,
        )
        self._entries.append(entry)
        return entry

    def succeed(self, entry: CohortRequestLedgerEntry) -> None:
        entry.outcome = "SUCCESS"

    def error(self, entry: CohortRequestLedgerEntry, exc: Exception, *, retry_after_seconds: float | None = None) -> None:
        entry.outcome = "ERROR"
        entry.error = f"{type(exc).__name__}: {exc}"
        entry.retry_after_seconds = retry_after_seconds

    def fail(self, entry: CohortRequestLedgerEntry, exc: Exception, *, retry_after_seconds: float | None = None) -> None:
        self.error(entry, exc, retry_after_seconds=retry_after_seconds)
        self._blocked = True
