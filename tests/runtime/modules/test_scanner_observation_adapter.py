"""Focused contract tests for the Scanner V2 observation boundary."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import copy
import unittest

from pumpagent.runtime.domain.base import FrozenDict
from pumpagent.runtime.adapters.scanner_observation import (
    ScannerAdapterStatus,
    ScannerAttentionDecision,
    ScannerTriggerReason,
    build_observation_request_from_scanner_result,
)


BUCKET = 1_783_889_100
REQUESTED_AT = datetime(2026, 7, 12, 19, 30, tzinfo=timezone.utc)


def record(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "contract_version": "1.0",
        "scanner_version": "Market Participation Scanner V2 MVP",
        "provider": "coinalyze",
        "exchange": "BYBIT",
        "symbol": "BTCUSDT.6",
        "status": "VALID",
        "timestamp_bucket": BUCKET,
        "timeframe": "5m",
        "metrics": {
            "price_5m_pct": 1.25,
            "volume_ratio_5m": 2.4,
            "oi_change_5m_pct": 0.8,
        },
        "data_quality": {
            "closed_candles_only": True,
            "ohlcv_oi_aligned": True,
            "raw_ohlcv_records": 17,
            "closed_ohlcv_records": 16,
            "required_closed_ohlcv_records": 11,
        },
    }
    result.update(overrides)
    return result


DECISION = ScannerAttentionDecision(
    eligible=True,
    approved_reasons=(
        ScannerTriggerReason.VOLUME_SPIKE,
        ScannerTriggerReason.OI_GROWTH,
        ScannerTriggerReason.PRICE_ACTIVITY,
    ),
)


class ScannerObservationAdapterTests(unittest.TestCase):
    def test_valid_eligible_result_creates_canonical_request(self) -> None:
        result = build_observation_request_from_scanner_result(
            record(), DECISION, request_timestamp=REQUESTED_AT
        )
        self.assertTrue(result.success)
        request = result.request
        assert request is not None
        self.assertEqual((request.exchange, request.symbol, request.timeframe), ("bybit", "BTCUSDT", "5m"))
        self.assertEqual(request.request_timestamp, REQUESTED_AT)
        self.assertEqual(request.trigger_timestamp.tzinfo, timezone.utc)
        self.assertEqual(request.triggering_closed_candle_timestamp, request.trigger_timestamp)
        self.assertEqual(request.trigger_reasons, ("VOLUME_SPIKE", "OI_GROWTH", "PRICE_ACTIVITY"))

    def test_metrics_preserve_5m_names_units_and_quality(self) -> None:
        result = build_observation_request_from_scanner_result(record(), DECISION)
        assert result.request is not None
        metrics = result.request.trigger_metrics
        self.assertEqual(metrics["price_change_5m_pct"], 1.25)
        self.assertEqual(metrics["volume_ratio_5m"], 2.4)
        self.assertEqual(metrics["oi_change_5m_pct"], 0.8)
        self.assertEqual(metrics["timestamp_bucket_5m"], BUCKET)
        self.assertNotIn("price_change_1m_pct", metrics)
        self.assertIsInstance(metrics, FrozenDict)
        self.assertIsInstance(metrics["units"], FrozenDict)
        with self.assertRaises(TypeError):
            metrics["units"]["volume_ratio_5m"] = "changed"  # type: ignore[index]

    def test_no_request_for_non_attention_skipped_or_failed(self) -> None:
        cases = (
            (record(), ScannerAttentionDecision(False), ScannerAdapterStatus.NOT_ATTENTION_ELIGIBLE),
            (record(status="SKIPPED"), DECISION, ScannerAdapterStatus.SKIPPED),
            (record(status="FAILED"), DECISION, ScannerAdapterStatus.FAILED),
        )
        for source, decision, expected in cases:
            with self.subTest(expected=expected):
                result = build_observation_request_from_scanner_result(source, decision)
                self.assertEqual(result.status, expected)
                self.assertIsNone(result.request)

    def test_quality_identity_and_timeframe_failures_are_typed(self) -> None:
        cases = (
            (record(data_quality={"closed_candles_only": True, "ohlcv_oi_aligned": False}), ScannerAdapterStatus.UNALIGNED_EVIDENCE),
            (record(data_quality={"closed_candles_only": False, "ohlcv_oi_aligned": True}), ScannerAdapterStatus.OPEN_CANDLE),
            (record(timeframe="1m"), ScannerAdapterStatus.UNSUPPORTED_TIMEFRAME),
            (record(exchange=""), ScannerAdapterStatus.INCOMPLETE_IDENTITY),
        )
        for source, expected in cases:
            with self.subTest(expected=expected):
                result = build_observation_request_from_scanner_result(source, DECISION)
                self.assertEqual(result.status, expected)
                self.assertIsNone(result.request)

    def test_naive_timestamp_is_rejected_and_epoch_is_utc(self) -> None:
        naive = datetime(2026, 7, 12, 19, 30)
        rejected = build_observation_request_from_scanner_result(record(), DECISION, request_timestamp=naive)
        self.assertEqual(rejected.status, ScannerAdapterStatus.INVALID_TIMESTAMP)
        accepted = build_observation_request_from_scanner_result(record(), DECISION)
        assert accepted.request is not None
        self.assertEqual(accepted.request.trigger_timestamp.tzinfo, timezone.utc)

    def test_missing_metric_is_not_fabricated_as_zero(self) -> None:
        source = record(metrics={"price_5m_pct": 1.0, "volume_ratio_5m": 2.0, "oi_change_5m_pct": None})
        result = build_observation_request_from_scanner_result(source, DECISION)
        self.assertEqual(result.status, ScannerAdapterStatus.MALFORMED)
        self.assertIsNone(result.request)

    def test_input_is_unchanged_and_conversion_is_deterministic(self) -> None:
        source = record()
        original = copy.deepcopy(source)
        first = build_observation_request_from_scanner_result(source, DECISION)
        second = build_observation_request_from_scanner_result(source, DECISION)
        self.assertEqual(source, original)
        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.adapter_reason = "changed"  # type: ignore[misc]

    def test_attention_contract_rejects_unapproved_or_trading_reasons(self) -> None:
        for reason in ("LONG", "SHORT", "confidence", "CONTINUATION_ALIVE"):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                ScannerAttentionDecision(True, (reason,))


if __name__ == "__main__":
    unittest.main()
