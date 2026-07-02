"""Hypothesis Engine v0.1.

Hypothesis explains the current market condition from evidence sections.
It does not decide official market state or future scenario probabilities.
"""

from __future__ import annotations

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


class HypothesisError(ValueError):
    """Raised when Hypothesis cannot produce a HypothesisPackage."""


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
