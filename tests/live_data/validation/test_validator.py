from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.live_data.domain import (
    LiveDataQualityStatus,
    LiveDataTransport,
    NormalizedMarketDataInput,
    SourceMetadata,
)
from pumpagent.live_data.validation import (
    LiveDataValidationResult,
    validate_normalized_market_data_input,
)


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def make_source_metadata() -> SourceMetadata:
    return SourceMetadata(
        exchange="binance",
        adapter_name="fixture_adapter",
        adapter_version="0.1",
        source_timestamp=NOW,
        receive_timestamp=NOW,
        latency_ms=10.0,
        transport=LiveDataTransport.FIXTURE,
        correlation_id="corr-1",
        source_symbol="BTCUSDT",
        normalized_symbol="BTCUSDT",
        source_timeframe="1m",
        normalized_timeframe="1m",
    )


def make_input(
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
    )


class LiveDataValidatorTests(unittest.TestCase):
    def test_valid_input_returns_structured_valid_result(self) -> None:
        result = validate_normalized_market_data_input(make_input())

        self.assertIsInstance(result, LiveDataValidationResult)
        self.assertTrue(result.is_valid)
        self.assertTrue(result.required_fields_valid)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.missing_fields, ())

    def test_missing_required_top_level_field_is_reported(self) -> None:
        data = replace(make_input(), source_event_id=None)

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("source_event_id", result.missing_fields)
        self.assertIn("source_event_id is required.", result.errors)

    def test_symbol_exchange_and_timeframe_must_be_non_empty(self) -> None:
        data = replace(make_input(), symbol="", exchange=" ", timeframe="")

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("symbol", result.missing_fields)
        self.assertIn("exchange", result.missing_fields)
        self.assertIn("timeframe", result.missing_fields)

    def test_source_event_id_data_source_and_schema_version_must_be_non_empty(
        self,
    ) -> None:
        data = replace(
            make_input(),
            source_event_id="",
            data_source=" ",
            schema_version="",
        )

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("source_event_id", result.missing_fields)
        self.assertIn("data_source", result.missing_fields)
        self.assertIn("schema_version", result.missing_fields)

    def test_price_and_volume_must_be_numeric_and_finite(self) -> None:
        data = replace(make_input(), price=float("nan"), volume=float("inf"))

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("price must be numeric and finite.", result.errors)
        self.assertIn("volume must be numeric and finite.", result.errors)

    def test_ohlcv_must_be_non_empty(self) -> None:
        data = replace(make_input(), ohlcv=())

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("ohlcv", result.missing_fields)
        self.assertIn("ohlcv must contain at least one candle.", result.errors)

    def test_every_candle_must_contain_required_fields(self) -> None:
        candle = {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
        }
        data = replace(make_input(), ohlcv=(candle,))

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("ohlcv[0].volume", result.missing_fields)
        self.assertIn("ohlcv[0].volume is required.", result.errors)

    def test_malformed_candle_must_be_mapping_object(self) -> None:
        data = replace(make_input(), ohlcv=(["bad"],))

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("ohlcv[0] candle must be a mapping/object.", result.errors)

    def test_candle_numeric_fields_must_be_numeric_and_finite(self) -> None:
        candle = {
            "timestamp": "2026-07-01T12:00:00+00:00",
            "open": "not-a-number",
            "high": 101.0,
            "low": 98.0,
            "close": float("inf"),
            "volume": 10.0,
        }
        data = replace(make_input(), ohlcv=(candle,))

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("ohlcv[0].open must be numeric and finite.", result.errors)
        self.assertIn("ohlcv[0].close must be numeric and finite.", result.errors)

    def test_timestamps_must_be_parseable(self) -> None:
        candle = {
            "timestamp": "not-a-time",
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
            "volume": 10.0,
        }
        data = replace(
            make_input(),
            source_timestamp="not-a-time",
            receive_timestamp="also-not-a-time",
            ohlcv=(candle,),
        )

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertIn("source_timestamp must be a parseable timestamp.", result.errors)
        self.assertIn("receive_timestamp must be a parseable timestamp.", result.errors)
        self.assertIn(
            "ohlcv[0].timestamp must be a parseable timestamp.",
            result.errors,
        )

    def test_partial_quality_allows_required_fields_when_valid(self) -> None:
        data = replace(
            make_input(LiveDataQualityStatus.PARTIAL),
            missing_fields=("open_interest",),
        )

        result = validate_normalized_market_data_input(data)

        self.assertTrue(result.is_valid)
        self.assertTrue(result.required_fields_valid)
        self.assertIn("open_interest", result.missing_fields)

    def test_partial_quality_required_fields_invalid_when_core_field_missing(
        self,
    ) -> None:
        data = replace(
            make_input(LiveDataQualityStatus.PARTIAL),
            symbol="",
            missing_fields=("open_interest",),
        )

        result = validate_normalized_market_data_input(data)

        self.assertFalse(result.is_valid)
        self.assertFalse(result.required_fields_valid)
        self.assertIn("partial_quality_has_invalid_required_fields", result.warnings)

    def test_validation_result_serializes_to_primitives(self) -> None:
        result = validate_normalized_market_data_input(make_input())

        serialized = result.to_dict()

        self.assertTrue(serialized["is_valid"])
        self.assertEqual(serialized["errors"], [])
        self.assertEqual(serialized["missing_fields"], [])


if __name__ == "__main__":
    unittest.main()
