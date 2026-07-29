"""Deterministic JSONL export through authenticated readiness authorization."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pumpagent.learning.domain import LEARNING_CASE_SCHEMA_VERSION
from pumpagent.learning.labels import LABEL_POLICY_VERSION, label_outcome
from pumpagent.learning.repository import SQLiteLearningCaseRepository
from pumpagent.learning.readiness import (
    ACTIVE_READINESS_VALIDATOR,
    READINESS_POLICIES,
    authorize_case_for_export,
)
from pumpagent.runtime.domain.base import to_primitive


DATASET_EXPORT_SCHEMA_VERSION = "learning_dataset_export_v1"


def export_jsonl_dataset(
    repository: SQLiteLearningCaseRepository,
    output_path: str | Path,
    *,
    runtime_version: str,
    horizon_minutes: int,
    readiness_policy: str = "evaluation",
    validator_version: str = ACTIVE_READINESS_VALIDATOR,
    label_policy_version: str = LABEL_POLICY_VERSION,
) -> dict[str, Any]:
    if readiness_policy not in READINESS_POLICIES:
        raise ValueError(f"Unknown readiness policy: {readiness_policy}")
    all_cases = repository.list_cases()
    authorizations = {
        case.case_id: authorize_case_for_export(
            repository,
            case.case_id,
            policy_name=readiness_policy,
            horizon_minutes=horizon_minutes,
            validator_version=validator_version,
            label_policy_version=label_policy_version,
        )
        for case in all_cases
    }
    cases = tuple(
        case
        for case in all_cases
        if authorizations[case.case_id].authorized
    )
    rows: list[dict[str, Any]] = []
    horizons: set[int] = set()
    outcome_versions: set[str] = set()
    outcome_schema_versions: set[str] = set()
    runtime_schema_versions: set[str] = set()
    for case in cases:
        authorization = authorizations[case.case_id]
        assessment = authorization.assessment
        outcome = authorization.outcome
        assert assessment is not None and outcome is not None
        stored_runtime_version = case.provenance.get("runtime_version")
        if stored_runtime_version != runtime_version:
            raise ValueError(
                "Export Runtime version does not match case provenance."
            )
        outcomes = (outcome,)
        horizons.add(outcome.horizon_minutes)
        outcome_versions.add(outcome.computation_version)
        outcome_schema_versions.add(outcome.schema_version)
        runtime_schema_versions.add(case.runtime_event_schema_version)
        rows.append(
            {
                "case_id": case.case_id,
                "runtime_event_id": case.runtime_event_id,
                "market_identity": {
                    "symbol": case.symbol,
                    "exchange": case.exchange,
                    "timeframe": case.timeframe,
                },
                "cycle_timestamp": case.cycle_timestamp.isoformat(),
                "episode_id": case.episode_id,
                "runtime_event": to_primitive(
                    case.runtime_event_payload["runtime_event"]
                ),
                "outcomes": [item.to_dict() for item in outcomes],
                "labels": [label_outcome(item).__dict__ for item in outcomes],
                "review_status": assessment.review_status,
                "schema_versions": {
                    "learning_case": case.schema_version,
                    "runtime_event": case.runtime_event_schema_version,
                    "outcome": (
                        outcomes[0].schema_version if outcomes else None
                    ),
                },
                "runtime_version": stored_runtime_version,
                "label_policy_version": LABEL_POLICY_VERSION,
                "outcome_computation_versions": sorted(
                    {item.computation_version for item in outcomes}
                ),
                "readiness_assessment_id": assessment.assessment_id,
                "readiness_validator_version": assessment.validator_version,
                "readiness_horizon_minutes": (
                    assessment.evaluated_outcome_horizon
                ),
                "canonical_payload_digest": (
                    assessment.canonical_payload_digest
                ),
                "readiness_dependency_fingerprint": (
                    assessment.provenance["dependency_fingerprint"]
                ),
                "authoritative_outcome_id": outcome.outcome_id,
                "authoritative_outcome_computation_version": (
                    outcome.computation_version
                ),
                "readiness_policy": readiness_policy,
                "authorization_version": "dataset_authorization_v1",
            }
        )
    rows.sort(key=lambda item: (item["cycle_timestamp"], item["case_id"]))
    canonical_rows = tuple(_canonical_json(row) for row in rows)
    content = "\n".join(canonical_rows) + ("\n" if canonical_rows else "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    exclusions: Counter[str] = Counter()
    for case in all_cases:
        if case in cases:
            continue
        exclusions[authorizations[case.case_id].reason_code] += 1
    timestamps = [case.cycle_timestamp for case in cases]
    created_at = (
        max(case.ingestion_timestamp for case in cases)
        if cases
        else datetime(1970, 1, 1, tzinfo=timezone.utc)
    )
    manifest = {
        "schema_version": DATASET_EXPORT_SCHEMA_VERSION,
        "export_id": f"learning-export:{digest}",
        "creation_timestamp": created_at.isoformat(),
        "case_count": len(rows),
        "symbols": sorted({case.symbol for case in cases}),
        "exchanges": sorted({case.exchange for case in cases}),
        "timeframes": sorted({case.timeframe for case in cases}),
        "source_date_range": {
            "start": min(timestamps).isoformat() if timestamps else None,
            "end": max(timestamps).isoformat() if timestamps else None,
        },
        "runtime_version": runtime_version,
        "schema_versions": {
            "learning_case": LEARNING_CASE_SCHEMA_VERSION,
            "dataset_export": DATASET_EXPORT_SCHEMA_VERSION,
            "runtime_events": sorted(runtime_schema_versions),
            "outcomes": sorted(outcome_schema_versions),
        },
        "label_policy": label_policy_version,
        "readiness_policy": readiness_policy,
        "readiness_validator_version": validator_version,
        "readiness_horizon_minutes": horizon_minutes,
        "outcome_horizons": sorted(horizons),
        "outcome_computation_versions": sorted(outcome_versions),
        "included_case_ids": [row["case_id"] for row in rows],
        "content_digest": digest,
        "exclusions_by_reason": dict(sorted(exclusions.items())),
    }
    target = Path(output_path)
    target.write_text(content, encoding="utf-8")
    target.with_suffix(target.suffix + ".manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}.")
