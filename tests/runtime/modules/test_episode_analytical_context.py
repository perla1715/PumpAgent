from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
import json
from unittest import TestCase

from pumpagent.runtime.domain.episode_analytical_context import (
    EpisodeAnalyticalContext,
    build_episode_analytical_context_from_runtime_result,
    prepare_runtime_previous_context,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ObservationEpisodeStatus,
    ProcessDirection,
)
from pumpagent.runtime.modules.observation_lifecycle.runtime_cycle import (
    ObservationRuntimeCycleStatus,
    process_observation_runtime_cycle,
)
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator
from tests.runtime.modules.test_observation_runtime_cycle import (
    CANDLE,
    active_entry,
    cycle,
    manager_with,
    process_snapshot,
    snapshot,
)


class RecordingRuntime(RuntimeOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.previous_inputs: list[tuple[object, str]] = []

    def process_market_update(self, snapshot, *, previous_state="UNKNOWN", previous_hypothesis=None,
                              episode_id=None, previous_process_evidence=None,
                              previous_process_quality_assessments=(),
                              healthy_baseline_reference=None,
                              healthy_baseline_designation=None,
                              previous_scenario_probability=None,
                              classification_timestamp=None):  # type: ignore[no-untyped-def]
        self.previous_inputs.append(
            (
                previous_hypothesis,
                previous_state,
                previous_process_evidence,
                previous_scenario_probability,
            )
        )
        return super().process_market_update(
            snapshot, previous_state=previous_state, previous_hypothesis=previous_hypothesis,
            episode_id=episode_id, previous_process_evidence=previous_process_evidence,
            previous_process_quality_assessments=previous_process_quality_assessments,
            healthy_baseline_reference=healthy_baseline_reference,
            healthy_baseline_designation=healthy_baseline_designation,
            previous_scenario_probability=previous_scenario_probability,
            classification_timestamp=classification_timestamp,
        )


class EpisodeAnalyticalContextTests(TestCase):
    def test_first_and_second_cycle_use_only_episode_context(self) -> None:
        manager = manager_with(active_entry())
        runtime = RecordingRuntime()

        first = process_observation_runtime_cycle(cycle(snapshot()), manager, runtime)
        self.assertIs(first.status, ObservationRuntimeCycleStatus.COMPLETED)
        self.assertEqual(
            runtime.previous_inputs[0],
            (None, AgentStateType.UNKNOWN.name, None, None),
        )
        context = first.resulting_watchlist_entry.active_episode_analytical_context
        self.assertIsInstance(context, EpisodeAnalyticalContext)
        self.assertEqual(context.schema_version, "episode_analytical_context_v5")
        self.assertEqual(
            context.latest_hypothesis, first.runtime_result.hypothesis_package
        )
        self.assertEqual(context.latest_agent_state, first.runtime_result.agent_state)
        self.assertIs(
            context.latest_agent_state.process_direction,
            first.runtime_result.agent_state.process_direction,
        )
        self.assertIs(
            context.latest_scenario_probability,
            first.runtime_result.scenario_probability,
        )
        self.assertIs(
            context.latest_confidence_assessment,
            first.runtime_result.confidence_assessment,
        )
        self.assertEqual(
            context.latest_confidence,
            first.runtime_result.compatibility_context["confidence"],
        )
        self.assertEqual(context.latest_runtime_event_id, first.runtime_event_id)
        self.assertEqual(context.latest_process_evidence, first.runtime_result.process_evidence)
        self.assertEqual(context.completed_analytical_cycle_count, 1)

        second = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE + timedelta(minutes=5))),
            manager,
            runtime,
        )
        self.assertIs(second.status, ObservationRuntimeCycleStatus.COMPLETED)
        self.assertIs(runtime.previous_inputs[1][0], context.latest_hypothesis)
        self.assertEqual(runtime.previous_inputs[1][1], context.latest_agent_state.current_state.name)
        self.assertIs(runtime.previous_inputs[1][2], context.latest_process_evidence)
        self.assertIs(
            runtime.previous_inputs[1][3],
            context.latest_scenario_probability,
        )
        self.assertEqual(
            second.runtime_result.scenario_probability.previous_scenario_probability_id,
            context.latest_scenario_probability.scenario_probability_id,
        )
        self.assertEqual(second.resulting_watchlist_entry.observation_count, 2)
        self.assertEqual(
            second.resulting_watchlist_entry.active_episode_analytical_context.completed_analytical_cycle_count,
            2,
        )
        self.assertIs(
            second.resulting_watchlist_entry.active_episode_analytical_context.latest_scenario_probability,
            second.runtime_result.scenario_probability,
        )
        self.assertIsNot(
            second.resulting_watchlist_entry.active_episode_analytical_context.latest_scenario_probability,
            context.latest_scenario_probability,
        )
        self.assertIs(
            second.resulting_watchlist_entry.active_episode_analytical_context.latest_confidence_assessment,
            second.runtime_result.confidence_assessment,
        )
        self.assertIsNot(
            second.resulting_watchlist_entry.active_episode_analytical_context.latest_confidence_assessment,
            context.latest_confidence_assessment,
        )
        self.assertIs(
            second.resulting_watchlist_entry.active_episode_analytical_context.latest_agent_state.process_direction,
            ProcessDirection.UP,
        )

    def test_extraction_is_deterministic_serializable_immutable_and_non_mutating(self) -> None:
        result = process_observation_runtime_cycle(
            cycle(snapshot()), manager_with(active_entry()), RecordingRuntime()
        )
        episode = result.cycle_completion_result.previous_episode
        source_before = result.runtime_result
        kwargs = {"updated_at": result.completion_timestamp}
        first = build_episode_analytical_context_from_runtime_result(
            result.runtime_result, episode, CANDLE, **kwargs
        )
        second = build_episode_analytical_context_from_runtime_result(
            result.runtime_result, episode, CANDLE, **kwargs
        )
        self.assertEqual(first, second)
        self.assertIs(result.runtime_result, source_before)
        json.dumps(first.to_dict())
        with self.assertRaises(FrozenInstanceError):
            first.latest_confidence = 1  # type: ignore[misc]

    def test_preparation_rejects_episode_and_each_market_mismatch(self) -> None:
        completed = process_observation_runtime_cycle(
            cycle(snapshot()), manager_with(active_entry()), RecordingRuntime()
        ).resulting_watchlist_entry
        episode = completed.active_episode
        context = completed.active_episode_analytical_context
        for changed in (
            replace(episode, episode_id="replacement"),
            replace(episode, exchange="other"),
            replace(episode, symbol="ETHUSDT"),
            replace(episode, timeframe="15m"),
        ):
            with self.assertRaises(ValueError):
                prepare_runtime_previous_context(changed, context)

    def test_closed_or_replacement_episode_cannot_inherit_context(self) -> None:
        completed = process_observation_runtime_cycle(
            cycle(snapshot()), manager_with(active_entry()), RecordingRuntime()
        ).resulting_watchlist_entry
        context = completed.active_episode_analytical_context
        closed = replace(completed.active_episode, status=ObservationEpisodeStatus.CLOSED,
                         closing_timestamp=completed.last_updated,
                         closure_reason="controlled close")
        with self.assertRaises(ValueError):
            prepare_runtime_previous_context(closed, context)
        replacement = replace(active_entry().active_episode, episode_id="replacement")
        prepared = prepare_runtime_previous_context(replacement, None)
        self.assertIsNone(prepared.previous_hypothesis)
        self.assertEqual(prepared.previous_state, AgentStateType.UNKNOWN.name)

    def test_runtime_failure_and_duplicate_leave_context_unchanged(self) -> None:
        manager = manager_with(active_entry())
        runtime = RecordingRuntime()
        first = process_observation_runtime_cycle(cycle(snapshot()), manager, runtime)
        before = first.resulting_watchlist_entry
        duplicate = process_observation_runtime_cycle(cycle(snapshot()), manager, runtime)
        self.assertIs(duplicate.status, ObservationRuntimeCycleStatus.ADMISSION_STOPPED)
        self.assertIs(manager.get(symbol="BTCUSDT", exchange="bybit", timeframe="5m"), before)
