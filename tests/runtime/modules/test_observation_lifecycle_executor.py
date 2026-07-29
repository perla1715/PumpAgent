"""Tests for the pure Observation Lifecycle Executor."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from pumpagent.runtime.domain.enums import (
    ObservationEpisodeStatus,
    ObservationLifecycleDecision,
    ObservationTriggerRelation,
)
from pumpagent.runtime.domain.observation_episode import (
    ObservationEpisode,
    generate_episode_id,
)
from pumpagent.runtime.domain.observation_policy import ObservationRequest
from pumpagent.runtime.modules.observation_lifecycle.executor import (
    ObservationLifecycleExecutionInput,
    execute_observation_lifecycle,
)
from pumpagent.runtime.modules.watchlist.observation_boundary import (
    ObservationBoundaryInput,
    WatchlistObservationContext,
    evaluate_observation_boundary,
    prepare_observation_boundary,
)


OPENED = datetime(2026, 7, 15, 11, 30, tzinfo=timezone.utc)
REQUESTED = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
EXECUTED = REQUESTED + timedelta(seconds=1)


def make_request(**overrides: object) -> ObservationRequest:
    values: dict[str, object] = {
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "request_timestamp": REQUESTED,
        "trigger_timestamp": REQUESTED - timedelta(seconds=10),
        "trigger_reasons": ("scanner_attention",),
        "trigger_metrics": {"price": 1, "confidence": 99, "recommendation": "buy"},
    }
    values.update(overrides)
    return ObservationRequest(**values)  # type: ignore[arg-type]


def make_episode(**overrides: object) -> ObservationEpisode:
    values: dict[str, object] = {
        "episode_id": generate_episode_id("bybit", "BTCUSDT", "5m", OPENED),
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "opening_timestamp": OPENED,
        "status": ObservationEpisodeStatus.ACTIVE,
        "scanner_trigger_timestamp": OPENED - timedelta(seconds=10),
        "trigger_reasons": ("original_trigger",),
        "trigger_metrics": {"original": True},
        "observation_cycle_count": 4,
    }
    values.update(overrides)
    return ObservationEpisode(**values)  # type: ignore[arg-type]


def approved_boundary(
    action: ObservationLifecycleDecision,
    *,
    episode: ObservationEpisode | None = None,
    request: ObservationRequest | None = None,
):
    request = request or make_request()
    if action is ObservationLifecycleDecision.NO_ACTION and episode is None:
        request = replace(request, eligible=False)
    active = episode is not None
    context = WatchlistObservationContext(
        exchange=episode.exchange if episode else request.exchange,
        symbol=episode.symbol if episode else request.symbol,
        timeframe=episode.timeframe if episode else request.timeframe,
        has_active_episode=active,
        active_episode_id=episode.episode_id if episode else None,
        active_episode_opening_timestamp=episode.opening_timestamp if episode else None,
        lifecycle_status=ObservationEpisodeStatus.ACTIVE if active else None,
    )
    values: dict[str, object] = {
        "request": request,
        "watchlist_context": context,
        "trigger_relation": ObservationTriggerRelation.NEWER if active else None,
    }
    if action is ObservationLifecycleDecision.CLOSE:
        values.update(closure_requested=True, closure_reason="authorized close")
    elif action is ObservationLifecycleDecision.REPLACE:
        values.update(replacement_requested=True, closure_reason="authorized replace")
    elif action is ObservationLifecycleDecision.NO_ACTION and active:
        values["trigger_relation"] = ObservationTriggerRelation.DUPLICATE
    return evaluate_observation_boundary(
        ObservationBoundaryInput(**values)  # type: ignore[arg-type]
    )


def execute(
    action: ObservationLifecycleDecision,
    episode: ObservationEpisode | None = None,
    **overrides: object,
):
    values: dict[str, object] = {
        "boundary_result": approved_boundary(action, episode=episode),
        "active_episode": episode,
        "execution_timestamp": EXECUTED,
    }
    values.update(overrides)
    return execute_observation_lifecycle(
        ObservationLifecycleExecutionInput(**values)  # type: ignore[arg-type]
    )


class ObservationLifecycleExecutorTests(unittest.TestCase):
    def test_open_creates_valid_deterministic_active_episode(self) -> None:
        first = execute(ObservationLifecycleDecision.OPEN)
        second = execute(ObservationLifecycleDecision.OPEN)
        opened = first.newly_opened_episode
        self.assertEqual(first, second)
        self.assertIs(opened, first.resulting_active_episode)
        self.assertEqual(opened.status, ObservationEpisodeStatus.ACTIVE)
        self.assertEqual(opened.observation_cycle_count, 0)
        self.assertIsNone(opened.latest_accepted_candle_timestamp)
        self.assertTrue(first.state_changed)

    def test_open_rejects_active_and_unevaluated_boundaries(self) -> None:
        episode = make_episode()
        with self.assertRaises(ValueError):
            ObservationLifecycleExecutionInput(
                approved_boundary(ObservationLifecycleDecision.OPEN), episode, EXECUTED
            )
        unevaluated = prepare_observation_boundary(
            ObservationBoundaryInput(
                request=make_request(),
                watchlist_context=WatchlistObservationContext(
                    "bybit", "BTCUSDT", "5m", False
                ),
            )
        )
        with self.assertRaises(ValueError):
            ObservationLifecycleExecutionInput(unevaluated, None, EXECUTED)

    def test_continue_preserves_identity_count_and_trigger(self) -> None:
        episode = make_episode()
        result = execute(ObservationLifecycleDecision.CONTINUE, episode)
        self.assertIs(result.previous_episode, episode)
        self.assertIs(result.resulting_active_episode, episode)
        self.assertEqual(result.resulting_active_episode.observation_cycle_count, 4)
        self.assertEqual(result.resulting_active_episode.trigger_reasons, ("original_trigger",))
        self.assertFalse(result.state_changed)

    def test_close_closes_immutably_and_removes_active_episode(self) -> None:
        episode = make_episode()
        result = execute(ObservationLifecycleDecision.CLOSE, episode)
        self.assertIsNone(result.resulting_active_episode)
        self.assertEqual(result.closed_episode.status, ObservationEpisodeStatus.CLOSED)
        self.assertEqual(result.closed_episode.episode_id, episode.episode_id)
        self.assertEqual(result.closed_episode.opening_timestamp, episode.opening_timestamp)
        self.assertEqual(result.closed_episode.closure_reason, "authorized close")
        self.assertEqual(episode.status, ObservationEpisodeStatus.ACTIVE)

    def test_close_rejects_missing_or_mismatched_identity(self) -> None:
        episode = make_episode()
        with self.assertRaises(ValueError):
            ObservationLifecycleExecutionInput(
                approved_boundary(ObservationLifecycleDecision.CLOSE, episode=episode),
                None,
                EXECUTED,
            )
        other = replace(episode, episode_id="episode_other")
        with self.assertRaises(ValueError):
            ObservationLifecycleExecutionInput(
                approved_boundary(ObservationLifecycleDecision.CLOSE, episode=episode),
                other,
                EXECUTED,
            )

    def test_replace_closes_old_and_assigns_trigger_only_to_new(self) -> None:
        episode = make_episode()
        result = execute(ObservationLifecycleDecision.REPLACE, episode)
        closed = result.closed_episode
        opened = result.newly_opened_episode
        self.assertEqual(closed.episode_id, episode.episode_id)
        self.assertNotEqual(opened.episode_id, episode.episode_id)
        self.assertIs(opened, result.resulting_active_episode)
        self.assertEqual(closed.trigger_reasons, ("original_trigger",))
        self.assertEqual(opened.trigger_reasons, ("scanner_attention",))
        self.assertEqual(opened.observation_cycle_count, 0)
        self.assertEqual(closed.observation_cycle_count, 4)
        self.assertTrue(result.replacement_trigger_belongs_only_to_new_episode)

    def test_no_action_preserves_present_or_absent_state(self) -> None:
        episode = make_episode()
        present = execute(ObservationLifecycleDecision.NO_ACTION, episode)
        absent = execute(ObservationLifecycleDecision.NO_ACTION)
        self.assertIs(present.resulting_active_episode, episode)
        self.assertFalse(present.state_changed)
        self.assertIsNone(absent.resulting_active_episode)
        self.assertFalse(absent.state_changed)

    def test_timestamp_ordering_is_enforced(self) -> None:
        boundary = approved_boundary(ObservationLifecycleDecision.OPEN)
        with self.assertRaises(ValueError):
            ObservationLifecycleExecutionInput(
                boundary, None, REQUESTED - timedelta(microseconds=1)
            )
        episode = make_episode(opening_timestamp=EXECUTED + timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "Closing timestamp"):
            execute(ObservationLifecycleDecision.CLOSE, episode)
        with self.assertRaises(ValueError):
            ObservationLifecycleExecutionInput(boundary, None, datetime(2026, 7, 15))
        future_trigger = approved_boundary(
            ObservationLifecycleDecision.OPEN,
            request=make_request(trigger_timestamp=EXECUTED + timedelta(seconds=1)),
        )
        with self.assertRaises(ValueError):
            ObservationLifecycleExecutionInput(future_trigger, None, EXECUTED)

    def test_immutable_serializable_and_no_external_state_mutation(self) -> None:
        episode = make_episode()
        boundary = approved_boundary(ObservationLifecycleDecision.REPLACE, episode=episode)
        before_boundary = boundary.to_dict()
        before_episode = episode.to_dict()
        result = execute_observation_lifecycle(
            ObservationLifecycleExecutionInput(boundary, episode, EXECUTED)
        )
        json.dumps(result.to_dict())
        self.assertEqual(boundary.to_dict(), before_boundary)
        self.assertEqual(episode.to_dict(), before_episode)
        with self.assertRaises(FrozenInstanceError):
            result.state_changed = False  # type: ignore[misc]

    def test_analytical_values_cannot_influence_authorized_action(self) -> None:
        low = make_request(trigger_metrics={"confidence": 0, "recommendation": "avoid"})
        high = make_request(trigger_metrics={"confidence": 100, "recommendation": "buy"})
        low_boundary = approved_boundary(ObservationLifecycleDecision.OPEN, request=low)
        high_boundary = approved_boundary(ObservationLifecycleDecision.OPEN, request=high)
        low_result = execute_observation_lifecycle(
            ObservationLifecycleExecutionInput(low_boundary, None, EXECUTED)
        )
        high_result = execute_observation_lifecycle(
            ObservationLifecycleExecutionInput(high_boundary, None, EXECUTED)
        )
        self.assertEqual(low_result.executed_decision, high_result.executed_decision)
        self.assertEqual(
            low_result.newly_opened_episode.episode_id,
            high_result.newly_opened_episode.episode_id,
        )


if __name__ == "__main__":
    unittest.main()
