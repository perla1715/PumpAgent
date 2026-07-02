from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
from urllib import error

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
BYBIT_ADAPTER = SRC / "pumpagent" / "live_data" / "adapters" / "bybit_adapter.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.live_data.adapters.base_adapter import BaseLiveDataAdapter
from pumpagent.live_data.adapters.bybit_adapter import BybitAdapter
from pumpagent.live_data.domain import (
    LiveDataError,
    LiveDataErrorType,
    LiveDataResult,
    LiveDataTransport,
)
from pumpagent.live_data.sources import LiveDataSource


class FakeHttpResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class MockedBybitAdapter(BybitAdapter):
    def __init__(self) -> None:
        self.performed_request_metadata = None

    def _perform_request(self, request_metadata: dict[str, object]) -> LiveDataResult:
        self.performed_request_metadata = request_metadata
        return LiveDataResult(
            success=False,
            error=LiveDataError(
                error_type=LiveDataErrorType.UNKNOWN_ERROR,
                message="mocked transport placeholder",
                exchange=self.EXCHANGE_NAME,
                symbol=request_metadata["params"]["symbol"],
                timeframe=request_metadata["params"]["interval"],
                receive_timestamp=datetime.now(timezone.utc),
                retryable=False,
                raw_payload_reference="mocked://bybit/kline",
            ),
        )


class BybitAdapterTests(unittest.TestCase):
    def test_bybit_adapter_satisfies_live_data_source(self) -> None:
        adapter = BybitAdapter()

        self.assertIsInstance(adapter, BaseLiveDataAdapter)
        self.assertIsInstance(adapter, LiveDataSource)

    def test_capabilities_describe_bybit_public_rest_linear_scope(self) -> None:
        capabilities = BybitAdapter().capabilities()

        self.assertEqual(capabilities.adapter_name, "bybit")
        self.assertEqual(capabilities.supported_transports, (LiveDataTransport.REST,))
        self.assertTrue(capabilities.public_data_only)
        self.assertEqual(capabilities.supported_market_categories, ("linear",))
        self.assertIn("1", capabilities.supported_timeframes)
        self.assertIn("D", capabilities.supported_timeframes)
        self.assertTrue(capabilities.supports_historical)
        self.assertFalse(capabilities.supports_websocket)
        self.assertFalse(capabilities.supports_optional_metrics)

    def test_unsupported_timeframe_returns_live_data_error(self) -> None:
        result = BybitAdapter().load("BTCUSDT", "2", category="linear")

        self.assertIsInstance(result, LiveDataResult)
        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.UNSUPPORTED_TIMEFRAME)
        self.assertIn("unsupported_timeframe", result.error.validation_errors)

    def test_unsupported_category_returns_adapter_error(self) -> None:
        result = BybitAdapter().load("BTCUSDT", "1", category="spot")

        self.assertIsInstance(result, LiveDataResult)
        self.assertFalse(result.success)
        # Category checks are adapter-local request validation, not normalized
        # Live Data payload validation.
        self.assertEqual(result.error.error_type, LiveDataErrorType.VALIDATION_FAILED)
        self.assertIn("unsupported_category", result.error.validation_errors)

    def test_empty_symbol_returns_local_request_validation_error(self) -> None:
        result = BybitAdapter().load("", "1", category="linear")

        self.assertIsInstance(result, LiveDataResult)
        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.VALIDATION_FAILED)
        self.assertIn("empty_symbol", result.error.validation_errors)

    def test_empty_category_returns_local_request_validation_error(self) -> None:
        result = BybitAdapter().load("BTCUSDT", "1", category="")

        self.assertIsInstance(result, LiveDataResult)
        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.VALIDATION_FAILED)
        self.assertIn("empty_category", result.error.validation_errors)

    def test_load_returns_live_data_result_and_uses_mocked_transport(self) -> None:
        adapter = MockedBybitAdapter()

        result = adapter.load("btcusdt", "1")

        self.assertIsInstance(result, LiveDataResult)
        self.assertFalse(result.success)
        self.assertIsNotNone(adapter.performed_request_metadata)
        self.assertEqual(adapter.performed_request_metadata["endpoint"], "/v5/market/kline")
        self.assertEqual(
            adapter.performed_request_metadata["params"],
            {
                "category": "linear",
                "symbol": "BTCUSDT",
                "interval": "1",
                "limit": 1,
            },
        )

    def test_historical_request_metadata_is_prepared_without_http(self) -> None:
        adapter = MockedBybitAdapter()
        start = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 1, 12, 1, tzinfo=timezone.utc)

        result = adapter.load_historical_candles(
            "BTCUSDT",
            "1",
            start=start,
            end=end,
            limit=100,
        )

        params = adapter.performed_request_metadata["params"]
        self.assertFalse(result.success)
        self.assertEqual(params["start"], 1782907200000)
        self.assertEqual(params["end"], 1782907260000)
        self.assertEqual(params["limit"], 100)

    def test_no_runtime_objects_are_created(self) -> None:
        result = MockedBybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(hasattr(result, "market_snapshot"))
        self.assertFalse(hasattr(result, "runtime_event"))
        self.assertFalse(hasattr(result, "decision_alert"))

    def test_successful_http_response_returns_raw_acquisition_data(self) -> None:
        payload = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "category": "linear",
                "symbol": "BTCUSDT",
                "list": [["1782907200000", "100", "101", "99", "100", "10", "1000"]],
            },
        }

        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            return_value=FakeHttpResponse(payload),
        ) as urlopen_mock:
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertTrue(result.success)
        self.assertIsNone(result.data)
        self.assertEqual(result.raw_data["payload"], payload)
        self.assertEqual(result.raw_data["endpoint"], "/v5/market/kline")
        urlopen_mock.assert_called_once()
        called_url = urlopen_mock.call_args.args[0]
        self.assertIn("/v5/market/kline?", called_url)
        self.assertIn("category=linear", called_url)
        self.assertIn("symbol=BTCUSDT", called_url)
        self.assertIn("interval=1", called_url)

    def test_configurable_timeout_is_used_for_http_request(self) -> None:
        payload = {
            "retCode": 0,
            "result": {"list": []},
        }

        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            return_value=FakeHttpResponse(payload),
        ) as urlopen_mock:
            BybitAdapter(timeout_seconds=3.5).load("BTCUSDT", "1")

        self.assertEqual(urlopen_mock.call_args.kwargs["timeout"], 3.5)

    def test_timeout_maps_to_timeout_error(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.TIMEOUT)
        self.assertIn("timeout", result.error.validation_errors)

    def test_http_429_maps_to_rate_limited(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            side_effect=error.HTTPError(
                url="https://api.bybit.com/v5/market/kline",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=None,
            ),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.RATE_LIMITED)
        self.assertIn("rate_limited", result.error.validation_errors)

    def test_http_5xx_maps_to_exchange_unavailable(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            side_effect=error.HTTPError(
                url="https://api.bybit.com/v5/market/kline",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=None,
            ),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.EXCHANGE_UNAVAILABLE)
        self.assertIn("exchange_unavailable", result.error.validation_errors)

    def test_malformed_json_maps_to_malformed_payload(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            return_value=FakeHttpResponse(b"{not-json"),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn("invalid_json", result.error.validation_errors)

    def test_unicode_decode_error_maps_to_malformed_payload(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            return_value=FakeHttpResponse(b"\xff"),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn("invalid_utf8", result.error.validation_errors)

    def test_missing_ret_code_maps_to_malformed_payload(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            return_value=FakeHttpResponse({"result": {"list": []}}),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn("missing_ret_code", result.error.validation_errors)

    def test_missing_result_maps_to_malformed_payload(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            return_value=FakeHttpResponse({"retCode": 0}),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn("missing_result", result.error.validation_errors)

    def test_missing_list_maps_to_malformed_payload(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            return_value=FakeHttpResponse({"retCode": 0, "result": {}}),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn("missing_list", result.error.validation_errors)

    def test_malformed_list_maps_to_malformed_payload(self) -> None:
        with mock.patch(
            "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
            return_value=FakeHttpResponse({"retCode": 0, "result": {"list": "bad"}}),
        ):
            result = BybitAdapter().load("BTCUSDT", "1")

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn("malformed_list", result.error.validation_errors)

    def test_non_zero_ret_code_maps_to_existing_adapter_error_types(self) -> None:
        cases = (
            (10000, LiveDataErrorType.TIMEOUT, "timeout"),
            (10006, LiveDataErrorType.RATE_LIMITED, "rate_limited"),
            (10016, LiveDataErrorType.EXCHANGE_UNAVAILABLE, "exchange_unavailable"),
            (10029, LiveDataErrorType.UNSUPPORTED_SYMBOL, "unsupported_symbol"),
            (99999, LiveDataErrorType.UNKNOWN_ERROR, "non_zero_ret_code"),
        )

        for ret_code, expected_error, expected_reason in cases:
            with self.subTest(ret_code=ret_code), mock.patch(
                "pumpagent.live_data.adapters.bybit_adapter.request.urlopen",
                return_value=FakeHttpResponse(
                    {"retCode": ret_code, "retMsg": "Bybit error"}
                ),
            ):
                result = BybitAdapter().load("BTCUSDT", "1")

            self.assertFalse(result.success)
            self.assertEqual(result.error.error_type, expected_error)
            self.assertIn(expected_reason, result.error.validation_errors)

    def test_bybit_adapter_imports_no_runtime_or_networking_modules(self) -> None:
        tree = ast.parse(BYBIT_ADAPTER.read_text(encoding="utf-8"))
        imports = _imports_from(tree)

        # The approved HTTP transport uses urllib from the standard library.
        # Runtime imports and unapproved networking libraries remain forbidden.
        forbidden = tuple(
            imported
            for imported in imports
            if imported == "pumpagent.runtime"
            or imported.startswith("pumpagent.runtime.")
            or imported
            in {
                "requests",
                "httpx",
                "aiohttp",
                "websocket",
                "websockets",
            }
        )
        self.assertEqual(forbidden, ())


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
