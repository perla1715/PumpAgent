"""Trusted derivation, persistence authentication, and dataset authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Mapping

from pumpagent.learning.domain import (
    LEARNING_CASE_SCHEMA_VERSION,
    OUTCOME_COMPUTATION_VERSION,
    OUTCOME_RECORD_SCHEMA_VERSION,
    READINESS_VALIDATOR_VERSION,
    CompletenessStatus,
    LearningCase,
    LearningReadinessAssessment,
    LearningReadinessStatus,
    OutcomeLabel,
    OutcomeRecord,
    ReadinessCheck,
    build_readiness_assessment_id,
)
from pumpagent.learning.labels import LABEL_POLICY_VERSION, label_outcome
from pumpagent.learning.repository import SQLiteLearningCaseRepository
from pumpagent.runtime.orchestrator.logging import (
    CANONICAL_RUNTIME_EVENT_SCHEMA_VERSION,
)


@dataclass(frozen=True)
class ReadinessPolicy:
    name: str
    require_human_approval: bool
    allowed_review_statuses: frozenset[str]


EVALUATION_POLICY = ReadinessPolicy(
    name="evaluation",
    require_human_approval=True,
    allowed_review_statuses=frozenset({"approved", "not_required"}),
)
TRAINING_POLICY = ReadinessPolicy(
    name="training",
    require_human_approval=True,
    allowed_review_statuses=frozenset({"approved", "not_required"}),
)
READINESS_POLICIES = {
    EVALUATION_POLICY.name: EVALUATION_POLICY,
    TRAINING_POLICY.name: TRAINING_POLICY,
}


def _is_policy_authorized(
    *,
    technically_ready: bool,
    review_status: str,
    manually_excluded: bool,
    administratively_blocked: bool,
    policy: ReadinessPolicy,
) -> bool:
    return (
        technically_ready
        and review_status in policy.allowed_review_statuses
        and not manually_excluded
        and not administratively_blocked
    )


SUPPORTED_READINESS_VALIDATORS = frozenset({READINESS_VALIDATOR_VERSION})
ACTIVE_READINESS_VALIDATOR = READINESS_VALIDATOR_VERSION
SUPPORTED_RUNTIME_VERSIONS = frozenset(
    {
        "526e72f",
        "526e72f1e926f82e220e197d62205ab0f625a39a",
    }
)


@dataclass(frozen=True)
class ExportAuthorization:
    authorized: bool
    reason_code: str
    assessment: LearningReadinessAssessment | None
    outcome: OutcomeRecord | None


class LearningReadinessService:
    """Assess stored facts without modifying canonical analytical content."""

    def __init__(
        self,
        repository: SQLiteLearningCaseRepository,
        *,
        validator_version: str = READINESS_VALIDATOR_VERSION,
    ) -> None:
        self.repository = repository
        self.validator_version = validator_version

    def assess(
        self, case_id: str, *, horizon_minutes: int = 60
    ) -> LearningReadinessAssessment:
        assessment = self.derive_assessment(
            case_id, horizon_minutes=horizon_minutes
        )
        return self.repository.store_readiness_assessment(assessment)

    def derive_assessment(
        self, case_id: str, *, horizon_minutes: int = 60
    ) -> LearningReadinessAssessment:
        if self.validator_version not in SUPPORTED_READINESS_VALIDATORS:
            raise ValueError(
                f"Unsupported readiness validator: {self.validator_version}"
            )
        case = self.repository.get_case(case_id)
        if case is None:
            raise ValueError("LearningCase does not exist.")
        checks: list[ReadinessCheck] = []
        invalid: list[str] = []
        not_ready: list[str] = []
        warnings: list[str] = []
        outcome_load_error: str | None = None
        try:
            outcomes = self.repository.list_outcomes(case_id)
        except (TypeError, ValueError) as exc:
            outcomes = ()
            outcome_load_error = str(exc)
        outcome = next(
            (
                item
                for item in outcomes
                if item.horizon_minutes == horizon_minutes
            ),
            None,
        )

        def check(
            check_id: str,
            passed: bool,
            detail: str,
            *,
            invalid_failure: bool = False,
        ) -> None:
            checks.append(ReadinessCheck(check_id, passed, detail))
            if not passed:
                (invalid if invalid_failure else not_ready).append(
                    f"{check_id}: {detail}"
                )

        integrity_ok, integrity_errors = self.repository.integrity_check()
        check(
            "repository_integrity",
            integrity_ok,
            "; ".join(integrity_errors) or "Repository integrity is valid.",
            invalid_failure=True,
        )
        persisted_digest = self.repository.case_payload_digest(case.case_id)
        try:
            recomputed_digest = _digest(_canonical_json(case.to_dict()))
        except (TypeError, ValueError):
            recomputed_digest = "invalid-non-canonical-payload"
        check(
            "canonical_payload_digest",
            persisted_digest == recomputed_digest,
            "Persisted LearningCase digest matches canonical content.",
            invalid_failure=True,
        )
        runtime_errors = _runtime_payload_errors(case)
        check(
            "canonical_runtime_event",
            not runtime_errors,
            "; ".join(runtime_errors)
            or "Completed canonical RuntimeEvent structure, identities, and timestamps validate.",
            invalid_failure=True,
        )
        finite_runtime = _all_finite(case.runtime_event_payload)
        check(
            "finite_runtime_values",
            finite_runtime,
            "Canonical RuntimeEvent contains only finite numerical values.",
            invalid_failure=True,
        )
        provenance_ok = _provenance_complete(case)
        check(
            "replay_or_ingestion_provenance",
            provenance_ok,
            "Runtime version and replay or ingestion provenance are present.",
        )

        check(
            "selected_outcome_exists",
            outcome is not None and outcome_load_error is None,
            outcome_load_error
            or f"Authoritative {horizon_minutes}-minute outcome exists.",
            invalid_failure=outcome_load_error is not None,
        )
        if outcome is not None:
            outcome_errors = _outcome_errors(case, outcome)
            check(
                "outcome_identity_and_boundaries",
                not outcome_errors,
                "; ".join(outcome_errors)
                or "Outcome identity, horizon, chronology, and versions validate.",
                invalid_failure=any(
                    marker in " ".join(outcome_errors)
                    for marker in ("identity", "cycle", "market", "finite", "schema")
                ),
            )
            check(
                "outcome_complete",
                outcome.completeness_status is CompletenessStatus.COMPLETE,
                "Selected outcome is complete.",
            )
            conflicts = tuple(
                item
                for item in outcomes
                if item.horizon_minutes == horizon_minutes
                and item.outcome_id != outcome.outcome_id
            )
            check(
                "single_authoritative_outcome",
                not conflicts,
                "No conflicting authoritative outcome is selected.",
                invalid_failure=True,
            )
            label = label_outcome(outcome)
            repeated_label = label_outcome(outcome)
            label_ok = (
                label == repeated_label
                and label.policy_version == LABEL_POLICY_VERSION
                and label.horizon_minutes == horizon_minutes
                and label.label is not OutcomeLabel.INSUFFICIENT_OUTCOME
            )
            check(
                "deterministic_supported_label",
                label_ok,
                "Selected-horizon label is supported, sufficient, and reproducible.",
            )

        governance = self.repository.current_governance(case.case_id)
        review_status = governance.review_status.value
        manually_excluded = governance.manually_excluded
        administratively_blocked = governance.administratively_blocked
        checks.extend(
            (
                ReadinessCheck(
                    "manual_exclusion_state",
                    not manually_excluded,
                    (
                        "Current governance permits dataset consideration."
                        if not manually_excluded
                        else "Current governance manually excludes the case."
                    ),
                ),
                ReadinessCheck(
                    "administrative_block_state",
                    not administratively_blocked,
                    (
                        "Current governance has no administrative block."
                        if not administratively_blocked
                        else "Current governance administratively blocks the case."
                    ),
                ),
            )
        )
        if manually_excluded:
            warnings.append("manual_exclusion")
        if administratively_blocked:
            warnings.append("administrative_block")

        dependencies_pending = (
            outcome is None
            or (
                outcome is not None
                and outcome.completeness_status
                is CompletenessStatus.UNAVAILABLE
            )
        )
        if invalid:
            status = LearningReadinessStatus.INVALID
        elif dependencies_pending:
            status = LearningReadinessStatus.PENDING
        elif not_ready:
            status = LearningReadinessStatus.NOT_READY
        else:
            status = LearningReadinessStatus.LEARNING_READY
        technically_ready = status is LearningReadinessStatus.LEARNING_READY
        approved_for_evaluation = _is_policy_authorized(
            technically_ready=technically_ready,
            review_status=review_status,
            manually_excluded=manually_excluded,
            administratively_blocked=administratively_blocked,
            policy=EVALUATION_POLICY,
        )
        approved_for_training = _is_policy_authorized(
            technically_ready=technically_ready,
            review_status=review_status,
            manually_excluded=manually_excluded,
            administratively_blocked=administratively_blocked,
            policy=TRAINING_POLICY,
        )
        outcome_id = outcome.outcome_id if outcome else None
        assessment_timestamp = max(
            case.ingestion_timestamp,
            outcome.creation_timestamp if outcome else case.ingestion_timestamp,
            (
                governance.review_timestamp
                if governance.review_timestamp is not None
                else case.ingestion_timestamp
            ),
        )
        assessment_provenance = {
            "runtime_schema_version": case.runtime_event_schema_version,
            "outcome_computation_version": (
                outcome.computation_version if outcome else None
            ),
            "source_replay_or_ingestion": dict(case.provenance),
        }
        assessment_provenance["dependency_fingerprint"] = _digest(
            _canonical_json(
                {
                    "case_id": case.case_id,
                    "runtime_event_id": case.runtime_event_id,
                    "canonical_payload_digest": recomputed_digest,
                    "outcome_record_id": outcome_id,
                    "outcome_horizon": horizon_minutes,
                    "outcome_computation_version": (
                        outcome.computation_version if outcome else None
                    ),
                    "label_policy_version": LABEL_POLICY_VERSION,
                    "validator_version": self.validator_version,
                    "review_status": review_status,
                    "review_id": governance.review_id,
                    "review_timestamp": (
                        governance.review_timestamp.isoformat()
                        if governance.review_timestamp is not None
                        else None
                    ),
                    "review_approved": governance.review_approved,
                    "review_not_required": (
                        governance.review_not_required
                    ),
                    "manually_excluded": manually_excluded,
                    "administratively_blocked": administratively_blocked,
                }
            )
        )
        assessment_id = build_readiness_assessment_id(
            case_id=case.case_id,
            runtime_event_id=case.runtime_event_id,
            validator_version=self.validator_version,
            canonical_payload_digest=recomputed_digest,
            outcome_record_id=outcome_id,
            label_policy_version=LABEL_POLICY_VERSION,
            review_status=review_status,
            manually_excluded=manually_excluded,
            administratively_blocked=administratively_blocked,
            provenance=assessment_provenance,
        )
        assessment = LearningReadinessAssessment(
            assessment_id=assessment_id,
            case_id=case.case_id,
            runtime_event_id=case.runtime_event_id,
            assessment_version=self.validator_version,
            assessment_timestamp=assessment_timestamp,
            readiness_status=status,
            evaluated_outcome_horizon=horizon_minutes,
            canonical_payload_digest=recomputed_digest,
            outcome_record_id=outcome_id,
            label_policy_version=LABEL_POLICY_VERSION,
            checks_performed=tuple(checks),
            failure_reasons=tuple(invalid + not_ready),
            warnings=tuple(warnings),
            validator_version=self.validator_version,
            review_status=review_status,
            technically_ready=technically_ready,
            approved_for_evaluation=approved_for_evaluation,
            approved_for_training=approved_for_training,
            manually_excluded=manually_excluded,
            administratively_blocked=administratively_blocked,
            provenance=assessment_provenance,
        )
        return assessment

    def assess_all(
        self, *, horizon_minutes: int = 60
    ) -> tuple[LearningReadinessAssessment, ...]:
        return tuple(
            self.assess(case.case_id, horizon_minutes=horizon_minutes)
            for case in self.repository.list_cases()
        )


def authorize_case_for_export(
    repository: SQLiteLearningCaseRepository,
    case_id: str,
    *,
    policy_name: str,
    horizon_minutes: int,
    validator_version: str = ACTIVE_READINESS_VALIDATOR,
    label_policy_version: str = LABEL_POLICY_VERSION,
) -> ExportAuthorization:
    if policy_name not in READINESS_POLICIES:
        raise ValueError(f"Unknown readiness policy: {policy_name}")
    if validator_version not in SUPPORTED_READINESS_VALIDATORS:
        return ExportAuthorization(False, "unsupported_validator", None, None)
    if label_policy_version != LABEL_POLICY_VERSION:
        return ExportAuthorization(False, "label_policy_mismatch", None, None)
    case = repository.get_case(case_id)
    if case is None:
        return ExportAuthorization(False, "invalid_case", None, None)
    assessments = repository.list_readiness_assessments(case_id)
    if not assessments:
        return ExportAuthorization(
            False, "missing_readiness_assessment", None, None
        )
    horizon_candidates = tuple(
        item
        for item in assessments
        if item.evaluated_outcome_horizon == horizon_minutes
    )
    if not horizon_candidates:
        return ExportAuthorization(False, "horizon_mismatch", None, None)
    validator_candidates = tuple(
        item
        for item in horizon_candidates
        if item.validator_version == validator_version
    )
    if not validator_candidates:
        return ExportAuthorization(False, "unsupported_validator", None, None)
    label_candidates = tuple(
        item
        for item in validator_candidates
        if item.label_policy_version == label_policy_version
    )
    if not label_candidates:
        return ExportAuthorization(False, "label_policy_mismatch", None, None)
    outcomes = repository.list_outcomes(case_id)
    outcome = next(
        (
            item
            for item in outcomes
            if item.horizon_minutes == horizon_minutes
        ),
        None,
    )
    current_digest = repository.case_payload_digest(case_id)
    outcome_candidates = tuple(
        item
        for item in label_candidates
        if item.outcome_record_id
        == (outcome.outcome_id if outcome is not None else None)
    )
    if not outcome_candidates:
        return ExportAuthorization(
            False, "stale_outcome", None, outcome
        )
    current_candidates = tuple(
        item
        for item in outcome_candidates
        if item.canonical_payload_digest == current_digest
    )
    if not current_candidates:
        return ExportAuthorization(
            False, "stale_case_digest", None, outcome
        )
    candidate = sorted(
        current_candidates,
        key=lambda item: (item.assessment_timestamp, item.assessment_id),
        reverse=True,
    )[0]
    try:
        expected = LearningReadinessService(
            repository, validator_version=validator_version
        ).derive_assessment(case_id, horizon_minutes=horizon_minutes)
    except (TypeError, ValueError):
        return ExportAuthorization(False, "invalid_case", candidate, outcome)
    if candidate != expected:
        return ExportAuthorization(
            False, "forged_readiness_assessment", candidate, outcome
        )
    if not candidate.technically_ready:
        return ExportAuthorization(
            False,
            (
                "invalid_case"
                if candidate.readiness_status is LearningReadinessStatus.INVALID
                else "technical_not_ready"
            ),
            candidate,
            outcome,
        )
    if candidate.manually_excluded:
        return ExportAuthorization(
            False, "manual_exclusion", candidate, outcome
        )
    if candidate.administratively_blocked:
        return ExportAuthorization(
            False, "administrative_block", candidate, outcome
        )
    if policy_name == "training" and not candidate.approved_for_training:
        return ExportAuthorization(
            False, "review_not_approved", candidate, outcome
        )
    if policy_name == "evaluation" and not candidate.approved_for_evaluation:
        return ExportAuthorization(
            False, "review_not_approved", candidate, outcome
        )
    return ExportAuthorization(True, "authorized", candidate, outcome)


def _runtime_payload_errors(case: LearningCase) -> tuple[str, ...]:
    errors: list[str] = []
    payload = case.to_dict()["runtime_event_payload"]
    if payload.get("persistence_schema_version") != CANONICAL_RUNTIME_EVENT_SCHEMA_VERSION:
        errors.append("unsupported persistence schema")
    event = payload.get("runtime_event")
    if not isinstance(event, dict):
        return ("canonical RuntimeEvent payload is missing",)
    if event.get("schema_version") != "runtime_event_v2":
        errors.append("unsupported RuntimeEvent schema")
    if event.get("runtime_status") != "completed":
        errors.append("RuntimeEvent is not COMPLETED")
    required = (
        "market_snapshot",
        "observation_package",
        "structural_evidence",
        "market_efficiency_evidence",
        "process_evidence",
        "process_quality_assessment",
        "hypothesis_package",
        "agent_state",
        "scenario_probability",
        "confidence_assessment",
        "decision_assessment",
    )
    if any(not isinstance(event.get(name), dict) for name in required):
        errors.append("required canonical RuntimeEvent section is missing")
        return tuple(errors)
    event_id = event.get("event_id")
    episode = event.get("episode_id")
    cycle = event.get("cycle_timestamp")
    market = (event.get("symbol"), event.get("exchange"), event.get("timeframe"))
    snapshot = event["market_snapshot"]
    observation = event["observation_package"]
    process = event["process_evidence"]
    quality = event["process_quality_assessment"]
    hypothesis = event["hypothesis_package"]
    scenario = event["scenario_probability"]
    confidence = event["confidence_assessment"]
    decision = event["decision_assessment"]
    structure = event["structural_evidence"]
    efficiency = event["market_efficiency_evidence"]
    agent_state = event["agent_state"]
    if event_id != case.runtime_event_id or episode != case.episode_id:
        errors.append("RuntimeEvent identity mismatch")
    if market != (case.symbol, case.exchange, case.timeframe):
        errors.append("RuntimeEvent market identity mismatch")
    if (
        (snapshot.get("symbol"), snapshot.get("exchange"), snapshot.get("timeframe"))
        != market
        or snapshot.get("timestamp") != cycle
        or observation.get("observation_timestamp") != cycle
        or process.get("observation_timestamp") != cycle
        or quality.get("current_observation", {}).get("observation_timestamp")
        != cycle
        or scenario.get("observation_timestamp") != cycle
        or scenario.get("created_at") != cycle
        or decision.get("created_at") != cycle
    ):
        errors.append("canonical temporal invariant mismatch")
    if process.get("runtime_event_id") != event_id or process.get("episode_id") != episode:
        errors.append("ProcessEvidence identity mismatch")
    for name, section in (
        ("ObservationPackage", observation),
        ("StructuralEvidence", structure),
        ("MarketEfficiencyEvidence", efficiency),
        ("HypothesisPackage", hypothesis),
        ("AgentState", agent_state),
        ("ConfidenceAssessment", confidence),
    ):
        if section.get("event_id") != event_id:
            errors.append(f"{name} event identity mismatch")
    if (process.get("symbol"), process.get("exchange"), process.get("timeframe")) != market:
        errors.append("ProcessEvidence market identity mismatch")
    if quality.get("runtime_event_id") != event_id or quality.get("episode_id") != episode:
        errors.append("ProcessQuality identity mismatch")
    if hypothesis.get("event_id") != event_id or hypothesis.get("episode_id") != episode:
        errors.append("Hypothesis identity mismatch")
    if scenario.get("runtime_event_id") != event_id or scenario.get("episode_id") != episode:
        errors.append("Scenario identity mismatch")
    expected_process_id = f"process-evidence:{episode}:{event_id}"
    if (
        scenario.get("source_process_evidence_id") != expected_process_id
        or scenario.get("source_process_quality_assessment_id")
        != quality.get("assessment_id")
        or scenario.get("source_hypothesis_id")
        != hypothesis.get("hypothesis_id")
    ):
        errors.append("Scenario source provenance mismatch")
    if confidence.get("event_id") != event_id or confidence.get("episode_id") != episode:
        errors.append("Confidence identity mismatch")
    if confidence.get("source_hypothesis_id") != hypothesis.get("hypothesis_id"):
        errors.append("Confidence source provenance mismatch")
    if decision.get("runtime_event_id") != event_id or decision.get("episode_id") != episode:
        errors.append("Decision identity mismatch")
    if (
        decision.get("hypothesis_reference") != hypothesis.get("hypothesis_id")
        or decision.get("scenario_probability_reference")
        != scenario.get("scenario_probability_id")
        or decision.get("process_evidence_reference") != expected_process_id
        or decision.get("confidence_reference")
        != f"confidence:{episode}:{event_id}"
        or decision.get("process_quality_reference", {}).get("assessment_id")
        != quality.get("assessment_id")
    ):
        errors.append("Decision source provenance mismatch")
    expected_schemas = {
        "market_snapshot": "1.0",
        "observation_package": "1.0",
        "structural_evidence": "1.0",
        "market_efficiency_evidence": "1.0",
        "process_evidence": "process_evidence_v2",
        "process_quality_assessment": "process_quality_assessment_v1",
        "hypothesis_package": "1.0",
        "agent_state": "agent_state_v2",
        "scenario_probability": "scenario_probability_v1",
        "confidence_assessment": "1.0",
        "decision_assessment": "decision_assessment_v1",
    }
    if any(
        event[name].get("schema_version") != schema
        for name, schema in expected_schemas.items()
    ):
        errors.append("unsupported canonical section schema")
    history = event.get("process_quality_history")
    if not isinstance(history, list) or not history:
        errors.append("ProcessQuality history is missing")
    else:
        timestamps = [
            item.get("current_observation", {}).get("observation_timestamp")
            for item in history
        ]
        try:
            parsed = tuple(datetime.fromisoformat(str(value)) for value in timestamps)
            if any(a >= b for a, b in zip(parsed, parsed[1:])) or timestamps[-1] != cycle:
                errors.append("ProcessQuality history chronology mismatch")
        except (TypeError, ValueError):
            errors.append("ProcessQuality history timestamp is invalid")
    for baseline_name in ("healthy_baseline_reference", "healthy_baseline_designation"):
        baseline = event.get(baseline_name)
        if baseline is not None:
            if baseline.get("episode_id") != episode:
                errors.append("Healthy Baseline identity mismatch")
            for key in ("creation_timestamp", "effective_after_assessment"):
                value = baseline.get(key)
                if isinstance(value, dict):
                    value = value.get("observation", value).get("observation_timestamp")
                if value is not None:
                    try:
                        if datetime.fromisoformat(str(value)) > datetime.fromisoformat(str(cycle)):
                            errors.append("Healthy Baseline temporal mismatch")
                    except ValueError:
                        errors.append("Healthy Baseline timestamp is invalid")
    return tuple(errors)


def _outcome_errors(case: LearningCase, outcome: OutcomeRecord) -> tuple[str, ...]:
    errors: list[str] = []
    if outcome.source_case_id != case.case_id or outcome.source_runtime_event_id != case.runtime_event_id:
        errors.append("outcome identity mismatch")
    if outcome.source_cycle_timestamp != case.cycle_timestamp:
        errors.append("outcome cycle mismatch")
    if dict(outcome.source_data_identity) != {
        "symbol": case.symbol,
        "exchange": case.exchange,
        "timeframe": case.timeframe,
    }:
        errors.append("outcome market identity mismatch")
    if outcome.schema_version != OUTCOME_RECORD_SCHEMA_VERSION:
        errors.append("unsupported outcome schema")
    if outcome.computation_version != OUTCOME_COMPUTATION_VERSION:
        errors.append("unsupported outcome computation")
    if not _all_finite(outcome.to_dict()):
        errors.append("non-finite outcome metric")
    if outcome.observation_start_timestamp is not None and outcome.observation_start_timestamp <= case.cycle_timestamp:
        errors.append("outcome observation start is not post-cycle")
    boundary = case.cycle_timestamp + timedelta(minutes=outcome.horizon_minutes)
    if outcome.observation_end_timestamp is not None and outcome.observation_end_timestamp > boundary:
        errors.append("outcome exceeds horizon boundary")
    if outcome.completeness_status is CompletenessStatus.COMPLETE and outcome.observation_end_timestamp != boundary:
        errors.append("complete outcome does not reach exact horizon boundary")
    return tuple(errors)


def _provenance_complete(case: LearningCase) -> bool:
    provenance = dict(case.provenance)
    runtime_version = provenance.get("runtime_version")
    source = (
        provenance.get("source")
        or provenance.get("replay_source")
        or provenance.get("ingestion_source")
    )
    return bool(runtime_version in SUPPORTED_RUNTIME_VERSIONS and source)


def _all_finite(value: object) -> bool:
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, Mapping):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return all(_all_finite(item) for item in value)
    return True


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
