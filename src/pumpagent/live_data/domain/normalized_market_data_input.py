"""Normalized market data input contract for Runtime handoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pumpagent.live_data.domain.base import (
    SerializableMixin,
    freeze_dataclass_fields,
)
from pumpagent.live_data.domain.enums import LiveDataQualityStatus
from pumpagent.live_data.domain.source_metadata import SourceMetadata


@dataclass(frozen=True)
class NormalizedMarketDataInput(SerializableMixin):
    source_event_id: str
    symbol: str
    exchange: str
    timeframe: str
    source_timestamp: datetime
    receive_timestamp: datetime
    price: float
    ohlcv: tuple[dict[str, Any], ...]
    volume: float
    data_source: str
    quality_status: LiveDataQualityStatus
    source_metadata: SourceMetadata
    schema_version: str = "1.0"
    optional_market_metrics: dict[str, Any] = field(default_factory=dict)
    quality_reasons: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    validation_warnings: tuple[str, ...] = ()
    raw_payload_reference: str | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
