"""Lifecycle helpers for lightweight market hypotheses."""

from __future__ import annotations

from typing import Any


def resolve_hypothesis_status(
    *,
    label: str,
    confidence_score: int,
    previous: Any | None,
) -> str:
    """Resolve the MVP lifecycle status for a market hypothesis."""

    if previous is None:
        return "CREATED"

    if previous.label != label:
        return "REPLACED"

    if confidence_score >= previous.confidence_score:
        return "UPDATED"

    return "WEAKENED"
