from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from pumpagent.runtime.domain import (
    CANONICAL_SCENARIO_ORDER,
    SCENARIO_PROBABILITY_POLICY_VERSION,
    HypothesisSemanticCode,
    ScenarioArtifactType,
    ScenarioAssessmentStatus,
    ScenarioIdentifier,
    ScenarioProbability,
    ScenarioProvenanceReference,
    ScenarioReasonCode,
    ScenarioValidationCode,
    ScenarioWeight,
    canonical_process_evidence_id,
    canonical_scenario_probability_id,
)
from pumpagent.runtime.domain.enums import UncertaintyLevel


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def provenance(
    artifact_type: ScenarioArtifactType,
    artifact_id: str,
    *,
    event_id: str = "event-2",
    timestamp: datetime = NOW,
) -> ScenarioProvenanceReference:
    return ScenarioProvenanceReference(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        episode_id="episode-1",
        runtime_event_id=event_id,
        observation_timestamp=timestamp,
    )


def healthy_distribution() -> tuple[ScenarioWeight, ...]:
    values = (
        Decimal("0.100000"),
        Decimal("0.650000"),
        Decimal("0.150000"),
        Decimal("0.070000"),
        Decimal("0.030000"),
    )
    return tuple(
        ScenarioWeight(scenario=scenario, probability=probability)
        for scenario, probability in zip(CANONICAL_SCENARIO_ORDER, values)
    )


def make_scenario(
    **changes: object,
) -> ScenarioProbability:
    supporting = (
        provenance(
            ScenarioArtifactType.PROCESS_EVIDENCE,
            "process-evidence:episode-1:event-2",
        ),
        provenance(
            ScenarioArtifactType.PROCESS_QUALITY,
            "process-quality-assessment:episode-1:event-2",
        ),
        provenance(ScenarioArtifactType.HYPOTHESIS, "hypothesis-2"),
    )
    values: dict[str, object] = {
        "scenario_probability_id": (
            "scenario-probability:episode-1:event-2:hypothesis-2"
        ),
        "episode_id": "episode-1",
        "runtime_event_id": "event-2",
        "observation_timestamp": NOW,
        "created_at": NOW,
        "source_process_evidence_id": (
            "process-evidence:episode-1:event-2"
        ),
        "source_process_quality_assessment_id": (
            "process-quality-assessment:episode-1:event-2"
        ),
        "source_hypothesis_id": "hypothesis-2",
        "source_healthy_baseline_id": None,
        "previous_scenario_probability_id": None,
        "hypothesis_semantic_code": (
            HypothesisSemanticCode.CONTINUATION_EXPLANATION
        ),
        "status": ScenarioAssessmentStatus.COMPLETED,
        "distribution": healthy_distribution(),
        "primary_scenario": ScenarioIdentifier.CONTINUATION_PERSISTS,
        "uncertainty": UncertaintyLevel.LOW,
        "reason_codes": (
            ScenarioReasonCode.PROCESS_CONTINUATION_ALIVE,
            ScenarioReasonCode.PRIMARY_SCENARIO_QUALIFIED,
        ),
        "supporting_provenance": supporting,
        "contradicting_provenance": (),
        "missing_prerequisites": (),
    }
    values.update(changes)
    return ScenarioProbability(**values)  # type: ignore[arg-type]


class ScenarioProbabilityContractTests(unittest.TestCase):
    def test_canonical_vocabulary_is_exact_and_finite(self) -> None:
        self.assertEqual(
            tuple(item.value for item in ScenarioIdentifier),
            (
                "continue_observation",
                "continuation_persists",
                "saturation_persists",
                "failure_candidate_persists",
                "first_failure_confirms",
            ),
        )
        self.assertEqual(len(ScenarioValidationCode), 13)

    def test_canonical_identity_helpers_are_deterministic(self) -> None:
        self.assertEqual(
            canonical_scenario_probability_id(
                "episode-1",
                "event-2",
                "hypothesis-2",
            ),
            "scenario-probability:episode-1:event-2:hypothesis-2",
        )
        self.assertEqual(
            canonical_process_evidence_id("episode-1", "event-2"),
            "process-evidence:episode-1:event-2",
        )

    def test_valid_contract_is_immutable_and_serializes_unambiguously(self) -> None:
        value = make_scenario()
        with self.assertRaises(FrozenInstanceError):
            value.primary_scenario = ScenarioIdentifier.CONTINUE_OBSERVATION

        serialized = value.to_dict()
        self.assertEqual(
            serialized["scenario_probability_id"],
            "scenario-probability:episode-1:event-2:hypothesis-2",
        )
        self.assertEqual(
            [item["scenario"] for item in serialized["distribution"]],
            [item.value for item in CANONICAL_SCENARIO_ORDER],
        )
        self.assertEqual(
            [item["probability"] for item in serialized["distribution"]],
            [
                "0.100000",
                "0.650000",
                "0.150000",
                "0.070000",
                "0.030000",
            ],
        )
        self.assertEqual(
            serialized["hypothesis_semantic_code"],
            "continuation_explanation",
        )
        self.assertEqual(
            serialized["policy_version"],
            SCENARIO_PROBABILITY_POLICY_VERSION,
        )
        json.dumps(serialized)

    def test_rejects_non_canonical_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical formula"):
            make_scenario(scenario_probability_id="forged")

    def test_rejects_non_canonical_process_evidence_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "Process Evidence identity"):
            make_scenario(source_process_evidence_id="forged")

    def test_rejects_self_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot reference itself"):
            make_scenario(
                previous_scenario_probability_id=(
                    "scenario-probability:episode-1:event-2:hypothesis-2"
                )
            )

    def test_rejects_duplicate_or_incomplete_scenario_set(self) -> None:
        duplicate = list(healthy_distribution())
        duplicate[-1] = ScenarioWeight(
            ScenarioIdentifier.FAILURE_CANDIDATE_PERSISTS,
            Decimal("0.030000"),
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            make_scenario(distribution=tuple(duplicate))

        with self.assertRaisesRegex(ValueError, "complete canonical scenario set"):
            make_scenario(distribution=healthy_distribution()[:-1])

    def test_rejects_invalid_probability_precision_range_and_sum(self) -> None:
        with self.assertRaisesRegex(ValueError, "six fractional"):
            ScenarioWeight(
                ScenarioIdentifier.CONTINUE_OBSERVATION,
                Decimal("0.1"),
            )
        with self.assertRaisesRegex(ValueError, "between"):
            ScenarioWeight(
                ScenarioIdentifier.CONTINUE_OBSERVATION,
                Decimal("1.100000"),
            )

        invalid_sum = list(healthy_distribution())
        invalid_sum[0] = replace(
            invalid_sum[0],
            probability=Decimal("0.110000"),
        )
        with self.assertRaisesRegex(ValueError, "sum exactly"):
            make_scenario(distribution=tuple(invalid_sum))

    def test_rejects_tie_and_insufficient_dominance(self) -> None:
        tied = (
            Decimal("0.300000"),
            Decimal("0.300000"),
            Decimal("0.150000"),
            Decimal("0.150000"),
            Decimal("0.100000"),
        )
        tied_distribution = tuple(
            ScenarioWeight(scenario, probability)
            for scenario, probability in zip(CANONICAL_SCENARIO_ORDER, tied)
        )
        with self.assertRaisesRegex(ValueError, "unique highest"):
            make_scenario(
                distribution=tied_distribution,
                primary_scenario=ScenarioIdentifier.CONTINUE_OBSERVATION,
            )

        weak_margin = (
            Decimal("0.100000"),
            Decimal("0.350000"),
            Decimal("0.250001"),
            Decimal("0.199999"),
            Decimal("0.100000"),
        )
        weak_distribution = tuple(
            ScenarioWeight(scenario, probability)
            for scenario, probability in zip(
                CANONICAL_SCENARIO_ORDER,
                weak_margin,
            )
        )
        with self.assertRaisesRegex(ValueError, "dominance margin"):
            make_scenario(distribution=weak_distribution)

    def test_rejects_duplicate_missing_or_misaligned_provenance(self) -> None:
        value = make_scenario()
        duplicated = value.supporting_provenance + (
            value.supporting_provenance[0],
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            make_scenario(supporting_provenance=duplicated)

        missing = tuple(
            item
            for item in value.supporting_provenance
            if item.artifact_type is not ScenarioArtifactType.PROCESS_QUALITY
        )
        with self.assertRaisesRegex(ValueError, "process_quality"):
            make_scenario(supporting_provenance=missing)

        cross_episode = replace(
            value.supporting_provenance[0],
            episode_id="episode-2",
        )
        with self.assertRaisesRegex(ValueError, "cross Episode"):
            make_scenario(
                supporting_provenance=(
                    cross_episode,
                    *value.supporting_provenance[1:],
                )
            )

    def test_validates_optional_historical_provenance(self) -> None:
        current = make_scenario()
        baseline_id = (
            "healthy-baseline:episode-1:"
            "process-quality-assessment:episode-1:event-1"
        )
        previous_id = (
            "scenario-probability:episode-1:event-1:hypothesis-1"
        )
        historical = (
            provenance(
                ScenarioArtifactType.HEALTHY_BASELINE,
                baseline_id,
                event_id="event-1",
                timestamp=NOW - timedelta(minutes=5),
            ),
            provenance(
                ScenarioArtifactType.PREVIOUS_SCENARIO_PROBABILITY,
                previous_id,
                event_id="event-1",
                timestamp=NOW - timedelta(minutes=5),
            ),
        )
        value = make_scenario(
            source_healthy_baseline_id=baseline_id,
            previous_scenario_probability_id=previous_id,
            supporting_provenance=current.supporting_provenance + historical,
        )
        self.assertEqual(value.source_healthy_baseline_id, baseline_id)

        future = replace(
            historical[-1],
            observation_timestamp=NOW + timedelta(minutes=5),
        )
        with self.assertRaisesRegex(ValueError, "precede"):
            make_scenario(
                previous_scenario_probability_id=previous_id,
                supporting_provenance=current.supporting_provenance + (future,),
            )

    def test_rejects_invalid_enum_values_and_duplicate_reasons(self) -> None:
        with self.assertRaisesRegex(ValueError, "HypothesisSemanticCode"):
            make_scenario(hypothesis_semantic_code="continuation_explanation")
        with self.assertRaisesRegex(ValueError, "unique"):
            make_scenario(
                reason_codes=(
                    ScenarioReasonCode.PRIMARY_SCENARIO_QUALIFIED,
                    ScenarioReasonCode.PRIMARY_SCENARIO_QUALIFIED,
                )
            )


if __name__ == "__main__":
    unittest.main()
