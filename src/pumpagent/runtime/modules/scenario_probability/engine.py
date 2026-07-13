"""Scenario Probability Engine v0.1.

Scenario Probability estimates possible next scenarios. It does not describe
the current state, make decisions, produce final confidence, or trigger alerts.
"""

from __future__ import annotations

from pumpagent.runtime.domain import (
    AgentState,
    HypothesisPackage,
    RuntimeEvent,
    ScenarioProbability,
)
from pumpagent.runtime.domain.enums import AgentStateType, UncertaintyLevel


UNKNOWN_STATE_SCENARIOS = (
    "continue_observation",
    "insufficient_evidence_persists",
    "state_clarifies_after_more_data",
)

SCENARIO_POLICIES = {
    AgentStateType.UNKNOWN: {
        "scenario_probabilities": {
            "continue_observation": 0.40,
            "insufficient_evidence_persists": 0.35,
            "state_clarifies_after_more_data": 0.25,
        },
        "primary_scenario": "continue_observation",
        "uncertainty": UncertaintyLevel.HIGH,
        "monitoring_focus": (
            "collect_more_evidence",
            "wait_for_state_clarity",
            "monitor_missing_or_contradicting_evidence",
        ),
        "notes": (
            "UNKNOWN current state prevents precise scenario weighting. "
            "Continue observation until the official state clarifies."
        ),
    },
    AgentStateType.CONTINUATION_ALIVE: {
        "scenario_probabilities": {
            "continuation_persists": 0.55,
            "continuation_degrades_to_saturation": 0.30,
            "first_failure_candidate_emerges": 0.15,
        },
        "primary_scenario": "continuation_persists",
        "uncertainty": UncertaintyLevel.MEDIUM,
        "monitoring_focus": (
            "continuation_quality",
            "participation_support",
            "contradiction_emergence",
        ),
        "notes": (
            "CONTINUATION_ALIVE favors persistence, while keeping degradation "
            "and first-failure candidacy visible as alternatives."
        ),
    },
    AgentStateType.CONTINUATION_SATURATION: {
        "scenario_probabilities": {
            "saturation_resolves_to_continuation": 0.25,
            "saturation_persists": 0.45,
            "first_failure_risk_increases": 0.30,
        },
        "primary_scenario": "saturation_persists",
        "uncertainty": UncertaintyLevel.MEDIUM,
        "monitoring_focus": (
            "reclaim_quality",
            "weakening_persistence",
            "participation_deterioration",
        ),
        "notes": (
            "CONTINUATION_SATURATION centers on unresolved saturation; it "
            "does not assume reversal or confirmed failure."
        ),
    },
    AgentStateType.FIRST_FAILURE_CANDIDATE: {
        "scenario_probabilities": {
            "failure_candidate_invalidated": 0.20,
            "failure_candidate_persists": 0.45,
            "first_failure_confirms": 0.35,
        },
        "primary_scenario": "failure_candidate_persists",
        "uncertainty": UncertaintyLevel.MEDIUM,
        "monitoring_focus": (
            "failed_reclaim",
            "contradiction_persistence",
            "invalidation_evidence",
        ),
        "notes": (
            "FIRST_FAILURE_CANDIDATE keeps invalidation, persistence, and "
            "confirmation separate; it does not treat candidate status as "
            "confirmed failure."
        ),
    },
}


class ScenarioProbabilityError(ValueError):
    """Raised when Scenario Probability cannot produce a package."""


def build_scenario_probability(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    *,
    runtime_event_id: str | None = None,
) -> ScenarioProbability:
    """Build possible-next-scenarios from current hypothesis and state."""

    event_id = runtime_event_id or hypothesis.event_id
    _validate_inputs(
        hypothesis,
        agent_state,
        runtime_event_id=event_id,
    )

    policy = SCENARIO_POLICIES.get(
        agent_state.current_state,
        SCENARIO_POLICIES[AgentStateType.UNKNOWN],
    )
    probabilities = policy["scenario_probabilities"]
    scenario_set = tuple(probabilities)
    primary_scenario = policy["primary_scenario"]
    alternatives = tuple(
        scenario for scenario in scenario_set if scenario != primary_scenario
    )
    uncertainty = policy["uncertainty"]
    monitoring_focus = policy["monitoring_focus"]
    notes = (
        "Scenario probabilities are deterministic MVP weights, not calibrated "
        "predictions. Scenario Probability translates official Agent State "
        "into possible next scenarios only; it does not inspect raw market "
        "data, produce final confidence, make decisions, or trigger alerts. "
        f"{policy['notes']}"
    )
    _validate_probabilities(probabilities)

    return ScenarioProbability(
        event_id=event_id,
        scenario_set=scenario_set,
        scenario_probabilities=probabilities,
        primary_scenario=primary_scenario,
        alternative_scenarios=alternatives,
        supporting_evidence=hypothesis.supporting_evidence,
        contradicting_evidence=hypothesis.contradicting_evidence,
        uncertainty=uncertainty,
        monitoring_focus=monitoring_focus,
        schema_version=hypothesis.schema_version,
        scenario_time_horizon="next_runtime_cycle",
        scenario_notes=notes,
        metadata={
            "source_hypothesis_event_id": hypothesis.event_id,
            "source_agent_state": agent_state.current_state.value,
            "engine_version": "v0.1",
            "probability_model": "deterministic_mvp_weights",
        },
    )


def add_scenario_probability(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only scenario_probability added."""

    if event.hypothesis_package is None:
        raise ScenarioProbabilityError(
            "RuntimeEvent.hypothesis_package is required."
        )

    if event.agent_state is None:
        raise ScenarioProbabilityError("RuntimeEvent.agent_state is required.")

    scenario_probability = build_scenario_probability(
        event.hypothesis_package,
        event.agent_state,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(scenario_probability=scenario_probability)


def _validate_inputs(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    *,
    runtime_event_id: str,
) -> None:
    if hypothesis.event_id != runtime_event_id:
        raise ScenarioProbabilityError(
            "HypothesisPackage.event_id must match the RuntimeEvent.event_id."
        )

    if agent_state.event_id != runtime_event_id:
        raise ScenarioProbabilityError(
            "AgentState.event_id must match the RuntimeEvent.event_id."
        )


def _validate_probabilities(probabilities: dict[str, float]) -> None:
    if round(sum(probabilities.values()), 10) != 1.0:
        raise ScenarioProbabilityError("Scenario probabilities must sum to 1.0.")
