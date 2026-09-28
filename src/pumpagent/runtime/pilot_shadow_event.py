"""Fail-closed, non-executing composition of one Pilot shadow event."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any

from pumpagent.runtime.adapters.scanner_observation import (
    ScannerAttentionDecision,
    build_observation_request_from_scanner_result,
)
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import AgentStateType, ObservationEpisodeStatus
from pumpagent.runtime.domain.observation_episode import ObservationEpisode, generate_episode_id
from pumpagent.runtime.modules.observation_lifecycle.runtime_cycle import (
    ObservationRuntimeCycleInput,
    ObservationRuntimeCycleStatus,
    process_observation_runtime_cycle,
)
from pumpagent.runtime.modules.watchlist import WatchlistEntry, WatchlistManager
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator
from pumpagent.pilot.checkpoint import INPUT_SCHEMA, RECORD_SCHEMA, map_recorded_snapshot
from pumpagent.pilot.journal import AppendStatus, append_checkpoint, recover


class ShadowEventStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ShadowEventResult:
    """Immutable result; COMPLETED is only possible after every commit stage."""

    status: ShadowEventStatus
    shadow_event_id: str
    reason: str
    scanner_attention: ScannerAttentionDecision
    observation_episode_id: str | None = None
    checkpoint_id: str | None = None
    checkpoint_append_status: str | None = None
    runtime_event_id: str | None = None
    decision_assessment_type: str | None = None
    journal_record_name: str | None = None


def run_shadow_event(
    snapshot: MarketSnapshot,
    scanner_result: object,
    attention_decision: ScannerAttentionDecision,
    journal_directory: Path,
    *,
    runtime: RuntimeOrchestrator | None = None,
    run_id: str = "pilot-shadow",
    recorded_at: datetime | None = None,
) -> ShadowEventResult:
    """Run one fixture-driven event, failing closed at every boundary.

    The journal directory must already exist and be a dedicated ``pilot_shadow``
    directory. No trading, exchange, or position-management interface is used.
    """
    shadow_id = _shadow_id(snapshot)
    def failed(reason: str, **kwargs: Any) -> ShadowEventResult:
        return ShadowEventResult(ShadowEventStatus.FAILED, shadow_id, reason, attention_decision, **kwargs)
    if not isinstance(snapshot, MarketSnapshot):
        return failed("Only a validated MarketSnapshot is supported by this coordinator.")
    if not isinstance(attention_decision, ScannerAttentionDecision):
        return failed("Explicit ScannerAttentionDecision is required.")
    if snapshot.timeframe != "5m":
        return failed("Only closed 5m snapshots are supported.")
    if not isinstance(journal_directory, Path) or not journal_directory.is_absolute():
        return failed("An absolute dedicated pilot_shadow journal directory is required.")
    try:
        adapted = build_observation_request_from_scanner_result(
            scanner_result, attention_decision,
            request_timestamp=recorded_at or snapshot.timestamp,
        )
    except (TypeError, ValueError) as exc:
        return failed(f"Scanner observation adaptation failed: {exc}")
    if not adapted.success or adapted.request is None:
        return failed(f"Scanner attention/admission rejected: {adapted.adapter_reason}")
    request = adapted.request
    if request.symbol != snapshot.symbol.strip().upper() or request.exchange != snapshot.exchange.strip().lower():
        return failed("Scanner identity does not match the snapshot.")
    scanner_provider = _field(scanner_result, "provider")
    if not isinstance(scanner_provider, str) or scanner_provider.strip().lower() != snapshot.data_source.strip().lower():
        return failed("Scanner provider/source identity does not match the snapshot.")
    latest_candle_timestamp = snapshot.ohlcv[-1].get("timestamp") if snapshot.ohlcv else None
    if request.triggering_closed_candle_timestamp != latest_candle_timestamp:
        return failed("Scanner closed-candle timestamp does not match the snapshot stream.")
    if not snapshot.ohlcv or snapshot.ohlcv[-1].get("is_closed") is not True:
        return failed("The latest 5m candle is not explicitly closed.")
    if not journal_directory.exists() or journal_directory.name != "pilot_shadow":
        return failed("Dedicated pilot_shadow journal directory does not exist.")
    try:
        recovered = recover(journal_directory, symbol=snapshot.symbol)
        previous_id = recovered.last_valid.checkpoint_id if recovered.last_valid else None
        payload = _checkpoint_payload(snapshot, scanner_result, run_id, previous_id, recorded_at)
        mapped = map_recorded_snapshot(payload)
        append = append_checkpoint(
            journal_directory, payload,
            committed_at=(recorded_at or snapshot.timestamp).isoformat().replace("+00:00", "Z"),
        )
    except Exception as exc:
        return failed(f"Checkpoint mapping/journal append failed: {exc}")
    if append.status is not AppendStatus.APPENDED:
        return failed("Duplicate checkpoint rejected; no second shadow event was completed.",
                      checkpoint_id=append.record.checkpoint_id, checkpoint_append_status=append.status.value)
    episode: ObservationEpisode | None = None
    try:
        episode = ObservationEpisode(
            episode_id=generate_episode_id(request.exchange, request.symbol, request.timeframe, request.trigger_timestamp),
            exchange=request.exchange, symbol=request.symbol, timeframe=request.timeframe,
            opening_timestamp=request.trigger_timestamp, status=ObservationEpisodeStatus.ACTIVE,
            scanner_trigger_timestamp=request.trigger_timestamp,
            trigger_reasons=request.trigger_reasons, trigger_metrics=request.trigger_metrics,
        )
        watchlist = WatchlistManager()
        watchlist._entries[(request.exchange, request.symbol, request.timeframe)] = WatchlistEntry(
            symbol=request.symbol, exchange=request.exchange, timeframe=request.timeframe,
            first_seen=request.request_timestamp, last_updated=request.request_timestamp,
            current_agent_state=AgentStateType.UNKNOWN, active_episode=episode,
            active_episode_id=episode.episode_id, lifecycle_status=ObservationEpisodeStatus.ACTIVE,
            latest_accepted_trigger_timestamp=request.trigger_timestamp,
        )
        cycle = process_observation_runtime_cycle(
            ObservationRuntimeCycleInput(
                snapshot=snapshot, closed_candle_timestamp=snapshot.ohlcv[-1]["timestamp"],
                exchange=snapshot.exchange, symbol=snapshot.symbol, timeframe=snapshot.timeframe,
                runtime_request_timestamp=request.request_timestamp,
                runtime_completion_timestamp=recorded_at or snapshot.timestamp,
            ), watchlist, runtime or RuntimeOrchestrator(),
        )
    except Exception as exc:
        return failed(f"Runtime analytical cycle failed: {exc}", checkpoint_id=mapped.checkpoint_id,
                      checkpoint_append_status=append.status.value,
                      observation_episode_id=episode.episode_id if episode else None)
    if cycle.status is not ObservationRuntimeCycleStatus.COMPLETED or cycle.runtime_result is None:
        return failed(f"Runtime/DecisionAssessment did not complete: {cycle.orchestration_reason}",
                      checkpoint_id=mapped.checkpoint_id, checkpoint_append_status=append.status.value,
                      observation_episode_id=cycle.episode_id, runtime_event_id=cycle.runtime_event_id)
    decision = cycle.runtime_result.decision_assessment
    if decision is None or not decision.non_execution_confirmation:
        return failed("DecisionAssessment contract did not confirm non-execution.",
                      checkpoint_id=mapped.checkpoint_id, checkpoint_append_status=append.status.value,
                      observation_episode_id=cycle.episode_id, runtime_event_id=cycle.runtime_event_id)
    return ShadowEventResult(
        ShadowEventStatus.COMPLETED, shadow_id, "Shadow event completed without execution side effects.",
        attention_decision, cycle.episode_id, mapped.checkpoint_id, append.status.value,
        cycle.runtime_event_id, decision.decision_type.value,
        f"{append.record.to_dict()['commit_sequence']:020d}.json",
    )


def _shadow_id(snapshot: object) -> str:
    return f"shadow:{getattr(snapshot, 'event_id', 'unknown')}"


def _field(source: object, name: str) -> object:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _checkpoint_payload(snapshot: MarketSnapshot, scanner_result: object, run_id: str,
                        previous_id: str | None, recorded_at: datetime | None) -> dict:
    candle = snapshot.ohlcv[-1]
    closed = candle["timestamp"]
    opened = closed - timedelta(minutes=5)
    recorded = recorded_at or snapshot.timestamp
    evidence_id = str(snapshot.event_id)
    source_hash = hashlib.sha256(json.dumps(scanner_result, sort_keys=True, default=str).encode()).hexdigest()
    def metric(name: str, value: object, unit: str = "native") -> dict:
        return {"status": "AVAILABLE", "value": value, "unit": unit,
                "provenance": {"source_evidence_id": evidence_id, "source_field": name,
                                "kind": "OBSERVED", "derivation_version": "shadow_event_v1"}}
    def unavailable(name: str) -> dict:
        return {"status": "UNAVAILABLE", "reason": f"{name} not supplied by fixture."}
    values = {
        "price": metric("price", snapshot.price), "volume": metric("volume", snapshot.volume),
        "open": metric("open", candle["open"]), "high": metric("high", candle["high"]),
        "low": metric("low", candle["low"]), "close": metric("close", candle["close"]),
        "open_interest": unavailable("open_interest"), "oi_change_pct": unavailable("oi_change_pct"),
        "cvd": unavailable("cvd"), "funding_rate": unavailable("funding_rate"),
        "liquidations": unavailable("liquidations"),
    }
    return {
        "schema_version": INPUT_SCHEMA, "run_id": run_id, "event_id": str(snapshot.event_id),
        "symbol": snapshot.symbol, "provider": snapshot.data_source, "market_id": snapshot.exchange,
        "contract_id": snapshot.symbol, "source_evidence": {"id": evidence_id, "sha256": source_hash},
        "timeframe": snapshot.timeframe, "candle_open_time": _iso(opened), "candle_close_time": _iso(closed),
        "provider_event_time": {"status": "AVAILABLE", "value": _iso(closed)},
        "received_time": _iso(snapshot.timestamp), "recorded_time": _iso(recorded),
        "normalized_values": values,
        "quality": {"status": "good", "missing_metrics": list(values)[6:], "reasons": [], "warnings": []},
        "previous_checkpoint_id": previous_id,
        "assessments": {"geometry_state": "UNASSESSED", "counter_thesis_state": "UNASSESSED", "alert_state": "UNASSESSED"},
        "versions": {"code": "shadow_event_v1", "config": "shadow_event_v1", "schema": RECORD_SCHEMA},
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
