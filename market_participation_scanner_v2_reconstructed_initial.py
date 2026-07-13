"""Minimal reconstructed Market Participation Scanner v2.

This script intentionally stays narrow: it loads Coinalyze Bybit USDT futures,
selects a SCAN_OFFSET/SCAN_LIMIT batch, fetches 5-minute OHLCV and open
interest history, and reports one line for every selected market.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import time
from typing import Any
from urllib import error, parse, request


API_BASE_URL = "https://api.coinalyze.net/v1"
API_RATE_LIMIT_PER_MINUTE = 40
REQUEST_TIMEOUT_SECONDS = 20

SCAN_LIMIT = 20
SCAN_OFFSET = 0
HISTORY_INTERVAL = "5min"
HISTORY_LOOKBACK_SECONDS = 60 * 60
MIN_HISTORY_POINTS = 2


@dataclass(frozen=True)
class Config:
    api_key: str
    scan_limit: int
    scan_offset: int


@dataclass(frozen=True)
class MarketResult:
    symbol: str
    status: str
    reason: str | None = None
    price_change_pct: float | None = None
    volume_ratio: float | None = None
    oi_change_pct: float | None = None


class CoinalyzeClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.request_units_used = 0
        self._window_started_at = time.monotonic()
        self._window_units_used = 0

    def get_future_markets(self) -> list[dict[str, Any]]:
        payload = self._get_json("/future-markets", {}, request_units=1)
        if not isinstance(payload, list):
            raise RuntimeError("future-markets response was not a list")
        return [market for market in payload if isinstance(market, dict)]

    def get_ohlcv_history(
        self, symbols: list[str], *, start: int, end: int
    ) -> dict[str, list[dict[str, Any]]]:
        payload = self._get_json(
            "/ohlcv-history",
            {
                "symbols": ",".join(symbols),
                "interval": HISTORY_INTERVAL,
                "from": str(start),
                "to": str(end),
            },
            request_units=len(symbols),
        )
        return _history_by_symbol(payload, history_name="history")

    def get_open_interest_history(
        self, symbols: list[str], *, start: int, end: int
    ) -> dict[str, list[dict[str, Any]]]:
        payload = self._get_json(
            "/open-interest-history",
            {
                "symbols": ",".join(symbols),
                "interval": HISTORY_INTERVAL,
                "from": str(start),
                "to": str(end),
            },
            request_units=len(symbols),
        )
        return _history_by_symbol(payload, history_name="history")

    def _get_json(
        self, path: str, params: dict[str, str], *, request_units: int
    ) -> Any:
        self._wait_for_capacity(request_units)
        url = f"{API_BASE_URL}{path}"
        if params:
            url = f"{url}?{parse.urlencode(params)}"

        api_request = request.Request(url, headers={"api_key": self.api_key})
        try:
            with request.urlopen(
                api_request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"HTTP {exc.code} from {path}: {_safe_error_detail(detail)}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(f"request failed for {path}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"request timed out for {path}") from exc

        self.request_units_used += request_units
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON from {path}") from exc

    def _wait_for_capacity(self, request_units: int) -> None:
        now = time.monotonic()
        elapsed = now - self._window_started_at
        if elapsed >= 60:
            self._window_started_at = now
            self._window_units_used = 0

        if self._window_units_used + request_units > API_RATE_LIMIT_PER_MINUTE:
            wait_seconds = max(0.0, 60.0 - elapsed) + 1.0
            print(
                "RATE LIMIT PAUSE | "
                f"waiting {wait_seconds:.1f}s before next {request_units} API units"
            )
            time.sleep(wait_seconds)
            self._window_started_at = time.monotonic()
            self._window_units_used = 0

        self._window_units_used += request_units


def main() -> None:
    started_at = time.monotonic()
    config = load_config()
    client = CoinalyzeClient(config.api_key)

    available_count = 0
    selected_markets: list[dict[str, Any]] = []
    attempted = 0
    valid = 0
    skipped = 0
    failed = 0

    try:
        all_markets = client.get_future_markets()
        markets = filter_bybit_usdt_markets(all_markets)
        available_count = len(markets)
        selected_markets = markets[
            config.scan_offset : config.scan_offset + config.scan_limit
        ]

        print(f"Markets available: {available_count}")
        print(f"Scan offset: {config.scan_offset}")
        print(f"Scan limit: {config.scan_limit}")
        print(f"Markets selected: {len(selected_markets)}")

        symbols = [str(market["symbol"]) for market in selected_markets]
        now = int(time.time())
        start = now - HISTORY_LOOKBACK_SECONDS

        ohlcv_by_symbol: dict[str, list[dict[str, Any]]] = {}
        oi_by_symbol: dict[str, list[dict[str, Any]]] = {}
        batch_error: str | None = None

        if symbols:
            try:
                ohlcv_by_symbol = client.get_ohlcv_history(
                    symbols, start=start, end=now
                )
                oi_by_symbol = client.get_open_interest_history(
                    symbols, start=start, end=now
                )
            except Exception as exc:  # noqa: BLE001 - batch failure is reported per market.
                batch_error = str(exc)

        for index, market in enumerate(selected_markets, start=1):
            attempted += 1
            symbol = str(market["symbol"])
            try:
                if batch_error is not None:
                    result = MarketResult(
                        symbol=symbol,
                        status="ERROR",
                        reason=f"batch API request failed: {batch_error}",
                    )
                else:
                    result = process_market(
                        symbol,
                        ohlcv_by_symbol.get(symbol, []),
                        oi_by_symbol.get(symbol, []),
                    )
            except Exception as exc:  # noqa: BLE001 - one symbol must not stop the batch.
                result = MarketResult(
                    symbol=symbol,
                    status="ERROR",
                    reason=f"unexpected processing error: {exc}",
                )

            print_result(index, len(selected_markets), result)
            if result.status == "VALID":
                valid += 1
            elif result.status == "SKIPPED":
                skipped += 1
            else:
                failed += 1
    finally:
        elapsed = time.monotonic() - started_at
        print()
        print("SCAN SUMMARY")
        print(f"Markets available: {available_count}")
        print(f"Scan offset: {config.scan_offset}")
        print(f"Scan limit: {config.scan_limit}")
        print(f"Markets selected: {len(selected_markets)}")
        print(f"Markets attempted: {attempted}")
        print(f"Markets valid: {valid}")
        print(f"Markets skipped: {skipped}")
        print(f"Markets failed: {failed}")
        print(f"API requests used: {client.request_units_used}")
        print(f"Elapsed time: {elapsed:.2f}s")


def load_config() -> Config:
    load_dotenv(Path(".env"))
    api_key = os.environ.get("COINALYZE_API_KEY") or os.environ.get("API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing Coinalyze API key. Set COINALYZE_API_KEY or API_KEY."
        )

    return Config(
        api_key=api_key,
        scan_limit=_env_int("SCAN_LIMIT", SCAN_LIMIT),
        scan_offset=_env_int("SCAN_OFFSET", SCAN_OFFSET),
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def filter_bybit_usdt_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = []
    for market in markets:
        symbol = str(market.get("symbol", ""))
        exchange = str(market.get("exchange", "")).upper()
        quote_asset = str(market.get("quote_asset", "")).upper()
        is_perpetual = bool(market.get("is_perpetual", False))
        has_ohlcv = bool(market.get("has_ohlcv_data", False))

        if not symbol.endswith(".6"):
            continue
        if quote_asset != "USDT":
            continue
        if exchange and "BYBIT" not in exchange and exchange != "6":
            continue
        if not is_perpetual:
            continue
        if not has_ohlcv:
            continue
        filtered.append(market)

    return sorted(filtered, key=lambda item: str(item.get("symbol", "")))


def process_market(
    symbol: str,
    ohlcv_history: list[dict[str, Any]],
    oi_history: list[dict[str, Any]],
) -> MarketResult:
    if len(ohlcv_history) < MIN_HISTORY_POINTS:
        return MarketResult(
            symbol=symbol,
            status="SKIPPED",
            reason="insufficient OHLCV history",
        )
    if len(oi_history) < MIN_HISTORY_POINTS:
        return MarketResult(
            symbol=symbol,
            status="SKIPPED",
            reason="insufficient Open Interest history",
        )

    first_close = _as_float(ohlcv_history[0].get("c"))
    latest_close = _as_float(ohlcv_history[-1].get("c"))
    latest_volume = _as_float(ohlcv_history[-1].get("v"))
    baseline_volumes = [_as_float(candle.get("v")) for candle in ohlcv_history[:-1]]
    baseline_volumes = [value for value in baseline_volumes if value is not None]

    first_oi = _as_float(oi_history[0].get("c"))
    latest_oi = _as_float(oi_history[-1].get("c"))

    if first_close is None or latest_close is None or first_close == 0:
        return MarketResult(symbol=symbol, status="SKIPPED", reason="invalid price data")
    if latest_volume is None or not baseline_volumes:
        return MarketResult(symbol=symbol, status="SKIPPED", reason="invalid volume data")
    if first_oi is None or latest_oi is None or first_oi == 0:
        return MarketResult(symbol=symbol, status="SKIPPED", reason="invalid OI data")

    average_baseline_volume = sum(baseline_volumes) / len(baseline_volumes)
    if average_baseline_volume == 0:
        return MarketResult(
            symbol=symbol,
            status="SKIPPED",
            reason="zero baseline volume",
        )

    return MarketResult(
        symbol=symbol,
        status="VALID",
        price_change_pct=_pct_change(first_close, latest_close),
        volume_ratio=latest_volume / average_baseline_volume,
        oi_change_pct=_pct_change(first_oi, latest_oi),
    )


def print_result(index: int, total: int, result: MarketResult) -> None:
    prefix = f"[{index:02d}/{total:02d}] {result.symbol}"
    if result.status == "VALID":
        print(
            f"{prefix} | PRICE: {result.price_change_pct:+.2f}% | "
            f"VOLUME: {result.volume_ratio:.2f}x | "
            f"OI: {result.oi_change_pct:+.2f}% | STATUS: VALID"
        )
        return

    print(f"{prefix} | STATUS: {result.status} | REASON: {result.reason}")


def _history_by_symbol(payload: Any, *, history_name: str) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(payload, list):
        raise RuntimeError("history response was not a list")

    histories: dict[str, list[dict[str, Any]]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        history = item.get(history_name)
        if isinstance(symbol, str) and isinstance(history, list):
            histories[symbol] = [
                candle for candle in history if isinstance(candle, dict)
            ]
    return histories


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be zero or greater")
    return parsed


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(start: float, end: float) -> float:
    return ((end - start) / start) * 100.0


def _safe_error_detail(detail: str) -> str:
    if not detail:
        return "no response body"
    return detail[:300].replace("\n", " ")


if __name__ == "__main__":
    main()
