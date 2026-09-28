"""AgentState domain model."""

from __future__ import annotations

from dataclasses import dataclass

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    ProcessDirection,
    StateTransitionStatus,
)


AGENT_STATE_SCHEMA_VERSION = "agent_state_v2"


@dataclass(frozen=True)
class AgentState(SerializableMixin):
    event_id: str
    current_state: AgentStateType
    process_direction: ProcessDirection
    previous_state: AgentStateType
    state_transition_status: StateTransitionStatus
    transition_reason: str
    supporting_evidence: tuple[str, ...]
    blocking_evidence: tuple[str, ...]
    state_confidence_context: ConfidenceLevel
    schema_version: str = AGENT_STATE_SCHEMA_VERSION
    state_duration: int | None = None
    previous_state_duration: int | None = None
    allowed_next_states: tuple[AgentStateType, ...] = ()
    rejected_state_transitions: tuple[AgentStateType, ...] = ()
    state_history_reference: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.process_direction, ProcessDirection):
            raise ValueError("process_direction must be a ProcessDirection.")
