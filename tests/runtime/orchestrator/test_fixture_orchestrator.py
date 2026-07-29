from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest import mock

from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.orchestrator import (
    FixtureRuntimeStage,
    RuntimeOrchestrator,
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
        completed = RuntimeOrchestrator(
            hypothesis_id_generator=lambda: IDENTITY["hypothesis_id"]
        ).process_market_update(
            run_fixture_runtime_cycle(**BASE).market_snapshot,
            episode_id=IDENTITY["episode_id"],
            runtime_event_id=BASE["event_id"],
        )
        with mock.patch(
            "pumpagent.runtime.orchestrator.fixture_orchestrator."
            "RuntimeOrchestrator.process_market_update",
            autospec=True,
        ) as process:
            process.return_value = completed
            with self.assertWarns(DeprecationWarning):
                actual = run_fixture_runtime_cycle(
                    **BASE, **IDENTITY, target_stage=FixtureRuntimeStage.STRUCTURE
                )

        self.assertIs(actual.runtime_status, RuntimeStatus.CREATED)
        self.assertIsNotNone(actual.structural_evidence)
        self.assertIsNone(actual.market_efficiency_evidence)
        process.assert_called_once()

    def test_legacy_structure_flag_returns_partial_compatibility_projection(self) -> None:
        with self.assertWarns(DeprecationWarning):
            event = run_fixture_runtime_cycle(
                **BASE, **IDENTITY, run_structure=True
            )

        self.assertIs(event.runtime_status, RuntimeStatus.CREATED)
        self.assertEqual(event.event_id, BASE["event_id"])
        self.assertEqual(event.schema_version, "1.0")
        self.assertEqual(event.cycle_timestamp, BASE["cycle_timestamp"])
        self.assertIsNotNone(event.observation_package)
        self.assertIsNotNone(event.structural_evidence)
        self.assertIsNone(event.market_efficiency_evidence)
        self.assertIsNone(event.decision_assessment)

    def test_analytical_fixture_requires_episode_and_hypothesis_identity(self) -> None:
        with self.assertWarns(DeprecationWarning), self.assertRaisesRegex(
            ValueError, "episode_id"
        ):
            run_fixture_runtime_cycle(**BASE, run_hypothesis=True)
        with self.assertWarns(DeprecationWarning), self.assertRaisesRegex(
            ValueError, "hypothesis_id"
        ):
            run_fixture_runtime_cycle(
                **BASE, episode_id="episode-fixture-1", run_hypothesis=True
            )

    def test_partial_fixture_stages_preserve_baseline_contract(self) -> None:
        cases = (
            ({"run_perception": True}, "observation_package", "structural_evidence"),
            ({"run_structure": True}, "structural_evidence", "market_efficiency_evidence"),
            (
                {"run_market_efficiency": True},
                "market_efficiency_evidence",
                "hypothesis_package",
            ),
            ({"run_hypothesis": True}, "hypothesis_package", "agent_state"),
            ({"run_agent_state": True}, "agent_state", "scenario_probability"),
        )
        for flags, present, absent in cases:
            with self.subTest(stage=present), self.assertWarns(DeprecationWarning):
                event = run_fixture_runtime_cycle(
                    **BASE,
                    **(
                        IDENTITY
                        if present in {"hypothesis_package", "agent_state"}
                        else {}
                    ),
                    **flags,
                )
            self.assertIsNotNone(getattr(event, present))
            self.assertIsNone(getattr(event, absent))
            self.assertEqual(event.event_id, BASE["event_id"])

    def test_legacy_perception_target_remains_observation_only(self) -> None:
        with self.assertWarns(DeprecationWarning):
            event = run_fixture_runtime_cycle(**BASE, target_stage="perception")

        self.assertIsNotNone(event.observation_package)
        self.assertIsNone(event.structural_evidence)

    def test_retired_post_agent_state_stages_remain_rejected(self) -> None:
        cases = (
            {"run_scenario_probability": True},
            {"run_confidence": True},
            {"run_decision_alert": True},
        )
        for flags in cases:
            with self.subTest(flags=flags), self.assertRaisesRegex(
                ValueError, "after Agent State are retired"
            ):
                run_fixture_runtime_cycle(**BASE, **IDENTITY, **flags)

    def test_learning_memory_remains_outside_runtime_orchestration(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            run_fixture_runtime_cycle(**BASE, run_learning_memory=True)


if __name__ == "__main__":
    unittest.main()
