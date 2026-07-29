from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
from unittest import TestCase, mock

import pumpagent.runtime.modules.scenario_probability as scenario_probability_module
import pumpagent.runtime.modules.decision as decision_module
import pumpagent.runtime.modules.confidence as confidence_module

from pumpagent.runtime.domain import HypothesisLifecycleStatus, MarketSnapshot
from pumpagent.runtime.domain.decision import DecisionReasonCode, DecisionType
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    DataQualityStatus,
    ObservationEpisodeStatus,
    ProcessDirection,
)
from pumpagent.runtime.domain.process_evidence import ProcessState, ProcessTransition
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.modules.observation_lifecycle.cycle_completion import CycleCompletionStatus
from pumpagent.runtime.modules.observation_lifecycle.runtime_cycle import (
    ObservationRuntimeCycleInput,
    ObservationRuntimeCycleStatus,
    process_observation_runtime_cycle,
)
from pumpagent.runtime.modules.market_eligibility import (
    MarketEligibilityConfig,
    MarketEligibilityFilter,
    MarketEligibilityReason,
)
from pumpagent.runtime.modules.watchlist import WatchlistEntry, WatchlistManager
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator


OPENED = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
CANDLE = OPENED + timedelta(minutes=5)


def snapshot(candle: datetime = CANDLE, *, symbol: str = "BTCUSDT", closed: bool = True) -> MarketSnapshot:
    return MarketSnapshot(
        event_id=f"snapshot-{candle.isoformat()}", timestamp=candle,
        symbol=symbol, exchange="bybit", timeframe="5m", price=101.0,
        ohlcv=({"timestamp": candle, "open": 100.0, "high": 102.0,
                "low": 99.0, "close": 101.0, "volume": 60.0,
                "is_closed": closed},),
        volume=60.0, data_source="test", data_quality_status=DataQualityStatus.VALID,
    )


def process_snapshot(candle: datetime, *, closes=(100.0, 102.0), volumes=(40.0, 60.0),
                     oi_change=1.0) -> MarketSnapshot:
    rows = tuple({
        "timestamp": candle - timedelta(minutes=5 * (len(closes) - index - 1)),
        "open": close - 1.0, "high": close + 1.0, "low": close - 2.0,
        "close": close, "volume": volume, "is_closed": True,
    } for index, (close, volume) in enumerate(zip(closes, volumes)))
    return MarketSnapshot(
        event_id=f"process-{candle.isoformat()}", timestamp=candle,
        symbol="BTCUSDT", exchange="bybit", timeframe="5m", price=closes[-1],
        ohlcv=rows, volume=volumes[-1], data_source="test",
        data_quality_status=DataQualityStatus.VALID,
        optional_market_metrics={"oi_change_5m_pct": oi_change},
    )


def active_entry(symbol: str = "BTCUSDT") -> WatchlistEntry:
    episode = ObservationEpisode(
        episode_id=f"episode-{symbol}", exchange="bybit", symbol=symbol, timeframe="5m",
        opening_timestamp=OPENED, status=ObservationEpisodeStatus.ACTIVE,
        scanner_trigger_timestamp=OPENED, trigger_reasons=("scanner",),
    )
    return WatchlistEntry(
        symbol=symbol, exchange="bybit", timeframe="5m", first_seen=OPENED,
        last_updated=OPENED, current_agent_state=AgentStateType.UNKNOWN,
        active_episode=episode, active_episode_id=episode.episode_id,
        lifecycle_status=ObservationEpisodeStatus.ACTIVE,
        latest_accepted_trigger_timestamp=OPENED,
    )


def manager_with(*entries: WatchlistEntry) -> WatchlistManager:
    manager = WatchlistManager()
    for entry in entries:
        manager._entries[(entry.exchange, entry.symbol, entry.timeframe)] = entry  # noqa: SLF001
    return manager


def cycle(value: MarketSnapshot, *, candle: datetime | None = None) -> ObservationRuntimeCycleInput:
    return ObservationRuntimeCycleInput(
        snapshot=value, closed_candle_timestamp=candle or value.timestamp,
        exchange=value.exchange, symbol=value.symbol, timeframe=value.timeframe,
        runtime_request_timestamp=value.timestamp,
        runtime_completion_timestamp=value.timestamp + timedelta(seconds=1),
    )


class CountingRuntime(RuntimeOrchestrator):
    def __init__(self, *, fail: bool = False, hypothesis_id_generator=None) -> None:
        super().__init__(
            **(
                {"hypothesis_id_generator": hypothesis_id_generator}
                if hypothesis_id_generator is not None
                else {}
            )
        )
        self.calls = 0
        self.fail = fail

    def process_market_update(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail:
            raise RuntimeError("controlled failure")
        return super().process_market_update(*args, **kwargs)


class ObservationRuntimeCycleTests(TestCase):
    def test_changed_interpretation_replaces_canonical_hypothesis(self) -> None:
        generated = iter(("opaque-hypothesis-1", "opaque-hypothesis-2"))
        manager = manager_with(active_entry())
        runtime = CountingRuntime(hypothesis_id_generator=lambda: next(generated))

        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, runtime
        )
        second_candle = CANDLE + timedelta(minutes=5)
        second = process_observation_runtime_cycle(
            cycle(
                process_snapshot(
                    second_candle,
                    closes=(100.0, 103.0),
                    volumes=(40.0, 90.0),
                    oi_change=2.0,
                )
            ),
            manager,
            runtime,
        )

        first_hypothesis = first.runtime_result.hypothesis
        replacement = second.runtime_result.hypothesis
        self.assertIs(
            first_hypothesis.lifecycle_status,
            HypothesisLifecycleStatus.CREATED,
        )
        self.assertIs(
            replacement.lifecycle_status,
            HypothesisLifecycleStatus.REPLACED,
        )
        self.assertEqual(replacement.hypothesis_id, "opaque-hypothesis-2")
        self.assertEqual(
            replacement.previous_hypothesis_id,
            first_hypothesis.hypothesis_id,
        )
        self.assertEqual(
            replacement.previous_runtime_event_id,
            first.runtime_result.event_id,
        )
        stored = manager.get(exchange="bybit", symbol="BTCUSDT", timeframe="5m")
        self.assertEqual(stored.hypothesis_id, replacement.hypothesis_id)
        self.assertIs(
            stored.active_episode_analytical_context.latest_hypothesis,
            replacement,
        )

    def test_ineligible_cycle_stops_intentionally_without_mutating_episode(self) -> None:
        manager = manager_with(active_entry())
        runtime = CountingRuntime()
        completed = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, runtime
        )
        self.assertIs(completed.status, ObservationRuntimeCycleStatus.COMPLETED)
        before = manager.get(exchange="bybit", symbol="BTCUSDT", timeframe="5m")
        self.assertIsNotNone(before.active_episode_analytical_context)
        history_size_before = runtime.hypothesis_history.size()

        runtime.market_eligibility_filter = MarketEligibilityFilter(
            MarketEligibilityConfig(minimum_candles=3)
        )
        runtime.calls = 0
        next_candle = CANDLE + timedelta(minutes=5)

        analytical_calls = (
            "build_observation_package",
            "build_structural_evidence",
            "build_market_efficiency_evidence",
            "classify_market_process",
            "build_operational_hypothesis_package",
            "build_agent_state_from_hypothesis_package",
            "build_scenario_probability",
            "build_confidence_assessment",
        )
        patches = [
            mock.patch(f"pumpagent.runtime.orchestrator.runtime_loop.{name}")
            for name in analytical_calls
        ]
        mocked = [patch.start() for patch in patches]
        try:
            result = process_observation_runtime_cycle(
                cycle(process_snapshot(next_candle)), manager, runtime
            )
        finally:
            for patch in reversed(patches):
                patch.stop()

        after = manager.get(exchange="bybit", symbol="BTCUSDT", timeframe="5m")
        self.assertIs(result.status, ObservationRuntimeCycleStatus.INELIGIBLE)
        self.assertTrue(result.runtime_invoked)
        self.assertEqual(runtime.calls, 1)
        self.assertIsNone(result.runtime_result)
        self.assertIsNone(result.cycle_completion_result)
        self.assertIsNotNone(result.eligibility_result)
        self.assertFalse(result.eligibility_result.eligible)
        self.assertIs(
            result.eligibility_result.reason,
            MarketEligibilityReason.INSUFFICIENT_HISTORY,
        )
        self.assertEqual(
            dict(result.eligibility_result.details),
            {"candle_count": 2, "minimum_candles": 3},
        )
        serialized = result.to_dict()
        self.assertEqual(serialized["status"], "INELIGIBLE")
        self.assertEqual(
            serialized["eligibility_result"]["reason"],
            "INSUFFICIENT_HISTORY",
        )
        json.dumps(serialized)
        self.assertFalse(result.watchlist_changed)
        self.assertEqual(after, before)
        self.assertIsNotNone(after.active_episode)
        self.assertEqual(after.active_episode.observation_cycle_count, 1)
        self.assertEqual(after.latest_accepted_closed_candle_timestamp, CANDLE)
        self.assertIs(
            after.active_episode_analytical_context,
            before.active_episode_analytical_context,
        )
        self.assertIs(
            after.active_episode_analytical_context.latest_agent_state.process_direction,
            before.active_episode_analytical_context.latest_agent_state.process_direction,
        )
        self.assertIs(
            after.active_episode_analytical_context.latest_scenario_probability,
            before.active_episode_analytical_context.latest_scenario_probability,
        )
        self.assertIs(
            after.active_episode_analytical_context.latest_confidence_assessment,
            before.active_episode_analytical_context.latest_confidence_assessment,
        )
        self.assertEqual(runtime.hypothesis_history.size(), history_size_before)
        for analytical_call in mocked:
            analytical_call.assert_not_called()

    def test_scenario_identity_failures_do_not_advance_continuity(self) -> None:
        def mismatched_builder(field_name: str, value: str):
            def build(*args, **kwargs):  # type: ignore[no-untyped-def]
                scenario = scenario_probability_module.build_scenario_probability(
                    *args,
                    **kwargs,
                )
                if field_name == "event_id":
                    return replace(
                        scenario,
                        event_id=value,
                        supporting_evidence=tuple(
                            replace(reference, source_event_id=value)
                            for reference in scenario.supporting_evidence
                        ),
                        contradicting_evidence=tuple(
                            replace(reference, source_event_id=value)
                            for reference in scenario.contradicting_evidence
                        ),
                    )
                return replace(scenario, **{field_name: value})

            return build

        cases = (
            ("event_id", "other-event"),
            ("episode_id", "other-episode"),
            ("source_hypothesis_id", "other-hypothesis"),
        )
        for field_name, value in cases:
            with self.subTest(field_name=field_name):
                entry = active_entry()
                manager = manager_with(entry)
                with mock.patch(
                    "pumpagent.runtime.orchestrator.runtime_loop."
                    "build_scenario_probability",
                    side_effect=mismatched_builder(field_name, value),
                ):
                    result = process_observation_runtime_cycle(
                        cycle(process_snapshot(CANDLE)),
                        manager,
                        CountingRuntime(),
                    )

                self.assertIs(
                    result.status,
                    ObservationRuntimeCycleStatus.RUNTIME_FAILED,
                )
                self.assertFalse(result.watchlist_changed)
                self.assertIs(
                    manager.get(exchange="bybit", symbol="BTCUSDT", timeframe="5m"),
                    entry,
                )
                self.assertEqual(entry.active_episode.observation_cycle_count, 0)
                self.assertIsNone(entry.active_episode_analytical_context)

    def test_failure_before_scenario_does_not_advance_continuity(self) -> None:
        entry = active_entry()
        manager = manager_with(entry)
        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop."
            "build_agent_state_from_hypothesis_package",
            side_effect=RuntimeError("agent state failed"),
        ), mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.build_scenario_probability"
        ) as scenario_builder:
            result = process_observation_runtime_cycle(
                cycle(process_snapshot(CANDLE)),
                manager,
                CountingRuntime(),
            )

        self.assertIs(result.status, ObservationRuntimeCycleStatus.RUNTIME_FAILED)
        scenario_builder.assert_not_called()
        self.assertIs(
            manager.get(exchange="bybit", symbol="BTCUSDT", timeframe="5m"),
            entry,
        )

    def test_confidence_identity_failures_do_not_advance_continuity(self) -> None:
        def mismatched_builder(field_name: str, value: str):
            def build(*args, **kwargs):  # type: ignore[no-untyped-def]
                assessment = confidence_module.build_confidence_assessment(
                    *args,
                    **kwargs,
                )
                return replace(assessment, **{field_name: value})

            return build

        cases = (
            ("event_id", "other-event"),
            ("episode_id", "other-episode"),
            ("source_hypothesis_id", "other-hypothesis"),
        )
        for field_name, value in cases:
            with self.subTest(field_name=field_name):
                entry = active_entry()
                manager = manager_with(entry)
                with mock.patch(
                    "pumpagent.runtime.orchestrator.runtime_loop."
                    "build_confidence_assessment",
                    side_effect=mismatched_builder(field_name, value),
                ):
                    result = process_observation_runtime_cycle(
                        cycle(process_snapshot(CANDLE)),
                        manager,
                        CountingRuntime(),
                    )

                self.assertIs(
                    result.status,
                    ObservationRuntimeCycleStatus.RUNTIME_FAILED,
                )
                self.assertFalse(result.watchlist_changed)
                self.assertIs(
                    manager.get(exchange="bybit", symbol="BTCUSDT", timeframe="5m"),
                    entry,
                )
                self.assertIsNone(entry.active_episode_analytical_context)

    def test_failure_before_confidence_preserves_previous_assessment(self) -> None:
        manager = manager_with(active_entry())
        runtime = CountingRuntime()
        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)),
            manager,
            runtime,
        )
        before = first.resulting_watchlist_entry
        previous_assessment = (
            before.active_episode_analytical_context.latest_confidence_assessment
        )
        next_candle = CANDLE + timedelta(minutes=5)

        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.build_confidence_assessment",
            side_effect=RuntimeError("confidence failed"),
        ):
            failed = process_observation_runtime_cycle(
                cycle(process_snapshot(next_candle)),
                manager,
                runtime,
            )

        self.assertIs(failed.status, ObservationRuntimeCycleStatus.RUNTIME_FAILED)
        after = manager.get(exchange="bybit", symbol="BTCUSDT", timeframe="5m")
        self.assertIs(after, before)
        self.assertIs(
            after.active_episode_analytical_context.latest_confidence_assessment,
            previous_assessment,
        )

    def test_failure_after_scenario_preserves_previous_scenario_continuity(self) -> None:
        manager = manager_with(active_entry())
        runtime = CountingRuntime()
        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)),
            manager,
            runtime,
        )
        before = first.resulting_watchlist_entry
        previous_scenario = (
            before.active_episode_analytical_context.latest_scenario_probability
        )
        previous_assessment = (
            before.active_episode_analytical_context.latest_confidence_assessment
        )
        next_candle = CANDLE + timedelta(minutes=5)

        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.build_scenario_probability",
            wraps=scenario_probability_module.build_scenario_probability,
        ) as scenario_builder, mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.build_confidence_assessment",
            wraps=confidence_module.build_confidence_assessment,
        ) as confidence_builder, mock.patch.object(
            manager,
            "apply_completed_observation_cycle",
            side_effect=ValueError("controlled commit failure"),
        ):
            failed = process_observation_runtime_cycle(
                cycle(process_snapshot(next_candle)),
                manager,
                runtime,
            )

        scenario_builder.assert_called_once()
        confidence_builder.assert_called_once()
        self.assertIs(
            failed.status,
            ObservationRuntimeCycleStatus.COMPLETION_REJECTED,
        )
        after = manager.get(exchange="bybit", symbol="BTCUSDT", timeframe="5m")
        self.assertIs(after, before)
        self.assertIs(
            after.active_episode_analytical_context.latest_scenario_probability,
            previous_scenario,
        )
        self.assertIs(
            after.active_episode_analytical_context.latest_confidence_assessment,
            previous_assessment,
        )
        self.assertEqual(after.active_episode.observation_cycle_count, 1)

    def test_canonical_scenario_passes_unchanged_to_confidence_and_decision(
        self,
    ) -> None:
        manager = manager_with(active_entry())
        runtime = CountingRuntime()
        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)),
            manager,
            runtime,
        )
        self.assertIs(first.status, ObservationRuntimeCycleStatus.COMPLETED)

        next_candle = CANDLE + timedelta(minutes=5)
        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop."
            "build_confidence_assessment",
            wraps=confidence_module.build_confidence_assessment,
        ) as confidence_builder, mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop."
            "build_decision_assessment",
            wraps=decision_module.build_decision_assessment,
        ) as decision_builder:
            second = process_observation_runtime_cycle(
                cycle(process_snapshot(next_candle)),
                manager,
                runtime,
            )

        self.assertIs(second.status, ObservationRuntimeCycleStatus.COMPLETED)
        scenario = second.runtime_result.scenario_probability
        self.assertIs(confidence_builder.call_args.args[2], scenario)
        decision_input = decision_builder.call_args.args[0]
        self.assertIs(decision_input.scenario_probability, scenario)
        self.assertIsNotNone(second.runtime_result.decision_assessment)
        self.assertEqual(
            second.runtime_result.decision_assessment.scenario_probability_reference,
            scenario.scenario_probability_id,
        )

    def test_process_continuity_and_conservative_downstream_mapping(self) -> None:
        manager = manager_with(active_entry())
        runtime = CountingRuntime()
        first = process_observation_runtime_cycle(cycle(process_snapshot(CANDLE)), manager, runtime)
        self.assertIs(first.process_state, ProcessState.UNKNOWN)
        self.assertIs(first.process_transition, ProcessTransition.INITIAL)
        self.assertFalse(first.previous_process_evidence_used)
        self.assertIsNotNone(first.runtime_result.decision_assessment)
        self.assertIs(
            first.runtime_result.decision_assessment.decision_type,
            DecisionType.STAY_OUT,
        )
        self.assertEqual(
            first.runtime_result.decision_assessment.reason_codes,
            (DecisionReasonCode.UPSTREAM_INHIBITION,),
        )
        self.assertEqual(
            first.runtime_result.hypothesis.hypothesis_label,
            "No clear hypothesis",
        )
        self.assertIs(first.runtime_result.agent_state.process_direction, ProcessDirection.UP)

        second_candle = CANDLE + timedelta(minutes=5)
        second = process_observation_runtime_cycle(
            cycle(process_snapshot(second_candle)), manager, runtime
        )
        self.assertIs(second.process_state, ProcessState.CONTINUATION_ALIVE)
        self.assertTrue(second.previous_process_evidence_used)
        self.assertIs(second.runtime_result.agent_state.current_state,
                      AgentStateType.CONTINUATION_ALIVE)
        self.assertIs(second.runtime_result.agent_state.process_direction, ProcessDirection.UP)

        third_candle = second_candle + timedelta(minutes=5)
        weakening = process_observation_runtime_cycle(
            cycle(process_snapshot(third_candle, closes=(102.0, 101.0),
                                   volumes=(60.0, 40.0), oi_change=-1.0)),
            manager, runtime,
        )
        self.assertIs(weakening.process_state, ProcessState.WEAKENING)
        self.assertEqual(
            weakening.runtime_result.hypothesis.hypothesis_label,
            "Move is weakening",
        )
        self.assertIs(weakening.runtime_result.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertIs(
            weakening.runtime_result.agent_state.process_direction,
            ProcessDirection.DOWN,
        )
        self.assertIs(
            weakening.resulting_watchlist_entry.active_episode_analytical_context.latest_agent_state.process_direction,
            ProcessDirection.DOWN,
        )
        self.assertIs(
            second.resulting_watchlist_entry.active_episode_analytical_context.latest_agent_state.process_direction,
            ProcessDirection.UP,
        )
        self.assertNotIn(weakening.runtime_result.agent_state.current_state, (
            AgentStateType.CONTINUATION_SATURATION, AgentStateType.FIRST_FAILURE_CANDIDATE,
        ))

    def test_process_and_hypothesis_boundaries_are_atomic(self) -> None:
        manager = manager_with(active_entry())
        before = manager.list_active()
        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.classify_market_process",
            side_effect=RuntimeError("classifier failed"),
        ) as classifier:
            failed = process_observation_runtime_cycle(
                cycle(process_snapshot(CANDLE)), manager, CountingRuntime()
            )
        classifier.assert_called_once()
        self.assertIs(failed.status, ObservationRuntimeCycleStatus.RUNTIME_FAILED)
        self.assertEqual(manager.list_active(), before)

        completed = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, CountingRuntime()
        )
        self.assertIs(completed.status, ObservationRuntimeCycleStatus.COMPLETED)

    def test_two_successful_cycles_invoke_once_each_and_advance_atomically(self) -> None:
        manager = manager_with(active_entry())
        runtime = CountingRuntime()
        first_snapshot = process_snapshot(CANDLE)
        first = process_observation_runtime_cycle(cycle(first_snapshot), manager, runtime)

        self.assertIs(first.status, ObservationRuntimeCycleStatus.COMPLETED)
        self.assertEqual(runtime.calls, 1)
        self.assertEqual(first.resulting_watchlist_entry.observation_count, 1)
        self.assertEqual(first.resulting_watchlist_entry.latest_runtime_event_id, first.runtime_event_id)
        self.assertEqual(first.resulting_watchlist_entry.active_episode_id, "episode-BTCUSDT")
        self.assertEqual(first.runtime_result.new_state, "UNKNOWN")
        self.assertIs(first.resulting_watchlist_entry.current_agent_state, AgentStateType.UNKNOWN)

        newer = CANDLE + timedelta(minutes=5)
        second = process_observation_runtime_cycle(
            cycle(process_snapshot(newer)),
            manager,
            runtime,
        )
        self.assertIs(second.status, ObservationRuntimeCycleStatus.COMPLETED)
        self.assertEqual(runtime.calls, 2)
        self.assertEqual(second.resulting_watchlist_entry.observation_count, 2)
        self.assertEqual(second.resulting_watchlist_entry.latest_accepted_closed_candle_timestamp, newer)

    def test_duplicate_older_open_no_episode_and_identity_mismatch_stop_before_runtime(self) -> None:
        completed = replace(
            active_entry(), active_episode=replace(active_entry().active_episode,
                observation_cycle_count=1, latest_accepted_candle_timestamp=CANDLE),
            observation_count=1, latest_accepted_closed_candle_timestamp=CANDLE,
        )
        manager = manager_with(completed)
        runtime = CountingRuntime()
        cases = (
            cycle(snapshot()),
            cycle(snapshot(CANDLE - timedelta(minutes=5))),
            cycle(snapshot(CANDLE + timedelta(minutes=5), closed=False), candle=CANDLE),
        )
        for value in cases:
            result = process_observation_runtime_cycle(value, manager, runtime)
            self.assertIs(result.status, ObservationRuntimeCycleStatus.ADMISSION_STOPPED)
        absent = process_observation_runtime_cycle(cycle(snapshot()), WatchlistManager(), runtime)
        mismatch_input = replace(cycle(snapshot()), symbol="ETHUSDT")
        mismatch = process_observation_runtime_cycle(mismatch_input, manager, runtime)
        self.assertIs(absent.status, ObservationRuntimeCycleStatus.ADMISSION_STOPPED)
        self.assertIs(mismatch.status, ObservationRuntimeCycleStatus.INVALID_CONTEXT)
        self.assertEqual(runtime.calls, 0)

    def test_runtime_exception_and_invalid_result_leave_watchlist_unchanged(self) -> None:
        manager = manager_with(active_entry())
        before = manager.list_active()
        failed = process_observation_runtime_cycle(cycle(snapshot()), manager, CountingRuntime(fail=True))
        self.assertIs(failed.status, ObservationRuntimeCycleStatus.RUNTIME_FAILED)
        self.assertEqual(manager.list_active(), before)

        runtime = CountingRuntime()
        with mock.patch.object(runtime, "process_market_update", return_value=None) as called:
            invalid = process_observation_runtime_cycle(cycle(snapshot()), manager, runtime)
        called.assert_called_once()
        self.assertIs(invalid.status, ObservationRuntimeCycleStatus.RUNTIME_FAILED)
        self.assertEqual(manager.list_active(), before)

    def test_completion_rejection_is_non_mutating(self) -> None:
        manager = manager_with(active_entry())
        before = manager.list_active()
        runtime = CountingRuntime()
        baseline = process_observation_runtime_cycle(
            cycle(snapshot()), manager_with(active_entry()), CountingRuntime()
        ).cycle_completion_result
        with mock.patch(
            "pumpagent.runtime.modules.observation_lifecycle.runtime_cycle.prepare_completed_observation_cycle",
            return_value=replace(
                baseline, status=CycleCompletionStatus.INVALID_CONTEXT, completed=False,
                updated_active_episode=None, resulting_watchlist_entry=None,
                resulting_observation_cycle_count=0,
                resulting_latest_accepted_candle_timestamp=None,
                completion_reason="controlled rejection", watchlist_state_changed=False,
            ),
        ) as prepare:
            rejected = process_observation_runtime_cycle(cycle(snapshot()), manager, runtime)
        prepare.assert_called_once()
        self.assertIs(rejected.status, ObservationRuntimeCycleStatus.COMPLETION_REJECTED)
        self.assertEqual(manager.list_active(), before)

    def test_episode_rebind_clears_legacy_global_history_and_result_serializes(self) -> None:
        runtime = CountingRuntime()
        first = process_observation_runtime_cycle(cycle(snapshot()), manager_with(active_entry()), runtime)
        self.assertEqual(runtime.hypothesis_history.size(), 1)
        eth = snapshot(symbol="ETHUSDT")
        second = process_observation_runtime_cycle(cycle(eth), manager_with(active_entry("ETHUSDT")), runtime)
        self.assertIs(second.status, ObservationRuntimeCycleStatus.COMPLETED)
        self.assertEqual(runtime.hypothesis_history.size(), 1)
        json.dumps(second.to_dict())
        with self.assertRaises(FrozenInstanceError):
            second.watchlist_changed = False  # type: ignore[misc]
        self.assertEqual(first.episode_id, "episode-BTCUSDT")
