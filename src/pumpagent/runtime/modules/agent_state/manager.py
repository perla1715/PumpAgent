"""Agent State Manager v0.1.

Agent State describes the current official market state. It does not estimate
future scenario probabilities or final confidence.
"""

from __future__ import annotations

from pumpagent.runtime.domain import AgentState, HypothesisPackage, RuntimeEvent
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    StateTransitionStatus,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.hypothesis import MarketHypothesis


MARKET_STATE_TO_AGENT_STATE = {
    "UNKNOWN": AgentStateType.UNKNOWN,
    "IGNITION": AgentStateType.IGNITION,
    "CONTINUATION_ALIVE": AgentStateType.CONTINUATION_ALIVE,
    # TODO: Decide whether WEAKENING maps to CONTINUATION_SATURATION,
    # FIRST_FAILURE_CANDIDATE, or a dedicated future state.
    "WEAKENING": AgentStateType.UNKNOWN,
}


class AgentStateError(ValueError):
    """Raised when Agent State cannot produce an AgentState."""


def build_agent_state(
    hypothesis: HypothesisPackage,
    *,
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


def build_agent_state_from_market_hypothesis(
    hypothesis: MarketHypothesis,
    *,
    previous_state: AgentStateType = AgentStateType.UNKNOWN,
) -> AgentState:
    """Build canonical AgentState from a lightweight MarketHypothesis."""

    current_state = _agent_state_type_from_market_state(hypothesis.market_state)
    transition_status = _transition_status(previous_state, current_state)

    return AgentState(
        event_id=hypothesis.id,
        current_state=current_state,
        previous_state=previous_state,
        state_transition_status=transition_status,
        transition_reason=_market_hypothesis_transition_reason(
            hypothesis,
            current_state,
        ),
        supporting_evidence=hypothesis.supporting_evidence,
        blocking_evidence=hypothesis.contradicting_evidence,
        state_confidence_context=_confidence_level_from_score(
            hypothesis.confidence_score,
        ),
        allowed_next_states=(),
        rejected_state_transitions=_rejected_transitions(),
        notes=(
            "Agent State bridge maps lightweight market hypotheses into the "
            "canonical AgentState domain object."
        ),
    )


def add_agent_state(
    event: RuntimeEvent,
    *,
    previous_state: AgentStateType = AgentStateType.UNKNOWN,
) -> RuntimeEvent:
    """Return a new event with only agent_state added."""

    if event.hypothesis_package is None:
        raise AgentStateError("RuntimeEvent.hypothesis_package is required.")

    agent_state = build_agent_state(
        event.hypothesis_package,
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


def _agent_state_type_from_market_state(market_state: str) -> AgentStateType:
    return MARKET_STATE_TO_AGENT_STATE.get(str(market_state), AgentStateType.UNKNOWN)


def _transition_status(
    previous_state: AgentStateType,
    current_state: AgentStateType,
) -> StateTransitionStatus:
    if previous_state == current_state:
        return StateTransitionStatus.UNCHANGED
    return StateTransitionStatus.CHANGED


def _market_hypothesis_transition_reason(
    hypothesis: MarketHypothesis,
    current_state: AgentStateType,
) -> str:
    if current_state == AgentStateType.UNKNOWN:
        return (
            "Market hypothesis state is unknown or conservatively unmapped: "
            f"{hypothesis.market_state}."
        )

    return f"Canonical state mapped from market hypothesis state {hypothesis.market_state}."


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
