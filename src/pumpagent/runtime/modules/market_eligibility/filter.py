"""Deterministic market eligibility checks for the Runtime boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any, Mapping, Protocol

from pumpagent.runtime.domain import MarketSnapshot


class MarketEligibilityReason(str, Enum):
    OK = "OK"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    LOW_VOLUME = "LOW_VOLUME"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    CHAOTIC_STRUCTURE = "CHAOTIC_STRUCTURE"
    NEW_LISTING = "NEW_LISTING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MarketEligibilityResult:
    """Structured outcome returned before any analytical module is called."""

    eligible: bool
    reason: MarketEligibilityReason
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason.value,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class MarketEligibilityConfig:
    """Conservative defaults matching the Runtime's current data contract."""

    minimum_candles: int = 1
    minimum_volume: float = 0.0
    minimum_liquidity: float = 0.0
    minimum_listing_age_candles: int = 100
    minimum_listing_age_days: float = 7.0
    maximum_spread_pct: float = 5.0


class EligibilityRule(Protocol):
    def evaluate(
        self, snapshot: MarketSnapshot, config: MarketEligibilityConfig
    ) -> MarketEligibilityResult | None: ...


def _reject(reason: MarketEligibilityReason, **details: Any) -> MarketEligibilityResult:
    return MarketEligibilityResult(False, reason, details)


class HistoryRule:
    def evaluate(self, snapshot: MarketSnapshot, config: MarketEligibilityConfig) -> MarketEligibilityResult | None:
        count = len(snapshot.ohlcv)
        if count < config.minimum_candles:
            return _reject(
                MarketEligibilityReason.INSUFFICIENT_HISTORY,
                candle_count=count,
                minimum_candles=config.minimum_candles,
            )
        return None


class NewListingRule:
    _AGE_CANDLE_KEYS = ("listing_age_candles", "market_age_candles")
    _AGE_DAY_KEYS = ("listing_age_days", "market_age_days")

    def evaluate(self, snapshot: MarketSnapshot, config: MarketEligibilityConfig) -> MarketEligibilityResult | None:
        metrics = snapshot.optional_market_metrics
        for key in self._AGE_CANDLE_KEYS:
            age = _number(metrics.get(key))
            if age is not None and age < config.minimum_listing_age_candles:
                return _reject(MarketEligibilityReason.NEW_LISTING, metric=key, value=age)
        for key in self._AGE_DAY_KEYS:
            age = _number(metrics.get(key))
            if age is not None and age < config.minimum_listing_age_days:
                return _reject(MarketEligibilityReason.NEW_LISTING, metric=key, value=age)
        return None


class VolumeRule:
    def evaluate(self, snapshot: MarketSnapshot, config: MarketEligibilityConfig) -> MarketEligibilityResult | None:
        volume = _number(snapshot.volume)
        if volume is None or volume <= config.minimum_volume:
            return _reject(
                MarketEligibilityReason.LOW_VOLUME,
                volume=snapshot.volume,
                minimum_volume=config.minimum_volume,
            )
        return None


class LiquidityRule:
    _KEYS = ("liquidity", "liquidity_usd", "market_depth_usd")

    def evaluate(self, snapshot: MarketSnapshot, config: MarketEligibilityConfig) -> MarketEligibilityResult | None:
        for key in self._KEYS:
            value = _number(snapshot.optional_market_metrics.get(key))
            if value is not None and value <= config.minimum_liquidity:
                return _reject(
                    MarketEligibilityReason.LOW_LIQUIDITY,
                    metric=key,
                    value=value,
                    minimum_liquidity=config.minimum_liquidity,
                )
        return None


class SpreadRule:
    def evaluate(self, snapshot: MarketSnapshot, config: MarketEligibilityConfig) -> MarketEligibilityResult | None:
        metrics = snapshot.optional_market_metrics
        spread_pct = _number(metrics.get("spread_pct"))
        if spread_pct is None:
            bid, ask = _number(metrics.get("bid")), _number(metrics.get("ask"))
            if bid is not None and ask is not None and bid > 0 and ask >= bid:
                spread_pct = (ask - bid) / bid * 100.0
        if spread_pct is not None and spread_pct > config.maximum_spread_pct:
            return _reject(
                MarketEligibilityReason.LOW_LIQUIDITY,
                spread_pct=spread_pct,
                maximum_spread_pct=config.maximum_spread_pct,
            )
        return None


class PriceStructureRule:
    def evaluate(self, snapshot: MarketSnapshot, config: MarketEligibilityConfig) -> MarketEligibilityResult | None:
        closes: list[float] = []
        for index, candle in enumerate(snapshot.ohlcv):
            close = _number(candle.get("close"))
            if close is None or close <= 0:
                return _reject(
                    MarketEligibilityReason.UNKNOWN,
                    diagnostic="invalid_close",
                    candle_index=index,
                )
            closes.append(close)

        returns = [(current - previous) / previous for previous, current in zip(closes, closes[1:])]
        if not returns:
            return None
        nonzero = [value for value in returns if value != 0]
        if returns and not nonzero:
            return _reject(MarketEligibilityReason.UNKNOWN, diagnostic="no_price_variation")

        return None


DEFAULT_RULES: tuple[EligibilityRule, ...] = (
    HistoryRule(),
    NewListingRule(),
    VolumeRule(),
    LiquidityRule(),
    SpreadRule(),
    PriceStructureRule(),
)


class MarketEligibilityFilter:
    """Run ordered, independently extensible eligibility rules."""

    def __init__(
        self,
        config: MarketEligibilityConfig | None = None,
        rules: tuple[EligibilityRule, ...] = DEFAULT_RULES,
    ) -> None:
        self.config = config or MarketEligibilityConfig()
        self.rules = rules

    def evaluate(self, snapshot: MarketSnapshot) -> MarketEligibilityResult:
        for rule in self.rules:
            result = rule.evaluate(snapshot, self.config)
            if result is not None:
                return result
        return MarketEligibilityResult(
            True,
            MarketEligibilityReason.OK,
            {"candle_count": len(snapshot.ohlcv)},
        )


def evaluate_market_eligibility(
    snapshot: MarketSnapshot,
    *,
    config: MarketEligibilityConfig | None = None,
) -> MarketEligibilityResult:
    return MarketEligibilityFilter(config=config).evaluate(snapshot)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if isfinite(converted) else None
