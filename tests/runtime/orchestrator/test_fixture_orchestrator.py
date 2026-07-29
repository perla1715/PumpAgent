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

from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.modules.market_data import FixtureLoadError
from pumpagent.runtime.orchestrator import (
    FixtureRuntimeStage,
    run_fixture_market_data_cycle,
    run_fixture_runtime_cycle,
)

FIXTURE_IDENTITY = {
    "episode_id": "episode-fixture-1",
    "hypothesis_id": "hypothesis-fixture-1",
}


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
            event.market_efficiency_evidence.to_dict(),
            run_fixture_market_data_cycle(
                event_id="runtime-evt-1",
                cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="1m",
                fixture_path=FIXTURE,
                run_market_efficiency=True,
            ).market_efficiency_evidence.to_dict(),
        )
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

    def test_orchestrator_can_run_through_hypothesis(self) -> None:
        efficiency_event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_market_efficiency=True,
        )
        original_snapshot = efficiency_event.market_snapshot.to_dict()
        original_structure = efficiency_event.structural_evidence.to_dict()
        original_efficiency = efficiency_event.market_efficiency_evidence.to_dict()

        event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_hypothesis=True,
            **FIXTURE_IDENTITY,
        )

        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNotNone(event.observation_package)
        self.assertIsNotNone(event.structural_evidence)
        self.assertIsNotNone(event.market_efficiency_evidence)
        self.assertIsNotNone(event.hypothesis_package)
        self.assertEqual(event.market_snapshot.to_dict(), original_snapshot)
        self.assertEqual(event.structural_evidence.to_dict(), original_structure)
        self.assertEqual(
            event.market_efficiency_evidence.to_dict(),
            original_efficiency,
        )
        self.assertEqual(event.hypothesis_package.event_id, event.event_id)
        self.assertEqual(
            event.hypothesis_package.hypothesis_label,
            "current_condition_explanation",
        )
        self.assertIsNone(event.agent_state)
        self.assertIsNone(event.scenario_probability)
        self.assertIsNone(event.confidence_assessment)
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)

    def test_orchestrator_can_run_through_agent_state(self) -> None:
        hypothesis_event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_hypothesis=True,
            **FIXTURE_IDENTITY,
        )
        original_snapshot = hypothesis_event.market_snapshot.to_dict()
        original_structure = hypothesis_event.structural_evidence.to_dict()
        original_efficiency = hypothesis_event.market_efficiency_evidence.to_dict()
        original_hypothesis = hypothesis_event.hypothesis_package.to_dict()

        event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_agent_state=True,
            **FIXTURE_IDENTITY,
        )

        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNotNone(event.observation_package)
        self.assertIsNotNone(event.structural_evidence)
        self.assertIsNotNone(event.market_efficiency_evidence)
        self.assertIsNotNone(event.hypothesis_package)
        self.assertIsNotNone(event.agent_state)
        self.assertEqual(event.market_snapshot.to_dict(), original_snapshot)
        self.assertEqual(event.structural_evidence.to_dict(), original_structure)
        self.assertEqual(
            event.market_efficiency_evidence.to_dict(),
            original_efficiency,
        )
        self.assertEqual(event.hypothesis_package.to_dict(), original_hypothesis)
        self.assertEqual(event.agent_state.event_id, event.event_id)
        self.assertEqual(event.agent_state.current_state.value, "unknown")
        self.assertIsNone(event.scenario_probability)
        self.assertIsNone(event.confidence_assessment)
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)

    def test_orchestrator_rejects_retired_scenario_probability_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "stages after Agent State are retired"):
            run_fixture_market_data_cycle(
                event_id="runtime-evt-1",
                cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="1m",
                fixture_path=FIXTURE,
                run_scenario_probability=True,
                **FIXTURE_IDENTITY,
            )
        return
        agent_state_event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_agent_state=True,
            **FIXTURE_IDENTITY,
        )
        original_snapshot = agent_state_event.market_snapshot.to_dict()
        original_structure = agent_state_event.structural_evidence.to_dict()
        original_efficiency = agent_state_event.market_efficiency_evidence.to_dict()
        original_hypothesis = agent_state_event.hypothesis_package.to_dict()
        original_agent_state = agent_state_event.agent_state.to_dict()

        event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_scenario_probability=True,
            **FIXTURE_IDENTITY,
        )

        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNone(event.observation_package)
        self.assertIsNotNone(event.structural_evidence)
        self.assertIsNotNone(event.market_efficiency_evidence)
        self.assertIsNotNone(event.hypothesis_package)
        self.assertIsNotNone(event.agent_state)
        self.assertIsNotNone(event.scenario_probability)
        self.assertEqual(event.market_snapshot.to_dict(), original_snapshot)
        self.assertEqual(event.structural_evidence.to_dict(), original_structure)
        self.assertEqual(
            event.market_efficiency_evidence.to_dict(),
            original_efficiency,
        )
        self.assertEqual(event.hypothesis_package.to_dict(), original_hypothesis)
        self.assertEqual(event.agent_state.to_dict(), original_agent_state)
        self.assertEqual(
            event.scenario_probability.runtime_event_id,
            event.event_id,
        )
        self.assertEqual(
            event.scenario_probability.primary_scenario,
            "continue_observation",
        )
        self.assertIsNone(event.confidence_assessment)
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)

    def test_orchestrator_rejects_retired_confidence_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "stages after Agent State are retired"):
            run_fixture_runtime_cycle(
                event_id="runtime-evt-1",
                cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="1m",
                fixture_path=FIXTURE,
                target_stage=FixtureRuntimeStage.CONFIDENCE,
                **FIXTURE_IDENTITY,
            )
        return
        scenario_event = run_fixture_market_data_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            run_scenario_probability=True,
            **FIXTURE_IDENTITY,
        )
        original_snapshot = scenario_event.market_snapshot.to_dict()
        original_structure = scenario_event.structural_evidence.to_dict()
        original_efficiency = scenario_event.market_efficiency_evidence.to_dict()
        original_hypothesis = scenario_event.hypothesis_package.to_dict()
        original_agent_state = scenario_event.agent_state.to_dict()
        original_scenario = scenario_event.scenario_probability.to_dict()

        event = run_fixture_runtime_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            target_stage=FixtureRuntimeStage.CONFIDENCE,
            **FIXTURE_IDENTITY,
        )

        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNone(event.observation_package)
        self.assertIsNotNone(event.structural_evidence)
        self.assertIsNotNone(event.market_efficiency_evidence)
        self.assertIsNotNone(event.hypothesis_package)
        self.assertIsNotNone(event.agent_state)
        self.assertIsNotNone(event.scenario_probability)
        self.assertIsNotNone(event.confidence_assessment)
        self.assertEqual(event.market_snapshot.to_dict(), original_snapshot)
        self.assertEqual(event.structural_evidence.to_dict(), original_structure)
        self.assertEqual(
            event.market_efficiency_evidence.to_dict(),
            original_efficiency,
        )
        self.assertEqual(event.hypothesis_package.to_dict(), original_hypothesis)
        self.assertEqual(event.agent_state.to_dict(), original_agent_state)
        self.assertEqual(event.scenario_probability.to_dict(), original_scenario)
        self.assertEqual(event.confidence_assessment.event_id, event.event_id)
        self.assertEqual(
            event.confidence_assessment.episode_id,
            event.hypothesis_package.episode_id,
        )
        self.assertEqual(
            event.confidence_assessment.source_hypothesis_id,
            event.hypothesis_package.hypothesis_id,
        )
        self.assertEqual(
            event.confidence_assessment.final_confidence_level.value,
            "low",
        )
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)

    def test_orchestrator_rejects_retired_decision_alert_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "stages after Agent State are retired"):
            run_fixture_runtime_cycle(
                event_id="runtime-evt-1",
                cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="1m",
                fixture_path=FIXTURE,
                target_stage=FixtureRuntimeStage.DECISION_ALERT,
                **FIXTURE_IDENTITY,
            )
        return
        confidence_event = run_fixture_runtime_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            target_stage=FixtureRuntimeStage.CONFIDENCE,
            **FIXTURE_IDENTITY,
        )
        original_snapshot = confidence_event.market_snapshot.to_dict()
        original_structure = confidence_event.structural_evidence.to_dict()
        original_efficiency = confidence_event.market_efficiency_evidence.to_dict()
        original_hypothesis = confidence_event.hypothesis_package.to_dict()
        original_agent_state = confidence_event.agent_state.to_dict()
        original_scenario = confidence_event.scenario_probability.to_dict()
        original_confidence = confidence_event.confidence_assessment.to_dict()

        event = run_fixture_runtime_cycle(
            event_id="runtime-evt-1",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            fixture_path=FIXTURE,
            target_stage=FixtureRuntimeStage.DECISION_ALERT,
            **FIXTURE_IDENTITY,
        )

        self.assertIsNotNone(event.market_snapshot)
        self.assertIsNone(event.observation_package)
        self.assertIsNotNone(event.structural_evidence)
        self.assertIsNotNone(event.market_efficiency_evidence)
        self.assertIsNotNone(event.hypothesis_package)
        self.assertIsNotNone(event.agent_state)
        self.assertIsNotNone(event.scenario_probability)
        self.assertIsNotNone(event.confidence_assessment)
        self.assertIsNotNone(event.decision_alert)
        self.assertEqual(event.market_snapshot.to_dict(), original_snapshot)
        self.assertEqual(event.structural_evidence.to_dict(), original_structure)
        self.assertEqual(
            event.market_efficiency_evidence.to_dict(),
            original_efficiency,
        )
        self.assertEqual(event.hypothesis_package.to_dict(), original_hypothesis)
        self.assertEqual(event.agent_state.to_dict(), original_agent_state)
        self.assertEqual(event.scenario_probability.to_dict(), original_scenario)
        self.assertEqual(event.confidence_assessment.to_dict(), original_confidence)
        self.assertEqual(event.decision_alert.event_id, event.event_id)
        self.assertTrue(event.decision_alert.non_execution_confirmation)
        self.assertIsNone(event.learning_metadata)

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
