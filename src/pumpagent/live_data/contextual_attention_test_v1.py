"""TEST_ONLY contextual attention profile evaluator and append-only journal.

This module consumes accepted Stage B records only.  It has no production
authority, thresholds, ranking, H2, baseline, Tracker, or trading behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CONTRACT_VERSION = "CONTEXTUAL_ATTENTION_TEST_V1"
P_PRICE_LED = "P_PRICE_LED"
P_PRICE_VOLUME_CONTEXT = "P_PRICE_VOLUME_CONTEXT"
P_PRICE_VOLUME_SEQUENCE = "P_PRICE_VOLUME_SEQUENCE"
PROFILES = (P_PRICE_LED, P_PRICE_VOLUME_CONTEXT, P_PRICE_VOLUME_SEQUENCE)

TEST_ATTENTION_YES = "TEST_ATTENTION_YES"
TEST_ATTENTION_NO = "TEST_ATTENTION_NO"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
DEFERRED_DATA_QUALITY = "DEFERRED_DATA_QUALITY"


@dataclass(frozen=True)
class ExperimentRecord:
    record_id: str
    symbol: str
    profile_id: str
    decision_state: str
    decision_timestamp: str
    market_data_timestamp: str | None
    latest_closed_bar_timestamp: str | None
    stage_b_observation_timestamp: str | None
    first_attention_timestamp: str | None
    price_evidence_state: str
    volume_evidence_state: str
    optional_oi_state: str
    evidence_completeness: str
    attention_reason: str
    provenance: dict[str, Any]
    source_stage_b_identity: str | None
    event_continuity_state: str | None
    experiment_contract_version: str
    test_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def evaluate_stage_b_record(
    stage_b: Mapping[str, Any],
    profile_id: str,
    *,
    decision_timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Map existing qualitative Stage B evidence to one shadow decision."""
    decision_time = _utc(decision_timestamp or _utcnow())
    profile = str(profile_id)
    if profile not in PROFILES:
        return _failure(stage_b, profile, decision_time, "UNKNOWN_PROFILE")

    symbol = str(stage_b.get("symbol", "")).upper()
    source = str(stage_b.get("native_or_reconstructed", "UNKNOWN")).upper()
    completeness = str(stage_b.get("evidence_completeness", "INSUFFICIENT_EVIDENCE"))
    price = str(stage_b.get("price_activity_state", "INSUFFICIENT_EVIDENCE"))
    volume = str(stage_b.get("volume_activity_state", "INSUFFICIENT_EVIDENCE"))
    oi = str(stage_b.get("optional_oi_activity_state", "NOT_AVAILABLE"))
    provenance = _provenance(stage_b)
    common = dict(
        symbol=symbol,
        profile_id=profile,
        market_data_timestamp=stage_b.get("market_data_timestamp"),
        latest_closed_bar_timestamp=stage_b.get("latest_closed_1m_timestamp"),
        stage_b_observation_timestamp=stage_b.get("observation_timestamp"),
        price_evidence_state=price,
        volume_evidence_state=volume,
        optional_oi_state=oi,
        evidence_completeness=completeness,
        provenance=provenance,
        source_stage_b_identity=stage_b.get("source_evidence_identity") or stage_b.get("source_stage_a_identity"),
        event_continuity_state=stage_b.get("event_continuity_state"),
    )

    if not symbol:
        return _make_record(common, decision_time, INSUFFICIENT_EVIDENCE, "MALFORMED_SYMBOL")
    if not _eligible(stage_b):
        return _make_record(common, decision_time, INSUFFICIENT_EVIDENCE, "STAGE_B_SOURCE_NOT_ELIGIBLE")
    if source != "NATIVE":
        return _make_record(common, decision_time, INSUFFICIENT_EVIDENCE, "NON_NATIVE_PROVENANCE")
    if completeness in {"STALE", "DATA_GAP", "DEFERRED_DATA_QUALITY"}:
        return _make_record(common, decision_time, DEFERRED_DATA_QUALITY, f"DATA_QUALITY_{completeness}")
    if completeness != "COMPLETE_FOR_STAGE_B_SKELETON":
        return _make_record(common, decision_time, INSUFFICIENT_EVIDENCE, "INCOMPLETE_STAGE_B_EVIDENCE")
    if profile == P_PRICE_VOLUME_SEQUENCE:
        sequence = stage_b.get("sequence_activity_state")
        if sequence is None:
            return _make_record(common, decision_time, INSUFFICIENT_EVIDENCE, "SEQUENCE_CONTEXT_NOT_AVAILABLE")
        sequence = str(sequence).upper()
        if sequence not in {"ACTIVITY_OBSERVED", "NO_ACTIVITY_OBSERVED"}:
            return _make_record(common, decision_time, INSUFFICIENT_EVIDENCE, "INVALID_SEQUENCE_CONTEXT")
        state = TEST_ATTENTION_YES if sequence == "ACTIVITY_OBSERVED" else TEST_ATTENTION_NO
        return _make_record(common, decision_time, state, f"SEQUENCE_{sequence}")
    if price == "ACTIVITY_OBSERVED":
        reason = "PRICE_ACTIVITY_OBSERVED" if profile == P_PRICE_LED else "PRICE_ACTIVITY_OBSERVED_VOLUME_CONTEXT_RECORDED"
        return _make_record(common, decision_time, TEST_ATTENTION_YES, reason)
    if price == "NO_ACTIVITY_OBSERVED":
        return _make_record(common, decision_time, TEST_ATTENTION_NO, "PRICE_NO_ACTIVITY_OBSERVED")
    return _make_record(common, decision_time, INSUFFICIENT_EVIDENCE, "PRICE_ACTIVITY_NOT_DECIDABLE")


class ExperimentJournal:
    """Small append-only JSONL journal with deterministic duplicate suppression."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def append(self, record: Mapping[str, Any]) -> str:
        rid = str(record["record_id"])
        if any(row.get("record_id") == rid for row in self.records()):
            return "DUPLICATE_NOOP"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(record), sort_keys=True) + "\n")
        return "APPENDED"


def _eligible(stage_b: Mapping[str, Any]) -> bool:
    # Stage B's accepted CONTEXT_AVAILABLE status is itself the result of its
    # Stage A eligibility/rank-freshness gate.  When propagated fields are
    # present, validate them defensively rather than trusting the status.
    eligibility_fields = {"trading_universe_eligible", "top50_status", "rank_freshness_state"}
    if eligibility_fields & set(stage_b):
        return bool(stage_b.get("trading_universe_eligible")) and stage_b.get("top50_status") == "NON_TOP50_ELIGIBLE" and stage_b.get("rank_freshness_state") == "FRESH"
    return stage_b.get("status") == "CONTEXT_AVAILABLE"


def _make_record(common: dict[str, Any], decision_time: datetime, state: str, reason: str) -> dict[str, Any]:
    identity_material = {
        "profile_id": common["profile_id"],
        "contract": CONTRACT_VERSION,
        "source": common["source_stage_b_identity"],
        "market_data_timestamp": common["market_data_timestamp"],
        "latest_closed_bar_timestamp": common["latest_closed_bar_timestamp"],
    }
    record = ExperimentRecord(record_id=_identity(identity_material), decision_state=state, decision_timestamp=decision_time.isoformat(), attention_reason=reason, experiment_contract_version=CONTRACT_VERSION, first_attention_timestamp=decision_time.isoformat() if state == TEST_ATTENTION_YES else None, **common)
    return record.to_dict()


def _failure(stage_b: Mapping[str, Any], profile: str, decision_time: datetime, reason: str) -> dict[str, Any]:
    common = dict(symbol=str(stage_b.get("symbol", "")).upper(), profile_id=profile, market_data_timestamp=stage_b.get("market_data_timestamp"), latest_closed_bar_timestamp=stage_b.get("latest_closed_1m_timestamp"), stage_b_observation_timestamp=stage_b.get("observation_timestamp"), price_evidence_state="INSUFFICIENT_EVIDENCE", volume_evidence_state="INSUFFICIENT_EVIDENCE", optional_oi_state="NOT_AVAILABLE", evidence_completeness="INSUFFICIENT_EVIDENCE", provenance=_provenance(stage_b), source_stage_b_identity=None, event_continuity_state=None)
    return _make_record(common, decision_time, INSUFFICIENT_EVIDENCE, reason)


def _provenance(stage_b: Mapping[str, Any]) -> dict[str, Any]:
    return {key: stage_b.get(key) for key in ("market_venue", "data_transport_provider", "metric_provider", "timeframe", "native_or_reconstructed")}


def _identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
