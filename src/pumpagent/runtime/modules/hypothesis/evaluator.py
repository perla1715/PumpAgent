"""Hypothesis Evaluator v1.

The evaluator classifies current diagnostic support for the existing
hypothesis context. It does not modify confidence, state, or hypothesis logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.runtime.modules.hypothesis.snapshot import (
    HistoryTrendSummary,
    HypothesisSnapshot,
    TREND_IMPROVING,
    TREND_STABLE,
    TREND_UNKNOWN,
    TREND_WEAKENING,
)


EVALUATION_REINFORCED = "REINFORCED"
EVALUATION_NEUTRAL = "NEUTRAL"
EVALUATION_WEAKENING = "WEAKENING"
EVALUATION_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class HypothesisEvaluation:
    status: str
    reason: str
    created_at: datetime | None


class HypothesisEvaluator:
    """Evaluate diagnostic hypothesis support from snapshot and history trend."""

    @classmethod
    def evaluate(
        cls,
        *,
        snapshot: HypothesisSnapshot | None,
        history_trend_summary: HistoryTrendSummary | None,
    ) -> HypothesisEvaluation:
        created_at = snapshot.created_at if snapshot is not None else None
        if snapshot is None or history_trend_summary is None:
            return HypothesisEvaluation(
                status=EVALUATION_UNKNOWN,
                reason="missing_snapshot_or_history_trend",
                created_at=created_at,
            )

        confidence_trend = history_trend_summary.confidence_trend
        evidence_trend = history_trend_summary.evidence_score_trend

        if confidence_trend == TREND_WEAKENING or evidence_trend == TREND_WEAKENING:
            return HypothesisEvaluation(
                status=EVALUATION_WEAKENING,
                reason="confidence_or_evidence_trend_weakening",
                created_at=created_at,
            )

        if confidence_trend == TREND_IMPROVING and evidence_trend == TREND_IMPROVING:
            return HypothesisEvaluation(
                status=EVALUATION_REINFORCED,
                reason="confidence_and_evidence_trends_improving",
                created_at=created_at,
            )

        if confidence_trend == TREND_STABLE and evidence_trend == TREND_STABLE:
            return HypothesisEvaluation(
                status=EVALUATION_NEUTRAL,
                reason="confidence_and_evidence_trends_stable",
                created_at=created_at,
            )

        if confidence_trend == TREND_UNKNOWN or evidence_trend == TREND_UNKNOWN:
            return HypothesisEvaluation(
                status=EVALUATION_UNKNOWN,
                reason="confidence_or_evidence_trend_unknown",
                created_at=created_at,
            )

        return HypothesisEvaluation(
            status=EVALUATION_UNKNOWN,
            reason="mixed_trend_context",
            created_at=created_at,
        )
