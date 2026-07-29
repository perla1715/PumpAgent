"""Fixture input wrapper for the canonical Runtime orchestrator."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
import warnings

from pumpagent.runtime.domain import RuntimeEvent
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator


class FixtureRuntimeStage(str, Enum):
    """Deprecated fixture selection retained for deterministic compatibility."""

    MARKET_DATA = "market_data"
    OBSERVATION_PACKAGE = "observation_package"
    PERCEPTION = "perception"
    STRUCTURE = "structure"
    MARKET_EFFICIENCY = "market_efficiency"
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
    episode_id: str | None = None,
    hypothesis_id: str | None = None,
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
    """Load fixture input and delegate all analysis to RuntimeOrchestrator."""

    if run_learning_memory:
        raise ValueError(
            "Learning Memory is outside the Runtime Orchestrator boundary."
        )
    if run_scenario_probability or run_confidence or run_decision_alert:
        raise ValueError(
            "Fixture Runtime stages after Agent State are retired."
        )
    stage = _resolve_target_stage(
        target_stage=target_stage,
        flags=(
            run_perception,
            run_structure,
            run_market_efficiency,
            run_hypothesis,
            run_agent_state,
        ),
    )
    input_event = RuntimeEvent(
        event_id=event_id,
        schema_version=schema_version,
        cycle_timestamp=cycle_timestamp,
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
    )
    input_event = add_market_snapshot_from_fixture(input_event, fixture_path)
    if stage is FixtureRuntimeStage.MARKET_DATA:
        return input_event
    if stage in {
        FixtureRuntimeStage.SCENARIO_PROBABILITY,
        FixtureRuntimeStage.CONFIDENCE,
        FixtureRuntimeStage.DECISION_ALERT,
    }:
        raise ValueError(
            "Fixture Runtime stages after Agent State are retired."
        )

    warnings.warn(
        "Fixture stage selection is deprecated; analytical fixture execution "
        "always delegates the complete canonical pipeline.",
        DeprecationWarning,
        stacklevel=2,
    )
    requires_hypothesis = stage in {
        FixtureRuntimeStage.HYPOTHESIS,
        FixtureRuntimeStage.AGENT_STATE,
    }
    if requires_hypothesis and (
        not isinstance(episode_id, str) or not episode_id.strip()
    ):
        raise ValueError("episode_id is required for the Hypothesis stage.")
    if requires_hypothesis and (
        not isinstance(hypothesis_id, str) or not hypothesis_id.strip()
    ):
        raise ValueError("hypothesis_id is required for the Hypothesis stage.")

    compatibility_episode_id = episode_id or f"fixture-episode:{event_id}"
    compatibility_hypothesis_id = (
        hypothesis_id or f"fixture-hypothesis:{event_id}"
    )
    runtime = RuntimeOrchestrator(
        hypothesis_id_generator=lambda: compatibility_hypothesis_id
    )
    completed = runtime.process_market_update(
        input_event.market_snapshot,
        episode_id=compatibility_episode_id,
        runtime_event_id=event_id,
    )
    if completed.runtime_status is not RuntimeStatus.COMPLETED:
        raise ValueError(
            "Canonical fixture delegation did not complete: "
            + ", ".join(completed.errors_or_warnings)
        )
    runtime.commit_runtime_continuity(completed.event_id)
    return _project_fixture_stage(
        completed,
        stage=stage,
        schema_version=schema_version,
        cycle_timestamp=cycle_timestamp,
    )


def run_fixture_market_data_cycle(**kwargs: object) -> RuntimeEvent:
    """Compatibility alias that delegates to the fixture input wrapper."""

    return run_fixture_runtime_cycle(**kwargs)


def _resolve_target_stage(
    *,
    target_stage: FixtureRuntimeStage | str | None,
    flags: tuple[bool, ...],
) -> FixtureRuntimeStage:
    if target_stage is not None:
        requested = (
            target_stage
            if isinstance(target_stage, FixtureRuntimeStage)
            else FixtureRuntimeStage(str(target_stage))
        )
        return (
            FixtureRuntimeStage.OBSERVATION_PACKAGE
            if requested is FixtureRuntimeStage.PERCEPTION
            else requested
        )
    stages = (
        FixtureRuntimeStage.OBSERVATION_PACKAGE,
        FixtureRuntimeStage.STRUCTURE,
        FixtureRuntimeStage.MARKET_EFFICIENCY,
        FixtureRuntimeStage.HYPOTHESIS,
        FixtureRuntimeStage.AGENT_STATE,
    )
    selected = FixtureRuntimeStage.MARKET_DATA
    for enabled, stage in zip(flags, stages):
        if enabled:
            selected = stage
    return selected


def _project_fixture_stage(
    completed: RuntimeEvent,
    *,
    stage: FixtureRuntimeStage,
    schema_version: str,
    cycle_timestamp: datetime,
) -> RuntimeEvent:
    """Project legacy partial sections from one canonical delegated execution."""

    order = (
        FixtureRuntimeStage.OBSERVATION_PACKAGE,
        FixtureRuntimeStage.STRUCTURE,
        FixtureRuntimeStage.MARKET_EFFICIENCY,
        FixtureRuntimeStage.HYPOTHESIS,
        FixtureRuntimeStage.AGENT_STATE,
    )
    reached = order.index(stage)
    return RuntimeEvent(
        event_id=completed.event_id,
        schema_version=schema_version,
        cycle_timestamp=cycle_timestamp,
        symbol=completed.symbol,
        exchange=completed.exchange,
        timeframe=completed.timeframe,
        episode_id=(
            completed.episode_id
            if reached >= order.index(FixtureRuntimeStage.HYPOTHESIS)
            else None
        ),
        runtime_status=RuntimeStatus.CREATED,
        market_snapshot=completed.market_snapshot,
        observation_package=completed.observation_package,
        structural_evidence=(
            completed.structural_evidence
            if reached >= order.index(FixtureRuntimeStage.STRUCTURE)
            else None
        ),
        market_efficiency_evidence=(
            completed.market_efficiency_evidence
            if reached >= order.index(FixtureRuntimeStage.MARKET_EFFICIENCY)
            else None
        ),
        hypothesis_package=(
            completed.hypothesis_package
            if reached >= order.index(FixtureRuntimeStage.HYPOTHESIS)
            else None
        ),
        agent_state=(
            completed.agent_state
            if reached >= order.index(FixtureRuntimeStage.AGENT_STATE)
            else None
        ),
    )
