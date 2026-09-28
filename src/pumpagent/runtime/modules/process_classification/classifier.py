"""Conservative, deterministic Process Classification v1.

The classifier consumes only current objective evidence and the optional prior
Process result from the same Observation Episode.  It owns no history, clock,
lifecycle, confidence, hypothesis, state, or trading behavior.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import (
    DataQualityStatus,
    EvidenceStrength,
    ProcessDirection,
    UncertaintyLevel,
)
from pumpagent.runtime.domain.market_efficiency_evidence import MarketEfficiencyEvidence
from pumpagent.runtime.domain.observation_package import ObservationPackage
from pumpagent.runtime.domain.process_evidence import (
    ProcessEvidence,
    ProcessEvidenceAvailability,
    ProcessEvidenceFamily,
    ProcessEvidenceItem,
    ProcessEvidenceRelationship,
    ProcessState,
    ProcessTransition,
)
from pumpagent.runtime.domain.structural_evidence import StructuralEvidence


PROCESS_CLASSIFICATION_INPUT_SCHEMA_VERSION = "process_classification_input_v1"
_OPTIONAL_FAMILIES = (
    ProcessEvidenceFamily.OPEN_INTEREST,
    ProcessEvidenceFamily.CVD,
    ProcessEvidenceFamily.FUNDING,
    ProcessEvidenceFamily.LIQUIDATIONS,
)


@dataclass(frozen=True)
class ProcessClassificationInput(SerializableMixin):
    episode_id: str
    runtime_event_id: str
    exchange: str
    symbol: str
    timeframe: str
    observations: ObservationPackage
    structural_evidence: StructuralEvidence
    market_efficiency_evidence: MarketEfficiencyEvidence
    previous_process_evidence: ProcessEvidence | None
    classification_timestamp: datetime
    schema_version: str = PROCESS_CLASSIFICATION_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("episode_id", "runtime_event_id", "exchange", "symbol", "timeframe", "schema_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")
        _require_aware("classification_timestamp", self.classification_timestamp)
        if not isinstance(self.observations, ObservationPackage):
            raise ValueError("observations must be an ObservationPackage.")
        if not isinstance(self.structural_evidence, StructuralEvidence):
            raise ValueError("structural_evidence must be StructuralEvidence.")
        if not isinstance(self.market_efficiency_evidence, MarketEfficiencyEvidence):
            raise ValueError("market_efficiency_evidence must be MarketEfficiencyEvidence.")
        event_ids = {
            self.runtime_event_id,
            self.observations.event_id,
            self.structural_evidence.event_id,
            self.market_efficiency_evidence.event_id,
        }
        if len(event_ids) != 1:
            raise ValueError("Runtime and source evidence event IDs must align.")
        _validate_source_event(self.structural_evidence.technical_context, self.observations.event_id)
        _validate_source_event(self.market_efficiency_evidence.market_mechanics_context,
                               self.observations.event_id)
        _validate_embedded_identity(self.structural_evidence.technical_context, self)
        _validate_embedded_identity(self.market_efficiency_evidence.market_mechanics_context, self)
        if self.previous_process_evidence is not None:
            previous = self.previous_process_evidence
            if not isinstance(previous, ProcessEvidence):
                raise ValueError("previous_process_evidence must be ProcessEvidence or None.")
            if previous.episode_id != self.episode_id:
                raise ValueError("Previous Process evidence cannot cross an Episode boundary.")
            if _identity(previous) != _identity(self):
                raise ValueError("Previous Process evidence market identity must match.")


@dataclass(frozen=True)
class _Fact:
    family: ProcessEvidenceFamily
    key: str
    description: str
    direction: str
    source_module: str
    source_field: str
    value: Any | None
    available: bool = True
    unit: str | None = None
    process_direction: ProcessDirection | None = None


def classify_market_process(value: ProcessClassificationInput) -> ProcessEvidence:
    """Return one deterministic ProcessEvidence without mutating source contracts."""
    if not isinstance(value, ProcessClassificationInput):
        raise ValueError("value must be ProcessClassificationInput.")

    facts = [
        _quality_fact(value.observations),
        _price_fact(value.observations),
        _volume_fact(value.observations),
        _oi_fact(value.observations),
        _structure_fact(value.structural_evidence),
        *_optional_facts(value.observations),
    ]
    by_family = {fact.family: fact for fact in facts}
    quality_ok = by_family[ProcessEvidenceFamily.DATA_QUALITY].direction == "valid"
    price = by_family[ProcessEvidenceFamily.PRICE]
    volume = by_family[ProcessEvidenceFamily.VOLUME]
    oi = by_family[ProcessEvidenceFamily.OPEN_INTEREST]
    structure = by_family[ProcessEvidenceFamily.STRUCTURE]
    previous = value.previous_process_evidence
    process_direction = (
        price.process_direction
        if quality_ok and price.available and price.process_direction is not None
        else ProcessDirection.UNKNOWN
    )

    state = ProcessState.UNKNOWN
    reasons: list[str] = []
    if previous is None:
        reasons.extend(("Initial Episode classification.", "No previous Process baseline."))
    elif not quality_ok:
        reasons.append("Data quality does not permit reliable Process comparison.")
    elif not price.available:
        reasons.append("Comparable Price evidence is unavailable.")
    elif not volume.available:
        reasons.append("Comparable Volume participation is unavailable.")
    else:
        alive = (
            price.direction == "progressing"
            and volume.direction == "supported"
            and (oi.direction in {"rising", "non_decreasing"} or structure.direction == "constructive")
            and oi.direction != "declining"
            and structure.direction != "deteriorating"
        )
        weakening = (
            previous.current_process_state is ProcessState.CONTINUATION_ALIVE
            and price.direction in {"retained", "stalled", "deteriorating"}
            and volume.direction == "contracting"
            and (oi.direction in {"stagnant", "declining"} or structure.direction == "deteriorating")
        )
        if weakening:
            state = ProcessState.WEAKENING
        elif alive:
            state = ProcessState.CONTINUATION_ALIVE
        else:
            reasons.extend(_insufficiency(price, volume, oi, structure))

    # The first-cycle domain invariant takes precedence over supportive current facts.
    if previous is None:
        state = ProcessState.UNKNOWN
    relationship_facts = [_relate(fact, state) for fact in facts]
    supporting = tuple(_item(value, fact, relationship) for fact, relationship in relationship_facts
                       if relationship is ProcessEvidenceRelationship.SUPPORTING)
    contradicting = tuple(_item(value, fact, relationship) for fact, relationship in relationship_facts
                          if relationship is ProcessEvidenceRelationship.CONTRADICTING)
    neutral = tuple(_item(value, fact, relationship) for fact, relationship in relationship_facts
                    if relationship in {ProcessEvidenceRelationship.NEUTRAL,
                                        ProcessEvidenceRelationship.UNAVAILABLE})
    available = frozenset(fact.family for fact in facts if fact.available)
    missing = frozenset(fact.family for fact in facts if not fact.available)
    if state is ProcessState.UNKNOWN and not reasons:
        reasons.append("Current evidence is materially mixed or insufficiently coherent.")

    transition = _transition(previous.current_process_state if previous else None, state)
    result = ProcessEvidence(
        episode_id=value.episode_id,
        runtime_event_id=value.runtime_event_id,
        exchange=value.exchange,
        symbol=value.symbol,
        timeframe=value.timeframe,
        observation_timestamp=value.classification_timestamp,
        current_process_state=state,
        process_direction=process_direction,
        previous_process_state=previous.current_process_state if previous else None,
        detected_transition=transition,
        process_summary=_summary(state),
        supporting_evidence=supporting,
        contradicting_evidence=contradicting,
        neutral_evidence=neutral,
        available_evidence_families=available,
        missing_evidence_families=missing,
        insufficiency_reasons=tuple(dict.fromkeys(reasons)) if state is ProcessState.UNKNOWN else (),
        evidence_strength=(EvidenceStrength.MODERATE if state is not ProcessState.UNKNOWN
                           else EvidenceStrength.WEAK),
        uncertainty_level=(UncertaintyLevel.MEDIUM if state is not ProcessState.UNKNOWN
                           else UncertaintyLevel.HIGH),
    )
    result.validate_previous_evidence(previous)
    return result


def _quality_fact(obs: ObservationPackage) -> _Fact:
    valid = obs.data_quality_status is DataQualityStatus.VALID and not obs.validation_warnings
    return _Fact(ProcessEvidenceFamily.DATA_QUALITY, "observation_data_quality",
                 "Observation data is valid and aligned." if valid else
                 "Observation data quality or validation warnings prevent reliable comparison.",
                 "valid" if valid else "invalid", "observation_package",
                 "data_quality_status", obs.data_quality_status.value)


def _price_fact(obs: ObservationPackage) -> _Fact:
    closes = _series(obs.normalized_ohlcv, "close")
    if len(closes) < 2:
        return _Fact(
            ProcessEvidenceFamily.PRICE,
            "close_progression",
            "At least two valid OHLCV closes are required.",
            "unavailable",
            "observation_package",
            "normalized_ohlcv.close",
            None,
            available=False,
            process_direction=ProcessDirection.UNKNOWN,
        )
    deltas = [right - left for left, right in zip(closes, closes[1:])]
    if closes[-1] > closes[0]:
        process_direction = ProcessDirection.UP
    elif closes[-1] < closes[0]:
        process_direction = ProcessDirection.DOWN
    else:
        process_direction = ProcessDirection.NEUTRAL
    if closes[-1] < closes[0] or deltas[-1] < 0:
        direction = "deteriorating"
    elif deltas[-1] == 0:
        direction = "stalled"
    elif closes[-1] > closes[0] and len(deltas) > 1 and deltas[-1] < deltas[-2]:
        direction = "retained"
    elif closes[-1] > closes[0]:
        direction = "progressing"
    else:
        direction = "stalled"
    return _Fact(ProcessEvidenceFamily.PRICE, "close_progression",
                 f"Observed close progression is {direction}.", direction,
                 "observation_package", "normalized_ohlcv.close", tuple(closes),
                 process_direction=process_direction)


def _volume_fact(obs: ObservationPackage) -> _Fact:
    volumes = _series(obs.normalized_ohlcv, "volume")
    if len(volumes) < 2 or any(number < 0 for number in volumes):
        return _unavailable(ProcessEvidenceFamily.VOLUME, "volume_participation",
                            "A valid OHLCV Volume sequence and baseline are required.",
                            "normalized_ohlcv.volume")
    if volumes[-1] < volumes[0] or volumes[-1] < volumes[-2]:
        direction = "contracting"
    elif len(volumes) >= 3 and volumes[-1] > max(volumes[:-1]) and all(
            number == volumes[0] for number in volumes[:-1]):
        direction = "isolated_spike"
    elif volumes[-1] >= volumes[0]:
        direction = "supported"
    else:
        direction = "unresolved"
    return _Fact(ProcessEvidenceFamily.VOLUME, "volume_participation",
                 f"Observed Volume participation is {direction}.", direction,
                 "observation_package", "normalized_ohlcv.volume", tuple(volumes))


def _oi_fact(obs: ObservationPackage) -> _Fact:
    metrics = obs.normalized_metrics
    for key in ("oi_change_5m_pct", "oi_change_1m", "oi_change_pct"):
        number = _number(metrics.get(key))
        if number is not None:
            direction = "rising" if number > 0 else "declining" if number < 0 else "stagnant"
            return _Fact(ProcessEvidenceFamily.OPEN_INTEREST, "open_interest_direction",
                         f"Comparable Open Interest change is {direction}.", direction,
                         "observation_package", f"normalized_metrics.{key}", number, unit="percent")
    return _unavailable(ProcessEvidenceFamily.OPEN_INTEREST, "open_interest_direction",
                        "Open Interest has no comparable directional baseline.",
                        "normalized_metrics.open_interest")


def _structure_fact(evidence: StructuralEvidence) -> _Fact:
    events = set(evidence.structural_events)
    context = evidence.technical_context
    chart = context.get("chart_structure", {}) if isinstance(context, Mapping) else {}
    warnings = tuple(chart.get("warnings", ())) if isinstance(chart, Mapping) else ()
    deteriorating = bool(events & {"lower_high_detected", "lower_low_detected"})
    constructive = bool(events & {"higher_high_detected", "higher_low_detected"})
    if warnings and not (constructive or deteriorating):
        return _unavailable(ProcessEvidenceFamily.STRUCTURE, "structural_direction",
                            "Structure warnings leave directional inference unsupported.",
                            "technical_context.chart_structure.warnings")
    direction = "deteriorating" if deteriorating else "constructive" if constructive else "neutral"
    return _Fact(ProcessEvidenceFamily.STRUCTURE, "structural_direction",
                 f"Structural evidence is {direction}.", direction, "structure",
                 "structural_events", tuple(evidence.structural_events))


def _optional_facts(obs: ObservationPackage) -> list[_Fact]:
    result: list[_Fact] = []
    for family, key in ((ProcessEvidenceFamily.CVD, "cvd"),
                        (ProcessEvidenceFamily.FUNDING, "funding_rate"),
                        (ProcessEvidenceFamily.LIQUIDATIONS, "liquidations")):
        number = _number(obs.normalized_metrics.get(key))
        if number is None:
            result.append(_unavailable(family, key, f"{key} is unavailable or incomparable.",
                                       f"normalized_metrics.{key}"))
            continue
        direction = "positive" if number > 0 else "negative" if number < 0 else "neutral"
        result.append(_Fact(family, key, f"{key} is transported as {direction} context.",
                            direction, "observation_package", f"normalized_metrics.{key}", number))
    return result


def _relate(fact: _Fact, state: ProcessState) -> tuple[_Fact, ProcessEvidenceRelationship]:
    if not fact.available:
        return fact, ProcessEvidenceRelationship.UNAVAILABLE
    if fact.family in {ProcessEvidenceFamily.FUNDING, ProcessEvidenceFamily.LIQUIDATIONS}:
        return fact, ProcessEvidenceRelationship.NEUTRAL
    if fact.family is ProcessEvidenceFamily.CVD:
        if fact.direction == "negative" and state is ProcessState.CONTINUATION_ALIVE:
            return fact, ProcessEvidenceRelationship.CONTRADICTING
        return fact, ProcessEvidenceRelationship.NEUTRAL
    support = {
        ProcessState.CONTINUATION_ALIVE: {"valid", "progressing", "supported", "rising", "non_decreasing", "constructive"},
        ProcessState.WEAKENING: {"valid", "retained", "stalled", "deteriorating", "contracting", "stagnant", "declining"},
        ProcessState.UNKNOWN: {"valid", "progressing", "supported", "rising", "non_decreasing", "constructive"},
    }[state]
    adverse = {"invalid", "deteriorating", "contracting", "declining", "isolated_spike"}
    if fact.direction in support:
        return fact, ProcessEvidenceRelationship.SUPPORTING
    if fact.direction in adverse:
        return fact, ProcessEvidenceRelationship.CONTRADICTING
    return fact, ProcessEvidenceRelationship.NEUTRAL


def _item(value: ProcessClassificationInput, fact: _Fact,
          relationship: ProcessEvidenceRelationship) -> ProcessEvidenceItem:
    return ProcessEvidenceItem(
        evidence_family=fact.family, evidence_key=fact.key, description=fact.description,
        relationship=relationship, source_module=fact.source_module,
        source_field=fact.source_field, observation_timestamp=value.classification_timestamp,
        availability_status=(ProcessEvidenceAvailability.AVAILABLE if fact.available else
                             ProcessEvidenceAvailability.UNAVAILABLE),
        normalized_value=fact.value if fact.available else None, unit=fact.unit,
        timeframe=value.timeframe,
    )


def _insufficiency(price: _Fact, volume: _Fact, oi: _Fact, structure: _Fact) -> list[str]:
    reasons: list[str] = []
    if price.direction != "progressing":
        reasons.append("Price does not show coherent positive progression.")
    if volume.direction != "supported":
        reasons.append("Volume participation is not sustained or expanding.")
    if oi.direction not in {"rising", "non_decreasing"} and structure.direction != "constructive":
        reasons.append("No independent OI or constructive Structure confirmation is available.")
    if structure.direction == "deteriorating":
        reasons.append("Confirmed Structure contradiction blocks continuation.")
    return reasons or ["Current evidence is materially mixed or insufficiently coherent."]


def _unavailable(family: ProcessEvidenceFamily, key: str, description: str,
                 source_field: str) -> _Fact:
    return _Fact(family, key, description, "unavailable", "observation_package" if
                 family is not ProcessEvidenceFamily.STRUCTURE else "structure",
                 source_field, None, available=False)


def _series(candles: Sequence[Mapping[str, Any]], field: str) -> list[float]:
    result: list[float] = []
    for candle in candles:
        if not isinstance(candle, Mapping):
            return []
        number = _number(candle.get(field))
        if number is None:
            return []
        result.append(number)
    return result


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _transition(previous: ProcessState | None, current: ProcessState) -> ProcessTransition:
    if previous is None:
        return ProcessTransition.INITIAL
    if previous is current:
        return ProcessTransition.UNCHANGED
    if current is ProcessState.UNKNOWN:
        return ProcessTransition.BECAME_UNKNOWN
    if previous is ProcessState.WEAKENING and current is ProcessState.CONTINUATION_ALIVE:
        return ProcessTransition.RECOVERED
    return ProcessTransition.CHANGED


def _summary(state: ProcessState) -> str:
    return {
        ProcessState.UNKNOWN: "Evidence is unresolved or insufficient for a Process classification.",
        ProcessState.CONTINUATION_ALIVE: "Price, Volume, and independent confirmation support continuation.",
        ProcessState.WEAKENING: "An established continuation now has independently confirmed deterioration.",
    }[state]


def _validate_source_event(context: Mapping[str, Any], observation_event_id: str) -> None:
    source = context.get("source_observation_event_id") if isinstance(context, Mapping) else None
    if source is not None and source != observation_event_id:
        raise ValueError("Embedded source observation event ID must align.")


def _validate_embedded_identity(context: Mapping[str, Any], value: ProcessClassificationInput) -> None:
    if not isinstance(context, Mapping):
        return
    chart = context.get("chart_structure")
    sources = (context, chart) if isinstance(chart, Mapping) else (context,)
    for source in sources:
        supplied = tuple(source.get(key) for key in ("exchange", "symbol", "timeframe"))
        if any(item not in (None, "") for item in supplied):
            if tuple(_canon(key, item) for key, item in zip(("exchange", "symbol", "timeframe"), supplied)) != _identity(value):
                raise ValueError("Embedded evidence market identity must match classifier input.")


def _identity(value: Any) -> tuple[str, str, str]:
    return (_canon("exchange", value.exchange), _canon("symbol", value.symbol),
            _canon("timeframe", value.timeframe))


def _canon(name: str, value: Any) -> str:
    text = str(value).strip()
    return text.upper() if name == "symbol" else text.lower()


def _require_aware(name: str, value: Any) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
