"""Typed adapter-level errors."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pumpagent.live_data.domain.base import SerializableMixin, freeze_dataclass_fields


class AdapterErrorType(str, Enum):
    TIMEOUT = "timeout"
    EXCHANGE_UNAVAILABLE = "exchange_unavailable"
    MALFORMED_PAYLOAD = "malformed_payload"
    RATE_LIMITED = "rate_limited"
    UNSUPPORTED_SYMBOL = "unsupported_symbol"
    UNSUPPORTED_TIMEFRAME = "unsupported_timeframe"
    UNKNOWN_ERROR = "unknown_error"


@dataclass(frozen=True)
class AdapterError(SerializableMixin):
    """Adapter-level acquisition error before Runtime handoff."""

    error_type: AdapterErrorType
    message: str
    retryable: bool = False
    raw_payload_reference: str | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
