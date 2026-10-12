"""Public Bybit market transport for bounded local collection orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from urllib import parse, request


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


def _get(path: str, params: dict[str, str]) -> dict[str, Any]:
    url = "https://api.bybit.com" + path + "?" + parse.urlencode(params)
    with request.urlopen(request.Request(url, headers={"Accept": "application/json"}), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))
