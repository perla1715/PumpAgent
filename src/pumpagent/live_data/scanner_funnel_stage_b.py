"""TEST_ONLY Scanner Funnel v1 Stage B contextual-evidence skeleton.

This module validates Stage A evidence and exposes qualitative context states.
It intentionally contains no anomaly cutoffs, ranking, H2, baseline, Tracker,
true-pump, or trading authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence

from pumpagent.live_data.stage_b_activity_mapper import map_closed_native_1m_activity

NOT_EVALUATED = "NOT_EVALUATED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
CONTEXT_AVAILABLE = "CONTEXT_AVAILABLE"
ACTIVITY_NOT_EVALUATED = "ACTIVITY_NOT_EVALUATED"
STALE = "STALE"
DATA_GAP = "DATA_GAP"


@dataclass(frozen=True)
class StageBRecord:
    symbol: str
    observation_timestamp: str
    market_data_timestamp: str | None
    latest_closed_1m_timestamp: str | None
    market_venue: str | None
    data_transport_provider: str | None
    timeframe: str
    native_or_reconstructed: str
    price_activity_state: str
    volume_activity_state: str
    optional_oi_activity_state: str
    evidence_completeness: str
    attention_reason: str
    attention_candidate_state: str
    context_window_provenance: str
    provider_freshness: str
    activity_evaluation_state: str
    activity_evaluation_reason: str
    activity_semantic_profile_id: str | None
    source_stage_a_identity: str
    source_evidence_identity: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "observation_timestamp": self.observation_timestamp,
            "market_data_timestamp": self.market_data_timestamp,
            "latest_closed_1m_timestamp": self.latest_closed_1m_timestamp,
            "market_venue": self.market_venue,
            "data_transport_provider": self.data_transport_provider,
            "timeframe": self.timeframe,
            "native_or_reconstructed": self.native_or_reconstructed,
            "price_activity_state": self.price_activity_state,
            "volume_activity_state": self.volume_activity_state,
            "optional_oi_activity_state": self.optional_oi_activity_state,
            "evidence_completeness": self.evidence_completeness,
            "attention_reason": self.attention_reason,
            "attention_candidate_state": self.attention_candidate_state,
            "context_window_provenance": self.context_window_provenance,
            "provider_freshness": self.provider_freshness,
            "activity_evaluation_state": self.activity_evaluation_state,
            "activity_evaluation_reason": self.activity_evaluation_reason,
            "activity_semantic_profile_id": self.activity_semantic_profile_id,
            "source_stage_a_identity": self.source_stage_a_identity,
            "source_evidence_identity": self.source_evidence_identity,
            "status": self.status,
            "stage": "B_CONTEXTUAL_ANOMALY_SKELETON",
        }


def evaluate_stage_a_record(
    stage_a: Mapping[str, Any],
    *,
    closed_1m_bars: Sequence[Mapping[str, Any]] | None,
    optional_oi: Mapping[str, Any] | None = None,
    observation_timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Produce one deterministic, non-authoritative Stage B evidence record."""
    observation = _utc(observation_timestamp or _parse_time(stage_a.get("scanner_cycle_timestamp")))
    symbol = str(stage_a.get("symbol", "")).upper()
    if not symbol:
        return _failure("", observation, "MALFORMED_STAGE_A_SYMBOL")
    source_stage_a_identity = _identity({k: stage_a.get(k) for k in (
        "symbol", "scanner_cycle_timestamp", "latest_closed_1m_timestamp", "top50_status"
    )})
    common = {
        "symbol": symbol,
        "observation_timestamp": observation.isoformat(),
        "market_data_timestamp": stage_a.get("market_data_timestamp"),
        "latest_closed_1m_timestamp": stage_a.get("latest_closed_1m_timestamp"),
        "market_venue": stage_a.get("market_venue"),
        "data_transport_provider": stage_a.get("data_transport_provider"),
        "timeframe": "1",
        "native_or_reconstructed": str(stage_a.get("native_or_reconstructed", "UNKNOWN")).upper(),
        "source_stage_a_identity": source_stage_a_identity,
    }
    freshness = str(stage_a.get("rank_freshness_state", "UNKNOWN"))
    source_form = common["native_or_reconstructed"]
    eligibility = bool(stage_a.get("trading_universe_eligible")) and stage_a.get("top50_status") == "NON_TOP50_ELIGIBLE" and freshness == "FRESH"
    cache_status = str(stage_a.get("closed_1m_cache_status", "UNKNOWN"))
    bars = list(closed_1m_bars or [])
    if not eligibility:
        return _record(common, price=NOT_EVALUATED, volume=NOT_EVALUATED, oi=_oi_state(optional_oi), completeness=INSUFFICIENT_EVIDENCE, reason="STAGE_A_NOT_ELIGIBLE", attention=NOT_EVALUATED, context="STAGE_A_ELIGIBILITY", freshness=freshness, evidence=bars, status="NOT_EVALUATED")
    if source_form != "NATIVE":
        return _record(common, price=NOT_EVALUATED, volume=NOT_EVALUATED, oi=_oi_state(optional_oi), completeness=INSUFFICIENT_EVIDENCE, reason="NON_NATIVE_STAGE_A_PROVENANCE", attention=NOT_EVALUATED, context="CLOSED_NATIVE_1M", freshness=freshness, evidence=bars, status="NOT_EVALUATED")
    if cache_status in {"STALE", STALE}:
        return _record(common, price=NOT_EVALUATED, volume=NOT_EVALUATED, oi=_oi_state(optional_oi), completeness=STALE, reason="STALE_CLOSED_1M_CONTEXT", attention=NOT_EVALUATED, context="CLOSED_NATIVE_1M", freshness="STALE", evidence=bars, status=STALE)
    if stage_a.get("data_gap_state") == "DATA_GAP":
        return _record(common, price=NOT_EVALUATED, volume=NOT_EVALUATED, oi=_oi_state(optional_oi), completeness=DATA_GAP, reason="CLOSED_1M_DATA_GAP", attention=NOT_EVALUATED, context="CLOSED_NATIVE_1M", freshness=freshness, evidence=bars, status=DATA_GAP)
    if not bars:
        return _record(common, price=INSUFFICIENT_EVIDENCE, volume=INSUFFICIENT_EVIDENCE, oi=_oi_state(optional_oi), completeness=INSUFFICIENT_EVIDENCE, reason="MISSING_CLOSED_1M_CONTEXT", attention=NOT_EVALUATED, context="CLOSED_NATIVE_1M", freshness=freshness, evidence=bars, status="NOT_EVALUATED")
    malformed = [bar for bar in bars if not _valid_closed_native_bar(bar)]
    if malformed:
        return _record(common, price=INSUFFICIENT_EVIDENCE, volume=INSUFFICIENT_EVIDENCE, oi=_oi_state(optional_oi), completeness=INSUFFICIENT_EVIDENCE, reason="INVALID_OR_NON_NATIVE_CLOSED_BAR", attention=NOT_EVALUATED, context="CLOSED_NATIVE_1M", freshness=freshness, evidence=bars, status="NOT_EVALUATED")
    mapped = map_closed_native_1m_activity(
        symbol=symbol, bars=bars, observation_timestamp=observation,
    )
    return _record(common, price=mapped["price_activity_state"], volume=CONTEXT_AVAILABLE, oi=_oi_state(optional_oi), completeness="COMPLETE_FOR_STAGE_B_SKELETON", reason=mapped["activity_evaluation_reason"], attention=NOT_EVALUATED, context="CLOSED_NATIVE_1M", freshness=freshness, evidence=bars, status="CONTEXT_AVAILABLE", activity=mapped)


def evaluate_stage_a_batch(records: Sequence[Mapping[str, Any]], *, bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]], oi_by_symbol: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Isolate malformed symbols while preserving deterministic output order."""
    output: list[dict[str, Any]] = []
    for stage_a in records:
        symbol = str(stage_a.get("symbol", "")).upper()
        try:
            output.append(evaluate_stage_a_record(stage_a, closed_1m_bars=bars_by_symbol.get(symbol), optional_oi=(oi_by_symbol or {}).get(symbol)))
        except Exception as exc:
            output.append(_failure(symbol, datetime.now(timezone.utc), f"SYMBOL_STAGE_B_ERROR:{type(exc).__name__}:{exc}"))
    return output


def _record(common: dict[str, Any], *, price: str, volume: str, oi: str, completeness: str, reason: str, attention: str, context: str, freshness: str, evidence: Sequence[Mapping[str, Any]], status: str, activity: Mapping[str, str] | None = None) -> dict[str, Any]:
    source_evidence_identity = _identity([dict(x) for x in evidence])
    activity = dict(activity or {})
    return StageBRecord(**common, price_activity_state=price, volume_activity_state=volume, optional_oi_activity_state=oi, evidence_completeness=completeness, attention_reason=reason, attention_candidate_state=attention, context_window_provenance=context, provider_freshness=freshness, activity_evaluation_state=activity.get("activity_evaluation_state", NOT_EVALUATED), activity_evaluation_reason=activity.get("activity_evaluation_reason", "ACTIVITY_EVALUATION_NOT_RUN"), activity_semantic_profile_id=activity.get("activity_semantic_profile_id"), source_evidence_identity=source_evidence_identity, status=status).to_dict()


def _failure(symbol: str, observation: datetime, reason: str) -> dict[str, Any]:
    return StageBRecord(symbol=symbol, observation_timestamp=observation.isoformat(), market_data_timestamp=None, latest_closed_1m_timestamp=None, market_venue=None, data_transport_provider=None, timeframe="1", native_or_reconstructed="UNKNOWN", price_activity_state=INSUFFICIENT_EVIDENCE, volume_activity_state=INSUFFICIENT_EVIDENCE, optional_oi_activity_state="NOT_AVAILABLE", evidence_completeness=INSUFFICIENT_EVIDENCE, attention_reason=reason, attention_candidate_state=NOT_EVALUATED, context_window_provenance="UNKNOWN", provider_freshness="UNKNOWN", activity_evaluation_state=NOT_EVALUATED, activity_evaluation_reason="ACTIVITY_EVALUATION_NOT_RUN", activity_semantic_profile_id=None, source_stage_a_identity="MALFORMED", source_evidence_identity=_identity([]), status="INSUFFICIENT_EVIDENCE").to_dict()


def _oi_state(value: Mapping[str, Any] | None) -> str:
    if value is None: return "NOT_REQUESTED"
    status = str(value.get("status", "")).upper()
    return status if status in {"NOT_REQUESTED", "NOT_AVAILABLE", "AVAILABLE", "INSUFFICIENT_EVIDENCE"} else "INSUFFICIENT_EVIDENCE"


def _valid_closed_native_bar(bar: Mapping[str, Any]) -> bool:
    if bar.get("closed") is False or "native_or_reconstructed" not in bar or str(bar.get("native_or_reconstructed")).upper() != "NATIVE": return False
    return all(bar.get(k) is not None for k in ("timestamp", "open", "high", "low", "close", "volume"))


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime): return _utc(value)
    if value is None: return datetime.now(timezone.utc)
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None: raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]
