"""Compose fixture Live Data into a Runtime-compatible MarketSnapshot.

This layer connects approved Live Data components only:
FixtureLiveDataSource -> Validation -> Quality Translation -> Runtime Bridge.
It does not create RuntimeEvent objects or invoke Runtime reasoning modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pumpagent.live_data.bridge import build_market_snapshot_from_live_data
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
from pumpagent.live_data.sources import FixtureLiveDataSource
from pumpagent.live_data.validation import validate_normalized_market_data_input
from pumpagent.runtime.domain import MarketSnapshot


@dataclass(frozen=True)
class FixtureMarketSnapshotFlowResult(SerializableMixin):
    """Result for the composed fixture-to-MarketSnapshot flow."""

    success: bool
    market_snapshot: MarketSnapshot | None = None
    error: LiveDataError | None = None

    def __post_init__(self) -> None:
        if self.success and self.market_snapshot is None:
            raise ValueError("Successful fixture flow result requires market_snapshot.")
        if self.success and self.error is not None:
            raise ValueError("Successful fixture flow result cannot include error.")
        if not self.success and self.error is None:
            raise ValueError("Failed fixture flow result requires error.")
        if not self.success and self.market_snapshot is not None:
            raise ValueError("Failed fixture flow result cannot include market_snapshot.")

        freeze_dataclass_fields(self)


def load_market_snapshot_from_fixture_flow(
    fixture_path: str | Path,
    *,
    mode: LiveDataMode = LiveDataMode.LIVE,
    allow_unknown_non_live: bool = False,
    source: FixtureLiveDataSource | None = None,
) -> FixtureMarketSnapshotFlowResult:
    """Load a fixture and prepare a Runtime MarketSnapshot."""

    source_result = (source or FixtureLiveDataSource()).load(fixture_path)
    if not source_result.success:
        return FixtureMarketSnapshotFlowResult(error=source_result.error, success=False)

    data = source_result.data
    validation = validate_normalized_market_data_input(data)
    if not validation.is_valid:
        return FixtureMarketSnapshotFlowResult(
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
        return FixtureMarketSnapshotFlowResult(
            success=False,
            error=_error_from_quality_block(data, quality.block_reason),
        )

    # The composition layer runs validation and quality translation to preserve
    # the approved fixture flow order. The Runtime Bridge repeats those checks
    # as the final safety gate before constructing a MarketSnapshot.
    bridge_result = build_market_snapshot_from_live_data(
        data,
        mode=mode,
        allow_unknown_non_live=allow_unknown_non_live,
    )
    if not bridge_result.success:
        return FixtureMarketSnapshotFlowResult(
            success=False,
            error=bridge_result.error,
        )

    return FixtureMarketSnapshotFlowResult(
        success=True,
        market_snapshot=bridge_result.market_snapshot,
    )


def _error_from_validation(
    data: NormalizedMarketDataInput,
    validation_errors: tuple[str, ...],
) -> LiveDataError:
    return LiveDataError(
        error_type=LiveDataErrorType.VALIDATION_FAILED,
        message="Fixture Live Data flow validation failed.",
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
        message=block_reason or "Fixture Live Data quality blocked MarketSnapshot.",
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
