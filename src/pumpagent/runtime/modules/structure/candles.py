"""Structure candle conversion helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pumpagent.runtime.modules.structure.models import StructureCandle


REQUIRED_OHLCV_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def to_structure_candles(
    raw_candles: tuple[Mapping[str, Any], ...],
) -> tuple[StructureCandle, ...]:
    """Convert normalized OHLCV mappings into internal StructureCandle objects."""

    candles: list[StructureCandle] = []
    for index, candle in enumerate(raw_candles):
        candles.append(
            StructureCandle(
                timestamp=candle["timestamp"],
                open=_as_float(candle["open"], "open", index),
                high=_as_float(candle["high"], "high", index),
                low=_as_float(candle["low"], "low", index),
                close=_as_float(candle["close"], "close", index),
                volume=_as_float(candle["volume"], "volume", index),
                candle_index=index,
            )
        )
    return tuple(candles)


def _as_float(value: Any, field_name: str, candle_index: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        from pumpagent.runtime.modules.structure.engine import StructureError

        raise StructureError(
            "ObservationPackage.normalized_ohlcv candle "
            f"{candle_index} field {field_name} must be numeric."
        ) from exc
