"""Scanner-to-Observation Lifecycle orchestration tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from pumpagent.runtime.adapters.scanner_observation import (
    ScannerAttentionDecision,
    ScannerTriggerReason,
)
from pumpagent.runtime.domain.enums import AgentStateType, ObservationLifecycleDecision
from pumpagent.runtime.modules.observation_lifecycle.orchestrator import (
    ExplicitLifecycleCommand,
    ScannerObservationOrchestrationInput,
    ScannerObservationOrchestrationStatus,
    process_scanner_observation_request,
)
from pumpagent.runtime.modules.watchlist.manager import WatchlistManager


BUCKET = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
REQUESTED = BUCKET + timedelta(seconds=10)


def scanner_result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "VALID",
        "exchange": "bybit",
        "symbol": "BTCUSDT.6",
        "timeframe": "5m",
        "timestamp_bucket": BUCKET,
        "data_quality": {"closed_candles_only": True, "ohlcv_oi_aligned": True},
        "metrics": {
            "price_5m_pct": 1.2,
            "volume_ratio_5m": 2.3,
            "oi_change_5m_pct": 0.7,
        },
    }
    result.update(overrides)
    return result


def orchestration_input(
    result: object | None = None, **overrides: object
) -> ScannerObservationOrchestrationInput:
    values: dict[str, object] = {
        "scanner_result": result if result is not None else scanner_result(),
        "attention_decision": ScannerAttentionDecision(
            True, (ScannerTriggerReason.VOLUME_SPIKE,)
        ),
        "request_timestamp": REQUESTED,
    }
    values.update(overrides)
    return ScannerObservationOrchestrationInput(**values)  # type: ignore[arg-type]


def run(manager: WatchlistManager, result: object | None = None, **overrides: object):
    return process_scanner_observation_request(
        orchestration_input(result, **overrides), manager
    )


class ScannerObservationOrchestratorTests(unittest.TestCase):
    def test_open_stores_unknown_episode_at_zero_cycles(self) -> None:
        manager = WatchlistManager()
        result = run(manager)
        entry = result.resulting_watchlist_entry
        self.assertEqual(result.status, ScannerObservationOrchestrationStatus.COMPLETED)
        self.assertEqual(result.lifecycle_action, ObservationLifecycleDecision.OPEN)
        self.assertTrue(result.watchlist_state_changed)
        self.assertIs(entry, manager.get(symbol="BTCUSDT", exchange="bybit", timeframe="5m"))
        self.assertEqual(entry.current_agent_state, AgentStateType.UNKNOWN)
        self.assertEqual(entry.observation_count, 0)
        self.assertEqual(entry.active_episode.observation_cycle_count, 0)

    def test_newer_continues_same_episode_without_increment(self) -> None:
        manager = WatchlistManager()
        opened = run(manager)
        episode_id = opened.resulting_watchlist_entry.active_episode_id
        newer = scanner_result(timestamp_bucket=BUCKET + timedelta(minutes=5))
        result = run(manager, newer, request_timestamp=REQUESTED + timedelta(minutes=5))
        self.assertEqual(result.lifecycle_action, ObservationLifecycleDecision.CONTINUE)
        self.assertEqual(result.resulting_watchlist_entry.active_episode_id, episode_id)
        self.assertEqual(result.resulting_watchlist_entry.observation_count, 0)
        before_duplicate = manager.list_active()
        duplicate = run(
            manager, newer, request_timestamp=REQUESTED + timedelta(minutes=5)
        )
        self.assertEqual(duplicate.lifecycle_action, ObservationLifecycleDecision.NO_ACTION)
        self.assertFalse(duplicate.watchlist_state_changed)
        self.assertEqual(manager.list_active(), before_duplicate)

    def test_duplicate_and_older_are_no_action_without_mutation(self) -> None:
        for trigger in (BUCKET, BUCKET - timedelta(minutes=5)):
            with self.subTest(trigger=trigger):
                manager = WatchlistManager()
                run(manager)
                before = manager.list_active()
                result = run(manager, scanner_result(timestamp_bucket=trigger))
                self.assertEqual(result.lifecycle_action, ObservationLifecycleDecision.NO_ACTION)
                self.assertFalse(result.watchlist_state_changed)
                self.assertEqual(manager.list_active(), before)

    def test_adapter_stops_skipped_failed_ineligible_and_malformed(self) -> None:
        cases = (
            (scanner_result(status="SKIPPED"), ScannerAttentionDecision(False)),
            (scanner_result(status="FAILED"), ScannerAttentionDecision(False)),
            (scanner_result(), ScannerAttentionDecision(False)),
            (scanner_result(data_quality={"closed_candles_only": True, "ohlcv_oi_aligned": False}), ScannerAttentionDecision(True, (ScannerTriggerReason.OI_GROWTH,))),
            (scanner_result(symbol=""), ScannerAttentionDecision(True, (ScannerTriggerReason.OI_GROWTH,))),
        )
        for source, attention in cases:
            with self.subTest(status=source.get("status"), attention=attention.eligible):
                manager = WatchlistManager()
                result = run(manager, source, attention_decision=attention)
                self.assertEqual(result.status, ScannerObservationOrchestrationStatus.ADAPTER_STOPPED)
                self.assertIsNone(result.watchlist_context)
                self.assertIsNone(result.policy_decision)
                self.assertIsNone(result.lifecycle_execution_result)
                self.assertEqual(manager.list_active(), ())

    def test_explicit_close_retains_completed_metadata(self) -> None:
        manager = WatchlistManager()
        old_id = run(manager).resulting_watchlist_entry.active_episode_id
        result = run(
            manager,
            lifecycle_command=ExplicitLifecycleCommand.CLOSE,
            closure_reason="operator close",
        )
        entry = result.resulting_watchlist_entry
        self.assertEqual(result.lifecycle_action, ObservationLifecycleDecision.CLOSE)
        self.assertIsNone(entry.active_episode)
        self.assertEqual(entry.latest_completed_episode.episode_id, old_id)

    def test_explicit_replace_resets_scope_and_assigns_trigger_only_to_new(self) -> None:
        manager = WatchlistManager()
        opened = run(manager).resulting_watchlist_entry
        # Seed Episode-scoped analytical references through the existing immutable entry.
        key = ("bybit", "BTCUSDT", "5m")
        manager._entries[key] = replace(  # type: ignore[attr-defined]
            opened, hypothesis_id="old", confidence=88, event_id="old-event"
        )
        replacement_source = scanner_result(timestamp_bucket=BUCKET + timedelta(minutes=5))
        result = run(
            manager,
            replacement_source,
            request_timestamp=REQUESTED + timedelta(minutes=5),
            lifecycle_command=ExplicitLifecycleCommand.REPLACE,
            closure_reason="new episode",
        )
        entry = result.resulting_watchlist_entry
        execution = result.lifecycle_execution_result
        self.assertEqual(result.lifecycle_action, ObservationLifecycleDecision.REPLACE)
        self.assertNotEqual(entry.active_episode_id, opened.active_episode_id)
        self.assertEqual(entry.observation_count, 0)
        self.assertEqual(entry.current_agent_state, AgentStateType.UNKNOWN)
        self.assertIsNone(entry.hypothesis_id)
        self.assertIsNone(entry.event_id)
        self.assertTrue(execution.replacement_trigger_belongs_only_to_new_episode)
        self.assertEqual(execution.closed_episode.trigger_reasons, opened.active_episode.trigger_reasons)
        self.assertEqual(execution.newly_opened_episode.scanner_trigger_timestamp, BUCKET + timedelta(minutes=5))

    def test_unrelated_market_is_unchanged(self) -> None:
        manager = WatchlistManager()
        run(manager, scanner_result(symbol="ETHUSDT.6"))
        eth = manager.get(symbol="ETHUSDT", exchange="bybit", timeframe="5m")
        run(manager)
        self.assertIs(manager.get(symbol="ETHUSDT", exchange="bybit", timeframe="5m"), eth)

    def test_executor_validation_failure_is_atomic(self) -> None:
        manager = WatchlistManager()
        run(manager)
        before = manager.list_active()
        with patch(
            "pumpagent.runtime.modules.observation_lifecycle.orchestrator.ObservationLifecycleExecutionInput",
            side_effect=ValueError("forced executor validation failure"),
        ):
            result = run(manager, scanner_result(timestamp_bucket=BUCKET + timedelta(minutes=5)), request_timestamp=REQUESTED + timedelta(minutes=5))
        self.assertEqual(result.status, ScannerObservationOrchestrationStatus.EXECUTION_FAILED)
        self.assertEqual(manager.list_active(), before)

    def test_market_identity_mismatch_is_atomic(self) -> None:
        manager = WatchlistManager()
        run(manager)
        before = manager.list_active()
        with patch(
            "pumpagent.runtime.modules.observation_lifecycle.orchestrator.build_watchlist_observation_context",
            side_effect=ValueError("Active Episode market identity must match the Watchlist entry."),
        ):
            result = run(manager, scanner_result(timestamp_bucket=BUCKET + timedelta(minutes=5)), request_timestamp=REQUESTED + timedelta(minutes=5))
        self.assertEqual(result.status, ScannerObservationOrchestrationStatus.PREPARATION_FAILED)
        self.assertEqual(manager.list_active(), before)

    def test_deterministic_immutable_serializable_and_runtime_disconnected(self) -> None:
        first = run(WatchlistManager())
        second = run(WatchlistManager())
        self.assertEqual(first, second)
        json.dumps(first.to_dict())
        with self.assertRaises(FrozenInstanceError):
            first.watchlist_state_changed = False  # type: ignore[misc]
        with patch(
            "pumpagent.runtime.orchestrator.runtime_loop.RuntimeOrchestrator",
            side_effect=AssertionError("Runtime must not be invoked"),
        ):
            self.assertEqual(run(WatchlistManager()).lifecycle_action, ObservationLifecycleDecision.OPEN)

    def test_analytical_modules_are_not_imported_or_evaluated(self) -> None:
        # The coordinator's module dependencies are lifecycle-only; Scanner metrics
        # are transported by the adapter and never interpreted here.
        import pumpagent.runtime.modules.observation_lifecycle.orchestrator as module

        source_names = set(module.__dict__)
        for forbidden in ("ProcessEngine", "HypothesisEngine", "Confidence", "Recommendation"):
            self.assertNotIn(forbidden, source_names)


if __name__ == "__main__":
    unittest.main()
