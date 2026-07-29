"""Confidence Engine v0.1.

Confidence evaluates reliability of the current Runtime reasoning. It does not
decide what happens next, generate alerts, or execute trades.
"""

from __future__ import annotations

from pumpagent.runtime.domain import (
    AgentState,
    ConfidenceAssessment,
    HypothesisPackage,
    RuntimeEvent,
    ScenarioProbability,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.market_metrics import calculate_confidence


class ConfidenceError(ValueError):
    """Raised when Confidence cannot produce a ConfidenceAssessment."""


def build_confidence_assessment(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    scenario_probability: ScenarioProbability | None,
    *,
    runtime_event_id: str | None = None,
    active_episode_id: str | None = None,
    data_quality_impact: str = "data_quality_not_independently_assessed_v0.1",
) -> ConfidenceAssessment:
    """Build final reliability assessment without decisions or alerts."""

    event_id = runtime_event_id or hypothesis.event_id
    _validate_inputs(
        hypothesis,
        agent_state,
        scenario_probability,
        runtime_event_id=event_id,
        active_episode_id=active_episode_id,
    )

    drivers = _confidence_drivers(
        hypothesis,
        agent_state,
        scenario_probability,
        data_quality_impact=data_quality_impact,
    )
    reducers = _confidence_reducers(
        hypothesis,
        agent_state,
        scenario_probability,
        data_quality_impact=data_quality_impact,
    )
    uncertainty = _overall_uncertainty(hypothesis, scenario_probability)
    final_level = _final_confidence_level(
        agent_state,
        scenario_probability,
        uncertainty,
        reducers,
    )

    return ConfidenceAssessment(
        event_id=event_id,
        episode_id=hypothesis.episode_id,
        source_hypothesis_id=hypothesis.hypothesis_id,
        final_confidence_level=final_level,
        confidence_summary=(
            "Reliability assessed from hypothesis support, official state "
            "certainty, Scenario Probability availability, scenario uncertainty, "
            "contradictions, and available data-quality context."
        ),
        confidence_drivers=drivers,
        confidence_reducers=reducers,
        data_quality_impact=data_quality_impact,
        contradiction_impact=_contradiction_impact(hypothesis),
        uncertainty_level=uncertainty,
        schema_version=hypothesis.schema_version,
        numeric_confidence_score=None,
        reliability_notes=(
            "Confidence Engine v0.1 evaluates reliability only for Runtime "
            "reasoning. It does not inspect raw market metrics, does not decide "
            "what happens next, generate alerts, or execute trades. HIGH "
            "confidence is not allowed in this MVP."
        ),
        calibration_notes=(
            "No calibrated numeric confidence score in v0.1. RuntimeEvent "
            "confidence is capped at MEDIUM; legacy scanner numeric confidence "
            "remains separate."
        ),
    )


def add_confidence_assessment(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only confidence_assessment added."""

    if event.hypothesis_package is None:
        raise ConfidenceError("RuntimeEvent.hypothesis_package is required.")

    if event.agent_state is None:
        raise ConfidenceError("RuntimeEvent.agent_state is required.")

    assessment = build_confidence_assessment(
        event.hypothesis_package,
        event.agent_state,
        event.scenario_probability,
        runtime_event_id=event.event_id,
        data_quality_impact=_data_quality_impact(event),
    )
    return event.with_sections(confidence_assessment=assessment)


def _validate_inputs(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    scenario_probability: ScenarioProbability | None,
    *,
    runtime_event_id: str,
    active_episode_id: str | None,
) -> None:
    if hypothesis.event_id != runtime_event_id:
        raise ConfidenceError(
            "HypothesisPackage.event_id must match the RuntimeEvent.event_id."
        )

    if agent_state.event_id != runtime_event_id:
        raise ConfidenceError(
            "AgentState.event_id must match the RuntimeEvent.event_id."
        )

    if (
        scenario_probability is not None
        and scenario_probability.runtime_event_id != runtime_event_id
    ):
        raise ConfidenceError(
            "ScenarioProbability.runtime_event_id must match the "
            "RuntimeEvent.event_id."
        )

    if (
        scenario_probability is not None
        and scenario_probability.episode_id != hypothesis.episode_id
    ):
        raise ConfidenceError(
            "ScenarioProbability.episode_id must match HypothesisPackage.episode_id."
        )

    if (
        scenario_probability is not None
        and scenario_probability.source_hypothesis_id != hypothesis.hypothesis_id
    ):
        raise ConfidenceError(
            "ScenarioProbability.source_hypothesis_id must match "
            "HypothesisPackage.hypothesis_id."
        )

    if (
        active_episode_id is not None
        and active_episode_id != hypothesis.episode_id
    ):
        raise ConfidenceError(
            "Active episode ID must match HypothesisPackage.episode_id."
        )


def _confidence_drivers(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    scenario_probability: ScenarioProbability | None,
    *,
    data_quality_impact: str,
) -> tuple[str, ...]:
    drivers: list[str] = []

    if agent_state.current_state != AgentStateType.UNKNOWN:
        drivers.append("agent_state_known")

    if scenario_probability is not None:
        drivers.append("scenario_probability_available")

        if scenario_probability.uncertainty not in (
            UncertaintyLevel.HIGH,
            UncertaintyLevel.UNKNOWN,
        ):
            drivers.append("scenario_uncertainty_not_high")

        if _scenario_weights_sum_valid(scenario_probability):
            drivers.append("scenario_weights_sum_valid")

    if hypothesis.supporting_evidence:
        drivers.append("hypothesis_has_supporting_evidence")

    if hypothesis.current_hypothesis_confidence_context not in (
        ConfidenceLevel.UNKNOWN,
        ConfidenceLevel.VERY_LOW,
        ConfidenceLevel.LOW,
    ):
        drivers.append("hypothesis_confidence_context_not_low")

    if _data_quality_is_acceptable(data_quality_impact):
        drivers.append("data_quality_acceptable")

    return tuple(drivers)


def _confidence_reducers(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    scenario_probability: ScenarioProbability | None,
    *,
    data_quality_impact: str,
) -> tuple[str, ...]:
    reducers: list[str] = []

    if agent_state.current_state == AgentStateType.UNKNOWN:
        reducers.append("agent_state_unknown")

    if scenario_probability is None:
        reducers.append("scenario_probability_missing")
    else:
        if scenario_probability.uncertainty == UncertaintyLevel.HIGH:
            reducers.append("scenario_uncertainty_high")
        elif scenario_probability.uncertainty == UncertaintyLevel.UNKNOWN:
            reducers.append("scenario_uncertainty_unknown")

        if scenario_probability.contradicting_provenance:
            reducers.append("scenario_has_contradicting_evidence")

    if not hypothesis.supporting_evidence:
        reducers.append("hypothesis_context_missing_or_generic")

    if hypothesis.uncertainty in (UncertaintyLevel.HIGH, UncertaintyLevel.UNKNOWN):
        reducers.append("hypothesis_uncertainty_high")

    if hypothesis.contradicting_evidence:
        reducers.append("hypothesis_has_contradicting_evidence")

    if not _data_quality_is_acceptable(data_quality_impact):
        reducers.append("data_quality_incomplete_or_poor")

    return tuple(reducers)


def _overall_uncertainty(
    hypothesis: HypothesisPackage,
    scenario_probability: ScenarioProbability | None,
) -> UncertaintyLevel:
    if scenario_probability is None:
        return UncertaintyLevel.HIGH

    if (
        hypothesis.uncertainty == UncertaintyLevel.HIGH
        or scenario_probability.uncertainty == UncertaintyLevel.HIGH
    ):
        return UncertaintyLevel.HIGH

    if (
        hypothesis.uncertainty == UncertaintyLevel.UNKNOWN
        or scenario_probability.uncertainty == UncertaintyLevel.UNKNOWN
    ):
        return UncertaintyLevel.UNKNOWN

    if (
        hypothesis.uncertainty == UncertaintyLevel.MEDIUM
        or scenario_probability.uncertainty == UncertaintyLevel.MEDIUM
    ):
        return UncertaintyLevel.MEDIUM

    return UncertaintyLevel.LOW


def _final_confidence_level(
    agent_state: AgentState,
    scenario_probability: ScenarioProbability | None,
    uncertainty: UncertaintyLevel,
    reducers: tuple[str, ...],
) -> ConfidenceLevel:
    """Return conservative categorical confidence.

    MEDIUM is only a v0.1 upper bound, not calibrated scoring.
    """

    if uncertainty in (UncertaintyLevel.HIGH, UncertaintyLevel.UNKNOWN):
        return ConfidenceLevel.LOW

    if agent_state.current_state == AgentStateType.UNKNOWN:
        return ConfidenceLevel.LOW

    if scenario_probability is None:
        return ConfidenceLevel.LOW

    if reducers:
        return ConfidenceLevel.LOW

    # HIGH is intentionally unavailable until Confidence is calibrated or
    # historically validated.
    return ConfidenceLevel.MEDIUM


def _contradiction_impact(hypothesis: HypothesisPackage) -> str:
    if hypothesis.contradicting_evidence:
        return "contradictions_reduce_reliability"
    return "no_contradicting_evidence_reported"


def _data_quality_impact(event: RuntimeEvent) -> str:
    if event.market_snapshot is not None:
        return f"market_snapshot_data_quality:{event.market_snapshot.data_quality_status.value}"

    if event.observation_package is not None:
        return (
            "observation_package_data_quality:"
            f"{event.observation_package.data_quality_status.value}"
        )

    return "data_quality_not_available"


def _data_quality_is_acceptable(data_quality_impact: str) -> bool:
    return data_quality_impact.endswith(":valid")


def _scenario_weights_sum_valid(scenario_probability: ScenarioProbability) -> bool:
    return (
        sum(
            (item.probability for item in scenario_probability.distribution),
            start=0,
        )
        == 1
    )
