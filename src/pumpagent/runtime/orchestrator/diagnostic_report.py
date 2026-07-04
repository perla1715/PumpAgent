"""Diagnostic Runtime Report v1.

The report packages already-produced runtime diagnostics into one immutable
object. It does not modify runtime behavior or feed back into decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.runtime.modules.evidence import EvidenceSummary
from pumpagent.runtime.modules.hypothesis import (
    HistoryTrendSummary,
    HypothesisSnapshot,
)
from pumpagent.runtime.modules.temporal_confidence import TemporalConfidenceState


@dataclass(frozen=True)
class DiagnosticRuntimeReport:
    state: str
    confidence: int
    confidence_trend: str
    temporal_confidence: TemporalConfidenceState | None
    evidence_summary: EvidenceSummary | None
    hypothesis_snapshot: HypothesisSnapshot | None
    hypothesis_history_size: int
    history_trend_summary: HistoryTrendSummary | None
    created_at: datetime


class DiagnosticRuntimeReportBuilder:
    """Build a deterministic diagnostic report from completed cycle outputs."""

    @classmethod
    def build(
        cls,
        *,
        state: object,
        confidence: int,
        confidence_trend: str,
        temporal_confidence: TemporalConfidenceState | None,
        evidence_summary: EvidenceSummary | None,
        hypothesis_snapshot: HypothesisSnapshot | None,
        hypothesis_history_size: int,
        history_trend_summary: HistoryTrendSummary | None,
        created_at: datetime,
    ) -> DiagnosticRuntimeReport:
        return DiagnosticRuntimeReport(
            state=_state_name(state),
            confidence=confidence,
            confidence_trend=confidence_trend,
            temporal_confidence=temporal_confidence,
            evidence_summary=evidence_summary,
            hypothesis_snapshot=hypothesis_snapshot,
            hypothesis_history_size=hypothesis_history_size,
            history_trend_summary=history_trend_summary,
            created_at=created_at,
        )


def build_diagnostic_runtime_report(
    *,
    state: object,
    confidence: int,
    confidence_trend: str,
    temporal_confidence: TemporalConfidenceState | None,
    evidence_summary: EvidenceSummary | None,
    hypothesis_snapshot: HypothesisSnapshot | None,
    hypothesis_history_size: int,
    history_trend_summary: HistoryTrendSummary | None,
    created_at: datetime,
) -> DiagnosticRuntimeReport:
    """Build one output-only diagnostic report."""

    return DiagnosticRuntimeReportBuilder.build(
        state=state,
        confidence=confidence,
        confidence_trend=confidence_trend,
        temporal_confidence=temporal_confidence,
        evidence_summary=evidence_summary,
        hypothesis_snapshot=hypothesis_snapshot,
        hypothesis_history_size=hypothesis_history_size,
        history_trend_summary=history_trend_summary,
        created_at=created_at,
    )


def _state_name(state: object) -> str:
    current_state = getattr(state, "current_state", state)
    name = getattr(current_state, "name", None)
    if name is not None:
        return str(name)
    return str(current_state)
