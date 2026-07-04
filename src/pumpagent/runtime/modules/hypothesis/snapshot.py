"""Hypothesis Snapshot v1.

Snapshots record current interpretation context only. They do not create
hypotheses, modify state or confidence, or make decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.runtime.modules.evidence import EvidenceSummary


TREND_IMPROVING = "IMPROVING"
TREND_STABLE = "STABLE"
TREND_WEAKENING = "WEAKENING"
TREND_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class HypothesisSnapshot:
    state: str
    confidence: int
    confidence_trend: str
    evidence_summary: EvidenceSummary | None
    created_at: datetime
    label: str


class HypothesisSnapshotBuilder:
    """Build deterministic context snapshots from prepared Runtime outputs."""

    @classmethod
    def build(
        cls,
        *,
        agent_state: object,
        confidence: int,
        confidence_trend: str,
        evidence_summary: EvidenceSummary | None,
        created_at: datetime,
    ) -> HypothesisSnapshot:
        return HypothesisSnapshot(
            state=_state_name(agent_state),
            confidence=confidence,
            confidence_trend=confidence_trend,
            evidence_summary=evidence_summary,
            created_at=created_at,
            label=cls._label_from_summary(evidence_summary),
        )

    @classmethod
    def _label_from_summary(cls, summary: EvidenceSummary | None) -> str:
        if summary is None or summary.evidence_count == 0:
            return "unknown"

        if summary.total_score <= 0.0:
            return "low_evidence"

        present_types = tuple(
            evidence_type
            for evidence_type, present in (
                ("structural", summary.has_structural_evidence),
                ("market", summary.has_market_evidence),
                ("temporal", summary.has_temporal_evidence),
            )
            if present
        )

        if len(present_types) != 1:
            return "mixed_evidence"

        return f"{present_types[0]}_only"


class HypothesisHistory:
    """Bounded in-memory history for diagnostic HypothesisSnapshot objects."""

    def __init__(self, max_length: int = 10) -> None:
        if max_length < 1:
            raise ValueError("HypothesisHistory max_length must be at least 1.")
        self.max_length = max_length
        self._snapshots: list[HypothesisSnapshot] = []

    def append(self, snapshot: HypothesisSnapshot) -> None:
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self.max_length:
            overflow = len(self._snapshots) - self.max_length
            del self._snapshots[:overflow]

    def latest(self) -> HypothesisSnapshot | None:
        if not self._snapshots:
            return None
        return self._snapshots[-1]

    def previous(self) -> HypothesisSnapshot | None:
        if len(self._snapshots) < 2:
            return None
        return self._snapshots[-2]

    def size(self) -> int:
        return len(self._snapshots)

    def clear(self) -> None:
        self._snapshots.clear()


@dataclass(frozen=True)
class HistoryTrendSummary:
    confidence_trend: str
    evidence_score_trend: str
    label_stability: str
    sample_size: int


class HistoryTrendAnalyzer:
    """Summarize bounded HypothesisHistory trends for diagnostics only."""

    EVIDENCE_SCORE_EPSILON = 0.01

    @classmethod
    def analyze(cls, history: HypothesisHistory) -> HistoryTrendSummary:
        sample_size = history.size()
        if sample_size < 2:
            return HistoryTrendSummary(
                confidence_trend=TREND_UNKNOWN,
                evidence_score_trend=TREND_UNKNOWN,
                label_stability=TREND_UNKNOWN,
                sample_size=sample_size,
            )

        first = history._snapshots[0]
        latest = history._snapshots[-1]
        return HistoryTrendSummary(
            confidence_trend=_numeric_trend(
                float(first.confidence),
                float(latest.confidence),
                stable_delta=0.0,
            ),
            evidence_score_trend=_evidence_score_trend(
                first,
                latest,
                stable_delta=cls.EVIDENCE_SCORE_EPSILON,
            ),
            label_stability=_label_stability(history._snapshots),
            sample_size=sample_size,
        )


def build_hypothesis_snapshot(
    *,
    agent_state: object,
    confidence: int,
    confidence_trend: str,
    evidence_summary: EvidenceSummary | None,
    created_at: datetime,
) -> HypothesisSnapshot:
    """Build a snapshot without changing Runtime behavior."""

    return HypothesisSnapshotBuilder.build(
        agent_state=agent_state,
        confidence=confidence,
        confidence_trend=confidence_trend,
        evidence_summary=evidence_summary,
        created_at=created_at,
    )


def _state_name(agent_state: object) -> str:
    current_state = getattr(agent_state, "current_state", agent_state)
    name = getattr(current_state, "name", None)
    if name is not None:
        return str(name)
    return str(current_state)


def _evidence_score(snapshot: HypothesisSnapshot) -> float | None:
    if snapshot.evidence_summary is None:
        return None
    return snapshot.evidence_summary.total_score


def _evidence_score_trend(
    first: HypothesisSnapshot,
    latest: HypothesisSnapshot,
    *,
    stable_delta: float,
) -> str:
    first_score = _evidence_score(first)
    latest_score = _evidence_score(latest)
    if first_score is None or latest_score is None:
        return TREND_UNKNOWN
    return _numeric_trend(first_score, latest_score, stable_delta=stable_delta)


def _numeric_trend(
    first_value: float,
    latest_value: float,
    *,
    stable_delta: float,
) -> str:
    delta = latest_value - first_value
    if abs(delta) <= stable_delta:
        return TREND_STABLE
    if delta > 0:
        return TREND_IMPROVING
    return TREND_WEAKENING


def _label_stability(snapshots: list[HypothesisSnapshot]) -> str:
    labels = {snapshot.label for snapshot in snapshots}
    if len(labels) == 1:
        return TREND_STABLE
    return TREND_WEAKENING
