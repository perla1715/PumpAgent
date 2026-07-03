"""Evidence Engine MVP.

Evidence explains which observed metrics supported or weakened a scan result.
It does not classify market state, calculate confidence, or make decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pumpagent.runtime.modules.market_metrics import metric_as_float


@dataclass(frozen=True)
class Evidence:
    name: str
    value: str
    positive: bool


def collect_evidence(data: Any) -> list[Evidence]:
    """Collect lightweight evidence from current market metrics only."""

    price_change_1m = metric_as_float(data, "price_change_1m")
    volume_spike_ratio = metric_as_float(data, "volume_spike_ratio")
    oi_change_1m = metric_as_float(data, "oi_change_1m")

    return [
        Evidence(
            name="Price",
            value="Price increasing"
            if price_change_1m is not None and price_change_1m > 0
            else "Price not increasing",
            positive=price_change_1m is not None and price_change_1m > 0,
        ),
        Evidence(
            name="Volume",
            value="Volume above average"
            if volume_spike_ratio is not None and volume_spike_ratio > 2
            else "Volume not above average",
            positive=volume_spike_ratio is not None and volume_spike_ratio > 2,
        ),
        Evidence(
            name="OI",
            value="OI increasing"
            if oi_change_1m is not None and oi_change_1m > 0
            else "OI not increasing",
            positive=oi_change_1m is not None and oi_change_1m > 0,
        ),
    ]


def format_evidence(evidence: list[Evidence]) -> str:
    """Format evidence as compact signed scan text."""

    parts = []
    for item in evidence:
        sign = "+" if item.positive else "-"
        parts.append(f"{sign} {item.value}")
    return "; ".join(parts)
