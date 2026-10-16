"""Isolated TEST_ONLY per-closed-1m Pump Start observation stream.

This module deliberately has no Scanner, admission, setup, or trading output.
It requires supplied *native* completed 1m/3m/5m bars and records only
point-in-time observational state in its own append-only journal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from pumpagent.live_data.adapters.coinalyze_oi import CoinalyzeOIRecord
from pumpagent.live_data.hybrid_pump_start import (
    MICRO_RANGE_BARS, P1_1M_IMPULSE_FLOOR_PCT, P2_5M_DISPLACEMENT_FLOOR_PCT,
    stable_start_event_id,
)

CONTRACT_VERSION = "MINIMAL_PUMP_START_DETECTION_TEST_ONLY_V2_ACTIVE_EVENT"
NO_PUMP_START = "NO_PUMP_START"
START_CANDIDATE = "START_CANDIDATE"
START_CONFIRMED = "START_CONFIRMED"
ACTIVE_PUMP_EVENT = "ACTIVE_PUMP_EVENT"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
PARTICIPATION_CONTEXT_AVAILABLE = "PARTICIPATION_CONTEXT_AVAILABLE"
PARTICIPATION_INSUFFICIENT_EVIDENCE = "PARTICIPATION_INSUFFICIENT_EVIDENCE"
# Reuses the accepted Stage A closed-bar freshness bound; it is not a new
# Pump Start parameter.
_EXISTING_CLOSED_BAR_MAX_AGE = timedelta(minutes=2)


def _utc(value: datetime | str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _bar_timestamp(bar: Mapping[str, Any]) -> datetime:
    raw = bar.get("timestamp", bar.get("market_timestamp_utc"))
    if raw is None:
        raise ValueError("bar timestamp missing")
    return _utc(str(raw))


def _native_closed(bar: Mapping[str, Any]) -> bool:
    return (bar.get("closed", bar.get("completed")) is True
            and bar.get("native_or_reconstructed") == "NATIVE")


def _pct(current: float, previous: float) -> float:
    return (float(current) / float(previous) - 1.0) * 100.0


class PumpStartJournal:
    """Append-only JSONL journal, deterministically keyed by source bar."""
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self.path.touch(exist_ok=True)

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, row: Mapping[str, Any]) -> str:
        row = dict(row)
        key = (row.get("contract_version"), row.get("symbol"), row.get("source_closed_1m_timestamp"))
        for old in self.records():
            if (old.get("contract_version"), old.get("symbol"), old.get("source_closed_1m_timestamp")) == key:
                return "DUPLICATE_NOOP"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush(); os.fsync(handle.fileno())
        return "APPENDED"


class PumpStartStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists(): self.save({"contract_version": CONTRACT_VERSION, "symbols": {}})

    def load(self) -> dict[str, Any]: return json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, symbol: str) -> dict[str, Any] | None: return self.load().get("symbols", {}).get(symbol.upper())

    def put(self, symbol: str, state: Mapping[str, Any]) -> None:
        doc = self.load(); doc.setdefault("symbols", {})[symbol.upper()] = dict(state); self.save(doc)

    def save(self, document: Mapping[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(dict(document), sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


class MinimalPumpStartStream:
    """Causal stream with explicit priming, gaps, provenance, and OI context."""
    def __init__(self, journal_path: str | Path, state_path: str | Path) -> None:
        self.journal = PumpStartJournal(journal_path); self.store = PumpStartStateStore(state_path)

    @staticmethod
    def _new_state(symbol: str) -> dict[str, Any]:
        return {"symbol": symbol.upper(), "last_processed_closed_1m_timestamp": None,
                "history": [], "active_event_id": None, "start_of_pump_timestamp": None,
                "start_price": None, "max_pump_price": None, "max_pump_pct": None,
                "max_pump_timestamp": None, "first_5pct_observation_timestamp": None,
                "gap_recovery": False}

    def prime_context(self, symbol: str, bars_1m: Sequence[Mapping[str, Any]]) -> None:
        """Restore strictly-prior context without writing prospective records."""
        valid = sorted((self._normalise_1m(b) for b in bars_1m if _native_closed(b)), key=lambda b: b["timestamp"])
        if not valid: return
        state = self.store.get(symbol) or self._new_state(symbol)
        state["history"] = valid[-MICRO_RANGE_BARS:]
        state["last_processed_closed_1m_timestamp"] = valid[-1]["timestamp"]
        state["gap_recovery"] = False
        self.store.put(symbol, state)

    def process(self, symbol: str, *, bars_1m: Sequence[Mapping[str, Any]], bars_3m: Sequence[Mapping[str, Any]],
                bars_5m: Sequence[Mapping[str, Any]], oi_records: Sequence[CoinalyzeOIRecord] = (),
                asof: datetime) -> list[dict[str, Any]]:
        """Process only bars later than persistent state; never backfill old bars."""
        asof = _utc(asof); symbol = symbol.upper(); state = self.store.get(symbol) or self._new_state(symbol)
        last = _utc(state["last_processed_closed_1m_timestamp"]) if state.get("last_processed_closed_1m_timestamp") else None
        rows = []; output: list[dict[str, Any]] = []
        for raw in bars_1m:
            try:
                if _bar_timestamp(raw) <= asof: rows.append(raw)
            except (TypeError, ValueError, KeyError):
                row = self._insufficient(
                    {**self._base(symbol, asof, asof), "source_closed_1m_timestamp": None},
                    state,
                    "MALFORMED_1M_TIMESTAMP",
                )
                row["journal_result"] = self.journal.append(row); output.append(row)
        rows.sort(key=_bar_timestamp)
        for raw in rows:
            timestamp = _bar_timestamp(raw)
            if last is not None and timestamp <= last: continue
            row = self._evaluate_one(symbol, state, raw, bars_3m, bars_5m, oi_records, asof)
            state["last_processed_closed_1m_timestamp"] = timestamp.isoformat()
            result = self.journal.append(row); row["journal_result"] = result
            output.append(row); last = timestamp
            self.store.put(symbol, state)
        return output

    def _evaluate_one(self, symbol: str, state: dict[str, Any], raw: Mapping[str, Any], bars3: Sequence[Mapping[str, Any]],
                      bars5: Sequence[Mapping[str, Any]], oi: Sequence[CoinalyzeOIRecord], asof: datetime) -> dict[str, Any]:
        timestamp = _bar_timestamp(raw); base = self._base(symbol, timestamp, asof)
        if not _native_closed(raw):
            return self._insufficient(base, state, "NON_NATIVE_OR_FORMING_1M")
        try:
            current = self._normalise_1m(raw)
        except (TypeError, ValueError, KeyError):
            return self._insufficient(base, state, "MALFORMED_NATIVE_1M")
        if asof - timestamp > _EXISTING_CLOSED_BAR_MAX_AGE:
            return self._insufficient(base, state, "STALE_CLOSED_1M")
        prior = state["history"]
        if state.get("last_processed_closed_1m_timestamp") and timestamp != _utc(state["last_processed_closed_1m_timestamp"]) + timedelta(minutes=1):
            state["history"] = []; state["gap_recovery"] = True
            return self._insufficient(base, state, "TIMESTAMP_GAP")
        native3 = self._latest_native(bars3, asof=timestamp + timedelta(minutes=1), timeframe_minutes=3)
        native5 = self._latest_native(bars5, asof=timestamp + timedelta(minutes=1), timeframe_minutes=5)
        state["history"] = (prior + [current])[-MICRO_RANGE_BARS:]
        if len(prior) < MICRO_RANGE_BARS:
            return self._insufficient(base, state, "INSUFFICIENT_WARMUP")
        if native3 is None or native5 is None:
            return self._insufficient(base, state, "MISSING_NATIVE_3M_OR_5M")
        r1 = _pct(current["close"], current["open"])
        d3 = _pct(native3["close"], native3["open"])
        d5 = _pct(native5["close"], native5["open"])
        prior_returns = [_pct(x["close"], x["open"]) for x in prior[-3:]]
        candidate = r1 > 0 and r1 >= P1_1M_IMPULSE_FLOOR_PCT and d3 > 0 and d5 >= P2_5M_DISPLACEMENT_FLOOR_PCT and r1 > median(prior_returns)
        participation, oi_row = self._participation(oi, native5, asof)
        details = {**base, "current_closed_1m": current, "native_3m_timestamp": native3["timestamp"].isoformat(),
                   "native_5m_timestamp": native5["timestamp"].isoformat(), "signed_1m_return_pct": r1,
                   "signed_3m_displacement_pct": d3, "signed_5m_displacement_pct": d5,
                   "prior_three_1m_return_median_pct": median(prior_returns), "participation_state": participation,
                   "oi_provenance": oi_row.to_dict() if oi_row else None}
        if state.get("active_event_id"):
            return self._active_update(details, state, current, timestamp, "ACTIVE_EVENT_CONTINUES")
        if not candidate:
            return {**details, "state": NO_PUMP_START, "reason": "PRICE_SHAPE_NOT_CANDIDATE", "event_id": None}
        prior_high = max(x["high"] for x in prior[-MICRO_RANGE_BARS:]); body = _pct(current["close"], current["open"]); rng = _pct(current["high"], current["low"])
        confirmed = current["close"] > prior_high and (body > 0 or rng > 0)
        if not confirmed:
            return {**details, "state": START_CANDIDATE, "reason": "CANDIDATE_NO_PRIOR_FIVE_HIGH_BREAK", "prior_five_high": prior_high}
        event_id = stable_start_event_id(symbol, timestamp, current["open"], CONTRACT_VERSION)
        start_price = current["open"]
        first_max_pct = _pct(current["high"], start_price)
        state.update(active_event_id=event_id, start_of_pump_timestamp=timestamp.isoformat(), start_price=start_price,
                     max_pump_price=current["high"], max_pump_pct=first_max_pct, max_pump_timestamp=timestamp.isoformat(),
                     first_5pct_observation_timestamp=timestamp.isoformat() if first_max_pct >= 5.0 else None)
        return {**details, "state": START_CONFIRMED, "active_event_state": ACTIVE_PUMP_EVENT,
                "reason": "PRICE_SHAPE_CONFIRMED", "event_id": event_id,
                **self._event_fields(state, current, timestamp)}

    @staticmethod
    def _insufficient(base: Mapping[str, Any], state: Mapping[str, Any], reason: str) -> dict[str, Any]:
        result = {**base, "state": INSUFFICIENT_EVIDENCE, "reason": reason, "data_quality_state": INSUFFICIENT_EVIDENCE}
        if state.get("active_event_id"):
            result.update({"active_event_state": ACTIVE_PUMP_EVENT, "event_id": state["active_event_id"],
                           "START_OF_PUMP": state.get("start_of_pump_timestamp"), "START_PRICE": state.get("start_price")})
        return result

    def _active_update(self, details: Mapping[str, Any], state: dict[str, Any], current: Mapping[str, Any], timestamp: datetime, reason: str) -> dict[str, Any]:
        previous_high = float(state.get("max_pump_price") or state["start_price"])
        if float(current["high"]) > previous_high:
            state["max_pump_price"] = float(current["high"])
            state["max_pump_pct"] = _pct(float(current["high"]), float(state["start_price"]))
            state["max_pump_timestamp"] = timestamp.isoformat()
        if state.get("first_5pct_observation_timestamp") is None and float(state["max_pump_pct"] or 0.0) >= 5.0:
            state["first_5pct_observation_timestamp"] = timestamp.isoformat()
        return {**details, "state": ACTIVE_PUMP_EVENT, "active_event_state": ACTIVE_PUMP_EVENT,
                "reason": reason, "event_id": state["active_event_id"], **self._event_fields(state, current, timestamp)}

    @staticmethod
    def _event_fields(state: Mapping[str, Any], current: Mapping[str, Any], timestamp: datetime) -> dict[str, Any]:
        start = float(state["start_price"])
        return {"START_OF_PUMP": state["start_of_pump_timestamp"], "START_PRICE": start,
                "CURRENT_PUMP_PCT": _pct(float(current["close"]), start),
                "MAX_PUMP_PCT": float(state["max_pump_pct"]), "MAX_PUMP_PRICE": float(state["max_pump_price"]),
                "MAX_PUMP_TIMESTAMP": state["max_pump_timestamp"],
                "FIRST_5PCT_OBSERVATION_TIMESTAMP": state.get("first_5pct_observation_timestamp"),
                "data_quality_state": "COMPLETE_NATIVE_EVIDENCE"}

    @staticmethod
    def _normalise_1m(bar: Mapping[str, Any]) -> dict[str, Any]:
        return {"timestamp": _bar_timestamp(bar).isoformat(), **{k: float(bar[k]) for k in ("open", "high", "low", "close")}}

    @staticmethod
    def _latest_native(rows: Sequence[Mapping[str, Any]], *, asof: datetime, timeframe_minutes: int) -> dict[str, Any] | None:
        valid = []
        for row in rows:
            if not _native_closed(row): continue
            ts = _bar_timestamp(row)
            if ts + timedelta(minutes=timeframe_minutes) <= asof:
                valid.append({"timestamp": ts, **{k: float(row[k]) for k in ("open", "high", "low", "close")}})
        return max(valid, key=lambda row: row["timestamp"]) if valid else None

    @staticmethod
    def _participation(records: Sequence[CoinalyzeOIRecord], native5: Mapping[str, Any], asof: datetime) -> tuple[str, CoinalyzeOIRecord | None]:
        for row in records:
            if (row.market_timestamp_utc == native5["timestamp"] and row.fetch_timestamp_utc <= asof
                    and row.closed_record_status == "CLOSED_NATIVE_5M" and row.timeframe == "5m"):
                return PARTICIPATION_CONTEXT_AVAILABLE, row
        return PARTICIPATION_INSUFFICIENT_EVIDENCE, None

    @staticmethod
    def _base(symbol: str, timestamp: datetime, asof: datetime) -> dict[str, Any]:
        return {"contract_version": CONTRACT_VERSION, "test_only": True, "symbol": symbol,
                "source_closed_1m_timestamp": timestamp.isoformat(), "decision_timestamp": asof.isoformat(),
                "price_provider": "bybit", "price_native_timeframes": ["1m", "3m", "5m"],
                "oi_provider": "coinalyze", "append_only": True, "production_authority": False,
                "trade_permission": False}
