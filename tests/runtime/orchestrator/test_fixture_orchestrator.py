from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import HypothesisLifecycleStatus, RuntimeEvent
from pumpagent.runtime.modules.market_data import FixtureLoadError
from pumpagent.runtime.orchestrator import (
    FixtureRuntimeStage,
    run_fixture_market_data_cycle,
    run_fixture_runtime_cycle,
)
from pumpagent.runtime.orchestrator.runtime_loop import AgentCycleResult, RuntimeOrchestrator


class FixtureRuntimeOrchestratorTests(unittest.TestCase):
    def run_cycle(self) -> RuntimeEvent:
        return run_fixture_runtime_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
        )

    def test_orchestrator_creates_runtime_event(self) -> None:
        event = self.run_cycle()

        self.assertIsInstance(event, RuntimeEvent)
        self.assertEqual(event.event_id, "runtime-evt-1")
        self.assertEqual(event.symbol, "BTCUSDT")
        self.assertEqual(event.exchange, "binance")
        self.assertEqual(event.timeframe, "1m")

    def test_legacy_market_data_entry_point_remains_compatible(self) -> None:
        event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
        )

        self.assertIsInstance(event, RuntimeEvent)
        self.assertIsNotNone(event.market_snapshot)

    def test_orchestrator_adds_market_snapshot(self) -> None:
        event = self.run_cycle()

        self.assertIsNotNone(event.market_snapshot)
        self.assertEqual(event.market_snapshot.symbol, "BTCUSDT")
        self.assertEqual(event.market_snapshot.exchange, "binance")
        self.assertEqual(event.market_snapshot.timeframe, "1m")

    def test_orchestrator_populates_no_other_runtime_sections(self) -> None:
        event = self.run_cycle()

        self.assertIsNone(event.observation_package)
        self.assertIsNone(event.structural_evidence)
        self.assertIsNone(event.market_efficiency_evidence)
        self.assertIsNone(event.hypothesis_package)
        self.assertIsNone(event.agent_state)
        self.assertIsNone(event.scenario_probability)
        self.assertIsNone(event.confidence_assessment)
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)

    def test_identity_fields_are_consistent(self) -> None:
        event = self.run_cycle()

        self.assertEqual(event.symbol, event.market_snapshot.symbol)
        self.assertEqual(event.exchange, event.market_snapshot.exchange)
        self.assertEqual(event.timeframe, event.market_snapshot.timeframe)

    def test_orchestrator_uses_fixture_market_data_identity_validation(self) -> None:
        with self.assertRaises(FixtureLoadError):
            run_fixture_market_data_cycle(
                event_id="runtime-evt-1",
                cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                symbol="ETHUSDT",
                exchange="binance",
                timeframe="1m",
                fixture_path=FIXTURE,
            )

    def test_orchestrator_can_run_market_data_to_observation_package(self) -> None:
        market_data_only_event = self.run_cycle()
        original_snapshot = market_data_only_event.market_snapshot.to_dict()

        event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_perception=True,
        )

        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNotNone(event.observation_package)
        self.assertEqual(event.market_snapshot.to_dict(), original_snapshot)
        self.assertIsNone(event.structural_evidence)
        self.assertIsNone(event.market_efficiency_evidence)
        self.assertIsNone(event.hypothesis_package)
        self.assertIsNone(event.agent_state)
        self.assertIsNone(event.scenario_probability)
        self.assertIsNone(event.confidence_assessment)
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)

    def test_legacy_perception_target_selects_observation_only(self) -> None:
        event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            target_stage="perception",
        )

        self.assertIsNotNone(event.observation_package)
        self.assertIsNone(event.structural_evidence)
        self.assertIsNone(event.market_efficiency_evidence)

    def test_orchestrator_can_run_market_data_to_observation_to_structure(
        self,
    ) -> None:
        observation_event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_perception=True,
        )
        original_snapshot = observation_event.market_snapshot.to_dict()
        original_observation = observation_event.observation_package.to_dict()

        event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_structure=True,
        )

        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNotNone(event.observation_package)
        self.assertIsNotNone(event.structural_evidence)
        self.assertIsNone(event.market_efficiency_evidence)
        self.assertEqual(event.market_snapshot.to_dict(), original_snapshot)
        self.assertEqual(event.observation_package.to_dict(), original_observation)
        self.assertEqual(event.structural_evidence.structural_bias, "not_assessed")
        self.assertIsNone(event.hypothesis_package)
        self.assertIsNone(event.agent_state)
        self.assertIsNone(event.scenario_probability)
        self.assertIsNone(event.confidence_assessment)
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)

    def test_orchestrator_can_run_through_market_efficiency(self) -> None:
        structure_event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_structure=True,
        )
        original_snapshot = structure_event.market_snapshot.to_dict()
        original_structure = structure_event.structural_evidence.to_dict()

        event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_market_efficiency=True,
        )

        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNotNone(event.observation_package)
        self.assertIsNotNone(event.structural_evidence)
        self.assertIsNotNone(event.market_efficiency_evidence)
        self.assertEqual(event.market_snapshot.to_dict(), original_snapshot)
        self.assertEqual(event.structural_evidence.to_dict(), original_structure)
        self.assertEqual(
            event.market_efficiency_evidence.participation_direction,
            "not_assessed",
        )
        self.assertEqual(
            event.market_efficiency_evidence.efficiency_status,
            "not_assessed",
        )
        self.assertIsNone(event.hypothesis_package)
        self.assertIsNone(event.agent_state)
        self.assertIsNone(event.scenario_probability)
        self.assertIsNone(event.confidence_assessment)
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)

    def _run_canonical_fixture_runtime(self, **kwargs: object):
        return run_fixture_runtime_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            episode_id="fixture-episode-1",
            runtime=RuntimeOrchestrator(
                hypothesis_id_generator=lambda: "fixture-canonical-hypothesis-1"
            ),
            **kwargs,
        )

    def test_hypothesis_and_later_fixture_stages_require_lifecycle_episode(self) -> None:
        with self.assertRaisesRegex(ValueError, "Lifecycle-owned episode_id"):
            run_fixture_runtime_cycle(
                event_id="runtime-evt-1",
                cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="1m",
                fixture_path=FIXTURE,
                run_hypothesis=True,
            )

    def test_hypothesis_fixture_stage_routes_to_canonical_runtime(self) -> None:
        result = self._run_canonical_fixture_runtime(run_hypothesis=True)

        self.assertIsInstance(result, AgentCycleResult)
        self.assertEqual(result.hypothesis.episode_id, "fixture-episode-1")
        self.assertEqual(result.hypothesis.hypothesis_id, "fixture-canonical-hypothesis-1")
        self.assertIs(result.hypothesis.lifecycle_status, HypothesisLifecycleStatus.CREATED)
        self.assertEqual(
            result.hypothesis.hypothesis_change_reason,
            "No previous canonical hypothesis was available in the Episode.",
        )
        self.assertIsNotNone(result.structure_result)
        self.assertIsNotNone(result.market_result)
        self.assertIsNotNone(result.agent_state)
        self.assertIsNotNone(result.scenario_probability)
        self.assertIsNotNone(result.confidence_assessment)
        self.assertIsNotNone(result.decision_assessment)

    def test_later_legacy_stage_targets_route_to_canonical_runtime(self) -> None:
        for stage in (
            FixtureRuntimeStage.AGENT_STATE,
            FixtureRuntimeStage.SCENARIO_PROBABILITY,
            FixtureRuntimeStage.CONFIDENCE,
            FixtureRuntimeStage.DECISION_ALERT,
        ):
            with self.subTest(stage=stage):
                result = self._run_canonical_fixture_runtime(target_stage=stage)
                self.assertIsInstance(result, AgentCycleResult)
                self.assertEqual(result.hypothesis.episode_id, "fixture-episode-1")
                self.assertIsNotNone(result.decision_assessment)

    def test_orchestrator_rejects_learning_memory_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "Learning Memory"):
            run_fixture_runtime_cycle(
                event_id="runtime-evt-1",
                cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="1m",
                fixture_path=FIXTURE,
                run_learning_memory=True,
            )

    def test_orchestrator_stage_enum_excludes_learning_memory(self) -> None:
        self.assertNotIn(
            "learning_memory",
            tuple(stage.value for stage in FixtureRuntimeStage),
        )


if __name__ == "__main__":
    unittest.main()
