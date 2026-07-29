from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from pumpagent.runtime.domain.enums import ObservationEpisodeStatus
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.domain.process_quality import (
    canonical_healthy_baseline_id,
    DiagnosticOutcome,
    HealthyActiveProcessAssessment,
    HealthyBaselineDesignation,
    LossOfEfficiencyAssessment,
    ProcessQualityAssessment,
    ProcessQualityConcept,
    ProcessQualityEvidenceReference,
    ProcessQualityLifecycleRelation,
    ProcessQualityLifecycleRelationType,
    ProcessQualityObservationReference,
)
from pumpagent.runtime.modules.observation_lifecycle.process_quality_history import (
    EpisodeProcessQualityHistory,
)


OPENED = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)


def episode(*, episode_id="episode-1", closed=False):
    return ObservationEpisode(
        episode_id=episode_id,
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="5m",
        opening_timestamp=OPENED,
        status=(
            ObservationEpisodeStatus.CLOSED
            if closed
            else ObservationEpisodeStatus.ACTIVE
        ),
        scanner_trigger_timestamp=OPENED,
        trigger_reasons=("fixture",),
        closing_timestamp=OPENED + timedelta(hours=1) if closed else None,
        closure_reason="closed" if closed else None,
    )


def observation(index, *, episode_id="episode-1"):
    return ProcessQualityObservationReference(
        episode_id=episode_id,
        runtime_event_id=f"event-{index}",
        observation_id=f"observation-{index}",
        observation_timestamp=OPENED + timedelta(minutes=5 * index),
    )


def evidence(source, key):
    return ProcessQualityEvidenceReference(
        source_observation=source,
        source_section="process_evidence",
        evidence_key=key,
        description=f"Evidence {key}.",
    )


def assessment(index, *, healthy=DiagnosticOutcome.SUPPORTED, loss=None,
               baseline=None, episode_id="episode-1"):
    source = observation(index, episode_id=episode_id)
    loss = loss or (
        DiagnosticOutcome.NOT_ESTABLISHED
        if baseline is not None
        else DiagnosticOutcome.INHIBITED
    )
    healthy_assessment = HealthyActiveProcessAssessment(
        outcome=healthy,
        supporting_evidence=(evidence(source, "healthy"),)
        if healthy is DiagnosticOutcome.SUPPORTED else (),
        contradicting_evidence=(evidence(source, "healthy_not_established"),)
        if healthy is DiagnosticOutcome.NOT_ESTABLISHED else (),
        missing_evidence=(evidence(source, "healthy_comparison"),)
        if healthy is DiagnosticOutcome.INHIBITED else (),
        inhibiting_evidence=(),
    )
    loss_assessment = LossOfEfficiencyAssessment(
        outcome=loss,
        healthy_baseline_reference=baseline,
        supporting_evidence=(evidence(source, "loss"),)
        if loss is DiagnosticOutcome.SUPPORTED else (),
        contradicting_evidence=(evidence(source, "loss_not_established"),)
        if loss is DiagnosticOutcome.NOT_ESTABLISHED else (),
        missing_evidence=(evidence(source, "healthy_baseline"),)
        if loss is DiagnosticOutcome.INHIBITED and baseline is None else (),
        inhibiting_evidence=(evidence(source, "loss_comparison"),)
        if loss is DiagnosticOutcome.INHIBITED and baseline is not None else (),
    )
    return ProcessQualityAssessment(
        assessment_id=f"assessment-{index}",
        episode_id=episode_id,
        runtime_event_id=source.runtime_event_id,
        current_observation=source,
        healthy_active_process=healthy_assessment,
        loss_of_efficiency=loss_assessment,
    )


def designation(source, index, predecessor=None, *, effective_after=None):
    return HealthyBaselineDesignation(
        baseline_id=canonical_healthy_baseline_id(
            source.episode_id,
            source.assessment_id,
        ),
        episode_id=source.episode_id,
        source_assessment=source.to_reference(),
        effective_after_assessment=(effective_after or source).to_reference(),
        creation_timestamp=source.current_observation.observation_timestamp,
        designation_reason="Accepted healthy comparison baseline.",
        predecessor_baseline=predecessor,
    )


def lifecycle(earlier, later, relation_type, *, relation_id="relation-1"):
    if relation_type is ProcessQualityLifecycleRelationType.RECOVERED:
        earlier_concept = ProcessQualityConcept.LOSS_OF_EFFICIENCY
        later_concept = ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS
        later_outcome = DiagnosticOutcome.SUPPORTED
    else:
        earlier_concept = ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS
        later_concept = ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS
        later_outcome = DiagnosticOutcome.NOT_ESTABLISHED
    return ProcessQualityLifecycleRelation(
        relation_id=relation_id,
        episode_id=earlier.episode_id,
        relation_type=relation_type,
        earlier_assessment=earlier.to_reference(),
        earlier_concept=earlier_concept,
        earlier_outcome=DiagnosticOutcome.SUPPORTED,
        later_assessment=later.to_reference(),
        later_concept=later_concept,
        later_outcome=later_outcome,
        justification_evidence=(evidence(later.current_observation, "relation"),),
        relation_explanation="Immutable historical qualification.",
    )


class EpisodeProcessQualityHistoryTests(unittest.TestCase):
    def initial_history(self):
        active = episode()
        first = assessment(1)
        history = EpisodeProcessQualityHistory(active.episode_id)
        history = history.accept_assessment(active, first)
        first_baseline = designation(first, 1)
        history = history.accept_baseline_designation(active, first_baseline)
        return active, history, first, first_baseline

    def test_missing_assessment_and_outcome_mismatch_are_rejected(self):
        active, history, first, _ = self.initial_history()
        missing = replace(first.to_reference(), assessment_id="missing")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            history.resolve_assessment(missing)
        mismatch = replace(
            first.to_reference(),
            healthy_active_process_outcome=DiagnosticOutcome.NOT_ESTABLISHED,
        )
        with self.assertRaisesRegex(ValueError, "canonical stored coordinates"):
            history.resolve_assessment(mismatch)

    def test_duplicate_initial_baseline_and_duplicate_identity_are_rejected(self):
        active, history, _, first_baseline = self.initial_history()
        second = assessment(2)
        history = history.accept_assessment(active, second)
        with self.assertRaisesRegex(ValueError, "replacement is forbidden"):
            history.accept_baseline_designation(active, designation(second, 2))
        with self.assertRaisesRegex(ValueError, "identity is already accepted"):
            history.accept_baseline_designation(active, first_baseline)

    def test_successor_baseline_is_rejected(self):
        active, history, _, first_baseline = self.initial_history()
        second = assessment(2)
        history = history.accept_assessment(active, second)
        with self.assertRaisesRegex(ValueError, "replacement is forbidden"):
            designation(second, 2, first_baseline.to_reference())

    def test_unknown_baseline_identity_is_rejected_at_reference_boundary(self):
        _active, _history, _, first_baseline = self.initial_history()
        with self.assertRaisesRegex(ValueError, "canonical MVP formula"):
            replace(
                first_baseline.to_reference(),
                baseline_id="healthy-baseline:episode-1:missing-assessment",
            )

    def test_cross_episode_and_acceptance_after_closure_are_rejected(self):
        active, history, _, _ = self.initial_history()
        with self.assertRaisesRegex(ValueError, "cross Episode"):
            history.accept_assessment(
                active,
                assessment(2, episode_id="episode-2"),
            )
        with self.assertRaisesRegex(ValueError, "after Episode closure"):
            history.accept_assessment(episode(closed=True), assessment(2))

    def test_repeated_observation_and_runtime_event_coordinates_are_rejected(self):
        active, history, first, _ = self.initial_history()
        candidate = assessment(2)
        repeated_observation = replace(
            candidate,
            current_observation=replace(
                candidate.current_observation,
                observation_id=first.current_observation.observation_id,
            ),
        )
        with self.assertRaisesRegex(ValueError, "Observation identity"):
            history.accept_assessment(active, repeated_observation)

        repeated_event_observation = replace(
            candidate.current_observation,
            runtime_event_id=first.runtime_event_id,
        )
        repeated_event = replace(
            candidate,
            runtime_event_id=first.runtime_event_id,
            current_observation=repeated_event_observation,
        )
        with self.assertRaisesRegex(ValueError, "Runtime-event identity"):
            history.accept_assessment(active, repeated_event)

    def test_missing_lifecycle_target_and_invalid_temporal_order_are_rejected(self):
        active, history, first, _ = self.initial_history()
        later = assessment(2, healthy=DiagnosticOutcome.NOT_ESTABLISHED)
        missing_relation = lifecycle(
            first,
            later,
            ProcessQualityLifecycleRelationType.INVALIDATED,
        )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            history.accept_lifecycle_relation(active, missing_relation)

        history = history.accept_assessment(active, later)
        with self.assertRaisesRegex(ValueError, "must follow"):
            replace(
                missing_relation,
                later_assessment=replace(
                    later.to_reference(),
                    observation=replace(
                        later.current_observation,
                        observation_timestamp=OPENED,
                    ),
                ),
            )

    def test_duplicate_and_conflicting_lifecycle_relations_are_rejected(self):
        active, history, first, _ = self.initial_history()
        later = assessment(2, healthy=DiagnosticOutcome.NOT_ESTABLISHED)
        history = history.accept_assessment(active, later)
        invalidated = lifecycle(
            first,
            later,
            ProcessQualityLifecycleRelationType.INVALIDATED,
        )
        history = history.accept_lifecycle_relation(active, invalidated)
        with self.assertRaisesRegex(ValueError, "Duplicate lifecycle"):
            history.accept_lifecycle_relation(
                active,
                replace(invalidated, relation_id="relation-2"),
            )
        with self.assertRaisesRegex(ValueError, "Conflicting lifecycle"):
            history.accept_lifecycle_relation(
                active,
                replace(
                    invalidated,
                    relation_id="relation-3",
                    relation_type=ProcessQualityLifecycleRelationType.CONTRADICTED,
                ),
            )
        with self.assertRaisesRegex(ValueError, "identity is already accepted"):
            history.accept_lifecycle_relation(active, invalidated)

    def test_canonical_recovered_history_preserves_earlier_diagnosis(self):
        active, history, _, baseline = self.initial_history()
        deteriorated = assessment(
            2,
            healthy=DiagnosticOutcome.NOT_ESTABLISHED,
            loss=DiagnosticOutcome.SUPPORTED,
            baseline=baseline.to_reference(),
        )
        history = history.accept_assessment(active, deteriorated)
        recovered = assessment(3)
        history = history.accept_assessment(active, recovered)
        recovery = lifecycle(
            deteriorated,
            recovered,
            ProcessQualityLifecycleRelationType.RECOVERED,
        )
        updated = history.accept_lifecycle_relation(active, recovery)
        self.assertIs(updated.assessments[1], deteriorated)
        self.assertIs(
            updated.assessments[1].loss_of_efficiency.outcome,
            DiagnosticOutcome.SUPPORTED,
        )
        self.assertIs(updated.resolve_lifecycle_relation("relation-1"), recovery)

    def test_canonical_invalidated_history_preserves_historical_assessment(self):
        active, history, first, _ = self.initial_history()
        later = assessment(2, healthy=DiagnosticOutcome.NOT_ESTABLISHED)
        history = history.accept_assessment(active, later)
        invalidated = lifecycle(
            first,
            later,
            ProcessQualityLifecycleRelationType.INVALIDATED,
        )
        updated = history.accept_lifecycle_relation(active, invalidated)
        self.assertIs(updated.assessments[0], first)
        self.assertIs(
            updated.assessments[0].healthy_active_process.outcome,
            DiagnosticOutcome.SUPPORTED,
        )
        self.assertIn(invalidated, updated.lifecycle_relations)

    def test_history_is_immutable_append_only_and_serializable(self):
        active, history, first, baseline = self.initial_history()
        before = history.to_dict()
        second = assessment(2)
        updated = history.accept_assessment(active, second)
        json.dumps(updated.to_dict())
        self.assertEqual(history.to_dict(), before)
        self.assertEqual(history.assessments, (first,))
        self.assertEqual(updated.assessments, (first, second))
        self.assertIs(history.applicable_baseline, baseline)
        with self.assertRaises(FrozenInstanceError):
            history.episode_id = "changed"  # type: ignore[misc]

    def test_single_designation_survives_reconstruction_and_later_use(self):
        active, history, _, first_baseline = self.initial_history()
        later = assessment(
            2,
            healthy=DiagnosticOutcome.NOT_ESTABLISHED,
            loss=DiagnosticOutcome.NOT_ESTABLISHED,
            baseline=first_baseline.to_reference(),
        )
        history = history.accept_assessment(active, later)

        reconstructed = EpisodeProcessQualityHistory(
            episode_id=history.episode_id,
            assessments=history.assessments,
            baseline_designations=history.baseline_designations,
            lifecycle_relations=history.lifecycle_relations,
        )
        self.assertEqual(reconstructed, history)
        self.assertEqual(
            later.loss_of_efficiency.healthy_baseline_reference,
            first_baseline.to_reference(),
        )
        self.assertIs(reconstructed.applicable_baseline, first_baseline)

    def test_multiple_supported_assessments_require_first_as_initial_baseline(self):
        active = episode()
        first = assessment(1)
        second = assessment(2)
        history = EpisodeProcessQualityHistory(active.episode_id)
        history = history.accept_assessment(active, first)
        history = history.accept_assessment(active, second)

        initial = designation(first, 1, effective_after=second)
        accepted = history.accept_baseline_designation(active, initial)
        self.assertIs(accepted.applicable_baseline, initial)
        with self.assertRaisesRegex(ValueError, "first accepted supported"):
            history.accept_baseline_designation(
                active,
                designation(second, 2, effective_after=second),
            )

    def test_second_designation_is_rejected_before_source_resolution(self):
        active, history, _, first_baseline = self.initial_history()
        second = assessment(2)
        history = history.accept_assessment(active, second)
        candidate = designation(second, 2)
        with self.assertRaisesRegex(ValueError, "replacement is forbidden"):
            history.accept_baseline_designation(active, candidate)

    def test_reconstruction_rejects_multiple_baselines(self):
        active, history, _, first_baseline = self.initial_history()
        second = assessment(2)
        history = history.accept_assessment(active, second)
        second_baseline = designation(second, 2)
        with self.assertRaisesRegex(ValueError, "replacement is forbidden"):
            EpisodeProcessQualityHistory(
                episode_id=active.episode_id,
                assessments=history.assessments,
                baseline_designations=(first_baseline, second_baseline),
            )

    def test_reconstruction_rejects_malformed_and_cross_episode_objects(self):
        active, history, first, first_baseline = self.initial_history()
        with self.assertRaisesRegex(ValueError, "canonical MVP formula"):
            replace(
                first_baseline,
                source_assessment=replace(
                    first.to_reference(),
                    assessment_id="missing",
                ),
            )
        with self.assertRaisesRegex(ValueError, "cross Episode"):
            EpisodeProcessQualityHistory(
                episode_id="episode-2",
                assessments=history.assessments,
            )

    def test_reconstruction_rejects_duplicate_and_conflicting_relations(self):
        active, history, first, _ = self.initial_history()
        later = assessment(2, healthy=DiagnosticOutcome.NOT_ESTABLISHED)
        history = history.accept_assessment(active, later)
        invalidated = lifecycle(
            first,
            later,
            ProcessQualityLifecycleRelationType.INVALIDATED,
        )
        duplicate = replace(invalidated, relation_id="relation-2")
        with self.assertRaisesRegex(ValueError, "cannot be duplicated"):
            EpisodeProcessQualityHistory(
                episode_id=active.episode_id,
                assessments=history.assessments,
                baseline_designations=history.baseline_designations,
                lifecycle_relations=(invalidated, duplicate),
            )
        conflict = replace(
            invalidated,
            relation_id="relation-3",
            relation_type=ProcessQualityLifecycleRelationType.CONTRADICTED,
        )
        with self.assertRaisesRegex(ValueError, "cannot conflict"):
            EpisodeProcessQualityHistory(
                episode_id=active.episode_id,
                assessments=history.assessments,
                baseline_designations=history.baseline_designations,
                lifecycle_relations=(invalidated, conflict),
            )

    def test_contradiction_then_invalidation_and_shared_assessment_are_legitimate(self):
        active, history, first, _ = self.initial_history()
        second = assessment(2, healthy=DiagnosticOutcome.NOT_ESTABLISHED)
        third = assessment(3, healthy=DiagnosticOutcome.NOT_ESTABLISHED)
        history = history.accept_assessment(active, second)
        history = history.accept_assessment(active, third)
        contradicted = lifecycle(
            first,
            second,
            ProcessQualityLifecycleRelationType.CONTRADICTED,
            relation_id="relation-1",
        )
        invalidated = lifecycle(
            first,
            third,
            ProcessQualityLifecycleRelationType.INVALIDATED,
            relation_id="relation-2",
        )
        history = history.accept_lifecycle_relation(active, contradicted)
        history = history.accept_lifecycle_relation(active, invalidated)
        self.assertEqual(history.lifecycle_relations, (contradicted, invalidated))

    def test_recovery_can_be_followed_by_later_deterioration(self):
        active, history, _, baseline = self.initial_history()
        deteriorated = assessment(
            2,
            healthy=DiagnosticOutcome.NOT_ESTABLISHED,
            loss=DiagnosticOutcome.SUPPORTED,
            baseline=baseline.to_reference(),
        )
        recovered = assessment(3)
        later_deterioration = assessment(
            4,
            healthy=DiagnosticOutcome.NOT_ESTABLISHED,
            loss=DiagnosticOutcome.SUPPORTED,
            baseline=baseline.to_reference(),
        )
        history = history.accept_assessment(active, deteriorated)
        history = history.accept_assessment(active, recovered)
        recovery = lifecycle(
            deteriorated,
            recovered,
            ProcessQualityLifecycleRelationType.RECOVERED,
        )
        history = history.accept_lifecycle_relation(active, recovery)
        history = history.accept_assessment(active, later_deterioration)
        self.assertIs(history.assessments[-1], later_deterioration)
        self.assertIn(recovery, history.lifecycle_relations)

    def test_empty_first_append_closed_reconstruction_freezing_and_json_round_trip(self):
        active = episode()
        empty = EpisodeProcessQualityHistory(active.episode_id)
        self.assertEqual(empty.assessments, ())
        self.assertEqual(json.loads(json.dumps(empty.to_dict())), empty.to_dict())

        first = assessment(1)
        appended = empty.accept_assessment(active, first)
        reconstructed = EpisodeProcessQualityHistory(
            episode_id=appended.episode_id,
            assessments=list(appended.assessments),
            baseline_designations=list(appended.baseline_designations),
            lifecycle_relations=list(appended.lifecycle_relations),
        )
        self.assertIsInstance(reconstructed.assessments, tuple)
        self.assertIsInstance(reconstructed.baseline_designations, tuple)
        self.assertIsInstance(reconstructed.lifecycle_relations, tuple)
        self.assertEqual(
            json.loads(json.dumps(reconstructed.to_dict())),
            reconstructed.to_dict(),
        )

        closed = episode(closed=True)
        retained = EpisodeProcessQualityHistory(
            episode_id=reconstructed.episode_id,
            assessments=reconstructed.assessments,
        )
        self.assertEqual(retained, reconstructed)
        with self.assertRaisesRegex(ValueError, "after Episode closure"):
            retained.accept_assessment(closed, assessment(2))


if __name__ == "__main__":
    unittest.main()
