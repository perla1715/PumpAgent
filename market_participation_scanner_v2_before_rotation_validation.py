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
import sys
import time
from typing import Any
from urllib import error, parse, request


API_BASE_URL = "https://api.coinalyze.net/v1"
API_RATE_LIMIT_PER_MINUTE = 40
REQUEST_TIMEOUT_SECONDS = 20

SCAN_LIMIT = 20
SCAN_OFFSET = 0
HISTORY_INTERVAL = "5min"
HISTORY_INTERVAL_SECONDS = 5 * 60
INTERVAL_SECONDS = HISTORY_INTERVAL_SECONDS
HISTORY_LOOKBACK_SECONDS = 60 * 60
USE_CLOSED_CANDLES_ONLY = True
OHLCV_OI_ALIGNMENT_REQUIRED = True
VOLUME_BASELINE_CANDLES = 10
MIN_OHLCV_RECORDS = VOLUME_BASELINE_CANDLES + 1
MIN_OI_RECORDS = 2
TIMESTAMP_MISMATCH_REASON = "OHLCV/OI timestamp mismatch"


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
    calculation_evidence: dict[str, Any] | None = None


class CoinalyzeClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.request_units_used = 0
        self.rate_limit_pauses: list[dict[str, float | int]] = []
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
            self.rate_limit_pauses.append(
                {
                    "wait_seconds": wait_seconds,
                    "api_units_before_pause": self.request_units_used,
                    "window_units_before_pause": self._window_units_used,
                    "next_request_units": request_units,
                }
            )
            print(
                "RATE LIMIT PAUSE | "
                f"waiting {wait_seconds:.1f}s before next {request_units} API units "
                f"| API units before pause: {self.request_units_used}"
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
    skipped_timestamp_mismatch = 0
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

        first_result: MarketResult | None = None
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
                        now=now,
                    )
            except Exception as exc:  # noqa: BLE001 - one symbol must not stop the batch.
                result = MarketResult(
                    symbol=symbol,
                    status="ERROR",
                    reason=f"unexpected processing error: {exc}",
                )

            if first_result is None:
                first_result = result
            print_result(index, len(selected_markets), result)
            if result.status == "VALID":
                valid += 1
            elif result.status == "SKIPPED":
                skipped += 1
                if result.reason and result.reason.startswith(TIMESTAMP_MISMATCH_REASON):
                    skipped_timestamp_mismatch += 1
            else:
                failed += 1

        if first_result is not None:
            print_calculation_evidence(first_result)
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
        print(f"Markets skipped timestamp mismatch: {skipped_timestamp_mismatch}")
        print(f"Markets failed: {failed}")
        print(f"API requests used: {client.request_units_used}")
        print(f"Rate-limit pauses: {len(client.rate_limit_pauses)}")
        for index, pause in enumerate(client.rate_limit_pauses, start=1):
            print(
                f"Rate-limit pause {index}: waited {pause['wait_seconds']:.1f}s | "
                f"API units before pause: {pause['api_units_before_pause']} | "
                f"API units after pause: {client.request_units_used}"
            )
        print(f"Elapsed time: {elapsed:.2f}s")


def load_config() -> Config:
    load_dotenv(Path(".env"))
    api_key = (
        os.environ.get("COINALYZE_API_KEY")
        or os.environ.get("COINALYZE_KEY")
        or os.environ.get("API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "Missing Coinalyze API key. Set COINALYZE_API_KEY, COINALYZE_KEY, "
            "or API_KEY."
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
    *,
    now: int,
) -> MarketResult:
    closed_ohlcv = _closed_sorted_records(
        ohlcv_history,
        now=now,
        interval_seconds=HISTORY_INTERVAL_SECONDS,
    )
    closed_oi = _closed_sorted_records(
        oi_history,
        now=now,
        interval_seconds=HISTORY_INTERVAL_SECONDS,
    )

    if len(closed_ohlcv) < MIN_OHLCV_RECORDS:
        return MarketResult(
            symbol=symbol,
            status="SKIPPED",
            reason=(
                "insufficient closed OHLCV history "
                f"({len(closed_ohlcv)}/{MIN_OHLCV_RECORDS})"
            ),
        )
    if len(closed_oi) < MIN_OI_RECORDS:
        return MarketResult(
            symbol=symbol,
            status="SKIPPED",
            reason=(
                "insufficient closed Open Interest history "
                f"({len(closed_oi)}/{MIN_OI_RECORDS})"
            ),
        )

    latest_candle = closed_ohlcv[-1]
    baseline_candles = closed_ohlcv[-(VOLUME_BASELINE_CANDLES + 1) : -1]
    previous_oi_record = closed_oi[-2]
    latest_oi_record = closed_oi[-1]
    latest_ohlcv_timestamp = _as_int(latest_candle.get("t"))
    latest_oi_timestamp = _as_int(latest_oi_record.get("t"))

    if latest_ohlcv_timestamp is None or latest_oi_timestamp is None:
        return MarketResult(
            symbol=symbol,
            status="SKIPPED",
            reason="invalid OHLCV/OI timestamp data",
        )

    latest_ohlcv_bucket = interval_bucket(latest_ohlcv_timestamp)
    latest_oi_bucket = interval_bucket(latest_oi_timestamp)
    bucket_difference = latest_ohlcv_bucket - latest_oi_bucket
    alignment_evidence = {
        "latest_ohlcv_timestamp": latest_ohlcv_timestamp,
        "latest_oi_timestamp": latest_oi_timestamp,
        "latest_ohlcv_bucket": latest_ohlcv_bucket,
        "latest_oi_bucket": latest_oi_bucket,
        "aligned": latest_ohlcv_bucket == latest_oi_bucket,
    }

    if OHLCV_OI_ALIGNMENT_REQUIRED and latest_ohlcv_bucket != latest_oi_bucket:
        return MarketResult(
            symbol=symbol,
            status="SKIPPED",
            reason=(
                f"{TIMESTAMP_MISMATCH_REASON} | "
                f"OHLCV bucket: {latest_ohlcv_bucket} | "
                f"OI bucket: {latest_oi_bucket} | "
                f"Difference in seconds: {bucket_difference}"
            ),
            calculation_evidence=alignment_evidence,
        )

    latest_open = _as_float(latest_candle.get("o"))
    latest_close = _as_float(latest_candle.get("c"))
    latest_volume = _as_float(latest_candle.get("v"))
    baseline_volumes = [_as_float(candle.get("v")) for candle in baseline_candles]
    baseline_volumes = [value for value in baseline_volumes if value is not None]

    previous_oi = _as_float(previous_oi_record.get("c"))
    latest_oi = _as_float(latest_oi_record.get("c"))

    if latest_open is None or latest_close is None or latest_open == 0:
        return MarketResult(symbol=symbol, status="SKIPPED", reason="invalid price data")
    if latest_volume is None or len(baseline_volumes) < VOLUME_BASELINE_CANDLES:
        return MarketResult(symbol=symbol, status="SKIPPED", reason="invalid volume data")
    if previous_oi is None or latest_oi is None or previous_oi == 0:
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
        price_change_pct=_pct_change(latest_open, latest_close),
        volume_ratio=latest_volume / average_baseline_volume,
        oi_change_pct=_pct_change(previous_oi, latest_oi),
        calculation_evidence={
            **alignment_evidence,
            "latest_completed_timestamp": latest_candle.get("t"),
            "latest_open": latest_open,
            "latest_close": latest_close,
            "baseline_volumes": baseline_volumes,
            "latest_volume": latest_volume,
            "previous_oi": previous_oi,
            "latest_oi": latest_oi,
        },
    )


def print_result(index: int, total: int, result: MarketResult) -> None:
    prefix = f"[{index:02d}/{total:02d}] {result.symbol}"
    if result.status == "VALID":
        print(
            f"{prefix} | PRICE_5M: {result.price_change_pct:+.2f}% | "
            f"VOLUME_RATIO_5M: {result.volume_ratio:.2f}x | "
            f"OI_CHANGE_5M: {result.oi_change_pct:+.2f}% | STATUS: VALID"
        )
        return

    print(f"{prefix} | STATUS: {result.status} | REASON: {result.reason}")


def print_calculation_evidence(result: MarketResult) -> None:
    print()
    print("FIRST MARKET CALCULATION EVIDENCE")
    print(f"Symbol: {result.symbol}")
    if result.calculation_evidence is not None:
        evidence = result.calculation_evidence
        print(
            "Latest completed OHLCV timestamp: "
            f"{_format_timestamp(evidence.get('latest_ohlcv_timestamp'))}"
        )
        print(
            "Latest completed OI timestamp: "
            f"{_format_timestamp(evidence.get('latest_oi_timestamp'))}"
        )
        print(f"OHLCV 5m bucket: {evidence.get('latest_ohlcv_bucket')}")
        print(f"OI 5m bucket: {evidence.get('latest_oi_bucket')}")
        print(f"Aligned: {evidence.get('aligned')}")

    if result.status != "VALID" or result.calculation_evidence is None:
        print(f"Status: {result.status}")
        print(f"Reason: {result.reason}")
        return

    evidence = result.calculation_evidence
    print(
        "Latest completed timestamp: "
        f"{_format_timestamp(evidence['latest_completed_timestamp'])}"
    )
    print(f"Previous close/open: {evidence['latest_open']}")
    print(f"Latest close: {evidence['latest_close']}")
    print(f"Previous baseline volumes: {_format_float_list(evidence['baseline_volumes'])}")
    print(f"Latest comparison volume: {evidence['latest_volume']}")
    print(f"Previous OI: {evidence['previous_oi']}")
    print(f"Latest OI: {evidence['latest_oi']}")
    print(f"Calculated PRICE: {result.price_change_pct:+.4f}%")
    print(f"Calculated VOLUME RATIO: {result.volume_ratio:.4f}x")
    print(f"Calculated OI CHANGE: {result.oi_change_pct:+.4f}%")


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


def _closed_sorted_records(
    records: list[dict[str, Any]],
    *,
    now: int,
    interval_seconds: int,
) -> list[dict[str, Any]]:
    by_timestamp: dict[int, dict[str, Any]] = {}
    for record in records:
        timestamp = _as_int(record.get("t"))
        if timestamp is None:
            continue
        if USE_CLOSED_CANDLES_ONLY and timestamp + interval_seconds > now:
            continue
        by_timestamp[timestamp] = record

    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def interval_bucket(timestamp: int) -> int:
    return timestamp - (timestamp % INTERVAL_SECONDS)


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


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pct_change(start: float, end: float) -> float:
    return ((end - start) / start) * 100.0


def _safe_error_detail(detail: str) -> str:
    if not detail:
        return "no response body"
    return detail[:300].replace("\n", " ")


def _format_timestamp(value: Any) -> str:
    timestamp = _as_int(value)
    if timestamp is None:
        return "UNKNOWN"
    return f"{timestamp}"


def _format_float_list(values: list[float]) -> str:
    return "[" + ", ".join(f"{value:.4f}" for value in values) + "]"


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
