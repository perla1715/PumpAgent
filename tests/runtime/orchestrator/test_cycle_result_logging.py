from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.orchestrator import (
    serialize_agent_cycle_result,
    serialize_runtime_event,
)
from tests.runtime.orchestrator.test_runtime_loop import (
    TEST_EPISODE_ID,
    make_snapshot,
    run_agent_cycle,
)
from pumpagent.runtime.orchestrator import RuntimeOrchestrator


class RuntimeCycleResultLoggingTests(unittest.TestCase):
    def test_canonical_runtime_event_serializes_complete_aggregate(self) -> None:
        event = RuntimeOrchestrator().process_market_update(
            make_snapshot(), episode_id=TEST_EPISODE_ID
        )

        serialized = serialize_runtime_event(event)

        self.assertEqual(
            serialized["persistence_schema_version"],
            "canonical_runtime_event_v1",
        )
        self.assertEqual(serialized["runtime_event"], event.to_dict())

    def test_canonical_runtime_event_serialization_is_deterministic(self) -> None:
        event = RuntimeOrchestrator().process_market_update(
            make_snapshot(), episode_id=TEST_EPISODE_ID
        )

        self.assertEqual(
            serialize_runtime_event(event),
            serialize_runtime_event(event),
        )

    def test_serialization_includes_core_fields(self) -> None:
        result = run_agent_cycle(make_snapshot(), previous_state="UNKNOWN")

        serialized = serialize_agent_cycle_result(result)

        self.assertEqual(serialized["schema_version"], "runtime_cycle_v4")
        self.assertEqual(serialized["event_id"], result.event_id)
        self.assertEqual(serialized["timestamp"], result.timestamp.isoformat())
        self.assertEqual(serialized["symbol"], "BTCUSDT")
        self.assertEqual(serialized["exchange"], "binance")
        self.assertEqual(serialized["timeframe"], "1m")
        self.assertEqual(serialized["previous_state"], "UNKNOWN")
        self.assertEqual(serialized["new_state"], "UNKNOWN")
        self.assertEqual(
            serialized["process_direction"],
            result.agent_state.process_direction.value,
        )
        self.assertEqual(
            serialized["hypothesis_id"], result.hypothesis.hypothesis_id
        )
        self.assertEqual(
            serialized["hypothesis_status"], result.hypothesis.lifecycle_status.name
        )
        self.assertEqual(
            serialized["hypothesis_label"], result.hypothesis.hypothesis_label
        )
        self.assertEqual(
            serialized["hypothesis_episode_id"], result.hypothesis.episode_id
        )
        self.assertEqual(
            serialized["hypothesis_event_id"], result.hypothesis.event_id
        )
        self.assertEqual(serialized["explanation_confidence_score"], 50)
        self.assertEqual(serialized["confidence"], 50)
        self.assertEqual(
            serialized["confidence_semantics"],
            "explanation_confidence_compatibility_score",
        )
        self.assertEqual(serialized["agent_state_event_id"], result.agent_state.event_id)
        scenario = serialized["scenario_probability"]
        self.assertEqual(scenario["event_id"], result.event_id)
        self.assertEqual(scenario["episode_id"], result.hypothesis.episode_id)
        self.assertEqual(
            scenario["source_hypothesis_id"],
            result.hypothesis.hypothesis_id,
        )
        self.assertEqual(
            scenario["primary_scenario"],
            result.scenario_probability.primary_scenario.value,
        )
        self.assertEqual(
            scenario["probability_model"],
            "deterministic_policy_weights_not_calibrated",
        )
        self.assertEqual(len(serialized["evidence"]), 3)
        confidence = serialized["confidence_assessment"]
        self.assertEqual(confidence["event_id"], result.event_id)
        self.assertEqual(confidence["episode_id"], result.hypothesis.episode_id)
        self.assertEqual(
            confidence["source_hypothesis_id"],
            result.hypothesis.hypothesis_id,
        )
        self.assertEqual(confidence["final_confidence_level"], "low")
        self.assertEqual(
            confidence["data_quality_impact"],
            "market_snapshot_data_quality:valid",
        )
        self.assertIsNone(confidence["numeric_confidence_score"])

    def test_schema_version_is_present(self) -> None:
        serialized = serialize_agent_cycle_result(run_agent_cycle(make_snapshot()))

        self.assertIn("schema_version", serialized)
        self.assertEqual(serialized["schema_version"], "runtime_cycle_v4")

    def test_existing_serialized_fields_remain_backward_compatible(self) -> None:
        serialized = serialize_agent_cycle_result(run_agent_cycle(make_snapshot()))

        expected_fields = {
            "schema_version",
            "event_id",
            "timestamp",
            "symbol",
            "exchange",
            "timeframe",
            "previous_state",
            "new_state",
            "process_direction",
            "hypothesis_id",
            "hypothesis_status",
            "hypothesis_label",
            "hypothesis_episode_id",
            "hypothesis_event_id",
            "explanation_confidence_score",
            "confidence",
            "confidence_semantics",
            "evidence",
            "agent_state_event_id",
            "scenario_probability",
            "confidence_assessment",
        }

        self.assertEqual(set(serialized), expected_fields)

    def test_serialization_is_deterministic(self) -> None:
        result = run_agent_cycle(make_snapshot())

        first = serialize_agent_cycle_result(result)
        second = serialize_agent_cycle_result(result)

        self.assertEqual(first, second)

    def test_missing_optional_data_is_handled_safely(self) -> None:
        result = run_agent_cycle(make_snapshot(include_market_metrics=False))

        serialized = serialize_agent_cycle_result(result)

        self.assertEqual(serialized["new_state"], "UNKNOWN")
        self.assertEqual(serialized["confidence"], 0)
        self.assertEqual(
            serialized["evidence"],
            (
                {"name": "Price", "value": "Price not increasing", "positive": False},
                {
                    "name": "Volume",
                    "value": "Volume not above average",
                    "positive": False,
                },
                {"name": "OI", "value": "OI not increasing", "positive": False},
            ),
        )

    def test_state_fields_come_from_canonical_agent_state(self) -> None:
        result = run_agent_cycle(make_snapshot(), previous_state="ignition")

        serialized = serialize_agent_cycle_result(result)

        self.assertEqual(
            serialized["previous_state"],
            result.agent_state.previous_state.name,
        )
        self.assertEqual(serialized["new_state"], result.agent_state.current_state.name)

    def test_serialization_does_not_mutate_agent_cycle_result(self) -> None:
        result = run_agent_cycle(make_snapshot())
        before = result.log_messages

        serialize_agent_cycle_result(result)

        self.assertEqual(result.log_messages, before)
        with self.assertRaises(FrozenInstanceError):
            result.confidence = 0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
