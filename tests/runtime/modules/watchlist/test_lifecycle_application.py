"""Focused storage tests for authorized Observation Lifecycle results."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
from unittest import TestCase, mock

from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ObservationEpisodeStatus,
    ObservationLifecycleDecision,
    ObservationTriggerRelation,
)
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.domain.observation_policy import ObservationRequest
from pumpagent.runtime.modules.observation_lifecycle.executor import (
    ObservationLifecycleExecutionInput,
    execute_observation_lifecycle,
)
from pumpagent.runtime.modules.watchlist import (
    ObservationBoundaryInput,
    WatchlistManager,
    WatchlistObservationContext,
    evaluate_observation_boundary,
)


OPENED = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
REQUESTED = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
EXECUTED = REQUESTED + timedelta(seconds=1)


def request(symbol: str = "BTCUSDT") -> ObservationRequest:
    return ObservationRequest(
        exchange="bybit",
        symbol=symbol,
        timeframe="5m",
        request_timestamp=REQUESTED,
        trigger_timestamp=REQUESTED - timedelta(seconds=10),
        trigger_reasons=("scanner_attention",),
        triggering_closed_candle_timestamp=REQUESTED - timedelta(minutes=5),
    )


def boundary(
    action: ObservationLifecycleDecision,
    episode: ObservationEpisode | None = None,
    *,
    incoming: ObservationRequest | None = None,
):
    incoming = incoming or request()
    active = episode is not None
    context = WatchlistObservationContext(
        exchange=episode.exchange if episode else incoming.exchange,
        symbol=episode.symbol if episode else incoming.symbol,
        timeframe=episode.timeframe if episode else incoming.timeframe,
        has_active_episode=active,
        active_episode_id=episode.episode_id if episode else None,
        active_episode_opening_timestamp=episode.opening_timestamp if episode else None,
        lifecycle_status=ObservationEpisodeStatus.ACTIVE if active else None,
    )
    values: dict[str, object] = {
        "request": incoming,
        "watchlist_context": context,
        "trigger_relation": ObservationTriggerRelation.NEWER if active else None,
    }
    if action is ObservationLifecycleDecision.CLOSE:
        values.update(closure_requested=True, closure_reason="done")
    elif action is ObservationLifecycleDecision.REPLACE:
        values.update(replacement_requested=True, closure_reason="new context")
    elif action is ObservationLifecycleDecision.NO_ACTION:
        values["trigger_relation"] = ObservationTriggerRelation.DUPLICATE
    return evaluate_observation_boundary(ObservationBoundaryInput(**values))  # type: ignore[arg-type]


def execute(
    action: ObservationLifecycleDecision,
    episode: ObservationEpisode | None = None,
    *,
    incoming: ObservationRequest | None = None,
):
    execution_timestamp = (
        EXECUTED + timedelta(seconds=1)
        if action is ObservationLifecycleDecision.REPLACE
        else EXECUTED
    )
    return execute_observation_lifecycle(
        ObservationLifecycleExecutionInput(
            boundary(action, episode, incoming=incoming), episode, execution_timestamp
        )
    )


def opened(manager: WatchlistManager, symbol: str = "BTCUSDT"):
    result = execute(ObservationLifecycleDecision.OPEN, incoming=request(symbol))
    return manager.apply_observation_lifecycle_result(result), result


def add_episode_diagnostics(manager: WatchlistManager, episode: ObservationEpisode):
    manager.update(
        symbol=episode.symbol,
        exchange=episode.exchange,
        timeframe=episode.timeframe,
        timestamp=EXECUTED + timedelta(minutes=1),
        current_agent_state=AgentStateType.IGNITION,
        hypothesis_id="hypothesis-old",
        confidence=91,
        event_id="event-old",
    )
    entry = manager.get(
        symbol=episode.symbol, exchange=episode.exchange, timeframe=episode.timeframe
    )
    manager._entries[("bybit", episode.symbol, "5m")] = replace(  # noqa: SLF001
        entry, diagnostic_metadata={"episode_signal": "old"}
    )


class LifecycleApplicationTests(TestCase):
 def test_open_stores_active_unknown_episode_and_serializes(self) -> None:
    manager = WatchlistManager()
    entry, result = opened(manager)

    self.assertIs(entry.active_episode, result.newly_opened_episode)
    self.assertEqual(entry.active_episode_id, result.newly_opened_episode.episode_id)
    self.assertIs(entry.lifecycle_status, ObservationEpisodeStatus.ACTIVE)
    self.assertIs(entry.current_agent_state, AgentStateType.UNKNOWN)
    self.assertEqual(entry.observation_count, 0)
    self.assertIsNone(entry.latest_runtime_event_id)
    json.dumps(entry.to_dict())


 def test_continue_preserves_episode_and_analytical_state(self) -> None:
    manager = WatchlistManager()
    entry, _ = opened(manager)
    add_episode_diagnostics(manager, entry.active_episode)
    before = manager.get(symbol="BTCUSDT", exchange="bybit", timeframe="5m")

    continued = manager.apply_observation_lifecycle_result(
        execute(ObservationLifecycleDecision.CONTINUE, before.active_episode)
    )

    self.assertEqual(continued.active_episode_id, before.active_episode_id)
    self.assertEqual(continued.observation_count, before.observation_count)
    self.assertEqual(continued.observation_count, 1)
    self.assertEqual(continued.hypothesis_id, "hypothesis-old")
    self.assertEqual(continued.diagnostic_metadata, before.diagnostic_metadata)


 def test_close_removes_only_active_ownership_and_retains_completed(self) -> None:
    manager = WatchlistManager()
    entry, _ = opened(manager)
    add_episode_diagnostics(manager, entry.active_episode)
    before = manager.get(symbol="BTCUSDT", exchange="bybit", timeframe="5m")
    closed = manager.apply_observation_lifecycle_result(
        execute(ObservationLifecycleDecision.CLOSE, before.active_episode)
    )

    self.assertIsNone(closed.active_episode)
    self.assertIsNone(closed.active_episode_id)
    self.assertIs(closed.lifecycle_status, ObservationEpisodeStatus.CLOSED)
    self.assertEqual(closed.latest_completed_episode.episode_id, before.active_episode_id)
    self.assertEqual(closed.hypothesis_id, "hypothesis-old")


 def test_replace_is_atomic_isolated_and_preserves_market_scope(self) -> None:
    manager = WatchlistManager()
    entry, _ = opened(manager)
    add_episode_diagnostics(manager, entry.active_episode)
    before = manager.get(symbol="BTCUSDT", exchange="bybit", timeframe="5m")
    replacement = execute(ObservationLifecycleDecision.REPLACE, before.active_episode)

    after = manager.apply_observation_lifecycle_result(replacement)

    self.assertEqual(after.latest_completed_episode, replacement.closed_episode)
    self.assertEqual(after.active_episode, replacement.newly_opened_episode)
    self.assertNotEqual(after.active_episode_id, before.active_episode_id)
    self.assertEqual((after.exchange, after.symbol, after.timeframe), (
        before.exchange,
        before.symbol,
        before.timeframe,
    ))
    self.assertEqual(after.first_seen, before.first_seen)
    self.assertEqual(after.observation_count, 0)
    self.assertIs(after.current_agent_state, AgentStateType.UNKNOWN)
    self.assertIsNone(after.hypothesis_id)
    self.assertEqual(after.confidence, 0)
    self.assertIsNone(after.latest_runtime_event_id)
    self.assertEqual(dict(after.diagnostic_metadata), {})


 def test_no_action_is_exact_noop_and_deterministic(self) -> None:
    manager = WatchlistManager()
    entry, _ = opened(manager)
    result = execute(ObservationLifecycleDecision.NO_ACTION, entry.active_episode)

    first = manager.apply_observation_lifecycle_result(result)
    second = manager.apply_observation_lifecycle_result(result)

    self.assertIs(first, entry)
    self.assertIs(second, entry)
    self.assertEqual(manager.list_active(), (entry,))


 def test_markets_are_isolated_and_identity_mismatch_is_rejected(self) -> None:
    manager = WatchlistManager()
    btc, _ = opened(manager)
    eth, _ = opened(manager, "ETHUSDT")
    manager.apply_observation_lifecycle_result(
        execute(ObservationLifecycleDecision.CONTINUE, btc.active_episode)
    )
    self.assertEqual(manager.get(symbol="ETHUSDT", exchange="bybit", timeframe="5m"), eth)

    replacement = execute(ObservationLifecycleDecision.REPLACE, btc.active_episode)
    mismatched_new = replace(replacement.newly_opened_episode, symbol="ETHUSDT")
    mismatched = replace(
        replacement,
        resulting_active_episode=mismatched_new,
        newly_opened_episode=mismatched_new,
    )
    with self.assertRaisesRegex(ValueError, "mismatched market"):
        manager.apply_observation_lifecycle_result(mismatched)


 def test_duplicate_open_and_wrong_active_episode_are_rejected(self) -> None:
    manager = WatchlistManager()
    entry, _ = opened(manager)
    with self.assertRaisesRegex(ValueError, "overwrite"):
        manager.apply_observation_lifecycle_result(
            execute(ObservationLifecycleDecision.OPEN)
        )

    other = replace(entry.active_episode, episode_id="episode_other")
    with self.assertRaisesRegex(ValueError, "same active Episode"):
        manager.apply_observation_lifecycle_result(
            execute(ObservationLifecycleDecision.CONTINUE, other)
        )


 def test_closed_episode_cannot_be_active_and_inputs_are_immutable(self) -> None:
    manager = WatchlistManager()
    entry, _ = opened(manager)
    before = entry.active_episode.to_dict()
    result = execute(ObservationLifecycleDecision.CLOSE, entry.active_episode)
    manager.apply_observation_lifecycle_result(result)
    self.assertEqual(entry.active_episode.to_dict(), before)
    with self.assertRaises(FrozenInstanceError):
        entry.active_episode_id = "changed"  # type: ignore[misc]
    with self.assertRaisesRegex(ValueError, "closed Episode"):
        replace(
            entry,
            active_episode=result.closed_episode,
            active_episode_id=result.closed_episode.episode_id,
        )


 def test_apply_does_not_invoke_policy_or_executor(self) -> None:
    manager = WatchlistManager()
    result = execute(ObservationLifecycleDecision.OPEN)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("lifecycle decision was recalculated")

    with mock.patch(
        "pumpagent.runtime.domain.observation_policy.evaluate_observation_policy",
        forbidden,
    ), mock.patch(
        "pumpagent.runtime.modules.observation_lifecycle.executor.execute_observation_lifecycle",
        forbidden,
    ):
        self.assertIsNotNone(
            manager.apply_observation_lifecycle_result(result).active_episode
        )
