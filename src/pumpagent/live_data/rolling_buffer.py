"""Persistent native 1m/5m rolling market-context buffer.

This module is intentionally acquisition-only.  It stores completed native
candles from an existing adapter and exposes history/readiness for later pilot
tasks.  It does not admit observations, run Scanner logic, call Runtime, or
execute trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import os
from typing import Any, Callable

from pumpagent.live_data.adapters.base_adapter import BaseLiveDataAdapter
from pumpagent.live_data.domain import LiveDataResult, NormalizedMarketDataInput
from pumpagent.live_data.normalizers.bybit_kline_normalizer import (
    normalize_bybit_kline_raw_data,
)


TIMEFRAME_MINUTES = {"1": 1, "3": 3, "5": 5}
REQUIRED_FIELDS = ("PRICE", "VOLUME")
OPTIONAL_FIELDS = ("OI", "FUNDING", "LIQUIDATIONS")


@dataclass(frozen=True)
class BufferStatus:
    provider: str
    symbol: str
    timeframe: str
    row_count: int
    latest_market_timestamp: datetime | None
    warmup_status: str
    available_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    persistence_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "row_count": self.row_count,
            "latest_market_timestamp": (
                self.latest_market_timestamp.isoformat()
                if self.latest_market_timestamp
                else None
            ),
            "warmup_status": self.warmup_status,
            "available_fields": list(self.available_fields),
            "missing_fields": list(self.missing_fields),
            "persistence_path": self.persistence_path,
        }


class RollingNumericContextBuffer:
    """Append-only JSONL-backed rolling closed-candle buffer."""

    def __init__(
        self,
        storage_path: str | Path,
        *,
        symbol: str,
        provider: str = "bybit",
        venue: str = "bybit_linear",
        timeframes: tuple[str, ...] = ("1", "3", "5"),
        min_closed: int = 120,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        unsupported = set(timeframes) - set(TIMEFRAME_MINUTES)
        if unsupported:
            raise ValueError(
                f"Only native 1m/3m/5m timeframes are supported: {unsupported}"
            )
        if min_closed < 1:
            raise ValueError("min_closed must be positive")
        self.storage_path = Path(storage_path)
        self.symbol = symbol.upper()
        self.provider = provider
        self.venue = venue
        self.timeframes = tuple(timeframes)
        self.min_closed = min_closed
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        self.storage_path.touch(exist_ok=True)

    def _read_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.storage_path.exists():
            return rows
        with self.storage_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Corrupt rolling buffer line {line_number}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"Rolling buffer line {line_number} is not an object")
                rows.append(row)
        return rows

    def _append_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with self.storage_path.open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def bootstrap(self, adapter: BaseLiveDataAdapter, *, limit: int | None = None) -> dict[str, Any]:
        """Load recent provider history, then leave the buffer ready for polling."""

        requested_limit = limit or max(self.min_closed + 10, 200)
        results = []
        for timeframe in self.timeframes:
            result = adapter.load_historical_candles(
                self.symbol,
                timeframe,
                limit=requested_limit,
            )
            results.append(self.ingest_result(result, timeframe=timeframe))
        return {"mode": "bootstrap", "results": results}

    def poll_once(self, adapter: BaseLiveDataAdapter) -> dict[str, Any]:
        """Fetch a small recent window for each timeframe and ingest closed bars."""

        results = []
        for timeframe in self.timeframes:
            result = adapter.load_historical_candles(
                self.symbol,
                timeframe,
                limit=3,
            )
            results.append(self.ingest_result(result, timeframe=timeframe))
        return {"mode": "poll", "results": results}

    def ingest_result(self, result: LiveDataResult, *, timeframe: str) -> dict[str, Any]:
        if not result.success:
            return {
                "timeframe": timeframe,
                "inserted": 0,
                "duplicates": 0,
                "open_rows": 0,
                "issues": [result.error.message if result.error else "acquisition_failed"],
            }
        normalized_result = normalize_bybit_kline_raw_data(result)
        if not normalized_result.success or normalized_result.data is None:
            return {
                "timeframe": timeframe,
                "inserted": 0,
                "duplicates": 0,
                "open_rows": 0,
                "issues": [
                    normalized_result.error.message
                    if normalized_result.error
                    else "normalization_failed"
                ],
            }
        return self.ingest_normalized(normalized_result.data)

    def ingest_normalized(self, normalized: NormalizedMarketDataInput) -> dict[str, Any]:
        if normalized.symbol.upper() != self.symbol:
            raise ValueError(f"Unexpected symbol {normalized.symbol}; expected {self.symbol}")
        timeframe = normalized.timeframe
        if timeframe not in self.timeframes:
            raise ValueError(f"Unexpected timeframe {timeframe}")
        candles = list(normalized.ohlcv)
        timestamps = [candle.get("timestamp") for candle in candles]
        issues: list[str] = []
        if timestamps != sorted(timestamps):
            issues.append("OUT_OF_ORDER_INPUT")
        ordered = sorted(candles, key=lambda candle: candle["timestamp"])
        now = _ensure_utc(self._now_fn())
        inserted = duplicates = open_rows = 0
        previous_timestamp: datetime | None = None
        existing_rows = self._read_rows()
        existing_keys = {
            (row["symbol"], row["provider"], row["timeframe"], row["market_timestamp_utc"])
            for row in existing_rows
        }
        new_rows: list[dict[str, Any]] = []
        for candle in ordered:
                timestamp = _ensure_utc(candle["timestamp"])
                if timestamp + timedelta(minutes=TIMEFRAME_MINUTES[timeframe]) > now:
                    open_rows += 1
                    continue
                continuity = "CONTIGUOUS"
                if previous_timestamp is not None:
                    expected = previous_timestamp + timedelta(
                        minutes=TIMEFRAME_MINUTES[timeframe]
                    )
                    if timestamp != expected:
                        continuity = "MISSING_INTERVAL"
                        issues.append("MISSING_INTERVAL")
                previous_timestamp = timestamp
                availability = {
                    "PRICE": "PRESENT",
                    "VOLUME": "PRESENT",
                    "OI": "PROVIDER_NOT_SUPPORTED",
                    "FUNDING": "PROVIDER_NOT_SUPPORTED",
                    "LIQUIDATIONS": "PROVIDER_NOT_SUPPORTED",
                }
                metrics = normalized.optional_market_metrics
                for field_name in OPTIONAL_FIELDS:
                    if field_name.lower() in metrics and metrics[field_name.lower()] is not None:
                        availability[field_name] = "PRESENT"
                values = {
                    "oi": _metric(metrics, "oi"),
                    "funding": _metric(metrics, "funding"),
                    "liquidations": _metric(metrics, "liquidations"),
                }
                key = (self.symbol, self.provider, timeframe, timestamp.isoformat())
                if key not in existing_keys:
                    new_rows.append(
                        {
                            "symbol": self.symbol,
                            "provider": self.provider,
                            "venue": self.venue,
                            "timeframe": timeframe,
                            "market_timestamp_utc": timestamp.isoformat(),
                            "ingest_timestamp_utc": now.isoformat(),
                            "price": float(candle["close"]),
                            "open": float(candle["open"]) if candle.get("open") is not None else None,
                            "high": float(candle["high"]) if candle.get("high") is not None else None,
                            "low": float(candle["low"]) if candle.get("low") is not None else None,
                            "volume": float(candle["volume"]),
                            "oi": values["oi"],
                            "funding": values["funding"],
                            "liquidations": values["liquidations"],
                            "field_availability": availability,
                            "raw_or_derived_status": "RAW_NATIVE_CLOSED_BAR",
                            "continuity_status": continuity,
                        }
                    )
                    existing_keys.add(key)
                    inserted += 1
                else:
                    duplicates += 1
        self._append_rows(new_rows)
        return {
            "timeframe": timeframe,
            "inserted": inserted,
            "duplicates": duplicates,
            "open_rows": open_rows,
            "issues": sorted(set(issues)),
        }

    def observations(self, timeframe: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        rows = [
            row for row in self._read_rows()
            if row["symbol"] == self.symbol
            and row["provider"] == self.provider
            and row["timeframe"] == timeframe
        ]
        rows.sort(key=lambda row: row["market_timestamp_utc"], reverse=True)
        return rows[:limit] if limit is not None else rows

    def latest_observation(
        self,
        timeframe: str,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Return the newest stored closed row that is not after ``as_of``."""

        rows = self.observations(timeframe)
        if as_of is None:
            return rows[0] if rows else None
        cutoff = _ensure_utc(as_of)
        for row in rows:
            if _parse_timestamp(row["market_timestamp_utc"]) <= cutoff:
                return row
        return None

    def context_snapshot(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        """Expose per-timeframe latest rows/readiness for later admission code."""

        statuses = {
            timeframe: self.status(timeframe, as_of=as_of).to_dict()
            for timeframe in self.timeframes
        }
        latest = {
            timeframe: self.latest_observation(timeframe, as_of=as_of)
            for timeframe in self.timeframes
        }
        return {
            "provider": self.provider,
            "venue": self.venue,
            "symbol": self.symbol,
            "as_of_timestamp_utc": _ensure_utc(as_of).isoformat() if as_of else None,
            "statuses": statuses,
            "latest": latest,
            "available_timeframes": tuple(
                timeframe for timeframe, row in latest.items() if row is not None
            ),
        }

    def status(self, timeframe: str, *, as_of: datetime | None = None) -> BufferStatus:
        rows = self.observations(timeframe)
        if as_of is not None:
            cutoff = _ensure_utc(as_of)
            rows = [
                row for row in rows
                if _parse_timestamp(row["market_timestamp_utc"]) <= cutoff
            ]
        row_count = len(rows)
        latest = _parse_timestamp(rows[0]["market_timestamp_utc"]) if rows else None
        available = ("PRICE", "VOLUME")
        missing = OPTIONAL_FIELDS
        return BufferStatus(
            provider=self.provider,
            symbol=self.symbol,
            timeframe=timeframe,
            row_count=row_count,
            latest_market_timestamp=latest,
            warmup_status="READY" if row_count >= self.min_closed else "WARMING_UP",
            available_fields=available,
            missing_fields=missing,
            persistence_path=str(self.storage_path),
        )

    def all_status(self) -> tuple[BufferStatus, ...]:
        return tuple(self.status(timeframe) for timeframe in self.timeframes)


def _metric(metrics: Any, name: str) -> float | None:
    value = metrics.get(name) if hasattr(metrics, "get") else None
    return float(value) if value is not None else None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)
