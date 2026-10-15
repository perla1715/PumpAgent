"""TEST_ONLY causal hybrid START_OF_PUMP detector.

This additive module deliberately does not emit PUMP_5PLUS, trade, dump, or
admission decisions.  It consumes completed-bar evidence and emits only the
bounded start states from the research contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from statistics import median
from typing import Any, Mapping

P1_1M_IMPULSE_FLOOR_PCT = 0.5
P2_5M_DISPLACEMENT_FLOOR_PCT = 0.5
MICRO_RANGE_BARS = 5
SCHEMA_VERSION = "hybrid-start/v1"

NO_PUMP_START = "NO_PUMP_START"
START_CANDIDATE = "START_CANDIDATE"
START_CONFIRMED = "START_CONFIRMED"
AMBIGUOUS_START = "AMBIGUOUS_START"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("completed bar timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _pct(numerator: float, denominator: float) -> float:
    return (float(numerator) / float(denominator) - 1.0) * 100.0


def _value(bar: Mapping[str, Any], name: str, fallback: float | None = None) -> float | None:
    value = bar.get(name)
    return None if value is None and fallback is None else float(fallback if value is None else value)


def _signed_return(bar: Mapping[str, Any]) -> float:
    supplied = _value(bar, "signed_return_pct")
    if supplied is not None:
        return supplied
    return _pct(float(bar["close"]), float(bar["open"]))


def _displacement(bar: Mapping[str, Any], horizon: int, history: list[dict[str, Any]]) -> float | None:
    key = f"signed_{horizon}m_displacement_pct"
    supplied = _value(bar, key)
    if supplied is not None:
        return supplied
    if len(history) < horizon - 1:
        return None
    return _pct(float(bar["close"]), float(history[-(horizon - 1)]["close"]))


def stable_start_event_id(symbol: str, start_timestamp: str | datetime,
                          start_price: float, schema_version: str = SCHEMA_VERSION) -> str:
    raw = "|".join((symbol.upper(), _timestamp(start_timestamp).isoformat(),
                     f"{float(start_price):.16g}", schema_version))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class HybridStartState:
    symbol: str
    status: str = INSUFFICIENT_EVIDENCE
    event_id: str | None = None
    start_timestamp: str | None = None
    start_price: float | None = None
    previous_completed_close: float | None = None
    continuity_ok: bool | None = None
    last_processed_closed_1m_timestamp: str | None = None
    candidate_timestamp: str | None = None
    current_event_pump_pct: float | None = None
    bars: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["bars"] = list(self.bars)
        return result

    @classmethod
    def restore(cls, document: Mapping[str, Any]) -> "HybridStartState":
        return cls(**dict(document))


class HybridPumpStartDetector:
    """Small, deterministic, in-memory TEST_ONLY start detector.

    Optional ``state`` is a plain mapping snapshot supplied by the caller for
    restart-safe restoration.  No runtime journal or production wiring is used.
    """

    def __init__(self, symbol: str, state: Mapping[str, Any] | None = None) -> None:
        self.state = HybridStartState.restore(state) if state else HybridStartState(symbol.upper())
        if self.state.symbol != symbol.upper():
            raise ValueError("state symbol does not match detector symbol")

    def snapshot(self) -> dict[str, Any]:
        return self.state.snapshot()

    def process_bar(self, bar: Mapping[str, Any], *, s_status: bool | None = None,
                    m_status: bool | None = None, volume: float | None = None) -> dict[str, Any]:
        """Process one completed native 1m bar; replay of the same bar is a no-op."""
        if bar.get("closed", bar.get("completed", True)) is False:
            raise ValueError("forming candles are not accepted")
        ts = _timestamp(bar["timestamp"])
        iso = ts.isoformat()
        if self.state.last_processed_closed_1m_timestamp is not None and iso <= self.state.last_processed_closed_1m_timestamp:
            return {"action": "DUPLICATE_NOOP", "status": self.state.status, "event_id": self.state.event_id}

        current = dict(bar)
        current["timestamp"] = iso
        current["signed_return_pct"] = _signed_return(current)
        current["signed_3m_displacement_pct"] = _displacement(current, 3, self.state.bars)
        current["signed_5m_displacement_pct"] = _displacement(current, 5, self.state.bars)
        current["s_status"] = s_status
        current["m_status"] = m_status
        if volume is not None:
            current["volume"] = volume

        prior = self.state.bars
        self.state.last_processed_closed_1m_timestamp = iso
        self.state.bars = (prior + [current])[-32:]
        if len(prior) < 5 or current["signed_3m_displacement_pct"] is None or current["signed_5m_displacement_pct"] is None:
            self.state.status = INSUFFICIENT_EVIDENCE
            return {"action": "STATE", "status": self.state.status, "timestamp": iso}

        prior3 = [float(x["signed_return_pct"]) for x in prior[-3:]]
        r1 = float(current["signed_return_pct"])
        d3 = float(current["signed_3m_displacement_pct"])
        d5 = float(current["signed_5m_displacement_pct"])
        acceleration = r1 > float(median(prior3))
        candidate = (r1 > 0.0 and r1 >= P1_1M_IMPULSE_FLOOR_PCT and d3 > 0.0
                     and d5 >= P2_5M_DISPLACEMENT_FLOOR_PCT and acceleration)
        if not candidate:
            if self.state.event_id is None:
                self.state.status = NO_PUMP_START
            return {"action": "STATE", "status": self.state.status, "timestamp": iso,
                    "signed_return_pct": r1, "signed_3m_displacement_pct": d3,
                    "signed_5m_displacement_pct": d5}

        if self.state.event_id is not None:
            self.state.status = START_CONFIRMED
            self.state.current_event_pump_pct = _pct(float(current["high"]), float(self.state.start_price))
            return {"action": "UPDATE_EXISTING_EVENT", "status": START_CONFIRMED,
                    "event_id": self.state.event_id, "timestamp": iso,
                    "current_event_pump_pct": self.state.current_event_pump_pct}

        self.state.candidate_timestamp = iso
        prior_high = max(float(x["high"]) for x in prior[-MICRO_RANGE_BARS:])
        range_break = float(current["close"]) > prior_high
        body_pct = _value(current, "body_pct", _pct(float(current["close"]), float(current["open"])))
        range_pct = _value(current, "range_pct", _pct(float(current["high"]), float(current["low"])))
        body_support = bool(current.get("body_support", body_pct is not None and body_pct > 0.0))
        range_support = bool(current.get("range_support", range_pct is not None and range_pct > 0.0))
        if not range_break or not (body_support or range_support):
            self.state.status = START_CANDIDATE
            return {"action": "STATE", "status": START_CANDIDATE, "timestamp": iso,
                    "prior_five_high": prior_high, "range_break": range_break,
                    "body_support": body_support, "range_support": range_support}

        previous_close = float(prior[-1]["close"])
        start_price = float(current["open"])
        self.state.status = START_CONFIRMED
        self.state.start_timestamp = iso
        self.state.start_price = start_price
        self.state.previous_completed_close = previous_close
        self.state.continuity_ok = abs(start_price - previous_close) <= max(abs(previous_close) * 0.02, 1e-12)
        self.state.event_id = stable_start_event_id(self.state.symbol, iso, start_price)
        self.state.current_event_pump_pct = _pct(float(current["high"]), start_price)
        return {"action": "START_CONFIRMED", "status": START_CONFIRMED, "event_id": self.state.event_id,
                "timestamp": iso, "start_price": start_price,
                "previous_completed_close": previous_close,
                "continuity_ok": self.state.continuity_ok,
                "current_event_pump_pct": self.state.current_event_pump_pct}
