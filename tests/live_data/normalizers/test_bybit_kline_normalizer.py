from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
NORMALIZER = (
    SRC / "pumpagent" / "live_data" / "normalizers" / "bybit_kline_normalizer.py"
)

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
from pumpagent.live_data.normalizers import normalize_bybit_kline_raw_data


class BybitKlineNormalizerHappyPathTests(unittest.TestCase):
    def test_successful_normalization_returns_normalized_market_data_input(self) -> None:
        result = normalize_bybit_kline_raw_data(_raw_result())

        self.assertTrue(result.success)
        self.assertIsInstance(result.data, NormalizedMarketDataInput)
        self.assertIsNone(result.raw_data)
        self.assertIsNone(result.error)

    def test_timestamp_and_ohlcv_conversion(self) -> None:
        result = normalize_bybit_kline_raw_data(_raw_result())
        data = result.data

        self.assertEqual(data.source_timestamp.isoformat(), "2026-07-01T12:01:00+00:00")
        self.assertEqual(len(data.ohlcv), 2)
        self.assertEqual(data.ohlcv[0]["timestamp"].isoformat(), "2026-07-01T12:00:00+00:00")
        self.assertEqual(data.ohlcv[0]["open"], 99.0)
        self.assertEqual(data.ohlcv[0]["high"], 101.0)
        self.assertEqual(data.ohlcv[0]["low"], 98.0)
        self.assertEqual(data.ohlcv[0]["close"], 100.0)
        self.assertEqual(data.ohlcv[0]["volume"], 10.0)

    def test_latest_close_and_volume_map_to_price_and_volume(self) -> None:
        result = normalize_bybit_kline_raw_data(_raw_result())
        data = result.data

        self.assertEqual(data.price, 102.0)
        self.assertEqual(data.volume, 12.0)

    def test_symbol_timeframe_and_source_metadata_mapping(self) -> None:
        result = normalize_bybit_kline_raw_data(_raw_result())
        data = result.data
        metadata = data.source_metadata

        self.assertEqual(data.symbol, "BTCUSDT")
        self.assertEqual(data.exchange, "bybit")
        self.assertEqual(data.timeframe, "1")
        self.assertEqual(data.data_source, "bybit_public_kline")
        self.assertEqual(data.quality_status, LiveDataQualityStatus.UNKNOWN)
        self.assertEqual(metadata.exchange, "bybit")
        self.assertEqual(metadata.adapter_name, "bybit")
        self.assertEqual(metadata.adapter_version, "0.3")
        self.assertEqual(metadata.transport, LiveDataTransport.REST)
        self.assertEqual(metadata.source_symbol, "BTCUSDT")
        self.assertEqual(metadata.normalized_symbol, "BTCUSDT")
        self.assertEqual(metadata.source_timeframe, "1")
        self.assertEqual(metadata.normalized_timeframe, "1")

    def test_turnover_and_source_reference_are_preserved_as_metadata(self) -> None:
        result = normalize_bybit_kline_raw_data(_raw_result())
        data = result.data

        self.assertEqual(data.optional_market_metrics["category"], "linear")
        self.assertEqual(data.optional_market_metrics["turnover"], 1224.0)
        self.assertEqual(data.raw_payload_reference, "/v5/market/kline")

    def test_no_runtime_imports_or_runtime_objects(self) -> None:
        result = normalize_bybit_kline_raw_data(_raw_result())
        imports = _imports_from(ast.parse(NORMALIZER.read_text(encoding="utf-8")))

        self.assertFalse(hasattr(result, "market_snapshot"))
        self.assertFalse(hasattr(result, "runtime_event"))
        self.assertFalse(
            any(
                imported == "pumpagent.runtime"
                or imported.startswith("pumpagent.runtime.")
                for imported in imports
            )
        )


class BybitKlineNormalizerErrorHandlingTests(unittest.TestCase):
    def test_failed_input_live_data_result_is_returned_as_failure(self) -> None:
        original_error = LiveDataError(
            error_type=LiveDataErrorType.MALFORMED_PAYLOAD,
            message="transport failed",
            exchange="bybit",
            symbol="BTCUSDT",
            timeframe="1",
            receive_timestamp=datetime.now(timezone.utc),
            retryable=False,
            validation_errors=("transport_failed",),
        )

        result = normalize_bybit_kline_raw_data(
            LiveDataResult(success=False, error=original_error)
        )

        self.assertFalse(result.success)
        self.assertIs(result.error, original_error)

    def test_missing_raw_data_returns_malformed_payload(self) -> None:
        result = normalize_bybit_kline_raw_data(
            LiveDataResult(success=True, data=_normalized_placeholder())
        )

        self.assert_error(result, "missing_raw_data")

    def test_non_mapping_raw_data_returns_malformed_payload(self) -> None:
        result = normalize_bybit_kline_raw_data(
            LiveDataResult(success=True, raw_data=["not", "a", "mapping"])
        )

        self.assert_error(result, "missing_raw_data")

    def test_missing_payload_returns_malformed_payload(self) -> None:
        result = normalize_bybit_kline_raw_data(_raw_result_without("payload"))

        self.assert_error(result, "missing_payload")

    def test_non_mapping_payload_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        raw["payload"] = "not-a-payload"

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "missing_payload")

    def test_missing_request_metadata_returns_malformed_payload(self) -> None:
        result = normalize_bybit_kline_raw_data(_raw_result_without("request_metadata"))

        self.assert_error(result, "missing_request_metadata")

    def test_non_mapping_request_metadata_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        raw["request_metadata"] = "not-metadata"

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "missing_request_metadata")

    def test_missing_request_metadata_params_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        del raw["request_metadata"]["params"]

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "missing_request_metadata")

    def test_missing_payload_result_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        del raw["payload"]["result"]

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "missing_result")

    def test_non_mapping_payload_result_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        raw["payload"]["result"] = "not-a-result"

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "missing_result")

    def test_missing_payload_result_list_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        del raw["payload"]["result"]["list"]

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "missing_list")

    def test_non_list_payload_result_list_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        raw["payload"]["result"]["list"] = "not-a-list"

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "missing_list")

    def test_empty_result_list_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        raw["payload"]["result"]["list"] = []

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "empty_list")

    def test_malformed_candle_row_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        raw["payload"]["result"]["list"] = ["bad"]

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "malformed_candle")

    def test_insufficient_candle_fields_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        raw["payload"]["result"]["list"] = [["1782907200000", "99"]]

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "insufficient_candle_fields")

    def test_invalid_timestamp_conversion_returns_malformed_payload(self) -> None:
        raw = _raw_data()
        raw["payload"]["result"]["list"][0][0] = "not-a-timestamp"

        result = normalize_bybit_kline_raw_data(LiveDataResult(success=True, raw_data=raw))

        self.assert_error(result, "invalid_timestamp")

    def test_invalid_ohlcv_numeric_conversion_returns_malformed_payload(self) -> None:
        for column_index in range(1, 6):
            with self.subTest(column_index=column_index):
                raw = _raw_data()
                raw["payload"]["result"]["list"][0][column_index] = "not-a-number"

                result = normalize_bybit_kline_raw_data(
                    LiveDataResult(success=True, raw_data=raw)
                )

                self.assert_error(result, "invalid_numeric")

    def assert_error(self, result: LiveDataResult, reason: str) -> None:
        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn(reason, result.error.validation_errors)
        self.assertFalse(hasattr(result, "market_snapshot"))
        self.assertFalse(hasattr(result, "runtime_event"))


def _raw_result() -> LiveDataResult:
    return LiveDataResult(success=True, raw_data=_raw_data())


def _raw_result_without(key: str) -> LiveDataResult:
    raw = _raw_data()
    del raw[key]
    return LiveDataResult(success=True, raw_data=raw)


def _raw_data() -> dict[str, object]:
    payload = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "category": "linear",
            "symbol": "BTCUSDT",
            "list": [
                ["1782907200000", "99", "101", "98", "100", "10", "1000"],
                ["1782907260000", "100", "103", "99", "102", "12", "1224"],
            ],
        },
    }
    return deepcopy(
        {
            "exchange": "bybit",
            "endpoint": "/v5/market/kline",
            "request_metadata": {
                "exchange": "bybit",
                "transport": "rest",
                "endpoint": "/v5/market/kline",
                "params": {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "interval": "1",
                    "limit": 2,
                },
                "public_data_only": True,
            },
            "payload": payload,
        }
    )


def _normalized_placeholder() -> NormalizedMarketDataInput:
    now = datetime.now(timezone.utc)
    return NormalizedMarketDataInput(
        source_event_id="placeholder",
        symbol="BTCUSDT",
        exchange="bybit",
        timeframe="1",
        source_timestamp=now,
        receive_timestamp=now,
        price=100.0,
        ohlcv=(
            {
                "timestamp": now,
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 10.0,
            },
        ),
        volume=10.0,
        data_source="placeholder",
        quality_status=LiveDataQualityStatus.UNKNOWN,
        source_metadata=SourceMetadata(
            exchange="bybit",
            adapter_name="bybit",
            adapter_version="0.3",
            source_timestamp=now,
            receive_timestamp=now,
            transport=LiveDataTransport.REST,
            source_symbol="BTCUSDT",
            normalized_symbol="BTCUSDT",
            source_timeframe="1",
            normalized_timeframe="1",
        ),
    )


def _imports_from(tree: ast.AST) -> tuple[str, ...]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(imports)


if __name__ == "__main__":
    unittest.main()
