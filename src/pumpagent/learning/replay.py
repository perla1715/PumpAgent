"""Deterministic offline replay through the canonical Runtime orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from pumpagent.learning.domain import SUPPORTED_HORIZONS_MINUTES
from pumpagent.learning.outcomes import OutcomeAttributionService
from pumpagent.learning.repository import SQLiteLearningCaseRepository
from pumpagent.learning.service import LearningCasePersistenceService
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.orchestrator import RuntimeOrchestrator


@dataclass(frozen=True)
class ReplayConfig:
    symbol: str
    exchange: str
    timeframe: str
    start: datetime | None = None
    end: datetime | None = None
    runtime_version: str = "unknown"


@dataclass(frozen=True)
class ReplayRunSummary:
    processed_cycles: int
    completed_cycles: int
    stored_cases: int
    skipped_cycles: int
    failed_cycles: int
    outcomes_attached: int
    reasons: tuple[str, ...]


class HistoricalReplayRunner:
    def __init__(
        self,
        repository: SQLiteLearningCaseRepository,
        *,
        orchestrator_factory: Callable[..., RuntimeOrchestrator] = RuntimeOrchestrator,
    ) -> None:
        self.repository = repository
        self.orchestrator_factory = orchestrator_factory

    def run(
        self,
        snapshots: Iterable[MarketSnapshot],
        config: ReplayConfig,
    ) -> ReplayRunSummary:
        ordered = _validate_and_select(snapshots, config)
        episode_id = (
            f"replay:{config.exchange}:{config.symbol}:{config.timeframe}:"
            f"{ordered[0].timestamp.isoformat() if ordered else 'empty'}"
        )
        hypothesis_ids = _DeterministicHypothesisIds(episode_id)
        runtime = self.orchestrator_factory(
            hypothesis_id_generator=hypothesis_ids.next
        )
        persistence = LearningCasePersistenceService(self.repository)
        completed = 0
        stored = 0
        failed = 0
        skipped = 0
        reasons: list[str] = []
        run_case_ids: list[str] = []
        previous = None
        for snapshot in ordered:
            event = runtime.process_market_update(
                snapshot,
                episode_id=episode_id,
                previous_state=(
                    previous.agent_state.current_state
                    if previous is not None
                    else "UNKNOWN"
                ),
                previous_hypothesis=(
                    previous.hypothesis_package if previous is not None else None
                ),
                previous_process_evidence=(
                    previous.process_evidence if previous is not None else None
                ),
                previous_process_quality_assessments=(
                    previous.process_quality_history if previous is not None else ()
                ),
                healthy_baseline_reference=(
                    previous.healthy_baseline_reference
                    if previous is not None
                    else None
                ),
                healthy_baseline_designation=(
                    previous.healthy_baseline_designation
                    if previous is not None
                    else None
                ),
                previous_scenario_probability=(
                    previous.scenario_probability if previous is not None else None
                ),
                classification_timestamp=snapshot.timestamp,
            )
            if event.runtime_status is RuntimeStatus.COMPLETED:
                completed += 1
                runtime.commit_runtime_continuity(event.event_id)
                stored_case = persistence.persist(
                    event,
                    ingestion_timestamp=event.cycle_timestamp,
                    provenance={
                        "source": "historical_replay",
                        "runtime_version": config.runtime_version,
                    },
                )
                stored += 1
                run_case_ids.append(stored_case.case_id)
                previous = event
            elif event.runtime_status is RuntimeStatus.REJECTED:
                skipped += 1
                reasons.extend(event.errors_or_warnings)
            else:
                failed += 1
                reasons.extend(event.errors_or_warnings)
        outcomes = self._complete_outcomes(ordered, tuple(run_case_ids))
        return ReplayRunSummary(
            processed_cycles=len(ordered),
            completed_cycles=completed,
            stored_cases=stored,
            skipped_cycles=skipped,
            failed_cycles=failed,
            outcomes_attached=outcomes,
            reasons=tuple(reasons),
        )

    def _complete_outcomes(
        self,
        snapshots: tuple[MarketSnapshot, ...],
        case_ids: tuple[str, ...],
    ) -> int:
        attribution = OutcomeAttributionService(self.repository)
        attached = 0
        for case_id in case_ids:
            case = self.repository.get_case(case_id)
            if case is None:
                raise ValueError("Replay case disappeared before outcome attribution.")
            future = tuple(
                _outcome_observation(snapshot)
                for snapshot in snapshots
                if snapshot.timestamp > case.cycle_timestamp
            )
            for horizon in SUPPORTED_HORIZONS_MINUTES:
                attribution.attribute(
                    case,
                    future,
                    horizon_minutes=horizon,
                    creation_timestamp=case.cycle_timestamp
                    + _minutes(horizon),
                )
                attached += 1
        return attached


class _DeterministicHypothesisIds:
    def __init__(self, episode_id: str) -> None:
        self.episode_id = episode_id
        self.index = 0

    def next(self) -> str:
        self.index += 1
        return f"replay-hypothesis:{self.episode_id}:{self.index:08d}"


def _validate_and_select(
    snapshots: Iterable[MarketSnapshot], config: ReplayConfig
) -> tuple[MarketSnapshot, ...]:
    values = tuple(snapshots)
    timestamps = tuple(item.timestamp for item in values)
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError("Replay snapshots must be strictly chronological.")
    selected: list[MarketSnapshot] = []
    for item in values:
        if (
            item.symbol != config.symbol
            or item.exchange != config.exchange
            or item.timeframe != config.timeframe
        ):
            raise ValueError("Replay market identity mismatch.")
        _validate_snapshot_has_no_future_data(item)
        if config.start is not None and item.timestamp < config.start:
            continue
        if config.end is not None and item.timestamp > config.end:
            continue
        selected.append(item)
    return tuple(selected)


def _validate_snapshot_has_no_future_data(snapshot: MarketSnapshot) -> None:
    seen: set[datetime] = set()
    for candle in snapshot.ohlcv:
        raw_timestamp = candle.get("timestamp")
        if isinstance(raw_timestamp, str):
            timestamp = datetime.fromisoformat(
                raw_timestamp.replace("Z", "+00:00")
            )
        elif isinstance(raw_timestamp, datetime):
            timestamp = raw_timestamp
        else:
            raise ValueError("Replay OHLCV timestamp is missing or invalid.")
        if timestamp.tzinfo is None:
            raise ValueError("Replay OHLCV timestamp must be timezone-aware.")
        if timestamp > snapshot.timestamp:
            raise ValueError(
                "Replay snapshot contains future OHLCV data."
            )
        if timestamp in seen:
            raise ValueError("Replay snapshot contains duplicate OHLCV timestamps.")
        seen.add(timestamp)


def _outcome_observation(snapshot: MarketSnapshot) -> dict[str, object]:
    candle = snapshot.ohlcv[-1] if snapshot.ohlcv else {}
    return {
        "timestamp": snapshot.timestamp,
        "symbol": snapshot.symbol,
        "exchange": snapshot.exchange,
        "timeframe": snapshot.timeframe,
        "close": snapshot.price,
        "high": candle.get("high", snapshot.price),
        "low": candle.get("low", snapshot.price),
        "volume": snapshot.volume,
    }


def _minutes(value: int):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    return timedelta(minutes=value)
