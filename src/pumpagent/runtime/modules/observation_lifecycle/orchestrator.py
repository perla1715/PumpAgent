"""Deterministic Scanner-to-Watchlist Observation Lifecycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from pumpagent.runtime.adapters.scanner_observation import (
    ScannerAttentionDecision,
    ScannerObservationAdapterResult,
    build_observation_request_from_scanner_result,
)
from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import (
    ObservationLifecycleDecision,
    ObservationTriggerRelation,
)
from pumpagent.runtime.domain.observation_policy import (
    ObservationPolicyDecision,
    ObservationRequest,
)
from pumpagent.runtime.modules.observation_lifecycle.executor import (
    ObservationLifecycleExecutionInput,
    ObservationLifecycleExecutionResult,
    execute_observation_lifecycle,
)
from pumpagent.runtime.modules.watchlist.manager import WatchlistEntry, WatchlistManager
from pumpagent.runtime.modules.watchlist.observation_boundary import (
    ObservationBoundaryInput,
    WatchlistObservationContext,
    build_watchlist_observation_context,
    evaluate_observation_boundary,
)


SCANNER_OBSERVATION_ORCHESTRATION_INPUT_SCHEMA_VERSION = (
    "scanner_observation_orchestration_input_v1"
)
SCANNER_OBSERVATION_ORCHESTRATION_RESULT_SCHEMA_VERSION = (
    "scanner_observation_orchestration_result_v1"
)


class ExplicitLifecycleCommand(str, Enum):
    CLOSE = "close"
    REPLACE = "replace"


class ScannerObservationOrchestrationStatus(str, Enum):
    COMPLETED = "completed"
    ADAPTER_STOPPED = "adapter_stopped"
    PREPARATION_FAILED = "preparation_failed"
    EXECUTION_FAILED = "execution_failed"
    APPLICATION_FAILED = "application_failed"


@dataclass(frozen=True)
class ScannerObservationOrchestrationInput:
    """Immutable inputs for one Scanner attention lifecycle request."""

    scanner_result: object
    attention_decision: ScannerAttentionDecision
    request_timestamp: datetime
    exchange: str | None = None
    provider: str | None = None
    lifecycle_command: ExplicitLifecycleCommand | None = None
    closure_reason: str | None = None
    schema_version: str = SCANNER_OBSERVATION_ORCHESTRATION_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        if not isinstance(self.attention_decision, ScannerAttentionDecision):
            raise ValueError("attention_decision must be a ScannerAttentionDecision.")
        _validate_timestamp("request_timestamp", self.request_timestamp)
        if self.lifecycle_command is not None and not isinstance(
            self.lifecycle_command, ExplicitLifecycleCommand
        ):
            raise ValueError("lifecycle_command must be CLOSE, REPLACE, or None.")
        if self.lifecycle_command is not None:
            _validate_non_empty("closure_reason", self.closure_reason)
        elif self.closure_reason is not None:
            raise ValueError("closure_reason requires a lifecycle command.")
        _validate_non_empty("schema_version", self.schema_version)


@dataclass(frozen=True)
class ScannerObservationOrchestrationResult(SerializableMixin):
    """Immutable result that identifies the last completed flow boundary."""

    scanner_adapter_result: ScannerObservationAdapterResult
    observation_request: ObservationRequest | None
    watchlist_context: WatchlistObservationContext | None
    policy_decision: ObservationPolicyDecision | None
    lifecycle_execution_result: ObservationLifecycleExecutionResult | None
    resulting_watchlist_entry: WatchlistEntry | None
    lifecycle_action: ObservationLifecycleDecision | None
    watchlist_state_changed: bool
    status: ScannerObservationOrchestrationStatus
    orchestration_reason: str
    timestamp: datetime
    schema_version: str = SCANNER_OBSERVATION_ORCHESTRATION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.scanner_adapter_result, ScannerObservationAdapterResult):
            raise ValueError("scanner_adapter_result has the wrong type.")
        if not isinstance(self.status, ScannerObservationOrchestrationStatus):
            raise ValueError("status has the wrong type.")
        if not isinstance(self.watchlist_state_changed, bool):
            raise ValueError("watchlist_state_changed must be a bool.")
        _validate_non_empty("orchestration_reason", self.orchestration_reason)
        _validate_non_empty("schema_version", self.schema_version)
        _validate_timestamp("timestamp", self.timestamp)


def process_scanner_observation_request(
    orchestration_input: ScannerObservationOrchestrationInput,
    watchlist: WatchlistManager,
) -> ScannerObservationOrchestrationResult:
    """Run one lifecycle-only Scanner request through the existing boundaries."""

    if not isinstance(orchestration_input, ScannerObservationOrchestrationInput):
        raise ValueError("orchestration_input has the wrong type.")
    if not isinstance(watchlist, WatchlistManager):
        raise ValueError("watchlist must be a WatchlistManager.")

    adapter = build_observation_request_from_scanner_result(
        orchestration_input.scanner_result,
        orchestration_input.attention_decision,
        exchange=orchestration_input.exchange,
        provider=orchestration_input.provider,
        request_timestamp=orchestration_input.request_timestamp,
    )
    if not adapter.success:
        return _result(
            adapter,
            orchestration_input.request_timestamp,
            ScannerObservationOrchestrationStatus.ADAPTER_STOPPED,
            adapter.adapter_reason,
        )

    request = adapter.request
    assert request is not None
    existing = watchlist.get(
        exchange=request.exchange, symbol=request.symbol, timeframe=request.timeframe
    )
    try:
        context = build_watchlist_observation_context(
            existing,
            exchange=request.exchange,
            symbol=request.symbol,
            timeframe=request.timeframe,
        )
        relation = _trigger_relation(request, context)
        command = orchestration_input.lifecycle_command
        boundary = evaluate_observation_boundary(
            ObservationBoundaryInput(
                request=request,
                watchlist_context=context,
                trigger_relation=relation,
                closure_requested=command is ExplicitLifecycleCommand.CLOSE,
                replacement_requested=command is ExplicitLifecycleCommand.REPLACE,
                closure_reason=orchestration_input.closure_reason,
            )
        )
    except (TypeError, ValueError) as exc:
        return _result(
            adapter,
            orchestration_input.request_timestamp,
            ScannerObservationOrchestrationStatus.PREPARATION_FAILED,
            str(exc),
            request=request,
            context=locals().get("context"),
            entry=existing,
        )

    try:
        execution = execute_observation_lifecycle(
            ObservationLifecycleExecutionInput(
                boundary_result=boundary,
                active_episode=existing.active_episode if existing else None,
                execution_timestamp=orchestration_input.request_timestamp,
            )
        )
    except (TypeError, ValueError) as exc:
        return _result(
            adapter,
            orchestration_input.request_timestamp,
            ScannerObservationOrchestrationStatus.EXECUTION_FAILED,
            str(exc),
            request=request,
            context=context,
            decision=boundary.policy_decision,
            entry=existing,
            action=boundary.proposed_lifecycle_action,
        )

    # Every pure preparation and executor validation has completed. This call is
    # the only mutation boundary in the orchestration flow.
    try:
        resulting_entry = watchlist.apply_observation_lifecycle_result(execution)
    except (TypeError, ValueError) as exc:
        return _result(
            adapter,
            orchestration_input.request_timestamp,
            ScannerObservationOrchestrationStatus.APPLICATION_FAILED,
            str(exc),
            request=request,
            context=context,
            decision=boundary.policy_decision,
            execution=execution,
            entry=existing,
            action=execution.executed_decision,
        )

    return _result(
        adapter,
        orchestration_input.request_timestamp,
        ScannerObservationOrchestrationStatus.COMPLETED,
        execution.execution_reason,
        request=request,
        context=context,
        decision=boundary.policy_decision,
        execution=execution,
        entry=resulting_entry,
        action=execution.executed_decision,
        changed=resulting_entry != existing,
    )


def _trigger_relation(
    request: ObservationRequest, context: WatchlistObservationContext
) -> ObservationTriggerRelation | None:
    """Classify only temporal ordering; never inspect Scanner market metrics."""

    if not context.has_active_episode:
        return None
    accepted = context.latest_accepted_trigger_timestamp
    if accepted is None:
        # Legacy active entries without an accepted trigger can accept the first one.
        return ObservationTriggerRelation.NEWER
    if request.trigger_timestamp > accepted:
        return ObservationTriggerRelation.NEWER
    if request.trigger_timestamp == accepted:
        return ObservationTriggerRelation.DUPLICATE
    return ObservationTriggerRelation.OLDER


def _result(
    adapter: ScannerObservationAdapterResult,
    timestamp: datetime,
    status: ScannerObservationOrchestrationStatus,
    reason: str,
    *,
    request: ObservationRequest | None = None,
    context: WatchlistObservationContext | None = None,
    decision: ObservationPolicyDecision | None = None,
    execution: ObservationLifecycleExecutionResult | None = None,
    entry: WatchlistEntry | None = None,
    action: ObservationLifecycleDecision | None = None,
    changed: bool = False,
) -> ScannerObservationOrchestrationResult:
    return ScannerObservationOrchestrationResult(
        scanner_adapter_result=adapter,
        observation_request=request,
        watchlist_context=context,
        policy_decision=decision,
        lifecycle_execution_result=execution,
        resulting_watchlist_entry=entry,
        lifecycle_action=action,
        watchlist_state_changed=changed,
        status=status,
        orchestration_reason=reason,
        timestamp=timestamp,
    )


def _validate_timestamp(name: str, value: Any) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime.")


def _validate_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
