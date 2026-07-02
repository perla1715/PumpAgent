from __future__ import annotations

import socket
from pathlib import Path
import sys
import unittest
from unittest import mock
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
VALID_FIXTURE = ROOT / "tests" / "fixtures" / "live_data" / "btcusdt_1m_normalized.json"
INVALID_FIXTURE = ROOT / "tests" / "fixtures" / "live_data" / "invalid_normalized.json"
MALFORMED_FIXTURE = (
    ROOT / "tests" / "fixtures" / "live_data" / "malformed_normalized.json"
)
NON_OBJECT_FIXTURE = (
    ROOT / "tests" / "fixtures" / "live_data" / "non_object_normalized.json"
)
MISSING_FIXTURE = ROOT / "tests" / "fixtures" / "live_data" / "missing.json"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.live_data.domain import (
    LiveDataErrorType,
    LiveDataQualityStatus,
    LiveDataTransport,
    NormalizedMarketDataInput,
)
from pumpagent.live_data.sources import FixtureLiveDataSource, LiveDataSource


class FixtureLiveDataSourceTests(unittest.TestCase):
    def test_fixture_source_satisfies_live_data_source_protocol(self) -> None:
        self.assertIsInstance(FixtureLiveDataSource(), LiveDataSource)

    def test_valid_fixture_loads_successfully(self) -> None:
        result = FixtureLiveDataSource().load(VALID_FIXTURE)

        self.assertTrue(result.success)
        self.assertIsInstance(result.data, NormalizedMarketDataInput)
        self.assertIsNone(result.error)
        self.assertEqual(result.data.source_event_id, "live-source-evt-1")
        self.assertEqual(result.data.quality_status, LiveDataQualityStatus.GOOD)

    def test_invalid_fixture_returns_live_data_error(self) -> None:
        result = FixtureLiveDataSource().load(INVALID_FIXTURE)

        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertFalse(result.error.retryable)
        self.assertIn("Missing fixture field: source_timestamp", result.error.message)

    def test_malformed_json_fixture_returns_live_data_error(self) -> None:
        result = FixtureLiveDataSource().load(MALFORMED_FIXTURE)

        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)

    def test_non_object_fixture_root_returns_live_data_error(self) -> None:
        result = FixtureLiveDataSource().load(NON_OBJECT_FIXTURE)

        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn("Fixture root must be a JSON object.", result.error.message)

    def test_missing_fixture_returns_structured_error(self) -> None:
        result = FixtureLiveDataSource().load(MISSING_FIXTURE)

        self.assertFalse(result.success)
        self.assertIsNone(result.data)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertEqual(result.error.raw_payload_reference, str(MISSING_FIXTURE))

    def test_source_metadata_is_preserved(self) -> None:
        result = FixtureLiveDataSource().load(VALID_FIXTURE)
        metadata = result.data.source_metadata

        self.assertEqual(metadata.exchange, "binance")
        self.assertEqual(metadata.adapter_name, "fixture_source")
        self.assertEqual(metadata.adapter_version, "0.1")
        self.assertEqual(metadata.transport, LiveDataTransport.FIXTURE)
        self.assertEqual(metadata.correlation_id, "fixture-corr-1")
        self.assertEqual(metadata.request_id, "fixture-request-1")
        self.assertEqual(metadata.sequence_id, "fixture-seq-1")

    def test_optional_metrics_are_preserved_exactly(self) -> None:
        result = FixtureLiveDataSource().load(VALID_FIXTURE)
        metrics = result.data.optional_market_metrics

        self.assertEqual(metrics["open_interest"], 1000.0)
        self.assertEqual(metrics["normalizer_version"], "0.1")
        self.assertEqual(metrics["validator_version"], "0.1")
        self.assertEqual(result.data.quality_reasons, ("fixture_quality_declared_good",))
        self.assertEqual(result.data.missing_fields, ("funding_rate",))
        self.assertEqual(result.data.validation_warnings, ("optional_metric_missing",))

    def test_source_does_not_create_runtime_objects_or_bridge_outputs(self) -> None:
        result = FixtureLiveDataSource().load(VALID_FIXTURE)

        self.assertTrue(result.success)
        self.assertFalse(hasattr(result, "market_snapshot"))
        self.assertFalse(hasattr(result, "runtime_event"))
        self.assertFalse(hasattr(result.data, "data_quality_status"))

    def test_source_does_not_invoke_bridge_runtime_modules_or_networking(self) -> None:
        with mock.patch(
            "pumpagent.live_data.bridge.build_market_snapshot_from_live_data"
        ) as bridge_mock, mock.patch(
            "pumpagent.runtime.modules.market_data.load_market_snapshot_from_fixture"
        ) as runtime_market_data_mock, mock.patch(
            "pumpagent.runtime.modules.perception.build_observation_package"
        ) as runtime_perception_mock, mock.patch.object(
            socket, "socket"
        ) as socket_mock, mock.patch.object(
            urllib.request, "urlopen"
        ) as urlopen_mock:
            result = FixtureLiveDataSource().load(VALID_FIXTURE)

        self.assertTrue(result.success)
        bridge_mock.assert_not_called()
        runtime_market_data_mock.assert_not_called()
        runtime_perception_mock.assert_not_called()
        socket_mock.assert_not_called()
        urlopen_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
