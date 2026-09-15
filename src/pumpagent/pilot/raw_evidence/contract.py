"""Canonical provenance and conservative validation for local Coinalyze bytes."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = "coinalyze-raw-evidence/v1"


class Status(str, Enum):
    ARCHIVED = "ARCHIVED"
    DUPLICATE_NOOP = "DUPLICATE_NOOP"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    CORRUPTION_ERROR = "CORRUPTION_ERROR"


class InvalidCapture(ValueError):
    """Messages deliberately omit supplied values, which may contain secrets."""


REQUIRED = frozenset({
    "archive_schema_version", "provider", "provider_api_version_or_unavailable_reason",
    "endpoint_identity", "http_method", "request_parameters", "request_started_at",
    "response_received_at", "recorded_at", "market_identity", "exchange_identity",
    "contract_identity", "requested_symbol", "requested_interval",
    "http_status_or_transport_error", "content_type_or_unavailable_reason",
    "raw_response_bytes", "capture_code_version", "capture_config_version",
    "capture_status", "quality_flags",
})
CAPTURE_STATUSES = frozenset({
    "VALID_RESPONSE", "MALFORMED_RESPONSE", "INCOMPLETE_CAPTURE", "PROVIDER_ERROR",
})
QUALITY_FLAGS = frozenset({
    "COMPLETENESS_NOT_ASSERTED", "MISSING_RECORDS", "GAPS", "DUPLICATE_RECORDS",
    "CONFLICTING_DUPLICATES", "OPEN_OR_INCOMPLETE_CANDLES", "UNAVAILABLE_METRICS",
    "LATE_RECORDS",
})
PARAMETERS = frozenset({"symbol", "symbols", "interval", "from", "to",
                        "convert_to_usd", "exchange", "contract"})
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")
SECRET_KEY = re.compile(r"(?:apikey|authorization|authentication|password|passwd|secret|token|cookie|credential|privatekey)", re.I)
SECRET_TEXT = re.compile(
    r"(?:\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]+|-----BEGIN .*PRIVATE KEY|"
    r"\b(?:api[_-]?key|authorization|password|secret|token|cookie)\s*[=:])", re.I,
)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe(value: Any, *, metadata: bool = True) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or SECRET_KEY.search(re.sub(r"[^a-zA-Z]", "", key)):
                raise InvalidCapture("Credential-bearing or invalid metadata key")
            _safe(item, metadata=metadata)
    elif isinstance(value, list):
        for item in value:
            _safe(item, metadata=metadata)
    elif isinstance(value, str):
        if SECRET_TEXT.search(value) or (metadata and (
            len(value) > 4096 or any(ord(c) < 32 for c in value)
        )):
            raise InvalidCapture("Unsafe metadata text")
    elif value is not None and type(value) not in (bool, int, float):
        raise InvalidCapture("Metadata must contain JSON values")


def _token(value: Any) -> None:
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise InvalidCapture("Invalid identity or version token")


def timestamp(value: Any) -> str:
    if not isinstance(value, str) or not ISO.fullmatch(value):
        raise InvalidCapture("Timestamp must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # datetime accepts offsets such as +00:60; the contract does not.
        if value[-1:] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise InvalidCapture("Invalid timestamp") from None


def _available(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"value", "unavailable_reason"}:
        raise InvalidCapture("Optional provenance needs a value or explicit unavailable reason")
    present, reason = value["value"], value["unavailable_reason"]
    if not ((isinstance(present, str) and bool(present.strip()) and reason is None)
            or (present is None and isinstance(reason, str) and bool(reason.strip()))):
        raise InvalidCapture("Optional provenance needs exactly one value or reason")


def _json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    def invalid_constant(_: str) -> None:
        raise ValueError("Non-finite JSON constant")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Non-finite JSON number")
        return parsed

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant,
                      parse_float=finite_float)


def _payload(provenance: dict[str, Any], raw: bytes) -> None:
    status = provenance["capture_status"]
    flags = set(provenance["quality_flags"])
    # Reject recognizable credentials even in an error body, without storing it.
    text = raw.decode("utf-8", errors="replace")
    if SECRET_TEXT.search(text) or re.search(
        r'"(?:api[_-]?key|authorization|password|secret|token|cookie)"\s*:', text, re.I
    ):
        raise InvalidCapture("Credential-bearing response")
    try:
        body = _json(raw)
    except (ValueError, UnicodeError, RecursionError):
        # Syntax failure could be truncation; never label it an ordinary malformed
        # but complete response. The caller must identify an incomplete/error capture.
        if status not in {"INCOMPLETE_CAPTURE", "PROVIDER_ERROR"}:
            raise InvalidCapture("Undecodable/truncated response requires incomplete or provider-error status")
        return

    _safe(body, metadata=False)
    def check_identity(value: Any) -> None:
        if isinstance(value, dict):
            for key, expected in (("symbol", "requested_symbol"), ("exchange", "exchange_identity"),
                                  ("contract", "contract_identity")):
                if key in value and value[key] != provenance[expected]:
                    raise InvalidCapture("Payload identity conflicts with explicit provenance")
            for item in value.values():
                check_identity(item)
        elif isinstance(value, list):
            for item in value:
                check_identity(item)
    check_identity(body)
    malformed = False
    observed: set[str] = set()
    seen: dict[int, bytes] = {}
    times: list[int] = []
    if not isinstance(body, list):
        malformed = True
        groups = [body] if isinstance(body, dict) else []
    else:
        groups = body
    if not groups:
        observed.add("MISSING_RECORDS")
    for group in groups:
        if not isinstance(group, dict):
            malformed = True
            continue
        history = group.get("history")
        if "symbol" not in group or not isinstance(history, list):
            malformed = True
            continue
        if not history:
            observed.add("MISSING_RECORDS")
        for record in history:
            if not isinstance(record, dict):
                malformed = True
                continue
            t = record.get("t")
            if type(t) is not int or t < 0:
                malformed = True
                continue
            # Comparison only; raw bytes are the sole stored market-data object.
            encoded = json.dumps(record, sort_keys=True, ensure_ascii=True,
                                 separators=(",", ":"), allow_nan=False).encode("ascii")
            if t in seen:
                observed.add("DUPLICATE_RECORDS")
                if seen[t] != encoded:
                    observed.add("CONFLICTING_DUPLICATES")
            else:
                seen[t] = encoded
                times.append(t)
            if any(value is None for key, value in record.items() if key != "t"):
                observed.add("UNAVAILABLE_METRICS")
            if record.get("closed") is False or record.get("complete") is False:
                observed.add("OPEN_OR_INCOMPLETE_CANDLES")
        # Examine timestamps only for a quality marker; never change stored records.
        if provenance["requested_interval"] in {"1m", "1min"}:
            ordered = sorted(times)
            if any(b - a > 60 for a, b in zip(ordered, ordered[1:])):
                observed.update({"GAPS", "MISSING_RECORDS"})
    if malformed and status != "MALFORMED_RESPONSE" and status not in {"PROVIDER_ERROR", "INCOMPLETE_CAPTURE"}:
        raise InvalidCapture("Malformed payload requires explicit non-valid status")
    # Invalid provider timestamps are specifically MALFORMED_RESPONSE, including
    # captures otherwise described as transport errors/incomplete.
    if any(isinstance(g, dict) and isinstance(g.get("history"), list)
           and any(isinstance(r, dict) and (type(r.get("t")) is not int or r["t"] < 0)
                   for r in g["history"]) for g in groups):
        if status != "MALFORMED_RESPONSE":
            raise InvalidCapture("Malformed provider timestamp requires MALFORMED_RESPONSE")
    if not observed <= flags:
        raise InvalidCapture("Explicit quality flags must describe observed payload defects")


def validate(capture: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    if not isinstance(capture, dict) or set(capture) != REQUIRED:
        raise InvalidCapture("Capture fields must exactly match the v1 contract")
    raw = capture["raw_response_bytes"]
    if type(raw) is not bytes:
        raise InvalidCapture("raw_response_bytes must be bytes")
    source = {k: v for k, v in capture.items() if k != "raw_response_bytes"}
    _safe(source)
    try:
        # Snapshot caller-owned metadata; canonicalization also rejects NaN/Infinity.
        p = json.loads(canonical(source))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise InvalidCapture("Metadata is not canonical JSON") from None
    if p["archive_schema_version"] != SCHEMA_VERSION or p["provider"] != "coinalyze":
        raise InvalidCapture("Unsupported schema or provider")
    if p["http_method"] != "GET" or not isinstance(p["endpoint_identity"], str) or not re.fullmatch(r"/[A-Za-z0-9/_-]+", p["endpoint_identity"]):
        raise InvalidCapture("Only explicit GET endpoint paths without queries are accepted")
    for key in ("market_identity", "exchange_identity", "contract_identity", "requested_symbol",
                "requested_interval", "capture_code_version", "capture_config_version"):
        _token(p[key])
    for key in ("provider_api_version_or_unavailable_reason", "content_type_or_unavailable_reason"):
        _available(p[key])
    for key in ("request_started_at", "response_received_at", "recorded_at"):
        p[key] = timestamp(p[key])
    if not p["request_started_at"] <= p["response_received_at"] <= p["recorded_at"]:
        raise InvalidCapture("Capture timestamps are reversed")
    params = p["request_parameters"]
    if not isinstance(params, dict) or not set(params) <= PARAMETERS:
        raise InvalidCapture("Request parameters contain unsupported or secret fields")
    if ("symbol" in params) == ("symbols" in params) or "interval" not in params:
        raise InvalidCapture("Exactly one request symbol and explicit interval are required")
    for key, expected in (("symbol", "requested_symbol"), ("symbols", "requested_symbol"),
                          ("interval", "requested_interval"), ("exchange", "exchange_identity"),
                          ("contract", "contract_identity")):
        if key in params and params[key] != p[expected]:
            raise InvalidCapture("Request identity conflicts with explicit provenance")
    for key in ("from", "to"):
        if key in params and (type(params[key]) is not int or params[key] < 0):
            raise InvalidCapture("Provider range parameters must be nonnegative epoch seconds")
    if "from" in params and "to" in params and params["from"] > params["to"]:
        raise InvalidCapture("Request range is reversed")
    if "convert_to_usd" in params and type(params["convert_to_usd"]) is not bool:
        raise InvalidCapture("convert_to_usd must be boolean")
    transport = p["http_status_or_transport_error"]
    if not isinstance(transport, dict) or set(transport) != {"http_status", "transport_error"}:
        raise InvalidCapture("Transport status needs explicit HTTP status or error")
    code, error = transport["http_status"], transport["transport_error"]
    if not ((type(code) is int and 100 <= code <= 599 and error is None)
            or (code is None and isinstance(error, str) and bool(error.strip()))):
        raise InvalidCapture("Invalid transport status")
    if not isinstance(p["capture_status"], str) or p["capture_status"] not in CAPTURE_STATUSES:
        raise InvalidCapture("Explicit capture status is required")
    if (code is None or not 200 <= code < 300) and p["capture_status"] == "VALID_RESPONSE":
        raise InvalidCapture("Transport failure cannot be a valid response")
    flags = p["quality_flags"]
    if not isinstance(flags, list) or any(not isinstance(f, str) for f in flags) or len(set(flags)) != len(flags) or not set(flags) <= QUALITY_FLAGS or "COMPLETENESS_NOT_ASSERTED" not in flags:
        raise InvalidCapture("Explicit unique quality flags must disclaim completeness")
    p["quality_flags"] = sorted(flags)
    _payload(p, raw)
    return p, raw


def envelope(p: dict[str, Any], raw: bytes, sequence: int) -> dict[str, Any]:
    identity = {key: p[key] for key in (
        "provider", "endpoint_identity", "http_method", "request_parameters",
        "market_identity", "exchange_identity", "contract_identity", "requested_symbol",
        "requested_interval", "request_started_at",
    )}
    directory = f"{sequence:020d}"
    return {
        **p, "raw_evidence_id": "re1:" + digest(canonical(identity)),
        "raw_sha256": digest(raw), "raw_size_bytes": len(raw),
        "canonical_request_identity": identity,
        "transport_status": p["http_status_or_transport_error"],
        "content_type": p["content_type_or_unavailable_reason"],
        "raw_object_reference": f"{directory}/raw.bin",
        "metadata_object_reference": f"{directory}/metadata.json",
        "manifest_sequence": sequence,
    }
