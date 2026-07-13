"""Structure-only indicator calculations."""

from __future__ import annotations

from pumpagent.runtime.modules.structure.models import EmaSet, StructureCandle


EMA_PERIODS = (7, 14, 21)


def calculate_emas(candles: tuple[StructureCandle, ...]) -> EmaSet:
    """Calculate EMA values only after full-period warmup is available."""

    series_by_period = {
        period: _ema_series([candle.close for candle in candles], period)
        for period in EMA_PERIODS
    }
    latest_by_period = {
        period: _latest_available(series)
        for period, series in series_by_period.items()
    }
    available_periods = tuple(
        period for period in EMA_PERIODS if latest_by_period[period] is not None
    )
    unavailable_periods = tuple(
        period for period in EMA_PERIODS if latest_by_period[period] is None
    )

    return EmaSet(
        ema_7=latest_by_period[7],
        ema_14=latest_by_period[14],
        ema_21=latest_by_period[21],
        available_periods=available_periods,
        unavailable_periods=unavailable_periods,
    )


def _ema_series(values: list[float], period: int) -> tuple[float | None, ...]:
    if len(values) < period:
        return tuple(None for _ in values)

    series: list[float | None] = [None] * (period - 1)
    ema = sum(values[:period]) / period
    series.append(ema)
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
        series.append(ema)
    return tuple(series)


def _latest_available(series: tuple[float | None, ...]) -> float | None:
    for value in reversed(series):
        if value is not None:
            return value
    return None
