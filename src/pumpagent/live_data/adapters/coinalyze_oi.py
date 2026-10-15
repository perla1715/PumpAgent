"""Bounded Coinalyze single-symbol futures Open Interest side-channel.

Bybit remains the venue and native OHLCV source.  This adapter supplies only
closed native Coinalyze 5-minute single-symbol OI with explicit provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
from typing import Any
from urllib import error, parse, request


COINALYZE_BASE_URL = "https://api.coinalyze.net/v1"
INTERVAL_SECONDS = 300


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CoinalyzeOIRecord:
    bybit_symbol: str
    coinalyze_market_symbol: str
    market_timestamp_utc: datetime
    fetch_timestamp_utc: datetime
    value: float
    unit: str = "contracts_or_provider_native_oi"
    timeframe: str = "5m"
    metric_provider: str = "coinalyze"
    venue_mapping: str = "BYBIT_PERPETUAL"
    closed_record_status: str = "CLOSED_NATIVE_5M"
    provenance: str = "coinalyze:/open-interest-history"
    missingness_status: str = "AVAILABLE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bybit_symbol": self.bybit_symbol,
            "coinalyze_market_symbol": self.coinalyze_market_symbol,
            "market_timestamp_utc": self.market_timestamp_utc.isoformat(),
            "fetch_timestamp_utc": self.fetch_timestamp_utc.isoformat(),
            "value": self.value, "unit": self.unit, "timeframe": self.timeframe,
            "metric_provider": self.metric_provider, "venue_mapping": self.venue_mapping,
            "closed_record_status": self.closed_record_status,
            "provenance": self.provenance, "missingness_status": self.missingness_status,
        }


class CoinalyzeOISideChannel:
    """Small HTTP client and deterministic parser for Coinalyze futures OI."""

    def __init__(self, api_key: str | None = None, *, timeout_seconds: float = 20.0) -> None:
        self.api_key = api_key or os.environ.get("COINALYZE_API_KEY") or os.environ.get("COINALYZE_KEY") or os.environ.get("API_KEY")
        self.timeout_seconds = timeout_seconds

    def resolve_market(self, bybit_symbol: str, *, markets: list[dict[str, Any]] | None = None) -> str:
        symbol = bybit_symbol.upper().strip()
        if not symbol:
            raise ValueError("bybit_symbol must be non-empty")
        values = markets if markets is not None else self._get_json("/future-markets", {})
        for market in values:
            candidate = str(market.get("symbol", "")).upper()
            quote = str(market.get("quote_asset", "")).upper()
            exchange = str(market.get("exchange", "")).upper()
            if candidate.rstrip(".6") == symbol and quote == "USDT" and (not exchange or "BYBIT" in exchange or exchange == "6"):
                return candidate
            if candidate == f"{symbol}.6" and (not exchange or "BYBIT" in exchange or exchange == "6"):
                return candidate
        raise LookupError(f"No Coinalyze Bybit perpetual mapping for {symbol}")

    def fetch_closed_5m_oi(
        self, bybit_symbol: str, *, start: datetime, end: datetime, now: datetime | None = None,
        market_symbol: str | None = None, records: list[dict[str, Any]] | None = None,
    ) -> list[CoinalyzeOIRecord]:
        start_utc, end_utc = _utc(start), _utc(end)
        current = _utc(now or datetime.now(timezone.utc))
        mapped = market_symbol or self.resolve_market(bybit_symbol)
        raw = records if records is not None else self._get_json("/open-interest-history", {
            "symbols": mapped, "interval": "5min", "from": str(int(start_utc.timestamp())), "to": str(int(end_utc.timestamp())),
        })
        history = _history_for_symbol(raw, mapped)
        result: list[CoinalyzeOIRecord] = []
        for item in history:
            timestamp = _record_timestamp(item)
            if timestamp < start_utc or timestamp > end_utc or timestamp + timedelta(seconds=INTERVAL_SECONDS) > current:
                continue
            value = item.get("c", item.get("value", item.get("oi")))
            if value is None:
                continue
            result.append(CoinalyzeOIRecord(bybit_symbol=bybit_symbol.upper(), coinalyze_market_symbol=mapped,
                market_timestamp_utc=timestamp, fetch_timestamp_utc=current, value=float(value)))
        result.sort(key=lambda value: value.market_timestamp_utc)
        dedup: dict[datetime, CoinalyzeOIRecord] = {item.market_timestamp_utc: item for item in result}
        return list(dedup.values())

    @staticmethod
    def align_to_bybit_5m(records: list[CoinalyzeOIRecord], *, bybit_timestamp: datetime, asof: datetime) -> CoinalyzeOIRecord | None:
        target, cutoff = _utc(bybit_timestamp), _utc(asof)
        for record in records:
            if (record.market_timestamp_utc == target
                    and record.market_timestamp_utc + timedelta(seconds=INTERVAL_SECONDS) <= cutoff
                    and record.fetch_timestamp_utc <= cutoff):
                return record
        return None

    def _get_json(self, path: str, params: dict[str, str]) -> Any:
        if not self.api_key:
            raise RuntimeError("COINALYZE_API_KEY/COINALYZE_KEY/API_KEY is unavailable")
        url = f"{COINALYZE_BASE_URL}{path}?{parse.urlencode(params)}"
        try:
            req = request.Request(url, headers={"api_key": self.api_key})
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise RuntimeError(f"Coinalyze HTTP {exc.code} from {path}") from exc
        except (error.URLError, TimeoutError, socket.timeout) as exc:
            raise RuntimeError(f"Coinalyze request failed for {path}: {exc}") from exc


class OISidecarStore:
    """Append-only restart-safe OI metric sidecar."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self.path.touch(exist_ok=True)

    def records(self) -> list[CoinalyzeOIRecord]:
        result = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line: continue
            value = json.loads(line)
            result.append(CoinalyzeOIRecord(bybit_symbol=value["bybit_symbol"], coinalyze_market_symbol=value["coinalyze_market_symbol"],
                market_timestamp_utc=_utc(datetime.fromisoformat(value["market_timestamp_utc"])), fetch_timestamp_utc=_utc(datetime.fromisoformat(value["fetch_timestamp_utc"])),
                value=float(value["value"]), unit=value["unit"], timeframe=value["timeframe"], metric_provider=value["metric_provider"],
                venue_mapping=value["venue_mapping"], closed_record_status=value["closed_record_status"], provenance=value["provenance"], missingness_status=value["missingness_status"]))
        return result

    def records_for_symbol(self, bybit_symbol: str) -> list[CoinalyzeOIRecord]:
        return [item for item in self.records() if item.bybit_symbol == bybit_symbol.upper()]

    @staticmethod
    def align_to_bybit_5m(records: list[CoinalyzeOIRecord], *, bybit_timestamp: datetime, asof: datetime) -> CoinalyzeOIRecord | None:
        return CoinalyzeOISideChannel.align_to_bybit_5m(records, bybit_timestamp=bybit_timestamp, asof=asof)

    def append(self, record: CoinalyzeOIRecord) -> str:
        for existing in self.records():
            if existing.bybit_symbol == record.bybit_symbol and existing.market_timestamp_utc == record.market_timestamp_utc:
                if existing.to_dict() != record.to_dict(): raise ValueError("OI sidecar identity conflict")
                return "DUPLICATE_NOOP"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record.to_dict(), sort_keys=True) + "\n"); stream.flush(); os.fsync(stream.fileno())
        return "APPENDED"


def _history_for_symbol(payload: Any, symbol: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(symbol) or payload.get(symbol.upper()) or payload.get("history")
        if isinstance(value, list): return [item for item in value if isinstance(item, dict)]
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict) and "history" in payload[0]:
            return [item for item in payload[0]["history"] if isinstance(item, dict)]
        return [item for item in payload if isinstance(item, dict)]
    raise ValueError("Unexpected Coinalyze history response")


def _record_timestamp(item: dict[str, Any]) -> datetime:
    raw = item.get("t", item.get("timestamp"))
    if raw is None: raise ValueError("Coinalyze OI record has no timestamp")
    return datetime.fromtimestamp(float(raw), tz=timezone.utc)
