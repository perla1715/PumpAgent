from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import fields, replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pumpagent.learning.domain import (
    CaseStatus,
    DatasetEligibility,
    LearningReadinessStatus,
    ReviewRecord,
    build_readiness_assessment_id,
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

    def test_coherent_public_forgery_is_rejected_by_repository(self) -> None:
        self._attach_outcome()
        authentic = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        forged_outcome_id = "nonexistent-outcome"
        forged_id = build_readiness_assessment_id(
            case_id=authentic.case_id,
            runtime_event_id=authentic.runtime_event_id,
            validator_version=authentic.validator_version,
            canonical_payload_digest=authentic.canonical_payload_digest,
            outcome_record_id=forged_outcome_id,
            label_policy_version=authentic.label_policy_version,
            review_status=authentic.review_status,
            manually_excluded=authentic.manually_excluded,
            administratively_blocked=authentic.administratively_blocked,
            provenance=authentic.provenance,
        )
        forged = replace(
            authentic,
            assessment_id=forged_id,
            outcome_record_id=forged_outcome_id,
        )
        with self.assertRaisesRegex(
            LearningCaseConflictError, "does not authenticate"
        ):
            self.repository.store_readiness_assessment(forged)
        self.assertEqual(
            self.repository.list_readiness_assessments(self.case.case_id),
            (authentic,),
        )
        unsupported_id = build_readiness_assessment_id(
            case_id=authentic.case_id,
            runtime_event_id=authentic.runtime_event_id,
            validator_version="unsupported-validator",
            canonical_payload_digest=authentic.canonical_payload_digest,
            outcome_record_id=authentic.outcome_record_id,
            label_policy_version=authentic.label_policy_version,
            review_status=authentic.review_status,
            manually_excluded=authentic.manually_excluded,
            administratively_blocked=authentic.administratively_blocked,
            provenance=authentic.provenance,
        )
        unsupported = replace(
            authentic,
            assessment_id=unsupported_id,
            assessment_version="unsupported-validator",
            validator_version="unsupported-validator",
        )
        with self.assertRaisesRegex(
            LearningCaseConflictError, "Unsupported"
        ):
            self.repository.store_readiness_assessment(unsupported)

    def test_missing_and_incomplete_outcomes_are_not_ready(self) -> None:
        missing = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        self.assertIs(
            missing.readiness_status, LearningReadinessStatus.PENDING
        )
        self._attach_outcome(minutes=59)
        incomplete = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        self.assertIs(
            incomplete.readiness_status, LearningReadinessStatus.NOT_READY
        )

    def test_unsupported_validator_is_rejected_and_history_survives_restart(
        self,
    ) -> None:
        self._attach_outcome()
        first = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            LearningReadinessService(
                self.repository, validator_version="unsupported-validator"
            ).assess(self.case.case_id)
        reopened = SQLiteLearningCaseRepository(self.path)

        self.assertEqual(
            reopened.latest_readiness_assessment(self.case.case_id), first
        )
        self.assertEqual(
            len(reopened.list_readiness_assessments(self.case.case_id)), 1
        )
        with self.assertRaises(LearningCaseConflictError):
            reopened.store_readiness_assessment(
                replace(first, warnings=("conflicting retry",))
            )

    def test_f03_2_assessment_schema_remains_readable_but_not_authoritative(
        self,
    ) -> None:
        self._attach_outcome()
        current = LearningReadinessService(self.repository).derive_assessment(
            self.case.case_id
        )
        values = {
            field.name: getattr(current, field.name)
            for field in fields(current)
        }
        values["schema_version"] = "learning_readiness_assessment_v1"
        values["assessment_id"] = build_readiness_assessment_id(
            case_id=current.case_id,
            runtime_event_id=current.runtime_event_id,
            validator_version=current.validator_version,
            canonical_payload_digest=current.canonical_payload_digest,
            outcome_record_id=current.outcome_record_id,
            label_policy_version=current.label_policy_version,
            review_status=current.review_status,
            manually_excluded=current.manually_excluded,
            administratively_blocked=current.administratively_blocked,
            provenance=current.provenance,
            schema_version="learning_readiness_assessment_v1",
        )
        legacy = type(current)(**values)
        payload = json.dumps(
            legacy.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO readiness_assessments VALUES (?, ?, ?, ?, ?, ?)",
                (
                    legacy.assessment_id,
                    legacy.case_id,
                    payload,
                    hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    legacy.readiness_status.value,
                    legacy.assessment_timestamp.isoformat(),
                ),
            )
        reopened = SQLiteLearningCaseRepository(self.path)
        self.assertEqual(
            reopened.list_readiness_assessments(self.case.case_id),
            (legacy,),
        )
        manifest = export_jsonl_dataset(
            reopened,
            Path(self.temp.name) / "legacy-v1.jsonl",
            runtime_version="526e72f",
            horizon_minutes=60,
        )
        self.assertEqual(manifest["case_count"], 0)
        self.assertEqual(
            manifest["exclusions_by_reason"],
            {"forged_readiness_assessment": 1},
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
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=60,
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
            horizon_minutes=60,
            readiness_policy="evaluation",
        )
        training = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=60,
            readiness_policy="training",
        )
        self.assertEqual(evaluation["case_count"], 1)
        self.assertEqual(training["case_count"], 0)
        self.assertEqual(
            training["exclusions_by_reason"],
            {"review_not_approved": 1},
        )

    def test_stale_digest_and_wrong_horizon_cannot_authorize_export(
        self,
    ) -> None:
        self._attach_outcome()
        LearningReadinessService(self.repository).assess(self.case.case_id)
        self.repository.record_review(
            ReviewRecord(
                review_id="review:stale",
                case_id=self.case.case_id,
                review_status="approved",
                annotation="Changes current review dependency.",
                tags=("reviewed",),
                reviewed_by="reviewer",
                reviewed_at=NOW + timedelta(hours=2),
            )
        )
        output = Path(self.temp.name) / "stale.jsonl"
        stale = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=60,
        )
        wrong_horizon = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=15,
        )
        self.assertEqual(stale["case_count"], 0)
        self.assertEqual(
            stale["exclusions_by_reason"], {"stale_case_digest": 1}
        )
        self.assertEqual(wrong_horizon["case_count"], 0)
        self.assertEqual(
            wrong_horizon["exclusions_by_reason"], {"horizon_mismatch": 1}
        )

    def test_unsupported_validator_and_label_policy_cannot_authorize(
        self,
    ) -> None:
        self._attach_outcome()
        LearningReadinessService(self.repository).assess(self.case.case_id)
        output = Path(self.temp.name) / "unsupported.jsonl"
        unsupported_validator = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=60,
            validator_version="unsupported-validator",
        )
        unsupported_label = export_jsonl_dataset(
            self.repository,
            output,
            runtime_version="526e72f",
            horizon_minutes=60,
            label_policy_version="unsupported-label-policy",
        )
        self.assertEqual(
            unsupported_validator["exclusions_by_reason"],
            {"unsupported_validator": 1},
        )
        self.assertEqual(
            unsupported_label["exclusions_by_reason"],
            {"label_policy_mismatch": 1},
        )

    def test_later_authoritative_outcome_makes_old_assessment_stale(self) -> None:
        service = OutcomeAttributionService(self.repository)
        service.attribute(
            self.case,
            future_observations(minutes=59),
            horizon_minutes=60,
            creation_timestamp=NOW + timedelta(minutes=59),
        )
        old = LearningReadinessService(self.repository).assess(
            self.case.case_id
        )
        self.assertIs(
            old.readiness_status, LearningReadinessStatus.NOT_READY
        )
        service.attribute(
            self.case,
            future_observations(minutes=60),
            horizon_minutes=60,
            creation_timestamp=NOW + timedelta(minutes=60),
        )
        manifest = export_jsonl_dataset(
            self.repository,
            Path(self.temp.name) / "stale-outcome.jsonl",
            runtime_version="526e72f",
            horizon_minutes=60,
        )
        self.assertEqual(manifest["case_count"], 0)
        self.assertEqual(
            manifest["exclusions_by_reason"], {"stale_outcome": 1}
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

    def test_manual_exclusion_and_administrative_block_governance(self) -> None:
        for status, expected_reason, manual, blocked in (
            ("excluded", "manual_exclusion", True, False),
            ("blocked", "administrative_block", False, True),
        ):
            with self.subTest(status=status):
                path = Path(self.temp.name) / f"{status}.sqlite3"
                repository = SQLiteLearningCaseRepository(path)
                repository.initialize()
                case = LearningCasePersistenceService(repository).persist(
                    completed_event(),
                    ingestion_timestamp=NOW,
                    provenance={
                        "source": "test_ingestion",
                        "runtime_version": "526e72f",
                    },
                )
                OutcomeAttributionService(repository).attribute(
                    case,
                    future_observations(),
                    horizon_minutes=60,
                    creation_timestamp=NOW + timedelta(minutes=60),
                )
                old = LearningReadinessService(repository).assess(case.case_id)
                repository.record_review(
                    ReviewRecord(
                        review_id=f"review:{status}",
                        case_id=case.case_id,
                        review_status=status,
                        annotation=f"Apply {status}.",
                        tags=(),
                        reviewed_by="governance-reviewer",
                        reviewed_at=NOW + timedelta(hours=2),
                    )
                )
                stale = export_jsonl_dataset(
                    repository,
                    Path(self.temp.name) / f"{status}-stale.jsonl",
                    runtime_version="526e72f",
                    horizon_minutes=60,
                )
                current = LearningReadinessService(repository).assess(
                    case.case_id
                )
                evaluation = export_jsonl_dataset(
                    repository,
                    Path(self.temp.name) / f"{status}-evaluation.jsonl",
                    runtime_version="526e72f",
                    horizon_minutes=60,
                )
                training = export_jsonl_dataset(
                    repository,
                    Path(self.temp.name) / f"{status}-training.jsonl",
                    runtime_version="526e72f",
                    horizon_minutes=60,
                    readiness_policy="training",
                )
                self.assertNotEqual(old.assessment_id, current.assessment_id)
                self.assertEqual(stale["case_count"], 0)
                self.assertEqual(
                    stale["exclusions_by_reason"],
                    {"stale_case_digest": 1},
                )
                self.assertTrue(current.technically_ready)
                self.assertIs(current.manually_excluded, manual)
                self.assertIs(current.administratively_blocked, blocked)
                self.assertFalse(current.approved_for_evaluation)
                self.assertFalse(current.approved_for_training)
                self.assertEqual(
                    evaluation["exclusions_by_reason"],
                    {expected_reason: 1},
                )
                self.assertEqual(
                    training["exclusions_by_reason"],
                    {expected_reason: 1},
                )
                forged_values = {
                    field.name: getattr(current, field.name)
                    for field in fields(current)
                }
                forged_values.update(
                    manually_excluded=False,
                    administratively_blocked=False,
                    approved_for_evaluation=True,
                    approved_for_training=True,
                )
                forged_values["assessment_id"] = (
                    build_readiness_assessment_id(
                        case_id=forged_values["case_id"],
                        runtime_event_id=forged_values["runtime_event_id"],
                        validator_version=forged_values[
                            "validator_version"
                        ],
                        canonical_payload_digest=forged_values[
                            "canonical_payload_digest"
                        ],
                        outcome_record_id=forged_values[
                            "outcome_record_id"
                        ],
                        label_policy_version=forged_values[
                            "label_policy_version"
                        ],
                        review_status=forged_values["review_status"],
                        manually_excluded=False,
                        administratively_blocked=False,
                        provenance=forged_values["provenance"],
                    )
                )
                forged = type(current)(**forged_values)
                with self.assertRaises(LearningCaseConflictError):
                    repository.store_readiness_assessment(forged)

    def test_review_contract_rejects_unknown_and_contradictory_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            ReviewRecord(
                review_id="unknown",
                case_id=self.case.case_id,
                review_status="unknown",
                annotation=None,
                tags=(),
                reviewed_by="reviewer",
                reviewed_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "contradicts"):
            ReviewRecord(
                review_id="contradictory",
                case_id=self.case.case_id,
                review_status="approved",
                annotation=None,
                tags=("manual-exclusion",),
                reviewed_by="reviewer",
                reviewed_at=NOW,
            )

    def test_governance_transition_and_restart_are_authoritative(self) -> None:
        self._attach_outcome()
        repository = self.repository
        repository.record_review(
            ReviewRecord(
                review_id="review:excluded",
                case_id=self.case.case_id,
                review_status="excluded",
                annotation=None,
                tags=(),
                reviewed_by="reviewer",
                reviewed_at=NOW + timedelta(hours=1),
            )
        )
        excluded = LearningReadinessService(repository).assess(
            self.case.case_id
        )
        repository.record_review(
            ReviewRecord(
                review_id="review:approved",
                case_id=self.case.case_id,
                review_status="approved",
                annotation=None,
                tags=(),
                reviewed_by="reviewer",
                reviewed_at=NOW + timedelta(hours=2),
            )
        )
        reopened = SQLiteLearningCaseRepository(self.path)
        governance = reopened.current_governance(self.case.case_id)
        approved = LearningReadinessService(reopened).assess(
            self.case.case_id
        )
        self.assertTrue(excluded.manually_excluded)
        self.assertEqual(governance.review_status.value, "approved")
        self.assertFalse(governance.manually_excluded)
        self.assertTrue(approved.approved_for_evaluation)
        self.assertTrue(approved.approved_for_training)
        self.assertNotEqual(
            excluded.provenance["dependency_fingerprint"],
            approved.provenance["dependency_fingerprint"],
        )

    def test_equal_timestamp_review_selection_ignores_insertion_order(
        self,
    ) -> None:
        results = []
        for index, order in enumerate(
            (
                (("review:a", "approved"), ("review:z", "blocked")),
                (("review:z", "blocked"), ("review:a", "approved")),
            )
        ):
            path = Path(self.temp.name) / f"order-{index}.sqlite3"
            repository = SQLiteLearningCaseRepository(path)
            repository.initialize()
            case = LearningCasePersistenceService(repository).persist(
                completed_event(),
                ingestion_timestamp=NOW,
                provenance={
                    "source": "test_ingestion",
                    "runtime_version": "526e72f",
                },
            )
            for review_id, status in order:
                repository.record_review(
                    ReviewRecord(
                        review_id=review_id,
                        case_id=case.case_id,
                        review_status=status,
                        annotation=None,
                        tags=(),
                        reviewed_by="reviewer",
                        reviewed_at=NOW + timedelta(hours=1),
                    )
                )
            results.append(repository.current_governance(case.case_id))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0].review_id, "review:z")
        self.assertTrue(results[0].administratively_blocked)

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
            not_ready.readiness_status, LearningReadinessStatus.PENDING
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
            cli_main(
                [
                    "--store",
                    store,
                    "review-case",
                    "--case-id",
                    self.case.case_id,
                    "--status",
                    "excluded",
                    "--reviewed-by",
                    "cli-reviewer",
                    "--reviewed-at",
                    (NOW + timedelta(hours=2)).isoformat(),
                ]
            ),
            0,
        )
        self.assertEqual(
            cli_main(
                [
                    "--store",
                    store,
                    "governance-state",
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
                    "assess-readiness",
                    "--case-id",
                    self.case.case_id,
                ]
            ),
            0,
        )
        excluded_output = Path(self.temp.name) / "cli-excluded.jsonl"
        self.assertEqual(
            cli_main(
                [
                    "--store",
                    store,
                    "export",
                    "--output",
                    str(excluded_output),
                    "--runtime-version",
                    "526e72f",
                    "--policy",
                    "evaluation",
                    "--horizon",
                    "60",
                ]
            ),
            0,
        )
        manifest = json.loads(
            excluded_output.with_suffix(
                excluded_output.suffix + ".manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["case_count"], 0)
        self.assertEqual(
            manifest["exclusions_by_reason"], {"manual_exclusion": 1}
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
                    "--horizon",
                    "60",
                ]
            ),
            0,
        )
