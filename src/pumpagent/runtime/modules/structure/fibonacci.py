"""Fibonacci level helpers for structural evidence."""

from __future__ import annotations

from pumpagent.runtime.modules.structure.models import FibonacciLevel, Impulse


FIBONACCI_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)


def calculate_fibonacci_levels(impulse: Impulse) -> tuple[FibonacciLevel, ...]:
    """Calculate standard retracement levels from a valid impulse."""

    if not impulse.is_valid or impulse.high is None or impulse.low is None:
        return ()

    price_range = impulse.high - impulse.low
    levels: list[FibonacciLevel] = []
    for ratio in FIBONACCI_RATIOS:
        if impulse.direction == "up":
            price = impulse.high - price_range * ratio
        else:
            price = impulse.low + price_range * ratio
        levels.append(
            FibonacciLevel(
                ratio=ratio,
                price=price,
                label=f"{ratio:.3f}".rstrip("0").rstrip("."),
            )
        )
    return tuple(levels)


def describe_fibonacci_position(
    latest_price: float,
    levels: tuple[FibonacciLevel, ...],
) -> str:
    """Describe latest price relative to computed Fibonacci levels."""

    if not levels:
        return "unavailable"

    ordered_prices = sorted(level.price for level in levels)
    if latest_price < ordered_prices[0]:
        return "below_range"
    if latest_price > ordered_prices[-1]:
        return "above_range"

    for level in levels:
        if latest_price == level.price:
            return f"at_{level.label}"

    lower = ordered_prices[0]
    upper = ordered_prices[-1]
    for left, right in zip(ordered_prices, ordered_prices[1:]):
        if left < latest_price < right:
            lower = left
            upper = right
            break
    return f"between_{lower:g}_and_{upper:g}"
