"""Market Efficiency Engine v0.2.

Market Efficiency produces objective participation evidence from observations.
It remains evidence-only and does not own downstream Runtime reasoning.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pumpagent.runtime.domain import (
    MarketEfficiencyEvidence,
    ObservationPackage,
    RuntimeEvent,
)
from pumpagent.runtime.domain.enums import EvidenceStrength, UncertaintyLevel


PARTICIPATION_METRICS = (
    "open_interest",
    "funding_rate",
    "cvd",
    "liquidations",
    "volume",
)


class MarketEfficiencyError(ValueError):
    """Raised when Market Efficiency cannot produce evidence."""


def build_market_efficiency_evidence(
    observations: ObservationPackage,
    *,
    runtime_event_id: str | None = None,
) -> MarketEfficiencyEvidence:
    """Build participation evidence from observations without interpretation."""

    _validate_observations(observations)
    event_id = runtime_event_id or observations.event_id

    available_metrics = _available_participation_metrics(observations)
    missing_metrics = tuple(
        metric for metric in PARTICIPATION_METRICS if metric not in available_metrics
    )
    supporting_evidence = tuple(
        f"{metric}_available" for metric in available_metrics
    )
    evidence_against = tuple(f"{metric}_missing" for metric in missing_metrics)
    context = _build_market_mechanics_context(
        observations,
        available_metrics=available_metrics,
        missing_metrics=missing_metrics,
    )

    if available_metrics == ("volume",):
        return MarketEfficiencyEvidence(
            event_id=event_id,
            participation_summary="Only volume participation metric available.",
            participation_direction="not_assessed",
            efficiency_summary=(
                "No optional participation metrics available for efficiency evidence."
            ),
            efficiency_status="not_assessed",
            supporting_evidence=supporting_evidence,
            evidence_against=evidence_against,
            evidence_strength=EvidenceStrength.WEAK,
            uncertainty=UncertaintyLevel.HIGH,
            schema_version=observations.schema_version,
            participation_score=None,
            market_mechanics_context=context,
            notes="Market Efficiency Engine v0.2 preserves uncertainty when optional metrics are missing.",
        )

    evidence_strength = (
        EvidenceStrength.MODERATE
        if len(available_metrics) >= 3
        else EvidenceStrength.WEAK
    )
    uncertainty = (
        UncertaintyLevel.MEDIUM
        if len(missing_metrics) <= 2
        else UncertaintyLevel.HIGH
    )

    return MarketEfficiencyEvidence(
        event_id=event_id,
        participation_summary="Participation metric availability extracted.",
        participation_direction="not_assessed",
        efficiency_summary="Objective participation evidence extracted.",
        efficiency_status="not_assessed",
        supporting_evidence=supporting_evidence,
        evidence_against=evidence_against,
        evidence_strength=evidence_strength,
        uncertainty=uncertainty,
        schema_version=observations.schema_version,
        participation_score=None,
        market_mechanics_context=context,
        notes="Evidence-only participation output for downstream Runtime modules.",
    )


def add_market_efficiency_evidence(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only market_efficiency_evidence added."""

    if event.observation_package is None:
        raise MarketEfficiencyError("RuntimeEvent.observation_package is required.")

    evidence = build_market_efficiency_evidence(
        event.observation_package,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(market_efficiency_evidence=evidence)


def _validate_observations(observations: ObservationPackage) -> None:
    if observations.normalized_volume is None:
        raise MarketEfficiencyError(
            "ObservationPackage.normalized_volume is required."
        )

    try:
        float(observations.normalized_volume)
    except (TypeError, ValueError) as exc:
        raise MarketEfficiencyError(
            "ObservationPackage.normalized_volume must be numeric."
        ) from exc

    if not isinstance(observations.normalized_metrics, Mapping):
        raise MarketEfficiencyError(
            "ObservationPackage.normalized_metrics must be a mapping."
        )


def _available_participation_metrics(
    observations: ObservationPackage,
) -> tuple[str, ...]:
    available: list[str] = []

    if observations.normalized_volume is not None:
        available.append("volume")

    for metric in ("open_interest", "funding_rate", "cvd", "liquidations"):
        if metric in observations.normalized_metrics:
            available.append(metric)

    return tuple(available)


def _build_market_mechanics_context(
    observations: ObservationPackage,
    *,
    available_metrics: tuple[str, ...],
    missing_metrics: tuple[str, ...],
) -> dict[str, Any]:
    metrics = observations.normalized_metrics
    return {
        "source_observation_event_id": observations.event_id,
        "available_participation_metrics": available_metrics,
        "missing_participation_metrics": missing_metrics,
        "volume": observations.normalized_volume,
        "open_interest": metrics.get("open_interest"),
        "funding_rate": metrics.get("funding_rate"),
        "cvd": metrics.get("cvd"),
        "liquidations": metrics.get("liquidations"),
    }
