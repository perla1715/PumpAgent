from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from unittest import TestCase, mock

from pumpagent.runtime.domain.enums import DataQualityStatus
from pumpagent.runtime.domain.process_evidence import ProcessTransition
from pumpagent.runtime.domain.process_quality import (
    canonical_healthy_baseline_id,
    DiagnosticOutcome,
    HealthyBaselineReference,
    ProcessQualityAssessment,
)
from pumpagent.runtime.modules.observation_lifecycle.runtime_cycle import (
    ObservationRuntimeCycleStatus,
    process_observation_runtime_cycle,
)
from pumpagent.runtime.modules.process_quality import (
    ProcessQualityAssessmentInput,
    build_process_quality_assessment,
)
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator
from tests.runtime.modules.test_observation_runtime_cycle import (
    CANDLE,
    active_entry,
    cycle,
    manager_with,
    process_snapshot,
)


class ProcessQualityEngineTests(TestCase):
    def test_reference_identity_and_process_quality_input_are_canonical(self) -> None:
        manager = manager_with(active_entry())
        runtime = RuntimeOrchestrator()
        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, runtime
        ).runtime_result
        source_time = CANDLE + timedelta(minutes=5)
        source = runtime.process_market_update(
            process_snapshot(
                source_time,
                closes=(100.0, 103.0),
                volumes=(40.0, 90.0),
                oi_change=2.0,
            ),
            episode_id=first.process_quality_assessment.episode_id,
            previous_process_evidence=first.process_evidence,
            previous_process_quality_assessments=(
                first.process_quality_assessment,
            ),
            classification_timestamp=source_time,
        )
        source_assessment = source.process_quality_assessment
        expected_id = canonical_healthy_baseline_id(
            source_assessment.episode_id,
            source_assessment.assessment_id,
        )
        valid = HealthyBaselineReference(
            baseline_id=expected_id,
            episode_id=source_assessment.episode_id,
            source_assessment=source_assessment.to_reference(),
        )
        self.assertEqual(valid.baseline_id, expected_id)
        with self.assertRaisesRegex(ValueError, "canonical MVP formula"):
            HealthyBaselineReference(
                baseline_id="forged-baseline",
                episode_id=source_assessment.episode_id,
                source_assessment=source_assessment.to_reference(),
            )

        forged = object.__new__(HealthyBaselineReference)
        object.__setattr__(forged, "baseline_id", "forged-baseline")
        object.__setattr__(forged, "episode_id", valid.episode_id)
        object.__setattr__(forged, "source_assessment", valid.source_assessment)
        object.__setattr__(forged, "schema_version", valid.schema_version)
        current_time = source_time + timedelta(minutes=5)
        with self.assertRaisesRegex(ValueError, "canonical MVP formula"):
            ProcessQualityAssessmentInput(
                process_evidence=replace(
                    source.process_evidence,
                    runtime_event_id="forged-input-event",
                    observation_timestamp=current_time,
                    previous_process_state=(
                        source.process_evidence.current_process_state
                    ),
                    detected_transition=ProcessTransition.UNCHANGED,
                ),
                structural_evidence=replace(
                    source.structure_result,
                    event_id="forged-input-event",
                ),
                market_efficiency_evidence=replace(
                    source.market_result,
                    event_id="forged-input-event",
                ),
                data_quality_status=DataQualityStatus.VALID,
                previous_assessments=(source_assessment,),
                healthy_baseline=forged,
            )

    def test_first_observation_produces_immutable_assessment_and_missing_baseline(
        self,
    ) -> None:
        manager = manager_with(active_entry())
        result = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, RuntimeOrchestrator()
        )

        self.assertIs(result.status, ObservationRuntimeCycleStatus.COMPLETED)
        assessment = result.runtime_result.process_quality_assessment
        self.assertIsInstance(assessment, ProcessQualityAssessment)
        self.assertEqual(assessment.episode_id, result.episode_id)
        self.assertEqual(assessment.runtime_event_id, result.runtime_event_id)
        self.assertIs(
            assessment.loss_of_efficiency.outcome,
            DiagnosticOutcome.INHIBITED,
        )
        self.assertIsNone(
            assessment.loss_of_efficiency.healthy_baseline_reference
        )
        self.assertTrue(
            any(
                item.evidence_key == "healthy_baseline"
                for item in assessment.loss_of_efficiency.missing_evidence
            )
        )
        with self.assertRaises(FrozenInstanceError):
            assessment.assessment_id = "changed"  # type: ignore[misc]

    def test_later_observation_preserves_history_and_previous_reference(self) -> None:
        manager = manager_with(active_entry())
        runtime = RuntimeOrchestrator()
        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, runtime
        )
        second_candle = CANDLE + timedelta(minutes=5)
        second = process_observation_runtime_cycle(
            cycle(process_snapshot(second_candle)), manager, runtime
        )

        self.assertIs(second.status, ObservationRuntimeCycleStatus.COMPLETED)
        first_assessment = first.runtime_result.process_quality_assessment
        context = second.resulting_watchlist_entry.active_episode_analytical_context
        self.assertEqual(
            context.previous_process_quality_reference,
            first_assessment.to_reference(),
        )
        self.assertEqual(len(context.process_quality_history), 2)
        self.assertEqual(context.process_quality_history[0], first_assessment)
        self.assertEqual(
            context.latest_process_quality_assessment,
            second.runtime_result.process_quality_assessment,
        )

    def test_authenticated_baseline_enables_later_loss_evaluation(self) -> None:
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
        source = second.runtime_result.process_quality_assessment
        self.assertIs(
            source.healthy_active_process.outcome,
            DiagnosticOutcome.SUPPORTED,
        )
        designation = second.runtime_result.healthy_baseline_designation
        baseline = designation.to_reference()
        third_candle = second_candle + timedelta(minutes=5)
        third = runtime.process_market_update(
            process_snapshot(
                third_candle,
                closes=(103.0, 102.5),
                volumes=(90.0, 40.0),
                oi_change=-1.0,
            ),
            episode_id=source.episode_id,
            previous_process_evidence=second.runtime_result.process_evidence,
            previous_process_quality_assessments=(
                first.runtime_result.process_quality_assessment,
                source,
            ),
            healthy_baseline_reference=baseline,
            healthy_baseline_designation=designation,
            classification_timestamp=third_candle,
        )

        self.assertIs(
            third.process_quality_assessment.loss_of_efficiency.outcome,
            DiagnosticOutcome.SUPPORTED,
        )
        self.assertEqual(
            third.process_quality_assessment.loss_of_efficiency.healthy_baseline_reference,
            baseline,
        )

    def test_cross_episode_history_and_baseline_are_rejected(self) -> None:
        manager = manager_with(active_entry())
        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, RuntimeOrchestrator()
        ).runtime_result
        next_candle = CANDLE + timedelta(minutes=5)
        runtime = RuntimeOrchestrator()

        with self.assertRaisesRegex(ValueError, "cross Episode"):
            runtime.process_market_update(
                process_snapshot(next_candle),
                episode_id="different-episode",
                previous_process_quality_assessments=(
                    first.process_quality_assessment,
                ),
                classification_timestamp=next_candle,
            )

        supported_candle = next_candle + timedelta(minutes=5)
        supported = runtime.process_market_update(
            process_snapshot(
                supported_candle,
                closes=(100.0, 103.0),
                volumes=(40.0, 90.0),
                oi_change=2.0,
            ),
            episode_id=first.process_quality_assessment.episode_id,
            previous_process_evidence=first.process_evidence,
            previous_process_quality_assessments=(
                first.process_quality_assessment,
            ),
            classification_timestamp=supported_candle,
        )
        baseline = HealthyBaselineReference(
            baseline_id=canonical_healthy_baseline_id(
                supported.process_quality_assessment.episode_id,
                supported.process_quality_assessment.assessment_id,
            ),
            episode_id=supported.process_quality_assessment.episode_id,
            source_assessment=supported.process_quality_assessment.to_reference(),
        )
        with self.assertRaisesRegex(ValueError, "cross Episode"):
            build_process_quality_assessment(
                ProcessQualityAssessmentInput(
                    process_evidence=replace(
                        supported.process_evidence, episode_id="different-episode"
                    ),
                    structural_evidence=supported.structure_result,
                    market_efficiency_evidence=supported.market_result,
                    data_quality_status=DataQualityStatus.VALID,
                    healthy_baseline=baseline,
                )
            )

    def test_duplicate_rejection_and_runtime_failure_preserve_assessment(self) -> None:
        manager = manager_with(active_entry())
        runtime = RuntimeOrchestrator()
        first = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, runtime
        )
        before = first.resulting_watchlist_entry
        previous = before.active_episode_analytical_context.latest_process_quality_assessment

        rejected = process_observation_runtime_cycle(
            cycle(process_snapshot(CANDLE)), manager, runtime
        )
        self.assertIs(
            rejected.status, ObservationRuntimeCycleStatus.ADMISSION_STOPPED
        )
        self.assertIs(
            manager.get(symbol="BTCUSDT", exchange="bybit", timeframe="5m"),
            before,
        )

        with mock.patch(
            "pumpagent.runtime.orchestrator.runtime_loop."
            "build_process_quality_assessment",
            side_effect=RuntimeError("process quality failure"),
        ):
            failed = process_observation_runtime_cycle(
                cycle(process_snapshot(CANDLE + timedelta(minutes=5))),
                manager,
                runtime,
            )
        self.assertIs(failed.status, ObservationRuntimeCycleStatus.RUNTIME_FAILED)
        retained = manager.get(
            symbol="BTCUSDT", exchange="bybit", timeframe="5m"
        )
        self.assertIs(
            retained.active_episode_analytical_context.latest_process_quality_assessment,
            previous,
        )

    def test_process_quality_executes_between_classification_and_hypothesis(self) -> None:
        order: list[str] = []

        from pumpagent.runtime.orchestrator import runtime_loop

        original_classify = runtime_loop.classify_market_process
        original_quality = runtime_loop.build_process_quality_assessment
        original_hypothesis = runtime_loop.build_operational_hypothesis_package

        def classify(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("classification")
            return original_classify(*args, **kwargs)

        def quality(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("process_quality")
            return original_quality(*args, **kwargs)

        def hypothesis(*args, **kwargs):  # type: ignore[no-untyped-def]
            order.append("hypothesis")
            return original_hypothesis(*args, **kwargs)

        with mock.patch.object(runtime_loop, "classify_market_process", side_effect=classify), \
                mock.patch.object(
                    runtime_loop,
                    "build_process_quality_assessment",
                    side_effect=quality,
                ), \
                mock.patch.object(
                    runtime_loop,
                    "build_operational_hypothesis_package",
                    side_effect=hypothesis,
                ):
            runtime_loop.RuntimeOrchestrator().process_market_update(
                process_snapshot(CANDLE), episode_id="episode-BTCUSDT"
            )

        self.assertEqual(
            order, ["classification", "process_quality", "hypothesis"]
        )
