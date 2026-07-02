"""MarketSnapshot domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import DataQualityStatus


@dataclass(frozen=True)
class MarketSnapshot(SerializableMixin):
    event_id: str
    timestamp: datetime
    symbol: str
    exchange: str
    timeframe: str
    price: float
    ohlcv: tuple[dict[str, Any], ...]
    volume: float
    data_source: str
    data_quality_status: DataQualityStatus
    schema_version: str = "1.0"
    optional_market_metrics: dict[str, Any] = field(default_factory=dict)
    raw_payload_reference: str | None = None
    latency_ms: int | None = None
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
