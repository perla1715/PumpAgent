from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
VALID_FIXTURE = ROOT / "tests" / "fixtures" / "live_data" / "btcusdt_1m_normalized.json"
INVALID_FIXTURE = ROOT / "tests" / "fixtures" / "live_data" / "invalid_normalized.json"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.live_data.domain import LiveDataErrorType, LiveDataMode
from pumpagent.live_data.flows import load_market_snapshot_from_fixture_flow
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus


class FixtureMarketSnapshotFlowTests(unittest.TestCase):
    def test_valid_fixture_completes_full_flow_and_produces_market_snapshot(self) -> None:
        result = load_market_snapshot_from_fixture_flow(VALID_FIXTURE)

        self.assertTrue(result.success)
        self.assertIsInstance(result.market_snapshot, MarketSnapshot)
        self.assertIsNone(result.error)
        self.assertEqual(result.market_snapshot.event_id, "live-source-evt-1")
        self.assertEqual(result.market_snapshot.data_quality_status, DataQualityStatus.VALID)

    def test_good_quality_succeeds(self) -> None:
        fixture = self._fixture_with(quality_status="good")

        result = load_market_snapshot_from_fixture_flow(fixture)

        self.assertTrue(result.success)
        self.assertEqual(result.market_snapshot.data_quality_status, DataQualityStatus.VALID)

    def test_delayed_quality_succeeds(self) -> None:
        fixture = self._fixture_with(quality_status="delayed")

        result = load_market_snapshot_from_fixture_flow(fixture)

        self.assertTrue(result.success)
        self.assertEqual(
            result.market_snapshot.data_quality_status,
            DataQualityStatus.DELAYED,
        )

    def test_partial_quality_succeeds_when_required_fields_are_valid(self) -> None:
        fixture = self._fixture_with(
            quality_status="partial",
            missing_fields=["funding_rate", "open_interest"],
        )

        result = load_market_snapshot_from_fixture_flow(fixture)

        self.assertTrue(result.success)
        self.assertEqual(
            result.market_snapshot.data_quality_status,
            DataQualityStatus.MISSING,
        )

    def test_corrupted_quality_is_blocked_before_market_snapshot(self) -> None:
        fixture = self._fixture_with(quality_status="corrupted")

        with mock.patch(
            "pumpagent.live_data.flows.fixture_market_snapshot_flow."
            "build_market_snapshot_from_live_data"
        ) as bridge_mock:
            result = load_market_snapshot_from_fixture_flow(fixture)

        self.assertFalse(result.success)
        self.assertIsNone(result.market_snapshot)
        self.assertEqual(result.error.error_type, LiveDataErrorType.QUALITY_BLOCKED)
        self.assertIn("corrupted_data_blocked", result.error.validation_errors)
        bridge_mock.assert_not_called()

    def test_unknown_quality_is_blocked_in_live_mode(self) -> None:
        fixture = self._fixture_with(quality_status="unknown")

        with mock.patch(
            "pumpagent.live_data.flows.fixture_market_snapshot_flow."
            "build_market_snapshot_from_live_data"
        ) as bridge_mock:
            result = load_market_snapshot_from_fixture_flow(
                fixture,
                mode=LiveDataMode.LIVE,
            )

        self.assertFalse(result.success)
        self.assertIsNone(result.market_snapshot)
        self.assertEqual(result.error.error_type, LiveDataErrorType.QUALITY_BLOCKED)
        self.assertIn("unknown_quality_blocked", result.error.validation_errors)
        bridge_mock.assert_not_called()

    def test_unknown_quality_non_live_requires_explicit_opt_in(self) -> None:
        fixture = self._fixture_with(quality_status="unknown")

        for mode in (
            LiveDataMode.REPLAY,
            LiveDataMode.SIMULATION,
            LiveDataMode.TESTING,
        ):
            with self.subTest(mode=mode):
                blocked = load_market_snapshot_from_fixture_flow(
                    fixture,
                    mode=mode,
                    allow_unknown_non_live=False,
                )
                allowed = load_market_snapshot_from_fixture_flow(
                    fixture,
                    mode=mode,
                    allow_unknown_non_live=True,
                )

                self.assertFalse(blocked.success)
                self.assertEqual(
                    blocked.error.error_type,
                    LiveDataErrorType.QUALITY_BLOCKED,
                )
                self.assertTrue(allowed.success)
                self.assertEqual(
                    allowed.market_snapshot.data_quality_status,
                    DataQualityStatus.MISSING,
                )

    def test_validation_failure_blocks_flow(self) -> None:
        fixture = self._fixture_with(symbol="")

        with mock.patch(
            "pumpagent.live_data.flows.fixture_market_snapshot_flow."
            "build_market_snapshot_from_live_data"
        ) as bridge_mock:
            result = load_market_snapshot_from_fixture_flow(fixture)

        self.assertFalse(result.success)
        self.assertIsNone(result.market_snapshot)
        self.assertEqual(result.error.error_type, LiveDataErrorType.VALIDATION_FAILED)
        self.assertIn("symbol must be a non-empty string.", result.error.validation_errors)
        bridge_mock.assert_not_called()

    def test_fixture_loading_error_propagates_as_live_data_error(self) -> None:
        result = load_market_snapshot_from_fixture_flow(INVALID_FIXTURE)

        self.assertFalse(result.success)
        self.assertIsNone(result.market_snapshot)
        self.assertEqual(result.error.error_type, LiveDataErrorType.MALFORMED_PAYLOAD)
        self.assertIn("Missing fixture field", result.error.message)

    def test_metadata_survives_entire_flow(self) -> None:
        result = load_market_snapshot_from_fixture_flow(VALID_FIXTURE)
        metrics = result.market_snapshot.optional_market_metrics

        self.assertEqual(metrics["open_interest"], 1000.0)
        self.assertEqual(metrics["quality_reasons"], ("fixture_quality_declared_good",))
        self.assertEqual(metrics["missing_fields"], ("funding_rate",))
        self.assertEqual(metrics["validation_warnings"], ("optional_metric_missing",))
        self.assertEqual(metrics["adapter_name"], "fixture_source")
        self.assertEqual(metrics["adapter_version"], "0.1")
        self.assertEqual(metrics["transport"], "fixture")
        self.assertEqual(metrics["correlation_id"], "fixture-corr-1")
        self.assertEqual(metrics["normalizer_version"], "0.1")
        self.assertEqual(metrics["validator_version"], "0.1")
        self.assertEqual(
            metrics["source_timestamp"].isoformat(),
            "2026-07-01T12:00:00+00:00",
        )
        self.assertEqual(
            metrics["receive_timestamp"].isoformat(),
            "2026-07-01T12:00:01+00:00",
        )
        self.assertEqual(result.market_snapshot.latency_ms, 1000)
        self.assertEqual(result.market_snapshot.raw_payload_reference, str(VALID_FIXTURE))

    def test_no_runtime_event_or_reasoning_modules_are_invoked(self) -> None:
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
        ) as learning_mock, mock.patch.object(
            socket, "socket"
        ) as socket_mock, mock.patch.object(
            urllib.request, "urlopen"
        ) as urlopen_mock:
            result = load_market_snapshot_from_fixture_flow(VALID_FIXTURE)

        self.assertTrue(result.success)
        self.assertFalse(hasattr(result, "runtime_event"))
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
        socket_mock.assert_not_called()
        urlopen_mock.assert_not_called()

    def _fixture_with(self, **updates: object) -> Path:
        payload = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
        payload.update(updates)
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "fixture.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
