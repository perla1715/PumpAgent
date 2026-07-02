"""Decision / Alert v0.1.

Decision / Alert produces human-facing operational output only. It never
executes trades, creates orders, or bypasses human review.
"""

from __future__ import annotations

from pumpagent.runtime.domain import (
    AgentState,
    ConfidenceAssessment,
    DecisionAlert,
    HypothesisPackage,
    RuntimeEvent,
    ScenarioProbability,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    AlertCategory,
    AlertLevel,
    ConfidenceLevel,
    DecisionType,
)


class DecisionAlertError(ValueError):
    """Raised when Decision / Alert cannot produce an output."""


def build_decision_alert(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    scenario_probability: ScenarioProbability,
    confidence_assessment: ConfidenceAssessment,
    *,
    runtime_event_id: str | None = None,
) -> DecisionAlert:
    """Build human-facing operational output without execution behavior."""

    event_id = runtime_event_id or confidence_assessment.event_id
    _validate_inputs(
        hypothesis,
        agent_state,
        scenario_probability,
        confidence_assessment,
        runtime_event_id=event_id,
    )

    if (
        confidence_assessment.final_confidence_level
        in (ConfidenceLevel.UNKNOWN, ConfidenceLevel.VERY_LOW, ConfidenceLevel.LOW)
        or agent_state.current_state == AgentStateType.UNKNOWN
    ):
        decision_type = DecisionType.REVIEW_REQUIRED
        alert_level = AlertLevel.INFO
        alert_category = AlertCategory.WATCH
        review_priority = "normal"
        reason = (
            "Human review required because confidence is low or official state "
            "is UNKNOWN."
        )
        required_human_action = (
            "Review the RuntimeEvent reasoning before taking any external action."
        )
    else:
        # Reserved for future approved non-UNKNOWN / higher-confidence rules.
        # Even this path remains human-facing and non-execution only.
        decision_type = DecisionType.OBSERVE
        alert_level = AlertLevel.NONE
        alert_category = AlertCategory.NO_ACTION
        review_priority = "low"
        reason = "No warning or alert condition produced by Decision / Alert v0.1."
        required_human_action = "Continue observation; no autonomous action is allowed."

    return DecisionAlert(
        event_id=event_id,
        decision_type=decision_type,
        alert_level=alert_level,
        decision_summary="Human-facing operational output only.",
        reason=reason,
        required_human_action=required_human_action,
        non_execution_confirmation=True,
        schema_version=confidence_assessment.schema_version,
        monitoring_instructions=(
            "Do not execute trades from this output.",
            "Use this output only for human review.",
            f"Monitor scenario: {scenario_probability.primary_scenario}.",
        ),
        review_priority=review_priority,
        invalidation_conditions=(
            "new_market_snapshot_available",
            "confidence_assessment_changes",
            "agent_state_changes",
        ),
        follow_up_required=decision_type
        in (DecisionType.REVIEW_REQUIRED, DecisionType.HUMAN_DECISION_REQUIRED),
        display_message=(
            "Decision / Alert v0.1 is non-execution and requires human review."
        ),
        notification_context=(
            "No trade, order, live API call, or autonomous trading signal is produced."
        ),
        alert_category=alert_category,
    )


def add_decision_alert(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only decision_alert added."""

    if event.hypothesis_package is None:
        raise DecisionAlertError("RuntimeEvent.hypothesis_package is required.")

    if event.agent_state is None:
        raise DecisionAlertError("RuntimeEvent.agent_state is required.")

    if event.scenario_probability is None:
        raise DecisionAlertError("RuntimeEvent.scenario_probability is required.")

    if event.confidence_assessment is None:
        raise DecisionAlertError("RuntimeEvent.confidence_assessment is required.")

    decision_alert = build_decision_alert(
        event.hypothesis_package,
        event.agent_state,
        event.scenario_probability,
        event.confidence_assessment,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(decision_alert=decision_alert)


def _validate_inputs(
    hypothesis: HypothesisPackage,
    agent_state: AgentState,
    scenario_probability: ScenarioProbability,
    confidence_assessment: ConfidenceAssessment,
    *,
    runtime_event_id: str,
) -> None:
    if hypothesis.event_id != runtime_event_id:
        raise DecisionAlertError(
            "HypothesisPackage.event_id must match the RuntimeEvent.event_id."
        )

    if agent_state.event_id != runtime_event_id:
        raise DecisionAlertError(
            "AgentState.event_id must match the RuntimeEvent.event_id."
        )

    if scenario_probability.event_id != runtime_event_id:
        raise DecisionAlertError(
            "ScenarioProbability.event_id must match the RuntimeEvent.event_id."
        )

    if confidence_assessment.event_id != runtime_event_id:
        raise DecisionAlertError(
            "ConfidenceAssessment.event_id must match the RuntimeEvent.event_id."
        )
