"""Hypothesis Engine v0.1.

Hypothesis explains the current market condition from evidence sections.
It does not decide official market state or future scenario probabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pumpagent.runtime.domain import (
    HypothesisPackage,
    MarketEfficiencyEvidence,
    RuntimeEvent,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import (
    ConfidenceLevel,
    EvidenceStrength,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.evidence import Evidence, collect_evidence
from pumpagent.runtime.modules.hypothesis.formatter import format_hypothesis_summary
from pumpagent.runtime.modules.hypothesis.lifecycle import resolve_hypothesis_status
from pumpagent.runtime.modules.market_metrics import calculate_confidence
from pumpagent.runtime.modules.perception import detect_market_state


HYPOTHESIS_LABELS = {
    "IGNITION": "Ignition attempt",
    "CONTINUATION_ALIVE": "Continuation remains active",
    "WEAKENING": "Move is weakening",
    "UNKNOWN": "No clear hypothesis",
}


@dataclass(frozen=True)
class MarketHypothesis:
    id: str
    label: str
    summary: str
    market_state: str
    confidence_score: int
    evidence: tuple[Evidence, ...]
    supporting_evidence: tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    status: str
    lifecycle_reason: str | None = None
    previous_hypothesis_id: str | None = None


class HypothesisError(ValueError):
    """Raised when Hypothesis cannot produce a HypothesisPackage."""


def build_hypothesis(
    data: Any,
    previous: MarketHypothesis | None = None,
) -> MarketHypothesis:
    """Build a lightweight current-market interpretation."""

    market_state = detect_market_state(data)
    label = HYPOTHESIS_LABELS.get(market_state, HYPOTHESIS_LABELS["UNKNOWN"])
    confidence_score = calculate_confidence(data)
    evidence = tuple(collect_evidence(data))
    supporting_evidence = tuple(item.value for item in evidence if item.positive)
    contradicting_evidence = tuple(item.value for item in evidence if not item.positive)
    status = resolve_hypothesis_status(
        label=label,
        confidence_score=confidence_score,
        previous=previous,
    )

    return MarketHypothesis(
        id=_hypothesis_id(market_state, label),
        label=label,
        summary=format_hypothesis_summary(
            label=label,
            confidence_score=confidence_score,
            supporting_evidence=supporting_evidence,
            contradicting_evidence=contradicting_evidence,
        ),
        market_state=market_state,
        confidence_score=confidence_score,
        evidence=evidence,
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        status=status,
        lifecycle_reason=_lifecycle_reason(status),
        previous_hypothesis_id=(
            previous.id if previous is not None and status == "REPLACED" else None
        ),
    )


def build_hypothesis_package(
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
    *,
    runtime_event_id: str | None = None,
) -> HypothesisPackage:
    """Build an explanation package without state or scenario classification."""

    event_id = runtime_event_id or structural_evidence.event_id
    _validate_evidence_alignment(
        structural_evidence,
        market_efficiency_evidence,
        runtime_event_id=event_id,
    )

    supporting_evidence = _supporting_evidence(
        structural_evidence,
        market_efficiency_evidence,
    )
    contradicting_evidence = (
        structural_evidence.evidence_against
        + market_efficiency_evidence.evidence_against
    )
    uncertainty = _combined_uncertainty(
        structural_evidence,
        market_efficiency_evidence,
    )
    confidence_context = _confidence_context(
        structural_evidence,
        market_efficiency_evidence,
        uncertainty,
    )

    return HypothesisPackage(
        event_id=event_id,
        hypothesis_label="current_condition_explanation",
        hypothesis_summary=(
            "Current market condition is explained from structural and "
            "participation evidence only."
        ),
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        competing_hypotheses=(),
        current_hypothesis_confidence_context=confidence_context,
        reasoning_notes=(
            "Hypothesis Engine v0.1 combines evidence for current-condition "
            "explanation only; it does not decide official state, final "
            "confidence, alerts, trades, or future scenario probabilities."
        ),
        schema_version=structural_evidence.schema_version,
        uncertainty=uncertainty,
        assumptions=(
            "structural_evidence_is_precomputed",
            "market_efficiency_evidence_is_precomputed",
        ),
    )


def add_hypothesis_package(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only hypothesis_package added."""

    if event.structural_evidence is None:
        raise HypothesisError("RuntimeEvent.structural_evidence is required.")

    if event.market_efficiency_evidence is None:
        raise HypothesisError(
            "RuntimeEvent.market_efficiency_evidence is required."
        )

    hypothesis = build_hypothesis_package(
        event.structural_evidence,
        event.market_efficiency_evidence,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(hypothesis_package=hypothesis)


def _validate_evidence_alignment(
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
    *,
    runtime_event_id: str,
) -> None:
    if structural_evidence.event_id != runtime_event_id:
        raise HypothesisError(
            "StructuralEvidence.event_id must match the RuntimeEvent.event_id."
        )

    if market_efficiency_evidence.event_id != runtime_event_id:
        raise HypothesisError(
            "MarketEfficiencyEvidence.event_id must match the RuntimeEvent.event_id."
        )


def _hypothesis_id(market_state: str, label: str) -> str:
    label_slug = label.lower().replace(" ", "_")
    return f"{market_state.lower()}:{label_slug}"


def _lifecycle_reason(status: str) -> str:
    if status == "CREATED":
        return "No previous hypothesis was available."
    if status == "UPDATED":
        return "The same hypothesis label remains with stable or higher confidence."
    if status == "WEAKENED":
        return "The same hypothesis label remains with lower confidence."
    return "The current label differs from the previous hypothesis label."


def _supporting_evidence(
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
) -> tuple[str, ...]:
    structural_items = (
        f"structure:{item}" for item in structural_evidence.structural_events
    )
    efficiency_items = (
        f"market_efficiency:{item}"
        for item in market_efficiency_evidence.supporting_evidence
    )
    return tuple(structural_items) + tuple(efficiency_items)


def _combined_uncertainty(
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
) -> UncertaintyLevel:
    if (
        structural_evidence.uncertainty == UncertaintyLevel.HIGH
        or market_efficiency_evidence.uncertainty == UncertaintyLevel.HIGH
    ):
        return UncertaintyLevel.HIGH

    if (
        structural_evidence.uncertainty == UncertaintyLevel.MEDIUM
        or market_efficiency_evidence.uncertainty == UncertaintyLevel.MEDIUM
    ):
        return UncertaintyLevel.MEDIUM

    if (
        structural_evidence.uncertainty == UncertaintyLevel.LOW
        and market_efficiency_evidence.uncertainty == UncertaintyLevel.LOW
    ):
        return UncertaintyLevel.LOW

    return UncertaintyLevel.UNKNOWN


def _confidence_context(
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
    uncertainty: UncertaintyLevel,
) -> ConfidenceLevel:
    """Return only current_hypothesis_confidence_context.

    This is not final reliability scoring and does not replace the Confidence
    Engine.
    """

    if uncertainty == UncertaintyLevel.HIGH:
        return ConfidenceLevel.LOW

    if (
        structural_evidence.evidence_strength == EvidenceStrength.MODERATE
        and market_efficiency_evidence.evidence_strength == EvidenceStrength.MODERATE
    ):
        return ConfidenceLevel.MEDIUM

    if (
        structural_evidence.evidence_strength == EvidenceStrength.UNKNOWN
        or market_efficiency_evidence.evidence_strength == EvidenceStrength.UNKNOWN
    ):
        return ConfidenceLevel.VERY_LOW

    return ConfidenceLevel.LOW
