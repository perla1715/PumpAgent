from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest import mock

from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.orchestrator import (
    FixtureRuntimeStage,
    run_fixture_market_data_cycle,
    run_fixture_runtime_cycle,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
BASE = {
    "event_id": "runtime-evt-1",
    "cycle_timestamp": datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
    "symbol": "BTCUSDT",
    "exchange": "binance",
    "timeframe": "1m",
    "fixture_path": FIXTURE,
}
IDENTITY = {
    "episode_id": "episode-fixture-1",
    "hypothesis_id": "hypothesis-fixture-1",
}


class FixtureRuntimeOrchestratorTests(unittest.TestCase):
    def test_market_data_only_returns_created_runtime_event(self) -> None:
        event = run_fixture_runtime_cycle(**BASE)

        self.assertIsInstance(event, RuntimeEvent)
        self.assertIs(event.runtime_status, RuntimeStatus.CREATED)
        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNone(event.observation_package)

    def test_compatibility_alias_remains_market_data_only(self) -> None:
        event = run_fixture_market_data_cycle(**BASE)

        self.assertIs(event.runtime_status, RuntimeStatus.CREATED)
        self.assertIsNotNone(event.market_snapshot)

    def test_analytical_fixture_delegates_to_primary_orchestrator(self) -> None:
        with mock.patch(
            "pumpagent.runtime.orchestrator.fixture_orchestrator."
            "RuntimeOrchestrator.process_market_update",
            autospec=True,
        ) as process:
            expected = mock.sentinel.runtime_event
            process.return_value = expected
            with self.assertWarns(DeprecationWarning):
                actual = run_fixture_runtime_cycle(
                    **BASE, **IDENTITY, target_stage=FixtureRuntimeStage.STRUCTURE
                )

        self.assertIs(actual, expected)
        process.assert_called_once()

    def test_any_legacy_analytical_flag_runs_complete_canonical_cycle(self) -> None:
        with self.assertWarns(DeprecationWarning):
            event = run_fixture_runtime_cycle(
                **BASE, **IDENTITY, run_structure=True
            )

        self.assertIs(event.runtime_status, RuntimeStatus.COMPLETED)
        self.assertIsNotNone(event.decision_assessment)

    def test_analytical_fixture_requires_episode_and_hypothesis_identity(self) -> None:
        with self.assertWarns(DeprecationWarning), self.assertRaisesRegex(
            ValueError, "episode_id"
        ):
            run_fixture_runtime_cycle(**BASE, run_structure=True)
        with self.assertWarns(DeprecationWarning), self.assertRaisesRegex(
            ValueError, "hypothesis_id"
        ):
            run_fixture_runtime_cycle(
                **BASE, episode_id="episode-fixture-1", run_structure=True
            )

    def test_learning_memory_remains_outside_runtime_orchestration(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            run_fixture_runtime_cycle(**BASE, run_learning_memory=True)


if __name__ == "__main__":
    unittest.main()
