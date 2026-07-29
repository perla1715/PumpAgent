"""Versioned deterministic research labels derived only from outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from pumpagent.learning.domain import (
    CompletenessStatus,
    OutcomeLabel,
    OutcomeRecord,
)


LABEL_POLICY_VERSION = "objective_outcome_labels_v1"


@dataclass(frozen=True)
class LabelPolicyConfig:
    continuation_return: float = 0.03
    excursion_trigger: float = 0.03
    failure_close_ceiling: float = 0.0
    recovery_close_floor: float = 0.0
    range_return: float = 0.01
    policy_version: str = LABEL_POLICY_VERSION


@dataclass(frozen=True)
class OutcomeLabelResult:
    case_id: str
    horizon_minutes: int
    label: OutcomeLabel
    policy_version: str
    reason: str


def label_outcome(
    outcome: OutcomeRecord, config: LabelPolicyConfig = LabelPolicyConfig()
) -> OutcomeLabelResult:
    if (
        outcome.completeness_status is not CompletenessStatus.COMPLETE
        or outcome.close_to_close_return is None
        or outcome.maximum_high_return is None
        or outcome.minimum_low_return is None
    ):
        return _result(
            outcome,
            OutcomeLabel.INSUFFICIENT_OUTCOME,
            config,
            "Configured outcome horizon is incomplete.",
        )
    close = outcome.close_to_close_return
    high = outcome.maximum_high_return
    low = outcome.minimum_low_return
    if high >= config.excursion_trigger and close <= config.failure_close_ceiling:
        label = OutcomeLabel.PUMP_FAILURE
        reason = "Positive excursion failed by the configured horizon."
    elif low <= -config.excursion_trigger and close >= config.recovery_close_floor:
        label = OutcomeLabel.DUMP_RECOVERY
        reason = "Negative excursion recovered by the configured horizon."
    elif close >= config.continuation_return:
        label = OutcomeLabel.PUMP_CONTINUATION
        reason = "Close return met the positive continuation threshold."
    elif close <= -config.continuation_return:
        label = OutcomeLabel.DUMP_CONTINUATION
        reason = "Close return met the negative continuation threshold."
    else:
        label = OutcomeLabel.RANGE_OR_CONTROL
        reason = "No configured directional outcome threshold was met."
    return _result(outcome, label, config, reason)


def _result(
    outcome: OutcomeRecord,
    label: OutcomeLabel,
    config: LabelPolicyConfig,
    reason: str,
) -> OutcomeLabelResult:
    return OutcomeLabelResult(
        case_id=outcome.source_case_id,
        horizon_minutes=outcome.horizon_minutes,
        label=label,
        policy_version=config.policy_version,
        reason=reason,
    )
