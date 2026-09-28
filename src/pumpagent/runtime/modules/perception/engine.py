"""Runtime Perception Engine v0.1.

Perception validates MarketSnapshot and produces ObservationPackage.
It does not construct final evidence contracts or own downstream reasoning.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pumpagent.runtime.domain import (
    MarketSnapshot,
    ObservationPackage,
    RuntimeEvent,
)
from pumpagent.runtime.modules.evidence import collect_evidence, format_evidence
from pumpagent.runtime.modules.market_metrics import (
    calculate_confidence,
    metric_as_float,
    metric_value,
)


REQUIRED_OHLCV_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
class PerceptionError(ValueError):
    """Raised when Perception cannot produce an observation package."""


def detect_market_state(data: Any) -> str:
    """Classify the current market state from objective market-change metrics."""

    price_change_1m = metric_as_float(data, "price_change_1m")
    price_change_3m = metric_as_float(data, "price_change_3m")
    volume_spike_ratio = metric_as_float(data, "volume_spike_ratio")
    oi_change_1m = metric_as_float(data, "oi_change_1m")

    if (
        price_change_1m is not None
        and volume_spike_ratio is not None
        and oi_change_1m is not None
        and price_change_1m > 1.0
        and volume_spike_ratio > 8.0
        and oi_change_1m > 0
    ):
        return "IGNITION"

    if (
        price_change_3m is not None
        and oi_change_1m is not None
        and volume_spike_ratio is not None
        and price_change_3m > 2.0
        and oi_change_1m >= 0
        and volume_spike_ratio > 2.0
    ):
        return "CONTINUATION_ALIVE"

    if (
        price_change_3m is not None
        and oi_change_1m is not None
        and volume_spike_ratio is not None
        and price_change_3m > 0
        and oi_change_1m <= 0
        and volume_spike_ratio < 2.0
    ):
        return "WEAKENING"

    return "UNKNOWN"


def format_market_state_scan_line(data: Any) -> str:
    """Format one market state scan line for console output."""

    state = detect_market_state(data)
    confidence = calculate_confidence(data)
    symbol = metric_value(data, "symbol", default="UNKNOWN")
    price = metric_value(data, "price", default=None)
    volume = metric_value(data, "volume", default=None)
    oi = metric_value(data, "open_interest", default=None)
    if oi is None:
        oi = metric_value(data, "oi", default=None)
    evidence = format_evidence(collect_evidence(data))

    return (
        f"{symbol} | {state} | CONF={confidence}% | {price} | {volume} | {oi} "
        f"| Evidence: {evidence}"
    )


def print_market_state_scan(markets: Iterable[Any]) -> None:
    """Print one market state scan line per market."""

    for market in markets:
        print(format_market_state_scan_line(market))


def build_observation_package(
    snapshot: MarketSnapshot,
    *,
    runtime_event_id: str | None = None,
) -> ObservationPackage:
    """Build an ObservationPackage from a MarketSnapshot without interpretation."""

    _validate_market_snapshot(snapshot)
    observation_event_id = runtime_event_id or snapshot.event_id

    available_metrics = ["price", "ohlcv", "volume"]
    normalized_metrics: dict[str, Any] = {}

    for key, value in snapshot.optional_market_metrics.items():
        available_metrics.append(str(key))
        normalized_metrics[str(key)] = value

    return ObservationPackage(
        event_id=observation_event_id,
        observation_timestamp=snapshot.timestamp,
        normalized_price=snapshot.price,
        normalized_ohlcv=snapshot.ohlcv,
        normalized_volume=snapshot.volume,
        available_metrics=tuple(available_metrics),
        missing_metrics=snapshot.missing_fields,
        data_quality_status=snapshot.data_quality_status,
        schema_version=snapshot.schema_version,
        normalized_metrics=normalized_metrics,
        previous_snapshot_reference=snapshot.event_id,
    )


def add_observation_package(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only observation_package added."""

    if event.market_snapshot is None:
        raise PerceptionError("RuntimeEvent.market_snapshot is required.")

    # ObservationPackage.event_id belongs to the Runtime cycle. The source
    # MarketSnapshot.event_id is retained as previous_snapshot_reference.
    observations = build_observation_package(
        event.market_snapshot,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(observation_package=observations)


def _validate_market_snapshot(snapshot: MarketSnapshot) -> None:
    if snapshot.ohlcv is None or len(snapshot.ohlcv) == 0:
        raise PerceptionError("MarketSnapshot.ohlcv must contain at least one candle.")

    for index, candle in enumerate(snapshot.ohlcv):
        if not isinstance(candle, Mapping):
            raise PerceptionError(
                f"MarketSnapshot.ohlcv candle {index} must be a mapping."
            )

        missing_fields = [
            field for field in REQUIRED_OHLCV_FIELDS if field not in candle
        ]
        if missing_fields:
            joined_fields = ", ".join(missing_fields)
            raise PerceptionError(
                "MarketSnapshot.ohlcv candle "
                f"{index} is missing required fields: {joined_fields}."
            )

    if snapshot.price is None:
        raise PerceptionError("MarketSnapshot.price is required.")

    if snapshot.volume is None:
        raise PerceptionError("MarketSnapshot.volume is required.")
