from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, mock

from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.orchestrator import (
    RuntimeOrchestrator,
    run_fixture_market_data_cycle,
    serialize_runtime_event,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
CYCLE_TIMESTAMP = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def fixture_snapshot():  # type: ignore[no-untyped-def]
    return run_fixture_market_data_cycle(
        event_id="temporal-input-event",
        cycle_timestamp=CYCLE_TIMESTAMP,
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        fixture_path=FIXTURE,
    ).market_snapshot


class RuntimeEventTemporalIdentityTests(TestCase):
    def test_future_classification_timestamp_cannot_complete(self) -> None:
        snapshot = fixture_snapshot()

        event = RuntimeOrchestrator().process_market_update(
            snapshot,
            episode_id="episode-temporal",
            classification_timestamp=snapshot.timestamp + timedelta(days=365),
        )

        self.assertIs(event.runtime_status, RuntimeStatus.FAILED)
        self.assertIsNone(event.process_evidence)
        self.assertIsNone(event.decision_assessment)
        self.assertEqual(
            serialize_runtime_event(event)["runtime_event"]["runtime_status"],
            RuntimeStatus.FAILED.value,
        )

    def test_past_classification_timestamp_cannot_complete(self) -> None:
        snapshot = fixture_snapshot()

        event = RuntimeOrchestrator().process_market_update(
            snapshot,
            episode_id="episode-temporal",
            classification_timestamp=snapshot.timestamp - timedelta(days=365),
        )

        self.assertIs(event.runtime_status, RuntimeStatus.FAILED)
        self.assertIsNone(event.process_evidence)
        self.assertIsNone(event.decision_assessment)

    def test_valid_supported_path_uses_one_canonical_timestamp(self) -> None:
        snapshot = fixture_snapshot()
        runtime = RuntimeOrchestrator()

        with mock.patch.object(
            runtime,
            "_process_market_update",
            wraps=runtime._process_market_update,  # noqa: SLF001
        ) as execute:
            event = runtime.process_market_update(
                snapshot,
                episode_id="episode-temporal",
                classification_timestamp=snapshot.timestamp,
            )

        execute.assert_called_once()
        self.assertIs(event.runtime_status, RuntimeStatus.COMPLETED)
        self.assertEqual(event.cycle_timestamp, snapshot.timestamp)
        self.assertEqual(
            event.observation_package.observation_timestamp,
            snapshot.timestamp,
        )
        self.assertEqual(
            event.process_evidence.observation_timestamp,
            snapshot.timestamp,
        )
        self.assertEqual(
            event.process_quality_assessment.current_observation.observation_timestamp,
            snapshot.timestamp,
        )
        self.assertEqual(
            event.scenario_probability.observation_timestamp,
            snapshot.timestamp,
        )
        self.assertEqual(event.scenario_probability.created_at, snapshot.timestamp)
        self.assertEqual(event.decision_assessment.created_at, snapshot.timestamp)
        self.assertEqual(
            serialize_runtime_event(event)["runtime_event"]["runtime_status"],
            RuntimeStatus.COMPLETED.value,
        )

    def test_serialization_rejects_forged_completed_timestamp(self) -> None:
        snapshot = fixture_snapshot()
        event = RuntimeOrchestrator().process_market_update(
            snapshot,
            episode_id="episode-temporal",
        )
        forged_observation = replace(
            event.observation_package,
            observation_timestamp=snapshot.timestamp + timedelta(minutes=1),
        )
        forged_event = copy.copy(event)
        object.__setattr__(
            forged_event,
            "observation_package",
            forged_observation,
        )

        with self.assertRaisesRegex(ValueError, "ObservationPackage timestamp"):
            serialize_runtime_event(forged_event)
