from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
ADAPTER_SRC = SRC / "pumpagent" / "live_data" / "adapters"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.live_data.adapters import (
    AdapterCapabilities,
    AdapterError,
    AdapterErrorType,
    AdapterResult,
    BaseLiveDataAdapter,
)
from pumpagent.live_data.domain import (
    LiveDataError,
    LiveDataErrorType,
    LiveDataQualityStatus,
    LiveDataResult,
    LiveDataTransport,
    NormalizedMarketDataInput,
    SourceMetadata,
)
from pumpagent.live_data.sources import LiveDataSource


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


class DummyAdapter(BaseLiveDataAdapter):
    def load_latest_snapshot(self, symbol: str, timeframe: str) -> LiveDataResult:
        return LiveDataResult(success=True, data=_normalized_input(symbol, timeframe))

    def load_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> LiveDataResult:
        return LiveDataResult(success=True, data=_normalized_input(symbol, timeframe))

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_name="dummy",
            supported_transports=(LiveDataTransport.FIXTURE,),
            supported_timeframes=("1m",),
            supported_market_categories=("fixture",),
            supports_historical=True,
        )


class AdapterFrameworkTests(unittest.TestCase):
    def test_base_adapter_satisfies_live_data_source_protocol(self) -> None:
        self.assertIsInstance(DummyAdapter(), LiveDataSource)

    def test_adapter_public_methods_return_live_data_result_only(self) -> None:
        adapter = DummyAdapter()

        latest = adapter.load_latest_snapshot("BTCUSDT", "1m")
        historical = adapter.load_historical_candles("BTCUSDT", "1m")
        default_load = adapter.load("BTCUSDT", "1m")

        self.assertIsInstance(latest, LiveDataResult)
        self.assertIsInstance(historical, LiveDataResult)
        self.assertIsInstance(default_load, LiveDataResult)

    def test_adapter_results_do_not_return_runtime_objects(self) -> None:
        result = DummyAdapter().load("BTCUSDT", "1m")

        self.assertFalse(hasattr(result, "market_snapshot"))
        self.assertFalse(hasattr(result, "runtime_event"))
        self.assertFalse(hasattr(result, "decision_alert"))

    def test_adapter_capabilities_are_metadata_only(self) -> None:
        capabilities = DummyAdapter().capabilities()

        self.assertEqual(capabilities.adapter_name, "dummy")
        self.assertEqual(capabilities.supported_transports, (LiveDataTransport.FIXTURE,))
        self.assertEqual(capabilities.supported_market_categories, ("fixture",))
        self.assertTrue(capabilities.supports_historical)
        self.assertTrue(capabilities.public_data_only)
        self.assertIsNone(capabilities.rate_limit_notes)
        self.assertFalse(hasattr(capabilities, "market_snapshot"))

    def test_capabilities_can_describe_bybit_public_rest_linear_support(self) -> None:
        capabilities = AdapterCapabilities(
            adapter_name="bybit",
            supported_transports=(LiveDataTransport.REST,),
            supported_timeframes=(
                "1",
                "3",
                "5",
                "15",
                "30",
                "60",
                "120",
                "240",
                "360",
                "720",
                "D",
                "W",
                "M",
            ),
            supported_market_categories=("linear",),
            supports_historical=True,
            supports_websocket=False,
            supports_optional_metrics=False,
            public_data_only=True,
            rate_limit_notes="Public REST only; no local rate limiter in v0.3.",
        )

        self.assertEqual(capabilities.adapter_name, "bybit")
        self.assertEqual(capabilities.supported_transports, (LiveDataTransport.REST,))
        self.assertEqual(capabilities.supported_market_categories, ("linear",))
        self.assertIn("1", capabilities.supported_timeframes)
        self.assertIn("D", capabilities.supported_timeframes)
        self.assertTrue(capabilities.supports_historical)
        self.assertFalse(capabilities.supports_websocket)
        self.assertFalse(capabilities.supports_optional_metrics)
        self.assertTrue(capabilities.public_data_only)
        self.assertIn("Public REST only", capabilities.rate_limit_notes)

    def test_adapter_error_and_result_are_typed_and_serializable(self) -> None:
        error = AdapterError(
            error_type=AdapterErrorType.TIMEOUT,
            message="adapter timed out",
            retryable=True,
        )
        result = AdapterResult(success=False, error=error)

        self.assertEqual(result.error.error_type, AdapterErrorType.TIMEOUT)
        self.assertTrue(result.error.retryable)
        self.assertEqual(result.to_dict()["error"]["error_type"], "timeout")

    def test_adapter_result_success_cannot_include_error(self) -> None:
        error = AdapterError(
            error_type=AdapterErrorType.UNKNOWN_ERROR,
            message="unexpected",
        )

        with self.assertRaisesRegex(ValueError, "cannot include error"):
            AdapterResult(success=True, raw_payload={}, error=error)

    def test_adapter_framework_does_not_import_runtime_or_unapproved_networking(
        self,
    ) -> None:
        violations: list[str] = []

        for path in ADAPTER_SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for imported_module in _imports_from(tree):
                if _is_forbidden(imported_module):
                    violations.append(f"{path.relative_to(ROOT)} imports {imported_module}")

        self.assertEqual(violations, [])

    def test_no_unapproved_exchange_adapter_is_implemented_yet(self) -> None:
        adapter_files = {
            path.name
            for path in ADAPTER_SRC.rglob("*.py")
            if path.name not in {"__init__.py", "base_adapter.py"}
        }

        self.assertNotIn("binance_adapter.py", adapter_files)
        self.assertNotIn("mexc_adapter.py", adapter_files)


def _normalized_input(symbol: str, timeframe: str) -> NormalizedMarketDataInput:
    return NormalizedMarketDataInput(
        source_event_id="adapter-source-evt-1",
        symbol=symbol,
        exchange="dummy",
        timeframe=timeframe,
        source_timestamp=NOW,
        receive_timestamp=NOW,
        price=100.0,
        ohlcv=(
            {
                "timestamp": NOW.isoformat(),
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 10.0,
            },
        ),
        volume=10.0,
        data_source="dummy_adapter",
        quality_status=LiveDataQualityStatus.GOOD,
        source_metadata=SourceMetadata(
            exchange="dummy",
            adapter_name="dummy",
            adapter_version="0.1",
            source_timestamp=NOW,
            receive_timestamp=NOW,
            transport=LiveDataTransport.FIXTURE,
            source_symbol=symbol,
            normalized_symbol=symbol,
            source_timeframe=timeframe,
            normalized_timeframe=timeframe,
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


def _is_forbidden(module_name: str) -> bool:
    forbidden_exact = {
        "pumpagent.runtime",
        "pumpagent.runtime.domain",
        "pumpagent.runtime.orchestrator",
        "pumpagent.runtime.modules",
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        "sqlite3",
        "sqlalchemy",
    }
    forbidden_prefixes = tuple(f"{item}." for item in forbidden_exact)
    return module_name in forbidden_exact or module_name.startswith(forbidden_prefixes)


if __name__ == "__main__":
    unittest.main()
