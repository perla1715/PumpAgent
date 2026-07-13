"""Internal Structure Engine MVP models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields


@dataclass(frozen=True)
class StructureCandle(SerializableMixin):
    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    volume: float
    candle_index: int

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)


@dataclass(frozen=True)
class EmaSet(SerializableMixin):
    ema_7: float | None
    ema_14: float | None
    ema_21: float | None
    available_periods: tuple[int, ...]
    unavailable_periods: tuple[int, ...]

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)


@dataclass(frozen=True)
class SwingPoint(SerializableMixin):
    kind: str
    timestamp: Any
    price: float
    candle_index: int

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)


@dataclass(frozen=True)
class Impulse(SerializableMixin):
    direction: str
    start: SwingPoint | None
    end: SwingPoint | None
    high: float | None
    low: float | None
    is_valid: bool
    invalid_reason: str | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)


@dataclass(frozen=True)
class FibonacciLevel(SerializableMixin):
    ratio: float
    price: float
    label: str

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)


@dataclass(frozen=True)
class ChartStructure(SerializableMixin):
    event_id: str
    symbol: str
    exchange: str
    timeframe: str
    candle_count: int
    first_price: float
    latest_price: float
    emas: EmaSet
    ema_positions: dict[str, str]
    swing_highs: tuple[SwingPoint, ...]
    swing_lows: tuple[SwingPoint, ...]
    latest_higher_high: SwingPoint | None
    latest_higher_low: SwingPoint | None
    latest_lower_high: SwingPoint | None
    latest_lower_low: SwingPoint | None
    latest_impulse: Impulse
    fibonacci_levels: tuple[FibonacciLevel, ...]
    fibonacci_position: str
    structural_events: tuple[str, ...]
    key_levels: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    source_observation_event_id: str
    schema_version: str = "structure_chart_v1"

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
