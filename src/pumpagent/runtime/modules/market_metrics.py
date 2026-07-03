"""Shared lightweight market metric helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def metric_value(data: Any, key: str, *, default: Any = None) -> Any:
    if isinstance(data, Mapping):
        return data.get(key, default)

    optional_market_metrics = getattr(data, "optional_market_metrics", None)
    if isinstance(optional_market_metrics, Mapping) and key in optional_market_metrics:
        return optional_market_metrics[key]

    return getattr(data, key, default)


def metric_as_float(data: Any, key: str) -> float | None:
    value = metric_value(data, key, default=None)
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_confidence(data: Any) -> int:
    """Return an initial heuristic confidence score between 0 and 100."""

    score = 0

    price_change_1m = metric_as_float(data, "price_change_1m")
    if price_change_1m is not None:
        if price_change_1m > 2.0:
            score += 30
        elif price_change_1m > 1.0:
            score += 20
        elif price_change_1m > 0:
            score += 10

    volume_spike_ratio = metric_as_float(data, "volume_spike_ratio")
    if volume_spike_ratio is not None:
        if volume_spike_ratio > 10.0:
            score += 30
        elif volume_spike_ratio > 5.0:
            score += 20
        elif volume_spike_ratio > 2.0:
            score += 10

    oi_change_1m = metric_as_float(data, "oi_change_1m")
    if oi_change_1m is not None:
        if oi_change_1m > 2.0:
            score += 30
        elif oi_change_1m > 0.5:
            score += 20
        elif oi_change_1m > 0:
            score += 10

    return min(score, 100)
