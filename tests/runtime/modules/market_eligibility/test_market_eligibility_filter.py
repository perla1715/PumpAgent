from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase, mock

from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus, RuntimeStatus
from pumpagent.runtime.modules.market_eligibility import (
    MarketEligibilityConfig,
    MarketEligibilityFilter,
    MarketEligibilityReason,
    MarketEligibilityResult,
    evaluate_market_eligibility,
)
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator


def make_snapshot(
    closes: tuple[float, ...] = (100.0, 101.0),
    *,
    volume: float = 50.0,
    metrics: dict[str, object] | None = None,
) -> MarketSnapshot:
    candles = tuple(
        {
            "timestamp": f"2026-07-01T12:{index:02d}:00+00:00",
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volume,
        }
        for index, close in enumerate(closes)
    )
    return MarketSnapshot(
        event_id="eligibility-test",
        timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        price=closes[-1] if closes else 0.0,
        ohlcv=candles,
        volume=volume,
        data_source="unit-test",
        data_quality_status=DataQualityStatus.VALID,
        optional_market_metrics=metrics or {},
    )


class MarketEligibilityFilterTests(TestCase):
    def test_accepts_market_with_required_information(self) -> None:
        result = evaluate_market_eligibility(make_snapshot())

        self.assertTrue(result.eligible)
        self.assertEqual(result.reason, MarketEligibilityReason.OK)
        self.assertEqual(result.to_dict()["reason"], "OK")

    def test_rejects_insufficient_history_first(self) -> None:
        result = evaluate_market_eligibility(
            make_snapshot((100.0,)),
            config=MarketEligibilityConfig(minimum_candles=2),
        )

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, MarketEligibilityReason.INSUFFICIENT_HISTORY)
        self.assertEqual(result.details["candle_count"], 1)

    def test_rejects_new_listing_from_available_metadata(self) -> None:
        result = evaluate_market_eligibility(
            make_snapshot(metrics={"listing_age_days": 2.0})
        )

        self.assertEqual(result.reason, MarketEligibilityReason.NEW_LISTING)

    def test_rejects_low_volume_and_explicit_low_liquidity(self) -> None:
        low_volume = evaluate_market_eligibility(make_snapshot(volume=0.0))
        low_liquidity = evaluate_market_eligibility(
            make_snapshot(metrics={"liquidity_usd": 0.0})
        )

        self.assertEqual(low_volume.reason, MarketEligibilityReason.LOW_VOLUME)
        self.assertEqual(low_liquidity.reason, MarketEligibilityReason.LOW_LIQUIDITY)

    def test_rejects_wide_spread_when_available(self) -> None:
        result = evaluate_market_eligibility(
            make_snapshot(metrics={"bid": 100.0, "ask": 106.0})
        )

        self.assertEqual(result.reason, MarketEligibilityReason.LOW_LIQUIDITY)
        self.assertAlmostEqual(result.details["spread_pct"], 6.0)

    def test_strong_pump_remains_eligible(self) -> None:
        result = evaluate_market_eligibility(
            make_snapshot((100.0, 101.0, 102.0, 160.0, 161.0, 162.0))
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.reason, MarketEligibilityReason.OK)

    def test_strong_dump_remains_eligible(self) -> None:
        result = evaluate_market_eligibility(
            make_snapshot((160.0, 159.0, 158.0, 100.0, 99.0, 98.0))
        )

        self.assertTrue(result.eligible)
        self.assertEqual(result.reason, MarketEligibilityReason.OK)

    def test_no_price_variation_is_structurally_ineligible(self) -> None:
        result = evaluate_market_eligibility(make_snapshot((100.0, 100.0)))

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, MarketEligibilityReason.UNKNOWN)
        self.assertEqual(result.details["diagnostic"], "no_price_variation")

    def test_rules_and_thresholds_are_configurable(self) -> None:
        config = MarketEligibilityConfig(minimum_candles=3)
        result = MarketEligibilityFilter(config=config).evaluate(make_snapshot())

        self.assertEqual(result.reason, MarketEligibilityReason.INSUFFICIENT_HISTORY)

    def test_rejection_stops_before_perception(self) -> None:
        rejected = MarketEligibilityResult(
            False, MarketEligibilityReason.INSUFFICIENT_HISTORY
        )
        eligibility_filter = mock.Mock()
        eligibility_filter.evaluate.return_value = rejected
        orchestrator = RuntimeOrchestrator(
            market_eligibility_filter=eligibility_filter
        )

        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.build_observation_package"
        ) as perception:
            result = orchestrator.process_market_update(make_snapshot())

        self.assertIs(result.runtime_status, RuntimeStatus.REJECTED)
        self.assertIs(
            result.compatibility_context["eligibility_result"],
            rejected,
        )
        perception.assert_not_called()
