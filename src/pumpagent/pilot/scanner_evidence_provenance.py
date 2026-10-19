"""Frozen, replayable stage-one control evidence for Pilot Scanner packages.

This module is presentation/provenance only.  It neither selects candidates
nor changes the canonical Pilot profile evaluator.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.pilot.pilot_search_profile import CanonicalProfileEvaluation, PriceObservation, evaluate_canonical_profile
from pumpagent.pilot.runtime_handoff import PilotDiscoveryPreparation, StageOneEvidenceRecord


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    """Stable UTF-8 JSON for evidence identity; never includes credentials."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def evidence_payload(
    record: StageOneEvidenceRecord,
    *,
    profile: CanonicalProfileEvaluation | None,
    rank_snapshot: RankSnapshot,
) -> dict[str, Any]:
    """Serialize exact normalized stage-one evidence and its coverage facts."""
    resolution = rank_snapshot.resolution_for(record.symbol[:-4])
    entry = resolution.entry
    candles = []
    if record.batch is not None:
        candles = [
            {
                "timestamp_utc": _iso(candle.timestamp_utc),
                "open": candle.open, "high": candle.high, "low": candle.low,
                "close": candle.close, "volume": candle.volume,
                "timeframe": candle.timeframe, "closed": candle.closed,
            }
            for candle in record.batch.candles
        ]
    return {
        "schema_version": "pilot-stage1-frozen-evidence/v1",
        "symbol": record.symbol.upper(),
        "provider": "coinalyze",
        "provider_symbol": record.provider_symbol,
        "timeframe": "1m",
        "cohort_evaluation_timestamp_utc": _iso(record.coverage.evaluation_timestamp_utc),
        "terminal_state": record.terminal_state,
        "failure_reason": record.failure_reason,
        "coverage": {
            "requested_start_utc": _iso(record.coverage.requested_start_utc),
            "evaluation_timestamp_utc": _iso(record.coverage.evaluation_timestamp_utc),
            "returned_record_count": record.coverage.returned_record_count,
            "admissible_closed_bucket_count": record.coverage.admissible_closed_bucket_count,
            "expected_required_bucket_count": record.coverage.expected_required_bucket_count,
            "first_admissible_bucket_utc": _optional_iso(record.coverage.first_admissible_bucket_utc),
            "last_admissible_bucket_utc": _optional_iso(record.coverage.last_admissible_bucket_utc),
            "missing_bucket_timestamps_utc": [_iso(value) for value in record.coverage.missing_bucket_timestamps_utc],
            "complete_for_profile": record.coverage.complete_for_profile,
        },
        "source": {
            "raw_source_identity": record.coverage.raw_source_identity,
            "raw_sha256": record.coverage.raw_sha256,
        },
        "rank_snapshot": {
            "source": rank_snapshot.source,
            "coverage_complete": rank_snapshot.coverage_complete,
            "page_count": rank_snapshot.page_count,
            "endpoint_pages": list(rank_snapshot.endpoint_pages),
            "fetched_at_utc": _iso(rank_snapshot.fetched_at),
            "snapshot_id": rank_snapshot.snapshot_id,
            "resolved_entry": None if entry is None else {
                "asset_id": entry.asset_id, "symbol": entry.symbol, "rank": entry.rank,
            },
            "resolution": {
                "state": resolution.state, "method": resolution.method, "reason": resolution.reason,
            },
        },
        "candles": candles,
        "canonical_evaluation": None if profile is None else _profile_dict(profile),
    }


def evidence_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def replay_canonical_profile(payload: Mapping[str, Any]) -> CanonicalProfileEvaluation:
    """Offline replay using the production canonical evaluator, never formulas here."""
    rank = payload["rank_snapshot"]
    entry = rank["resolved_entry"]
    if entry is None:
        raise ValueError("artifact has no resolved rank entry for replay")
    snapshot = RankSnapshot(
        str(rank["source"]), _parse_utc(str(rank["fetched_at_utc"])),
        (RankEntry(str(entry["asset_id"]), str(entry["symbol"]), int(entry["rank"])),), str(rank["snapshot_id"]),
    )
    observations = tuple(
        PriceObservation(_parse_utc(str(row["timestamp_utc"])), float(row["close"]))
        for row in payload["candles"]
        if row.get("closed") and row.get("timeframe") == "1m"
    )
    return evaluate_canonical_profile(
        symbol=str(payload["symbol"]), observations=observations,
        evaluation_timestamp_utc=_parse_utc(str(payload["cohort_evaluation_timestamp_utc"])),
        rank_snapshot=snapshot,
    )


def write_stage_one_evidence_bundle(directory: str | Path, preparation: PilotDiscoveryPreparation) -> dict[str, Any]:
    """Write deterministic per-symbol evidence, index, and refreshed package manifest."""
    if preparation.rank_snapshot is None:
        raise ValueError("PilotDiscoveryPreparation must retain the rank snapshot for evidence replay")
    root = Path(directory)
    evidence_dir = root / "stage1_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    profiles = {
        screen.instrument.symbol: screen.profile
        for screen in (*preparation.stage1_non_survivors, *preparation.stage1_survivors)
    }
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(preparation.stage1_evidence, key=lambda item: item.symbol):
        if record.symbol in seen:
            raise ValueError("stage-one evidence has duplicate symbols")
        seen.add(record.symbol)
        payload = evidence_payload(record, profile=profiles.get(record.symbol), rank_snapshot=preparation.rank_snapshot)
        digest = evidence_digest(payload)
        relative = f"stage1_evidence/{record.symbol.upper()}.json"
        (root / relative).write_bytes(canonical_json(payload))
        files.append({
            "symbol": record.symbol.upper(), "terminal_state": record.terminal_state,
            "evidence_reference": relative, "evidence_sha256": digest,
            "coverage": payload["coverage"],
            "canonical_evaluation_present": payload["canonical_evaluation"] is not None,
            "failure_reason": record.failure_reason,
        })
    if set(preparation.eligible_symbols) != seen:
        raise ValueError("every final eligible symbol must have exactly one stage-one evidence index entry")
    index = {
        "schema_version": "pilot-stage1-control-index/v1",
        "cohort_evaluation_timestamp_utc": _optional_iso(preparation.cohort_evaluation_timestamp_utc),
        "eligible_symbols": list(preparation.eligible_symbols),
        "entries": files,
    }
    index_path = root / "15_STAGE1_EVIDENCE_INDEX.json"
    index_path.write_bytes(canonical_json(index))
    manifest_digest = refresh_package_manifest(root)
    return {"index_reference": index_path.name, "entries": files, "package_manifest_sha256": manifest_digest}


def refresh_package_manifest(directory: str | Path) -> str:
    """Refresh the existing path/size/SHA-256 package manifest for future packages."""
    root = Path(directory)
    manifest = root / "MANIFEST.sha256"
    lines = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item != manifest):
        relative = path.relative_to(root).as_posix()
        lines.append(f"{relative}\t{path.stat().st_size}\t{hashlib.sha256(path.read_bytes()).hexdigest()}")
    manifest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _profile_dict(profile: CanonicalProfileEvaluation) -> dict[str, Any]:
    raw = asdict(profile)
    return {key: _optional_iso(value) if isinstance(value, datetime) or value is None and key.endswith("_utc") else value for key, value in raw.items()}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _optional_iso(value: datetime | None) -> str | None:
    return None if value is None else _iso(value)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("artifact timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)
