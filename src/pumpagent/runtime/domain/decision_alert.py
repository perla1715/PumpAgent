"""DecisionAlert domain model."""

from __future__ import annotations

from dataclasses import dataclass

from pumpagent.runtime.domain.base import SerializableMixin
from pumpagent.runtime.domain.enums import AlertCategory, AlertLevel, DecisionType


@dataclass(frozen=True)
class DecisionAlert(SerializableMixin):
    event_id: str
    decision_type: DecisionType
    alert_level: AlertLevel
    decision_summary: str
    reason: str
    required_human_action: str
    non_execution_confirmation: bool
    schema_version: str = "1.0"
    monitoring_instructions: tuple[str, ...] = ()
    review_priority: str | None = None
    invalidation_conditions: tuple[str, ...] = ()
    follow_up_required: bool = False
    display_message: str | None = None
    notification_context: str | None = None
    alert_category: AlertCategory = AlertCategory.NO_ACTION
