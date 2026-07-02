"""Bybit public Kline adapter v0.3.

This adapter performs only the approved public REST Kline transport. It does
not normalize payloads or call Runtime.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from urllib import error, parse, request
from typing import Any

from pumpagent.live_data.adapters.adapter_capabilities import AdapterCapabilities
from pumpagent.live_data.adapters.base_adapter import BaseLiveDataAdapter
from pumpagent.live_data.domain import (
    LiveDataError,
    LiveDataErrorType,
    LiveDataResult,
    LiveDataTransport,
)


class BybitAdapter(BaseLiveDataAdapter):
    """Bybit public Kline transport adapter without normalization or Runtime calls."""

    EXCHANGE_NAME = "bybit"
    KLINE_ENDPOINT = "/v5/market/kline"
    SUPPORTED_MARKET_CATEGORIES = ("linear",)
    SUPPORTED_TIMEFRAMES = (
        "1",
        "3",
        "5",
        "15",
        "30",
        "60",
        "120",
        "240",
        "360",
        "720",
        "D",
        "W",
        "M",
    )

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def load(
        self,
        symbol: str,
        timeframe: str,
        *,
        category: str = "linear",
    ) -> LiveDataResult:
        """LiveDataSource entry point for latest Bybit Kline acquisition."""

        return self.load_latest_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            category=category,
        )

    def load_latest_snapshot(
        self,
        symbol: str,
        timeframe: str,
        *,
        category: str = "linear",
    ) -> LiveDataResult:
        """Prepare a latest Kline request and delegate to mocked transport."""

        validation_error = self._validate_request(
            symbol=symbol,
            timeframe=timeframe,
            category=category,
        )
        if validation_error is not None:
            return LiveDataResult(success=False, error=validation_error)

        request_metadata = self._build_kline_request_metadata(
            symbol=symbol,
            timeframe=timeframe,
            category=category,
            limit=1,
        )
        return self._perform_request(request_metadata)

    def load_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        category: str = "linear",
    ) -> LiveDataResult:
        """Prepare a historical Kline request and delegate to mocked transport."""

        validation_error = self._validate_request(
            symbol=symbol,
            timeframe=timeframe,
            category=category,
        )
        if validation_error is not None:
            return LiveDataResult(success=False, error=validation_error)

        request_metadata = self._build_kline_request_metadata(
            symbol=symbol,
            timeframe=timeframe,
            category=category,
            limit=limit,
            start=start,
            end=end,
        )
        return self._perform_request(request_metadata)

    def capabilities(self) -> AdapterCapabilities:
        """Report Bybit v0.3 public REST Kline capabilities."""

        return AdapterCapabilities(
            adapter_name=self.EXCHANGE_NAME,
            supported_transports=(LiveDataTransport.REST,),
            supported_timeframes=self.SUPPORTED_TIMEFRAMES,
            supported_market_categories=self.SUPPORTED_MARKET_CATEGORIES,
            supports_historical=True,
            supports_websocket=False,
            supports_optional_metrics=False,
            public_data_only=True,
            rate_limit_notes="Public REST Kline metadata only; no rate limiter in v0.3.",
        )

    def _build_kline_request_metadata(
        self,
        *,
        symbol: str,
        timeframe: str,
        category: str,
        limit: int | None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "category": category,
            "symbol": symbol.upper(),
            "interval": timeframe,
        }
        if limit is not None:
            params["limit"] = limit
        if start is not None:
            params["start"] = _timestamp_ms(start)
        if end is not None:
            params["end"] = _timestamp_ms(end)

        return {
            "exchange": self.EXCHANGE_NAME,
            "transport": LiveDataTransport.REST.value,
            "endpoint": self.KLINE_ENDPOINT,
            "params": params,
            "public_data_only": True,
        }

    def _validate_request(
        self,
        *,
        symbol: str,
        timeframe: str,
        category: str,
    ) -> LiveDataError | None:
        if not isinstance(symbol, str) or not symbol.strip():
            return _adapter_error(
                error_type=LiveDataErrorType.VALIDATION_FAILED,
                message="Bybit symbol must be a non-empty string.",
                symbol=symbol,
                timeframe=timeframe,
                validation_errors=("empty_symbol",),
            )

        if not isinstance(category, str) or not category.strip():
            return _adapter_error(
                error_type=LiveDataErrorType.VALIDATION_FAILED,
                message="Bybit market category must be a non-empty string.",
                symbol=symbol,
                timeframe=timeframe,
                validation_errors=("empty_category",),
            )

        # Category checks are adapter-local request validation. They are not
        # Live Data payload validation and do not require a new error enum yet.
        if category not in self.SUPPORTED_MARKET_CATEGORIES:
            return _adapter_error(
                error_type=LiveDataErrorType.VALIDATION_FAILED,
                message=f"Unsupported Bybit market category: {category}",
                symbol=symbol,
                timeframe=timeframe,
                validation_errors=("unsupported_category",),
            )

        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            return _adapter_error(
                error_type=LiveDataErrorType.UNSUPPORTED_TIMEFRAME,
                message=f"Unsupported Bybit timeframe: {timeframe}",
                symbol=symbol,
                timeframe=timeframe,
                validation_errors=("unsupported_timeframe",),
            )

        return None

    def _perform_request(self, request_metadata: dict[str, Any]) -> LiveDataResult:
        """Perform the approved public REST Kline request."""

        url = _request_url(request_metadata)
        try:
            with request.urlopen(url, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                raw_body = response.read().decode("utf-8")
        except TimeoutError:
            return LiveDataResult(
                success=False,
                error=_adapter_error(
                    error_type=LiveDataErrorType.TIMEOUT,
                    message="Bybit Kline request timed out.",
                    symbol=_request_symbol(request_metadata),
                    timeframe=_request_timeframe(request_metadata),
                    validation_errors=("timeout",),
                ),
            )
        except error.HTTPError as exc:
            return LiveDataResult(
                success=False,
                error=_error_from_http_status(
                    status=exc.code,
                    message=str(exc),
                    request_metadata=request_metadata,
                ),
            )
        except error.URLError as exc:
            reason = str(getattr(exc, "reason", exc))
            error_type = (
                LiveDataErrorType.TIMEOUT
                if "timed out" in reason.lower()
                else LiveDataErrorType.UNKNOWN_ERROR
            )
            return LiveDataResult(
                success=False,
                error=_adapter_error(
                    error_type=error_type,
                    message=f"Bybit Kline request failed: {reason}",
                    symbol=_request_symbol(request_metadata),
                    timeframe=_request_timeframe(request_metadata),
                    validation_errors=(error_type.value,),
                ),
            )
        except UnicodeDecodeError:
            return LiveDataResult(
                success=False,
                error=_adapter_error(
                    error_type=LiveDataErrorType.MALFORMED_PAYLOAD,
                    message="Bybit Kline response was not valid UTF-8.",
                    symbol=_request_symbol(request_metadata),
                    timeframe=_request_timeframe(request_metadata),
                    validation_errors=("invalid_utf8",),
                ),
            )

        if status == 429 or 500 <= status <= 599:
            return LiveDataResult(
                success=False,
                error=_error_from_http_status(
                    status=status,
                    message=f"Bybit Kline HTTP status {status}.",
                    request_metadata=request_metadata,
                ),
            )

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return LiveDataResult(
                success=False,
                error=_adapter_error(
                    error_type=LiveDataErrorType.MALFORMED_PAYLOAD,
                    message="Bybit Kline response was not valid JSON.",
                    symbol=_request_symbol(request_metadata),
                    timeframe=_request_timeframe(request_metadata),
                    validation_errors=("invalid_json",),
                ),
            )

        payload_error = _validate_bybit_payload(payload, request_metadata)
        if payload_error is not None:
            return LiveDataResult(success=False, error=payload_error)

        return LiveDataResult(
            success=True,
            raw_data={
                "exchange": self.EXCHANGE_NAME,
                "endpoint": self.KLINE_ENDPOINT,
                "request_metadata": request_metadata,
                "payload": payload,
            },
        )


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _request_url(request_metadata: dict[str, Any]) -> str:
    query = parse.urlencode(request_metadata["params"])
    return f"https://api.bybit.com{request_metadata['endpoint']}?{query}"


def _request_symbol(request_metadata: dict[str, Any]) -> str:
    return str(request_metadata["params"].get("symbol", ""))


def _request_timeframe(request_metadata: dict[str, Any]) -> str:
    return str(request_metadata["params"].get("interval", ""))


def _error_from_http_status(
    *,
    status: int,
    message: str,
    request_metadata: dict[str, Any],
) -> LiveDataError:
    if status == 429:
        error_type = LiveDataErrorType.RATE_LIMITED
        validation_error = "rate_limited"
    elif 500 <= status <= 599:
        error_type = LiveDataErrorType.EXCHANGE_UNAVAILABLE
        validation_error = "exchange_unavailable"
    else:
        error_type = LiveDataErrorType.UNKNOWN_ERROR
        validation_error = "http_error"

    return _adapter_error(
        error_type=error_type,
        message=message,
        symbol=_request_symbol(request_metadata),
        timeframe=_request_timeframe(request_metadata),
        validation_errors=(validation_error,),
    )


def _validate_bybit_payload(
    payload: Any,
    request_metadata: dict[str, Any],
) -> LiveDataError | None:
    if not isinstance(payload, dict):
        return _malformed_payload_error(
            "Bybit Kline payload must be a JSON object.",
            "payload_not_object",
            request_metadata,
        )

    if "retCode" not in payload:
        return _malformed_payload_error(
            "Bybit Kline payload missing retCode.",
            "missing_ret_code",
            request_metadata,
        )

    ret_code = payload["retCode"]
    if ret_code != 0:
        return _error_from_bybit_ret_code(
            ret_code=ret_code,
            ret_msg=str(payload.get("retMsg", "Bybit returned non-zero retCode.")),
            request_metadata=request_metadata,
        )

    if "result" not in payload:
        return _malformed_payload_error(
            "Bybit Kline payload missing result.",
            "missing_result",
            request_metadata,
        )

    result = payload["result"]
    if not isinstance(result, dict):
        return _malformed_payload_error(
            "Bybit Kline result must be an object.",
            "malformed_result",
            request_metadata,
        )

    if "list" not in result:
        return _malformed_payload_error(
            "Bybit Kline result missing list.",
            "missing_list",
            request_metadata,
        )

    if not isinstance(result["list"], list):
        return _malformed_payload_error(
            "Bybit Kline list must be an array.",
            "malformed_list",
            request_metadata,
        )

    return None


def _error_from_bybit_ret_code(
    *,
    ret_code: Any,
    ret_msg: str,
    request_metadata: dict[str, Any],
) -> LiveDataError:
    mapping = {
        10000: (LiveDataErrorType.TIMEOUT, "timeout"),
        10006: (LiveDataErrorType.RATE_LIMITED, "rate_limited"),
        10016: (LiveDataErrorType.EXCHANGE_UNAVAILABLE, "exchange_unavailable"),
        10029: (LiveDataErrorType.UNSUPPORTED_SYMBOL, "unsupported_symbol"),
    }
    error_type, validation_error = mapping.get(
        ret_code,
        (LiveDataErrorType.UNKNOWN_ERROR, "non_zero_ret_code"),
    )

    return _adapter_error(
        error_type=error_type,
        message=f"Bybit Kline retCode {ret_code}: {ret_msg}",
        symbol=_request_symbol(request_metadata),
        timeframe=_request_timeframe(request_metadata),
        validation_errors=(validation_error,),
    )


def _malformed_payload_error(
    message: str,
    validation_error: str,
    request_metadata: dict[str, Any],
) -> LiveDataError:
    return _adapter_error(
        error_type=LiveDataErrorType.MALFORMED_PAYLOAD,
        message=message,
        symbol=_request_symbol(request_metadata),
        timeframe=_request_timeframe(request_metadata),
        validation_errors=(validation_error,),
    )


def _adapter_error(
    *,
    error_type: LiveDataErrorType,
    message: str,
    symbol: str,
    timeframe: str,
    validation_errors: tuple[str, ...],
) -> LiveDataError:
    return LiveDataError(
        error_type=error_type,
        message=message,
        exchange=BybitAdapter.EXCHANGE_NAME,
        symbol=symbol,
        timeframe=timeframe,
        receive_timestamp=datetime.now(timezone.utc),
        retryable=False,
        validation_errors=validation_errors,
    )
