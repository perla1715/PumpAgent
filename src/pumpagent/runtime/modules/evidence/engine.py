"""Evidence Engine MVP.

Evidence explains which observed metrics supported or weakened a scan result.
It does not classify market state, calculate confidence, or make decisions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from pumpagent.runtime.modules.market_metrics import metric_as_float


@dataclass(frozen=True)
class Evidence:
    name: str
    value: str
    positive: bool
    score: float | None = None
    confidence: float | None = None
    source: str | None = None
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        _validate_optional_ratio(self.score, "score")
        _validate_optional_ratio(self.confidence, "confidence")


@dataclass(frozen=True)
class AggregatedEvidenceScore:
    structural_score: float | None
    market_score: float | None
    temporal_score: float | None
    total_score: float
    evidence_count: int
    diagnostic_only: bool = True


class EvidenceScore:
    """Deterministic helper for diagnostic evidence-strength scores only."""

    STRENGTH_SCORES = {
        "unknown": 0.0,
        "weak": 0.25,
        "moderate": 0.5,
        "strong": 1.0,
    }

    TEMPORAL_TREND_SCORES = {
        "UNKNOWN": 0.0,
        "WEAKENING": 0.0,
        "STABLE": 0.5,
        "IMPROVING": 1.0,
    }

    @classmethod
    def combine_strength(
        cls,
        *,
        score: float | None,
        confidence: float | None = None,
    ) -> float | None:
        """Combine explicit evidence strength with optional confidence."""

        _validate_optional_ratio(score, "score")
        _validate_optional_ratio(confidence, "confidence")

        if score is None:
            return None

        if confidence is None:
            return score

        return score * confidence

    @classmethod
    def total_score(cls, evidence: Iterable[Evidence]) -> float:
        """Calculate a deterministic average score for evidence items."""

        scores = tuple(
            score
            for score in (
                cls.combine_strength(
                    score=item.score if item.score is not None else _signed_score(item),
                    confidence=item.confidence,
                )
                for item in evidence
            )
            if score is not None
        )
        return _average(scores)

    @classmethod
    def aggregate(
        cls,
        *,
        structural_evidence: object | None = None,
        market_evidence: object | None = None,
        temporal_evidence: object | None = None,
    ) -> AggregatedEvidenceScore:
        """Aggregate structural, market, and temporal evidence diagnostics."""

        structural_score = cls._score_domain_evidence(
            structural_evidence,
            explicit_score_field="structural_score",
        )
        market_score = cls._score_domain_evidence(
            market_evidence,
            explicit_score_field="participation_score",
        )
        temporal_score = cls._score_temporal_evidence(temporal_evidence)
        domain_scores = tuple(
            score
            for score in (structural_score, market_score, temporal_score)
            if score is not None
        )

        return AggregatedEvidenceScore(
            structural_score=structural_score,
            market_score=market_score,
            temporal_score=temporal_score,
            total_score=_average(domain_scores),
            evidence_count=len(domain_scores),
        )

    @classmethod
    def _score_domain_evidence(
        cls,
        evidence: object | None,
        *,
        explicit_score_field: str,
    ) -> float | None:
        if evidence is None:
            return None

        if isinstance(evidence, Evidence):
            return cls.combine_strength(score=evidence.score, confidence=evidence.confidence)

        explicit_score = getattr(evidence, explicit_score_field, None)
        if explicit_score is not None:
            return cls.combine_strength(score=explicit_score)

        strength = getattr(evidence, "evidence_strength", None)
        if strength is None:
            return None

        return cls.STRENGTH_SCORES.get(_enum_value(strength))

    @classmethod
    def _score_temporal_evidence(cls, evidence: object | None) -> float | None:
        if evidence is None:
            return None

        if isinstance(evidence, Evidence):
            return cls.combine_strength(score=evidence.score, confidence=evidence.confidence)

        explicit_score = getattr(evidence, "score", None)
        confidence = getattr(evidence, "confidence", None)
        if explicit_score is not None:
            return cls.combine_strength(score=explicit_score, confidence=confidence)

        trend = getattr(evidence, "trend", None)
        if trend is None:
            return None

        return cls.TEMPORAL_TREND_SCORES.get(_enum_value(trend))


def collect_evidence(data: Any) -> list[Evidence]:
    """Collect lightweight evidence from current market metrics only."""

    price_change_1m = metric_as_float(data, "price_change_1m")
    volume_spike_ratio = metric_as_float(data, "volume_spike_ratio")
    oi_change_1m = metric_as_float(data, "oi_change_1m")

    return [
        Evidence(
            name="Price",
            value="Price increasing"
            if price_change_1m is not None and price_change_1m > 0
            else "Price not increasing",
            positive=price_change_1m is not None and price_change_1m > 0,
        ),
        Evidence(
            name="Volume",
            value="Volume above average"
            if volume_spike_ratio is not None and volume_spike_ratio > 2
            else "Volume not above average",
            positive=volume_spike_ratio is not None and volume_spike_ratio > 2,
        ),
        Evidence(
            name="OI",
            value="OI increasing"
            if oi_change_1m is not None and oi_change_1m > 0
            else "OI not increasing",
            positive=oi_change_1m is not None and oi_change_1m > 0,
        ),
    ]


def format_evidence(evidence: list[Evidence]) -> str:
    """Format evidence as compact signed scan text."""

    parts = []
    for item in evidence:
        sign = "+" if item.positive else "-"
        parts.append(f"{sign} {item.value}")
    return "; ".join(parts)


def aggregate_evidence_score(
    *,
    structural_evidence: object | None = None,
    market_evidence: object | None = None,
    temporal_evidence: object | None = None,
) -> AggregatedEvidenceScore:
    """Expose a diagnostic aggregate score without affecting Runtime state."""

    return EvidenceScore.aggregate(
        structural_evidence=structural_evidence,
        market_evidence=market_evidence,
        temporal_evidence=temporal_evidence,
    )


def _signed_score(evidence: Evidence) -> float:
    return 1.0 if evidence.positive else 0.0


def _average(scores: tuple[float, ...]) -> float:
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _validate_optional_ratio(value: float | None, field_name: str) -> None:
    if value is None:
        return
    if value < 0.0 or value > 1.0:
        raise ValueError(f"Evidence {field_name} must be between 0.0 and 1.0.")
