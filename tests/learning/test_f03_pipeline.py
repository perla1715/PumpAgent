from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pumpagent.learning.domain import (
    CaseStatus,
    CompletenessStatus,
    DatasetEligibility,
    OutcomeLabel,
    ReviewRecord,
)
from pumpagent.learning.cli import main as cli_main
from pumpagent.learning.export import export_jsonl_dataset
from pumpagent.learning.labels import LabelPolicyConfig, label_outcome
from pumpagent.learning.outcomes import (
    OutcomeAttributionError,
    OutcomeAttributionService,
    compute_outcome_record,
)
from pumpagent.learning.replay import (
    HistoricalReplayRunner,
    ReplayConfig,
)
from pumpagent.learning.readiness import LearningReadinessService
from pumpagent.learning.repository import (
    LearningCaseConflictError,
    SQLiteLearningCaseRepository,
)
from pumpagent.learning.service import (
    LearningCasePersistenceError,
    LearningCasePersistenceService,
)
from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.orchestrator import RuntimeOrchestrator
from tests.runtime.orchestrator.test_runtime_loop import make_snapshot


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def completed_event(timestamp: datetime = NOW):  # type: ignore[no-untyped-def]
    snapshot = replace(
        make_snapshot(),
        event_id=f"snapshot:{timestamp.isoformat()}",
        timestamp=timestamp,
    )
    return RuntimeOrchestrator(
        hypothesis_id_generator=lambda: f"hypothesis:{timestamp.isoformat()}"
    ).process_market_update(snapshot, episode_id="episode-learning")


def future_observations(
    *,
    minutes: int = 60,
    symbol: str = "BTCUSDT",
    exchange: str = "binance",
    timeframe: str = "1m",
    close_step: float = 0.1,
):  # type: ignore[no-untyped-def]
    return tuple(
        {
            "timestamp": NOW + timedelta(minutes=index),
            "symbol": symbol,
            "exchange": exchange,
            "timeframe": timeframe,
            "close": 101.0 + close_step * index,
            "high": 101.2 + close_step * index,
            "low": 100.8 + close_step * index,
            "volume": 60.0 + index,
        }
        for index in range(1, minutes + 1)
    )


class LearningPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "learning.sqlite3"
        self.repository = SQLiteLearningCaseRepository(self.path)
        self.repository.initialize()
        self.service = LearningCasePersistenceService(self.repository)
        self.event = completed_event()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_completed_event_persists_and_survives_restart(self) -> None:
        case = self.service.persist(self.event, ingestion_timestamp=NOW)
        reopened = SQLiteLearningCaseRepository(self.path)
        stored = reopened.get_case(case.case_id)

        self.assertEqual(stored, case)
        self.assertEqual(
            reopened.get_case_by_runtime_event_id(self.event.event_id),
            case,
        )
        self.assertEqual(stored.cycle_timestamp.tzinfo, timezone.utc)
        with self.assertRaises(TypeError):
            stored.runtime_event_payload["runtime_event"] = {}  # type: ignore[index]

    def test_non_completed_events_are_rejected(self) -> None:
        created = RuntimeEvent(
            event_id="created",
            schema_version="runtime_event_v2",
            cycle_timestamp=NOW,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )
        failed = RuntimeOrchestrator().process_market_update(
            make_snapshot(),
            episode_id="episode-learning",
            classification_timestamp=NOW + timedelta(days=1),
        )
        for event in (created, failed):
            with self.subTest(status=event.runtime_status), self.assertRaises(
                LearningCasePersistenceError
            ):
                self.service.persist(event, ingestion_timestamp=NOW)

    def test_case_writes_are_idempotent_and_conflicts_fail(self) -> None:
        case = self.service.persist(self.event, ingestion_timestamp=NOW)
        self.assertEqual(
            self.service.persist(self.event, ingestion_timestamp=NOW),
            case,
        )
        conflict = replace(case, ingestion_timestamp=NOW + timedelta(seconds=1))
        with self.assertRaises(LearningCaseConflictError):
            self.repository.store_case(conflict)
        self.assertEqual(len(self.repository.list_cases()), 1)

    def test_outcome_conflict_rolls_back(self) -> None:
        case = self.service.persist(self.event, ingestion_timestamp=NOW)
        record = compute_outcome_record(
            case, future_observations(), horizon_minutes=5
        )
        self.repository.attach_outcome(record)
        with self.assertRaises(LearningCaseConflictError):
            self.repository.attach_outcome(
                replace(record, close_to_close_return=0.999)
            )
        self.assertEqual(self.repository.list_outcomes(case.case_id), (record,))

    def test_outcome_attachment_rejects_forged_cycle_and_market_identity(
        self,
    ) -> None:
        case = self.service.persist(self.event, ingestion_timestamp=NOW)
        record = compute_outcome_record(
            case, future_observations(), horizon_minutes=5
        )
        with self.assertRaises(LearningCaseConflictError):
            self.repository.attach_outcome(
                replace(
                    record,
                    source_cycle_timestamp=NOW - timedelta(minutes=1),
                )
            )
        with self.assertRaises(LearningCaseConflictError):
            self.repository.attach_outcome(
                replace(
                    record,
                    outcome_id=f"{record.outcome_id}:late-boundary",
                    observation_end_timestamp=NOW + timedelta(hours=1),
                )
            )
        with self.assertRaises(LearningCaseConflictError):
            self.repository.attach_outcome(
                replace(
                    record,
                    source_data_identity={
                        "symbol": "ETHUSDT",
                        "exchange": "binance",
                        "timeframe": "1m",
                    },
                )
            )

    def test_review_is_separate_idempotent_record(self) -> None:
        case = self.service.persist(self.event, ingestion_timestamp=NOW)
        review = ReviewRecord(
            review_id=f"review:{case.case_id}",
            case_id=case.case_id,
            review_status="approved",
            annotation="Suitable for controlled research.",
            tags=("reviewed",),
            reviewed_by="human-reviewer",
            reviewed_at=NOW + timedelta(hours=1),
        )
        self.assertEqual(self.repository.record_review(review), review)
        self.assertEqual(self.repository.record_review(review), review)
        self.assertIs(
            self.repository.get_case(case.case_id).case_status,
            CaseStatus.REVIEWED,
        )


class OutcomeAttributionTests(TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.repository = SQLiteLearningCaseRepository(
            Path(self.temp.name) / "learning.sqlite3"
        )
        self.repository.initialize()
        self.case = LearningCasePersistenceService(self.repository).persist(
            completed_event(),
            ingestion_timestamp=NOW,
            provenance={
                "source": "test_ingestion",
                "runtime_version": "526e72f",
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_all_configured_complete_horizons_and_case_eligibility(self) -> None:
        service = OutcomeAttributionService(self.repository)
        records = tuple(
            service.attribute(
                self.case,
                future_observations(),
                horizon_minutes=horizon,
                creation_timestamp=NOW + timedelta(minutes=horizon),
            )
            for horizon in (5, 15, 30, 60)
        )

        self.assertTrue(all(item.window_complete for item in records))
        self.assertEqual(
            self.repository.get_case(self.case.case_id).case_status,
            CaseStatus.OUTCOME_COMPLETE,
        )
        self.assertEqual(
            self.repository.get_case(self.case.case_id).dataset_eligibility,
            DatasetEligibility.ELIGIBLE,
        )

    def test_incomplete_missing_duplicate_and_out_of_order_data(self) -> None:
        incomplete = compute_outcome_record(
            self.case, future_observations(minutes=4), horizon_minutes=5
        )
        self.assertIs(
            incomplete.completeness_status,
            CompletenessStatus.INCOMPLETE,
        )
        missing = future_observations(minutes=5)[:2] + future_observations(minutes=5)[3:]
        self.assertFalse(
            compute_outcome_record(
                self.case, missing, horizon_minutes=5
            ).window_complete
        )
        duplicate = future_observations(minutes=2) + future_observations(minutes=2)[-1:]
        with self.assertRaisesRegex(OutcomeAttributionError, "Duplicate"):
            compute_outcome_record(self.case, duplicate, horizon_minutes=5)
        with self.assertRaisesRegex(OutcomeAttributionError, "chronological"):
            compute_outcome_record(
                self.case,
                tuple(reversed(future_observations(minutes=2))),
                horizon_minutes=5,
            )

    def test_identity_and_leakage_boundaries(self) -> None:
        cases = (
            future_observations(symbol="ETHUSDT"),
            future_observations(exchange="bybit"),
            future_observations(timeframe="5m"),
        )
        for observations in cases:
            with self.subTest(), self.assertRaises(OutcomeAttributionError):
                compute_outcome_record(
                    self.case, observations, horizon_minutes=5
                )
        past = dict(future_observations(minutes=1)[0])
        past["timestamp"] = NOW
        with self.assertRaisesRegex(OutcomeAttributionError, "strictly after"):
            compute_outcome_record(self.case, (past,), horizon_minutes=5)

    def test_identical_outcome_retry_and_conflict(self) -> None:
        service = OutcomeAttributionService(self.repository)
        first = service.attribute(
            self.case,
            future_observations(),
            horizon_minutes=5,
            creation_timestamp=NOW + timedelta(minutes=5),
        )
        self.assertEqual(
            service.attribute(
                self.case,
                future_observations(),
                horizon_minutes=5,
                creation_timestamp=NOW + timedelta(minutes=5),
            ),
            first,
        )
        with self.assertRaises(LearningCaseConflictError):
            service.attribute(
                self.case,
                future_observations(close_step=0.2),
                horizon_minutes=5,
                creation_timestamp=NOW + timedelta(minutes=5),
            )

    def test_partial_outcome_can_complete_later_without_overwrite(self) -> None:
        service = OutcomeAttributionService(self.repository)
        partial = service.attribute(
            self.case,
            future_observations(minutes=4),
            horizon_minutes=5,
            creation_timestamp=NOW + timedelta(minutes=4),
        )
        completed = service.attribute(
            self.case,
            future_observations(minutes=5),
            horizon_minutes=5,
            creation_timestamp=NOW + timedelta(minutes=5),
        )

        self.assertIs(
            partial.completeness_status,
            CompletenessStatus.INCOMPLETE,
        )
        self.assertIs(
            completed.completeness_status,
            CompletenessStatus.COMPLETE,
        )
        self.assertNotEqual(partial.outcome_id, completed.outcome_id)
        self.assertEqual(
            self.repository.list_outcomes(self.case.case_id),
            (completed,),
        )


class LabelsAndExportTests(TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.repository = SQLiteLearningCaseRepository(
            Path(self.temp.name) / "learning.sqlite3"
        )
        self.repository.initialize()
        self.case = LearningCasePersistenceService(self.repository).persist(
            completed_event(),
            ingestion_timestamp=NOW,
            provenance={
                "source": "test_ingestion",
                "runtime_version": "526e72f",
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_label_thresholds_and_incomplete_label(self) -> None:
        base = compute_outcome_record(
            self.case, future_observations(), horizon_minutes=5
        )
        config = LabelPolicyConfig(
            continuation_return=0.03,
            excursion_trigger=0.03,
            failure_close_ceiling=0.0,
            recovery_close_floor=0.0,
        )
        cases = (
            (
                replace(
                    base,
                    close_to_close_return=0.03,
                    maximum_high_return=0.03,
                    minimum_low_return=0.0,
                ),
                OutcomeLabel.PUMP_CONTINUATION,
            ),
            (
                replace(
                    base,
                    close_to_close_return=-0.03,
                    maximum_high_return=0.0,
                    minimum_low_return=-0.03,
                ),
                OutcomeLabel.DUMP_CONTINUATION,
            ),
            (
                replace(
                    base,
                    close_to_close_return=0.0,
                    maximum_high_return=0.03,
                    minimum_low_return=-0.001,
                ),
                OutcomeLabel.PUMP_FAILURE,
            ),
            (
                replace(
                    base,
                    close_to_close_return=0.0,
                    maximum_high_return=0.001,
                    minimum_low_return=-0.03,
                ),
                OutcomeLabel.DUMP_RECOVERY,
            ),
        )
        for outcome, expected in cases:
            self.assertIs(label_outcome(outcome, config).label, expected)
        incomplete = replace(
            base,
            window_complete=False,
            completeness_status=CompletenessStatus.INCOMPLETE,
        )
        self.assertIs(
            label_outcome(incomplete, config).label,
            OutcomeLabel.INSUFFICIENT_OUTCOME,
        )

    def test_export_is_deterministic_and_excludes_pending_cases(self) -> None:
        output = Path(self.temp.name) / "dataset.jsonl"
        empty = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=60,
        )
        self.assertEqual(empty["case_count"], 0)
        service = OutcomeAttributionService(self.repository)
        for horizon in (5, 15, 30, 60):
            service.attribute(
                self.case,
                future_observations(),
                horizon_minutes=horizon,
                creation_timestamp=NOW + timedelta(minutes=horizon),
            )
        self.repository.record_review(
            ReviewRecord(
                review_id=f"review:{self.case.case_id}:export",
                case_id=self.case.case_id,
                review_status="approved",
                annotation=None,
                tags=(),
                reviewed_by="export-reviewer",
                reviewed_at=NOW + timedelta(hours=2),
            )
        )
        LearningReadinessService(self.repository).assess(self.case.case_id)
        first = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=60,
        )
        content = output.read_text(encoding="utf-8")
        second = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=60,
        )
        self.assertEqual(first, second)
        self.assertEqual(content, output.read_text(encoding="utf-8"))
        self.assertEqual(first["case_count"], 1)
        self.assertEqual(first["included_case_ids"], [self.case.case_id])


class ReplayTests(TestCase):
    def test_replay_is_chronological_deterministic_and_resumable(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "learning.sqlite3"
            repository = SQLiteLearningCaseRepository(path)
            repository.initialize()
            snapshots = tuple(
                replace(
                    make_snapshot(),
                    event_id=f"replay-snapshot-{index}",
                    timestamp=NOW + timedelta(minutes=5 * index),
                    timeframe="5m",
                    price=101.0 + index,
                )
                for index in range(13)
            )
            config = ReplayConfig(
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="5m",
                runtime_version="526e72f",
            )
            first = HistoricalReplayRunner(repository).run(snapshots, config)
            case_count = len(repository.list_cases())
            second = HistoricalReplayRunner(repository).run(snapshots, config)

            self.assertEqual(first.completed_cycles, 13)
            self.assertEqual(first.stored_cases, 13)
            self.assertEqual(len(repository.list_cases()), case_count)
            self.assertEqual(second.completed_cycles, 13)
            self.assertEqual(repository.integrity_check(), (True, ()))
            with self.assertRaisesRegex(ValueError, "chronological"):
                HistoricalReplayRunner(repository).run(
                    tuple(reversed(snapshots)), config
                )

    def test_cli_initialization_counts_and_integrity(self) -> None:
        with TemporaryDirectory() as directory:
            store = str(Path(directory) / "cli.sqlite3")
            self.assertEqual(cli_main(["--store", store, "init"]), 0)
            self.assertEqual(cli_main(["--store", store, "counts"]), 0)
            self.assertEqual(cli_main(["--store", store, "validate"]), 0)

    def test_replay_rejects_future_ohlcv_before_runtime_execution(self) -> None:
        with TemporaryDirectory() as directory:
            repository = SQLiteLearningCaseRepository(
                Path(directory) / "learning.sqlite3"
            )
            repository.initialize()
            snapshot = replace(
                make_snapshot(),
                timestamp=NOW,
                ohlcv=(
                    {
                        "timestamp": NOW + timedelta(minutes=1),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 10.0,
                    },
                ),
            )
            with self.assertRaisesRegex(ValueError, "future OHLCV"):
                HistoricalReplayRunner(repository).run(
                    (snapshot,),
                    ReplayConfig(
                        symbol="BTCUSDT",
                        exchange="binance",
                        timeframe="1m",
                        runtime_version="526e72f",
                    ),
                )
            self.assertEqual(repository.list_cases(), ())

    def test_replay_rejects_out_of_order_and_non_finite_ohlcv(self) -> None:
        with TemporaryDirectory() as directory:
            repository = SQLiteLearningCaseRepository(
                Path(directory) / "learning.sqlite3"
            )
            repository.initialize()
            first = dict(make_snapshot().ohlcv[0])
            second = dict(make_snapshot().ohlcv[1])
            first["timestamp"] = NOW
            second["timestamp"] = NOW - timedelta(minutes=1)
            invalid_values = (
                ("out-of-order", (first, second)),
                (
                    "nan",
                    ({**first, "close": float("nan")},),
                ),
                (
                    "positive infinity",
                    ({**first, "close": float("inf")},),
                ),
                (
                    "negative infinity",
                    ({**first, "close": float("-inf")},),
                ),
            )
            for label, candles in invalid_values:
                with self.subTest(label=label), self.assertRaises(ValueError):
                    HistoricalReplayRunner(repository).run(
                        (
                            replace(
                                make_snapshot(),
                                timestamp=NOW,
                                ohlcv=candles,
                            ),
                        ),
                        ReplayConfig(
                            symbol="BTCUSDT",
                            exchange="binance",
                            timeframe="1m",
                            runtime_version="526e72f",
                        ),
                    )
            self.assertEqual(repository.list_cases(), ())

    def test_replay_outcome_completion_is_scoped_to_current_run_cases(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            repository = SQLiteLearningCaseRepository(
                Path(directory) / "learning.sqlite3"
            )
            repository.initialize()
            config = ReplayConfig(
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="5m",
                runtime_version="526e72f",
            )
            first = tuple(
                replace(
                    make_snapshot(),
                    event_id=f"first-{index}",
                    timestamp=NOW + timedelta(minutes=5 * index),
                    timeframe="5m",
                    price=101.0 + index,
                )
                for index in range(13)
            )
            HistoricalReplayRunner(repository).run(first, config)
            first_ids = tuple(case.case_id for case in repository.list_cases())
            before = {
                case_id: repository.list_outcomes(case_id)
                for case_id in first_ids
            }
            second = tuple(
                replace(
                    make_snapshot(),
                    event_id=f"second-{index}",
                    timestamp=NOW + timedelta(hours=2, minutes=5 * index),
                    timeframe="5m",
                    price=201.0 + index,
                )
                for index in range(13)
            )
            HistoricalReplayRunner(repository).run(second, config)
            self.assertEqual(
                {
                    case_id: repository.list_outcomes(case_id)
                    for case_id in first_ids
                },
                before,
            )
