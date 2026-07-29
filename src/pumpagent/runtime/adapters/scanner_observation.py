"""Pure Scanner V2 to ObservationRequest boundary adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import DataQualityStatus
from pumpagent.runtime.domain.observation_policy import ObservationRequest


SCANNER_ADAPTER_SCHEMA_VERSION = "scanner_observation_request_adapter_v1"
SUPPORTED_TIMEFRAME = "5m"
COINALYZE_BYBIT_SUFFIX = ".6"


class ScannerAdapterStatus(str, Enum):
    SUCCESS = "success"
    NOT_ATTENTION_ELIGIBLE = "not_attention_eligible"
    SKIPPED = "skipped"
    FAILED = "failed"
    MALFORMED = "malformed"
    INCOMPLETE_IDENTITY = "incomplete_identity"
    INVALID_TIMESTAMP = "invalid_timestamp"
    UNALIGNED_EVIDENCE = "unaligned_evidence"
    OPEN_CANDLE = "open_candle"
    UNSUPPORTED_TIMEFRAME = "unsupported_timeframe"


class ScannerTriggerReason(str, Enum):
    VOLUME_SPIKE = "VOLUME_SPIKE"
    OI_GROWTH = "OI_GROWTH"
    PRICE_ACTIVITY = "PRICE_ACTIVITY"


@dataclass(frozen=True)
class ScannerAttentionDecision:
    """Explicit Scanner-policy output transported by the adapter."""

    eligible: bool
    approved_reasons: tuple[ScannerTriggerReason | str, ...] = ()

    def __post_init__(self) -> None:
        normalized: list[str] = []
        for reason in self.approved_reasons:
            try:
                value = ScannerTriggerReason(reason).value
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Unsupported Scanner trigger reason: {reason!r}.") from exc
            if value not in normalized:
                normalized.append(value)
        object.__setattr__(self, "approved_reasons", tuple(normalized))
        if self.eligible and not normalized:
            raise ValueError("An eligible attention decision requires approved reasons.")
        if not self.eligible and normalized:
            raise ValueError("An ineligible attention decision cannot approve reasons.")


@dataclass(frozen=True)
class ScannerObservationAdapterResult(SerializableMixin):
    status: ScannerAdapterStatus
    request: ObservationRequest | None
    adapter_reason: str
    source_scanner_status: str | None
    schema_version: str = SCANNER_ADAPTER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.status, ScannerAdapterStatus):
            raise ValueError("status must be a ScannerAdapterStatus.")
        if not self.adapter_reason.strip():
            raise ValueError("adapter_reason must be non-empty.")
        if (self.status is ScannerAdapterStatus.SUCCESS) != (self.request is not None):
            raise ValueError("Only a successful adapter result may contain a request.")

    @property
    def success(self) -> bool:
        return self.status is ScannerAdapterStatus.SUCCESS


def build_observation_request_from_scanner_result(
    scanner_result: object,
    attention_decision: ScannerAttentionDecision,
    *,
    exchange: str | None = None,
    provider: str | None = None,
    timeframe: str | None = None,
    request_timestamp: datetime | None = None,
) -> ScannerObservationAdapterResult:
    """Convert one eligible Scanner V2 result without invoking lifecycle policy."""

    status_value = _field(scanner_result, "status")
    source_status = status_value.upper() if isinstance(status_value, str) else None
    if source_status == "SKIPPED":
        return _failure(ScannerAdapterStatus.SKIPPED, "Scanner result was SKIPPED.", source_status)
    if source_status in {"FAILED", "ERROR"}:
        return _failure(ScannerAdapterStatus.FAILED, "Scanner result was FAILED.", source_status)
    if source_status != "VALID":
        return _failure(ScannerAdapterStatus.MALFORMED, "Scanner status must be VALID, SKIPPED, or FAILED.", source_status)

    source_timeframe = timeframe or _field(scanner_result, "timeframe")
    if not isinstance(source_timeframe, str) or not source_timeframe.strip():
        return _failure(ScannerAdapterStatus.INCOMPLETE_IDENTITY, "Scanner timeframe is missing.", source_status)
    if source_timeframe.strip() != SUPPORTED_TIMEFRAME:
        return _failure(ScannerAdapterStatus.UNSUPPORTED_TIMEFRAME, "Only 5m Scanner evidence is supported.", source_status)

    source_symbol = _field(scanner_result, "symbol")
    resolved_exchange = exchange or _field(scanner_result, "exchange")
    if not isinstance(resolved_exchange, str) or not resolved_exchange.strip():
        return _failure(ScannerAdapterStatus.INCOMPLETE_IDENTITY, "Exchange must be resolved explicitly.", source_status)
    canonical_symbol = _canonical_symbol(source_symbol)
    if canonical_symbol is None:
        return _failure(ScannerAdapterStatus.INCOMPLETE_IDENTITY, "Scanner symbol is missing or unsupported.", source_status)

    quality = _field(scanner_result, "data_quality")
    evidence = _field(scanner_result, "calculation_evidence")
    closed = _nested(quality, "closed_candles_only")
    aligned = _nested(quality, "ohlcv_oi_aligned")
    if closed is None and isinstance(evidence, Mapping):
        closed = True  # MarketResult only exists after Scanner closed-record filtering.
    if aligned is None:
        aligned = _nested(evidence, "aligned")
    if closed is not True:
        return _failure(ScannerAdapterStatus.OPEN_CANDLE, "Scanner evidence is not confirmed closed.", source_status)
    if aligned is not True:
        return _failure(ScannerAdapterStatus.UNALIGNED_EVIDENCE, "OHLCV and OI evidence is not aligned.", source_status)

    bucket_value = _field(scanner_result, "timestamp_bucket")
    if bucket_value is None:
        bucket_value = _nested(evidence, "latest_ohlcv_bucket")
    bucket_timestamp = _timestamp(bucket_value)
    if bucket_timestamp is None or int(bucket_timestamp.timestamp()) % 300 != 0:
        return _failure(ScannerAdapterStatus.INVALID_TIMESTAMP, "Scanner bucket must be an aware, normalized 5m timestamp.", source_status)
    created_at = bucket_timestamp if request_timestamp is None else _timestamp(request_timestamp)
    if created_at is None:
        return _failure(ScannerAdapterStatus.INVALID_TIMESTAMP, "Request timestamp must be timezone-aware.", source_status)

    metrics = _field(scanner_result, "metrics")
    price = _metric(metrics, "price_5m_pct", _field(scanner_result, "price_change_pct"))
    volume = _metric(metrics, "volume_ratio_5m", _field(scanner_result, "volume_ratio"))
    oi = _metric(metrics, "oi_change_5m_pct", _field(scanner_result, "oi_change_pct"))
    if not all(_valid_number(value) for value in (price, volume, oi)) or volume < 0:
        return _failure(ScannerAdapterStatus.MALFORMED, "Required Scanner 5m metrics are invalid.", source_status)

    if not attention_decision.eligible:
        return _failure(ScannerAdapterStatus.NOT_ATTENTION_ELIGIBLE, "Valid evidence was not attention-eligible.", source_status)

    source_provider = provider or _field(scanner_result, "provider") or "coinalyze"
    trigger_metrics = {
        "price_change_5m_pct": price,
        "volume_ratio_5m": volume,
        "oi_change_5m_pct": oi,
        "timestamp_bucket_5m": int(bucket_timestamp.timestamp()),
        "closed_candles_only": True,
        "ohlcv_oi_aligned": True,
        "source_provider": str(source_provider),
        "source_instrument": str(source_symbol),
        "source_scanner_status": source_status,
        "units": {
            "price_change_5m_pct": "percent",
            "volume_ratio_5m": "ratio_to_previous_10_closed_5m_candles",
            "oi_change_5m_pct": "percent",
            "timestamp_bucket_5m": "unix_seconds_utc",
        },
    }
    for name in ("raw_ohlcv_records", "closed_ohlcv_records", "required_closed_ohlcv_records"):
        value = _nested(quality, name)
        if value is None:
            value = _field(scanner_result, name)
        if value is not None:
            trigger_metrics[name] = value

    request = ObservationRequest(
        exchange=resolved_exchange.strip().lower(),
        symbol=canonical_symbol,
        timeframe=SUPPORTED_TIMEFRAME,
        request_timestamp=created_at,
        trigger_timestamp=bucket_timestamp,
        triggering_closed_candle_timestamp=bucket_timestamp,
        trigger_reasons=attention_decision.approved_reasons,
        trigger_metrics=trigger_metrics,
        data_quality_status=DataQualityStatus.VALID,
        eligible=True,
    )
    return ScannerObservationAdapterResult(
        status=ScannerAdapterStatus.SUCCESS,
        request=request,
        adapter_reason="Eligible Scanner V2 evidence converted to ObservationRequest.",
        source_scanner_status=source_status,
    )


def _field(source: object, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _nested(source: object, name: str) -> Any:
    return _field(source, name) if source is not None else None


def _metric(metrics: object, name: str, fallback: Any) -> Any:
    value = _nested(metrics, name)
    return fallback if value is None else value


def _canonical_symbol(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    symbol = value.strip().upper()
    if symbol.endswith(COINALYZE_BYBIT_SUFFIX):
        symbol = symbol[: -len(COINALYZE_BYBIT_SUFFIX)]
    return symbol if symbol and "." not in symbol else None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc)
    return None


def _valid_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _failure(status: ScannerAdapterStatus, reason: str, source_status: str | None) -> ScannerObservationAdapterResult:
    return ScannerObservationAdapterResult(status, None, reason, source_status)
