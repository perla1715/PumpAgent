"""Runtime cycle result serialization helpers."""

from __future__ import annotations

from typing import Any

from pumpagent.runtime.orchestrator.runtime_loop import AgentCycleResult


def serialize_agent_cycle_result(result: AgentCycleResult) -> dict[str, Any]:
    """Return a deterministic plain-dictionary representation of one cycle."""

    return {
        "event_id": result.event_id,
        "timestamp": result.timestamp.isoformat(),
        "symbol": result.snapshot.symbol,
        "exchange": result.snapshot.exchange,
        "timeframe": result.snapshot.timeframe,
        "previous_state": result.agent_state.previous_state.name,
        "new_state": result.agent_state.current_state.name,
        "hypothesis_id": result.hypothesis.id,
        "hypothesis_status": result.hypothesis.status,
        "confidence": result.confidence,
        "evidence": tuple(_serialize_evidence_item(item) for item in result.evidence),
        "agent_state_event_id": result.agent_state.event_id,
    }


def _serialize_evidence_item(item: object) -> dict[str, object]:
    return {
        "name": getattr(item, "name", ""),
        "value": getattr(item, "value", ""),
        "positive": bool(getattr(item, "positive", False)),
    }
