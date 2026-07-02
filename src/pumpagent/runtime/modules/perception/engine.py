"""Runtime Perception Engine v0.1.

Perception reads only MarketSnapshot and produces objective evidence contracts.
It does not create hypotheses, states, probabilities, confidence, or alerts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pumpagent.runtime.domain import (
    MarketEfficiencyEvidence,
    MarketSnapshot,
    ObservationPackage,
    RuntimeEvent,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import EvidenceStrength, UncertaintyLevel


REQUIRED_OHLCV_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


class PerceptionError(ValueError):
    """Raised when Perception cannot produce objective evidence."""


@dataclass(frozen=True)
class PerceptionEvidenceResult:
    """Runtime-only container for evidence produced from one MarketSnapshot."""

    structural_evidence: StructuralEvidence
    market_efficiency_evidence: MarketEfficiencyEvidence


def build_perception_evidence(
    snapshot: MarketSnapshot,
    *,
    runtime_event_id: str | None = None,
) -> PerceptionEvidenceResult:
    """Build objective evidence contracts from MarketSnapshot only."""

    _validate_market_snapshot(snapshot)
    event_id = runtime_event_id or snapshot.event_id

    return PerceptionEvidenceResult(
        structural_evidence=_build_structural_evidence(snapshot, event_id=event_id),
        market_efficiency_evidence=_build_market_efficiency_evidence(
            snapshot,
            event_id=event_id,
        ),
    )


def add_perception_evidence(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only Perception-owned evidence sections added."""

    if event.market_snapshot is None:
        raise PerceptionError("RuntimeEvent.market_snapshot is required.")

    before_snapshot = event.market_snapshot
    evidence = build_perception_evidence(
        before_snapshot,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(
        structural_evidence=evidence.structural_evidence,
        market_efficiency_evidence=evidence.market_efficiency_evidence,
    )


def build_observation_package(
    snapshot: MarketSnapshot,
    *,
    runtime_event_id: str | None = None,
) -> ObservationPackage:
    """Build an ObservationPackage from a MarketSnapshot without interpretation."""

    _validate_market_snapshot(snapshot)
    observation_event_id = runtime_event_id or snapshot.event_id

    available_metrics = ["price", "ohlcv", "volume"]
    normalized_metrics: dict[str, Any] = {}

    for key, value in snapshot.optional_market_metrics.items():
        available_metrics.append(str(key))
        normalized_metrics[str(key)] = value

    return ObservationPackage(
        event_id=observation_event_id,
        observation_timestamp=snapshot.timestamp,
        normalized_price=snapshot.price,
        normalized_ohlcv=snapshot.ohlcv,
        normalized_volume=snapshot.volume,
        available_metrics=tuple(available_metrics),
        missing_metrics=snapshot.missing_fields,
        data_quality_status=snapshot.data_quality_status,
        schema_version=snapshot.schema_version,
        normalized_metrics=normalized_metrics,
        previous_snapshot_reference=snapshot.event_id,
    )


def add_observation_package(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new event with only observation_package added."""

    if event.market_snapshot is None:
        raise PerceptionError("RuntimeEvent.market_snapshot is required.")

    # ObservationPackage.event_id belongs to the Runtime cycle. The source
    # MarketSnapshot.event_id is retained as previous_snapshot_reference.
    observations = build_observation_package(
        event.market_snapshot,
        runtime_event_id=event.event_id,
    )
    return event.with_sections(observation_package=observations)


def _build_structural_evidence(
    snapshot: MarketSnapshot,
    *,
    event_id: str,
) -> StructuralEvidence:
    candles = snapshot.ohlcv
    ohlcv_integrity = _ohlcv_integrity_context(snapshot)
    high_values = tuple(_as_float(candle["high"], "high", index) for index, candle in enumerate(candles))
    low_values = tuple(_as_float(candle["low"], "low", index) for index, candle in enumerate(candles))
    latest_close = _as_float(candles[-1]["close"], "close", len(candles) - 1)

    technical_context = {
        "source_snapshot_event_id": snapshot.event_id,
        "candle_count": len(candles),
        "latest_close": latest_close,
        "observed_high": max(high_values),
        "observed_low": min(low_values),
        "high_low_range": max(high_values) - min(low_values),
        "data_quality_status": snapshot.data_quality_status.value,
        "ohlcv_integrity": ohlcv_integrity,
    }

    return StructuralEvidence(
        event_id=event_id,
        structure_summary="Objective candle availability extracted.",
        trend_structure="not_assessed",
        structural_bias="not_assessed",
        key_levels=(
            {"type": "observed_high", "value": technical_context["observed_high"]},
            {"type": "observed_low", "value": technical_context["observed_low"]},
            {"type": "latest_close", "value": latest_close},
        ),
        structural_events=(
            "candle_data_available",
            "latest_close_available",
            "high_low_range_available",
        ),
        evidence_strength=EvidenceStrength.WEAK,
        evidence_against=_missing_evidence(snapshot.missing_fields),
        uncertainty=_uncertainty_from_snapshot(snapshot),
        schema_version=snapshot.schema_version,
        structural_score=None,
        technical_context=technical_context,
        notes="Perception v0.1 structural evidence is objective and does not assess state.",
    )


def _build_market_efficiency_evidence(
    snapshot: MarketSnapshot,
    *,
    event_id: str,
) -> MarketEfficiencyEvidence:
    available_metrics = _available_participation_metrics(snapshot)
    missing_metrics = tuple(
        metric
        for metric in ("open_interest", "funding_rate", "cvd", "liquidations")
        if metric not in available_metrics
    )

    context = {
        "source_snapshot_event_id": snapshot.event_id,
        "volume_available": snapshot.volume is not None,
        "available_participation_metrics": available_metrics,
        "missing_participation_metrics": missing_metrics,
        "open_interest_available": "open_interest" in available_metrics,
        "funding_rate_available": "funding_rate" in available_metrics,
        "cvd_available": "cvd" in available_metrics,
        "liquidations_available": "liquidations" in available_metrics,
        "data_quality_status": snapshot.data_quality_status.value,
    }

    return MarketEfficiencyEvidence(
        event_id=event_id,
        participation_summary="Objective participation metric availability extracted.",
        participation_direction="not_assessed",
        efficiency_summary="Efficiency not assessed by Perception v0.1.",
        efficiency_status="not_assessed",
        supporting_evidence=tuple(f"{metric}_available" for metric in available_metrics),
        evidence_against=tuple(f"{metric}_missing" for metric in missing_metrics),
        evidence_strength=EvidenceStrength.WEAK,
        uncertainty=_uncertainty_from_snapshot(snapshot),
        schema_version=snapshot.schema_version,
        participation_score=None,
        market_mechanics_context=context,
        notes="Perception v0.1 participation evidence is objective and does not assess outcomes.",
    )


def _validate_market_snapshot(snapshot: MarketSnapshot) -> None:
    if snapshot.ohlcv is None or len(snapshot.ohlcv) == 0:
        raise PerceptionError("MarketSnapshot.ohlcv must contain at least one candle.")

    for index, candle in enumerate(snapshot.ohlcv):
        if not isinstance(candle, Mapping):
            raise PerceptionError(
                f"MarketSnapshot.ohlcv candle {index} must be a mapping."
            )

        missing_fields = [
            field for field in REQUIRED_OHLCV_FIELDS if field not in candle
        ]
        if missing_fields:
            joined_fields = ", ".join(missing_fields)
            raise PerceptionError(
                "MarketSnapshot.ohlcv candle "
                f"{index} is missing required fields: {joined_fields}."
            )

    if snapshot.price is None:
        raise PerceptionError("MarketSnapshot.price is required.")

    if snapshot.volume is None:
        raise PerceptionError("MarketSnapshot.volume is required.")


def _ohlcv_integrity_context(snapshot: MarketSnapshot) -> dict[str, Any]:
    malformed_candle_indexes: list[int] = []
    missing_fields_by_index: dict[int, tuple[str, ...]] = {}

    for index, candle in enumerate(snapshot.ohlcv):
        if not isinstance(candle, Mapping):
            malformed_candle_indexes.append(index)
            missing_fields_by_index[index] = REQUIRED_OHLCV_FIELDS
            continue

        missing_fields = tuple(
            field for field in REQUIRED_OHLCV_FIELDS if field not in candle
        )
        if missing_fields:
            malformed_candle_indexes.append(index)
            missing_fields_by_index[index] = missing_fields

    latest_candle = snapshot.ohlcv[-1] if snapshot.ohlcv else None
    latest_timestamp = (
        latest_candle.get("timestamp")
        if isinstance(latest_candle, Mapping)
        else None
    )

    return {
        "ohlcv_present": len(snapshot.ohlcv) > 0,
        "candle_count": len(snapshot.ohlcv),
        "required_candle_fields": REQUIRED_OHLCV_FIELDS,
        "all_required_candle_fields_present": not malformed_candle_indexes,
        "latest_candle_timestamp": latest_timestamp,
        "malformed_candle_indexes": tuple(malformed_candle_indexes),
        "missing_fields_by_candle_index": missing_fields_by_index,
    }


def _available_participation_metrics(snapshot: MarketSnapshot) -> tuple[str, ...]:
    available = ["volume"]
    for metric in ("open_interest", "funding_rate", "cvd", "liquidations"):
        if metric in snapshot.optional_market_metrics:
            available.append(metric)
    return tuple(available)


def _missing_evidence(missing_fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{field}_missing" for field in missing_fields)


def _uncertainty_from_snapshot(snapshot: MarketSnapshot) -> UncertaintyLevel:
    if snapshot.missing_fields:
        return UncertaintyLevel.HIGH
    return UncertaintyLevel.MEDIUM


def _as_float(value: Any, field_name: str, candle_index: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PerceptionError(
            f"MarketSnapshot.ohlcv candle {candle_index} field {field_name} "
            "must be numeric."
        ) from exc
