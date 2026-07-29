"""Leakage-safe objective future-outcome attribution."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import sqrt
from statistics import pstdev
from typing import Any, Mapping, Sequence

from pumpagent.learning.domain import (
    CompletenessStatus,
    LearningCase,
    OUTCOME_COMPUTATION_VERSION,
    OutcomeRecord,
    SUPPORTED_HORIZONS_MINUTES,
)
from pumpagent.learning.repository import LearningCaseRepository


class OutcomeAttributionError(ValueError):
    pass


class OutcomeAttributionService:
    def __init__(self, repository: LearningCaseRepository) -> None:
        self.repository = repository

    def attribute(
        self,
        case: LearningCase,
        observations: Sequence[Mapping[str, Any]],
        *,
        horizon_minutes: int,
        creation_timestamp: datetime | None = None,
    ) -> OutcomeRecord:
        record = compute_outcome_record(
            case,
            observations,
            horizon_minutes=horizon_minutes,
            creation_timestamp=creation_timestamp,
        )
        return self.repository.attach_outcome(record)


def compute_outcome_record(
    case: LearningCase,
    observations: Sequence[Mapping[str, Any]],
    *,
    horizon_minutes: int,
    creation_timestamp: datetime | None = None,
) -> OutcomeRecord:
    if horizon_minutes not in SUPPORTED_HORIZONS_MINUTES:
        raise OutcomeAttributionError("Unsupported outcome horizon.")
    ordered = _validated_observations(case, observations)
    horizon_end = case.cycle_timestamp + timedelta(minutes=horizon_minutes)
    window = tuple(
        item
        for item in ordered
        if case.cycle_timestamp < item["timestamp"] <= horizon_end
    )
    step_minutes = _timeframe_minutes(case.timeframe)
    expected_timestamps = tuple(
        case.cycle_timestamp + timedelta(minutes=step)
        for step in range(step_minutes, horizon_minutes + 1, step_minutes)
    )
    actual_timestamps = tuple(item["timestamp"] for item in window)
    missing = tuple(
        timestamp for timestamp in expected_timestamps if timestamp not in actual_timestamps
    )
    complete = bool(expected_timestamps) and not missing
    reasons: list[str] = []
    if not window:
        reasons.append("no_future_observations")
    if missing:
        reasons.append("missing_expected_candles")
    status = (
        CompletenessStatus.COMPLETE
        if complete
        else CompletenessStatus.INCOMPLETE
        if window
        else CompletenessStatus.UNAVAILABLE
    )
    metrics = _metrics(case, window) if window else _empty_metrics()
    created_at = creation_timestamp or horizon_end
    outcome_boundary = (
        window[-1]["timestamp"].isoformat() if window else "unavailable"
    )
    return OutcomeRecord(
        outcome_id=(
            f"outcome:{case.case_id}:{horizon_minutes}m:{outcome_boundary}"
        ),
        source_case_id=case.case_id,
        source_runtime_event_id=case.runtime_event_id,
        source_cycle_timestamp=case.cycle_timestamp,
        horizon_minutes=horizon_minutes,
        observation_start_timestamp=window[0]["timestamp"] if window else None,
        observation_end_timestamp=window[-1]["timestamp"] if window else None,
        source_data_identity={
            "symbol": case.symbol,
            "exchange": case.exchange,
            "timeframe": case.timeframe,
        },
        window_complete=complete,
        completeness_status=status,
        missing_reasons=tuple(reasons),
        creation_timestamp=created_at,
        computation_version=OUTCOME_COMPUTATION_VERSION,
        **metrics,
    )


def _validated_observations(
    case: LearningCase, observations: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    seen: set[datetime] = set()
    previous: datetime | None = None
    for raw in observations:
        for name, expected in (
            ("symbol", case.symbol),
            ("exchange", case.exchange),
            ("timeframe", case.timeframe),
        ):
            if raw.get(name) != expected:
                raise OutcomeAttributionError(f"Outcome {name} mismatch.")
        timestamp = _timestamp(raw.get("timestamp"))
        if timestamp <= case.cycle_timestamp:
            raise OutcomeAttributionError(
                "Outcome observations must be strictly after the source cycle."
            )
        if timestamp in seen:
            raise OutcomeAttributionError("Duplicate outcome candle timestamp.")
        if previous is not None and timestamp <= previous:
            raise OutcomeAttributionError(
                "Outcome observations must be strictly chronological."
            )
        seen.add(timestamp)
        previous = timestamp
        item = dict(raw)
        item["timestamp"] = timestamp
        for name in ("close", "high", "low"):
            value = item.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise OutcomeAttributionError(f"Outcome {name} must be numeric.")
            item[name] = float(value)
        volume = item.get("volume")
        if volume is not None:
            if isinstance(volume, bool) or not isinstance(volume, (int, float)):
                raise OutcomeAttributionError("Outcome volume must be numeric.")
            item["volume"] = float(volume)
        normalized.append(item)
    return tuple(normalized)


def _metrics(
    case: LearningCase, window: tuple[dict[str, Any], ...]
) -> dict[str, float | int | None]:
    snapshot = case.runtime_event_payload["runtime_event"]["market_snapshot"]
    base_close = float(snapshot["price"])
    base_volume = snapshot.get("volume")
    close_returns = tuple(item["close"] / base_close - 1.0 for item in window)
    high_returns = tuple(item["high"] / base_close - 1.0 for item in window)
    low_returns = tuple(item["low"] / base_close - 1.0 for item in window)
    favorable_index = max(range(len(close_returns)), key=close_returns.__getitem__)
    adverse_index = min(range(len(close_returns)), key=close_returns.__getitem__)
    periodic = tuple(
        window[index]["close"] / window[index - 1]["close"] - 1.0
        for index in range(1, len(window))
    )
    realized = (
        pstdev(periodic) * sqrt(len(periodic))
        if len(periodic) >= 2
        else 0.0
        if periodic
        else None
    )
    last_volume = window[-1].get("volume")
    volume_change = (
        float(last_volume) / float(base_volume) - 1.0
        if last_volume is not None
        and isinstance(base_volume, (int, float))
        and float(base_volume) != 0.0
        else None
    )
    return {
        "close_to_close_return": close_returns[-1],
        "maximum_favorable_excursion": max(close_returns),
        "maximum_adverse_excursion": min(close_returns),
        "maximum_high_return": max(high_returns),
        "minimum_low_return": min(low_returns),
        "time_to_maximum_favorable_excursion_seconds": int(
            (window[favorable_index]["timestamp"] - case.cycle_timestamp).total_seconds()
        ),
        "time_to_maximum_adverse_excursion_seconds": int(
            (window[adverse_index]["timestamp"] - case.cycle_timestamp).total_seconds()
        ),
        "realized_volatility": realized,
        "volume_change": volume_change,
    }


def _empty_metrics() -> dict[str, None]:
    return {
        "close_to_close_return": None,
        "maximum_favorable_excursion": None,
        "maximum_adverse_excursion": None,
        "maximum_high_return": None,
        "minimum_low_return": None,
        "time_to_maximum_favorable_excursion_seconds": None,
        "time_to_maximum_adverse_excursion_seconds": None,
        "realized_volatility": None,
        "volume_change": None,
    }


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OutcomeAttributionError("Outcome timestamp must be timezone-aware.")
    return value


def _timeframe_minutes(value: str) -> int:
    if not value.endswith("m") or not value[:-1].isdigit():
        raise OutcomeAttributionError("F-03 supports minute timeframes only.")
    minutes = int(value[:-1])
    if minutes <= 0:
        raise OutcomeAttributionError("Invalid minute timeframe.")
    return minutes
