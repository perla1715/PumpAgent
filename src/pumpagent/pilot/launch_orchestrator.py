"""Injected, Shadow-only first-Pilot launch orchestration.

This owner composes accepted Pilot boundaries. It performs no trading and does
not mutate the Case Manifest. Real providers are supplied by the caller; tests
use the same injection points with offline fakes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from pumpagent.live_data.adapters.bybit_public_market import fetch_instruments, fetch_tickers
from pumpagent.live_data.adapters.coinalyze_pilot_price import CoinalyzeNativePilotPriceClient

from pumpagent.live_data.adapters.market_cap_rank import RankSnapshot
from pumpagent.pilot.pilot_search_profile import PilotAdmissionEvidenceJournal
from pumpagent.pilot.cohort_rate_budget import CoinalyzeCohortRateBudget
from pumpagent.pilot.runtime_handoff import (
    CoinalyzePilotPriceSource,
    GuardedPreAdmissionHandoff,
    NativeCandleBatch,
    PilotDiscoveryPreparation,
    PilotRuntimeInputAssembler,
    PilotRuntimeInputError,
)
from pumpagent.pilot.shadow_admission import (
    GuardedShadowAdmission,
    ShadowPilotAdmissionError,
    admit_guarded_handoff,
    process_admitted_shadow_cycle,
)
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus
from pumpagent.runtime.modules.observation_lifecycle.runtime_cycle import ObservationRuntimeCycleResult
from pumpagent.runtime.modules.watchlist.manager import WatchlistManager
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator


class LaunchStatus(str, Enum):
    DISCOVERY_FAILED = "DISCOVERY_FAILED"
    NO_QUALIFYING_CANDIDATE = "NO_QUALIFYING_CANDIDATE"
    EVIDENCE_ASSEMBLY_FAILED = "EVIDENCE_ASSEMBLY_FAILED"
    PRE_ADMISSION_VERIFICATION_FAILED = "PRE_ADMISSION_VERIFICATION_FAILED"
    ADMISSION_FAILED = "ADMISSION_FAILED"
    ADMITTED_WAITING_FOR_FRESH_SNAPSHOT = "ADMITTED_WAITING_FOR_FRESH_SNAPSHOT"
    RUNTIME_CYCLE_FAILED = "RUNTIME_CYCLE_FAILED"
    RUNTIME_CYCLE_COMPLETED = "RUNTIME_CYCLE_COMPLETED"


class RankSnapshotSource(Protocol):
    def fetch(self) -> RankSnapshot: ...


@dataclass(frozen=True)
class FirstShadowPilotLaunchResult:
    status: LaunchStatus
    reason: str
    handoff: GuardedPreAdmissionHandoff | None = None
    admission: GuardedShadowAdmission | None = None
    runtime_cycle: ObservationRuntimeCycleResult | None = None
    manifest_transition_required: bool = False
    manifest_transition_action: str | None = None
    discovery_preparation: PilotDiscoveryPreparation | None = None

    def __post_init__(self) -> None:
        if self.status is LaunchStatus.ADMITTED_WAITING_FOR_FRESH_SNAPSHOT and self.admission is None:
            raise ValueError("admitted waiting state requires GuardedShadowAdmission")
        if self.runtime_cycle is not None and self.admission is None:
            raise ValueError("runtime result requires an admitted event")


def build_first_shadow_pilot_launch_orchestrator(
    *,
    journal_directory: str | Path,
    coinalyze_api_key: str | None = None,
    watchlist: WatchlistManager | None = None,
    runtime: RuntimeOrchestrator | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> "FirstShadowPilotLaunchOrchestrator":
    """Compose existing public/read-only adapters without performing a request.

    CoinalyzeNativePilotPriceClient owns environment-key fallback. The API key
    is passed only to that client and is never serialized into results, journal,
    receipts, or MarketSnapshot metadata.
    """
    from pumpagent.live_data.adapters.market_cap_rank import CoinGeckoMarketCapRankSource

    return FirstShadowPilotLaunchOrchestrator(
        rank_source=CoinGeckoMarketCapRankSource(),
        instruments_loader=fetch_instruments,
        tickers_loader=fetch_tickers,
        price_source=CoinalyzeNativePilotPriceClient(api_key=coinalyze_api_key),
        journal=PilotAdmissionEvidenceJournal(journal_directory),
        watchlist=watchlist,
        runtime=runtime,
        clock=clock,
        rate_budget_factory=CoinalyzeCohortRateBudget,
    )


class FirstShadowPilotLaunchOrchestrator:
    """Compose one automatic Pilot discovery/admission and later one cycle."""

    def __init__(
        self,
        *,
        rank_source: RankSnapshotSource,
        instruments_loader: Callable[[], Mapping[str, Any] | Sequence[Mapping[str, Any]]],
        tickers_loader: Callable[[], Mapping[str, Any] | Sequence[Mapping[str, Any]]],
        price_source: CoinalyzePilotPriceSource,
        journal: PilotAdmissionEvidenceJournal,
        watchlist: WatchlistManager | None = None,
        runtime: RuntimeOrchestrator | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        rate_budget_factory: Callable[[], CoinalyzeCohortRateBudget] | None = None,
    ) -> None:
        self.rank_source = rank_source
        self.instruments_loader = instruments_loader
        self.tickers_loader = tickers_loader
        self.price_source = price_source
        self.journal = journal
        self.watchlist = watchlist or WatchlistManager()
        self.runtime = runtime or RuntimeOrchestrator()
        self.clock = clock
        self.rate_budget_factory = rate_budget_factory or (
            CoinalyzeCohortRateBudget if callable(getattr(price_source, "begin_discovery_cycle", None)) else None
        )
        self._admitted_journal_digests: set[str] = set()

    def discover_and_admit(
        self, *, evaluation_timestamp_utc: datetime | None = None
    ) -> FirstShadowPilotLaunchResult:
        """Run Phase A only; no runtime snapshot is fabricated or reused."""
        requested_evaluation = _utc(evaluation_timestamp_utc or self.clock())
        try:
            rank_snapshot = self.rank_source.fetch()
            instruments = _instrument_rows(self.instruments_loader())
            tickers = _ticker_map(self.tickers_loader())
        except Exception as exc:
            return FirstShadowPilotLaunchResult(LaunchStatus.DISCOVERY_FAILED, _reason(exc))
        # The final boundary is frozen only after the rank source has supplied
        # its own point-in-time timestamp.  A rank snapshot genuinely later
        # than the post-fetch clock remains invalid rather than being backdated.
        post_fetch_clock = _utc(self.clock())
        rank_fetched = _utc(rank_snapshot.fetched_at)
        if requested_evaluation > post_fetch_clock:
            return FirstShadowPilotLaunchResult(
                LaunchStatus.DISCOVERY_FAILED,
                "requested evaluation timestamp is later than the discovery evaluation boundary",
            )
        if rank_fetched > post_fetch_clock:
            return FirstShadowPilotLaunchResult(
                LaunchStatus.DISCOVERY_FAILED,
                "rank snapshot timestamp is later than the discovery evaluation boundary",
            )
        evaluation = max(requested_evaluation, post_fetch_clock, rank_fetched)
        try:
            assembler = PilotRuntimeInputAssembler(
                self.price_source, self.journal,
                rate_budget=self.rate_budget_factory() if self.rate_budget_factory else None,
            )
            preparation = assembler.prepare_discovery(
                rank_snapshot=rank_snapshot,
                instruments=instruments,
                tickers=tickers,
                evaluation_timestamp_utc=evaluation,
            )
            if preparation.eligible_count == 0:
                return FirstShadowPilotLaunchResult(
                    LaunchStatus.EVIDENCE_ASSEMBLY_FAILED,
                    "no current non-Top-50 eligible instruments are available",
                    discovery_preparation=preparation,
                )
            if preparation.selection.winner is None:
                return FirstShadowPilotLaunchResult(
                    LaunchStatus.NO_QUALIFYING_CANDIDATE,
                    "no candidate passed the accepted Pilot Search Profile",
                    discovery_preparation=preparation,
                )
            handoff = assembler.handoff_for_selection(preparation.selection)
        except PilotRuntimeInputError as exc:
            return FirstShadowPilotLaunchResult(LaunchStatus.EVIDENCE_ASSEMBLY_FAILED, str(exc))
        except Exception as exc:
            return FirstShadowPilotLaunchResult(LaunchStatus.EVIDENCE_ASSEMBLY_FAILED, _reason(exc))
        return self.admit_verified_handoff(handoff, admission_timestamp_utc=evaluation, discovery_preparation=preparation)

    def admit_verified_handoff(
        self, handoff: GuardedPreAdmissionHandoff, *, admission_timestamp_utc: datetime | None = None,
        discovery_preparation: PilotDiscoveryPreparation | None = None,
    ) -> FirstShadowPilotLaunchResult:
        """Consume one verified handoff exactly once through canonical admission."""
        if handoff.journal_record_digest in self._admitted_journal_digests:
            return FirstShadowPilotLaunchResult(LaunchStatus.ADMISSION_FAILED, "verified handoff was already admitted", handoff=handoff, discovery_preparation=discovery_preparation)
        try:
            admission = admit_guarded_handoff(
                handoff, self.journal, self.watchlist,
                admission_timestamp_utc=admission_timestamp_utc or self.clock(),
            )
        except ShadowPilotAdmissionError as exc:
            status = (LaunchStatus.PRE_ADMISSION_VERIFICATION_FAILED if "journal" in str(exc).lower() or "provenance" in str(exc).lower()
                      else LaunchStatus.ADMISSION_FAILED)
            return FirstShadowPilotLaunchResult(status, str(exc), handoff=handoff, discovery_preparation=discovery_preparation)
        self._admitted_journal_digests.add(handoff.journal_record_digest)
        return FirstShadowPilotLaunchResult(
            LaunchStatus.ADMITTED_WAITING_FOR_FRESH_SNAPSHOT,
            "Shadow admission completed; a fresh later closed 5m snapshot is required.",
            handoff=handoff,
            admission=admission,
            manifest_transition_required=True,
            manifest_transition_action=(
                "At real launch, append a provenance-preserving superseding Case Manifest record; "
                "do not overwrite PILOT_EVENT_001 historical fields."
            ),
            discovery_preparation=discovery_preparation,
        )

    def continue_once(
        self, admission: GuardedShadowAdmission, *, evaluation_timestamp_utc: datetime | None = None
    ) -> FirstShadowPilotLaunchResult:
        """Run Phase B with independently fetched closed native 5m evidence."""
        evaluation = _utc(evaluation_timestamp_utc or self.clock())
        receipt = admission.receipt
        try:
            batch = self.price_source.fetch_closed_native(
                bybit_symbol=receipt.symbol,
                provider_symbol=receipt.provider_symbol_literal,
                timeframe="5m",
                evaluation_timestamp_utc=evaluation,
            )
            snapshot = build_fresh_coinalyze_5m_snapshot(admission, batch, evaluation)
            cycle = process_admitted_shadow_cycle(admission, snapshot, self.watchlist, self.runtime)
        except Exception as exc:
            return FirstShadowPilotLaunchResult(LaunchStatus.RUNTIME_CYCLE_FAILED, _reason(exc), admission=admission)
        status = LaunchStatus.RUNTIME_CYCLE_COMPLETED if cycle.status.value == "COMPLETED" else LaunchStatus.RUNTIME_CYCLE_FAILED
        return FirstShadowPilotLaunchResult(status, cycle.orchestration_reason, admission=admission, runtime_cycle=cycle)


def build_fresh_coinalyze_5m_snapshot(
    admission: GuardedShadowAdmission,
    batch: NativeCandleBatch,
    evaluation_timestamp_utc: datetime,
) -> MarketSnapshot:
    """Convert independently fetched native closed 5m evidence into Runtime input."""
    if not isinstance(admission, GuardedShadowAdmission) or not isinstance(batch, NativeCandleBatch):
        raise PilotRuntimeInputError("admission and native candle batch are required")
    receipt = admission.receipt
    evaluation = _utc(evaluation_timestamp_utc)
    if (batch.bybit_symbol.upper(), batch.provider_symbol, batch.timeframe) != (receipt.symbol.upper(), receipt.provider_symbol_literal, "5m"):
        raise PilotRuntimeInputError("fresh 5m evidence identity does not match admitted Pilot")
    if not batch.candles or any(not candle.closed or candle.timeframe != "5m" for candle in batch.candles):
        raise PilotRuntimeInputError("fresh runtime evidence must be native closed 5m candles")
    if any(_utc(candle.timestamp_utc) > evaluation for candle in batch.candles):
        raise PilotRuntimeInputError("fresh runtime evidence contains a future candle")
    latest = batch.candles[-1]
    if _utc(latest.timestamp_utc) < receipt.admission_timestamp_utc:
        raise PilotRuntimeInputError("fresh runtime evidence does not contain a candle after admission")
    rows = tuple({
        "timestamp": _utc(candle.timestamp_utc), "open": candle.open, "high": candle.high,
        "low": candle.low, "close": candle.close, "volume": candle.volume, "is_closed": True,
    } for candle in batch.candles)
    event_id = "pilot-shadow:" + hashlib.sha256(
        f"{receipt.episode_id}|{_utc(latest.timestamp_utc).isoformat()}|{batch.raw_sha256}".encode("utf-8")
    ).hexdigest()
    return MarketSnapshot(
        event_id=event_id,
        timestamp=_utc(latest.timestamp_utc),
        symbol=receipt.symbol.upper(),
        exchange="bybit",
        timeframe="5m",
        price=float(latest.close),
        ohlcv=rows,
        volume=float(latest.volume),
        data_source="coinalyze",
        data_quality_status=DataQualityStatus.VALID,
        raw_payload_reference=batch.raw_source_identity,
        optional_market_metrics={"raw_evidence_sha256": batch.raw_sha256, "pilot_journal_record_digest": receipt.journal_record_digest},
    )


def _instrument_rows(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        values = payload.get("result", {}).get("list") if isinstance(payload.get("result"), Mapping) else payload.get("list")
    else:
        values = payload
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise PilotRuntimeInputError("Bybit instruments response has no list")
    return tuple(value for value in values if isinstance(value, Mapping))


def _ticker_map(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> Mapping[str, Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        values = payload.get("result", {}).get("list") if isinstance(payload.get("result"), Mapping) else payload.get("list")
        if values is None and all(isinstance(key, str) and isinstance(value, Mapping) for key, value in payload.items()):
            return {key.upper(): value for key, value in payload.items()}
    else:
        values = payload
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise PilotRuntimeInputError("Bybit tickers response has no list")
    return {str(value.get("symbol", "")).upper(): value for value in values if isinstance(value, Mapping) and value.get("symbol")}


def _reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PilotRuntimeInputError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
