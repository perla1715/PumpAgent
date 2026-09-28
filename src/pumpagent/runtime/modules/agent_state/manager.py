"""Agent State Manager v0.1.

Agent State describes the current official market state. It does not estimate
future scenario probabilities or final confidence.
"""

from __future__ import annotations

from pumpagent.runtime.domain import AgentState, HypothesisPackage, RuntimeEvent
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    ProcessDirection,
    StateTransitionStatus,
    UncertaintyLevel,
)


class AgentStateError(ValueError):
    """Raised when Agent State cannot produce an AgentState."""


def build_agent_state(
    hypothesis: HypothesisPackage,
    *,
    process_direction: ProcessDirection,
    runtime_event_id: str | None = None,
    previous_state: AgentStateType = AgentStateType.UNKNOWN,
) -> AgentState:
    """Build the current official state from the current-condition hypothesis."""

    event_id = runtime_event_id or hypothesis.event_id
    _validate_hypothesis_alignment(hypothesis, runtime_event_id=event_id)

    current_state = _current_state_from_hypothesis(hypothesis)
    transition_status = _transition_status(previous_state, current_state)

    return AgentState(
        event_id=event_id,
        current_state=current_state,
        process_direction=process_direction,
        previous_state=previous_state,
        state_transition_status=transition_status,
        transition_reason=_transition_reason(hypothesis, current_state),
        supporting_evidence=hypothesis.supporting_evidence,
        blocking_evidence=hypothesis.contradicting_evidence,
        state_confidence_context=hypothesis.current_hypothesis_confidence_context,
        schema_version=hypothesis.schema_version,
        allowed_next_states=(),
        rejected_state_transitions=_rejected_transitions(),
        notes=(
            "Agent State Manager v0.1 describes only the current official "
            "state; Scenario Probability will evaluate possible next scenarios."
        ),
    )


def build_agent_state_from_hypothesis_package(
    hypothesis: HypothesisPackage,
    *,
    event_id: str,
    previous_state: AgentStateType,
    canonical_process_state: str,
    canonical_process_direction: ProcessDirection,
    supporting_evidence: tuple[str, ...],
    contradicting_evidence: tuple[str, ...],
) -> AgentState:
    """Preserve operational Agent State behavior from canonical inputs."""

    if hypothesis.event_id != event_id:
        raise AgentStateError("Hypothesis Runtime event identity does not align.")
    if not isinstance(canonical_process_direction, ProcessDirection):
        raise AgentStateError(
            "canonical_process_direction must be a ProcessDirection."
        )
    current_state = _agent_state_type_from_process_state(canonical_process_state)
    transition_status = _transition_status(previous_state, current_state)
    return AgentState(
        event_id=event_id,
        current_state=current_state,
        process_direction=canonical_process_direction,
        previous_state=previous_state,
        state_transition_status=transition_status,
        transition_reason=(
            "Market hypothesis state is unknown or conservatively unmapped: "
            f"{canonical_process_state}."
            if current_state is AgentStateType.UNKNOWN
            else (
                "Canonical state mapped from market hypothesis state "
                f"{canonical_process_state}."
            )
        ),
        supporting_evidence=supporting_evidence,
        blocking_evidence=contradicting_evidence,
        state_confidence_context=_confidence_level_from_score(
            hypothesis.explanation_confidence_score
        ),
        allowed_next_states=(),
        rejected_state_transitions=_rejected_transitions(),
        notes=(
            "Agent State bridge maps canonical Hypothesis and Process inputs "
            "without changing operational state rules."
        ),
    )


def add_agent_state(
    event: RuntimeEvent,
    *,
    process_direction: ProcessDirection,
    previous_state: AgentStateType = AgentStateType.UNKNOWN,
) -> RuntimeEvent:
    """Return a new event with only agent_state added."""

    if event.hypothesis_package is None:
        raise AgentStateError("RuntimeEvent.hypothesis_package is required.")

    agent_state = build_agent_state(
        event.hypothesis_package,
        process_direction=process_direction,
        runtime_event_id=event.event_id,
        previous_state=previous_state,
    )
    return event.with_sections(agent_state=agent_state)


def _validate_hypothesis_alignment(
    hypothesis: HypothesisPackage,
    *,
    runtime_event_id: str,
) -> None:
    if hypothesis.event_id != runtime_event_id:
        raise AgentStateError(
            "HypothesisPackage.event_id must match the RuntimeEvent.event_id."
        )


def _current_state_from_hypothesis(hypothesis: HypothesisPackage) -> AgentStateType:
    """Return UNKNOWN for all v0.1 inputs.

    Specific state assignment is deferred until explicit transition rules are
    approved.
    """

    if hypothesis.uncertainty in (
        UncertaintyLevel.HIGH,
        UncertaintyLevel.UNKNOWN,
    ):
        return AgentStateType.UNKNOWN

    if hypothesis.current_hypothesis_confidence_context in (
        ConfidenceLevel.UNKNOWN,
        ConfidenceLevel.VERY_LOW,
        ConfidenceLevel.LOW,
    ):
        return AgentStateType.UNKNOWN

    return AgentStateType.UNKNOWN


def _agent_state_type_from_process_state(process_state: str) -> AgentStateType:
    """Conservative MVP bridge; WEAKENING has no non-deferred Agent State."""
    if process_state == "CONTINUATION_ALIVE":
        return AgentStateType.CONTINUATION_ALIVE
    return AgentStateType.UNKNOWN


def _transition_status(
    previous_state: AgentStateType,
    current_state: AgentStateType,
) -> StateTransitionStatus:
    if previous_state == current_state:
        return StateTransitionStatus.UNCHANGED
    return StateTransitionStatus.CHANGED


def _confidence_level_from_score(score: int) -> ConfidenceLevel:
    if score >= 80:
        return ConfidenceLevel.HIGH
    if score >= 50:
        return ConfidenceLevel.MEDIUM
    if score > 0:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.UNKNOWN


def _transition_reason(
    hypothesis: HypothesisPackage,
    current_state: AgentStateType,
) -> str:
    if current_state == AgentStateType.UNKNOWN:
        return (
            "Evidence is insufficient for a specific official state; "
            f"hypothesis uncertainty is {hypothesis.uncertainty.value}."
        )

    return "Official state assigned from current-condition hypothesis."


def _rejected_transitions() -> tuple[AgentStateType, ...]:
    return (
        AgentStateType.IGNITION,
        AgentStateType.CONTINUATION_ALIVE,
        AgentStateType.CONTINUATION_SATURATION,
        AgentStateType.FIRST_FAILURE_CANDIDATE,
        AgentStateType.FIRST_FAILURE,
        AgentStateType.CONTINUATION_DEATH,
    )
