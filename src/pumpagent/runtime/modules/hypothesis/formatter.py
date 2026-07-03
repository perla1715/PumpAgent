"""Formatting helpers for lightweight market hypotheses."""

from __future__ import annotations


def format_hypothesis_summary(
    *,
    label: str,
    confidence_score: int,
    supporting_evidence: tuple[str, ...],
    contradicting_evidence: tuple[str, ...],
) -> str:
    """Create a compact explanation for a market hypothesis."""

    parts = [f"{label} with confidence {confidence_score}%."]

    if supporting_evidence:
        parts.append(f"Supports: {_join_evidence(supporting_evidence)}.")
    else:
        parts.append("Supports: none.")

    if contradicting_evidence:
        parts.append(f"Contradicts: {_join_evidence(contradicting_evidence)}.")
    else:
        parts.append("Contradicts: none.")

    return " ".join(parts)


def _join_evidence(evidence: tuple[str, ...]) -> str:
    return ", ".join(evidence[:2])
