"""Immutable domain foundation for Observation Episodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import ObservationEpisodeStatus


OBSERVATION_EPISODE_SCHEMA_VERSION = "observation_episode_v1"


def generate_episode_id(
    exchange: str,
    symbol: str,
    timeframe: str,
    opening_timestamp: datetime,
) -> str:
    """Generate a stable ID from the canonical market and opening instant."""

    _validate_market_identity(exchange, symbol, timeframe)
    _validate_aware_timestamp("opening_timestamp", opening_timestamp)
    identity = "|".join(
        (
            exchange.strip().lower(),
            symbol.strip().upper(),
            timeframe.strip().lower(),
            opening_timestamp.astimezone(timezone.utc).isoformat(),
        )
    )
    return f"episode_{sha256(identity.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class ObservationEpisodeIdentity(SerializableMixin):
    """Canonical immutable identity of one Observation Episode."""

    episode_id: str
    exchange: str
    symbol: str
    timeframe: str
    opening_timestamp: datetime

    def __post_init__(self) -> None:
        _validate_non_empty("episode_id", self.episode_id)
        _validate_market_identity(self.exchange, self.symbol, self.timeframe)
        _validate_aware_timestamp("opening_timestamp", self.opening_timestamp)


@dataclass(frozen=True)
class ObservationEpisode(ObservationEpisodeIdentity):
    """One immutable and serializable market observation lifecycle."""

    status: ObservationEpisodeStatus
    scanner_trigger_timestamp: datetime
    trigger_reasons: tuple[str, ...]
    trigger_metrics: Mapping[str, Any] = field(default_factory=dict)
    closing_timestamp: datetime | None = None
    closure_reason: str | None = None
    latest_accepted_candle_timestamp: datetime | None = None
    observation_cycle_count: int = 0
    schema_version: str = OBSERVATION_EPISODE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        super().__post_init__()
        freeze_dataclass_fields(self)

        if not isinstance(self.status, ObservationEpisodeStatus):
            raise ValueError("status must be an ObservationEpisodeStatus.")
        _validate_aware_timestamp(
            "scanner_trigger_timestamp", self.scanner_trigger_timestamp
        )
        if not self.trigger_reasons:
            raise ValueError("trigger_reasons must contain at least one reason.")
        for reason in self.trigger_reasons:
            _validate_non_empty("trigger reason", reason)
        if self.observation_cycle_count < 0:
            raise ValueError("observation_cycle_count cannot be negative.")
        _validate_non_empty("schema_version", self.schema_version)

        if self.latest_accepted_candle_timestamp is not None:
            _validate_aware_timestamp(
                "latest_accepted_candle_timestamp",
                self.latest_accepted_candle_timestamp,
            )
        if self.closing_timestamp is not None:
            _validate_aware_timestamp("closing_timestamp", self.closing_timestamp)

        if self.status is ObservationEpisodeStatus.ACTIVE:
            if self.closing_timestamp is not None:
                raise ValueError("An active Episode cannot have a closing timestamp.")
            if self.closure_reason is not None:
                raise ValueError("An active Episode cannot have a closure reason.")
        else:
            if self.closing_timestamp is None:
                raise ValueError("A closed Episode must have a closing timestamp.")
            _validate_non_empty("closure_reason", self.closure_reason)


def _validate_market_identity(exchange: str, symbol: str, timeframe: str) -> None:
    _validate_non_empty("exchange", exchange)
    _validate_non_empty("symbol", symbol)
    _validate_non_empty("timeframe", timeframe)


def _validate_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _validate_aware_timestamp(name: str, value: object) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
