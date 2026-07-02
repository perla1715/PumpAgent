"""Base class for future Live Data exchange adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pumpagent.live_data.adapters.adapter_capabilities import AdapterCapabilities
from pumpagent.live_data.domain import LiveDataResult


class BaseLiveDataAdapter(ABC):
    """Acquisition-only base adapter.

    Adapters satisfy LiveDataSource by returning LiveDataResult from load().
    They must not create RuntimeEvent, create MarketSnapshot, invoke Runtime
    modules, perform market reasoning, generate alerts, or execute trades.
    """

    def load(self, symbol: str, timeframe: str) -> LiveDataResult:
        """Default LiveDataSource entry point for latest snapshot acquisition."""

        return self.load_latest_snapshot(symbol=symbol, timeframe=timeframe)

    @abstractmethod
    def load_latest_snapshot(self, symbol: str, timeframe: str) -> LiveDataResult:
        """Acquire the latest normalized market-data input."""

    @abstractmethod
    def load_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> LiveDataResult:
        """Acquire historical candle data as a LiveDataResult."""

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """Report adapter capability metadata."""
