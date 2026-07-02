"""Enums for Live Data v0.2 contracts."""

from __future__ import annotations

from enum import Enum


class LiveDataQualityStatus(str, Enum):
    GOOD = "good"
    DELAYED = "delayed"
    PARTIAL = "partial"
    CORRUPTED = "corrupted"
    UNKNOWN = "unknown"


class LiveDataErrorType(str, Enum):
    TIMEOUT = "timeout"
    RETRYABLE_NETWORK_ERROR = "retryable_network_error"
    EXCHANGE_UNAVAILABLE = "exchange_unavailable"
    RATE_LIMITED = "rate_limited"
    MALFORMED_PAYLOAD = "malformed_payload"
    VALIDATION_FAILED = "validation_failed"
    STALE_DATA = "stale_data"
    UNSUPPORTED_SYMBOL = "unsupported_symbol"
    UNSUPPORTED_TIMEFRAME = "unsupported_timeframe"
    QUALITY_BLOCKED = "quality_blocked"
    UNKNOWN_ERROR = "unknown_error"


class LiveDataTransport(str, Enum):
    FIXTURE = "fixture"
    REPLAY = "replay"
    REST = "rest"
    WEBSOCKET = "websocket"


class LiveDataMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"
    SIMULATION = "simulation"
    TESTING = "testing"
