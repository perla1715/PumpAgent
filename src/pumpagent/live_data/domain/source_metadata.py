"""Source metadata carried with normalized Live Data inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.live_data.domain.base import SerializableMixin
from pumpagent.live_data.domain.enums import LiveDataTransport


@dataclass(frozen=True)
class SourceMetadata(SerializableMixin):
    exchange: str
    adapter_name: str
    adapter_version: str
    source_timestamp: datetime
    receive_timestamp: datetime
    transport: LiveDataTransport
    source_symbol: str
    normalized_symbol: str
    source_timeframe: str
    normalized_timeframe: str
    schema_version: str = "1.0"
    latency_ms: float | None = None
    correlation_id: str | None = None
    request_id: str | None = None
    sequence_id: str | None = None
