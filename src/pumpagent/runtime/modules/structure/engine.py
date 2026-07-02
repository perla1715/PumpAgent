"""Structure Engine v0.2.

Structure produces objective structural evidence from normalized observations.
It remains evidence-only and does not own downstream Runtime reasoning.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pumpagent.runtime.domain import ObservationPackage, RuntimeEvent, StructuralEvidence
from pumpagent.runtime.domain.enums import EvidenceStrength, UncertaintyLevel


REQUIRED_OHLCV_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


class StructureError(ValueError):
    """Raised when Structure cannot produce StructuralEvidence."""


def build_structural_evidence(
    observations: ObservationPackage,
    *,
    runtime_event_id: str | None = None,
) -> StructuralEvidence:
    """Build StructuralEvidence from observations without state classification."""

    _validate_observations(observations)
    event_id = runtime_event_id or observations.event_id
    candles = observations.normalized_ohlcv

    key_levels = _build_key_levels(candles)
    technical_context = {
        "candle_count": len(candles),
        "source_observation_event_id": observations.event_id,
    }

    if len(candles) < 2:
        return StructuralEvidence(
            event_id=event_id,
            structure_summary="Insufficient OHLCV sequence for structural evidence.",
            trend_structure="insufficient_sequence",
            structural_bias="not_assessed",
            key_levels=tuple(key_levels),
            structural_events=("insufficient_ohlcv_sequence",),
            evidence_strength=EvidenceStrength.UNKNOWN,
            evidence_against=(),
            uncertainty=UncertaintyLevel.HIGH,
            schema_version=observations.schema_version,
            technical_context=technical_context,
            notes="Structure Engine v0.2 requires at least two candles for sequence facts.",
        )

    first_close = _as_float(candles[0]["close"], "close", 0)
    last_close = _as_float(candles[-1]["close"], "close", len(candles) - 1)

    if last_close > first_close:
        trend_structure = "rising_close_sequence"
        structural_events = ("higher_final_close",)
    elif last_close < first_close:
        trend_structure = "falling_close_sequence"
        structural_events = ("lower_final_close",)
    else:
        trend_structure = "flat_close_sequence"
        structural_events = ("unchanged_final_close",)

    return StructuralEvidence(
        event_id=event_id,
        structure_summary="Objective OHLCV sequence facts extracted.",
        trend_structure=trend_structure,
        structural_bias="not_assessed",
        key_levels=tuple(key_levels),
        structural_events=structural_events,
        evidence_strength=EvidenceStrength.MODERATE,
        evidence_against=(),
        uncertainty=UncertaintyLevel.MEDIUM,
        schema_version=observations.schema_version,
        technical_context=technical_context,
        notes="Evidence-only structure output for downstream Runtime modules.",
    )


def add_structural_evidence(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only structural_evidence added."""

    if event.observation_package is None:
        raise StructureError("RuntimeEvent.observation_package is required.")

    evidence = build_structural_evidence(
        event.observation_package,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(structural_evidence=evidence)


def _validate_observations(observations: ObservationPackage) -> None:
    if len(observations.normalized_ohlcv) == 0:
        raise StructureError(
            "ObservationPackage.normalized_ohlcv must contain at least one candle."
        )

    for index, candle in enumerate(observations.normalized_ohlcv):
        if not isinstance(candle, Mapping):
            raise StructureError(
                f"ObservationPackage.normalized_ohlcv candle {index} must be a mapping."
            )

        missing_fields = [
            field for field in REQUIRED_OHLCV_FIELDS if field not in candle
        ]
        if missing_fields:
            joined_fields = ", ".join(missing_fields)
            raise StructureError(
                "ObservationPackage.normalized_ohlcv candle "
                f"{index} is missing required fields: {joined_fields}."
            )


def _build_key_levels(candles: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
    highs = [
        _as_float(candle["high"], "high", index)
        for index, candle in enumerate(candles)
    ]
    lows = [
        _as_float(candle["low"], "low", index)
        for index, candle in enumerate(candles)
    ]
    latest_close = _as_float(candles[-1]["close"], "close", len(candles) - 1)

    return [
        {"type": "observed_high", "value": max(highs)},
        {"type": "observed_low", "value": min(lows)},
        {"type": "latest_close", "value": latest_close},
    ]


def _as_float(value: Any, field_name: str, candle_index: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise StructureError(
            "ObservationPackage.normalized_ohlcv candle "
            f"{candle_index} field {field_name} must be numeric."
        ) from exc
