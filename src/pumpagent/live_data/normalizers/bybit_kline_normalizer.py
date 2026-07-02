"""Bybit Kline raw payload normalizer.

This module performs only structural transformation from Bybit raw Kline
transport output into the generic Live Data contract. It does not validate
quality, translate quality, call Runtime, or communicate with exchanges.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pumpagent.live_data.domain import (
    LiveDataError,
    LiveDataErrorType,
    LiveDataQualityStatus,
    LiveDataResult,
    LiveDataTransport,
    NormalizedMarketDataInput,
    SourceMetadata,
)


def normalize_bybit_kline_raw_data(raw_result: LiveDataResult) -> LiveDataResult:
    """Transform successful Bybit Kline raw data into NormalizedMarketDataInput."""

    if not raw_result.success:
        return LiveDataResult(success=False, error=raw_result.error)

    raw_data = raw_result.raw_data
    if raw_data is None:
        return _transformation_error("Bybit raw_data is missing.", "missing_raw_data")

    payload = raw_data.get("payload")
    if payload is None:
        return _transformation_error("Bybit raw payload is missing.", "missing_payload")

    request_metadata = raw_data.get("request_metadata")
    if request_metadata is None:
        return _transformation_error(
            "Bybit request metadata is missing.",
            "missing_request_metadata",
        )

    params = request_metadata["params"]
    result = payload.get("result")
    if result is None:
        return _transformation_error("Bybit payload result is missing.", "missing_result")

    candles = result.get("list")
    if candles is None:
        return _transformation_error(
            "Bybit payload result.list is missing.",
            "missing_list",
        )
    if not candles:
        return _transformation_error(
            "Bybit payload result.list is empty.",
            "empty_list",
        )

    malformed_candle = _first_malformed_candle(candles)
    if malformed_candle is not None:
        return _transformation_error(
            "Bybit candle row is malformed.",
            malformed_candle,
        )

    latest_candle_result = _latest_candle(candles)
    if isinstance(latest_candle_result, LiveDataResult):
        return latest_candle_result
    latest_candle = latest_candle_result

    source_timestamp_result = _timestamp_from_ms(latest_candle[0])
    if isinstance(source_timestamp_result, LiveDataResult):
        return source_timestamp_result
    source_timestamp = source_timestamp_result
    receive_timestamp = datetime.now(timezone.utc)
    symbol = str(result.get("symbol") or params["symbol"])
    timeframe = str(params["interval"])

    close_result = _numeric_value(latest_candle[4], "close")
    if isinstance(close_result, LiveDataResult):
        return close_result
    close = close_result

    volume_result = _numeric_value(latest_candle[5], "volume")
    if isinstance(volume_result, LiveDataResult):
        return volume_result
    volume = volume_result

    normalized_candles = []
    for candle in candles:
        normalized_candle = _normalize_candle(candle)
        if isinstance(normalized_candle, LiveDataResult):
            return normalized_candle
        normalized_candles.append(normalized_candle)

    turnover_result = _numeric_value(latest_candle[6], "turnover")
    if isinstance(turnover_result, LiveDataResult):
        return turnover_result

    normalized = NormalizedMarketDataInput(
        source_event_id=_source_event_id(symbol, timeframe, latest_candle[0]),
        symbol=symbol,
        exchange="bybit",
        timeframe=timeframe,
        source_timestamp=source_timestamp,
        receive_timestamp=receive_timestamp,
        price=close,
        ohlcv=tuple(normalized_candles),
        volume=volume,
        data_source="bybit_public_kline",
        quality_status=LiveDataQualityStatus.UNKNOWN,
        source_metadata=SourceMetadata(
            exchange="bybit",
            adapter_name="bybit",
            adapter_version="0.3",
            source_timestamp=source_timestamp,
            receive_timestamp=receive_timestamp,
            transport=LiveDataTransport.REST,
            source_symbol=symbol,
            normalized_symbol=symbol,
            source_timeframe=timeframe,
            normalized_timeframe=timeframe,
        ),
        optional_market_metrics={
            "category": result.get("category") or params.get("category"),
            "turnover": turnover_result,
        },
        raw_payload_reference=raw_data.get("endpoint"),
    )
    return LiveDataResult(success=True, data=normalized)


def _first_malformed_candle(candles: Any) -> str | None:
    for candle in candles:
        if not isinstance(candle, (list, tuple)):
            return "malformed_candle"
        if len(candle) < 7:
            return "insufficient_candle_fields"
    return None


def _latest_candle(candles: list[Any]) -> Any | LiveDataResult:
    try:
        return max(candles, key=lambda candle: int(candle[0]))
    except (TypeError, ValueError):
        return _transformation_error(
            "Bybit candle timestamp could not be converted.",
            "invalid_timestamp",
        )


def _normalize_candle(candle: Any) -> dict[str, Any] | LiveDataResult:
    timestamp = _timestamp_from_ms(candle[0])
    if isinstance(timestamp, LiveDataResult):
        return timestamp

    open_value = _numeric_value(candle[1], "open")
    high_value = _numeric_value(candle[2], "high")
    low_value = _numeric_value(candle[3], "low")
    close_value = _numeric_value(candle[4], "close")
    volume_value = _numeric_value(candle[5], "volume")
    values = (open_value, high_value, low_value, close_value, volume_value)
    for value in values:
        if isinstance(value, LiveDataResult):
            return value

    return {
        "timestamp": timestamp,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "volume": volume_value,
    }


def _timestamp_from_ms(value: Any) -> datetime | LiveDataResult:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return _transformation_error(
            "Bybit candle timestamp could not be converted.",
            "invalid_timestamp",
        )


def _numeric_value(value: Any, field_name: str) -> float | LiveDataResult:
    try:
        return float(value)
    except (TypeError, ValueError):
        return _transformation_error(
            f"Bybit candle {field_name} could not be converted.",
            "invalid_numeric",
        )


def _source_event_id(symbol: str, timeframe: str, source_timestamp_ms: Any) -> str:
    return f"bybit:kline:linear:{symbol}:{timeframe}:{source_timestamp_ms}"


def _transformation_error(message: str, reason: str) -> LiveDataResult:
    return LiveDataResult(
        success=False,
        error=LiveDataError(
            error_type=LiveDataErrorType.MALFORMED_PAYLOAD,
            message=message,
            exchange="bybit",
            symbol="",
            timeframe="",
            receive_timestamp=datetime.now(timezone.utc),
            retryable=False,
            validation_errors=(reason,),
        ),
    )
