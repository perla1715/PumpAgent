"""Deterministic JSONL learning dataset export."""

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
from pumpagent.runtime.domain.base import to_primitive


DATASET_EXPORT_SCHEMA_VERSION = "learning_dataset_export_v1"


def export_jsonl_dataset(
    repository: SQLiteLearningCaseRepository,
    output_path: str | Path,
    *,
    runtime_version: str,
) -> dict[str, Any]:
    cases = repository.list_dataset_eligible()
    rows: list[dict[str, Any]] = []
    horizons: set[int] = set()
    outcome_versions: set[str] = set()
    outcome_schema_versions: set[str] = set()
    runtime_schema_versions: set[str] = set()
    for case in cases:
        outcomes = repository.list_outcomes(case.case_id)
        horizons.update(item.horizon_minutes for item in outcomes)
        outcome_versions.update(item.computation_version for item in outcomes)
        outcome_schema_versions.update(item.schema_version for item in outcomes)
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
                "review_status": case.learning_metadata.review_status.value,
                "schema_versions": {
                    "learning_case": case.schema_version,
                    "runtime_event": case.runtime_event_schema_version,
                    "outcome": (
                        outcomes[0].schema_version if outcomes else None
                    ),
                },
                "runtime_version": runtime_version,
                "label_policy_version": LABEL_POLICY_VERSION,
                "outcome_computation_versions": sorted(
                    {item.computation_version for item in outcomes}
                ),
            }
        )
    rows.sort(key=lambda item: (item["cycle_timestamp"], item["case_id"]))
    canonical_rows = tuple(_canonical_json(row) for row in rows)
    content = "\n".join(canonical_rows) + ("\n" if canonical_rows else "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    all_cases = repository.list_cases()
    exclusions = Counter(
        reason
        for case in all_cases
        if case not in cases
        for reason in case.exclusion_reasons or ("not_dataset_eligible",)
    )
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
        "label_policy": LABEL_POLICY_VERSION,
        "outcome_horizons": sorted(horizons),
        "outcome_computation_versions": sorted(outcome_versions),
        "included_case_ids": [row["case_id"] for row in rows],
        "content_digest": digest,
        "exclusions_by_reason": dict(sorted(exclusions.items())),
    }
    target = Path(output_path)
    target.write_text(content, encoding="utf-8")
    target.with_suffix(target.suffix + ".manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}.")
