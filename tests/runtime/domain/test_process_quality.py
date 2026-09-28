from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from pumpagent.runtime.domain import (
    canonical_healthy_baseline_id,
    DiagnosticOutcome,
    HealthyActiveProcessAssessment,
    HealthyBaselineDesignation,
    HealthyBaselineReference,
    LossOfEfficiencyAssessment,
    ProcessQualityAssessment,
    ProcessQualityConcept,
    ProcessQualityEvidenceReference,
    ProcessQualityLifecycleRelation,
    ProcessQualityLifecycleRelationType,
    ProcessQualityObservationReference,
)


BASELINE_TIME = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
CURRENT_TIME = BASELINE_TIME + timedelta(minutes=5)


def observation(*, event="event-1", observation_id="observation-1", at=BASELINE_TIME,
                episode="episode-1"):
    return ProcessQualityObservationReference(
        episode_id=episode,
        runtime_event_id=event,
        observation_id=observation_id,
        observation_timestamp=at,
    )


def evidence(source, *, key="process-active", section="process_evidence"):
    return ProcessQualityEvidenceReference(
        source_observation=source,
        source_section=section,
        evidence_key=key,
        description=f"Canonical evidence reference: {key}.",
    )


def healthy(outcome=DiagnosticOutcome.SUPPORTED, *, source=None):
    source = source or observation()
    supporting = (evidence(source),) if outcome is DiagnosticOutcome.SUPPORTED else ()
    missing = (evidence(source, key="health-comparability"),) if outcome is DiagnosticOutcome.INHIBITED else ()
    contradicting = (evidence(source, key="health-not-established"),) if outcome is DiagnosticOutcome.NOT_ESTABLISHED else ()
    return HealthyActiveProcessAssessment(
        outcome=outcome,
        supporting_evidence=supporting,
        contradicting_evidence=contradicting,
        missing_evidence=missing,
        inhibiting_evidence=(),
        explanation_summary="Healthy Active Process was evaluated explicitly.",
    )


def loss(outcome=None, *, source=None, baseline=None):
    source = source or observation()
    outcome = outcome or (
        DiagnosticOutcome.NOT_ESTABLISHED
        if baseline is not None
        else DiagnosticOutcome.INHIBITED
    )
    supporting = (evidence(source, key="efficiency-loss"),) if outcome is DiagnosticOutcome.SUPPORTED else ()
    missing = (evidence(source, key="healthy_baseline"),) if outcome is DiagnosticOutcome.INHIBITED else ()
    contradicting = (evidence(source, key="efficiency-preserved"),) if outcome is DiagnosticOutcome.NOT_ESTABLISHED else ()
    return LossOfEfficiencyAssessment(
        outcome=outcome,
        healthy_baseline_reference=baseline,
        supporting_evidence=supporting,
        contradicting_evidence=contradicting,
        missing_evidence=missing,
        inhibiting_evidence=(),
        explanation_summary="Loss of Efficiency was evaluated explicitly.",
    )


def assessment(*, assessment_id="assessment-1", current=None, healthy_value=None,
               loss_value=None, episode="episode-1"):
    current = current or observation(episode=episode)
    return ProcessQualityAssessment(
        assessment_id=assessment_id,
        episode_id=episode,
        runtime_event_id=current.runtime_event_id,
        current_observation=current,
        healthy_active_process=healthy_value or healthy(source=current),
        loss_of_efficiency=loss_value or loss(source=current),
    )


def designation(source_assessment, *, baseline_id=None, predecessor=None,
                effective_after=None):
    return HealthyBaselineDesignation(
        baseline_id=baseline_id or canonical_healthy_baseline_id(
            source_assessment.episode_id,
            source_assessment.assessment_id,
        ),
        episode_id=source_assessment.episode_id,
        source_assessment=source_assessment.to_reference(),
        effective_after_assessment=(
            effective_after or source_assessment
        ).to_reference(),
        creation_timestamp=source_assessment.current_observation.observation_timestamp,
        designation_reason="Accepted supported Healthy Active Process baseline.",
        predecessor_baseline=predecessor,
    )


def current_observation():
    return observation(event="event-2", observation_id="observation-2", at=CURRENT_TIME)


def test_supported_healthy_assessment_and_explicit_loss_outcome_serialize():
    result = assessment()
    serialized = result.to_dict()
    assert serialized["healthy_active_process"]["outcome"] == "supported"
    assert serialized["loss_of_efficiency"]["outcome"] == "inhibited"
    assert serialized["current_observation"]["observation_timestamp"].endswith("+00:00")
    json.dumps(serialized)


def test_contracts_are_frozen_and_nested_collections_are_immutable():
    result = assessment()
    assert isinstance(result.healthy_active_process.supporting_evidence, tuple)
    with pytest.raises(FrozenInstanceError):
        result.assessment_id = "changed"
    with pytest.raises(FrozenInstanceError):
        result.healthy_active_process.outcome = DiagnosticOutcome.NOT_ESTABLISHED


@pytest.mark.parametrize("outcome", tuple(DiagnosticOutcome))
def test_all_and_only_approved_diagnostic_outcomes_are_serializable(outcome):
    concept = healthy(outcome)
    assert concept.to_dict()["outcome"] == outcome.value
    with pytest.raises(ValueError, match="DiagnosticOutcome"):
        replace(concept, outcome="unknown")


def test_not_established_and_inhibited_remain_distinct():
    not_established = healthy(DiagnosticOutcome.NOT_ESTABLISHED)
    inhibited = healthy(DiagnosticOutcome.INHIBITED)
    assert not_established.outcome is DiagnosticOutcome.NOT_ESTABLISHED
    assert inhibited.outcome is DiagnosticOutcome.INHIBITED
    assert not_established.contradicting_evidence
    assert inhibited.missing_evidence


def test_supported_requires_support_and_inhibited_requires_explicit_reason_evidence():
    with pytest.raises(ValueError, match="SUPPORTED requires"):
        replace(healthy(), supporting_evidence=())
    with pytest.raises(ValueError, match="INHIBITED requires"):
        HealthyActiveProcessAssessment(
            outcome=DiagnosticOutcome.INHIBITED,
            supporting_evidence=(),
            contradicting_evidence=(evidence(observation()),),
            missing_evidence=(),
            inhibiting_evidence=(),
            explanation_summary="Evaluation inhibited.",
        )


def test_both_concepts_cannot_be_supported_for_one_observation():
    baseline_source = assessment()
    baseline = designation(baseline_source).to_reference()
    current = current_observation()
    with pytest.raises(ValueError, match="cannot both be SUPPORTED"):
        assessment(
            assessment_id="assessment-2",
            current=current,
            healthy_value=healthy(source=current),
            loss_value=loss(DiagnosticOutcome.SUPPORTED, source=current, baseline=baseline),
        )


def test_supported_loss_requires_valid_preceding_same_episode_baseline():
    current = current_observation()
    with pytest.raises(ValueError, match="requires a Healthy Baseline"):
        loss(DiagnosticOutcome.SUPPORTED, source=current)
    with pytest.raises(ValueError, match="requires a Healthy Baseline"):
        loss(DiagnosticOutcome.NOT_ESTABLISHED, source=current)

    inhibited = loss(DiagnosticOutcome.INHIBITED, source=current)
    assert inhibited.healthy_baseline_reference is None
    assert any(
        reference.evidence_key == "healthy_baseline"
        for reference in inhibited.missing_evidence
    )
    with pytest.raises(ValueError, match="missing Healthy Baseline prerequisite"):
        replace(
            inhibited,
            missing_evidence=(
                evidence(current, key="different_missing_prerequisite"),
            ),
        )

    baseline_source = assessment()
    baseline = designation(baseline_source).to_reference()
    result = assessment(
        assessment_id="assessment-2",
        current=current,
        healthy_value=healthy(DiagnosticOutcome.NOT_ESTABLISHED, source=current),
        loss_value=loss(DiagnosticOutcome.SUPPORTED, source=current, baseline=baseline),
    )
    assert result.loss_of_efficiency.healthy_baseline_reference is baseline

    not_established = loss(
        DiagnosticOutcome.NOT_ESTABLISHED,
        source=current,
        baseline=baseline,
    )
    assert not_established.healthy_baseline_reference is baseline

    inhibited_with_baseline = replace(
        loss(DiagnosticOutcome.INHIBITED, source=current, baseline=baseline),
        missing_evidence=(),
        inhibiting_evidence=(evidence(current, key="comparison_inhibited"),),
    )
    assert inhibited_with_baseline.healthy_baseline_reference is baseline

    other_observation = replace(
        baseline.source_assessment.observation,
        episode_id="episode-2",
    )
    other_source = replace(
        baseline.source_assessment,
        episode_id="episode-2",
        observation=other_observation,
    )
    other_episode = replace(
        baseline,
        baseline_id=canonical_healthy_baseline_id(
            "episode-2",
            other_source.assessment_id,
        ),
        episode_id="episode-2",
        source_assessment=other_source,
    )
    with pytest.raises(ValueError, match="same Episode"):
        replace(
            result,
            loss_of_efficiency=replace(
                result.loss_of_efficiency,
                healthy_baseline_reference=other_episode,
            ),
        )

    non_preceding = replace(
        baseline,
        source_assessment=replace(
            baseline.source_assessment,
            observation=current,
            runtime_event_id=current.runtime_event_id,
        ),
    )
    with pytest.raises(ValueError, match="must precede"):
        replace(result, loss_of_efficiency=replace(
            result.loss_of_efficiency, healthy_baseline_reference=non_preceding
        ))


def test_healthy_baseline_reference_requires_canonical_identity():
    source = assessment()
    expected = canonical_healthy_baseline_id(
        source.episode_id,
        source.assessment_id,
    )
    valid = HealthyBaselineReference(
        baseline_id=expected,
        episode_id=source.episode_id,
        source_assessment=source.to_reference(),
    )
    assert valid.baseline_id == expected

    with pytest.raises(ValueError, match="canonical MVP formula"):
        replace(valid, baseline_id="forged-baseline")


def test_empty_baseline_identity_and_unsupported_baseline_source_are_rejected():
    source = assessment()
    valid = designation(source)
    with pytest.raises(ValueError, match="baseline_id"):
        replace(valid, baseline_id=" ")
    with pytest.raises(ValueError, match="Healthy Active Process SUPPORTED"):
        replace(
            valid,
            source_assessment=replace(
                valid.source_assessment,
                healthy_active_process_outcome=DiagnosticOutcome.NOT_ESTABLISHED,
            ),
        )


def test_assessment_and_evidence_cannot_cross_episode_or_come_from_future():
    current = current_observation()
    cross_episode = evidence(
        observation(event="other", observation_id="other", episode="episode-2")
    )
    with pytest.raises(ValueError, match="cannot cross Episode"):
        assessment(
            assessment_id="assessment-2",
            current=current,
            healthy_value=replace(healthy(source=current), supporting_evidence=(cross_episode,)),
        )
    future = evidence(observation(event="future", observation_id="future",
                                  at=CURRENT_TIME + timedelta(minutes=5)))
    with pytest.raises(ValueError, match="future"):
        assessment(
            assessment_id="assessment-2",
            current=current,
            healthy_value=replace(healthy(source=current), supporting_evidence=(future,)),
        )


def test_baseline_designation_is_episode_bound_and_replacement_is_forbidden():
    first_assessment = assessment()
    first = designation(first_assessment)
    assert first.predecessor_baseline is None
    assert first.baseline_id == (
        "healthy-baseline:episode-1:assessment-1"
    )

    later = assessment(assessment_id="assessment-2", current=current_observation())
    with pytest.raises(ValueError, match="replacement is forbidden"):
        designation(
            later,
            predecessor=first.to_reference(),
        )
    with pytest.raises(ValueError, match="effective order cannot precede"):
        designation(
            later,
            effective_after=first_assessment,
        )


def relation(earlier, later, relation_type, *, earlier_concept, later_concept,
             earlier_outcome=DiagnosticOutcome.SUPPORTED,
             later_outcome=DiagnosticOutcome.NOT_ESTABLISHED):
    return ProcessQualityLifecycleRelation(
        relation_id="relation-1",
        episode_id=earlier.episode_id,
        relation_type=relation_type,
        earlier_assessment=earlier.to_reference(),
        earlier_concept=earlier_concept,
        earlier_outcome=earlier_outcome,
        later_assessment=later.to_reference(),
        later_concept=later_concept,
        later_outcome=later_outcome,
        justification_evidence=(
            evidence(later.current_observation, key="lifecycle-comparison"),
        ),
        relation_explanation="Later canonical evidence qualifies the earlier diagnosis.",
    )


@pytest.mark.parametrize("relation_type", [
    ProcessQualityLifecycleRelationType.CONTRADICTED,
    ProcessQualityLifecycleRelationType.INVALIDATED,
])
def test_contradicted_and_invalidated_are_immutable_relations_not_outcomes(relation_type):
    earlier = assessment()
    current = current_observation()
    later = assessment(
        assessment_id="assessment-2",
        current=current,
        healthy_value=healthy(DiagnosticOutcome.NOT_ESTABLISHED, source=current),
        loss_value=loss(source=current),
    )
    value = relation(
        earlier,
        later,
        relation_type,
        earlier_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
        later_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
    )
    assert value.relation_type is relation_type
    assert relation_type.value not in {outcome.value for outcome in DiagnosticOutcome}
    with pytest.raises(FrozenInstanceError):
        value.relation_type = ProcessQualityLifecycleRelationType.RECOVERED


def test_lifecycle_relations_require_same_episode_order_and_earlier_supported():
    earlier = assessment()
    current = current_observation()
    later = assessment(
        assessment_id="assessment-2",
        current=current,
        healthy_value=healthy(DiagnosticOutcome.NOT_ESTABLISHED, source=current),
        loss_value=loss(source=current),
    )
    valid = relation(
        earlier,
        later,
        ProcessQualityLifecycleRelationType.CONTRADICTED,
        earlier_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
        later_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
    )
    with pytest.raises(ValueError, match="cannot cross Episode"):
        replace(valid, episode_id="episode-2")
    with pytest.raises(ValueError, match="must follow"):
        replace(valid, earlier_assessment=later.to_reference(),
                later_assessment=earlier.to_reference())
    with pytest.raises(ValueError, match="earlier SUPPORTED"):
        replace(valid, earlier_outcome=DiagnosticOutcome.NOT_ESTABLISHED)


def test_recovered_requires_earlier_loss_and_later_healthy_supported():
    baseline_source = assessment()
    baseline = designation(baseline_source).to_reference()
    loss_observation = current_observation()
    deteriorated = assessment(
        assessment_id="assessment-2",
        current=loss_observation,
        healthy_value=healthy(DiagnosticOutcome.NOT_ESTABLISHED, source=loss_observation),
        loss_value=loss(DiagnosticOutcome.SUPPORTED, source=loss_observation, baseline=baseline),
    )
    recovery_observation = observation(
        event="event-3", observation_id="observation-3",
        at=CURRENT_TIME + timedelta(minutes=5),
    )
    recovered = assessment(
        assessment_id="assessment-3",
        current=recovery_observation,
        healthy_value=healthy(source=recovery_observation),
        loss_value=loss(source=recovery_observation),
    )
    value = relation(
        deteriorated,
        recovered,
        ProcessQualityLifecycleRelationType.RECOVERED,
        earlier_concept=ProcessQualityConcept.LOSS_OF_EFFICIENCY,
        later_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
        later_outcome=DiagnosticOutcome.SUPPORTED,
    )
    assert value.to_dict()["relation_type"] == "recovered"
    with pytest.raises(ValueError, match="RECOVERED requires"):
        replace(
            value,
            later_assessment=replace(
                value.later_assessment,
                healthy_active_process_outcome=DiagnosticOutcome.NOT_ESTABLISHED,
            ),
            later_outcome=DiagnosticOutcome.NOT_ESTABLISHED,
        )


@pytest.mark.parametrize("relation_type", [
    ProcessQualityLifecycleRelationType.CONTRADICTED,
    ProcessQualityLifecycleRelationType.INVALIDATED,
])
def test_contradicted_and_invalidated_reject_cross_concept_relations(relation_type):
    earlier = assessment()
    current = current_observation()
    later = assessment(
        assessment_id="assessment-2",
        current=current,
        healthy_value=healthy(DiagnosticOutcome.NOT_ESTABLISHED, source=current),
        loss_value=loss(source=current),
    )
    with pytest.raises(ValueError, match="same earlier and later concept"):
        relation(
            earlier,
            later,
            relation_type,
            earlier_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
            later_concept=ProcessQualityConcept.LOSS_OF_EFFICIENCY,
            later_outcome=DiagnosticOutcome.INHIBITED,
        )


@pytest.mark.parametrize("relation_type", [
    ProcessQualityLifecycleRelationType.CONTRADICTED,
    ProcessQualityLifecycleRelationType.INVALIDATED,
])
@pytest.mark.parametrize("later_outcome", [
    DiagnosticOutcome.INHIBITED,
    DiagnosticOutcome.SUPPORTED,
])
def test_contradicted_and_invalidated_require_later_not_established(
    relation_type,
    later_outcome,
):
    earlier = assessment()
    current = current_observation()
    later_healthy = healthy(later_outcome, source=current)
    later = assessment(
        assessment_id="assessment-2",
        current=current,
        healthy_value=later_healthy,
        loss_value=loss(source=current),
    )
    with pytest.raises(ValueError, match="later NOT_ESTABLISHED"):
        relation(
            earlier,
            later,
            relation_type,
            earlier_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
            later_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
            later_outcome=later_outcome,
        )


def test_all_malformed_recovered_concept_combinations_are_rejected():
    earlier = assessment()
    current = current_observation()
    later = assessment(
        assessment_id="assessment-2",
        current=current,
        healthy_value=healthy(DiagnosticOutcome.NOT_ESTABLISHED, source=current),
        loss_value=loss(source=current),
    )
    with pytest.raises(ValueError, match="RECOVERED requires"):
        relation(
            earlier,
            later,
            ProcessQualityLifecycleRelationType.RECOVERED,
            earlier_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
            later_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
        )


def test_optional_summary_is_not_the_canonical_structured_explanation():
    value = replace(healthy(), explanation_summary=None)
    assert value.explanation_summary is None
    assert value.supporting_evidence
    with pytest.raises(ValueError, match="explanation_summary"):
        replace(value, explanation_summary=" ")


def test_every_public_contract_serializes_to_json_primitives():
    first = assessment()
    first_reference = first.to_reference()
    baseline = designation(first)
    baseline_reference = baseline.to_reference()
    current = current_observation()
    second = assessment(
        assessment_id="assessment-2",
        current=current,
        healthy_value=healthy(DiagnosticOutcome.NOT_ESTABLISHED, source=current),
        loss_value=loss(
            DiagnosticOutcome.NOT_ESTABLISHED,
            source=current,
            baseline=baseline_reference,
        ),
    )
    lifecycle = relation(
        first,
        second,
        ProcessQualityLifecycleRelationType.CONTRADICTED,
        earlier_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
        later_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
    )
    values = (
        first.current_observation,
        first.healthy_active_process.supporting_evidence[0],
        first_reference,
        baseline_reference,
        first.healthy_active_process,
        second.loss_of_efficiency,
        first,
        baseline,
        lifecycle,
    )
    for value in values:
        json.dumps(value.to_dict())


def test_reference_identity_timestamp_and_collection_validation_failures():
    with pytest.raises(ValueError, match="timezone-aware"):
        observation(at=BASELINE_TIME.replace(tzinfo=None))
    current = current_observation()
    with pytest.raises(ValueError, match="Runtime event IDs"):
        replace(
            assessment(
                assessment_id="assessment-2",
                current=current,
                healthy_value=healthy(source=current),
                loss_value=loss(source=current),
            ),
            runtime_event_id="different-event",
        )
    duplicate = evidence(current, key="duplicate")
    with pytest.raises(ValueError, match="unique across diagnostic relationships"):
        replace(
            healthy(source=current),
            supporting_evidence=(duplicate,),
            contradicting_evidence=(duplicate,),
        )
    with pytest.raises(ValueError, match="ProcessQualityEvidenceReference"):
        replace(healthy(source=current), supporting_evidence=(object(),))


def test_lifecycle_relation_rejects_same_assessment_empty_or_invalid_evidence():
    earlier = assessment()
    current = current_observation()
    later = assessment(
        assessment_id="assessment-2",
        current=current,
        healthy_value=healthy(DiagnosticOutcome.NOT_ESTABLISHED, source=current),
        loss_value=loss(source=current),
    )
    valid = relation(
        earlier,
        later,
        ProcessQualityLifecycleRelationType.CONTRADICTED,
        earlier_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
        later_concept=ProcessQualityConcept.HEALTHY_ACTIVE_PROCESS,
    )
    with pytest.raises(ValueError, match="distinct assessments"):
        replace(valid, later_assessment=valid.earlier_assessment)
    with pytest.raises(ValueError, match="require justification evidence"):
        replace(valid, justification_evidence=())
    future = evidence(
        observation(
            event="future-event",
            observation_id="future-observation",
            at=CURRENT_TIME + timedelta(minutes=5),
        )
    )
    with pytest.raises(ValueError, match="future"):
        replace(valid, justification_evidence=(future,))
    other_episode = evidence(
        observation(
            event="other-event",
            observation_id="other-observation",
            episode="episode-2",
        )
    )
    with pytest.raises(ValueError, match="cannot cross Episode"):
        replace(valid, justification_evidence=(other_episode,))


def test_baseline_rejects_noncanonical_identity_predecessor_and_empty_reason():
    first = designation(assessment())
    with pytest.raises(ValueError, match="replacement is forbidden"):
        replace(first, predecessor_baseline=first.to_reference())
    with pytest.raises(ValueError, match="canonical MVP formula"):
        replace(first, baseline_id="externally-forged-baseline")
    with pytest.raises(ValueError, match="designation_reason"):
        replace(first, designation_reason="")


def test_contracts_do_not_own_downstream_or_trading_fields():
    forbidden = {
        "confidence", "hypothesis", "scenario_probability", "decision", "alert",
        "trade_permission", "entry", "execution", "position_size",
    }
    assert not forbidden & set(ProcessQualityAssessment.__dataclass_fields__)
    assert not forbidden & set(HealthyActiveProcessAssessment.__dataclass_fields__)
    assert not forbidden & set(LossOfEfficiencyAssessment.__dataclass_fields__)
    assert "diagnostic_explanation" not in HealthyActiveProcessAssessment.__dataclass_fields__
    assert "diagnostic_explanation" not in LossOfEfficiencyAssessment.__dataclass_fields__
