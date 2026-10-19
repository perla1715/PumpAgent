"""Offline, TEST_ONLY Pilot Search Profile v1 and immutable pre-admission evidence.

This module has no provider client, scheduler, live-market loop, or admission side
effect. It evaluates supplied closed price observations and writes only a
pre-admission evidence record. PILOT_EVENT_001 remains outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from pumpagent.live_data.adapters.market_cap_rank import RankSnapshot

PROFILE_ID = "pilot_search_profile_v1"
PROVIDER = "coinalyze"
VENUE = "BYBIT"
NATIVE_TIMEFRAMES = ("1m", "5m")
PUMP_MIN_PERCENT = 5.0
WINDOW = timedelta(minutes=20)
MAX_PULLBACK_PERCENT = 5.0
_REQUIRED_METADATA = frozenset({"market_type", "contract_type", "quote_asset", "price_unit"})


class PilotEvidenceError(ValueError):
    """Raised for an invalid or non-reproducible offline pre-admission input."""


@dataclass(frozen=True)
class PriceObservation:
    timestamp_utc: datetime
    price: float

    def __post_init__(self) -> None:
        if self.timestamp_utc.tzinfo is None or self.timestamp_utc.utcoffset() is None:
            raise PilotEvidenceError("price observation timestamp must be timezone-aware")
        if not _finite_positive(self.price):
            raise PilotEvidenceError("price must be a positive finite number")

    def to_dict(self) -> dict[str, Any]:
        return {"timestamp_utc": _utc(self.timestamp_utc).isoformat(), "price": float(self.price)}


@dataclass(frozen=True)
class CandidateInput:
    symbol: str
    provider_symbol_literal: str
    observations: tuple[PriceObservation, ...]
    evaluation_timestamp_utc: datetime
    rank_snapshot: RankSnapshot
    instrument_metadata: Mapping[str, str]
    raw_evidence_ids: tuple[str, ...]
    raw_evidence_digests: tuple[str, ...]
    provider: str = PROVIDER
    venue: str = VENUE
    native_timeframes: tuple[str, ...] = NATIVE_TIMEFRAMES

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.provider_symbol_literal.strip():
            raise PilotEvidenceError("symbol and provider symbol are required")
        if self.provider.lower() != PROVIDER or self.venue != VENUE:
            raise PilotEvidenceError("Pilot provider/venue contract is Coinalyze/Bybit only")
        if tuple(self.native_timeframes) != NATIVE_TIMEFRAMES:
            raise PilotEvidenceError("Pilot requires exactly native Coinalyze 1m and 5m")
        if self.evaluation_timestamp_utc.tzinfo is None or self.evaluation_timestamp_utc.utcoffset() is None:
            raise PilotEvidenceError("evaluation timestamp must be timezone-aware")
        if not _REQUIRED_METADATA <= set(self.instrument_metadata):
            raise PilotEvidenceError("instrument metadata is incomplete")
        if not self.raw_evidence_ids or len(self.raw_evidence_ids) != len(self.raw_evidence_digests):
            raise PilotEvidenceError("source evidence identities and digests are required")
        if any(not _sha256(value) for value in self.raw_evidence_digests):
            raise PilotEvidenceError("source evidence digest must be SHA-256")
        if len(self.observations) < 2:
            raise PilotEvidenceError("at least two closed observations are required")
        times = [_utc(row.timestamp_utc) for row in self.observations]
        if times != sorted(times) or len(set(times)) != len(times):
            raise PilotEvidenceError("observations must be unique and ordered")
        if times[-1] > _utc(self.evaluation_timestamp_utc):
            raise PilotEvidenceError("future observation is not allowed")


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate: CandidateInput
    top50_status: str
    rank: int | None
    p1_pass: bool
    p2_pass: bool
    p4_pass: bool
    p5_pass: bool
    start_timestamp_utc: datetime | None
    start_price: float | None
    current_price: float | None
    local_high: float | None
    local_high_timestamp_utc: datetime | None
    pump_percent: float | None
    distance_from_high_percent: float | None
    max_running_high_pullback_percent: float | None
    reason: str

    @property
    def qualifies(self) -> bool:
        return self.top50_status == "NON_TOP50_ELIGIBLE" and self.p1_pass and self.p2_pass and self.p4_pass and self.p5_pass

    def evidence_dict(self) -> dict[str, Any]:
        c = self.candidate
        return {
            "schema_version": "pilot-pre-admission-evidence/v1",
            "profile_id": PROFILE_ID,
            "admission_state": "PRE_ADMISSION_NOT_STARTED",
            "case_event_identity": "PILOT_EVENT_001",
            "symbol": c.symbol.upper(),
            "provider_symbol_literal": c.provider_symbol_literal,
            "venue": c.venue,
            "provider": c.provider,
            "native_timeframes": list(c.native_timeframes),
            "evaluation_timestamp_utc": _utc(c.evaluation_timestamp_utc).isoformat(),
            "qualifying_window_start_utc": _time(self.start_timestamp_utc),
            "qualifying_window_end_utc": _time(c.evaluation_timestamp_utc),
            "start_price": self.start_price,
            "current_price": self.current_price,
            "qualifying_local_high": self.local_high,
            "qualifying_local_high_timestamp_utc": _time(self.local_high_timestamp_utc),
            "pump_percent": self.pump_percent,
            "distance_from_high_percent": self.distance_from_high_percent,
            "max_running_high_pullback_percent": self.max_running_high_pullback_percent,
            "p1_pass": self.p1_pass,
            "p2_pass": self.p2_pass,
            "p4_pass": self.p4_pass,
            "p5_pass": self.p5_pass,
            "top50_status": self.top50_status,
            "market_cap_rank": self.rank,
            "rank_snapshot_id": c.rank_snapshot.snapshot_id,
            "rank_snapshot_source": c.rank_snapshot.source,
            "rank_snapshot_fetched_at": _utc(c.rank_snapshot.fetched_at).isoformat(),
            "instrument_metadata": dict(sorted(c.instrument_metadata.items())),
            "raw_evidence_ids": list(c.raw_evidence_ids),
            "raw_evidence_digests": list(c.raw_evidence_digests),
            "source_observations": [row.to_dict() for row in c.observations],
            "qualifies": self.qualifies,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SelectionResult:
    evaluations: tuple[CandidateEvaluation, ...]
    winner: CandidateEvaluation | None

    def evidence_dict(self) -> dict[str, Any]:
        winner = self.winner
        return {
            "selection_schema_version": "pilot-pre-admission-selection/v1",
            "candidate_ranking": [
                {
                    "symbol": e.candidate.symbol.upper(),
                    "qualifies": e.qualifies,
                    "pump_percent": e.pump_percent,
                    "top50_status": e.top50_status,
                    "reason": e.reason,
                }
                for e in self.evaluations
            ],
            "winner": winner.evidence_dict() if winner else None,
            "winner_outranks_reason": (
                "Largest qualifying 20-minute pump percentage; ties sort by symbol."
                if winner else "No candidate passed all Pilot Search Profile and eligibility gates."
            ),
        }


@dataclass(frozen=True)
class CanonicalProfileEvaluation:
    """P1/P2/P4/P5 output from one ordered closed 1m evidence sequence."""

    top50_status: str
    rank: int | None
    p1_pass: bool
    p2_pass: bool
    p4_pass: bool
    p5_pass: bool
    start_timestamp_utc: datetime | None
    start_price: float | None
    current_price: float | None
    local_high: float | None
    local_high_timestamp_utc: datetime | None
    pump_percent: float | None
    distance_from_high_percent: float | None
    max_running_high_pullback_percent: float | None
    reason: str

    @property
    def qualifies(self) -> bool:
        return self.top50_status == "NON_TOP50_ELIGIBLE" and self.p1_pass and self.p2_pass and self.p4_pass and self.p5_pass


def evaluate_canonical_profile(
    *, symbol: str, observations: Sequence[PriceObservation], evaluation_timestamp_utc: datetime,
    rank_snapshot: RankSnapshot, rank_max_age: timedelta = timedelta(hours=24),
) -> CanonicalProfileEvaluation:
    """The single canonical owner of Pilot P1/P2/P4/P5 semantics."""
    evaluation = _utc(evaluation_timestamp_utc)
    entry, _ = rank_snapshot.resolve(_underlying(symbol))
    snapshot_time = _utc(rank_snapshot.fetched_at)
    rank_fresh = timedelta(0) <= evaluation - snapshot_time <= rank_max_age
    if not rank_fresh or entry is None:
        return CanonicalProfileEvaluation("TOP50_STATUS_UNRESOLVED", None, False, False, False, False, None, None, None, None, None, None, None, None, "Required market-cap rank is unresolved or stale.")
    if entry.rank <= 50:
        return CanonicalProfileEvaluation("EXCLUDED_TOP50", entry.rank, False, False, False, False, None, None, None, None, None, None, None, None, "Top-50 candidate is excluded.")
    window_start = evaluation - WINDOW
    rows = tuple(row for row in observations if window_start <= _utc(row.timestamp_utc) <= evaluation)
    if len(rows) < 2:
        return CanonicalProfileEvaluation("NON_TOP50_ELIGIBLE", entry.rank, False, False, False, False, None, None, None, None, None, None, None, None, "Insufficient closed observations in rolling 20-minute window.")
    start_index, high_index, pump = _largest_upward_pair(rows)
    start = rows[start_index]
    high = rows[high_index]
    current = rows[-1]
    distance = (high.price - current.price) / high.price * 100.0
    running_high = -math.inf
    maximum_pullback = 0.0
    for row in rows:
        running_high = max(running_high, row.price)
        maximum_pullback = max(maximum_pullback, (running_high - row.price) / running_high * 100.0)
    p1 = pump >= PUMP_MIN_PERCENT
    p2 = _utc(high.timestamp_utc) - _utc(start.timestamp_utc) <= WINDOW
    p4 = distance <= MAX_PULLBACK_PERCENT
    p5 = maximum_pullback <= MAX_PULLBACK_PERCENT
    reason = "Qualified Pilot Search Profile v1." if all((p1, p2, p4, p5)) else "Pilot Search Profile gate did not pass."
    return CanonicalProfileEvaluation("NON_TOP50_ELIGIBLE", entry.rank, p1, p2, p4, p5, _utc(start.timestamp_utc), start.price, current.price, high.price, _utc(high.timestamp_utc), pump, distance, maximum_pullback, reason)


def evaluate_candidate(candidate: CandidateInput, *, rank_max_age: timedelta = timedelta(hours=24)) -> CandidateEvaluation:
    profile = evaluate_canonical_profile(
        symbol=candidate.symbol, observations=candidate.observations,
        evaluation_timestamp_utc=candidate.evaluation_timestamp_utc,
        rank_snapshot=candidate.rank_snapshot, rank_max_age=rank_max_age,
    )
    return CandidateEvaluation(
        candidate, profile.top50_status, profile.rank, profile.p1_pass, profile.p2_pass,
        profile.p4_pass, profile.p5_pass, profile.start_timestamp_utc, profile.start_price,
        profile.current_price, profile.local_high, profile.local_high_timestamp_utc,
        profile.pump_percent, profile.distance_from_high_percent,
        profile.max_running_high_pullback_percent, profile.reason,
    )


def select_candidate(candidates: Sequence[CandidateInput]) -> SelectionResult:
    evaluations = tuple(evaluate_candidate(candidate) for candidate in candidates)
    qualified = [item for item in evaluations if item.qualifies]
    winner = sorted(qualified, key=lambda item: (-float(item.pump_percent or 0), item.candidate.symbol.upper()))[0] if qualified else None
    return SelectionResult(evaluations, winner)


class PilotAdmissionEvidenceJournal:
    """Append-only pre-admission records with a verified SHA-256 chain.

    The journal reuses Task 2's canonical-JSON and digest discipline for a
    different record type. It never writes the Case Manifest or starts a Pilot.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def append(self, selection: SelectionResult) -> str:
        if selection.winner is None:
            raise PilotEvidenceError("Only a selected qualifying candidate can be serialized")
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._lock() as fd:
            records, previous = self._records(fd)
            payload = selection.evidence_dict()
            canonical = _canonical(payload)
            digest = hashlib.sha256(canonical).hexdigest()
            record = {"sequence": len(records) + 1, "previous_record_digest": previous,
                      "record_digest": digest, "selection": payload}
            content = _canonical(record)
            name = f"{len(records)+1:020d}.json"
            target = self.directory / name
            try:
                out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as exc:
                raise PilotEvidenceError("journal destination collision") from exc
            try:
                os.write(out, content); os.fsync(out)
            finally:
                os.close(out)
            os.fsync(fd)
            return digest

    def verify(self) -> tuple[dict[str, Any], ...]:
        if not self.directory.is_dir():
            raise PilotEvidenceError("journal directory does not exist")
        with self._lock() as fd:
            records, _ = self._records(fd)
            return tuple(records)

    def _records(self, fd: int) -> tuple[list[dict[str, Any]], str | None]:
        names = sorted(os.listdir(fd))
        expected = [f"{index:020d}.json" for index in range(1, len(names) + 1)]
        if names != expected:
            raise PilotEvidenceError("journal sequence is invalid")
        previous = None
        records: list[dict[str, Any]] = []
        for sequence, name in enumerate(names, 1):
            with open(self.directory / name, "rb") as stream:
                record = json.loads(stream.read().decode("utf-8"))
            if record.get("sequence") != sequence or record.get("previous_record_digest") != previous:
                raise PilotEvidenceError("journal chain is invalid")
            payload = record.get("selection")
            digest = record.get("record_digest")
            if not isinstance(payload, dict) or not isinstance(digest, str) or hashlib.sha256(_canonical(payload)).hexdigest() != digest:
                raise PilotEvidenceError("journal record digest is invalid")
            if payload.get("winner", {}).get("admission_state") != "PRE_ADMISSION_NOT_STARTED":
                raise PilotEvidenceError("journal cannot record a Pilot admission")
            previous = hashlib.sha256(_canonical(record)).hexdigest()
            records.append(record)
        return records, previous

    def _lock(self):
        class Lock:
            def __init__(self, directory: Path): self.directory = directory; self.fd: int | None = None
            def __enter__(self):
                self.fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                fcntl.flock(self.fd, fcntl.LOCK_EX)
                return self.fd
            def __exit__(self, *_):
                assert self.fd is not None
                os.close(self.fd)
        return Lock(self.directory)


def validate_case_manifest_boundary(path: str | Path) -> dict[str, Any]:
    """Read-only guard confirming historical rows and untouched Pilot state."""
    with Path(path).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    historical = rows[:5]
    pilot = next((row for row in rows if row.get("case_id") == "PILOT_EVENT_001"), None)
    if [row.get("row_id") for row in historical] != ["1", "2", "3", "4", "5"]:
        raise PilotEvidenceError("historical Case Manifest rows are not preserved")
    if pilot is None or pilot.get("role") != "LIVE_PILOT" or pilot.get("status") != "NOT_STARTED":
        raise PilotEvidenceError("PILOT_EVENT_001 must remain NOT_STARTED")
    return {"historical_rows_unchanged": True, "pilot_event_not_started": True}


def _largest_upward_pair(rows: Sequence[PriceObservation]) -> tuple[int, int, float]:
    """Return the largest causal start→high percentage pair in chronological rows.

    Ties retain the first chronological pair encountered: earliest start, then
    earliest high. A later low is consequently never allowed to start an earlier
    high, and no observation after evaluation reaches this function.
    """
    best_start = best_high = 0
    best_pump = 0.0
    for start_index, start in enumerate(rows):
        for high_index in range(start_index, len(rows)):
            high = rows[high_index]
            pump = (high.price - start.price) / start.price * 100.0
            if pump > best_pump:
                best_start, best_high, best_pump = start_index, high_index, pump
    return best_start, best_high, best_pump


def _failed(candidate: CandidateInput, status: str, rank: int | None, reason: str) -> CandidateEvaluation:
    return CandidateEvaluation(candidate, status, rank, False, False, False, False,
                               None, None, None, None, None, None, None, None, reason)


def _underlying(symbol: str) -> str:
    value = symbol.upper()
    return value[:-4] if value.endswith("USDT") else value


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _time(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value else None


def _finite_positive(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
