from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from unittest import TestCase, mock

from pumpagent.runtime.domain.enums import EvidenceStrength, UncertaintyLevel
from pumpagent.runtime.domain.process_evidence import ProcessState
from pumpagent.runtime.domain.process_quality import DiagnosticOutcome
from pumpagent.runtime.modules.observation_lifecycle.runtime_cycle import (
    ObservationRuntimeCycleStatus,
    process_observation_runtime_cycle,
)
from pumpagent.runtime.modules.process_quality import (
    HealthyBaselineDesignationPolicyInput,
    designate_healthy_baseline,
)
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator
from tests.runtime.modules.test_observation_runtime_cycle import (
    CANDLE,
    active_entry,
    cycle,
    manager_with,
    process_snapshot,
)


def establish_baseline():
    manager = manager_with(active_entry())
    runtime = RuntimeOrchestrator()
    first = process_observation_runtime_cycle(
        cycle(process_snapshot(CANDLE)), manager, runtime
    )
    second_candle = CANDLE + timedelta(minutes=5)
    second = process_observation_runtime_cycle(
        cycle(
            process_snapshot(
                second_candle,
                closes=(100.0, 103.0),
                volumes=(40.0, 90.0),
                oi_change=2.0,
            )
        ),
        manager,
        runtime,
    )
    return manager, runtime, first, second


class HealthyBaselinePolicyTests(TestCase):
    def test_canonical_eligibility_thresholds_and_uncertainty_rule(self) -> None:
        _manager, _runtime, first, second = establish_baseline()
        current = second.runtime_result.process_quality_assessment
        process = second.runtime_result.process_evidence
        previous = (first.runtime_result.process_quality_assessment,)

        high_uncertainty = replace(
            current,
            uncertainty_level=UncertaintyLevel.HIGH,
        )
        self.assertIsNotNone(
            designate_healthy_baseline(
                HealthyBaselineDesignationPolicyInput(
                    current_assessment=high_uncertainty,
                    process_evidence=process,
                    data_quality_status=second.runtime_result.market_snapshot.data_quality_status,
                    previous_assessments=previous,
                )
            )
        )

        for insufficient_process in (
            replace(process, evidence_strength=EvidenceStrength.WEAK),
            replace(process, current_process_state=ProcessState.WEAKENING),
        ):
            self.assertIsNone(
                designate_healthy_baseline(
                    HealthyBaselineDesignationPolicyInput(
                        current_assessment=current,
                        process_evidence=insufficient_process,
                        data_quality_status=second.runtime_result.market_snapshot.data_quality_status,
                        previous_assessments=previous,
                    )
                )
            )

        without_structure = replace(
            current,
            healthy_active_process=replace(
                current.healthy_active_process,
                supporting_evidence=tuple(
                    item
                    for item in current.healthy_active_process.supporting_evidence
                    if item.source_section != "structural_evidence"
                ),
            ),
        )
        self.assertIsNone(
            designate_healthy_baseline(
                HealthyBaselineDesignationPolicyInput(
                    current_assessment=without_structure,
                    process_evidence=process,
                    data_quality_status=second.runtime_result.market_snapshot.data_quality_status,
                    previous_assessments=previous,
                )
            )
        )

    def test_first_supported_healthy_assessment_creates_and_activates_baseline(
        self,
    ) -> None:
        manager, _runtime, first, second = establish_baseline()

        self.assertIsNone(first.runtime_result.healthy_baseline_designation)
        designation = second.runtime_result.healthy_baseline_designation
        self.assertIsNotNone(designation)
        self.assertEqual(
            designation.source_assessment,
            second.runtime_result.process_quality_assessment.to_reference(),
        )
        self.assertEqual(
            designation.creation_timestamp,
            second.runtime_result.process_quality_assessment.current_observation.observation_timestamp,
        )
        context = manager.get(
            symbol="BTCUSDT", exchange="bybit", timeframe="5m"
        ).active_episode_analytical_context
        self.assertEqual(context.healthy_baseline_designation, designation)
        self.assertEqual(
            context.healthy_baseline_reference, designation.to_reference()
        )

    def test_inhibited_assessment_does_not_create_baseline(self) -> None:
        manager = manager_with(active_entry())
        result = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, RuntimeOrchestrator()
        )

        self.assertIs(
            result.runtime_result.process_quality_assessment.healthy_active_process.outcome,
            DiagnosticOutcome.INHIBITED,
        )
        self.assertIsNone(result.runtime_result.healthy_baseline_designation)
        self.assertIsNone(
            result.resulting_watchlist_entry.active_episode_analytical_context
            .healthy_baseline_reference
        )

    def test_rejected_failed_and_uncommitted_cycles_cannot_activate_baseline(
        self,
    ) -> None:
        manager = manager_with(active_entry())
        runtime = RuntimeOrchestrator()
        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, runtime
        )
        before = first.resulting_watchlist_entry

        duplicate = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, runtime
        )
        self.assertIs(
            duplicate.status, ObservationRuntimeCycleStatus.ADMISSION_STOPPED
        )
        self.assertIsNone(
            before.active_episode_analytical_context.healthy_baseline_reference
        )

        next_candle = CANDLE + timedelta(minutes=5)
        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop."
            "build_process_quality_assessment",
            side_effect=RuntimeError("failed assessment"),
        ):
            failed = process_observation_runtime_cycle(
                cycle(process_snapshot(next_candle)), manager, runtime
            )
        self.assertIs(failed.status, ObservationRuntimeCycleStatus.RUNTIME_FAILED)
        self.assertIs(
            manager.get(symbol="BTCUSDT", exchange="bybit", timeframe="5m"),
            before,
        )

        with mock.patch.object(
            manager,
            "apply_completed_observation_cycle",
            side_effect=ValueError("commit rejected"),
        ):
            uncommitted = process_observation_runtime_cycle(
                cycle(
                    process_snapshot(
                        next_candle,
                        closes=(100.0, 103.0),
                        volumes=(40.0, 90.0),
                        oi_change=2.0,
                    )
                ),
                manager,
                runtime,
            )
        self.assertIs(
            uncommitted.status,
            ObservationRuntimeCycleStatus.COMPLETION_REJECTED,
        )
        self.assertIsNone(
            before.active_episode_analytical_context.healthy_baseline_reference
        )

    def test_baseline_persists_is_consumed_and_is_never_replaced(self) -> None:
        manager, runtime, _first, second = establish_baseline()
        designation = second.runtime_result.healthy_baseline_designation
        third_candle = CANDLE + timedelta(minutes=10)
        third = process_observation_runtime_cycle(
            cycle(
                process_snapshot(
                    third_candle,
                    closes=(103.0, 102.5),
                    volumes=(90.0, 40.0),
                    oi_change=-1.0,
                )
            ),
            manager,
            runtime,
        )

        self.assertIs(third.status, ObservationRuntimeCycleStatus.COMPLETED)
        assessment = third.runtime_result.process_quality_assessment
        self.assertEqual(
            assessment.loss_of_efficiency.healthy_baseline_reference,
            designation.to_reference(),
        )
        self.assertEqual(
            third.runtime_result.healthy_baseline_designation,
            designation,
        )
        self.assertEqual(
            third.resulting_watchlist_entry.active_episode_analytical_context
            .healthy_baseline_designation,
            designation,
        )

    def test_cross_episode_corruption_and_identity_mutation_are_rejected(self) -> None:
        _manager, _runtime, first, second = establish_baseline()
        designation = second.runtime_result.healthy_baseline_designation
        current = second.runtime_result.process_quality_assessment
        process = second.runtime_result.process_evidence

        with self.assertRaisesRegex(ValueError, "cross Episode"):
            designate_healthy_baseline(
                HealthyBaselineDesignationPolicyInput(
                    current_assessment=replace(
                        current,
                        episode_id="different-episode",
                        current_observation=replace(
                            current.current_observation,
                            episode_id="different-episode",
                        ),
                    ),
                    process_evidence=replace(
                        process, episode_id="different-episode"
                    ),
                    data_quality_status=second.runtime_result.market_snapshot.data_quality_status,
                    previous_assessments=(
                        replace(
                            first.runtime_result.process_quality_assessment,
                            episode_id="different-episode",
                            current_observation=replace(
                                first.runtime_result.process_quality_assessment
                                .current_observation,
                                episode_id="different-episode",
                            ),
                        ),
                    ),
                    existing_designation=designation,
                )
            )

        source = designation.source_assessment
        corrupted_observation = replace(
            source.observation, runtime_event_id="corrupted-event"
        )
        corrupted_reference = replace(
            source,
            runtime_event_id="corrupted-event",
            observation=corrupted_observation,
        )
        corrupted_designation = replace(
            designation,
            source_assessment=corrupted_reference,
            effective_after_assessment=corrupted_reference,
        )
        with self.assertRaisesRegex(ValueError, "authenticated prior assessment"):
            designate_healthy_baseline(
                HealthyBaselineDesignationPolicyInput(
                    current_assessment=current,
                    process_evidence=process,
                    data_quality_status=second.runtime_result.market_snapshot.data_quality_status,
                    previous_assessments=(
                        first.runtime_result.process_quality_assessment,
                    ),
                    existing_designation=corrupted_designation,
                )
            )

        with self.assertRaises(FrozenInstanceError):
            designation.baseline_id = "changed"  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "canonical MVP formula"):
            replace(designation, baseline_id="externally-forged-baseline")
        with self.assertRaisesRegex(ValueError, "creation timestamp"):
            replace(
                designation,
                creation_timestamp=designation.creation_timestamp
                + timedelta(seconds=1),
            )
