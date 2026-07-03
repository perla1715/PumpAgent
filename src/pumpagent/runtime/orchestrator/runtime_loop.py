"""Agent Runtime Loop MVP.

The runtime loop coordinates one deterministic reasoning cycle for a market
snapshot. It does not make trading decisions, persist data, or emit side
effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pumpagent.runtime.domain import (
    AgentState,
    MarketEfficiencyEvidence,
    MarketSnapshot,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import AgentStateType
from pumpagent.runtime.modules.evidence import Evidence, collect_evidence
from pumpagent.runtime.modules.agent_state import build_agent_state_from_market_hypothesis
from pumpagent.runtime.modules.hypothesis import MarketHypothesis, build_hypothesis
from pumpagent.runtime.modules.market_efficiency import build_market_efficiency_evidence
from pumpagent.runtime.modules.market_metrics import calculate_confidence
from pumpagent.runtime.modules.perception import build_observation_package
from pumpagent.runtime.modules.structure import build_structural_evidence


@dataclass(frozen=True)
class AgentCycleResult:
    event_id: str
    snapshot: MarketSnapshot
    structure_result: StructuralEvidence
    market_result: MarketEfficiencyEvidence
    previous_state: str
    new_state: str
    agent_state: AgentState
    hypothesis: MarketHypothesis
    confidence: int
    evidence: tuple[Evidence, ...]
    timestamp: datetime
    log_messages: tuple[str, ...] = ()


class RuntimeOrchestrator:
    """Coordinate one side-effect-free agent reasoning cycle."""

    def process_market_update(
        self,
        snapshot: MarketSnapshot,
        *,
        previous_state: str = "UNKNOWN",
        previous_hypothesis: MarketHypothesis | None = None,
    ) -> AgentCycleResult:
        event_id = _cycle_event_id(snapshot)
        observations = build_observation_package(snapshot)
        structure_result = build_structural_evidence(observations)
        market_result = build_market_efficiency_evidence(observations)
        hypothesis_input = _combine_hypothesis_input(
            snapshot,
            structure_result=structure_result,
            market_result=market_result,
        )
        hypothesis = build_hypothesis(hypothesis_input, previous=previous_hypothesis)
        confidence = calculate_confidence(hypothesis_input)
        evidence = tuple(collect_evidence(hypothesis_input))
        agent_state = build_agent_state_from_market_hypothesis(
            hypothesis,
            event_id=event_id,
            previous_state=_agent_state_type_from_value(previous_state),
        )
        previous_state_name = agent_state.previous_state.name
        new_state_name = agent_state.current_state.name

        return AgentCycleResult(
            event_id=event_id,
            snapshot=snapshot,
            structure_result=structure_result,
            market_result=market_result,
            previous_state=previous_state_name,
            new_state=new_state_name,
            agent_state=agent_state,
            hypothesis=hypothesis,
            confidence=confidence,
            evidence=evidence,
            timestamp=snapshot.timestamp,
            log_messages=_cycle_log_messages(
                previous_state=previous_state_name,
                new_state=new_state_name,
                hypothesis=hypothesis,
            ),
        )


def run_agent_cycle(
    snapshot: MarketSnapshot,
    *,
    previous_state: str = "UNKNOWN",
    previous_hypothesis: MarketHypothesis | None = None,
) -> AgentCycleResult:
    """Convenience entry point for one runtime reasoning cycle."""

    return RuntimeOrchestrator().process_market_update(
        snapshot,
        previous_state=previous_state,
        previous_hypothesis=previous_hypothesis,
    )


def _cycle_log_messages(
    *,
    previous_state: str,
    new_state: str,
    hypothesis: MarketHypothesis,
) -> tuple[str, ...]:
    return (
        f"state:{previous_state}->{new_state}",
        f"hypothesis:{hypothesis.status}:{hypothesis.label}",
        f"confidence:{hypothesis.confidence_score}",
    )


def _combine_hypothesis_input(
    snapshot: MarketSnapshot,
    *,
    structure_result: StructuralEvidence,
    market_result: MarketEfficiencyEvidence,
) -> dict[str, Any]:
    data = {
        "symbol": snapshot.symbol,
        "price": snapshot.price,
        "volume": snapshot.volume,
        "structure_summary": structure_result.structure_summary,
        "structural_events": structure_result.structural_events,
        "market_summary": market_result.participation_summary,
        "market_supporting_evidence": market_result.supporting_evidence,
        "market_evidence_against": market_result.evidence_against,
    }
    data.update(snapshot.optional_market_metrics)
    return data


def _cycle_event_id(snapshot: MarketSnapshot) -> str:
    timestamp = snapshot.timestamp.isoformat()
    return f"agent-cycle:{snapshot.exchange}:{snapshot.symbol}:{snapshot.timeframe}:{snapshot.event_id}:{timestamp}"


def _agent_state_type_from_value(value: str | AgentStateType) -> AgentStateType:
    if isinstance(value, AgentStateType):
        return value

    normalized = str(value).lower()
    for state in AgentStateType:
        if normalized in (state.name.lower(), state.value):
            return state

    return AgentStateType.UNKNOWN
