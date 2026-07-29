"""Compatibility projection from canonical RuntimeEvent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.runtime.domain import (
    AgentState,
    ConfidenceAssessment,
    DecisionAssessment,
    HealthyBaselineDesignation,
    HealthyBaselineReference,
    HypothesisPackage,
    MarketEfficiencyEvidence,
    MarketSnapshot,
    ProcessQualityAssessment,
    ProcessQualityAssessmentReference,
    RuntimeEvent,
    ScenarioProbability,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.domain.process_evidence import (
    ProcessEvidence,
    ProcessState,
    ProcessTransition,
)
from pumpagent.runtime.modules.evidence import Evidence, EvidenceSummary
from pumpagent.runtime.modules.hypothesis import (
    HistoryTrendSummary,
    HypothesisEvaluation,
    HypothesisSnapshot,
)
from pumpagent.runtime.modules.temporal_confidence import TemporalConfidenceState
from pumpagent.runtime.orchestrator.diagnostic_report import DiagnosticRuntimeReport


@dataclass(frozen=True)
class AgentCycleResult:
    """Deprecated compatibility and diagnostic view of one completed event."""

    event_id: str
    snapshot: MarketSnapshot
    structure_result: StructuralEvidence
    market_result: MarketEfficiencyEvidence
    previous_state: str
    new_state: str
    agent_state: AgentState
    hypothesis: HypothesisPackage
    scenario_probability: ScenarioProbability
    confidence_assessment: ConfidenceAssessment
    confidence: int
    evidence: tuple[Evidence, ...]
    timestamp: datetime
    watchlist_action: str
    watchlist_observation_count: int
    temporal_confidence: TemporalConfidenceState | None
    confidence_trend: str
    confidence_delta: int | None
    log_messages: tuple[str, ...] = ()
    evidence_summary: EvidenceSummary | None = None
    hypothesis_snapshot: HypothesisSnapshot | None = None
    hypothesis_history_size: int = 0
    history_trend_summary: HistoryTrendSummary | None = None
    diagnostic_report: DiagnosticRuntimeReport | None = None
    hypothesis_evaluation: HypothesisEvaluation | None = None
    process_evidence: ProcessEvidence | None = None
    process_state: ProcessState | None = None
    process_transition: ProcessTransition | None = None
    previous_process_evidence_used: bool = False
    process_quality_assessment: ProcessQualityAssessment | None = None
    previous_process_quality_reference: ProcessQualityAssessmentReference | None = None
    process_quality_history: tuple[ProcessQualityAssessment, ...] = ()
    healthy_baseline_reference: HealthyBaselineReference | None = None
    healthy_baseline_designation: HealthyBaselineDesignation | None = None
    decision_assessment: DecisionAssessment | None = None


def project_agent_cycle_result(event: RuntimeEvent) -> AgentCycleResult:
    """Project diagnostics without executing or reconstructing analysis."""

    if not isinstance(event, RuntimeEvent):
        raise TypeError("event must be a RuntimeEvent.")
    if event.runtime_status is not RuntimeStatus.COMPLETED:
        raise ValueError("Only a completed RuntimeEvent can be projected.")
    context = event.compatibility_context
    return AgentCycleResult(
        event_id=event.event_id,
        snapshot=event.market_snapshot,
        structure_result=event.structural_evidence,
        market_result=event.market_efficiency_evidence,
        previous_state=event.agent_state.previous_state.name,
        new_state=event.agent_state.current_state.name,
        agent_state=event.agent_state,
        hypothesis=event.hypothesis_package,
        scenario_probability=event.scenario_probability,
        confidence_assessment=event.confidence_assessment,
        confidence=int(
            context.get(
                "confidence",
                event.hypothesis_package.explanation_confidence_score,
            )
        ),
        evidence=tuple(context.get("evidence", ())),
        timestamp=event.market_snapshot.timestamp,
        watchlist_action=str(context.get("watchlist_action", "none")),
        watchlist_observation_count=int(
            context.get("watchlist_observation_count", 0)
        ),
        temporal_confidence=context.get("temporal_confidence"),
        confidence_trend=str(context.get("confidence_trend", "UNKNOWN")),
        confidence_delta=context.get("confidence_delta"),
        log_messages=tuple(context.get("log_messages", ())),
        evidence_summary=context.get("evidence_summary"),
        hypothesis_snapshot=context.get("hypothesis_snapshot"),
        hypothesis_history_size=int(context.get("hypothesis_history_size", 0)),
        history_trend_summary=context.get("history_trend_summary"),
        diagnostic_report=context.get("diagnostic_report"),
        hypothesis_evaluation=context.get("hypothesis_evaluation"),
        process_evidence=event.process_evidence,
        process_state=event.process_evidence.current_process_state,
        process_transition=event.process_evidence.detected_transition,
        previous_process_evidence_used=bool(
            context.get("previous_process_evidence_used", False)
        ),
        process_quality_assessment=event.process_quality_assessment,
        previous_process_quality_reference=event.previous_process_quality_reference,
        process_quality_history=event.process_quality_history,
        healthy_baseline_reference=event.healthy_baseline_reference,
        healthy_baseline_designation=event.healthy_baseline_designation,
        decision_assessment=event.decision_assessment,
    )
