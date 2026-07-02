"""AgentState domain model."""

from __future__ import annotations

from dataclasses import dataclass

from pumpagent.runtime.domain.base import SerializableMixin
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    StateTransitionStatus,
)


@dataclass(frozen=True)
class AgentState(SerializableMixin):
    event_id: str
    current_state: AgentStateType
    previous_state: AgentStateType
    state_transition_status: StateTransitionStatus
    transition_reason: str
    supporting_evidence: tuple[str, ...]
    blocking_evidence: tuple[str, ...]
    state_confidence_context: ConfidenceLevel
    schema_version: str = "1.0"
    state_duration: int | None = None
    previous_state_duration: int | None = None
    allowed_next_states: tuple[AgentStateType, ...] = ()
    rejected_state_transitions: tuple[AgentStateType, ...] = ()
    state_history_reference: str | None = None
    notes: str | None = None
