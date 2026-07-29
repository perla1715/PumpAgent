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
    scenario_probability: ScenarioProbability | None,
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

    decision_type, alert_level, alert_category, review_priority, reason = (
        _attention_policy(agent_state, scenario_probability, confidence_assessment)
    )

    return DecisionAlert(
        event_id=event_id,
        decision_type=decision_type,
        alert_level=alert_level,
        decision_summary="Human-facing operational awareness only.",
        reason=reason,
        required_human_action=_required_human_action(decision_type),
        non_execution_confirmation=True,
        schema_version=confidence_assessment.schema_version,
        monitoring_instructions=_monitoring_instructions(scenario_probability),
        review_priority=review_priority,
        invalidation_conditions=(
            "new_market_snapshot_available",
            "confidence_assessment_changes",
            "agent_state_changes",
        ),
        follow_up_required=decision_type
        in (DecisionType.REVIEW_REQUIRED, DecisionType.HUMAN_DECISION_REQUIRED),
        display_message=(
            "Decision / Alert v0.1 provides operational awareness for human review."
        ),
        notification_context=(
            "No order, live API call, or autonomous instruction is produced."
        ),
        alert_category=alert_category,
    )


def add_decision_alert(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only decision_alert added."""

    if event.hypothesis_package is None:
        raise DecisionAlertError("RuntimeEvent.hypothesis_package is required.")

    if event.agent_state is None:
        raise DecisionAlertError("RuntimeEvent.agent_state is required.")

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
    scenario_probability: ScenarioProbability | None,
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

    if (
        scenario_probability is not None
        and scenario_probability.runtime_event_id != runtime_event_id
    ):
        raise DecisionAlertError(
            "ScenarioProbability.runtime_event_id must match the RuntimeEvent.event_id."
        )

    if (
        scenario_probability is not None
        and scenario_probability.episode_id != hypothesis.episode_id
    ):
        raise DecisionAlertError(
            "ScenarioProbability.episode_id must match HypothesisPackage.episode_id."
        )

    if (
        scenario_probability is not None
        and scenario_probability.source_hypothesis_id != hypothesis.hypothesis_id
    ):
        raise DecisionAlertError(
            "ScenarioProbability.source_hypothesis_id must match "
            "HypothesisPackage.hypothesis_id."
        )

    if confidence_assessment.event_id != runtime_event_id:
        raise DecisionAlertError(
            "ConfidenceAssessment.event_id must match the RuntimeEvent.event_id."
        )

    if confidence_assessment.episode_id != hypothesis.episode_id:
        raise DecisionAlertError(
            "ConfidenceAssessment.episode_id must match HypothesisPackage.episode_id."
        )

    if confidence_assessment.source_hypothesis_id != hypothesis.hypothesis_id:
        raise DecisionAlertError(
            "ConfidenceAssessment.source_hypothesis_id must match "
            "HypothesisPackage.hypothesis_id."
        )


def _attention_policy(
    agent_state: AgentState,
    scenario_probability: ScenarioProbability | None,
    confidence_assessment: ConfidenceAssessment,
) -> tuple[DecisionType, AlertLevel, AlertCategory, str, str]:
    if scenario_probability is None:
        return (
            DecisionType.REVIEW_REQUIRED,
            AlertLevel.INFO,
            AlertCategory.WATCH,
            "normal",
            "Review reasoning chain because Scenario Probability is missing.",
        )

    if agent_state.current_state == AgentStateType.UNKNOWN:
        return (
            DecisionType.REVIEW_REQUIRED,
            AlertLevel.INFO,
            AlertCategory.WATCH,
            "normal",
            "Review reasoning chain because official Agent State is UNKNOWN.",
        )

    if confidence_assessment.final_confidence_level in (
        ConfidenceLevel.UNKNOWN,
        ConfidenceLevel.VERY_LOW,
        ConfidenceLevel.LOW,
    ):
        return (
            DecisionType.REVIEW_REQUIRED,
            AlertLevel.INFO,
            AlertCategory.WATCH,
            "normal",
            "Review reasoning chain because Confidence is LOW or unavailable.",
        )

    if (
        confidence_assessment.final_confidence_level == ConfidenceLevel.MEDIUM
        and agent_state.current_state == AgentStateType.CONTINUATION_ALIVE
    ):
        return (
            DecisionType.OBSERVE,
            AlertLevel.NONE,
            AlertCategory.NO_ACTION,
            "low",
            "Continue observation; no alert condition produced by Decision / Alert v0.1.",
        )

    if (
        confidence_assessment.final_confidence_level == ConfidenceLevel.MEDIUM
        and agent_state.current_state == AgentStateType.CONTINUATION_SATURATION
    ):
        return (
            DecisionType.WARNING,
            AlertLevel.WARNING,
            AlertCategory.WARNING,
            "elevated",
            "State indicates saturation; increase attention without action advice.",
        )

    if (
        confidence_assessment.final_confidence_level == ConfidenceLevel.MEDIUM
        and agent_state.current_state == AgentStateType.FIRST_FAILURE_CANDIDATE
    ):
        return (
            DecisionType.WARNING,
            AlertLevel.WARNING,
            AlertCategory.HIGH_ATTENTION,
            "high",
            "First failure candidate requires focused human review.",
        )

    return (
        DecisionType.REVIEW_REQUIRED,
        AlertLevel.INFO,
        AlertCategory.WATCH,
        "normal",
        "Review reasoning chain because this Agent State has no approved MVP rule.",
    )


def _required_human_action(decision_type: DecisionType) -> str:
    if decision_type == DecisionType.OBSERVE:
        return "Continue observation; no autonomous action is allowed."
    if decision_type == DecisionType.WARNING:
        return "Review the RuntimeEvent reasoning and monitor the active scenario."
    return "Review the RuntimeEvent reasoning before relying on this output."


def _monitoring_instructions(
    scenario_probability: ScenarioProbability | None,
) -> tuple[str, ...]:
    instructions = [
        "Use this output only for human review.",
        "No autonomous action is authorized.",
    ]

    if scenario_probability is None:
        instructions.append("Review reasoning chain before relying on this output.")
    else:
        instructions.append(
            "Monitor primary scenario: "
            f"{scenario_probability.primary_scenario.value}."
        )

    return tuple(instructions)
