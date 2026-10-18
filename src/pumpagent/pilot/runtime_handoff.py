"""TEST_ONLY Pilot runtime input assembly and guarded pre-admission handoff.

The module is intentionally provider-injectable.  It performs no scheduling,
Shadow execution, Case Manifest mutation, alerting, or trading side effect.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Callable, Mapping, Protocol, Sequence

from pumpagent.live_data.adapters.market_cap_rank import RankSnapshot
from pumpagent.pilot.pilot_search_profile import (
    CandidateEvaluation,
    CandidateInput,
    PilotAdmissionEvidenceJournal,
    PriceObservation,
    SelectionResult,
    select_candidate,
)

PROVIDER = "coinalyze"
VENUE = "BYBIT"
NATIVE_TIMEFRAMES = ("1m", "5m")
WINDOW = timedelta(minutes=20)
_REQUIRED_METADATA = ("market_type", "contract_type", "quote_asset", "price_unit")


class PilotRuntimeInputError(ValueError):
    """Raised when a required Pilot input cannot be proven point-in-time valid."""


@dataclass(frozen=True)
class NativeCandle:
    timestamp_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str
    closed: bool

    def __post_init__(self) -> None:
        if self.timeframe not in NATIVE_TIMEFRAMES:
            raise PilotRuntimeInputError("Pilot accepts only native Coinalyze 1m and 5m candles")
        if self.timestamp_utc.tzinfo is None or self.timestamp_utc.utcoffset() is None:
            raise PilotRuntimeInputError("candle timestamp must be timezone-aware")
        if not self.closed:
            raise PilotRuntimeInputError("open or incomplete candle cannot be used for Pilot input")
        for value in (self.open, self.high, self.low, self.close, self.volume):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise PilotRuntimeInputError("candle values must be non-negative numbers")
        if self.close <= 0:
            raise PilotRuntimeInputError("candle close must be positive")


@dataclass(frozen=True)
class NativeCandleBatch:
    bybit_symbol: str
    provider_symbol: str
    timeframe: str
    candles: tuple[NativeCandle, ...]
    raw_source_identity: str
    raw_sha256: str

    def __post_init__(self) -> None:
        if self.timeframe not in NATIVE_TIMEFRAMES:
            raise PilotRuntimeInputError("unsupported native timeframe")
        if not self.bybit_symbol.strip() or not self.provider_symbol.strip():
            raise PilotRuntimeInputError("Bybit and Coinalyze symbols are required")
        if not self.raw_source_identity.strip() or not _sha256(self.raw_sha256):
            raise PilotRuntimeInputError("raw source identity and SHA-256 digest are required")
        if not self.candles or any(item.timeframe != self.timeframe for item in self.candles):
            raise PilotRuntimeInputError("native batch is empty or has a mismatched timeframe")
        times = tuple(_utc(item.timestamp_utc) for item in self.candles)
        if times != tuple(sorted(times)) or len(set(times)) != len(times):
            raise PilotRuntimeInputError("native candles must be ordered and unique")


class CoinalyzePilotPriceSource(Protocol):
    """Provider boundary for native Coinalyze price evidence only."""

    def resolve_bybit_symbol(self, bybit_symbol: str) -> str: ...

    def fetch_closed_native(
        self, *, bybit_symbol: str, provider_symbol: str, timeframe: str,
        evaluation_timestamp_utc: datetime,
    ) -> NativeCandleBatch: ...


@dataclass(frozen=True)
class EligibleInstrument:
    symbol: str
    metadata: Mapping[str, str]


@dataclass(frozen=True)
class GuardedPreAdmissionHandoff:
    """Verified evidence handoff for a later, separately authorized admission."""

    selected: CandidateEvaluation
    journal_record_digest: str
    journal_record_sequence: int
    journal_record_name: str
    admission_state: str = "PRE_ADMISSION_NOT_STARTED"
    actual_pilot_admission_occurred: bool = False

    def __post_init__(self) -> None:
        if self.admission_state != "PRE_ADMISSION_NOT_STARTED" or self.actual_pilot_admission_occurred:
            raise PilotRuntimeInputError("guarded handoff cannot represent an admitted Pilot")
        if not _sha256(self.journal_record_digest):
            raise PilotRuntimeInputError("verified immutable journal digest is required")

    def to_dict(self) -> dict[str, Any]:
        candidate = self.selected.candidate
        return {
            "admission_state": self.admission_state,
            "actual_pilot_admission_occurred": self.actual_pilot_admission_occurred,
            "symbol": candidate.symbol,
            "venue": candidate.venue,
            "provider": candidate.provider,
            "provider_symbol_literal": candidate.provider_symbol_literal,
            "evaluation_timestamp_utc": _utc(candidate.evaluation_timestamp_utc).isoformat(),
            "native_timeframes": list(candidate.native_timeframes),
            "instrument_metadata": dict(sorted(candidate.instrument_metadata.items())),
            "rank_snapshot_id": candidate.rank_snapshot.snapshot_id,
            "rank_snapshot_source": candidate.rank_snapshot.source,
            "raw_evidence_ids": list(candidate.raw_evidence_ids),
            "raw_evidence_digests": list(candidate.raw_evidence_digests),
            "pilot_search_profile_evidence": self.selected.evidence_dict(),
            "journal_record_digest": self.journal_record_digest,
            "journal_record_sequence": self.journal_record_sequence,
            "journal_record_name": self.journal_record_name,
        }


class PilotRuntimeInputAssembler:
    """Assemble every eligible candidate, then emit only a verified handoff."""

    def __init__(
        self,
        source: CoinalyzePilotPriceSource,
        journal: PilotAdmissionEvidenceJournal,
        *,
        selector: Callable[[Sequence[CandidateInput]], SelectionResult] = select_candidate,
    ) -> None:
        self.source = source
        self.journal = journal
        self.selector = selector

    def prepare(
        self,
        *,
        rank_snapshot: RankSnapshot,
        instruments: Sequence[Mapping[str, Any]],
        tickers: Mapping[str, Mapping[str, Any]],
        evaluation_timestamp_utc: datetime,
    ) -> GuardedPreAdmissionHandoff:
        evaluation = _utc(evaluation_timestamp_utc)
        candidates = tuple(
            self._assemble_candidate(instrument, rank_snapshot, evaluation)
            for instrument in eligible_instruments(instruments, tickers, rank_snapshot)
        )
        if not candidates:
            raise PilotRuntimeInputError("no current non-Top-50 eligible instruments are available")
        selection = self.selector(candidates)
        if selection.winner is None:
            raise PilotRuntimeInputError("no candidate passed the accepted Pilot Search Profile")
        digest = self.journal.append(selection)
        records = self.journal.verify()
        matched = [record for record in records if record.get("record_digest") == digest]
        if len(matched) != 1:
            raise PilotRuntimeInputError("journal verification did not yield the appended evidence digest")
        record = matched[0]
        if record.get("selection", {}).get("winner", {}).get("admission_state") != "PRE_ADMISSION_NOT_STARTED":
            raise PilotRuntimeInputError("verified evidence does not preserve pre-admission state")
        return GuardedPreAdmissionHandoff(
            selected=selection.winner,
            journal_record_digest=digest,
            journal_record_sequence=int(record["sequence"]),
            journal_record_name=f"{int(record['sequence']):020d}.json",
        )

    def _assemble_candidate(
        self, instrument: EligibleInstrument, rank_snapshot: RankSnapshot, evaluation: datetime,
    ) -> CandidateInput:
        provider_symbol = self.source.resolve_bybit_symbol(instrument.symbol)
        if not isinstance(provider_symbol, str) or not provider_symbol.strip():
            raise PilotRuntimeInputError("Coinalyze provider symbol is unresolved")
        one = self.source.fetch_closed_native(
            bybit_symbol=instrument.symbol, provider_symbol=provider_symbol,
            timeframe="1m", evaluation_timestamp_utc=evaluation,
        )
        five = self.source.fetch_closed_native(
            bybit_symbol=instrument.symbol, provider_symbol=provider_symbol,
            timeframe="5m", evaluation_timestamp_utc=evaluation,
        )
        _validate_batches(instrument, provider_symbol, one, five, evaluation)
        observations = tuple(PriceObservation(row.timestamp_utc, row.close) for row in one.candles)
        return CandidateInput(
            symbol=instrument.symbol,
            provider_symbol_literal=provider_symbol,
            observations=observations,
            evaluation_timestamp_utc=evaluation,
            rank_snapshot=rank_snapshot,
            instrument_metadata=dict(instrument.metadata),
            raw_evidence_ids=(one.raw_source_identity, five.raw_source_identity),
            raw_evidence_digests=(one.raw_sha256, five.raw_sha256),
            provider=PROVIDER,
            venue=VENUE,
            native_timeframes=NATIVE_TIMEFRAMES,
        )


def eligible_instruments(
    instruments: Sequence[Mapping[str, Any]], tickers: Mapping[str, Mapping[str, Any]],
    rank_snapshot: RankSnapshot,
) -> tuple[EligibleInstrument, ...]:
    """Apply the existing CoinGecko/Bybit non-Top-50 eligibility semantics."""
    values: dict[str, EligibleInstrument] = {}
    for row in instruments:
        symbol = str(row.get("symbol", "")).upper().strip()
        if not symbol.endswith("USDT") or row.get("status") != "Trading" or symbol not in tickers:
            continue
        rank, _ = rank_snapshot.resolve(symbol[:-4])
        if rank is None or rank.rank <= 50:
            continue
        metadata = {
            "market_type": str(row.get("category", "linear")),
            "contract_type": str(row.get("contractType", "perpetual")).lower(),
            "quote_asset": str(row.get("quoteCoin", "USDT")).upper(),
            "price_unit": "USDT",
        }
        if metadata["market_type"] != "linear" or metadata["quote_asset"] != "USDT":
            continue
        values[symbol] = EligibleInstrument(symbol, metadata)
    return tuple(values[symbol] for symbol in sorted(values))


def _validate_batches(
    instrument: EligibleInstrument, provider_symbol: str, one: NativeCandleBatch,
    five: NativeCandleBatch, evaluation: datetime,
) -> None:
    if set(instrument.metadata) < set(_REQUIRED_METADATA):
        raise PilotRuntimeInputError("instrument/unit metadata is incomplete")
    for batch, timeframe in ((one, "1m"), (five, "5m")):
        if batch.bybit_symbol.upper() != instrument.symbol or batch.provider_symbol != provider_symbol:
            raise PilotRuntimeInputError("provider-symbol identity mismatch")
        if batch.timeframe != timeframe or any(not candle.closed for candle in batch.candles):
            raise PilotRuntimeInputError("required native evidence is incomplete or unsupported")
        if any(_utc(candle.timestamp_utc) > evaluation for candle in batch.candles):
            raise PilotRuntimeInputError("future evidence cannot enter Pilot input")
    if len(one.candles) < 20:
        raise PilotRuntimeInputError("insufficient native closed 1m evidence for the rolling 20-minute profile")
    if _utc(one.candles[0].timestamp_utc) > evaluation - WINDOW:
        raise PilotRuntimeInputError("native 1m evidence does not cover the rolling 20-minute window")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PilotRuntimeInputError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sha256(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
