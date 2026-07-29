from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pumpagent.learning.domain import (
    CaseStatus,
    DatasetEligibility,
    LearningReadinessStatus,
    ReviewRecord,
)
from pumpagent.learning.cli import main as cli_main
from pumpagent.learning.export import export_jsonl_dataset
from pumpagent.learning.outcomes import OutcomeAttributionService
from pumpagent.learning.readiness import LearningReadinessService
from pumpagent.learning.repository import (
    LearningCaseConflictError,
    SQLiteLearningCaseRepository,
)
from pumpagent.learning.service import LearningCasePersistenceService
from tests.learning.test_f03_pipeline import (
    NOW,
    completed_event,
    future_observations,
)


class LearningReadinessTests(TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "learning.sqlite3"
        self.repository = SQLiteLearningCaseRepository(self.path)
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

    def _attach_outcome(self, minutes: int = 60) -> None:
        OutcomeAttributionService(self.repository).attribute(
            self.case,
            future_observations(minutes=minutes),
            horizon_minutes=60,
            creation_timestamp=NOW + timedelta(minutes=minutes),
        )

    def test_complete_valid_case_is_learning_ready_and_idempotent(self) -> None:
        self._attach_outcome()
        service = LearningReadinessService(self.repository)
        first = service.assess(self.case.case_id)
        second = service.assess(self.case.case_id)

        self.assertIs(
            first.readiness_status,
            LearningReadinessStatus.LEARNING_READY,
        )
        self.assertTrue(first.technically_ready)
        self.assertTrue(first.approved_for_evaluation)
        self.assertFalse(first.approved_for_training)
        self.assertEqual(first, second)
        self.assertEqual(
            len(self.repository.list_readiness_assessments(self.case.case_id)),
            1,
        )
        with self.assertRaisesRegex(ValueError, "Non-canonical"):
            replace(first, assessment_id="forged-readiness-id")

    def test_missing_and_incomplete_outcomes_are_not_ready(self) -> None:
        missing = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        self.assertIs(
            missing.readiness_status, LearningReadinessStatus.NOT_READY
        )
        self._attach_outcome(minutes=59)
        incomplete = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        self.assertIs(
            incomplete.readiness_status, LearningReadinessStatus.NOT_READY
        )

    def test_changed_validator_creates_immutable_history_and_survives_restart(
        self,
    ) -> None:
        self._attach_outcome()
        first = LearningReadinessService(
            self.repository, validator_version="validator-v1"
        ).assess(self.case.case_id)
        second = LearningReadinessService(
            self.repository, validator_version="validator-v2"
        ).assess(self.case.case_id)
        reopened = SQLiteLearningCaseRepository(self.path)

        self.assertNotEqual(first.assessment_id, second.assessment_id)
        self.assertEqual(
            reopened.latest_readiness_assessment(self.case.case_id), second
        )
        self.assertEqual(
            len(reopened.list_readiness_assessments(self.case.case_id)), 2
        )
        with self.assertRaises(LearningCaseConflictError):
            reopened.store_readiness_assessment(
                replace(first, warnings=("conflicting retry",))
            )

    def test_digest_mismatch_and_invalid_runtime_are_invalid(self) -> None:
        self._attach_outcome()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "UPDATE learning_cases SET payload_digest = ? WHERE case_id = ?",
                ("forged", self.case.case_id),
            )
        assessment = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        self.assertIs(
            assessment.readiness_status, LearningReadinessStatus.INVALID
        )
        self.assertIn("canonical_payload_digest", " ".join(assessment.failure_reasons))

    def test_non_finite_runtime_value_is_invalid(self) -> None:
        self._attach_outcome()
        with sqlite3.connect(self.path) as connection:
            payload = json.loads(
                connection.execute(
                    "SELECT payload FROM learning_cases WHERE case_id = ?",
                    (self.case.case_id,),
                ).fetchone()[0]
            )
            payload["runtime_event_payload"]["runtime_event"]["market_snapshot"][
                "optional_market_metrics"
            ]["forged"] = float("nan")
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            import hashlib

            connection.execute(
                "UPDATE learning_cases SET payload = ?, payload_digest = ? "
                "WHERE case_id = ?",
                (
                    raw,
                    hashlib.sha256(raw.encode()).hexdigest(),
                    self.case.case_id,
                ),
            )
        assessment = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        self.assertIs(
            assessment.readiness_status, LearningReadinessStatus.INVALID
        )

    def test_export_requires_persisted_named_policy_assessment(self) -> None:
        self._attach_outcome()
        output = Path(self.temp.name) / "dataset.jsonl"
        missing = export_jsonl_dataset(
            self.repository, output, runtime_version="526e72f"
        )
        self.assertEqual(missing["case_count"], 0)
        self.assertEqual(
            missing["exclusions_by_reason"],
            {"missing_readiness_assessment": 1},
        )
        LearningReadinessService(self.repository).assess(self.case.case_id)
        evaluation = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            readiness_policy="evaluation",
        )
        training = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            readiness_policy="training",
        )
        self.assertEqual(evaluation["case_count"], 1)
        self.assertEqual(training["case_count"], 0)
        self.assertEqual(
            training["exclusions_by_reason"],
            {"training_review_not_approved": 1},
        )

    def test_human_approval_creates_new_training_eligible_assessment(
        self,
    ) -> None:
        self._attach_outcome()
        service = LearningReadinessService(self.repository)
        pending = service.assess(self.case.case_id)
        self.repository.record_review(
            ReviewRecord(
                review_id=f"review:{self.case.case_id}",
                case_id=self.case.case_id,
                review_status="approved",
                annotation="Approved for controlled offline training.",
                tags=("training-approved",),
                reviewed_by="human-reviewer",
                reviewed_at=NOW + timedelta(hours=2),
            )
        )
        approved = service.assess(self.case.case_id)
        self.assertNotEqual(pending.assessment_id, approved.assessment_id)
        self.assertTrue(approved.technically_ready)
        self.assertTrue(approved.approved_for_training)

    def test_manual_exclusion_blocks_both_policies(self) -> None:
        other_path = Path(self.temp.name) / "excluded.sqlite3"
        repository = SQLiteLearningCaseRepository(other_path)
        repository.initialize()
        excluded = replace(
            self.case,
            case_status=CaseStatus.EXCLUDED,
            dataset_eligibility=DatasetEligibility.EXCLUDED,
            exclusion_reasons=("human_excluded",),
        )
        repository.store_case(excluded)
        OutcomeAttributionService(repository).attribute(
            excluded,
            future_observations(),
            horizon_minutes=60,
            creation_timestamp=NOW + timedelta(minutes=60),
        )
        assessment = LearningReadinessService(repository).assess(
            excluded.case_id
        )
        self.assertTrue(assessment.technically_ready)
        self.assertTrue(assessment.manually_excluded)
        self.assertFalse(assessment.approved_for_evaluation)
        self.assertFalse(assessment.approved_for_training)

    def test_mixed_market_assessments_remain_case_scoped(self) -> None:
        self._attach_outcome()
        ready = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        other = LearningCasePersistenceService(self.repository).persist(
            completed_event(NOW + timedelta(hours=2)),
            ingestion_timestamp=NOW + timedelta(hours=2),
            provenance={
                "source": "test_ingestion",
                "runtime_version": "526e72f",
            },
        )
        not_ready = LearningReadinessService(self.repository).assess(
            other.case_id
        )
        self.assertIs(
            ready.readiness_status, LearningReadinessStatus.LEARNING_READY
        )
        self.assertIs(
            not_ready.readiness_status, LearningReadinessStatus.NOT_READY
        )
        self.assertEqual(
            self.repository.list_cases_by_readiness_status(
                LearningReadinessStatus.LEARNING_READY
            ),
            (self.repository.get_case(self.case.case_id),),
        )

    def test_readiness_cli_assesses_counts_explains_and_exports(self) -> None:
        self._attach_outcome()
        store = str(self.path)
        self.assertEqual(
            cli_main(
                [
                    "--store",
                    store,
                    "assess-readiness",
                    "--case-id",
                    self.case.case_id,
                ]
            ),
            0,
        )
        self.assertEqual(
            cli_main(["--store", store, "readiness-counts"]), 0
        )
        self.assertEqual(
            cli_main(
                [
                    "--store",
                    store,
                    "explain-readiness",
                    "--case-id",
                    self.case.case_id,
                ]
            ),
            0,
        )
        self.assertEqual(
            cli_main(
                [
                    "--store",
                    store,
                    "export",
                    "--output",
                    str(Path(self.temp.name) / "cli-dataset.jsonl"),
                    "--runtime-version",
                    "526e72f",
                    "--policy",
                    "evaluation",
                ]
            ),
            0,
        )
