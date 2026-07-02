"""Validation for NormalizedMarketDataInput contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

from pumpagent.live_data.domain import (
    LiveDataQualityStatus,
    NormalizedMarketDataInput,
)
from pumpagent.live_data.domain.base import (
    SerializableMixin,
    freeze_dataclass_fields,
)


REQUIRED_TOP_LEVEL_FIELDS = (
    "source_event_id",
    "symbol",
    "exchange",
    "timeframe",
    "source_timestamp",
    "receive_timestamp",
    "price",
    "ohlcv",
    "volume",
    "data_source",
    "quality_status",
    "source_metadata",
    "schema_version",
)

CANDLE_REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
CANDLE_NUMERIC_FIELDS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class LiveDataValidationResult(SerializableMixin):
    is_valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)

    @property
    def required_fields_valid(self) -> bool:
        return self.is_valid


def validate_normalized_market_data_input(
    data: NormalizedMarketDataInput,
) -> LiveDataValidationResult:
    """Validate normalized Live Data before quality translation or bridge use."""

    errors: list[str] = []
    warnings: list[str] = list(data.validation_warnings)
    missing_fields: list[str] = list(data.missing_fields)

    _validate_required_top_level_fields(data, errors, missing_fields)
    _validate_non_empty_identity(data, errors, missing_fields)
    _validate_non_empty_source_strings(data, errors, missing_fields)
    _validate_numeric(data.price, "price", errors)
    _validate_numeric(data.volume, "volume", errors)
    _validate_timestamp(data.source_timestamp, "source_timestamp", errors)
    _validate_timestamp(data.receive_timestamp, "receive_timestamp", errors)
    _validate_ohlcv(data.ohlcv, errors, missing_fields)

    if data.quality_status == LiveDataQualityStatus.PARTIAL and errors:
        warnings.append("partial_quality_has_invalid_required_fields")

    return LiveDataValidationResult(
        is_valid=len(errors) == 0,
        errors=tuple(errors),
        warnings=tuple(warnings),
        missing_fields=_dedupe(missing_fields),
    )


def _validate_required_top_level_fields(
    data: NormalizedMarketDataInput,
    errors: list[str],
    missing_fields: list[str],
) -> None:
    for field_name in REQUIRED_TOP_LEVEL_FIELDS:
        value = getattr(data, field_name, None)
        if value is None:
            missing_fields.append(field_name)
            errors.append(f"{field_name} is required.")


def _validate_non_empty_identity(
    data: NormalizedMarketDataInput,
    errors: list[str],
    missing_fields: list[str],
) -> None:
    for field_name in ("symbol", "exchange", "timeframe"):
        value = getattr(data, field_name)
        if not isinstance(value, str) or not value.strip():
            missing_fields.append(field_name)
            errors.append(f"{field_name} must be a non-empty string.")


def _validate_non_empty_source_strings(
    data: NormalizedMarketDataInput,
    errors: list[str],
    missing_fields: list[str],
) -> None:
    for field_name in ("source_event_id", "data_source", "schema_version"):
        value = getattr(data, field_name)
        if not isinstance(value, str) or not value.strip():
            missing_fields.append(field_name)
            errors.append(f"{field_name} must be a non-empty string.")


def _validate_numeric(value: Any, field_name: str, errors: list[str]) -> None:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        errors.append(f"{field_name} must be numeric and finite.")
        return

    if not isfinite(numeric):
        errors.append(f"{field_name} must be numeric and finite.")


def _validate_timestamp(value: Any, field_name: str, errors: list[str]) -> None:
    if isinstance(value, datetime):
        return

    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return
        except ValueError:
            pass

    errors.append(f"{field_name} must be a parseable timestamp.")


def _validate_ohlcv(
    ohlcv: Any,
    errors: list[str],
    missing_fields: list[str],
) -> None:
    if not ohlcv:
        missing_fields.append("ohlcv")
        errors.append("ohlcv must contain at least one candle.")
        return

    for index, candle in enumerate(ohlcv):
        if not isinstance(candle, Mapping):
            errors.append(f"ohlcv[{index}] candle must be a mapping/object.")
            continue

        for field_name in CANDLE_REQUIRED_FIELDS:
            if field_name not in candle:
                missing_field = f"ohlcv[{index}].{field_name}"
                missing_fields.append(missing_field)
                errors.append(f"{missing_field} is required.")

        if "timestamp" in candle:
            _validate_timestamp(candle["timestamp"], f"ohlcv[{index}].timestamp", errors)

        for field_name in CANDLE_NUMERIC_FIELDS:
            if field_name in candle:
                _validate_numeric(
                    candle[field_name],
                    f"ohlcv[{index}].{field_name}",
                    errors,
                )


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
