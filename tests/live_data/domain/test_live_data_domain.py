from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.live_data.domain import (
    LiveDataError,
    LiveDataErrorType,
    LiveDataQualityStatus,
    LiveDataResult,
    LiveDataTransport,
    NormalizedMarketDataInput,
    SourceMetadata,
)


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def make_source_metadata() -> SourceMetadata:
    return SourceMetadata(
        exchange="binance",
        adapter_name="fixture_adapter",
        adapter_version="0.1",
        source_timestamp=NOW,
        receive_timestamp=NOW,
        latency_ms=12.5,
        transport=LiveDataTransport.FIXTURE,
        correlation_id="corr-1",
        source_symbol="BTCUSDT",
        normalized_symbol="BTCUSDT",
        source_timeframe="1m",
        normalized_timeframe="1m",
    )


def make_normalized_input(
    quality_status: LiveDataQualityStatus = LiveDataQualityStatus.GOOD,
) -> NormalizedMarketDataInput:
    return NormalizedMarketDataInput(
        source_event_id="source-evt-1",
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        source_timestamp=NOW,
        receive_timestamp=NOW,
        price=100.0,
        ohlcv=(
            {
                "timestamp": "2026-07-01T12:00:00+00:00",
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 10.0,
            },
        ),
        volume=10.0,
        data_source="fixture",
        quality_status=quality_status,
        source_metadata=make_source_metadata(),
        optional_market_metrics={
            "open_interest": 1000.0,
            "normalizer_version": "0.1",
            "validator_version": "0.1",
        },
        quality_reasons=("complete_fixture_data",),
        missing_fields=(),
        validation_warnings=(),
    )


def make_live_data_error() -> LiveDataError:
    return LiveDataError(
        error_type=LiveDataErrorType.VALIDATION_FAILED,
        message="payload failed validation",
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="1m",
        receive_timestamp=NOW,
        retryable=False,
        validation_errors=("price_missing",),
        correlation_id="corr-1",
    )


class LiveDataDomainTests(unittest.TestCase):
    def test_normalized_market_data_input_can_be_created(self) -> None:
        data = make_normalized_input()

        self.assertEqual(data.source_event_id, "source-evt-1")
        self.assertEqual(data.symbol, "BTCUSDT")
        self.assertEqual(data.quality_status, LiveDataQualityStatus.GOOD)
        self.assertEqual(data.source_metadata.adapter_name, "fixture_adapter")

    def test_source_metadata_can_be_created(self) -> None:
        metadata = make_source_metadata()

        self.assertEqual(metadata.exchange, "binance")
        self.assertEqual(metadata.transport, LiveDataTransport.FIXTURE)
        self.assertEqual(metadata.correlation_id, "corr-1")

    def test_live_data_error_can_be_created(self) -> None:
        error = make_live_data_error()

        self.assertEqual(error.error_type, LiveDataErrorType.VALIDATION_FAILED)
        self.assertFalse(error.retryable)
        self.assertEqual(error.validation_errors, ("price_missing",))

    def test_live_data_result_success_requires_data_only(self) -> None:
        data = make_normalized_input()

        result = LiveDataResult(success=True, data=data)

        self.assertTrue(result.success)
        self.assertIs(result.data, data)
        self.assertIsNone(result.raw_data)
        self.assertIsNone(result.error)

    def test_live_data_result_success_can_contain_raw_data_only(self) -> None:
        raw_data = {"payload": {"retCode": 0}}

        result = LiveDataResult(success=True, raw_data=raw_data)

        self.assertTrue(result.success)
        self.assertIs(result.raw_data, raw_data)
        self.assertIsNone(result.data)
        self.assertIsNone(result.error)

    def test_live_data_result_failure_requires_error_only(self) -> None:
        error = make_live_data_error()

        result = LiveDataResult(success=False, error=error)

        self.assertFalse(result.success)
        self.assertIs(result.error, error)
        self.assertIsNone(result.data)

    def test_live_data_result_rejects_success_without_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires data or raw_data"):
            LiveDataResult(success=True)

    def test_live_data_result_rejects_success_with_data_and_raw_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot include both"):
            LiveDataResult(
                success=True,
                data=make_normalized_input(),
                raw_data={"payload": {}},
            )

    def test_live_data_result_rejects_success_with_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot include error"):
            LiveDataResult(
                success=True,
                data=make_normalized_input(),
                error=make_live_data_error(),
            )

    def test_live_data_result_rejects_failure_without_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires error"):
            LiveDataResult(success=False)

    def test_live_data_result_rejects_failure_with_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot include data"):
            LiveDataResult(
                success=False,
                data=make_normalized_input(),
                error=make_live_data_error(),
            )

    def test_live_data_result_rejects_failure_with_raw_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot include raw_data"):
            LiveDataResult(
                success=False,
                raw_data={"payload": {}},
                error=make_live_data_error(),
            )

    def test_live_data_domain_objects_are_frozen(self) -> None:
        data = make_normalized_input()

        with self.assertRaises(FrozenInstanceError):
            data.symbol = "ETHUSDT"  # type: ignore[misc]

    def test_nested_payloads_are_defensively_frozen(self) -> None:
        data = make_normalized_input()

        with self.assertRaises(TypeError):
            data.ohlcv[0]["close"] = 101.0

        with self.assertRaises(TypeError):
            data.optional_market_metrics["open_interest"] = 1200.0

    def test_live_data_domain_serializes_to_primitives(self) -> None:
        serialized = make_normalized_input().to_dict()

        self.assertEqual(serialized["quality_status"], "good")
        self.assertEqual(serialized["source_timestamp"], NOW.isoformat())
        self.assertEqual(serialized["source_metadata"]["transport"], "fixture")


if __name__ == "__main__":
    unittest.main()
