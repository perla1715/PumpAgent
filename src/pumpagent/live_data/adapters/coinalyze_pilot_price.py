"""Native Coinalyze 1m/5m price boundary for the TEST_ONLY Pilot handoff."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import socket
from typing import Any, Callable
from urllib import error, parse, request

from pumpagent.pilot.runtime_handoff import NativeCandle, NativeCandleBatch, PilotRuntimeInputError

COINALYZE_BASE_URL = "https://api.coinalyze.net/v1"
_INTERVALS = {"1m": ("1min", 60), "5m": ("5min", 300)}


@dataclass(frozen=True)
class CoinalyzeMarketResolutionSnapshot:
    """One validated /future-markets response, owned by a discovery cycle."""

    markets: tuple[dict[str, Any], ...]
    fetched_at_utc: datetime
    raw_sha256: str

    def provenance(self) -> dict[str, Any]:
        return {
            "provider": "coinalyze",
            "endpoint": "/future-markets",
            "fetched_at_utc": self.fetched_at_utc.isoformat(),
            "raw_sha256": self.raw_sha256,
            "market_count": len(self.markets),
        }


class CoinalyzeNativePilotPriceClient:
    """Injectable native-price client with an explicit discovery-cycle resolution scope."""

    def __init__(self, api_key: str | None = None, *, timeout_seconds: float = 20.0,
                 request_json: Callable[[str, dict[str, str]], Any] | None = None) -> None:
        self.api_key = api_key or os.environ.get("COINALYZE_API_KEY") or os.environ.get("COINALYZE_KEY") or os.environ.get("API_KEY")
        self.timeout_seconds = timeout_seconds
        self._request_json = request_json or self._get_json
        self._discovery_resolution: CoinalyzeMarketResolutionSnapshot | None = None

    def begin_discovery_cycle(self) -> CoinalyzeMarketResolutionSnapshot:
        """Fetch one coherent mapping snapshot for the caller's discovery cycle."""
        if self._discovery_resolution is not None:
            raise PilotRuntimeInputError("Coinalyze discovery-cycle resolution is already active")
        snapshot = self._fetch_market_resolution_snapshot()
        self._discovery_resolution = snapshot
        return snapshot

    def end_discovery_cycle(self) -> None:
        """Discard cycle-local metadata so independent runs always fetch afresh."""
        self._discovery_resolution = None

    def resolve_bybit_symbol(self, bybit_symbol: str) -> str:
        symbol = bybit_symbol.upper().strip()
        if not symbol:
            raise PilotRuntimeInputError("Bybit symbol is required")
        snapshot = self._discovery_resolution or self._fetch_market_resolution_snapshot()
        for market in snapshot.markets:
            candidate = str(market.get("symbol", "")).upper()
            quote = str(market.get("quote_asset", "")).upper()
            exchange = str(market.get("exchange", "")).upper()
            if candidate == f"{symbol}.6" and quote == "USDT" and (not exchange or "BYBIT" in exchange or exchange == "6"):
                return candidate
        raise PilotRuntimeInputError(f"No Coinalyze Bybit perpetual mapping for {symbol}")

    def _fetch_market_resolution_snapshot(self) -> CoinalyzeMarketResolutionSnapshot:
        payload = self._request_json("/future-markets", {})
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return CoinalyzeMarketResolutionSnapshot(
            markets=tuple(_markets(payload)),
            fetched_at_utc=datetime.now(timezone.utc),
            raw_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def fetch_closed_native(self, *, bybit_symbol: str, provider_symbol: str, timeframe: str,
                            evaluation_timestamp_utc: datetime) -> NativeCandleBatch:
        if timeframe not in _INTERVALS:
            raise PilotRuntimeInputError("Pilot requires native 1m or 5m evidence")
        evaluation = _utc(evaluation_timestamp_utc)
        interval, seconds = _INTERVALS[timeframe]
        # 25 minutes is the smallest bounded request that covers a closed 20m
        # profile window and permits one delayed closed observation.
        start = evaluation - timedelta(minutes=25)
        params = {"symbols": provider_symbol, "interval": interval,
                  "from": str(int(start.timestamp())), "to": str(int(evaluation.timestamp()))}
        payload = self._request_json("/ohlcv-history", params)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        candles: list[NativeCandle] = []
        for record in _history(payload, provider_symbol):
            timestamp = _timestamp(record)
            if timestamp < start or timestamp > evaluation or timestamp + timedelta(seconds=seconds) > evaluation:
                continue
            try:
                candles.append(NativeCandle(
                    timestamp_utc=timestamp,
                    open=float(record["o"]), high=float(record["h"]), low=float(record["l"]),
                    close=float(record["c"]), volume=float(record.get("v", 0.0)),
                    timeframe=timeframe, closed=True,
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise PilotRuntimeInputError(f"invalid native Coinalyze {timeframe} candle") from exc
        return NativeCandleBatch(
            bybit_symbol=bybit_symbol.upper(), provider_symbol=provider_symbol,
            timeframe=timeframe, candles=tuple(sorted(candles, key=lambda item: item.timestamp_utc)),
            raw_source_identity=f"coinalyze:/ohlcv-history:{provider_symbol}:{interval}:{int(start.timestamp())}:{int(evaluation.timestamp())}",
            raw_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _get_json(self, path: str, params: dict[str, str]) -> Any:
        if not self.api_key:
            raise PilotRuntimeInputError("COINALYZE_API_KEY/COINALYZE_KEY/API_KEY is unavailable")
        url = f"{COINALYZE_BASE_URL}{path}?{parse.urlencode(params)}"
        try:
            req = request.Request(url, headers={"api_key": self.api_key})
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise PilotRuntimeInputError(f"Coinalyze HTTP {exc.code} from {path}") from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise PilotRuntimeInputError(f"Coinalyze request failed for {path}: {exc}") from exc


def _markets(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("result"), list):
        return [item for item in payload["result"] if isinstance(item, dict)]
    raise PilotRuntimeInputError("unexpected Coinalyze future-markets response")


def _history(payload: Any, provider_symbol: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        direct = payload.get(provider_symbol) or payload.get(provider_symbol.upper()) or payload.get("history")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict) and isinstance(payload[0].get("history"), list):
            return [item for item in payload[0]["history"] if isinstance(item, dict)]
        return [item for item in payload if isinstance(item, dict)]
    raise PilotRuntimeInputError("unexpected Coinalyze OHLCV response")


def _timestamp(record: dict[str, Any]) -> datetime:
    value = record.get("t", record.get("timestamp"))
    if value is None:
        raise PilotRuntimeInputError("Coinalyze OHLCV record has no timestamp")
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PilotRuntimeInputError("evaluation timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
