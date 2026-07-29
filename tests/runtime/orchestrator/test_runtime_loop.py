from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest import mock
from uuid import UUID

import pumpagent.runtime.modules.agent_state as agent_state_module
import pumpagent.runtime.modules.confidence as confidence_module
import pumpagent.runtime.modules.hypothesis as hypothesis_module
import pumpagent.runtime.modules.scenario_probability as scenario_probability_module

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
RUNTIME_LOOP = SRC / "pumpagent" / "runtime" / "orchestrator" / "runtime_loop.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain import HypothesisLifecycleStatus, HypothesisPackage
from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.domain.decision import DecisionReasonCode, DecisionType
from pumpagent.runtime.domain.enums import AgentStateType, DataQualityStatus, RuntimeStatus
from pumpagent.runtime.modules.hypothesis import HypothesisHistory
from pumpagent.runtime.modules.hypothesis import generate_hypothesis_id
from pumpagent.runtime.modules.temporal_confidence import (
    CONFIDENCE_TREND_IMPROVING,
    CONFIDENCE_TREND_UNKNOWN,
    CONFIDENCE_TREND_WEAKENING,
)
from pumpagent.runtime.orchestrator import (
    AgentCycleResult,
    DiagnosticRuntimeReport,
    RuntimeOrchestrator,
    build_diagnostic_runtime_report,
    project_agent_cycle_result,
    run_agent_cycle as _run_agent_cycle,
)


TEST_EPISODE_ID = "episode-runtime-test"


def run_agent_cycle(snapshot: MarketSnapshot, **kwargs: object) -> AgentCycleResult:
    event = _run_agent_cycle(snapshot, episode_id=TEST_EPISODE_ID, **kwargs)
    assert isinstance(event, RuntimeEvent)
    assert event.runtime_status is RuntimeStatus.COMPLETED
    return project_agent_cycle_result(event)


def make_snapshot(
    *,
    price_change_1m: float = 1.1,
    price_change_3m: float = 1.5,
    volume_spike_ratio: float = 8.1,
    oi_change_1m: float = 0.1,
    include_market_metrics: bool = True,
    optional_metrics: dict[str, object] | None = None,
) -> MarketSnapshot:
    metrics = {}
    if include_market_metrics:
        metrics.update(
            {
                "price_change_1m": price_change_1m,
                "price_change_3m": price_change_3m,
                "volume_spike_ratio": volume_spike_ratio,
                "oi_change_1m": oi_change_1m,
            }
        )
    if optional_metrics is not None:
        metrics.update(optional_metrics)

    return MarketSnapshot(
        event_id="snapshot-1",
        timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        price=101.0,
        ohlcv=(
            {
                "timestamp": "2026-07-01T11:59:00+00:00",
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 40.0,
            },
            {
                "timestamp": "2026-07-01T12:00:00+00:00",
                "open": 100.0,
                "high": 103.0,
                "low": 100.0,
                "close": 101.0,
                "volume": 60.0,
            },
        ),
        volume=60.0,
        data_source="unit-test",
        data_quality_status=DataQualityStatus.VALID,
        optional_market_metrics=metrics,
    )


def next_snapshot(**kwargs: object) -> MarketSnapshot:
    value = make_snapshot(**kwargs)
    return replace(
        value,
        event_id="snapshot-2",
        timestamp=value.timestamp + timedelta(minutes=1),
    )


class RuntimeLoopTests(unittest.TestCase):
    def test_late_failures_rollback_each_runtime_owned_continuity_stage(self) -> None:
        def assert_unchanged(runtime: RuntimeOrchestrator) -> None:
            self.assertEqual(runtime.watchlist.list_active(), ())
            self.assertEqual(runtime.temporal_confidence._states, {})  # noqa: SLF001
            self.assertEqual(runtime.hypothesis_history.size(), 0)

        runtime = RuntimeOrchestrator()

        def mutate_watchlist(**kwargs):  # type: ignore[no-untyped-def]
            runtime.watchlist.register(
                symbol=kwargs["symbol"],
                exchange=kwargs["exchange"],
                timeframe=kwargs["timeframe"],
                timestamp=kwargs["timestamp"],
                current_agent_state=kwargs["agent_state"].current_state,
                hypothesis_id=kwargs["hypothesis"].hypothesis_id,
                confidence=kwargs["confidence"],
                event_id=kwargs["event_id"],
            )
            return "REGISTERED", 1

        with mock.patch.object(
            runtime.watchlist, "track_cycle", side_effect=mutate_watchlist
        ), mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop._update_temporal_confidence",
            side_effect=RuntimeError("after watchlist"),
        ):
            event = runtime.process_market_update(
                make_snapshot(), episode_id=TEST_EPISODE_ID
            )
        self.assertIs(event.runtime_status, RuntimeStatus.FAILED)
        assert_unchanged(runtime)

        def mutate_temporal(*_args):  # type: ignore[no-untyped-def]
            runtime.temporal_confidence._states[  # noqa: SLF001
                ("binance", "BTCUSDT", "1m")
            ] = mock.sentinel.temporal
            return None

        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop._update_temporal_confidence",
            side_effect=mutate_temporal,
        ), mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.aggregate_evidence_score",
            side_effect=RuntimeError("after temporal confidence"),
        ):
            event = runtime.process_market_update(
                make_snapshot(), episode_id=TEST_EPISODE_ID
            )
        self.assertIs(event.runtime_status, RuntimeStatus.FAILED)
        assert_unchanged(runtime)

        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop."
            "build_diagnostic_runtime_report",
            side_effect=RuntimeError("after hypothesis history"),
        ):
            event = runtime.process_market_update(
                make_snapshot(), episode_id=TEST_EPISODE_ID
            )
        self.assertIs(event.runtime_status, RuntimeStatus.FAILED)
        assert_unchanged(runtime)

    def test_production_runtime_returns_completed_runtime_event(self) -> None:
        event = RuntimeOrchestrator().process_market_update(
            make_snapshot(), episode_id=TEST_EPISODE_ID
        )

        self.assertIsInstance(event, RuntimeEvent)
        self.assertIs(event.runtime_status, RuntimeStatus.COMPLETED)
        self.assertIsNotNone(event.decision_assessment)

    def test_compatibility_projection_is_deterministic_and_one_way(self) -> None:
        event = RuntimeOrchestrator().process_market_update(
            make_snapshot(), episode_id=TEST_EPISODE_ID
        )

        first = project_agent_cycle_result(event)
        second = project_agent_cycle_result(event)

        self.assertEqual(first, second)
        self.assertEqual(event.runtime_status, RuntimeStatus.COMPLETED)
        self.assertFalse(hasattr(first, "to_runtime_event"))

    def test_compatibility_projection_rejects_non_completed_event(self) -> None:
        failed = RuntimeOrchestrator().process_market_update(make_snapshot())

        with self.assertRaisesRegex(ValueError, "completed RuntimeEvent"):
            project_agent_cycle_result(failed)

    def test_confidence_executes_once_after_scenario_probability(self) -> None:
        order: list[str] = []

        def build_state(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("agent_state")
            return agent_state_module.build_agent_state_from_hypothesis_package(
                *args, **kwargs
            )

        def build_scenario(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("scenario_probability")
            return scenario_probability_module.build_scenario_probability(
                *args, **kwargs
            )

        def build_confidence(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("confidence_assessment")
            return confidence_module.build_confidence_assessment(*args, **kwargs)

        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop."
            "build_agent_state_from_hypothesis_package",
            side_effect=build_state,
        ) as state_builder, mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.build_scenario_probability",
            side_effect=build_scenario,
        ) as scenario_builder, mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop.build_confidence_assessment",
            side_effect=build_confidence,
        ) as confidence_builder:
            result = run_agent_cycle(make_snapshot())

        self.assertEqual(
            order,
            ["agent_state", "scenario_probability", "confidence_assessment"],
        )
        state_builder.assert_called_once()
        scenario_builder.assert_called_once()
        confidence_builder.assert_called_once()
        self.assertIsNotNone(result.scenario_probability)
        self.assertIsNotNone(result.confidence_assessment)
        self.assertEqual(
            confidence_builder.call_args.kwargs["data_quality_impact"],
            "market_snapshot_data_quality:valid",
        )

    def test_numeric_confidence_is_hypothesis_compatibility_projection(self) -> None:
        result = run_agent_cycle(make_snapshot())

        self.assertEqual(
            result.confidence,
            result.hypothesis.explanation_confidence_score,
        )
        self.assertNotIn("calculate_confidence", RUNTIME_LOOP.read_text(encoding="utf-8"))

    def test_direct_runtime_requires_lifecycle_owned_episode_id(self) -> None:
        event = RuntimeOrchestrator().process_market_update(make_snapshot())

        self.assertIs(event.runtime_status, RuntimeStatus.FAILED)
        self.assertIn("Lifecycle-owned episode_id", event.errors_or_warnings[0])

    def test_production_hypothesis_generator_returns_uuid4(self) -> None:
        generated = generate_hypothesis_id()

        self.assertEqual(UUID(generated).version, 4)

    def test_controlled_operational_dependencies_exclude_market_hypothesis(
        self,
    ) -> None:
        paths = (
            SRC / "pumpagent/runtime/orchestrator/runtime_loop.py",
            SRC / "pumpagent/runtime/orchestrator/logging.py",
            SRC / "pumpagent/runtime/domain/episode_analytical_context.py",
            SRC / "pumpagent/runtime/modules/observation_lifecycle/runtime_cycle.py",
            SRC / "pumpagent/runtime/modules/watchlist/manager.py",
            SRC / "pumpagent/runtime/modules/agent_state/manager.py",
        )

        for path in paths:
            with self.subTest(path=path):
                self.assertNotIn(
                    "MarketHypothesis",
                    path.read_text(encoding="utf-8"),
                )

    def test_legacy_hypothesis_modules_and_exports_are_removed(self) -> None:
        legacy_contract = "Market" + "Hypothesis"
        legacy_builder = "build_" + "hypothesis"
        legacy_bridge = "build_agent_state_from_market_" + "hypothesis"

        self.assertFalse(hasattr(hypothesis_module, legacy_contract))
        self.assertFalse(hasattr(hypothesis_module, legacy_builder))
        self.assertFalse(hasattr(agent_state_module, legacy_bridge))
        self.assertFalse(
            (SRC / "pumpagent/runtime/modules/hypothesis/lifecycle.py").exists()
        )
        self.assertFalse(
            (SRC / "pumpagent/runtime/modules/agent_state/legacy_bridge.py").exists()
        )

    def test_canonical_hypothesis_created_updated_and_weakened_keep_identity(
        self,
    ) -> None:
        generated = iter(("opaque-hypothesis-1", "unused-hypothesis-2"))
        runtime = RuntimeOrchestrator(hypothesis_id_generator=lambda: next(generated))
        first = project_agent_cycle_result(runtime.process_market_update(
            make_snapshot(), episode_id=TEST_EPISODE_ID
        ))
        stronger = project_agent_cycle_result(runtime.process_market_update(
            next_snapshot(
                price_change_1m=2.1,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            ),
            episode_id=TEST_EPISODE_ID,
            previous_hypothesis=first.hypothesis,
        ))
        later = replace(next_snapshot(), event_id="snapshot-3")
        weaker = project_agent_cycle_result(runtime.process_market_update(
            later,
            episode_id=TEST_EPISODE_ID,
            previous_hypothesis=stronger.hypothesis,
        ))

        self.assertIsInstance(first.hypothesis, HypothesisPackage)
        self.assertIs(
            first.hypothesis.lifecycle_status,
            HypothesisLifecycleStatus.CREATED,
        )
        self.assertEqual(first.hypothesis.hypothesis_id, "opaque-hypothesis-1")
        self.assertNotEqual(first.hypothesis.hypothesis_id, first.event_id)
        self.assertIs(
            stronger.hypothesis.lifecycle_status,
            HypothesisLifecycleStatus.UPDATED,
        )
        self.assertEqual(
            stronger.hypothesis.hypothesis_id,
            first.hypothesis.hypothesis_id,
        )
        self.assertIs(
            weaker.hypothesis.lifecycle_status,
            HypothesisLifecycleStatus.WEAKENED,
        )
        self.assertEqual(
            weaker.hypothesis.hypothesis_id,
            first.hypothesis.hypothesis_id,
        )
        self.assertEqual(
            weaker.hypothesis.previous_runtime_event_id,
            stronger.event_id,
        )

    def test_hypothesis_continuity_cannot_cross_episode_boundaries(self) -> None:
        generated = iter(("episode-one-hypothesis", "episode-two-hypothesis"))
        runtime = RuntimeOrchestrator(hypothesis_id_generator=lambda: next(generated))
        first = project_agent_cycle_result(runtime.process_market_update(
            make_snapshot(), episode_id="episode-one"
        ))

        failed = runtime.process_market_update(
                next_snapshot(),
                episode_id="episode-two",
                previous_hypothesis=first.hypothesis,
        )
        self.assertIs(failed.runtime_status, RuntimeStatus.FAILED)
        self.assertIn("cross Episode boundaries", failed.errors_or_warnings[0])

        second = project_agent_cycle_result(runtime.process_market_update(
            next_snapshot(), episode_id="episode-two"
        ))
        self.assertIs(
            second.hypothesis.lifecycle_status,
            HypothesisLifecycleStatus.CREATED,
        )
        self.assertEqual(
            second.hypothesis.hypothesis_id,
            "episode-two-hypothesis",
        )
        self.assertNotEqual(
            second.hypothesis.hypothesis_id,
            first.hypothesis.hypothesis_id,
        )

    def test_empty_diagnostic_runtime_report(self) -> None:
        report = build_diagnostic_runtime_report(
            state="UNKNOWN",
            confidence=0,
            confidence_trend="UNKNOWN",
            temporal_confidence=None,
            evidence_summary=None,
            hypothesis_snapshot=None,
            hypothesis_history_size=0,
            history_trend_summary=None,
            created_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertIsInstance(report, DiagnosticRuntimeReport)
        self.assertEqual(report.state, "UNKNOWN")
        self.assertEqual(report.confidence, 0)
        self.assertEqual(report.confidence_trend, "UNKNOWN")
        self.assertIsNone(report.temporal_confidence)
        self.assertIsNone(report.evidence_summary)
        self.assertIsNone(report.hypothesis_snapshot)
        self.assertEqual(report.hypothesis_history_size, 0)
        self.assertIsNone(report.history_trend_summary)

    def test_normal_processing(self) -> None:
        result = run_agent_cycle(make_snapshot(), previous_state="UNKNOWN")

        self.assertIsInstance(result, AgentCycleResult)
        self.assertEqual(result.snapshot.symbol, "BTCUSDT")
        self.assertEqual(
            result.event_id,
            "agent-cycle:binance:BTCUSDT:1m:snapshot-1:2026-07-01T12:00:00+00:00",
        )
        self.assertEqual(result.structure_result.trend_structure, "rising_close_sequence")
        self.assertIn("volume_available", result.market_result.supporting_evidence)
        self.assertEqual(result.hypothesis.hypothesis_label, "No clear hypothesis")
        self.assertEqual(result.previous_state, "UNKNOWN")
        self.assertEqual(result.new_state, "UNKNOWN")
        self.assertEqual(result.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.agent_state.previous_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.agent_state.event_id, result.event_id)
        self.assertEqual(
            result.scenario_probability.runtime_event_id,
            result.event_id,
        )
        self.assertEqual(
            result.scenario_probability.episode_id,
            TEST_EPISODE_ID,
        )
        self.assertEqual(
            result.scenario_probability.source_hypothesis_id,
            result.hypothesis.hypothesis_id,
        )
        self.assertEqual(len(result.scenario_probability.distribution), 5)
        self.assertEqual(
            sum(
                item.probability
                for item in result.scenario_probability.distribution
            ),
            1,
        )
        self.assertIsNone(
            result.scenario_probability.previous_scenario_probability_id
        )
        self.assertIsNotNone(result.decision_assessment)
        self.assertIs(
            result.decision_assessment.decision_type,
            DecisionType.STAY_OUT,
        )
        self.assertEqual(
            result.decision_assessment.reason_codes,
            (DecisionReasonCode.UPSTREAM_INHIBITION,),
        )
        self.assertEqual(result.confidence_assessment.event_id, result.event_id)
        self.assertEqual(
            result.confidence_assessment.episode_id,
            TEST_EPISODE_ID,
        )
        self.assertEqual(
            result.confidence_assessment.source_hypothesis_id,
            result.hypothesis.hypothesis_id,
        )
        self.assertEqual(
            result.confidence_assessment.data_quality_impact,
            "market_snapshot_data_quality:valid",
        )
        self.assertIsNone(result.confidence_assessment.numeric_confidence_score)
        self.assertEqual(result.confidence, 50)
        self.assertEqual(len(result.evidence), 3)
        self.assertEqual(result.timestamp, result.snapshot.timestamp)
        self.assertEqual(result.watchlist_action, "NONE")
        self.assertEqual(result.watchlist_observation_count, 0)
        self.assertIsNone(result.temporal_confidence)
        self.assertTrue(result.evidence_summary.has_structural_evidence)
        self.assertTrue(result.evidence_summary.has_market_evidence)
        self.assertFalse(result.evidence_summary.has_temporal_evidence)
        self.assertEqual(result.evidence_summary.evidence_count, 2)
        self.assertEqual(result.evidence_summary.strongest_evidence_type, "structural")
        self.assertIsNotNone(result.hypothesis_snapshot)
        self.assertEqual(result.hypothesis_snapshot.state, "UNKNOWN")
        self.assertEqual(result.hypothesis_snapshot.confidence, 50)
        self.assertEqual(result.hypothesis_snapshot.confidence_trend, "UNKNOWN")
        self.assertEqual(result.hypothesis_snapshot.label, "mixed_evidence")
        self.assertEqual(result.hypothesis_snapshot.created_at, result.timestamp)
        self.assertEqual(result.hypothesis_history_size, 1)
        self.assertIsNotNone(result.history_trend_summary)
        self.assertEqual(result.history_trend_summary.confidence_trend, "UNKNOWN")
        self.assertEqual(result.history_trend_summary.evidence_score_trend, "UNKNOWN")
        self.assertEqual(result.history_trend_summary.label_stability, "UNKNOWN")
        self.assertEqual(result.history_trend_summary.sample_size, 1)
        self.assertIsNotNone(result.diagnostic_report)
        self.assertEqual(result.diagnostic_report.state, "UNKNOWN")
        self.assertEqual(result.diagnostic_report.confidence, 50)
        self.assertEqual(result.diagnostic_report.confidence_trend, "UNKNOWN")
        self.assertIs(result.diagnostic_report.temporal_confidence, result.temporal_confidence)
        self.assertIs(result.diagnostic_report.evidence_summary, result.evidence_summary)
        self.assertIs(result.diagnostic_report.hypothesis_snapshot, result.hypothesis_snapshot)
        self.assertEqual(result.diagnostic_report.hypothesis_history_size, 1)
        self.assertIs(
            result.diagnostic_report.history_trend_summary,
            result.history_trend_summary,
        )
        self.assertEqual(result.diagnostic_report.created_at, result.timestamp)
        self.assertIsNotNone(result.hypothesis_evaluation)
        self.assertEqual(result.hypothesis_evaluation.status, "UNKNOWN")
        self.assertEqual(
            result.hypothesis_evaluation.reason,
            "confidence_or_evidence_trend_unknown",
        )
        self.assertEqual(result.hypothesis_evaluation.created_at, result.timestamp)
        self.assertEqual(result.confidence_trend, CONFIDENCE_TREND_UNKNOWN)
        self.assertIsNone(result.confidence_delta)

    def test_missing_data(self) -> None:
        snapshot = make_snapshot(include_market_metrics=False)

        result = project_agent_cycle_result(RuntimeOrchestrator().process_market_update(
            snapshot, episode_id=TEST_EPISODE_ID
        ))

        self.assertEqual(result.new_state, "UNKNOWN")
        self.assertEqual(result.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.confidence, 0)
        self.assertEqual(result.hypothesis.hypothesis_label, "No clear hypothesis")
        self.assertGreaterEqual(len(result.hypothesis.contradicting_evidence), 3)
        self.assertEqual(result.watchlist_action, "NONE")
        self.assertEqual(result.watchlist_observation_count, 0)
        self.assertIsNone(result.temporal_confidence)
        self.assertTrue(result.evidence_summary.has_structural_evidence)
        self.assertTrue(result.evidence_summary.has_market_evidence)
        self.assertFalse(result.evidence_summary.has_temporal_evidence)
        self.assertEqual(result.evidence_summary.evidence_count, 2)
        self.assertIsNotNone(result.hypothesis_snapshot)
        self.assertEqual(result.hypothesis_snapshot.state, "UNKNOWN")
        self.assertEqual(result.hypothesis_snapshot.confidence, 0)
        self.assertEqual(result.hypothesis_snapshot.label, "mixed_evidence")
        self.assertEqual(result.hypothesis_history_size, 1)
        self.assertEqual(result.confidence_trend, CONFIDENCE_TREND_UNKNOWN)
        self.assertIsNone(result.confidence_delta)

    def test_unchanged_hypothesis(self) -> None:
        snapshot = make_snapshot()
        previous = run_agent_cycle(snapshot).hypothesis

        result = run_agent_cycle(
            next_snapshot(),
            previous_state="IGNITION",
            previous_hypothesis=previous,
        )

        self.assertEqual(result.hypothesis.hypothesis_label, previous.hypothesis_label)
        self.assertEqual(
            result.hypothesis.explanation_confidence_score,
            previous.explanation_confidence_score,
        )
        self.assertEqual(result.hypothesis.lifecycle_status.name, "UPDATED")
        self.assertEqual(result.previous_state, "IGNITION")
        self.assertEqual(result.new_state, "UNKNOWN")
        self.assertEqual(result.agent_state.previous_state, AgentStateType.IGNITION)
        self.assertEqual(result.agent_state.current_state, AgentStateType.UNKNOWN)

    def test_runtime_returns_canonical_agent_state(self) -> None:
        result = run_agent_cycle(make_snapshot(), previous_state="unknown")

        self.assertEqual(result.agent_state.current_state, AgentStateType.UNKNOWN)
        self.assertEqual(result.agent_state.event_id, result.event_id)
        self.assertEqual(result.new_state, result.agent_state.current_state.name)
        self.assertEqual(result.previous_state, result.agent_state.previous_state.name)

    def test_runtime_event_id_is_stable_for_same_snapshot(self) -> None:
        snapshot = make_snapshot()

        first = run_agent_cycle(snapshot)
        second = run_agent_cycle(snapshot)

        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.agent_state.event_id, second.agent_state.event_id)

    def test_agent_cycle_result_has_snapshot_compatibility_defaults(self) -> None:
        self.assertIsNone(
            AgentCycleResult.__dataclass_fields__["hypothesis_snapshot"].default
        )
        self.assertEqual(
            AgentCycleResult.__dataclass_fields__["hypothesis_history_size"].default,
            0,
        )
        self.assertIsNone(
            AgentCycleResult.__dataclass_fields__["history_trend_summary"].default
        )
        self.assertIsNone(
            AgentCycleResult.__dataclass_fields__["diagnostic_report"].default
        )
        self.assertIsNone(
            AgentCycleResult.__dataclass_fields__["hypothesis_evaluation"].default
        )

    def test_diagnostic_runtime_report_is_deterministic(self) -> None:
        result = run_agent_cycle(make_snapshot())

        first = build_diagnostic_runtime_report(
            state=result.agent_state,
            confidence=result.confidence,
            confidence_trend=result.confidence_trend,
            temporal_confidence=result.temporal_confidence,
            evidence_summary=result.evidence_summary,
            hypothesis_snapshot=result.hypothesis_snapshot,
            hypothesis_history_size=result.hypothesis_history_size,
            history_trend_summary=result.history_trend_summary,
            created_at=result.timestamp,
        )
        second = build_diagnostic_runtime_report(
            state=result.agent_state,
            confidence=result.confidence,
            confidence_trend=result.confidence_trend,
            temporal_confidence=result.temporal_confidence,
            evidence_summary=result.evidence_summary,
            hypothesis_snapshot=result.hypothesis_snapshot,
            hypothesis_history_size=result.hypothesis_history_size,
            history_trend_summary=result.history_trend_summary,
            created_at=result.timestamp,
        )

        self.assertEqual(first, second)

    def test_runtime_updates_dynamic_watchlist(self) -> None:
        orchestrator = RuntimeOrchestrator()
        snapshot = make_snapshot()

        first = project_agent_cycle_result(orchestrator.process_market_update(snapshot, episode_id=TEST_EPISODE_ID))
        second = project_agent_cycle_result(orchestrator.process_market_update(
            next_snapshot(),
            previous_state=first.new_state,
            previous_hypothesis=first.hypothesis,
            episode_id=TEST_EPISODE_ID,
        ))
        entry = orchestrator.watchlist.get(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        self.assertEqual(first.watchlist_action, "NONE")
        self.assertEqual(second.watchlist_action, "NONE")
        self.assertEqual(second.watchlist_observation_count, 0)
        self.assertIsNone(second.temporal_confidence)
        self.assertIsNone(entry)
        self.assertEqual(second.hypothesis_history_size, 2)
        self.assertIsNotNone(second.history_trend_summary)
        self.assertEqual(second.history_trend_summary.confidence_trend, "STABLE")
        self.assertEqual(second.history_trend_summary.evidence_score_trend, "STABLE")
        self.assertEqual(second.history_trend_summary.label_stability, "STABLE")
        self.assertEqual(second.history_trend_summary.sample_size, 2)
        self.assertIsNotNone(second.hypothesis_evaluation)
        self.assertEqual(second.hypothesis_evaluation.status, "NEUTRAL")

    def test_runtime_hypothesis_history_respects_limit(self) -> None:
        orchestrator = RuntimeOrchestrator(hypothesis_history=HypothesisHistory(max_length=1))
        first = project_agent_cycle_result(orchestrator.process_market_update(
            make_snapshot(), episode_id=TEST_EPISODE_ID
        ))
        second = project_agent_cycle_result(orchestrator.process_market_update(
            next_snapshot(),
            previous_state=first.new_state,
            previous_hypothesis=first.hypothesis,
            episode_id=TEST_EPISODE_ID,
        ))

        self.assertEqual(first.hypothesis_history_size, 1)
        self.assertEqual(second.hypothesis_history_size, 1)
        self.assertEqual(orchestrator.hypothesis_history.latest(), second.hypothesis_snapshot)
        self.assertIsNone(orchestrator.hypothesis_history.previous())

    def test_runtime_temporal_confidence_improves(self) -> None:
        orchestrator = RuntimeOrchestrator()
        first = project_agent_cycle_result(orchestrator.process_market_update(
            make_snapshot(), episode_id=TEST_EPISODE_ID
        ))

        second = project_agent_cycle_result(orchestrator.process_market_update(
            next_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            ),
            previous_state=first.new_state,
            previous_hypothesis=first.hypothesis,
            episode_id=TEST_EPISODE_ID,
        ))

        self.assertEqual(second.confidence, 90)
        self.assertIsNone(second.confidence_delta)
        self.assertEqual(second.confidence_trend, CONFIDENCE_TREND_UNKNOWN)

    def test_runtime_temporal_confidence_weakens(self) -> None:
        orchestrator = RuntimeOrchestrator()
        first = project_agent_cycle_result(orchestrator.process_market_update(
            make_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            ),
            episode_id=TEST_EPISODE_ID,
        ))

        second = project_agent_cycle_result(orchestrator.process_market_update(
            next_snapshot(),
            previous_state=first.new_state,
            previous_hypothesis=first.hypothesis,
            episode_id=TEST_EPISODE_ID,
        ))

        self.assertEqual(second.confidence, 50)
        self.assertIsNone(second.confidence_delta)
        self.assertEqual(second.confidence_trend, CONFIDENCE_TREND_UNKNOWN)

    def test_hypothesis_update(self) -> None:
        previous = run_agent_cycle(make_snapshot()).hypothesis

        result = run_agent_cycle(
            next_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            ),
            previous_hypothesis=previous,
        )

        self.assertEqual(
            result.hypothesis.hypothesis_label, previous.hypothesis_label
        )
        self.assertEqual(result.hypothesis.lifecycle_status.name, "UPDATED")
        self.assertGreater(
            result.confidence, previous.explanation_confidence_score
        )

    def test_confidence_increase(self) -> None:
        previous = run_agent_cycle(make_snapshot()).hypothesis

        result = run_agent_cycle(
            next_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            ),
            previous_hypothesis=previous,
        )

        self.assertEqual(previous.explanation_confidence_score, 50)
        self.assertEqual(result.confidence, 90)
        self.assertEqual(result.hypothesis.lifecycle_status.name, "UPDATED")

    def test_confidence_decrease(self) -> None:
        previous = run_agent_cycle(
            make_snapshot(
                price_change_1m=2.1,
                price_change_3m=2.5,
                volume_spike_ratio=10.1,
                oi_change_1m=2.1,
            )
        ).hypothesis

        result = run_agent_cycle(next_snapshot(), previous_hypothesis=previous)

        self.assertEqual(previous.explanation_confidence_score, 90)
        self.assertEqual(result.confidence, 50)
        self.assertEqual(result.hypothesis.lifecycle_status.name, "WEAKENED")


if __name__ == "__main__":
    unittest.main()
