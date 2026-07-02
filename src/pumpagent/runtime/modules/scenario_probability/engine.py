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

    if agent_state.current_state == AgentStateType.UNKNOWN:
        scenario_set = UNKNOWN_STATE_SCENARIOS
        probabilities = _equal_probabilities(scenario_set)
        primary_scenario = "continue_observation"
        alternatives = tuple(
            scenario for scenario in scenario_set if scenario != primary_scenario
        )
        uncertainty = UncertaintyLevel.HIGH
        notes = (
            "Scenario probabilities are contextual and illustrative in v0.1. "
            "UNKNOWN current state prevents precise scenario weighting. "
            "They are not final confidence, not decisions, and not alert triggers."
        )
    else:
        # Reserved for future approved Agent State transition rules. Agent State
        # v0.1 currently returns UNKNOWN for all runtime inputs.
        scenario_set = ("state_continuation", "state_change_requires_review")
        probabilities = _equal_probabilities(scenario_set)
        primary_scenario = "state_continuation"
        alternatives = ("state_change_requires_review",)
        uncertainty = UncertaintyLevel.MEDIUM
        notes = (
            "Scenario probabilities are contextual and illustrative in v0.1. "
            "They do not represent final confidence, decisions, or alert triggers."
        )

    return ScenarioProbability(
        event_id=event_id,
        scenario_set=scenario_set,
        scenario_probabilities=probabilities,
        primary_scenario=primary_scenario,
        alternative_scenarios=alternatives,
        supporting_evidence=hypothesis.supporting_evidence,
        contradicting_evidence=hypothesis.contradicting_evidence,
        uncertainty=uncertainty,
        monitoring_focus=(
            "collect_more_evidence",
            "wait_for_state_clarity",
        ),
        schema_version=hypothesis.schema_version,
        scenario_time_horizon="next_runtime_cycle",
        scenario_notes=notes,
        metadata={
            "source_hypothesis_event_id": hypothesis.event_id,
            "source_agent_state": agent_state.current_state.value,
            "engine_version": "v0.1",
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


def _equal_probabilities(scenarios: tuple[str, ...]) -> dict[str, float]:
    probability = round(1.0 / len(scenarios), 4)
    probabilities = {scenario: probability for scenario in scenarios}
    remainder = round(1.0 - sum(probabilities.values()), 4)
    if remainder:
        probabilities[scenarios[0]] = round(probabilities[scenarios[0]] + remainder, 4)
    return probabilities
