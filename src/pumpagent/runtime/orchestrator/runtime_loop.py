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
    MarketEfficiencyEvidence,
    MarketSnapshot,
    StructuralEvidence,
)
from pumpagent.runtime.modules.evidence import Evidence, collect_evidence
from pumpagent.runtime.modules.hypothesis import MarketHypothesis, build_hypothesis
from pumpagent.runtime.modules.market_efficiency import build_market_efficiency_evidence
from pumpagent.runtime.modules.market_metrics import calculate_confidence
from pumpagent.runtime.modules.perception import build_observation_package
from pumpagent.runtime.modules.structure import build_structural_evidence


@dataclass(frozen=True)
class AgentCycleResult:
    snapshot: MarketSnapshot
    structure_result: StructuralEvidence
    market_result: MarketEfficiencyEvidence
    previous_state: str
    new_state: str
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
        new_state = hypothesis.market_state

        return AgentCycleResult(
            snapshot=snapshot,
            structure_result=structure_result,
            market_result=market_result,
            previous_state=previous_state,
            new_state=new_state,
            hypothesis=hypothesis,
            confidence=confidence,
            evidence=evidence,
            timestamp=snapshot.timestamp,
            log_messages=_cycle_log_messages(
                previous_state=previous_state,
                new_state=new_state,
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
