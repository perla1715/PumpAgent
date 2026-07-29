"""Runtime cycle result serialization helpers.

This module defines a review/logging schema only. It does not persist data.
Future shape changes should introduce a new schema version instead of silently
changing the fields emitted by an existing version.
"""

from __future__ import annotations

from typing import Any

from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.orchestrator.cycle_projection import AgentCycleResult


RUNTIME_CYCLE_SCHEMA_VERSION = "runtime_cycle_v4"
CANONICAL_RUNTIME_EVENT_SCHEMA_VERSION = "canonical_runtime_event_v1"


def serialize_runtime_event(event: RuntimeEvent) -> dict[str, Any]:
    """Serialize the authoritative Runtime aggregate."""

    if not isinstance(event, RuntimeEvent):
        raise TypeError("event must be a RuntimeEvent.")
    return {
        "persistence_schema_version": CANONICAL_RUNTIME_EVENT_SCHEMA_VERSION,
        "runtime_event": event.to_dict(),
    }


def serialize_agent_cycle_result(result: AgentCycleResult) -> dict[str, Any]:
    """Serialize the deprecated compatibility projection."""

    return {
        "schema_version": RUNTIME_CYCLE_SCHEMA_VERSION,
        "event_id": result.event_id,
        "timestamp": result.timestamp.isoformat(),
        "symbol": result.snapshot.symbol,
        "exchange": result.snapshot.exchange,
        "timeframe": result.snapshot.timeframe,
        "previous_state": result.agent_state.previous_state.name,
        "new_state": result.agent_state.current_state.name,
        "process_direction": result.agent_state.process_direction.value,
        "hypothesis_id": result.hypothesis.hypothesis_id,
        "hypothesis_status": result.hypothesis.lifecycle_status.name,
        "hypothesis_label": result.hypothesis.hypothesis_label,
        "hypothesis_episode_id": result.hypothesis.episode_id,
        "hypothesis_event_id": result.hypothesis.event_id,
        "explanation_confidence_score": (
            result.hypothesis.explanation_confidence_score
        ),
        "confidence": result.confidence,
        "confidence_semantics": "explanation_confidence_compatibility_score",
        "evidence": tuple(_serialize_evidence_item(item) for item in result.evidence),
        "agent_state_event_id": result.agent_state.event_id,
        "scenario_probability": {
            "event_id": result.scenario_probability.runtime_event_id,
            "episode_id": result.scenario_probability.episode_id,
            "source_hypothesis_id": (
                result.scenario_probability.source_hypothesis_id
            ),
            "primary_scenario": result.scenario_probability.primary_scenario.value,
            "alternative_scenarios": (
                tuple(
                    item.scenario.value
                    for item in result.scenario_probability.distribution
                    if item.scenario
                    is not result.scenario_probability.primary_scenario
                )
            ),
            "deterministic_policy_weights": dict(
                (
                    item.scenario.value,
                    str(item.probability),
                )
                for item in result.scenario_probability.distribution
            ),
            "uncertainty": result.scenario_probability.uncertainty.value,
            "reason_codes": tuple(
                item.value for item in result.scenario_probability.reason_codes
            ),
            "probability_model": "deterministic_policy_weights_not_calibrated",
        },
        "confidence_assessment": {
            "event_id": result.confidence_assessment.event_id,
            "episode_id": result.confidence_assessment.episode_id,
            "source_hypothesis_id": (
                result.confidence_assessment.source_hypothesis_id
            ),
            "final_confidence_level": (
                result.confidence_assessment.final_confidence_level.value
            ),
            "confidence_summary": result.confidence_assessment.confidence_summary,
            "confidence_drivers": result.confidence_assessment.confidence_drivers,
            "confidence_reducers": result.confidence_assessment.confidence_reducers,
            "data_quality_impact": result.confidence_assessment.data_quality_impact,
            "contradiction_impact": (
                result.confidence_assessment.contradiction_impact
            ),
            "uncertainty_level": (
                result.confidence_assessment.uncertainty_level.value
            ),
            "reliability_notes": result.confidence_assessment.reliability_notes,
            "calibration_notes": result.confidence_assessment.calibration_notes,
            "numeric_confidence_score": (
                result.confidence_assessment.numeric_confidence_score
            ),
        },
    }


def _serialize_evidence_item(item: object) -> dict[str, object]:
    return {
        "name": getattr(item, "name", ""),
        "value": getattr(item, "value", ""),
        "positive": bool(getattr(item, "positive", False)),
    }
