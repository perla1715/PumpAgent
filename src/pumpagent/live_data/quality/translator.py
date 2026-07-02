"""Translate Live Data quality into Runtime data quality."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pumpagent.live_data.domain import (
    LiveDataMode,
    LiveDataQualityStatus,
    NormalizedMarketDataInput,
)
from pumpagent.live_data.domain.base import (
    SerializableMixin,
    freeze_dataclass_fields,
)
from pumpagent.runtime.domain.enums import DataQualityStatus


@dataclass(frozen=True)
class QualityTranslationResult(SerializableMixin):
    runtime_quality_status: DataQualityStatus
    allowed: bool
    block_reason: str | None = None
    preserved_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)


def translate_quality_status(
    data: NormalizedMarketDataInput,
    *,
    mode: LiveDataMode,
    required_fields_valid: bool = True,
    allow_unknown_non_live: bool = False,
) -> QualityTranslationResult:
    """Apply the approved Live Data to Runtime quality mapping."""

    preserved_metadata = _preserved_metadata(data)

    if data.quality_status == LiveDataQualityStatus.GOOD:
        return QualityTranslationResult(
            runtime_quality_status=DataQualityStatus.VALID,
            allowed=True,
            preserved_metadata=preserved_metadata,
        )

    if data.quality_status == LiveDataQualityStatus.DELAYED:
        return QualityTranslationResult(
            runtime_quality_status=DataQualityStatus.DELAYED,
            allowed=True,
            preserved_metadata=preserved_metadata,
        )

    if data.quality_status == LiveDataQualityStatus.PARTIAL:
        return QualityTranslationResult(
            runtime_quality_status=DataQualityStatus.MISSING,
            allowed=required_fields_valid,
            block_reason=None
            if required_fields_valid
            else "partial_data_missing_required_fields",
            preserved_metadata=preserved_metadata,
        )

    if data.quality_status == LiveDataQualityStatus.CORRUPTED:
        return QualityTranslationResult(
            runtime_quality_status=DataQualityStatus.CORRUPTED,
            allowed=False,
            block_reason="corrupted_data_blocked",
            preserved_metadata=preserved_metadata,
        )

    if data.quality_status == LiveDataQualityStatus.UNKNOWN:
        allowed = mode in (
            LiveDataMode.REPLAY,
            LiveDataMode.SIMULATION,
            LiveDataMode.TESTING,
        ) and allow_unknown_non_live
        return QualityTranslationResult(
            runtime_quality_status=DataQualityStatus.MISSING,
            allowed=allowed,
            block_reason=None if allowed else "unknown_quality_blocked",
            preserved_metadata=preserved_metadata,
        )

    raise ValueError(f"Unsupported Live Data quality status: {data.quality_status}")


def _preserved_metadata(data: NormalizedMarketDataInput) -> dict[str, Any]:
    metadata = {
        "quality_reasons": data.quality_reasons,
        "missing_fields": data.missing_fields,
        "validation_warnings": data.validation_warnings,
        "source_timestamp": data.source_timestamp,
        "receive_timestamp": data.receive_timestamp,
        "latency_ms": data.source_metadata.latency_ms,
        "adapter_name": data.source_metadata.adapter_name,
        "adapter_version": data.source_metadata.adapter_version,
        "transport": data.source_metadata.transport.value,
        "correlation_id": data.source_metadata.correlation_id,
    }

    for key in ("normalizer_version", "validator_version"):
        if key in data.optional_market_metrics:
            metadata[key] = data.optional_market_metrics[key]

    return metadata
