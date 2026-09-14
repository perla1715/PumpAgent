"""Strict point-in-time input mapping and immutable canonical checkpoint records."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re

from pumpagent.live_data.domain import (
    LiveDataQualityStatus, LiveDataTransport, NormalizedMarketDataInput, SourceMetadata,
)
from pumpagent.live_data.validation.validator import validate_normalized_market_data_input

INPUT_SCHEMA = "pilot_recorded_snapshot_v1"
RECORD_SCHEMA = "pilot_checkpoint_record_v1"
REQUIRED_METRICS = ("price", "volume", "open", "high", "low", "close")
OPTIONAL_METRICS = ("open_interest", "oi_change_pct", "cvd", "funding_rate", "liquidations")
IDENTITY_FIELDS = (
    "run_id", "event_id", "symbol", "provider", "market_id", "contract_id",
    "timeframe", "candle_open_time", "candle_close_time",
)


class ValidationError(ValueError):
    """Input does not satisfy the closed Pilot v1 schema."""


def canonical(value: object) -> bytes:
    """V1 canonical UTF-8 JSON: sorted keys, compact separators, no NaN, one LF."""
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValidationError("Value is not canonical JSON.") from exc


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _object(value: object, keys: tuple, name: str) -> None:
    if type(value) is not dict or set(value) != set(keys):
        raise ValidationError(f"{name}: exact fields required: {', '.join(keys)}")


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValidationError(f"{name}: nonempty trimmed text required.")


def timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValidationError("Timestamp must be an explicit timezone-aware ISO string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("Invalid timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("Naive timestamps are forbidden.")
    return parsed.astimezone(timezone.utc)


def _time_text(value: object) -> str:
    return timestamp(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValidationError("A lowercase SHA-256 digest is required.")


@dataclass(frozen=True)
class PilotCheckpointRecordV1:
    """Immutable canonical bytes; to_dict returns a detached editable copy."""
    canonical_bytes: bytes

    def to_dict(self) -> dict:
        return json.loads(self.canonical_bytes)

    @property
    def checkpoint_id(self) -> str:
        return self.to_dict()["checkpoint_id"]


@dataclass(frozen=True)
class MappedCheckpoint:
    canonical_input: bytes
    checkpoint_id: str

    def to_dict(self) -> dict:
        return json.loads(self.canonical_input)


def map_recorded_snapshot(value: dict) -> MappedCheckpoint:
    """Pure strict mapping: preserve supplied metrics/assessments, infer nothing."""
    _object(value, (
        "schema_version", "run_id", "event_id", "symbol", "provider", "market_id",
        "contract_id", "source_evidence", "timeframe", "candle_open_time",
        "candle_close_time", "provider_event_time", "received_time", "recorded_time",
        "normalized_values", "quality", "previous_checkpoint_id", "assessments", "versions",
    ), "recorded snapshot")
    # Copy through strict serialization so callers cannot mutate the mapped input.
    data = json.loads(canonical(value))
    if data["schema_version"] != INPUT_SCHEMA:
        raise ValidationError("Unsupported input schema version.")
    for field in ("run_id", "event_id", "symbol", "provider", "market_id", "contract_id"):
        _text(data[field], field)
    _object(data["source_evidence"], ("id", "sha256"), "source evidence")
    _text(data["source_evidence"]["id"], "source evidence id")
    _sha(data["source_evidence"]["sha256"])
    _object(data["versions"], ("code", "config", "schema"), "versions")
    for key, item in data["versions"].items():
        _text(item, key)
    if data["versions"]["schema"] != RECORD_SCHEMA:
        raise ValidationError("Unsupported checkpoint schema version.")
    for field in ("candle_open_time", "candle_close_time", "received_time", "recorded_time"):
        data[field] = _time_text(data[field])
    opened, closed, received, recorded = (
        timestamp(data[f]) for f in
        ("candle_open_time", "candle_close_time", "received_time", "recorded_time")
    )
    interval = re.fullmatch(r"([1-9][0-9]*)(s|m|h|d)", str(data["timeframe"]))
    if not interval:
        raise ValidationError("Explicit timeframe duration required, e.g. 1m.")
    seconds = int(interval[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[interval[2]]
    try:
        duration = timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValidationError("Timeframe exceeds supported datetime range.") from exc
    if closed - opened != duration or not closed <= received <= recorded:
        raise ValidationError("Invalid candle interval or point-in-time ordering.")
    provider_time = data["provider_event_time"]
    if type(provider_time) is not dict:
        raise ValidationError("provider_event_time requires explicit availability.")
    if provider_time.get("status") == "UNAVAILABLE":
        _object(provider_time, ("status", "reason"), "provider_event_time")
        _text(provider_time["reason"], "unavailable timestamp reason")
    else:
        _object(provider_time, ("status", "value"), "provider_event_time")
        if provider_time["status"] != "AVAILABLE":
            raise ValidationError("Invalid timestamp availability.")
        provider_time["value"] = _time_text(provider_time["value"])
        if timestamp(provider_time["value"]) > recorded:
            raise ValidationError("Future provider event time.")
    values = data["normalized_values"]
    _object(values, REQUIRED_METRICS + OPTIONAL_METRICS, "normalized values")
    missing = []
    for name, metric in values.items():
        if type(metric) is not dict:
            raise ValidationError(f"{name}: explicit metric object required.")
        if metric.get("status") == "UNAVAILABLE":
            _object(metric, ("status", "reason"), name)
            _text(metric["reason"], "missing metric reason")
            if name in REQUIRED_METRICS:
                raise ValidationError(f"Required metric {name} unavailable.")
            missing.append(name)
            continue
        _object(metric, ("status", "value", "unit", "provenance"), name)
        if metric["status"] != "AVAILABLE" or type(metric["value"]) not in (int, float):
            raise ValidationError(f"{name}: finite numeric metric required.")
        try:
            finite = math.isfinite(metric["value"])
        except OverflowError:
            finite = False
        if not finite:
            raise ValidationError(f"{name}: non-finite metric.")
        _text(metric["unit"], "metric unit")
        provenance = metric["provenance"]
        _object(provenance, ("source_evidence_id", "source_field", "kind", "derivation_version"), name)
        for field, item in provenance.items():
            _text(item, field)
        if provenance["kind"] not in ("OBSERVED", "DERIVED"):
            raise ValidationError("Invalid metric provenance kind.")
        if provenance["source_evidence_id"] != data["source_evidence"]["id"]:
            raise ValidationError("Metric source evidence mismatch.")
    quality = data["quality"]
    _object(quality, ("status", "missing_metrics", "reasons", "warnings"), "quality")
    try:
        quality_status = LiveDataQualityStatus(quality["status"])
    except (ValueError, TypeError) as exc:
        raise ValidationError("Invalid quality status.") from exc
    for field in ("missing_metrics", "reasons", "warnings"):
        if type(quality[field]) is not list:
            raise ValidationError("Quality flags must be explicit lists.")
        for item in quality[field]:
            _text(item, field)
        if len(set(quality[field])) != len(quality[field]):
            raise ValidationError("Duplicate quality flags.")
    if set(quality["missing_metrics"]) != set(missing):
        raise ValidationError("Quality missingness does not match metrics.")
    if quality_status != LiveDataQualityStatus.GOOD and not quality["reasons"]:
        raise ValidationError("Non-good quality needs an explicit reason.")
    _object(data["assessments"], ("geometry_state", "counter_thesis_state", "alert_state"), "assessments")
    for field, state in data["assessments"].items():
        _text(state, field)  # Opaque supplied labels, including UNASSESSED; no interpretation.
    previous = data["previous_checkpoint_id"]
    if previous is not None:
        if not isinstance(previous, str) or not previous.startswith("pc1:"):
            raise ValidationError("Invalid previous checkpoint ID.")
        _sha(previous[4:])
    numbers = {k: values[k]["value"] for k in REQUIRED_METRICS}
    snapshot = NormalizedMarketDataInput(
        source_event_id=data["source_evidence"]["id"], symbol=data["symbol"],
        exchange=data["market_id"], timeframe=data["timeframe"],
        source_timestamp=closed, receive_timestamp=received,
        price=numbers["price"], volume=numbers["volume"],
        ohlcv=({"timestamp": opened, **{k: numbers[k] for k in ("open", "high", "low", "close", "volume")}},),
        data_source=data["provider"], quality_status=quality_status,
        source_metadata=SourceMetadata(
            exchange=data["market_id"], adapter_name="pilot_recorded_input",
            adapter_version=INPUT_SCHEMA, source_timestamp=closed, receive_timestamp=received,
            transport=LiveDataTransport.REPLAY, source_symbol=data["contract_id"],
            normalized_symbol=data["symbol"], source_timeframe=data["timeframe"],
            normalized_timeframe=data["timeframe"],
        ),
    )
    validation = validate_normalized_market_data_input(snapshot)
    if not validation.is_valid:
        raise ValidationError("; ".join(validation.errors))
    identity = {field: data[field] for field in IDENTITY_FIELDS}
    return MappedCheckpoint(canonical(data), "pc1:" + digest(identity))


def build_record(mapped: MappedCheckpoint, sequence: int, committed_at: str) -> PilotCheckpointRecordV1:
    if type(sequence) is not int or sequence < 1:
        raise ValidationError("Commit sequence must be a positive integer.")
    # Revalidate even manually constructed MappedCheckpoint instances.
    checked = map_recorded_snapshot(mapped.to_dict())
    if checked != mapped:
        raise ValidationError("Mapped checkpoint integrity mismatch.")
    committed_at = _time_text(committed_at)
    if timestamp(committed_at) < timestamp(mapped.to_dict()["recorded_time"]):
        raise ValidationError("Commit time precedes recorded time.")
    body = {"schema_version": RECORD_SCHEMA, "checkpoint_id": mapped.checkpoint_id,
            "input": mapped.to_dict(), "commit_sequence": sequence, "committed_at": committed_at}
    return PilotCheckpointRecordV1(canonical({**body, "canonical_digest": digest(body)}))


def decode_record(raw: bytes) -> PilotCheckpointRecordV1:
    try:
        data = json.loads(raw)
        _object(data, ("schema_version", "checkpoint_id", "input", "commit_sequence", "committed_at", "canonical_digest"), "record")
        record = build_record(map_recorded_snapshot(data["input"]), data["commit_sequence"], data["committed_at"])
        if record.canonical_bytes != raw:
            raise ValidationError("Noncanonical record or digest/identity/schema mismatch.")
        return record
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValidationError("Invalid checkpoint record: " + str(exc)) from exc
