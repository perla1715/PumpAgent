"""Public Bybit market transport for bounded local collection orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from urllib import parse, request


_INTERVAL_MINUTES = {"1": 1, "3": 3, "5": 5}


def fetch_instruments() -> dict[str, Any]:
    return _get("/v5/market/instruments-info", {"category": "linear", "limit": "500"})


def fetch_tickers() -> dict[str, Any]:
    return _get("/v5/market/tickers", {"category": "linear"})


def fetch_closed_1m_bars(symbol: str) -> list[dict[str, Any]]:
    payload = _get("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "1", "limit": "122"})
    return [{
        "timestamp": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).isoformat(),
        "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
        "close": float(row[4]), "volume": float(row[5]), "closed": True,
        "native_or_reconstructed": "NATIVE",
    } for row in payload["result"]["list"]]


def fetch_completed_native_bars(symbol: str, interval: str, *, limit: int,
                                asof: datetime | None = None) -> list[dict[str, Any]]:
    """Fetch only Bybit bars known closed at ``asof`` with explicit provenance.

    This additive helper is used by the isolated Pump Start stream.  It does
    not change the legacy latest-bar collector's transport or interpretation.
    """
    if interval not in _INTERVAL_MINUTES:
        raise ValueError("interval must be one of native 1, 3, or 5 minutes")
    cutoff = (asof or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload = _get("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": interval, "limit": str(limit)})
    minutes = _INTERVAL_MINUTES[interval]
    rows: list[dict[str, Any]] = []
    for raw in payload["result"]["list"]:
        timestamp = datetime.fromtimestamp(int(raw[0]) / 1000, tz=timezone.utc)
        closed = timestamp.timestamp() + minutes * 60 <= cutoff.timestamp()
        if not closed: continue
        rows.append({"timestamp": timestamp.isoformat(), "open": float(raw[1]), "high": float(raw[2]),
                     "low": float(raw[3]), "close": float(raw[4]), "volume": float(raw[5]),
                     "closed": True, "native_or_reconstructed": "NATIVE", "timeframe": f"{interval}m",
                     "provider": "bybit", "venue": "BYBIT_LINEAR"})
    return sorted(rows, key=lambda row: row["timestamp"])


def _get(path: str, params: dict[str, str]) -> dict[str, Any]:
    url = "https://api.bybit.com" + path + "?" + parse.urlencode(params)
    with request.urlopen(request.Request(url, headers={"Accept": "application/json"}), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))
