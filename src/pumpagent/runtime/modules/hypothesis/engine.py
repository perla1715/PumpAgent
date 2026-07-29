"""Hypothesis Engine v0.1.

Hypothesis explains the current market condition from evidence sections.
It does not decide official market state or future scenario probabilities.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pumpagent.runtime.domain import (
    HypothesisEvidenceReference,
    HypothesisLifecycleStatus,
    HypothesisPackage,
    HypothesisSemanticCode,
    MarketEfficiencyEvidence,
    RuntimeEvent,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import (
    ConfidenceLevel,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.evidence import collect_evidence
from pumpagent.runtime.modules.hypothesis.formatter import format_hypothesis_summary
from pumpagent.runtime.modules.market_metrics import calculate_confidence
from pumpagent.runtime.domain.process_evidence import (
    ProcessEvidence,
    ProcessState,
    ProcessTransition,
)


HYPOTHESIS_LABELS = {
    "IGNITION": "Ignition attempt",
    "CONTINUATION_ALIVE": "Continuation remains active",
    "WEAKENING": "Move is weakening",
    "UNKNOWN": "No clear hypothesis",
}


class HypothesisError(ValueError):
    """Raised when Hypothesis cannot produce a HypothesisPackage."""


def build_hypothesis_package(
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
    *,
    episode_id: str,
    hypothesis_id: str,
    explanation_confidence_score: int,
    lifecycle_status: HypothesisLifecycleStatus,
    hypothesis_change_reason: str,
    previous_hypothesis_id: str | None = None,
    previous_runtime_event_id: str | None = None,
    runtime_event_id: str | None = None,
    hypothesis_label: str = "current_condition_explanation",
    hypothesis_summary: str | None = None,
    semantic_code: HypothesisSemanticCode = HypothesisSemanticCode.UNRESOLVED,
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
    contradicting_evidence = _contradicting_evidence(
        structural_evidence,
        market_efficiency_evidence,
    )
    uncertainty = _combined_uncertainty(
        structural_evidence,
        market_efficiency_evidence,
    )
    confidence_context = _confidence_level_from_score(explanation_confidence_score)

    return HypothesisPackage(
        event_id=event_id,
        episode_id=episode_id,
        hypothesis_id=hypothesis_id,
        hypothesis_label=hypothesis_label,
        hypothesis_summary=hypothesis_summary or (
            "Current market condition is explained from structural and "
            "participation evidence only."
        ),
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        explanation_confidence_score=explanation_confidence_score,
        current_hypothesis_confidence_context=confidence_context,
        reasoning_notes=(
            "Hypothesis Engine v0.1 combines evidence for current-condition "
            "explanation only; it does not decide official state, final "
            "confidence, alerts, trades, or future scenario probabilities."
        ),
        schema_version=structural_evidence.schema_version,
        uncertainty=uncertainty,
        semantic_code=semantic_code,
        lifecycle_status=lifecycle_status,
        previous_hypothesis_id=previous_hypothesis_id,
        previous_runtime_event_id=previous_runtime_event_id,
        hypothesis_change_reason=hypothesis_change_reason,
    )


def add_hypothesis_package(
    event: RuntimeEvent,
    *,
    episode_id: str,
    hypothesis_id: str,
    explanation_confidence_score: int,
    lifecycle_status: HypothesisLifecycleStatus,
    hypothesis_change_reason: str,
    previous_hypothesis_id: str | None = None,
    previous_runtime_event_id: str | None = None,
) -> RuntimeEvent:
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
        episode_id=episode_id,
        hypothesis_id=hypothesis_id,
        explanation_confidence_score=explanation_confidence_score,
        lifecycle_status=lifecycle_status,
        hypothesis_change_reason=hypothesis_change_reason,
        previous_hypothesis_id=previous_hypothesis_id,
        previous_runtime_event_id=previous_runtime_event_id,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(hypothesis_package=hypothesis)


def generate_hypothesis_id() -> str:
    """Return one opaque production hypothesis identity."""

    return str(uuid4())


def build_operational_hypothesis_package(
    data: Any,
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
    *,
    episode_id: str,
    runtime_event_id: str,
    process_evidence: ProcessEvidence,
    previous: HypothesisPackage | None,
    new_hypothesis_id: Callable[[], str] = generate_hypothesis_id,
) -> HypothesisPackage:
    """Build the sole hypothesis object for one controlled operational cycle."""

    if process_evidence.episode_id != episode_id:
        raise HypothesisError("Process evidence must belong to the active Episode.")
    if process_evidence.runtime_event_id != runtime_event_id:
        raise HypothesisError("Process evidence must belong to the current Runtime event.")
    if previous is not None and previous.episode_id != episode_id:
        raise HypothesisError("Previous hypothesis cannot cross Episode boundaries.")

    market_state = process_evidence.current_process_state.name
    label = HYPOTHESIS_LABELS.get(market_state, HYPOTHESIS_LABELS["UNKNOWN"])
    semantic_code = _semantic_code_from_process(process_evidence)
    score = calculate_confidence(data)
    lifecycle_status = _canonical_lifecycle_status(
        label=label,
        explanation_confidence_score=score,
        previous=previous,
    )
    if lifecycle_status in (
        HypothesisLifecycleStatus.CREATED,
        HypothesisLifecycleStatus.REPLACED,
    ):
        hypothesis_id = new_hypothesis_id()
        if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
            raise HypothesisError("The hypothesis ID generator returned an invalid ID.")
        if hypothesis_id == runtime_event_id:
            raise HypothesisError("hypothesis_id must not equal the Runtime event ID.")
    else:
        assert previous is not None
        hypothesis_id = previous.hypothesis_id

    evidence = tuple(collect_evidence(data))
    supporting = tuple(item.value for item in evidence if item.positive)
    contradicting = tuple(item.value for item in evidence if not item.positive)
    return build_hypothesis_package(
        structural_evidence,
        market_efficiency_evidence,
        episode_id=episode_id,
        hypothesis_id=hypothesis_id,
        explanation_confidence_score=score,
        lifecycle_status=lifecycle_status,
        hypothesis_change_reason=_canonical_lifecycle_reason(lifecycle_status),
        previous_hypothesis_id=(previous.hypothesis_id if previous else None),
        previous_runtime_event_id=(previous.event_id if previous else None),
        runtime_event_id=runtime_event_id,
        hypothesis_label=label,
        semantic_code=semantic_code,
        hypothesis_summary=format_hypothesis_summary(
            label=label,
            confidence_score=score,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
        ),
    )


def _semantic_code_from_process(
    process_evidence: ProcessEvidence,
) -> HypothesisSemanticCode:
    if process_evidence.detected_transition is ProcessTransition.RECOVERED:
        return HypothesisSemanticCode.RECOVERY_EXPLANATION
    if process_evidence.current_process_state is ProcessState.CONTINUATION_ALIVE:
        return HypothesisSemanticCode.CONTINUATION_EXPLANATION
    if process_evidence.current_process_state is ProcessState.WEAKENING:
        return HypothesisSemanticCode.WEAKENING_EXPLANATION
    return HypothesisSemanticCode.UNRESOLVED


def _canonical_lifecycle_status(
    *,
    label: str,
    explanation_confidence_score: int,
    previous: HypothesisPackage | None,
) -> HypothesisLifecycleStatus:
    if previous is None:
        return HypothesisLifecycleStatus.CREATED
    if previous.hypothesis_label != label:
        return HypothesisLifecycleStatus.REPLACED
    if explanation_confidence_score < previous.explanation_confidence_score:
        return HypothesisLifecycleStatus.WEAKENED
    return HypothesisLifecycleStatus.UPDATED


def _canonical_lifecycle_reason(status: HypothesisLifecycleStatus) -> str:
    if status is HypothesisLifecycleStatus.CREATED:
        return "No previous canonical hypothesis was available in the Episode."
    if status is HypothesisLifecycleStatus.UPDATED:
        return "The same explanation remains with stable or higher confidence."
    if status is HypothesisLifecycleStatus.WEAKENED:
        return "The same explanation remains with lower confidence."
    return "The current explanation differs from the previous hypothesis."


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
) -> tuple[HypothesisEvidenceReference, ...]:
    structural_items = (
        HypothesisEvidenceReference(
            source_event_id=structural_evidence.event_id,
            source_section="structural_evidence",
            evidence_key=item,
            description=f"Structure reported {item}.",
        )
        for item in structural_evidence.structural_events
    )
    efficiency_items = (
        HypothesisEvidenceReference(
            source_event_id=market_efficiency_evidence.event_id,
            source_section="market_efficiency_evidence",
            evidence_key=item,
            description=f"Market Efficiency reported {item}.",
        )
        for item in market_efficiency_evidence.supporting_evidence
    )
    return tuple(structural_items) + tuple(efficiency_items)


def _contradicting_evidence(
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
) -> tuple[HypothesisEvidenceReference, ...]:
    structural_items = (
        HypothesisEvidenceReference(
            source_event_id=structural_evidence.event_id,
            source_section="structural_evidence",
            evidence_key=item,
            description=f"Structure reported contradictory evidence: {item}.",
        )
        for item in structural_evidence.evidence_against
    )
    efficiency_items = (
        HypothesisEvidenceReference(
            source_event_id=market_efficiency_evidence.event_id,
            source_section="market_efficiency_evidence",
            evidence_key=item,
            description=f"Market Efficiency reported contradictory evidence: {item}.",
        )
        for item in market_efficiency_evidence.evidence_against
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


def _confidence_level_from_score(score: int) -> ConfidenceLevel:
    """Categorize the existing 0-100 explanation-confidence score."""

    if score >= 80:
        return ConfidenceLevel.HIGH
    if score >= 50:
        return ConfidenceLevel.MEDIUM
    if score > 0:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.UNKNOWN
