"""Pure admission boundary for one closed 5-minute Observation Cycle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any

from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.base import SerializableMixin
from pumpagent.runtime.domain.enums import DataQualityStatus, ObservationEpisodeStatus
from pumpagent.runtime.modules.watchlist import WatchlistObservationContext

CYCLE_ADMISSION_INPUT_SCHEMA_VERSION = "closed_cycle_admission_input_v1"
CYCLE_ADMISSION_RESULT_SCHEMA_VERSION = "closed_cycle_admission_result_v1"
MVP_OBSERVATION_TIMEFRAME = "5m"


class CycleAdmissionDecision(str, Enum):
    ADMIT = "ADMIT"
    DUPLICATE = "DUPLICATE"
    OLDER = "OLDER"
    INVALID = "INVALID"
    NO_ACTIVE_EPISODE = "NO_ACTIVE_EPISODE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    NOT_CLOSED = "NOT_CLOSED"


@dataclass(frozen=True)
class ClosedObservationCycleAdmissionInput(SerializableMixin):
    snapshot: MarketSnapshot
    watchlist_context: WatchlistObservationContext
    request_timestamp: datetime
    latest_closed_candle_timestamp: datetime | None = None
    schema_version: str = CYCLE_ADMISSION_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, MarketSnapshot):
            raise ValueError("snapshot must be a MarketSnapshot.")
        if not isinstance(self.watchlist_context, WatchlistObservationContext):
            raise ValueError("watchlist_context must be a WatchlistObservationContext.")
        _require_aware("request_timestamp", self.request_timestamp)
        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise ValueError("schema_version must be a non-empty string.")


@dataclass(frozen=True)
class ClosedObservationCycleAdmissionResult(SerializableMixin):
    decision: CycleAdmissionDecision
    admitted: bool
    episode_id: str | None
    exchange: str
    symbol: str
    timeframe: str
    candidate_closed_candle_timestamp: datetime | None
    previously_accepted_closed_candle_timestamp: datetime | None
    admission_reason: str
    runtime_allowed: bool
    cycle_count_increment_allowed_after_runtime_success: bool
    request_timestamp: datetime
    schema_version: str = CYCLE_ADMISSION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        expected = self.decision is CycleAdmissionDecision.ADMIT
        if self.admitted is not expected or self.runtime_allowed is not expected:
            raise ValueError("Only ADMIT may authorize Runtime.")
        if self.cycle_count_increment_allowed_after_runtime_success is not expected:
            raise ValueError("Cycle count authorization must match admission.")

    @property
    def market_identity(self) -> tuple[str, str, str]:
        return (self.exchange, self.symbol, self.timeframe)


def evaluate_closed_observation_cycle_admission(
    admission_input: ClosedObservationCycleAdmissionInput,
) -> ClosedObservationCycleAdmissionResult:
    """Decide eligibility without calling Runtime or mutating lifecycle state."""
    if not isinstance(admission_input, ClosedObservationCycleAdmissionInput):
        raise ValueError("admission_input must be a ClosedObservationCycleAdmissionInput.")
    snapshot, context = admission_input.snapshot, admission_input.watchlist_context

    def result(decision: CycleAdmissionDecision, reason: str, candidate: datetime | None = None) -> ClosedObservationCycleAdmissionResult:
        admitted = decision is CycleAdmissionDecision.ADMIT
        return ClosedObservationCycleAdmissionResult(
            decision=decision, admitted=admitted,
            episode_id=context.active_episode_id if context.has_active_episode else None,
            exchange=snapshot.exchange, symbol=snapshot.symbol, timeframe=snapshot.timeframe,
            candidate_closed_candle_timestamp=candidate,
            previously_accepted_closed_candle_timestamp=context.latest_accepted_closed_candle_timestamp,
            admission_reason=reason, runtime_allowed=admitted,
            cycle_count_increment_allowed_after_runtime_success=admitted,
            request_timestamp=admission_input.request_timestamp,
        )

    if not context.has_active_episode or context.lifecycle_status is not ObservationEpisodeStatus.ACTIVE:
        return result(CycleAdmissionDecision.NO_ACTIVE_EPISODE, "No ACTIVE Observation Episode exists.")
    if (_canonical(snapshot.exchange) != _canonical(context.exchange) or
            _canonical_symbol(snapshot.symbol) != _canonical_symbol(context.symbol)):
        return result(CycleAdmissionDecision.IDENTITY_MISMATCH, "Snapshot market identity does not match the active Episode.")
    if (_canonical(snapshot.timeframe) != MVP_OBSERVATION_TIMEFRAME or
            _canonical(context.timeframe) != MVP_OBSERVATION_TIMEFRAME):
        return result(CycleAdmissionDecision.UNSUPPORTED_TIMEFRAME, "Only the 5m timeframe is supported for Observation Cycles.")
    if not _snapshot_is_usable(snapshot):
        return result(CycleAdmissionDecision.INVALID, "Snapshot quality or OHLCV data is unusable.")
    latest_bucket = _candle_timestamp(snapshot.ohlcv[-1].get("timestamp"))
    candidate = admission_input.latest_closed_candle_timestamp
    if candidate is None:
        candidate = _explicitly_closed_latest_bucket(snapshot)
        if candidate is None:
            return result(CycleAdmissionDecision.NOT_CLOSED, "No explicit fully closed candle boundary was supplied or marked final.")
    if not _is_aware(candidate):
        return result(CycleAdmissionDecision.INVALID, "Candidate closed-candle timestamp must be timezone-aware.", candidate)
    if candidate != latest_bucket:
        return result(CycleAdmissionDecision.INVALID, "Closed-candle boundary must identify the latest OHLCV bucket.", candidate)
    previous = context.latest_accepted_closed_candle_timestamp
    if previous is not None and candidate == previous:
        return result(CycleAdmissionDecision.DUPLICATE, "This candle was already accepted for the active Episode.", candidate)
    if previous is not None and candidate < previous:
        return result(CycleAdmissionDecision.OLDER, "Candidate candle precedes the latest accepted candle.", candidate)
    return result(CycleAdmissionDecision.ADMIT, "A newer valid closed 5m candle may enter Runtime.", candidate)


def _snapshot_is_usable(snapshot: MarketSnapshot) -> bool:
    if snapshot.data_quality_status is not DataQualityStatus.VALID or snapshot.missing_fields or not snapshot.ohlcv:
        return False
    required = ("timestamp", "open", "high", "low", "close", "volume")
    for candle in snapshot.ohlcv:
        if any(key not in candle or candle[key] is None for key in required):
            return False
        try:
            if not all(isfinite(float(candle[key])) for key in required[1:]):
                return False
        except (TypeError, ValueError):
            return False
        if not _is_aware(_candle_timestamp(candle["timestamp"])):
            return False
    return True


def _explicitly_closed_latest_bucket(snapshot: MarketSnapshot) -> datetime | None:
    candle = snapshot.ohlcv[-1]
    if candle.get("is_closed", candle.get("closed", candle.get("final"))) is not True:
        return None
    return _candle_timestamp(candle.get("timestamp"))


def _candle_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _canonical(value: str) -> str:
    return value.strip().lower()


def _canonical_symbol(value: str) -> str:
    return value.strip().upper()


def _is_aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _require_aware(name: str, value: object) -> None:
    if not _is_aware(value):
        raise ValueError(f"{name} must be timezone-aware.")
