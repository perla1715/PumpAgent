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
    scenario_probability: ScenarioProbability,
    *,
    runtime_event_id: str | None = None,
    data_quality_impact: str = "data_quality_not_independently_assessed_v0.1",
) -> ConfidenceAssessment:
    """Build final reliability assessment without decisions or alerts."""

    event_id = runtime_event_id or hypothesis.event_id
    _validate_inputs(
        hypothesis,
        agent_state,
        scenario_probability,
        runtime_event_id=event_id,
    )

    drivers = _confidence_drivers(hypothesis, agent_state, scenario_probability)
    reducers = _confidence_reducers(hypothesis, agent_state, scenario_probability)
    uncertainty = _overall_uncertainty(hypothesis, scenario_probability)
    final_level = _final_confidence_level(agent_state, uncertainty, reducers)

    return ConfidenceAssessment(
        event_id=event_id,
        final_confidence_level=final_level,
        confidence_summary=(
            "Reliability assessed from hypothesis quality, official state "
            "certainty, scenario uncertainty, evidence, contradictions, and "
            "available data-quality context."
        ),
        confidence_drivers=drivers,
        confidence_reducers=reducers,
        data_quality_impact=data_quality_impact,
        contradiction_impact=_contradiction_impact(hypothesis),
        uncertainty_level=uncertainty,
        schema_version=hypothesis.schema_version,
        numeric_confidence_score=None,
        reliability_notes=(
            "Confidence Engine v0.1 evaluates reliability only. It does not "
            "decide what happens next, generate alerts, or execute trades."
        ),
        calibration_notes="No calibrated numeric confidence score in v0.1.",
    )


def add_confidence_assessment(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only confidence_assessment added."""

    if event.hypothesis_package is None:
        raise ConfidenceError("RuntimeEvent.hypothesis_package is required.")

    if event.agent_state is None:
        raise ConfidenceError("RuntimeEvent.agent_state is required.")

    if event.scenario_probability is None:
        raise ConfidenceError("RuntimeEvent.scenario_probability is required.")

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
    scenario_probability: ScenarioProbability,
    *,
    runtime_event_id: str,
) -> None:
    if hypothesis.event_id != runtime_event_id:
        raise ConfidenceError(
            "HypothesisPackage.event_id must match the RuntimeEvent.event_id."
        )

    if agent_state.event_id != runtime_event_id:
        raise ConfidenceError(
            "AgentState.event_id must match the RuntimeEvent.event_id."
        )

    if scenario_probability.event_id != runtime_event_id:
        raise ConfidenceError(
            "ScenarioProbability.event_id must match the RuntimeEvent.event_id."
        )


def _confidence_drivers(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    scenario_probability: ScenarioProbability,
) -> tuple[str, ...]:
    drivers: list[str] = []

    if hypothesis.supporting_evidence:
        drivers.append("hypothesis_has_supporting_evidence")

    if agent_state.supporting_evidence:
        drivers.append("agent_state_has_supporting_evidence")

    if scenario_probability.scenario_set:
        drivers.append("scenario_set_available")

    return tuple(drivers)


def _confidence_reducers(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    scenario_probability: ScenarioProbability,
) -> tuple[str, ...]:
    reducers: list[str] = []

    if hypothesis.uncertainty in (UncertaintyLevel.HIGH, UncertaintyLevel.UNKNOWN):
        reducers.append("hypothesis_uncertainty_high")

    if agent_state.current_state == AgentStateType.UNKNOWN:
        reducers.append("agent_state_unknown")

    if scenario_probability.uncertainty in (
        UncertaintyLevel.HIGH,
        UncertaintyLevel.UNKNOWN,
    ):
        reducers.append("scenario_uncertainty_high")

    if hypothesis.contradicting_evidence:
        reducers.append("hypothesis_has_contradicting_evidence")

    if scenario_probability.contradicting_evidence:
        reducers.append("scenario_has_contradicting_evidence")

    return tuple(reducers)


def _overall_uncertainty(
    hypothesis: HypothesisPackage,
    scenario_probability: ScenarioProbability,
) -> UncertaintyLevel:
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

    if len(reducers) >= 2:
        return ConfidenceLevel.LOW

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
