"""One admitted closed-candle Observation Cycle integrated with Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pumpagent.runtime.domain import MarketSnapshot, RuntimeEvent
from pumpagent.runtime.domain.enums import RuntimeStatus
from pumpagent.runtime.domain.confidence_assessment import ConfidenceAssessment
from pumpagent.runtime.domain.decision import DecisionAssessment
from pumpagent.runtime.domain.scenario_probability import ScenarioProbability
from pumpagent.runtime.domain.process_evidence import ProcessEvidence, ProcessState, ProcessTransition
from pumpagent.runtime.domain.episode_analytical_context import (
    build_episode_analytical_context_from_runtime_result,
    prepare_runtime_previous_context,
)
from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.modules.observation_lifecycle.cycle_admission import (
    ClosedObservationCycleAdmissionInput,
    ClosedObservationCycleAdmissionResult,
    CycleAdmissionDecision,
    evaluate_closed_observation_cycle_admission,
)
from pumpagent.runtime.modules.observation_lifecycle.cycle_completion import (
    CycleCompletionStatus,
    ObservationCycleCompletionInput,
    ObservationCycleCompletionResult,
    prepare_completed_observation_cycle,
)
from pumpagent.runtime.modules.market_eligibility import MarketEligibilityResult
from pumpagent.runtime.modules.watchlist.manager import WatchlistEntry, WatchlistManager
from pumpagent.runtime.modules.watchlist.observation_boundary import (
    build_watchlist_observation_context,
)
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator


OBSERVATION_RUNTIME_CYCLE_INPUT_SCHEMA_VERSION = "observation_runtime_cycle_input_v1"
OBSERVATION_RUNTIME_CYCLE_RESULT_SCHEMA_VERSION = "observation_runtime_cycle_result_v1"


class ObservationRuntimeCycleStatus(str, Enum):
    COMPLETED = "COMPLETED"
    INELIGIBLE = "INELIGIBLE"
    ADMISSION_STOPPED = "ADMISSION_STOPPED"
    RUNTIME_FAILED = "RUNTIME_FAILED"
    COMPLETION_REJECTED = "COMPLETION_REJECTED"
    INVALID_CONTEXT = "INVALID_CONTEXT"


@dataclass(frozen=True)
class ObservationRuntimeCycleInput(SerializableMixin):
    snapshot: MarketSnapshot
    closed_candle_timestamp: datetime
    exchange: str
    symbol: str
    timeframe: str
    runtime_request_timestamp: datetime
    runtime_completion_timestamp: datetime
    schema_version: str = OBSERVATION_RUNTIME_CYCLE_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.snapshot, MarketSnapshot):
            raise ValueError("snapshot must be a MarketSnapshot.")
        for name, value in (
            ("exchange", self.exchange), ("symbol", self.symbol),
            ("timeframe", self.timeframe), ("schema_version", self.schema_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")
        _require_aware("closed_candle_timestamp", self.closed_candle_timestamp)
        _require_aware("runtime_request_timestamp", self.runtime_request_timestamp)
        _require_aware("runtime_completion_timestamp", self.runtime_completion_timestamp)
        if self.runtime_completion_timestamp < self.runtime_request_timestamp:
            raise ValueError("runtime_completion_timestamp cannot precede the request.")


@dataclass(frozen=True)
class ObservationRuntimeCycleResult(SerializableMixin):
    status: ObservationRuntimeCycleStatus
    exchange: str
    symbol: str
    timeframe: str
    episode_id: str | None
    admission_result: ClosedObservationCycleAdmissionResult | None
    runtime_invoked: bool
    runtime_result: RuntimeEvent | None
    runtime_event_id: str | None
    cycle_completion_result: ObservationCycleCompletionResult | None
    resulting_watchlist_entry: WatchlistEntry | None
    watchlist_changed: bool
    orchestration_reason: str
    request_timestamp: datetime
    completion_timestamp: datetime
    process_evidence: ProcessEvidence | None = None
    process_state: ProcessState | None = None
    process_transition: ProcessTransition | None = None
    previous_process_evidence_used: bool = False
    eligibility_result: MarketEligibilityResult | None = None
    schema_version: str = OBSERVATION_RUNTIME_CYCLE_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.status, ObservationRuntimeCycleStatus):
            raise ValueError("status must be an ObservationRuntimeCycleStatus.")
        if self.status is ObservationRuntimeCycleStatus.COMPLETED:
            if not self.runtime_invoked or self.runtime_result is None or not self.watchlist_changed:
                raise ValueError("COMPLETED requires Runtime and a Watchlist change.")
            if self.eligibility_result is not None:
                raise ValueError("COMPLETED cannot contain an eligibility rejection.")
        elif self.status is ObservationRuntimeCycleStatus.INELIGIBLE:
            if (
                not self.runtime_invoked
                or self.eligibility_result is None
                or self.runtime_result is None
            ):
                raise ValueError(
                    "INELIGIBLE requires canonical Runtime and eligibility results."
                )
            if self.eligibility_result.eligible:
                raise ValueError("INELIGIBLE requires a rejected eligibility result.")
            if self.cycle_completion_result is not None:
                raise ValueError("INELIGIBLE cannot contain a completion result.")
            if self.watchlist_changed:
                raise ValueError("INELIGIBLE cannot change Watchlist state.")
        elif self.watchlist_changed:
            raise ValueError("Only COMPLETED may change Watchlist.")
        elif self.eligibility_result is not None:
            raise ValueError("Only INELIGIBLE may contain an eligibility result.")

    @property
    def market_identity(self) -> tuple[str, str, str]:
        return self.exchange, self.symbol, self.timeframe


def process_observation_runtime_cycle(
    cycle_input: ObservationRuntimeCycleInput,
    watchlist: WatchlistManager,
    runtime: RuntimeOrchestrator,
) -> ObservationRuntimeCycleResult:
    """Process exactly one supplied closed 5m snapshot, with one storage commit."""
    if not isinstance(cycle_input, ObservationRuntimeCycleInput):
        raise ValueError("cycle_input must be an ObservationRuntimeCycleInput.")
    if not isinstance(watchlist, WatchlistManager):
        raise ValueError("watchlist must be a WatchlistManager.")
    if not isinstance(runtime, RuntimeOrchestrator):
        raise ValueError("runtime must be a RuntimeOrchestrator.")

    value = cycle_input
    if not _same_identity(value, value.snapshot):
        return _result(value, ObservationRuntimeCycleStatus.INVALID_CONTEXT,
                       "Input market identity does not match the snapshot.")

    entry = watchlist.get(exchange=value.exchange, symbol=value.symbol, timeframe=value.timeframe)
    try:
        context = build_watchlist_observation_context(
            entry, exchange=value.exchange, symbol=value.symbol, timeframe=value.timeframe
        )
        admission = evaluate_closed_observation_cycle_admission(
            ClosedObservationCycleAdmissionInput(
                snapshot=value.snapshot,
                watchlist_context=context,
                request_timestamp=value.runtime_request_timestamp,
                latest_closed_candle_timestamp=value.closed_candle_timestamp,
            )
        )
    except (TypeError, ValueError) as exc:
        return _result(value, ObservationRuntimeCycleStatus.INVALID_CONTEXT, str(exc), entry=entry)

    if admission.decision is not CycleAdmissionDecision.ADMIT:
        return _result(value, ObservationRuntimeCycleStatus.ADMISSION_STOPPED,
                       admission.admission_reason, admission=admission, entry=entry)
    if entry is None or entry.active_episode is None:
        return _result(value, ObservationRuntimeCycleStatus.INVALID_CONTEXT,
                       "ADMIT requires a stored active Episode.", admission=admission, entry=entry)

    # The concrete Runtime's helper history is mutable and legacy-global. Bind it
    # before invocation so no completed/other Episode can enter this cycle.
    try:
        previous = prepare_runtime_previous_context(
            entry.active_episode, entry.active_episode_analytical_context
        )
        runtime.bind_observation_episode(entry.active_episode.episode_id)
    except (TypeError, ValueError) as exc:
        return _result(value, ObservationRuntimeCycleStatus.INVALID_CONTEXT,
                       f"Runtime Episode context is invalid: {exc}",
                       admission=admission, entry=entry)
    try:
        runtime_result = runtime.process_market_update(
            value.snapshot,
            previous_state=previous.previous_state,
            previous_hypothesis=previous.previous_hypothesis,
            episode_id=entry.active_episode.episode_id,
            previous_process_evidence=previous.previous_process_evidence,
            previous_process_quality_assessments=(
                previous.previous_process_quality_assessments
            ),
            healthy_baseline_reference=previous.healthy_baseline_reference,
            healthy_baseline_designation=previous.healthy_baseline_designation,
            previous_scenario_probability=(
                previous.previous_scenario_probability
            ),
            classification_timestamp=value.closed_candle_timestamp,
        )
    except Exception as exc:  # Runtime is an integration boundary; technical failure is data.
        return _result(value, ObservationRuntimeCycleStatus.RUNTIME_FAILED,
                       f"Runtime failed: {exc}", admission=admission, entry=entry,
                       runtime_invoked=True)

    if not isinstance(runtime_result, RuntimeEvent):
        return _result(
            value,
            ObservationRuntimeCycleStatus.RUNTIME_FAILED,
            "Runtime returned an invalid result contract.",
            admission=admission,
            entry=entry,
            runtime_invoked=True,
        )
    if runtime_result.runtime_status is RuntimeStatus.REJECTED:
        eligibility_result = runtime_result.compatibility_context.get(
            "eligibility_result"
        )
        if isinstance(eligibility_result, MarketEligibilityResult):
            return _result(
                value,
                ObservationRuntimeCycleStatus.INELIGIBLE,
                f"Market eligibility rejected the cycle: {eligibility_result.reason.value}.",
                admission=admission,
                entry=entry,
                runtime_invoked=True,
                runtime_result=runtime_result,
                eligibility_result=eligibility_result,
            )
        return _result(
            value,
            ObservationRuntimeCycleStatus.INELIGIBLE,
            runtime_result.errors_or_warnings[0],
            admission=admission,
            entry=entry,
            runtime_invoked=True,
            runtime_result=runtime_result,
        )
    if runtime_result.runtime_status is RuntimeStatus.FAILED:
        return _result(
            value,
            ObservationRuntimeCycleStatus.RUNTIME_FAILED,
            runtime_result.errors_or_warnings[0],
            admission=admission,
            entry=entry,
            runtime_invoked=True,
            runtime_result=runtime_result,
        )

    invalid_reason = _runtime_invalid_reason(runtime_result, value)
    if invalid_reason is not None:
        runtime.rollback_runtime_continuity(runtime_result.event_id)
        return _result(value, ObservationRuntimeCycleStatus.RUNTIME_FAILED, invalid_reason,
                       admission=admission, entry=entry, runtime_invoked=True,
                       runtime_result=(runtime_result
                                       if isinstance(runtime_result, RuntimeEvent)
                                       else None))

    try:
        analytical_context = build_episode_analytical_context_from_runtime_result(
            runtime_result, entry.active_episode, value.closed_candle_timestamp,
            updated_at=value.runtime_completion_timestamp,
        )
    except Exception as exc:
        runtime.rollback_runtime_continuity(runtime_result.event_id)
        return _result(value, ObservationRuntimeCycleStatus.RUNTIME_FAILED,
                       f"Runtime analytical context is invalid: {exc}", admission=admission,
                       entry=entry, runtime_invoked=True, runtime_result=runtime_result)

    try:
        completion = prepare_completed_observation_cycle(
            ObservationCycleCompletionInput(
                admission_result=admission,
                active_episode=entry.active_episode,
                watchlist_entry=entry,
                runtime_event_id=runtime_result.event_id,
                runtime_completion_timestamp=value.runtime_completion_timestamp,
                accepted_closed_candle_timestamp=value.closed_candle_timestamp,
                runtime_diagnostics={
                    "diagnostic_report_present": runtime_result.compatibility_context.get(
                        "diagnostic_report"
                    )
                    is not None
                },
                analytical_context=analytical_context,
            )
        )
    except Exception as exc:
        runtime.rollback_runtime_continuity(runtime_result.event_id)
        return _result(
            value,
            ObservationRuntimeCycleStatus.COMPLETION_REJECTED,
            str(exc),
            admission=admission,
            entry=entry,
            runtime_invoked=True,
            runtime_result=runtime_result,
        )
    if completion.status is not CycleCompletionStatus.COMPLETED:
        runtime.rollback_runtime_continuity(runtime_result.event_id)
        return _result(value, ObservationRuntimeCycleStatus.COMPLETION_REJECTED,
                       completion.completion_reason, admission=admission, entry=entry,
                       runtime_invoked=True, runtime_result=runtime_result,
                       completion=completion)
    try:
        resulting = watchlist.apply_completed_observation_cycle(completion)
    except Exception as exc:
        runtime.rollback_runtime_continuity(runtime_result.event_id)
        return _result(value, ObservationRuntimeCycleStatus.COMPLETION_REJECTED,
                       str(exc), admission=admission, entry=entry,
                       runtime_invoked=True, runtime_result=runtime_result,
                       completion=completion)
    runtime.commit_runtime_continuity(runtime_result.event_id)
    return _result(value, ObservationRuntimeCycleStatus.COMPLETED,
                   "The admitted Runtime cycle completed and was applied atomically.",
                   admission=admission, entry=resulting, runtime_invoked=True,
                   runtime_result=runtime_result, completion=completion, changed=True)


def _runtime_invalid_reason(result: object, value: ObservationRuntimeCycleInput) -> str | None:
    if not isinstance(result, RuntimeEvent):
        return "Runtime returned an invalid result contract."
    if result.runtime_status is not RuntimeStatus.COMPLETED:
        return "Runtime did not return a completed canonical event."
    if not isinstance(result.event_id, str) or not result.event_id.strip():
        return "Runtime returned an empty event ID."
    if result.market_snapshot != value.snapshot or not _same_identity(result.market_snapshot, value):
        return "Runtime result market identity or snapshot does not match the admitted input."
    if (
        result.cycle_timestamp != value.snapshot.timestamp
        or result.cycle_timestamp < value.closed_candle_timestamp
    ):
        return "Runtime result timestamp is incompatible with the admitted candle."
    if result.process_evidence is None:
        return "Controlled Runtime returned no Process evidence."
    if result.process_quality_assessment is None:
        return "Controlled Runtime returned no Process Quality assessment."
    if result.process_quality_assessment.episode_id != result.process_evidence.episode_id:
        return "Runtime Process Quality Episode ID does not align."
    if result.process_quality_assessment.runtime_event_id != result.event_id:
        return "Runtime Process Quality event ID does not align."
    if result.process_evidence.runtime_event_id != result.event_id:
        return "Runtime Process evidence event ID does not align."
    if not _same_identity(result.process_evidence, value):
        return "Runtime Process evidence market identity does not align."
    if result.hypothesis_package.event_id != result.event_id:
        return "Runtime Hypothesis event ID does not align."
    if result.hypothesis_package.episode_id != result.process_evidence.episode_id:
        return "Runtime Hypothesis Episode ID does not align."
    if result.agent_state.event_id != result.event_id:
        return "Runtime Agent State event ID does not align."
    if not isinstance(result.scenario_probability, ScenarioProbability):
        return "Controlled Runtime returned no canonical Scenario Probability."
    if result.scenario_probability.runtime_event_id != result.event_id:
        return "Runtime Scenario Probability event ID does not align."
    if result.scenario_probability.episode_id != result.hypothesis_package.episode_id:
        return "Runtime Scenario Probability Episode ID does not align."
    if (
        result.scenario_probability.source_hypothesis_id
        != result.hypothesis_package.hypothesis_id
    ):
        return "Runtime Scenario Probability source Hypothesis ID does not align."
    if not isinstance(result.confidence_assessment, ConfidenceAssessment):
        return "Controlled Runtime returned no canonical ConfidenceAssessment."
    if result.confidence_assessment.event_id != result.event_id:
        return "Runtime ConfidenceAssessment event ID does not align."
    if result.confidence_assessment.episode_id != result.hypothesis_package.episode_id:
        return "Runtime ConfidenceAssessment Episode ID does not align."
    if (
        result.confidence_assessment.source_hypothesis_id
        != result.hypothesis_package.hypothesis_id
    ):
        return "Runtime ConfidenceAssessment source Hypothesis ID does not align."
    if not isinstance(result.decision_assessment, DecisionAssessment):
        return "Controlled Runtime returned no canonical DecisionAssessment."
    if result.decision_assessment.runtime_event_id != result.event_id:
        return "Runtime Decision event ID does not align."
    if result.decision_assessment.episode_id != result.hypothesis_package.episode_id:
        return "Runtime Decision Episode ID does not align."
    if (
        result.decision_assessment.scenario_probability_reference
        != result.scenario_probability.scenario_probability_id
    ):
        return "Runtime Decision Scenario Probability reference does not align."
    return None


def _result(value: ObservationRuntimeCycleInput, status: ObservationRuntimeCycleStatus,
            reason: str, *, admission: ClosedObservationCycleAdmissionResult | None = None,
            entry: WatchlistEntry | None = None, runtime_invoked: bool = False,
            runtime_result: RuntimeEvent | None = None,
            completion: ObservationCycleCompletionResult | None = None,
            eligibility_result: MarketEligibilityResult | None = None,
            changed: bool = False) -> ObservationRuntimeCycleResult:
    return ObservationRuntimeCycleResult(
        status=status, exchange=value.exchange, symbol=value.symbol, timeframe=value.timeframe,
        episode_id=admission.episode_id if admission else (entry.active_episode_id if entry else None),
        admission_result=admission, runtime_invoked=runtime_invoked,
        runtime_result=runtime_result,
        runtime_event_id=runtime_result.event_id if runtime_result else None,
        cycle_completion_result=completion, resulting_watchlist_entry=entry,
        watchlist_changed=changed, orchestration_reason=reason,
        request_timestamp=value.runtime_request_timestamp,
        completion_timestamp=value.runtime_completion_timestamp,
        process_evidence=runtime_result.process_evidence if runtime_result else None,
        process_state=(
            runtime_result.process_evidence.current_process_state
            if runtime_result and runtime_result.process_evidence
            else None
        ),
        process_transition=(
            runtime_result.process_evidence.detected_transition
            if runtime_result and runtime_result.process_evidence
            else None
        ),
        previous_process_evidence_used=(
            bool(
                runtime_result.compatibility_context.get(
                    "previous_process_evidence_used", False
                )
            )
            if runtime_result
            else False
        ),
        eligibility_result=eligibility_result,
    )


def _same_identity(left: object, right: object) -> bool:
    return (getattr(left, "exchange").strip().lower(), getattr(left, "symbol").strip().upper(),
            getattr(left, "timeframe").strip().lower()) == (
            getattr(right, "exchange").strip().lower(), getattr(right, "symbol").strip().upper(),
            getattr(right, "timeframe").strip().lower())


def _require_aware(name: str, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
