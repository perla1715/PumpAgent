from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
BRIDGE = SRC / "pumpagent" / "live_data" / "bridge" / "runtime_market_data_bridge.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.live_data.bridge import (
    RuntimeMarketDataBridgeResult,
    build_market_snapshot_from_live_data,
)
from pumpagent.live_data.domain import (
    LiveDataErrorType,
    LiveDataMode,
    LiveDataQualityStatus,
    LiveDataTransport,
    LiveDataResult,
    NormalizedMarketDataInput,
    SourceMetadata,
)
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def make_source_metadata() -> SourceMetadata:
    return SourceMetadata(
        exchange="binance",
        adapter_name="fixture_adapter",
        adapter_version="0.1",
        source_timestamp=NOW,
        receive_timestamp=NOW,
        latency_ms=14.7,
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
        optional_market_metrics={
            "open_interest": 1000.0,
            "normalizer_version": "0.1",
            "validator_version": "0.1",
        },
        quality_reasons=("complete_fixture_data",),
        missing_fields=(),
        validation_warnings=(),
        raw_payload_reference="fixture://source-evt-1",
    )


class RuntimeMarketDataBridgeTests(unittest.TestCase):
    def test_valid_good_input_creates_market_snapshot(self) -> None:
        result = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.GOOD),
            mode=LiveDataMode.LIVE,
        )

        self.assertIsInstance(result, RuntimeMarketDataBridgeResult)
        self.assertNotIsInstance(result, LiveDataResult)
        self.assertTrue(result.success)
        self.assertIsInstance(result.market_snapshot, MarketSnapshot)
        self.assertIsNone(result.error)
        self.assertEqual(result.market_snapshot.event_id, "source-evt-1")
        self.assertEqual(
            result.market_snapshot.data_quality_status,
            DataQualityStatus.VALID,
        )

    def test_delayed_maps_to_runtime_delayed(self) -> None:
        result = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.DELAYED),
            mode=LiveDataMode.LIVE,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.market_snapshot.data_quality_status,
            DataQualityStatus.DELAYED,
        )

    def test_partial_maps_to_runtime_missing_when_required_fields_valid(self) -> None:
        data = replace(
            make_input(LiveDataQualityStatus.PARTIAL),
            missing_fields=("open_interest",),
        )

        result = build_market_snapshot_from_live_data(data, mode=LiveDataMode.LIVE)

        self.assertTrue(result.success)
        self.assertEqual(
            result.market_snapshot.data_quality_status,
            DataQualityStatus.MISSING,
        )
        self.assertEqual(result.market_snapshot.missing_fields, ("open_interest",))

    def test_corrupted_is_blocked(self) -> None:
        result = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.CORRUPTED),
            mode=LiveDataMode.LIVE,
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.market_snapshot)
        self.assertEqual(result.error.error_type, LiveDataErrorType.QUALITY_BLOCKED)
        self.assertEqual(result.error.message, "corrupted_data_blocked")

    def test_unknown_is_blocked_in_live_mode(self) -> None:
        result = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.LIVE,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error.error_type, LiveDataErrorType.QUALITY_BLOCKED)
        self.assertEqual(result.error.message, "unknown_quality_blocked")

    def test_unknown_requires_explicit_allowance_in_replay(self) -> None:
        blocked = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.REPLAY,
        )
        allowed = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.REPLAY,
            allow_unknown_non_live=True,
        )

        self.assertFalse(blocked.success)
        self.assertTrue(allowed.success)
        self.assertEqual(
            allowed.market_snapshot.data_quality_status,
            DataQualityStatus.MISSING,
        )

    def test_unknown_can_be_allowed_in_simulation_and_testing(self) -> None:
        simulation = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.SIMULATION,
            allow_unknown_non_live=True,
        )
        testing = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.TESTING,
            allow_unknown_non_live=True,
        )

        self.assertTrue(simulation.success)
        self.assertTrue(testing.success)

    def test_invalid_validation_result_blocks_bridge(self) -> None:
        data = replace(make_input(), symbol="")

        result = build_market_snapshot_from_live_data(data, mode=LiveDataMode.LIVE)

        self.assertFalse(result.success)
        self.assertIsNone(result.market_snapshot)
        self.assertEqual(result.error.error_type, LiveDataErrorType.VALIDATION_FAILED)
        self.assertIn("symbol must be a non-empty string.", result.error.validation_errors)

    def test_missing_source_metadata_returns_structured_validation_error(
        self,
    ) -> None:
        data = replace(make_input(), source_metadata=None)

        result = build_market_snapshot_from_live_data(data, mode=LiveDataMode.LIVE)

        self.assertFalse(result.success)
        self.assertIsNone(result.market_snapshot)
        self.assertEqual(result.error.error_type, LiveDataErrorType.VALIDATION_FAILED)
        self.assertIn("source_metadata is required.", result.error.validation_errors)
        self.assertIsNone(result.error.correlation_id)

    def test_quality_block_reason_is_in_validation_errors(self) -> None:
        result = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.CORRUPTED),
            mode=LiveDataMode.LIVE,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error.message, "corrupted_data_blocked")
        self.assertIn("corrupted_data_blocked", result.error.validation_errors)

    def test_market_snapshot_preserves_core_market_fields(self) -> None:
        result = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.GOOD),
            mode=LiveDataMode.LIVE,
        )
        snapshot = result.market_snapshot

        self.assertEqual(snapshot.event_id, "source-evt-1")
        self.assertEqual(snapshot.timestamp, NOW)
        self.assertEqual(snapshot.symbol, "BTCUSDT")
        self.assertEqual(snapshot.exchange, "binance")
        self.assertEqual(snapshot.timeframe, "1m")
        self.assertEqual(snapshot.price, 100.0)
        self.assertEqual(snapshot.volume, 10.0)
        self.assertEqual(snapshot.data_source, "fixture")
        self.assertEqual(snapshot.schema_version, "1.0")
        self.assertEqual(
            snapshot.ohlcv,
            (
                {
                    "timestamp": "2026-07-01T12:00:00+00:00",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 10.0,
                },
            ),
        )

    def test_metadata_is_preserved_in_market_snapshot(self) -> None:
        result = build_market_snapshot_from_live_data(
            make_input(LiveDataQualityStatus.GOOD),
            mode=LiveDataMode.LIVE,
        )
        snapshot = result.market_snapshot
        metadata = snapshot.optional_market_metrics

        self.assertEqual(metadata["open_interest"], 1000.0)
        self.assertEqual(metadata["quality_reasons"], ("complete_fixture_data",))
        self.assertEqual(metadata["missing_fields"], ())
        self.assertEqual(metadata["validation_warnings"], ())
        self.assertEqual(metadata["source_timestamp"], NOW)
        self.assertEqual(metadata["receive_timestamp"], NOW)
        self.assertEqual(metadata["latency_ms"], 14.7)
        self.assertEqual(metadata["adapter_name"], "fixture_adapter")
        self.assertEqual(metadata["adapter_version"], "0.1")
        self.assertEqual(metadata["normalizer_version"], "0.1")
        self.assertEqual(metadata["validator_version"], "0.1")
        self.assertEqual(metadata["transport"], "fixture")
        self.assertEqual(metadata["correlation_id"], "corr-1")
        self.assertEqual(metadata["source_metadata"]["exchange"], "binance")
        self.assertEqual(metadata["source_metadata"]["adapter_name"], "fixture_adapter")
        self.assertEqual(metadata["source_metadata"]["adapter_version"], "0.1")
        self.assertEqual(metadata["source_metadata"]["transport"], "fixture")
        self.assertEqual(metadata["source_metadata"]["source_symbol"], "BTCUSDT")
        self.assertEqual(metadata["source_metadata"]["normalized_symbol"], "BTCUSDT")
        self.assertEqual(metadata["source_metadata"]["source_timeframe"], "1m")
        self.assertEqual(metadata["source_metadata"]["normalized_timeframe"], "1m")
        self.assertEqual(snapshot.raw_payload_reference, "fixture://source-evt-1")
        self.assertEqual(snapshot.latency_ms, 15)

    def test_missing_fields_are_preserved_in_market_snapshot(self) -> None:
        data = replace(
            make_input(LiveDataQualityStatus.PARTIAL),
            missing_fields=("open_interest",),
        )

        result = build_market_snapshot_from_live_data(data, mode=LiveDataMode.LIVE)

        self.assertTrue(result.success)
        self.assertEqual(result.market_snapshot.missing_fields, ("open_interest",))
        self.assertEqual(
            result.market_snapshot.optional_market_metrics["missing_fields"],
            ("open_interest",),
        )

    def test_bridge_does_not_create_runtime_event_or_run_runtime_modules(self) -> None:
        with mock.patch(
            "pumpagent.runtime.orchestrator.run_fixture_runtime_cycle"
        ) as orchestrator_mock, mock.patch(
            "pumpagent.runtime.modules.perception.build_observation_package"
        ) as perception_mock, mock.patch(
            "pumpagent.runtime.modules.structure.build_structural_evidence"
        ) as structure_mock, mock.patch(
            "pumpagent.runtime.modules.market_efficiency.build_market_efficiency_evidence"
        ) as efficiency_mock, mock.patch(
            "pumpagent.runtime.modules.hypothesis.build_hypothesis_package"
        ) as hypothesis_mock, mock.patch(
            "pumpagent.runtime.modules.agent_state.build_agent_state"
        ) as agent_state_mock, mock.patch(
            "pumpagent.runtime.modules.scenario_probability.build_scenario_probability"
        ) as scenario_mock, mock.patch(
            "pumpagent.runtime.modules.confidence.build_confidence_assessment"
        ) as confidence_mock, mock.patch(
            "pumpagent.runtime.modules.decision_alert.build_decision_alert"
        ) as decision_mock, mock.patch(
            "pumpagent.runtime.modules.learning_memory.build_learning_metadata"
        ) as learning_mock:
            result = build_market_snapshot_from_live_data(
                make_input(LiveDataQualityStatus.GOOD),
                mode=LiveDataMode.LIVE,
            )

        self.assertTrue(result.success)
        self.assertIsInstance(result.market_snapshot, MarketSnapshot)
        self.assertFalse(hasattr(result, "runtime_event"))
        self.assertFalse(hasattr(result, "observation_package"))
        self.assertFalse(hasattr(result, "decision_alert"))
        orchestrator_mock.assert_not_called()
        perception_mock.assert_not_called()
        structure_mock.assert_not_called()
        efficiency_mock.assert_not_called()
        hypothesis_mock.assert_not_called()
        agent_state_mock.assert_not_called()
        scenario_mock.assert_not_called()
        confidence_mock.assert_not_called()
        decision_mock.assert_not_called()
        learning_mock.assert_not_called()

    def test_bridge_does_not_import_exchange_adapters(self) -> None:
        imports = _imports_from(ast.parse(BRIDGE.read_text(encoding="utf-8")))

        self.assertFalse(
            any(
                imported == "pumpagent.live_data.adapters"
                or imported.startswith("pumpagent.live_data.adapters.")
                for imported in imports
            )
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
