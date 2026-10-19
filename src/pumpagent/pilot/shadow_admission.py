"""Guarded, Shadow-only Pilot admission and one-cycle continuation boundary.

This module consumes an already verified pre-admission handoff. It never
selects a candidate, queries a provider, mutates the Case Manifest, or executes
trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from pumpagent.pilot.pilot_search_profile import PilotAdmissionEvidenceJournal
from pumpagent.pilot.runtime_handoff import GuardedPreAdmissionHandoff
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus, ObservationLifecycleDecision
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.domain.observation_policy import ObservationRequest
from pumpagent.runtime.modules.observation_lifecycle.executor import (
    ObservationLifecycleExecutionInput,
    execute_observation_lifecycle,
)
from pumpagent.runtime.modules.observation_lifecycle.runtime_cycle import (
    ObservationRuntimeCycleInput,
    ObservationRuntimeCycleResult,
    process_observation_runtime_cycle,
)
from pumpagent.runtime.modules.watchlist.manager import WatchlistEntry, WatchlistManager
from pumpagent.runtime.modules.watchlist.observation_boundary import (
    ObservationBoundaryInput,
    build_watchlist_observation_context,
    evaluate_observation_boundary,
)
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator


class ShadowPilotAdmissionError(ValueError):
    """Raised before a Pilot handoff can become a Shadow admission."""


@dataclass(frozen=True)
class ShadowPilotAdmissionReceipt:
    """Immutable reference binding verified Pilot evidence to one Episode."""

    symbol: str
    venue: str
    provider: str
    provider_symbol_literal: str
    evaluation_timestamp_utc: datetime
    admission_timestamp_utc: datetime
    journal_record_digest: str
    journal_record_sequence: int
    journal_record_name: str
    rank_snapshot_id: str
    rank_snapshot_source: str
    native_timeframes: tuple[str, ...]
    raw_evidence_ids: tuple[str, ...]
    raw_evidence_digests: tuple[str, ...]
    episode_id: str
    shadow_only: bool = True
    automated_order_execution: bool = False
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for name in ("symbol", "venue", "provider", "provider_symbol_literal", "journal_record_digest", "journal_record_name", "rank_snapshot_id", "rank_snapshot_source", "episode_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ShadowPilotAdmissionError(f"{name} is required")
        for name in ("evaluation_timestamp_utc", "admission_timestamp_utc"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ShadowPilotAdmissionError(f"{name} must be timezone-aware")
        if self.admission_timestamp_utc < self.evaluation_timestamp_utc:
            raise ShadowPilotAdmissionError("admission cannot precede evaluation")
        if not _sha256(self.journal_record_digest):
            raise ShadowPilotAdmissionError("journal record digest must be SHA-256")
        if self.journal_record_sequence < 1:
            raise ShadowPilotAdmissionError("journal record sequence must be positive")
        if self.native_timeframes != ("1m", "5m"):
            raise ShadowPilotAdmissionError("receipt must retain native 1m/5m provenance")
        if not self.raw_evidence_ids or len(self.raw_evidence_ids) != len(self.raw_evidence_digests):
            raise ShadowPilotAdmissionError("receipt must retain raw evidence identities and digests")
        if any(not _sha256(value) for value in self.raw_evidence_digests):
            raise ShadowPilotAdmissionError("receipt raw evidence digests must be SHA-256")
        if not self.shadow_only or self.automated_order_execution:
            raise ShadowPilotAdmissionError("Pilot admission must remain Shadow-only and non-executing")
        expected = _receipt_digest(self._payload())
        if self.receipt_digest and self.receipt_digest != expected:
            raise ShadowPilotAdmissionError("admission receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": "pilot-shadow-admission-receipt/v1",
            "symbol": self.symbol.upper(),
            "venue": self.venue,
            "provider": self.provider,
            "provider_symbol_literal": self.provider_symbol_literal,
            "evaluation_timestamp_utc": _utc(self.evaluation_timestamp_utc).isoformat(),
            "admission_timestamp_utc": _utc(self.admission_timestamp_utc).isoformat(),
            "journal_record_digest": self.journal_record_digest,
            "journal_record_sequence": self.journal_record_sequence,
            "journal_record_name": self.journal_record_name,
            "rank_snapshot_id": self.rank_snapshot_id,
            "rank_snapshot_source": self.rank_snapshot_source,
            "native_timeframes": list(self.native_timeframes),
            "raw_evidence_ids": list(self.raw_evidence_ids),
            "raw_evidence_digests": list(self.raw_evidence_digests),
            "episode_id": self.episode_id,
            "shadow_only": self.shadow_only,
            "automated_order_execution": self.automated_order_execution,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "receipt_digest": self.receipt_digest}


@dataclass(frozen=True)
class GuardedShadowAdmission:
    """Result of one verified, registered Shadow-only Pilot admission."""

    receipt: ShadowPilotAdmissionReceipt
    episode: ObservationEpisode
    watchlist_entry: WatchlistEntry

    def __post_init__(self) -> None:
        if self.episode.episode_id != self.receipt.episode_id:
            raise ShadowPilotAdmissionError("receipt and lifecycle Episode ID differ")
        if self.watchlist_entry.active_episode != self.episode:
            raise ShadowPilotAdmissionError("registered Watchlist entry does not bind the admission Episode")


def admit_guarded_handoff(
    handoff: GuardedPreAdmissionHandoff,
    journal: PilotAdmissionEvidenceJournal,
    watchlist: WatchlistManager,
    *,
    admission_timestamp_utc: datetime | None = None,
) -> GuardedShadowAdmission:
    """Re-verify immutable evidence, then open/register one canonical Episode.

    The only Watchlist mutation occurs after evidence verification, policy
    evaluation, lifecycle execution, and immutable receipt construction succeed.
    """
    if not isinstance(handoff, GuardedPreAdmissionHandoff):
        raise ShadowPilotAdmissionError("GuardedPreAdmissionHandoff is required")
    if not isinstance(journal, PilotAdmissionEvidenceJournal):
        raise ShadowPilotAdmissionError("PilotAdmissionEvidenceJournal is required")
    if not isinstance(watchlist, WatchlistManager):
        raise ShadowPilotAdmissionError("WatchlistManager is required")
    if handoff.admission_state != "PRE_ADMISSION_NOT_STARTED" or handoff.actual_pilot_admission_occurred:
        raise ShadowPilotAdmissionError("only a pre-admission handoff may be admitted")
    selected = handoff.selected
    candidate = selected.candidate
    if not selected.qualifies:
        raise ShadowPilotAdmissionError("only a qualifying selected candidate may be admitted")
    if candidate.venue != "BYBIT" or candidate.provider.lower() != "coinalyze":
        raise ShadowPilotAdmissionError("Pilot admission requires Bybit/Coinalyze identity")
    if tuple(candidate.native_timeframes) != ("1m", "5m"):
        raise ShadowPilotAdmissionError("Pilot admission requires native Coinalyze 1m and 5m evidence")
    admission_timestamp = _utc(admission_timestamp_utc or candidate.evaluation_timestamp_utc)
    if admission_timestamp < _utc(candidate.evaluation_timestamp_utc):
        raise ShadowPilotAdmissionError("admission cannot precede candidate evaluation")

    _reverify_handoff_record(handoff, journal)

    request = ObservationRequest(
        exchange="bybit",
        symbol=candidate.symbol.upper(),
        timeframe="5m",
        request_timestamp=admission_timestamp,
        trigger_timestamp=_utc(candidate.evaluation_timestamp_utc),
        trigger_reasons=("PILOT_SEARCH_PROFILE_V1",),
        trigger_metrics={
            "pilot_journal_record_digest": handoff.journal_record_digest,
            "pilot_journal_record_sequence": handoff.journal_record_sequence,
            "pilot_journal_record_name": handoff.journal_record_name,
            "pilot_provider": candidate.provider,
            "pilot_provider_symbol_literal": candidate.provider_symbol_literal,
            "pilot_raw_evidence_ids": tuple(candidate.raw_evidence_ids),
            "pilot_raw_evidence_digests": tuple(candidate.raw_evidence_digests),
            "pilot_rank_snapshot_id": candidate.rank_snapshot.snapshot_id,
            "pilot_rank_snapshot_source": candidate.rank_snapshot.source,
            "pilot_search_profile": "pilot_search_profile_v1",
            "pilot_shadow_only": True,
        },
        data_quality_status=DataQualityStatus.VALID,
        eligible=True,
    )
    existing = watchlist.get(exchange=request.exchange, symbol=request.symbol, timeframe=request.timeframe)
    context = build_watchlist_observation_context(
        existing, exchange=request.exchange, symbol=request.symbol, timeframe=request.timeframe
    )
    boundary = evaluate_observation_boundary(ObservationBoundaryInput(request=request, watchlist_context=context))
    if boundary.proposed_lifecycle_action is not ObservationLifecycleDecision.OPEN:
        raise ShadowPilotAdmissionError("Pilot admission requires a new lifecycle OPEN transition")
    execution = execute_observation_lifecycle(
        ObservationLifecycleExecutionInput(
            boundary_result=boundary,
            active_episode=None,
            execution_timestamp=admission_timestamp,
        )
    )
    episode = execution.newly_opened_episode
    if episode is None:
        raise ShadowPilotAdmissionError("canonical lifecycle did not create an ObservationEpisode")
    receipt = ShadowPilotAdmissionReceipt(
        symbol=candidate.symbol.upper(),
        venue=candidate.venue,
        provider=candidate.provider,
        provider_symbol_literal=candidate.provider_symbol_literal,
        evaluation_timestamp_utc=_utc(candidate.evaluation_timestamp_utc),
        admission_timestamp_utc=admission_timestamp,
        journal_record_digest=handoff.journal_record_digest,
        journal_record_sequence=handoff.journal_record_sequence,
        journal_record_name=handoff.journal_record_name,
        rank_snapshot_id=candidate.rank_snapshot.snapshot_id,
        rank_snapshot_source=candidate.rank_snapshot.source,
        native_timeframes=tuple(candidate.native_timeframes),
        raw_evidence_ids=tuple(candidate.raw_evidence_ids),
        raw_evidence_digests=tuple(candidate.raw_evidence_digests),
        episode_id=episode.episode_id,
    )
    try:
        entry = watchlist.apply_observation_lifecycle_result(execution)
    except (TypeError, ValueError) as exc:
        raise ShadowPilotAdmissionError(f"Watchlist registration failed: {exc}") from exc
    if entry is None:
        raise ShadowPilotAdmissionError("canonical lifecycle did not register the ObservationEpisode")
    return GuardedShadowAdmission(receipt, episode, entry)


def process_admitted_shadow_cycle(
    admission: GuardedShadowAdmission,
    snapshot: MarketSnapshot,
    watchlist: WatchlistManager,
    runtime: RuntimeOrchestrator,
    *,
    completion_timestamp_utc: datetime | None = None,
) -> ObservationRuntimeCycleResult:
    """Process one fresh, supplied closed 5m snapshot for an admitted Episode.

    Admission evidence is not converted into a later market snapshot. The
    caller must provide that independent, point-in-time snapshot.
    """
    if not isinstance(admission, GuardedShadowAdmission):
        raise ShadowPilotAdmissionError("GuardedShadowAdmission is required")
    if not isinstance(snapshot, MarketSnapshot) or not isinstance(watchlist, WatchlistManager) or not isinstance(runtime, RuntimeOrchestrator):
        raise ShadowPilotAdmissionError("canonical snapshot, WatchlistManager, and RuntimeOrchestrator are required")
    receipt = admission.receipt
    if (snapshot.exchange.strip().lower(), snapshot.symbol.strip().upper(), snapshot.timeframe.strip().lower(), snapshot.data_source.strip().lower()) != ("bybit", receipt.symbol.upper(), "5m", receipt.provider.lower()):
        raise ShadowPilotAdmissionError("runtime snapshot identity does not match admitted Pilot provenance")
    if _utc(snapshot.timestamp) < receipt.admission_timestamp_utc:
        raise ShadowPilotAdmissionError("runtime snapshot cannot precede admission")
    if not snapshot.ohlcv or snapshot.ohlcv[-1].get("is_closed") is not True:
        raise ShadowPilotAdmissionError("runtime requires a supplied closed 5m snapshot")
    entry = watchlist.get(exchange="bybit", symbol=receipt.symbol, timeframe="5m")
    if entry is None or entry.active_episode_id != receipt.episode_id:
        raise ShadowPilotAdmissionError("admitted lifecycle Episode is not registered")
    completed = _utc(completion_timestamp_utc or snapshot.timestamp)
    return process_observation_runtime_cycle(
        ObservationRuntimeCycleInput(
            snapshot=snapshot,
            closed_candle_timestamp=snapshot.ohlcv[-1]["timestamp"],
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            runtime_request_timestamp=snapshot.timestamp,
            runtime_completion_timestamp=completed,
        ),
        watchlist,
        runtime,
    )


def _reverify_handoff_record(handoff: GuardedPreAdmissionHandoff, journal: PilotAdmissionEvidenceJournal) -> None:
    try:
        records = journal.verify()
    except Exception as exc:
        raise ShadowPilotAdmissionError(f"Pilot journal verification failed: {exc}") from exc
    matches = [record for record in records if record.get("record_digest") == handoff.journal_record_digest]
    if len(matches) != 1:
        raise ShadowPilotAdmissionError("verified journal does not contain exactly one handoff digest")
    record = matches[0]
    if record.get("sequence") != handoff.journal_record_sequence:
        raise ShadowPilotAdmissionError("handoff journal sequence differs from verified record")
    expected_name = f"{handoff.journal_record_sequence:020d}.json"
    if handoff.journal_record_name != expected_name:
        raise ShadowPilotAdmissionError("handoff journal record identity differs from verified sequence")
    if record.get("selection", {}).get("winner") != handoff.selected.evidence_dict():
        raise ShadowPilotAdmissionError("handoff candidate provenance differs from verified journal evidence")


def _receipt_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _sha256(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ShadowPilotAdmissionError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
