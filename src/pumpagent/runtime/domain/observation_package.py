"""ObservationPackage domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import DataQualityStatus


@dataclass(frozen=True)
class ObservationPackage(SerializableMixin):
    event_id: str
    observation_timestamp: datetime
    normalized_price: float
    normalized_ohlcv: tuple[dict[str, Any], ...]
    normalized_volume: float
    available_metrics: tuple[str, ...]
    missing_metrics: tuple[str, ...]
    data_quality_status: DataQualityStatus
    schema_version: str = "1.0"
    validation_warnings: tuple[str, ...] = ()
    observation_notes: str | None = None
    normalized_metrics: dict[str, Any] = field(default_factory=dict)
    previous_snapshot_reference: str | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
