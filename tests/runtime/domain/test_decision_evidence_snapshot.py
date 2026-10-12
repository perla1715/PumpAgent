from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from pumpagent.runtime.domain import (
    DECISION_EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_INTEGRITY_METADATA_SCHEMA_VERSION,
    DecisionEvidenceArtifactType,
    DecisionEvidenceDescriptor,
    DecisionEvidenceRelationship,
    DecisionEvidenceSnapshot,
    DecisionEvidenceSnapshotContent,
    SnapshotIntegrityMetadata,
    SnapshotIntegrityStatus,
    canonical_decision_evidence_snapshot_id,
)
from pumpagent.runtime.modules.decision import build_decision_assessment
from tests.runtime.modules.decision.test_decision_engine import directional_input


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def descriptor(
    *relationships: DecisionEvidenceRelationship,
    artifact_id: str = "process-evidence:episode-1:event-1",
    payload_present: bool = False,
    payload: object | None = None,
    available_at: datetime | None = NOW,
) -> DecisionEvidenceDescriptor:
    return DecisionEvidenceDescriptor(
        artifact_type=DecisionEvidenceArtifactType.PROCESS_EVIDENCE,
        artifact_id=artifact_id,
        episode_id="episode-1",
        runtime_event_id="event-1",
        relationships=frozenset(relationships),
        observation_timestamp=NOW - timedelta(minutes=1),
        available_at=available_at,
        payload_present=payload_present,
        payload=payload,
    )


def content(**changes: object) -> DecisionEvidenceSnapshotContent:
    decision_input = directional_input(long=True)
    decision = build_decision_assessment(decision_input)
    observation = decision_input.process_evidence.observation_timestamp
    values: dict[str, object] = {
        "snapshot_id": canonical_decision_evidence_snapshot_id(
            decision.decision_id
        ),
        "episode_id": decision.episode_id,
        "runtime_event_id": decision.runtime_event_id,
        "decision_id": decision.decision_id,
        "decision_policy_version": "decision_policy_v1",
        "observation_timestamp": observation,
        "snapshot_created_at": decision.created_at + timedelta(seconds=1),
        "decision_input": decision_input,
        "decision_assessment": decision,
        "evidence": (),
    }
    values.update(changes)
    return DecisionEvidenceSnapshotContent(**values)  # type: ignore[arg-type]


class DecisionEvidenceSnapshotContractTests(TestCase):
    def test_valid_pre_digest_snapshot_is_immutable_and_serializable(self) -> None:
        value = DecisionEvidenceSnapshot(
            content=content(),
            integrity=SnapshotIntegrityMetadata(
                integrity_status=SnapshotIntegrityStatus.PRE_DIGEST,
            ),
        )

        self.assertEqual(
            value.content.schema_version,
            DECISION_EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        )
        self.assertEqual(
            value.integrity.schema_version,
            SNAPSHOT_INTEGRITY_METADATA_SCHEMA_VERSION,
        )
        self.assertIsNone(value.integrity.digest_value)
        with self.assertRaises(FrozenInstanceError):
            value.content = content()

        serialized = value.to_dict()
        self.assertEqual(serialized["integrity"]["integrity_status"], "pre_digest")
        self.assertIsNone(serialized["integrity"]["digest_value"])

    def test_nested_collections_are_recursively_immutable(self) -> None:
        item = descriptor(
            DecisionEvidenceRelationship.AVAILABILITY_NOT_PROVEN,
            payload_present=True,
            payload={"nested": ["one", {"value": 2}]},
            available_at=None,
        )
        self.assertIsInstance(item.relationships, frozenset)
        self.assertEqual(item.payload["nested"], ("one", {"value": 2}))
        with self.assertRaises(TypeError):
            item.payload["new"] = "not allowed"
        with self.assertRaises(TypeError):
            item.payload["nested"][1]["value"] = 3
        with self.assertRaisesRegex(ValueError, "immutable primitive"):
            descriptor(
                DecisionEvidenceRelationship.AVAILABILITY_NOT_PROVEN,
                payload_present=True,
                payload=object(),
                available_at=None,
            )

    def test_canonical_snapshot_id_is_decision_derived(self) -> None:
        decision_id = "decision:episode-1:event-1"
        self.assertEqual(
            canonical_decision_evidence_snapshot_id(decision_id),
            "decision-evidence-snapshot:decision:episode-1:event-1",
        )
        with self.assertRaisesRegex(ValueError, "decision_id"):
            canonical_decision_evidence_snapshot_id("")
        with self.assertRaisesRegex(ValueError, "Snapshot ID"):
            content(snapshot_id="forged")

    def test_episode_runtime_and_decision_identity_mismatches_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "share one Episode"):
            content(episode_id="other-episode")
        with self.assertRaisesRegex(ValueError, "share one Runtime event"):
            content(runtime_event_id="other-event")
        with self.assertRaisesRegex(ValueError, "Decision ID"):
            content(decision_id="decision:episode-1:forged")

    def test_embedded_contract_types_are_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "DecisionEngineInput"):
            content(decision_input=object())
        with self.assertRaisesRegex(ValueError, "DecisionAssessment"):
            content(decision_assessment=object())

    def test_decision_references_must_align_with_exact_embedded_input(self) -> None:
        base = content()
        forged = copy.copy(base.decision_assessment)
        object.__setattr__(forged, "hypothesis_reference", "other-hypothesis")
        with self.assertRaisesRegex(ValueError, "hypothesis_reference"):
            replace(base, decision_assessment=forged)

        forged = copy.copy(base.decision_assessment)
        object.__setattr__(forged, "confidence_reference", "other-confidence")
        with self.assertRaisesRegex(ValueError, "confidence_reference"):
            replace(base, decision_assessment=forged)

    def test_observation_time_must_match_input_and_remain_distinct(self) -> None:
        base = content()
        self.assertEqual(
            base.observation_timestamp,
            base.decision_input.process_evidence.observation_timestamp,
        )
        self.assertGreater(base.snapshot_created_at, base.observation_timestamp)
        with self.assertRaisesRegex(ValueError, "observation time"):
            replace(
                base,
                observation_timestamp=base.observation_timestamp
                + timedelta(seconds=1),
            )

    def test_naive_timestamps_are_rejected(self) -> None:
        naive = datetime(2026, 7, 31, 12, 0)
        with self.assertRaisesRegex(ValueError, "snapshot_created_at"):
            content(snapshot_created_at=naive)
        with self.assertRaisesRegex(ValueError, "observation_timestamp"):
            descriptor(
                DecisionEvidenceRelationship.AVAILABLE,
                available_at=NOW,
            ).__class__(
                artifact_type=DecisionEvidenceArtifactType.PROCESS_EVIDENCE,
                artifact_id="process-evidence:episode-1:event-1",
                episode_id="episode-1",
                runtime_event_id="event-1",
                relationships=frozenset(
                    (DecisionEvidenceRelationship.AVAILABLE,)
                ),
                observation_timestamp=naive,
            )
        with self.assertRaisesRegex(ValueError, "available_at"):
            descriptor(
                DecisionEvidenceRelationship.AVAILABLE,
                available_at=naive,
            )

    def test_snapshot_cannot_precede_decision(self) -> None:
        base = content()
        with self.assertRaisesRegex(ValueError, "cannot precede"):
            replace(
                base,
                snapshot_created_at=base.decision_assessment.created_at
                - timedelta(microseconds=1),
            )

    def test_available_at_is_optional_and_never_inferred(self) -> None:
        item = descriptor(
            DecisionEvidenceRelationship.AVAILABILITY_NOT_PROVEN,
            available_at=None,
        )
        self.assertIsNone(item.available_at)
        self.assertNotEqual(item.available_at, item.observation_timestamp)

    def test_availability_not_proven_cannot_be_available_or_used(self) -> None:
        item = descriptor(
            DecisionEvidenceRelationship.AVAILABILITY_NOT_PROVEN,
            payload_present=True,
            payload={"observed": True},
            available_at=None,
        )
        self.assertIn(
            DecisionEvidenceRelationship.AVAILABILITY_NOT_PROVEN,
            item.relationships,
        )
        with self.assertRaisesRegex(ValueError, "treated as available"):
            descriptor(
                DecisionEvidenceRelationship.AVAILABILITY_NOT_PROVEN,
                DecisionEvidenceRelationship.AVAILABLE,
                available_at=None,
            )

    def test_relationship_values_are_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty"):
            descriptor()
        with self.assertRaisesRegex(ValueError, "DecisionEvidenceRelationship"):
            DecisionEvidenceDescriptor(
                artifact_type=DecisionEvidenceArtifactType.PROCESS_EVIDENCE,
                artifact_id="process-evidence:episode-1:event-1",
                episode_id="episode-1",
                runtime_event_id="event-1",
                relationships=frozenset(("available",)),
            )

    def test_admitted_and_considered_remain_explicit_distinct_states(self) -> None:
        available = descriptor(DecisionEvidenceRelationship.AVAILABLE)
        self.assertNotIn(DecisionEvidenceRelationship.ADMITTED, available.relationships)
        with self.assertRaisesRegex(ValueError, "also be AVAILABLE"):
            descriptor(DecisionEvidenceRelationship.ADMITTED)

        admitted = descriptor(
            DecisionEvidenceRelationship.AVAILABLE,
            DecisionEvidenceRelationship.ADMITTED,
        )
        self.assertNotIn(
            DecisionEvidenceRelationship.CONSIDERED,
            admitted.relationships,
        )
        with self.assertRaisesRegex(ValueError, "also be ADMITTED"):
            descriptor(
                DecisionEvidenceRelationship.AVAILABLE,
                DecisionEvidenceRelationship.CONSIDERED,
            )

    def test_use_relationships_require_considered(self) -> None:
        for relationship in (
            DecisionEvidenceRelationship.CONTRIBUTING,
            DecisionEvidenceRelationship.CONTRADICTING,
            DecisionEvidenceRelationship.INHIBITING,
        ):
            with self.subTest(relationship=relationship):
                with self.assertRaisesRegex(ValueError, "must be CONSIDERED"):
                    descriptor(
                        DecisionEvidenceRelationship.AVAILABLE,
                        DecisionEvidenceRelationship.ADMITTED,
                        relationship,
                    )

    def test_post_decision_diagnostic_cannot_enter_decision_use_chain(self) -> None:
        for relationship in (
            DecisionEvidenceRelationship.ADMITTED,
            DecisionEvidenceRelationship.CONSIDERED,
            DecisionEvidenceRelationship.CONTRIBUTING,
        ):
            relationships = {
                DecisionEvidenceRelationship.POST_DECISION_DIAGNOSTIC,
                DecisionEvidenceRelationship.AVAILABLE,
                relationship,
            }
            if relationship is DecisionEvidenceRelationship.CONSIDERED:
                relationships.add(DecisionEvidenceRelationship.ADMITTED)
            if relationship is DecisionEvidenceRelationship.CONTRIBUTING:
                relationships.update(
                    (
                        DecisionEvidenceRelationship.ADMITTED,
                        DecisionEvidenceRelationship.CONSIDERED,
                    )
                )
            with self.subTest(relationship=relationship):
                with self.assertRaisesRegex(ValueError, "POST_DECISION_DIAGNOSTIC"):
                    descriptor(*relationships)

    def test_missing_and_unavailable_evidence_cannot_have_payload_or_use(self) -> None:
        for relationship in (
            DecisionEvidenceRelationship.MISSING,
            DecisionEvidenceRelationship.UNAVAILABLE,
        ):
            with self.subTest(relationship=relationship):
                with self.assertRaisesRegex(ValueError, "cannot contain a payload"):
                    descriptor(
                        relationship,
                        payload_present=True,
                        payload={"fabricated": True},
                        available_at=None,
                    )
                with self.assertRaisesRegex(ValueError, "treated as available"):
                    descriptor(
                        relationship,
                        DecisionEvidenceRelationship.AVAILABLE,
                        available_at=None,
                    )

    def test_context_only_cannot_be_admitted_or_considered(self) -> None:
        with self.assertRaisesRegex(ValueError, "CONTEXT_ONLY"):
            descriptor(
                DecisionEvidenceRelationship.CONTEXT_ONLY,
                DecisionEvidenceRelationship.AVAILABLE,
                DecisionEvidenceRelationship.ADMITTED,
            )

    def test_duplicate_evidence_descriptors_are_rejected(self) -> None:
        item = DecisionEvidenceDescriptor(
            artifact_type=DecisionEvidenceArtifactType.PROCESS_EVIDENCE,
            artifact_id="process-evidence:episode-1:event-1",
            episode_id=content().episode_id,
            runtime_event_id=content().runtime_event_id,
            relationships=frozenset((DecisionEvidenceRelationship.AVAILABLE,)),
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            content(evidence=(item, item))

    def test_evidence_cannot_cross_snapshot_boundaries(self) -> None:
        base = content()
        item = DecisionEvidenceDescriptor(
            artifact_type=DecisionEvidenceArtifactType.PROCESS_EVIDENCE,
            artifact_id="process-evidence:other:event",
            episode_id="other-episode",
            runtime_event_id=base.runtime_event_id,
            relationships=frozenset((DecisionEvidenceRelationship.AVAILABLE,)),
        )
        with self.assertRaisesRegex(ValueError, "Episode boundary"):
            replace(base, evidence=(item,))

    def test_snapshot_and_integrity_versions_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported Decision"):
            content(schema_version="decision_evidence_snapshot_v999")
        with self.assertRaisesRegex(ValueError, "Unsupported Snapshot"):
            SnapshotIntegrityMetadata(
                integrity_status=SnapshotIntegrityStatus.PRE_DIGEST,
                schema_version="snapshot_integrity_metadata_v999",
            )
        with self.assertRaisesRegex(ValueError, "decision_policy_version"):
            content(decision_policy_version="")

    def test_integrity_pre_digest_does_not_generate_fake_values(self) -> None:
        metadata = SnapshotIntegrityMetadata(
            integrity_status=SnapshotIntegrityStatus.PRE_DIGEST,
        )
        self.assertIsNone(metadata.canonicalization_version)
        self.assertIsNone(metadata.digest_algorithm)
        self.assertIsNone(metadata.digest_value)
        with self.assertRaisesRegex(ValueError, "cannot contain digest fields"):
            SnapshotIntegrityMetadata(
                integrity_status=SnapshotIntegrityStatus.PRE_DIGEST,
                digest_algorithm="sha256",
            )

    def test_recorded_integrity_requires_complete_structural_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "DIGEST_RECORDED requires"):
            SnapshotIntegrityMetadata(
                integrity_status=SnapshotIntegrityStatus.DIGEST_RECORDED,
                canonicalization_version="canonical-json-v1",
                digest_algorithm="sha256",
            )
        value = SnapshotIntegrityMetadata(
            integrity_status=SnapshotIntegrityStatus.DIGEST_RECORDED,
            canonicalization_version="canonical-json-v1",
            digest_algorithm="sha256",
            digest_value="abc123",
        )
        self.assertEqual(value.digest_value, "abc123")
