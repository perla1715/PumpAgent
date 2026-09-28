"""Tests for the pure Watchlist--Observation Policy boundary."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
import unittest

from pumpagent.runtime.domain.base import FrozenDict
from pumpagent.runtime.domain.enums import (
    DataQualityStatus,
    ObservationEpisodeStatus,
    ObservationLifecycleDecision,
    ObservationTriggerRelation,
)
from pumpagent.runtime.domain.observation_policy import ObservationRequest
from pumpagent.runtime.modules.watchlist.observation_boundary import (
    ObservationBoundaryInput,
    WatchlistObservationContext,
    evaluate_observation_boundary,
    prepare_observation_boundary,
    prepare_observation_policy_context,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
OPENED = NOW - timedelta(minutes=20)
TRIGGERED = NOW - timedelta(minutes=1)


def request(**overrides: object) -> ObservationRequest:
    values: dict[str, object] = {
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "request_timestamp": NOW,
        "trigger_timestamp": TRIGGERED,
        "trigger_reasons": ("volume_growth",),
        "trigger_metrics": {"analytical": {"confidence": 99}},
    }
    values.update(overrides)
    return ObservationRequest(**values)  # type: ignore[arg-type]


def watchlist(active: bool = True, **overrides: object) -> WatchlistObservationContext:
    values: dict[str, object] = {
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "has_active_episode": active,
        "observation_count": 3,
        "diagnostic_metadata": {"confidence": 1, "nested": {"history": [1, 2]}},
    }
    if active:
        values.update(
            active_episode_id="episode-1",
            active_episode_opening_timestamp=OPENED,
            latest_accepted_trigger_timestamp=OPENED,
            latest_accepted_closed_candle_timestamp=OPENED,
            lifecycle_status=ObservationEpisodeStatus.ACTIVE,
        )
    values.update(overrides)
    return WatchlistObservationContext(**values)  # type: ignore[arg-type]


def boundary(active: bool = True, **overrides: object) -> ObservationBoundaryInput:
    values: dict[str, object] = {
        "request": request(),
        "watchlist_context": watchlist(active),
        "trigger_relation": ObservationTriggerRelation.NEWER if active else None,
    }
    values.update(overrides)
    return ObservationBoundaryInput(**values)  # type: ignore[arg-type]


class ObservationBoundaryTests(unittest.TestCase):
    def test_no_active_valid_and_invalid_requests(self) -> None:
        opened = evaluate_observation_boundary(boundary(False))
        invalid = evaluate_observation_boundary(
            boundary(False, request=request(data_quality_status=DataQualityStatus.CORRUPTED))
        )
        self.assertEqual(opened.proposed_lifecycle_action, ObservationLifecycleDecision.OPEN)
        self.assertTrue(opened.create_episode_required)
        self.assertEqual(invalid.proposed_lifecycle_action, ObservationLifecycleDecision.NO_ACTION)
        self.assertTrue(invalid.do_nothing)

    def test_active_newer_duplicate_and_older_requests(self) -> None:
        expected = {
            ObservationTriggerRelation.NEWER: ObservationLifecycleDecision.CONTINUE,
            ObservationTriggerRelation.DUPLICATE: ObservationLifecycleDecision.NO_ACTION,
            ObservationTriggerRelation.OLDER: ObservationLifecycleDecision.NO_ACTION,
        }
        for relation, action in expected.items():
            with self.subTest(relation=relation):
                result = evaluate_observation_boundary(boundary(trigger_relation=relation))
                self.assertEqual(result.proposed_lifecycle_action, action)
        self.assertTrue(evaluate_observation_boundary(boundary()).associate_with_active_episode_required)

    def test_explicit_close_and_replacement_are_descriptions_only(self) -> None:
        closed = evaluate_observation_boundary(
            boundary(closure_requested=True, closure_reason="explicit stop")
        )
        replaced = evaluate_observation_boundary(
            boundary(replacement_requested=True, closure_reason="explicit reset")
        )
        self.assertTrue(closed.close_episode_required)
        self.assertFalse(closed.create_episode_required)
        self.assertTrue(replaced.close_then_open_replacement_required)
        self.assertTrue(replaced.create_episode_required)

    def test_different_market_is_conservative_no_action(self) -> None:
        result = evaluate_observation_boundary(
            boundary(request=request(symbol="ETHUSDT"))
        )
        self.assertEqual(result.proposed_lifecycle_action, ObservationLifecycleDecision.NO_ACTION)
        self.assertEqual(result.active_episode_id, "episode-1")
        self.assertTrue(result.do_nothing)

    def test_invalid_active_context_combinations_are_rejected(self) -> None:
        invalid = (
            {"active_episode_id": None},
            {"active_episode_opening_timestamp": None},
            {"lifecycle_status": ObservationEpisodeStatus.CLOSED},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                watchlist(**values)
        with self.assertRaises(ValueError):
            watchlist(False, active_episode_id="episode-1")
        with self.assertRaises(ValueError):
            watchlist(observation_count=-1)

    def test_naive_timestamps_are_rejected(self) -> None:
        naive = datetime(2026, 7, 15, 12, 0)
        for name in (
            "active_episode_opening_timestamp",
            "latest_accepted_trigger_timestamp",
            "latest_accepted_closed_candle_timestamp",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                watchlist(**{name: naive})

    def test_flag_and_reason_requirements_are_policy_requirements(self) -> None:
        with self.assertRaises(ValueError):
            boundary(closure_requested=True, replacement_requested=True, closure_reason="x")
        with self.assertRaises(ValueError):
            boundary(closure_requested=True)
        with self.assertRaises(ValueError):
            boundary(False, closure_requested=True, closure_reason="x")

    def test_preparation_is_separate_deterministic_and_immutable(self) -> None:
        source = boundary()
        first = prepare_observation_policy_context(source)
        second = prepare_observation_policy_context(source)
        prepared = prepare_observation_boundary(source)
        self.assertEqual(first, second)
        self.assertIsNone(prepared.policy_decision)
        self.assertIsNone(prepared.proposed_lifecycle_action)
        self.assertEqual(source.watchlist_context.observation_count, 3)
        with self.assertRaises(FrozenInstanceError):
            source.watchlist_context.observation_count = 4

    def test_recursive_freezing_serialization_and_deterministic_evaluation(self) -> None:
        source = boundary()
        metadata = source.watchlist_context.diagnostic_metadata
        self.assertIsInstance(metadata, FrozenDict)
        self.assertIsInstance(metadata["nested"], FrozenDict)
        self.assertIsInstance(metadata["nested"]["history"], tuple)
        first = evaluate_observation_boundary(source)
        self.assertEqual(first, evaluate_observation_boundary(source))
        encoded = json.dumps(first.to_dict())
        self.assertIn('"proposed_lifecycle_action": "continue"', encoded)

    def test_no_watchlist_or_episode_mutation_and_diagnostics_do_not_decide(self) -> None:
        low = boundary(watchlist_context=watchlist(diagnostic_metadata={"confidence": 0}))
        high = boundary(watchlist_context=watchlist(diagnostic_metadata={"confidence": 100}))
        before_low = low.watchlist_context.to_dict()
        before_high = high.watchlist_context.to_dict()
        low_result = evaluate_observation_boundary(low)
        high_result = evaluate_observation_boundary(high)
        self.assertEqual(low_result.proposed_lifecycle_action, high_result.proposed_lifecycle_action)
        self.assertEqual(low.watchlist_context.to_dict(), before_low)
        self.assertEqual(high.watchlist_context.to_dict(), before_high)
        self.assertEqual(low_result.active_episode_id, "episode-1")


if __name__ == "__main__":
    unittest.main()
