"""Structure Engine MVP.

Structure produces or validates objective structural evidence.
It remains evidence-only and does not own downstream Runtime reasoning.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pumpagent.runtime.domain import ObservationPackage, RuntimeEvent, StructuralEvidence
from pumpagent.runtime.domain.enums import EvidenceStrength, UncertaintyLevel
from pumpagent.runtime.modules.structure.candles import (
    REQUIRED_OHLCV_FIELDS,
    to_structure_candles,
)
from pumpagent.runtime.modules.structure.fibonacci import (
    calculate_fibonacci_levels,
    describe_fibonacci_position,
)
from pumpagent.runtime.modules.structure.indicators import calculate_emas
from pumpagent.runtime.modules.structure.models import (
    ChartStructure,
    EmaSet,
    FibonacciLevel,
    StructureCandle,
    SwingPoint,
)
from pumpagent.runtime.modules.structure.swings import (
    detect_swings,
    latest_sequence_points,
    latest_valid_impulse,
)


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
    chart_structure = _build_chart_structure(
        observations,
        runtime_event_id=event_id,
    )
    technical_context = {
        "candle_count": chart_structure.candle_count,
        "source_observation_event_id": observations.event_id,
        "chart_structure": chart_structure.to_dict(),
    }

    return StructuralEvidence(
        event_id=event_id,
        structure_summary=_structure_summary(chart_structure),
        trend_structure=_trend_structure(chart_structure),
        structural_bias="not_assessed",
        key_levels=chart_structure.key_levels,
        structural_events=chart_structure.structural_events,
        evidence_strength=_evidence_strength(chart_structure),
        evidence_against=(),
        uncertainty=_uncertainty(chart_structure),
        schema_version=observations.schema_version,
        technical_context=technical_context,
        notes="Evidence-only Structure Engine MVP output.",
    )


def refine_structural_evidence(
    evidence: StructuralEvidence,
    *,
    runtime_event_id: str | None = None,
) -> StructuralEvidence:
    """Validate externally supplied StructuralEvidence without interpretation."""

    event_id = runtime_event_id or evidence.event_id
    if evidence.event_id != event_id:
        raise StructureError(
            "StructuralEvidence.event_id must match the RuntimeEvent.event_id."
        )

    return evidence


def add_structural_evidence(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only structural_evidence added."""

    if event.structural_evidence is not None:
        evidence = refine_structural_evidence(
            event.structural_evidence,
            runtime_event_id=event.event_id,
        )
        return event.with_sections(structural_evidence=evidence)

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


def _build_chart_structure(
    observations: ObservationPackage,
    *,
    runtime_event_id: str,
) -> ChartStructure:
    candles = to_structure_candles(observations.normalized_ohlcv)
    latest_price = candles[-1].close
    emas = calculate_emas(candles)
    swing_highs, swing_lows = detect_swings(candles)
    latest_higher_high, latest_lower_high = latest_sequence_points(swing_highs)
    latest_higher_low, latest_lower_low = latest_sequence_points(swing_lows)
    impulse = latest_valid_impulse(swing_highs, swing_lows)
    fibonacci_levels = calculate_fibonacci_levels(impulse)
    fibonacci_position = describe_fibonacci_position(latest_price, fibonacci_levels)
    ema_positions = _ema_positions(latest_price, emas)
    warnings = _warnings(candles, emas, impulse)
    key_levels = _build_key_levels(candles, emas, fibonacci_levels)
    structural_events = _structural_events(
        candles=candles,
        emas=emas,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        latest_higher_high=latest_higher_high,
        latest_higher_low=latest_higher_low,
        latest_lower_high=latest_lower_high,
        latest_lower_low=latest_lower_low,
        impulse_valid=impulse.is_valid,
        fibonacci_levels=fibonacci_levels,
    )

    return ChartStructure(
        event_id=runtime_event_id,
        symbol="",
        exchange="",
        timeframe="",
        candle_count=len(candles),
        first_price=candles[0].close,
        latest_price=latest_price,
        emas=emas,
        ema_positions=ema_positions,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        latest_higher_high=latest_higher_high,
        latest_higher_low=latest_higher_low,
        latest_lower_high=latest_lower_high,
        latest_lower_low=latest_lower_low,
        latest_impulse=impulse,
        fibonacci_levels=fibonacci_levels,
        fibonacci_position=fibonacci_position,
        structural_events=structural_events,
        key_levels=key_levels,
        warnings=warnings,
        source_observation_event_id=observations.event_id,
    )


def _ema_positions(latest_price: float, emas: EmaSet) -> dict[str, str]:
    return {
        "ema_7": _relative_position(latest_price, emas.ema_7),
        "ema_14": _relative_position(latest_price, emas.ema_14),
        "ema_21": _relative_position(latest_price, emas.ema_21),
    }


def _relative_position(latest_price: float, value: float | None) -> str:
    if value is None:
        return "unavailable"
    if latest_price > value:
        return "above"
    if latest_price < value:
        return "below"
    return "at"


def _warnings(
    candles: tuple[StructureCandle, ...],
    emas: EmaSet,
    impulse: Any,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if len(candles) < 2:
        warnings.append("insufficient_ohlcv_sequence")
    for period in emas.unavailable_periods:
        warnings.append(f"ema_{period}_unavailable")
    if len(candles) < 5:
        warnings.append("insufficient_candles_for_2_left_2_right_swings")
    if not impulse.is_valid:
        warnings.append("no_valid_swing_impulse")
    return tuple(warnings)


def _build_key_levels(
    candles: tuple[StructureCandle, ...],
    emas: EmaSet,
    fibonacci_levels: tuple[FibonacciLevel, ...],
) -> tuple[dict[str, Any], ...]:
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    latest_close = candles[-1].close
    key_levels: list[dict[str, Any]] = [
        {"type": "observed_high", "value": max(highs)},
        {"type": "observed_low", "value": min(lows)},
        {"type": "latest_close", "value": latest_close},
    ]

    for period, value in (
        (7, emas.ema_7),
        (14, emas.ema_14),
        (21, emas.ema_21),
    ):
        if value is not None:
            key_levels.append({"type": f"ema_{period}", "value": value})

    for level in fibonacci_levels:
        key_levels.append(
            {
                "type": "fibonacci",
                "ratio": level.ratio,
                "label": level.label,
                "value": level.price,
            }
        )

    return tuple(key_levels)


def _structural_events(
    *,
    candles: tuple[StructureCandle, ...],
    emas: EmaSet,
    swing_highs: tuple[SwingPoint, ...],
    swing_lows: tuple[SwingPoint, ...],
    latest_higher_high: SwingPoint | None,
    latest_higher_low: SwingPoint | None,
    latest_lower_high: SwingPoint | None,
    latest_lower_low: SwingPoint | None,
    impulse_valid: bool,
    fibonacci_levels: tuple[FibonacciLevel, ...],
) -> tuple[str, ...]:
    events: list[str] = []
    if len(candles) < 2:
        events.append("insufficient_ohlcv_sequence")
    for period in emas.available_periods:
        events.append(f"ema_{period}_available")
    for period in emas.unavailable_periods:
        events.append(f"ema_{period}_unavailable")
    if swing_highs:
        events.append("swing_high_detected")
    if swing_lows:
        events.append("swing_low_detected")
    if latest_higher_high is not None:
        events.append("higher_high_detected")
    if latest_higher_low is not None:
        events.append("higher_low_detected")
    if latest_lower_high is not None:
        events.append("lower_high_detected")
    if latest_lower_low is not None:
        events.append("lower_low_detected")
    if impulse_valid:
        events.append("valid_impulse_detected")
    else:
        events.append("no_valid_swing_impulse")
    if fibonacci_levels:
        events.append("fibonacci_levels_available")
    else:
        events.append("fibonacci_levels_unavailable")
    return tuple(events)


def _structure_summary(chart_structure: ChartStructure) -> str:
    if chart_structure.candle_count < 2:
        return "Insufficient OHLCV sequence for structural evidence."
    return "Objective chart structure facts extracted."


def _trend_structure(chart_structure: ChartStructure) -> str:
    if chart_structure.candle_count < 2:
        return "insufficient_sequence"
    if chart_structure.latest_impulse.is_valid and chart_structure.fibonacci_levels:
        return "ema_swing_fibonacci_structure_available"
    if chart_structure.latest_impulse.is_valid:
        return "ema_swing_structure_available"
    if chart_structure.latest_price > chart_structure.first_price:
        return "rising_close_sequence"
    if chart_structure.latest_price < chart_structure.first_price:
        return "falling_close_sequence"
    return "flat_close_sequence"


def _evidence_strength(chart_structure: ChartStructure) -> EvidenceStrength:
    if chart_structure.candle_count < 2:
        return EvidenceStrength.UNKNOWN
    if chart_structure.latest_impulse.is_valid and chart_structure.fibonacci_levels:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.WEAK


def _uncertainty(chart_structure: ChartStructure) -> UncertaintyLevel:
    if chart_structure.candle_count < 2:
        return UncertaintyLevel.HIGH
    if chart_structure.latest_impulse.is_valid and chart_structure.fibonacci_levels:
        return UncertaintyLevel.MEDIUM
    return UncertaintyLevel.HIGH
