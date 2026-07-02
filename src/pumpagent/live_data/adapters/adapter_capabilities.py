"""Adapter capability metadata."""

from __future__ import annotations

from dataclasses import dataclass

from pumpagent.live_data.domain import LiveDataTransport
from pumpagent.live_data.domain.base import SerializableMixin, freeze_dataclass_fields


@dataclass(frozen=True)
class AdapterCapabilities(SerializableMixin):
    """Metadata describing what a Live Data adapter can acquire."""

    adapter_name: str
    supported_transports: tuple[LiveDataTransport, ...]
    supported_timeframes: tuple[str, ...] = ()
    supported_market_categories: tuple[str, ...] = ()
    supports_historical: bool = False
    supports_websocket: bool = False
    supports_optional_metrics: bool = False
    optional_metrics: tuple[str, ...] = ()
    public_data_only: bool = True
    rate_limit_notes: str | None = None
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
