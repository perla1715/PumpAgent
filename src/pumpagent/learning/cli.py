"""Offline command-line entry points for the F-03 learning pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pumpagent.learning.domain import (
    SUPPORTED_HORIZONS_MINUTES,
    LearningReviewStatus,
    ReviewRecord,
)
from pumpagent.learning.export import export_jsonl_dataset
from pumpagent.learning.outcomes import OutcomeAttributionService
from pumpagent.learning.replay import HistoricalReplayRunner, ReplayConfig
from pumpagent.learning.readiness import LearningReadinessService
from pumpagent.learning.repository import SQLiteLearningCaseRepository
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        repository = SQLiteLearningCaseRepository(args.store)
        if args.command == "init":
            repository.initialize()
            print(json.dumps({"status": "initialized", "store": args.store}))
        elif args.command == "replay":
            repository.initialize()
            snapshots = _load_snapshots(args.input)
            config = ReplayConfig(
                symbol=args.symbol,
                exchange=args.exchange,
                timeframe=args.timeframe,
                start=_optional_time(args.start),
                end=_optional_time(args.end),
                runtime_version=args.runtime_version,
            )
            summary = HistoricalReplayRunner(repository).run(snapshots, config)
            print(json.dumps(summary.__dict__, sort_keys=True))
        elif args.command == "complete-outcomes":
            repository.initialize()
            observations = json.loads(Path(args.input).read_text(encoding="utf-8"))
            service = OutcomeAttributionService(repository)
            count = 0
            for case in repository.list_cases():
                applicable = tuple(
                    item
                    for item in observations
                    if _observation_applies_to_case(item, case)
                )
                for horizon in SUPPORTED_HORIZONS_MINUTES:
                    service.attribute(
                        case,
                        applicable,
                        horizon_minutes=horizon,
                        creation_timestamp=case.cycle_timestamp
                        + timedelta(minutes=horizon),
                    )
                    count += 1
            print(json.dumps({"outcomes_processed": count}))
        elif args.command == "counts":
            repository.initialize()
            counts: dict[str, int] = {}
            for case in repository.list_cases():
                counts[case.case_status.value] = counts.get(case.case_status.value, 0) + 1
            print(json.dumps(counts, sort_keys=True))
        elif args.command == "assess-readiness":
            repository.initialize()
            assessment = LearningReadinessService(
                repository, validator_version=args.validator_version
            ).assess(
                args.case_id, horizon_minutes=args.horizon
            )
            print(json.dumps(assessment.to_dict(), sort_keys=True))
        elif args.command == "assess-all":
            repository.initialize()
            assessments = LearningReadinessService(
                repository, validator_version=args.validator_version
            ).assess_all(
                horizon_minutes=args.horizon
            )
            print(json.dumps({"assessments": len(assessments)}))
        elif args.command == "readiness-counts":
            repository.initialize()
            counts: dict[str, int] = {}
            for case in repository.list_cases():
                assessment = repository.latest_readiness_assessment(case.case_id)
                status = (
                    assessment.readiness_status.value
                    if assessment is not None
                    else "unassessed"
                )
                counts[status] = counts.get(status, 0) + 1
            print(json.dumps(counts, sort_keys=True))
        elif args.command == "review-case":
            repository.initialize()
            reviewed_at = (
                _time(args.reviewed_at)
                if args.reviewed_at
                else datetime.now(timezone.utc)
            )
            identity = "|".join(
                (
                    args.case_id,
                    args.status,
                    reviewed_at.isoformat(),
                    args.reviewed_by,
                )
            )
            review = repository.record_review(
                ReviewRecord(
                    review_id=(
                        "learning-review:"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    ),
                    case_id=args.case_id,
                    review_status=args.status,
                    annotation=args.annotation,
                    tags=tuple(args.tag),
                    reviewed_by=args.reviewed_by,
                    reviewed_at=reviewed_at,
                )
            )
            governance = repository.current_governance(args.case_id)
            print(
                json.dumps(
                    {
                        "review": review.to_dict(),
                        "governance": governance.to_dict(),
                        "readiness_reassessment_required": True,
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "governance-state":
            repository.initialize()
            print(
                json.dumps(
                    repository.current_governance(args.case_id).to_dict(),
                    sort_keys=True,
                )
            )
        elif args.command == "explain-readiness":
            repository.initialize()
            assessment = repository.latest_readiness_assessment(args.case_id)
            if assessment is None:
                raise ValueError("Case has no readiness assessment.")
            print(
                json.dumps(
                    {
                        "case_id": assessment.case_id,
                        "status": assessment.readiness_status.value,
                        "checks": [
                            item.to_dict()
                            for item in assessment.checks_performed
                        ],
                        "failure_reasons": assessment.failure_reasons,
                        "warnings": assessment.warnings,
                        "approved_for_evaluation": (
                            assessment.approved_for_evaluation
                        ),
                        "approved_for_training": (
                            assessment.approved_for_training
                        ),
                        "manually_excluded": assessment.manually_excluded,
                        "administratively_blocked": (
                            assessment.administratively_blocked
                        ),
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "export":
            repository.initialize()
            manifest = export_jsonl_dataset(
                repository,
                args.output,
                runtime_version=args.runtime_version,
                readiness_policy=args.policy,
                horizon_minutes=args.horizon,
                validator_version=args.validator_version,
                label_policy_version=args.label_policy_version,
            )
            print(json.dumps(manifest, sort_keys=True))
        elif args.command == "validate":
            repository.initialize()
            valid, errors = repository.integrity_check()
            print(json.dumps({"valid": valid, "errors": errors}))
            return 0 if valid else 1
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pumpagent-learning")
    parser.add_argument("--store", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    replay = commands.add_parser("replay")
    replay.add_argument("--input", required=True)
    replay.add_argument("--symbol", required=True)
    replay.add_argument("--exchange", required=True)
    replay.add_argument("--timeframe", required=True)
    replay.add_argument("--start")
    replay.add_argument("--end")
    replay.add_argument("--runtime-version", required=True)
    outcomes = commands.add_parser("complete-outcomes")
    outcomes.add_argument("--input", required=True)
    commands.add_parser("counts")
    assess = commands.add_parser("assess-readiness")
    assess.add_argument("--case-id", required=True)
    assess.add_argument("--horizon", type=int, default=60)
    assess.add_argument(
        "--validator-version",
        default="learning_readiness_validator_v2",
    )
    assess_all = commands.add_parser("assess-all")
    assess_all.add_argument("--horizon", type=int, default=60)
    assess_all.add_argument(
        "--validator-version",
        default="learning_readiness_validator_v2",
    )
    commands.add_parser("readiness-counts")
    review = commands.add_parser("review-case")
    review.add_argument("--case-id", required=True)
    review.add_argument(
        "--status",
        required=True,
        choices=tuple(item.value for item in LearningReviewStatus),
    )
    review.add_argument("--reviewed-by", required=True)
    review.add_argument("--reviewed-at")
    review.add_argument("--annotation")
    review.add_argument("--tag", action="append", default=[])
    governance = commands.add_parser("governance-state")
    governance.add_argument("--case-id", required=True)
    explain = commands.add_parser("explain-readiness")
    explain.add_argument("--case-id", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output", required=True)
    export.add_argument("--runtime-version", required=True)
    export.add_argument(
        "--policy", choices=("evaluation", "training"), default="evaluation"
    )
    export.add_argument("--horizon", type=int, required=True)
    export.add_argument(
        "--validator-version",
        default="learning_readiness_validator_v2",
    )
    export.add_argument(
        "--label-policy-version",
        default="objective_outcome_labels_v1",
    )
    commands.add_parser("validate")
    return parser


def _load_snapshots(path: str) -> tuple[MarketSnapshot, ...]:
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise ValueError("Replay input must be a JSON array.")
    return tuple(_snapshot(value) for value in values)


def _snapshot(value: dict[str, object]) -> MarketSnapshot:
    return MarketSnapshot(
        event_id=str(value["event_id"]),
        timestamp=_time(value["timestamp"]),
        symbol=str(value["symbol"]),
        exchange=str(value["exchange"]),
        timeframe=str(value["timeframe"]),
        price=float(value["price"]),
        ohlcv=tuple(value["ohlcv"]),  # type: ignore[arg-type]
        volume=float(value["volume"]),
        data_source=str(value["data_source"]),
        data_quality_status=DataQualityStatus(str(value["data_quality_status"])),
        schema_version=str(value.get("schema_version", "1.0")),
        optional_market_metrics=value.get("optional_market_metrics", {}),  # type: ignore[arg-type]
        raw_payload_reference=value.get("raw_payload_reference"),  # type: ignore[arg-type]
        latency_ms=value.get("latency_ms"),  # type: ignore[arg-type]
        missing_fields=tuple(value.get("missing_fields", ())),  # type: ignore[arg-type]
    )


def _time(value: object) -> datetime:
    timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware.")
    return timestamp


def _optional_time(value: str | None) -> datetime | None:
    return None if value is None else _time(value)


def _observation_applies_to_case(value, case) -> bool:  # type: ignore[no-untyped-def]
    return (
        value.get("symbol") == case.symbol
        and value.get("exchange") == case.exchange
        and value.get("timeframe") == case.timeframe
        and _time(value.get("timestamp")) > case.cycle_timestamp
    )


if __name__ == "__main__":
    raise SystemExit(main())
