"""Hypothesis Snapshot v1.

Snapshots record current interpretation context only. They do not create
hypotheses, modify state or confidence, or make decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pumpagent.runtime.modules.evidence import EvidenceSummary


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
