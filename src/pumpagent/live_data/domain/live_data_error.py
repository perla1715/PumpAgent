"""Structured Live Data error contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.live_data.domain.base import (
    SerializableMixin,
    freeze_dataclass_fields,
)
from pumpagent.live_data.domain.enums import LiveDataErrorType


@dataclass(frozen=True)
class LiveDataError(SerializableMixin):
    error_type: LiveDataErrorType
    message: str
    exchange: str
    symbol: str
    timeframe: str
    receive_timestamp: datetime
    retryable: bool
    schema_version: str = "1.0"
    retry_after_ms: int | None = None
    source_timestamp: datetime | None = None
    validation_errors: tuple[str, ...] = ()
    raw_payload_reference: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
