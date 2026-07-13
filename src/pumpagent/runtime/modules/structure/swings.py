"""Swing detection for the Structure Engine MVP."""

from __future__ import annotations

from pumpagent.runtime.modules.structure.models import (
    Impulse,
    StructureCandle,
    SwingPoint,
)


PIVOT_LEFT = 2
PIVOT_RIGHT = 2


def detect_swings(
    candles: tuple[StructureCandle, ...],
) -> tuple[tuple[SwingPoint, ...], tuple[SwingPoint, ...]]:
    """Detect swing highs and lows with a deterministic 2-left / 2-right rule."""

    swing_highs: list[SwingPoint] = []
    swing_lows: list[SwingPoint] = []
    for index in range(PIVOT_LEFT, len(candles) - PIVOT_RIGHT):
        candle = candles[index]
        left = candles[index - PIVOT_LEFT : index]
        right = candles[index + 1 : index + PIVOT_RIGHT + 1]
        neighbors = left + right

        if all(candle.high > neighbor.high for neighbor in neighbors):
            swing_highs.append(
                SwingPoint(
                    kind="high",
                    timestamp=candle.timestamp,
                    price=candle.high,
                    candle_index=index,
                )
            )

        if all(candle.low < neighbor.low for neighbor in neighbors):
            swing_lows.append(
                SwingPoint(
                    kind="low",
                    timestamp=candle.timestamp,
                    price=candle.low,
                    candle_index=index,
                )
            )

    return tuple(swing_highs), tuple(swing_lows)


def latest_sequence_points(
    points: tuple[SwingPoint, ...],
) -> tuple[SwingPoint | None, SwingPoint | None]:
    """Return latest higher and lower points within one swing kind."""

    latest_higher = None
    latest_lower = None
    for previous, current in zip(points, points[1:]):
        if current.price > previous.price:
            latest_higher = current
        elif current.price < previous.price:
            latest_lower = current
    return latest_higher, latest_lower


def latest_valid_impulse(
    swing_highs: tuple[SwingPoint, ...],
    swing_lows: tuple[SwingPoint, ...],
) -> Impulse:
    """Use the latest opposite-kind swing pair as the MVP impulse."""

    points = sorted(swing_highs + swing_lows, key=lambda point: point.candle_index)
    for start, end in reversed(list(zip(points, points[1:]))):
        if start.kind == end.kind:
            continue
        high = max(start.price, end.price)
        low = min(start.price, end.price)
        direction = "up" if start.kind == "low" and end.kind == "high" else "down"
        return Impulse(
            direction=direction,
            start=start,
            end=end,
            high=high,
            low=low,
            is_valid=True,
        )

    return Impulse(
        direction="unknown",
        start=None,
        end=None,
        high=None,
        low=None,
        is_valid=False,
        invalid_reason="no_valid_swing_impulse",
    )
