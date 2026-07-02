"""Local fixture Live Data source.

This source only loads local fixture files into the approved Live Data
contract. It does not validate, translate quality, call Runtime, or communicate
with exchanges.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from pumpagent.live_data.domain import (
    LiveDataError,
    LiveDataErrorType,
    LiveDataQualityStatus,
    LiveDataResult,
    LiveDataTransport,
    NormalizedMarketDataInput,
    SourceMetadata,
)


class FixtureLiveDataSource:
    """Load one local Live Data fixture into NormalizedMarketDataInput."""

    def load(self, fixture_path: str | Path) -> LiveDataResult:
        path = Path(fixture_path)

        try:
            payload = _read_json(path)
            data = _build_normalized_input(payload, path)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return LiveDataResult(
                success=False,
                error=_fixture_error(path, str(exc)),
            )

        return LiveDataResult(success=True, data=data)


def _read_json(path: Path) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as fixture_file:
        payload = json.load(fixture_file)

    if not isinstance(payload, Mapping):
        raise ValueError("Fixture root must be a JSON object.")

    return payload


def _build_normalized_input(
    payload: Mapping[str, Any],
    path: Path,
) -> NormalizedMarketDataInput:
    source_timestamp = _parse_timestamp(_required(payload, "source_timestamp"))
    receive_timestamp = _parse_timestamp(_required(payload, "receive_timestamp"))
    source_metadata_payload = _required(payload, "source_metadata")
    if not isinstance(source_metadata_payload, Mapping):
        raise ValueError("Fixture field source_metadata must be an object.")
    source_metadata = _build_source_metadata(source_metadata_payload)

    return NormalizedMarketDataInput(
        source_event_id=str(_required(payload, "source_event_id")),
        symbol=str(_required(payload, "symbol")),
        exchange=str(_required(payload, "exchange")),
        timeframe=str(_required(payload, "timeframe")),
        source_timestamp=source_timestamp,
        receive_timestamp=receive_timestamp,
        price=_required(payload, "price"),
        ohlcv=tuple(dict(candle) for candle in _required(payload, "ohlcv")),
        volume=_required(payload, "volume"),
        data_source=str(_required(payload, "data_source")),
        quality_status=LiveDataQualityStatus(str(_required(payload, "quality_status"))),
        source_metadata=source_metadata,
        schema_version=str(payload.get("schema_version", "1.0")),
        optional_market_metrics=dict(payload.get("optional_market_metrics", {})),
        quality_reasons=tuple(str(item) for item in payload.get("quality_reasons", ())),
        missing_fields=tuple(str(item) for item in payload.get("missing_fields", ())),
        validation_warnings=tuple(
            str(item) for item in payload.get("validation_warnings", ())
        ),
        raw_payload_reference=str(path),
    )


def _build_source_metadata(payload: Mapping[str, Any]) -> SourceMetadata:
    return SourceMetadata(
        exchange=str(_required(payload, "exchange", context="source_metadata")),
        adapter_name=str(_required(payload, "adapter_name", context="source_metadata")),
        adapter_version=str(
            _required(payload, "adapter_version", context="source_metadata")
        ),
        source_timestamp=_parse_timestamp(
            _required(payload, "source_timestamp", context="source_metadata")
        ),
        receive_timestamp=_parse_timestamp(
            _required(payload, "receive_timestamp", context="source_metadata")
        ),
        transport=LiveDataTransport(
            str(_required(payload, "transport", context="source_metadata"))
        ),
        source_symbol=str(_required(payload, "source_symbol", context="source_metadata")),
        normalized_symbol=str(
            _required(payload, "normalized_symbol", context="source_metadata")
        ),
        source_timeframe=str(
            _required(payload, "source_timeframe", context="source_metadata")
        ),
        normalized_timeframe=str(
            _required(payload, "normalized_timeframe", context="source_metadata")
        ),
        schema_version=str(payload.get("schema_version", "1.0")),
        latency_ms=payload.get("latency_ms"),
        correlation_id=payload.get("correlation_id"),
        request_id=payload.get("request_id"),
        sequence_id=payload.get("sequence_id"),
    )


def _required(payload: Mapping[str, Any], field: str, *, context: str = "fixture") -> Any:
    if field not in payload:
        if context == "fixture":
            raise ValueError(f"Missing fixture field: {field}")
        raise ValueError(f"Missing {context} field: {field}")
    return payload[field]


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value

    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string.")

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fixture_error(path: Path, message: str) -> LiveDataError:
    return LiveDataError(
        error_type=LiveDataErrorType.MALFORMED_PAYLOAD,
        message=f"Could not load Live Data fixture: {message}",
        exchange="fixture",
        symbol="unknown",
        timeframe="unknown",
        receive_timestamp=datetime.now(timezone.utc),
        retryable=False,
        raw_payload_reference=str(path),
    )
