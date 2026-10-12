"""Bounded TEST_ONLY Automatic Anomaly producer.

This module evaluates an owner-authorized, bounded universe using the recovered
Median/MAD Structure A profile.  It is deliberately not a production Scanner:
it has no scheduler, no trading path, no persistence/quorum gate and no
cross-timeframe vote.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from pumpagent.live_data.rolling_buffer import RollingNumericContextBuffer
from pumpagent.live_data.shadow_attention import ShadowAdmissionResult, ShadowAttentionAdmission


PROFILE_ID = "automatic_anomaly_test_only_median_mad_structure_a_v1"
DISCOVERY_MODE = "AUTOMATIC_ANOMALY"
AUTHORIZED_UNIVERSE_SOURCE = "TARGET_SPIKE_DEVELOPMENT_SET_v2_OWNER_AUTHORIZATION_2026-10-06"
AUTHORIZED_SYMBOLS = ("BOMEUSDT", "NIGHTUSDT", "MOVRUSDT", "BEAMUSDT")
TIMEFRAMES = ("1", "3", "5")
N_PRIOR = 120
PRICE_CUTOFF = 2.5
PARTICIPATION_CUTOFF = 2.0
MAD_SCALE = 1.4826
EPSILON = 1e-12

# This is a safety denylist only.  It does not establish market-cap ranking.
KNOWN_TOP50_SYMBOLS = frozenset(
    {
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
        "DOGEUSDT", "TRXUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "MATICUSDT",
        "TONUSDT", "SHIBUSDT", "LTCUSDT", "BCHUSDT", "UNIUSDT", "ATOMUSDT",
        "XLMUSDT", "ETCUSDT", "FILUSDT", "APTUSDT", "NEARUSDT", "ARBUSDT",
        "OPUSDT", "INJUSDT", "SUIUSDT", "AAVEUSDT", "MKRUSDT", "WIFUSDT",
    }
)


@dataclass(frozen=True)
class UniverseEligibility:
    symbol: str
    status: str
    authorization_source: str = AUTHORIZED_UNIVERSE_SOURCE
    reason: str = ""


@dataclass(frozen=True)
class AnomalyDecision:
    symbol: str
    timeframe: str
    evaluation_timestamp_utc: datetime
    as_of_timestamp_utc: datetime
    decision: str
    reason: str
    price_input: float | None
    price_prior_median: float | None
    price_prior_mad: float | None
    price_robust_score: float | None
    participation_field: str
    participation_input: float | None
    participation_prior_median: float | None
    participation_prior_mad: float | None
    participation_robust_score: float | None
    provider: str
    source_provenance: str
    oi_status: str
    missingness: tuple[str, ...] = ()
    top50_status: str = "PASS"
    trade_permission: bool = False
    discovery_mode: str = DISCOVERY_MODE
    profile_id: str = PROFILE_ID
    profile_family: str = "MEDIAN_MAD"
    structure: str = "A"
    price_cutoff: float = PRICE_CUTOFF
    participation_cutoff: float = PARTICIPATION_CUTOFF
    n_prior: int = N_PRIOR

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "evaluation_timestamp_utc": self.evaluation_timestamp_utc.isoformat(),
            "as_of_timestamp_utc": self.as_of_timestamp_utc.isoformat(),
            "decision": self.decision,
            "reason": self.reason,
            "price_input": self.price_input,
            "price_prior_median": self.price_prior_median,
            "price_prior_mad": self.price_prior_mad,
            "price_robust_score": self.price_robust_score,
            "participation_field": self.participation_field,
            "participation_input": self.participation_input,
            "participation_prior_median": self.participation_prior_median,
            "participation_prior_mad": self.participation_prior_mad,
            "participation_robust_score": self.participation_robust_score,
            "provider": self.provider,
            "source_provenance": self.source_provenance,
            "oi_status": self.oi_status,
            "missingness": list(self.missingness),
            "top50_status": self.top50_status,
            "discovery_mode": self.discovery_mode,
            "profile_id": self.profile_id,
            "profile_family": self.profile_family,
            "structure": self.structure,
            "price_cutoff": self.price_cutoff,
            "participation_cutoff": self.participation_cutoff,
            "n_prior": self.n_prior,
            "persistence": "NOT_USED",
            "quorum": "NOT_USED",
            "trade_permission": False,
        }
        payload["record_id"] = _record_id(payload)
        return payload


class AutomaticAnomalyJournal:
    """Append-only, duplicate-safe JSONL discovery journal."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, decision: AnomalyDecision | Mapping[str, Any]) -> str:
        payload = decision.to_dict() if isinstance(decision, AnomalyDecision) else dict(decision)
        record_id = payload.setdefault("record_id", _record_id(payload))
        if any(item.get("record_id") == record_id for item in self.records()):
            return "DUPLICATE_NOOP"
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return "APPENDED"


def check_universe(symbol: str, *, top50_status: str | None = None) -> UniverseEligibility:
    """Apply the owner-authorized bounded universe and top-50 safety gate."""

    normalized = symbol.strip().upper()
    if normalized not in AUTHORIZED_SYMBOLS:
        return UniverseEligibility(normalized, "UNAUTHORIZED", reason="Symbol is outside the Owner-authorized bounded universe.")
    if top50_status is not None and top50_status not in {"PASS", "EXCLUDED_TOP50", "TOP50_STATUS_UNRESOLVED"}:
        raise ValueError("top50_status must be PASS, EXCLUDED_TOP50, or TOP50_STATUS_UNRESOLVED")
    status = top50_status or ("EXCLUDED_TOP50" if normalized in KNOWN_TOP50_SYMBOLS else "PASS")
    reason = "Owner authorization identifies this bounded set as non-top-50." if status == "PASS" else "Top-50 status blocks substantive evaluation."
    return UniverseEligibility(normalized, status, reason=reason)


class MedianMadStructureAEvaluator:
    """Evaluate one symbol/timeframe from closed native rows only."""

    def evaluate(
        self,
        *,
        symbol: str,
        timeframe: str,
        rows: Sequence[Mapping[str, Any]],
        as_of: datetime,
        provider: str = "bybit",
        source_provenance: str = "bybit_native_closed_rolling_context",
        oi_status: str = "NOT_AVAILABLE",
        top50_status: str = "PASS",
    ) -> AnomalyDecision:
        now = _utc(as_of)
        eligible = [row for row in rows if _timestamp(row.get("market_timestamp_utc")) <= now]
        eligible.sort(key=lambda row: _timestamp(row.get("market_timestamp_utc")))
        current = eligible[-1] if eligible else None
        prior = eligible[:-1]
        missing: list[str] = []
        if current is None:
            return self._decision(symbol, timeframe, now, now, "INSUFFICIENT_EVIDENCE", "No closed as-of observation.", None, None, None, None, None, None, None, None, provider, source_provenance, oi_status, ("PRICE", "VOLUME"), top50_status)
        if len(prior) < N_PRIOR:
            return self._decision(symbol, timeframe, now, _timestamp(current["market_timestamp_utc"]), "INSUFFICIENT_EVIDENCE", "N=120 strictly prior observations are not available.", None, None, None, None, None, None, None, None, provider, source_provenance, oi_status, ("N120",), top50_status)
        prior = prior[-N_PRIOR:]
        price = _number(current.get("price"))
        volume = _number(current.get("volume"))
        p_values = [_number(row.get("price")) for row in prior]
        v_values = [_number(row.get("volume")) for row in prior]
        if price is None or any(value is None for value in p_values):
            missing.append("PRICE")
        if volume is None or any(value is None for value in v_values):
            missing.append("VOLUME")
        if missing:
            return self._decision(symbol, timeframe, now, _timestamp(current["market_timestamp_utc"]), "INSUFFICIENT_EVIDENCE", "Required Price/Volume evidence is missing.", price, None, None, None, volume, None, None, None, provider, source_provenance, oi_status, tuple(missing), top50_status)
        p_median, p_mad = _median_mad(p_values)  # type: ignore[arg-type]
        v_median, v_mad = _median_mad(v_values)  # type: ignore[arg-type]
        if p_mad <= EPSILON or v_mad <= EPSILON:
            return self._decision(symbol, timeframe, now, _timestamp(current["market_timestamp_utc"]), "INSUFFICIENT_EVIDENCE", "MAD scale is degenerate under the recovered rule.", price, p_median, p_mad, None, volume, v_median, v_mad, None, provider, source_provenance, oi_status, ("DEGENERATE_MAD",), top50_status)
        p_score = abs(price - p_median) / (MAD_SCALE * p_mad)
        v_score = abs(volume - v_median) / (MAD_SCALE * v_mad)
        decision = "ATTENTION" if p_score >= PRICE_CUTOFF and v_score >= PARTICIPATION_CUTOFF else "NO_ATTENTION"
        reason = "Price anomaly and available participation support passed Structure A." if decision == "ATTENTION" else "Price and participation did not both pass Structure A cutoffs."
        return self._decision(symbol, timeframe, now, _timestamp(current["market_timestamp_utc"]), decision, reason, price, p_median, p_mad, p_score, volume, v_median, v_mad, v_score, provider, source_provenance, oi_status, tuple(missing), top50_status)

    @staticmethod
    def _decision(symbol: str, timeframe: str, evaluation: datetime, as_of: datetime, decision: str, reason: str, price: float | None, p_median: float | None, p_mad: float | None, p_score: float | None, volume: float | None, v_median: float | None, v_mad: float | None, v_score: float | None, provider: str, provenance: str, oi_status: str, missing: tuple[str, ...], top50_status: str) -> AnomalyDecision:
        return AnomalyDecision(symbol.upper(), timeframe, evaluation, as_of, decision, reason, price, p_median, p_mad, p_score, "VOLUME", volume, v_median, v_mad, v_score, provider, provenance, oi_status, missing, top50_status)


def evaluate_bounded_universe(
    *,
    buffers: Mapping[str, RollingNumericContextBuffer],
    journal: AutomaticAnomalyJournal,
    as_of: datetime | None = None,
    top50_statuses: Mapping[str, str] | None = None,
) -> list[AnomalyDecision]:
    """Evaluate every owner-authorized symbol independently, with no nomination."""

    evaluation_time = _utc(as_of or datetime.now(timezone.utc))
    evaluator = MedianMadStructureAEvaluator()
    decisions: list[AnomalyDecision] = []
    for symbol in AUTHORIZED_SYMBOLS:
        eligibility = check_universe(symbol, top50_status=(top50_statuses or {}).get(symbol))
        if eligibility.status != "PASS":
            for timeframe in TIMEFRAMES:
                decision = evaluator._decision(symbol, timeframe, evaluation_time, evaluation_time, eligibility.status, eligibility.reason, None, None, None, None, None, None, None, None, "bybit", "universe_gate", "NOT_EVALUATED", (eligibility.status,), eligibility.status)
                journal.append(decision)
                decisions.append(decision)
            continue
        buffer = buffers[symbol]
        for timeframe in TIMEFRAMES:
            decision = evaluator.evaluate(symbol=symbol, timeframe=timeframe, rows=buffer.observations(timeframe), as_of=evaluation_time, oi_status="AVAILABLE" if timeframe == "5" and any(row.get("oi") is not None for row in buffer.observations(timeframe)) else "NOT_AVAILABLE")
            journal.append(decision)
            decisions.append(decision)
    return decisions


def admit_attention(admission: ShadowAttentionAdmission, decision: AnomalyDecision) -> ShadowAdmissionResult:
    if decision.decision != "ATTENTION":
        return ShadowAdmissionResult("NO_ADMISSION", False, None, decision.reason)
    return admission.admit_decision(
        discovery_mode=DISCOVERY_MODE,
        scanner_decision=True,
        admission_timestamp=decision.evaluation_timestamp_utc,
        reason=f"TEST_ONLY {decision.profile_id}; {decision.reason}",
    )


def _median_mad(values: Sequence[float]) -> tuple[float, float]:
    center = float(median(values))
    deviation = float(median([abs(value - center) for value in values]))
    return center, deviation


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str):
        raise ValueError("row timestamp must be an ISO timestamp")
    return _utc(datetime.fromisoformat(value))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _record_id(payload: Mapping[str, Any]) -> str:
    stable = {key: value for key, value in payload.items() if key != "record_id"}
    return "aa_" + hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
