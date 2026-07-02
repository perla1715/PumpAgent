"""Convert validated Live Data inputs into Runtime MarketSnapshot objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pumpagent.live_data.domain import (
    LiveDataError,
    LiveDataErrorType,
    LiveDataMode,
    NormalizedMarketDataInput,
)
from pumpagent.live_data.domain.base import (
    SerializableMixin,
    freeze_dataclass_fields,
)
from pumpagent.live_data.quality import translate_quality_status
from pumpagent.live_data.validation import validate_normalized_market_data_input
from pumpagent.runtime.domain import MarketSnapshot


@dataclass(frozen=True)
class RuntimeMarketDataBridgeResult(SerializableMixin):
    success: bool
    market_snapshot: MarketSnapshot | None = None
    error: LiveDataError | None = None

    def __post_init__(self) -> None:
        if self.success and self.market_snapshot is None:
            raise ValueError("Successful bridge result requires market_snapshot.")
        if self.success and self.error is not None:
            raise ValueError("Successful bridge result cannot include error.")
        if not self.success and self.error is None:
            raise ValueError("Failed bridge result requires error.")
        if not self.success and self.market_snapshot is not None:
            raise ValueError("Failed bridge result cannot include market_snapshot.")

        freeze_dataclass_fields(self)


def build_market_snapshot_from_live_data(
    data: NormalizedMarketDataInput,
    *,
    mode: LiveDataMode,
    allow_unknown_non_live: bool = False,
) -> RuntimeMarketDataBridgeResult:
    """Validate, translate quality, and build a Runtime MarketSnapshot."""

    validation = validate_normalized_market_data_input(data)
    if not validation.is_valid:
        return RuntimeMarketDataBridgeResult(
            success=False,
            error=_error_from_validation(data, validation.errors),
        )

    quality = translate_quality_status(
        data,
        mode=mode,
        required_fields_valid=validation.required_fields_valid,
        allow_unknown_non_live=allow_unknown_non_live,
    )
    if not quality.allowed:
        return RuntimeMarketDataBridgeResult(
            success=False,
            error=_error_from_quality_block(data, quality.block_reason),
        )

    snapshot = MarketSnapshot(
        event_id=data.source_event_id,
        timestamp=data.source_timestamp,
        symbol=data.symbol,
        exchange=data.exchange,
        timeframe=data.timeframe,
        price=float(data.price),
        ohlcv=tuple(dict(candle) for candle in data.ohlcv),
        volume=float(data.volume),
        data_source=data.data_source,
        data_quality_status=quality.runtime_quality_status,
        schema_version=data.schema_version,
        optional_market_metrics=_optional_market_metrics(data, quality.preserved_metadata),
        raw_payload_reference=data.raw_payload_reference,
        latency_ms=_latency_ms(data.source_metadata.latency_ms),
        missing_fields=validation.missing_fields,
    )
    return RuntimeMarketDataBridgeResult(success=True, market_snapshot=snapshot)


def _optional_market_metrics(
    data: NormalizedMarketDataInput,
    preserved_metadata: dict[str, Any],
) -> dict[str, Any]:
    metrics = dict(data.optional_market_metrics)
    metrics.update(preserved_metadata)
    metrics["source_metadata"] = data.source_metadata.to_dict()
    return metrics


def _latency_ms(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(value))


def _error_from_validation(
    data: NormalizedMarketDataInput,
    validation_errors: tuple[str, ...],
) -> LiveDataError:
    return LiveDataError(
        error_type=LiveDataErrorType.VALIDATION_FAILED,
        message="NormalizedMarketDataInput failed validation.",
        exchange=_safe_text(data, "exchange"),
        symbol=_safe_text(data, "symbol"),
        timeframe=_safe_text(data, "timeframe"),
        receive_timestamp=_receive_timestamp(data),
        retryable=False,
        source_timestamp=_source_timestamp(data),
        validation_errors=validation_errors,
        raw_payload_reference=data.raw_payload_reference,
        correlation_id=_correlation_id(data),
    )


def _error_from_quality_block(
    data: NormalizedMarketDataInput,
    block_reason: str | None,
) -> LiveDataError:
    return LiveDataError(
        error_type=LiveDataErrorType.QUALITY_BLOCKED,
        message=block_reason or "Live Data quality blocked Runtime MarketSnapshot.",
        exchange=data.exchange,
        symbol=data.symbol,
        timeframe=data.timeframe,
        receive_timestamp=_receive_timestamp(data),
        retryable=False,
        source_timestamp=_source_timestamp(data),
        validation_errors=(block_reason,) if block_reason else (),
        raw_payload_reference=data.raw_payload_reference,
        correlation_id=_correlation_id(data),
    )


def _receive_timestamp(data: NormalizedMarketDataInput) -> datetime:
    if isinstance(data.receive_timestamp, datetime):
        return data.receive_timestamp
    return datetime.now(timezone.utc)


def _source_timestamp(data: NormalizedMarketDataInput) -> datetime | None:
    if isinstance(data.source_timestamp, datetime):
        return data.source_timestamp
    return None


def _correlation_id(data: NormalizedMarketDataInput) -> str | None:
    metadata = getattr(data, "source_metadata", None)
    return getattr(metadata, "correlation_id", None)


def _safe_text(data: NormalizedMarketDataInput, field_name: str) -> str:
    value = getattr(data, field_name, "")
    if isinstance(value, str):
        return value
    return ""
