"""Minimal fixture-based Runtime Orchestrator skeleton.

This skeleton creates a RuntimeEvent, populates its market_snapshot section,
and can optionally run through Decision / Alert.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.modules.agent_state import add_agent_state
from pumpagent.runtime.modules.confidence import add_confidence_assessment
from pumpagent.runtime.modules.decision_alert import add_decision_alert
from pumpagent.runtime.modules.hypothesis import add_hypothesis_package
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.perception import add_perception_evidence
from pumpagent.runtime.modules.scenario_probability import add_scenario_probability


class FixtureRuntimeStage(str, Enum):
    MARKET_DATA = "market_data"
    PERCEPTION = "perception"
    HYPOTHESIS = "hypothesis"
    AGENT_STATE = "agent_state"
    SCENARIO_PROBABILITY = "scenario_probability"
    CONFIDENCE = "confidence"
    DECISION_ALERT = "decision_alert"


def run_fixture_runtime_cycle(
    *,
    event_id: str,
    cycle_timestamp: datetime,
    symbol: str,
    exchange: str,
    timeframe: str,
    fixture_path: str | Path,
    schema_version: str = "1.0",
    run_perception: bool = False,
    run_structure: bool = False,
    run_market_efficiency: bool = False,
    run_hypothesis: bool = False,
    run_agent_state: bool = False,
    run_scenario_probability: bool = False,
    run_confidence: bool = False,
    run_decision_alert: bool = False,
    run_learning_memory: bool = False,
    target_stage: FixtureRuntimeStage | str | None = None,
) -> RuntimeEvent:
    """Create a RuntimeEvent and optionally run the fixture Runtime flow."""

    stage = _resolve_target_stage(
        target_stage=target_stage,
        run_perception=run_perception,
        run_structure=run_structure,
        run_market_efficiency=run_market_efficiency,
        run_hypothesis=run_hypothesis,
        run_agent_state=run_agent_state,
        run_scenario_probability=run_scenario_probability,
        run_confidence=run_confidence,
        run_decision_alert=run_decision_alert,
        run_learning_memory=run_learning_memory,
    )

    event = RuntimeEvent(
        event_id=event_id,
        schema_version=schema_version,
        cycle_timestamp=cycle_timestamp,
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
    )

    event = add_market_snapshot_from_fixture(event, fixture_path)

    if _stage_reaches(stage, FixtureRuntimeStage.PERCEPTION):
        event = add_perception_evidence(event)

    if _stage_reaches(stage, FixtureRuntimeStage.HYPOTHESIS):
        event = add_hypothesis_package(event)

    if _stage_reaches(stage, FixtureRuntimeStage.AGENT_STATE):
        event = add_agent_state(event)

    if _stage_reaches(stage, FixtureRuntimeStage.SCENARIO_PROBABILITY):
        event = add_scenario_probability(event)

    if _stage_reaches(stage, FixtureRuntimeStage.CONFIDENCE):
        event = add_confidence_assessment(event)

    if _stage_reaches(stage, FixtureRuntimeStage.DECISION_ALERT):
        event = add_decision_alert(event)

    return event


def run_fixture_market_data_cycle(**kwargs: object) -> RuntimeEvent:
    """Compatibility alias for the renamed fixture Runtime entry point."""

    return run_fixture_runtime_cycle(**kwargs)


def _resolve_target_stage(
    *,
    target_stage: FixtureRuntimeStage | str | None,
    run_perception: bool,
    run_structure: bool,
    run_market_efficiency: bool,
    run_hypothesis: bool,
    run_agent_state: bool,
    run_scenario_probability: bool,
    run_confidence: bool,
    run_decision_alert: bool,
    run_learning_memory: bool,
) -> FixtureRuntimeStage:
    if run_learning_memory:
        raise ValueError(
            "Learning Memory is outside the Runtime Orchestrator v0.1 boundary."
        )

    if target_stage is not None:
        if isinstance(target_stage, FixtureRuntimeStage):
            return target_stage
        return FixtureRuntimeStage(str(target_stage))

    if run_decision_alert:
        return FixtureRuntimeStage.DECISION_ALERT
    if run_confidence:
        return FixtureRuntimeStage.CONFIDENCE
    if run_scenario_probability:
        return FixtureRuntimeStage.SCENARIO_PROBABILITY
    if run_agent_state:
        return FixtureRuntimeStage.AGENT_STATE
    if run_hypothesis:
        return FixtureRuntimeStage.HYPOTHESIS
    if run_market_efficiency or run_structure:
        return FixtureRuntimeStage.PERCEPTION
    if run_perception:
        return FixtureRuntimeStage.PERCEPTION
    return FixtureRuntimeStage.MARKET_DATA


def _stage_reaches(
    current_stage: FixtureRuntimeStage,
    required_stage: FixtureRuntimeStage,
) -> bool:
    stage_order = (
        FixtureRuntimeStage.MARKET_DATA,
        FixtureRuntimeStage.PERCEPTION,
        FixtureRuntimeStage.HYPOTHESIS,
        FixtureRuntimeStage.AGENT_STATE,
        FixtureRuntimeStage.SCENARIO_PROBABILITY,
        FixtureRuntimeStage.CONFIDENCE,
        FixtureRuntimeStage.DECISION_ALERT,
    )
    return stage_order.index(current_stage) >= stage_order.index(required_stage)
