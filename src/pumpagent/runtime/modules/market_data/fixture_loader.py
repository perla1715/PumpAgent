"""Fixture-based Market Data loading.

This module is intentionally limited to local historical fixtures. It does not
connect to exchanges, perform live requests, or analyze market behavior.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping

from pumpagent.runtime.domain import MarketSnapshot, RuntimeEvent
from pumpagent.runtime.domain.enums import DataQualityStatus


class FixtureLoadError(ValueError):
    """Raised when a market data fixture cannot produce a MarketSnapshot."""


REQUIRED_FIELDS = (
    "event_id",
    "timestamp",
    "symbol",
    "exchange",
    "timeframe",
    "price",
    "ohlcv",
    "volume",
    "data_source",
    "data_quality_status",
)

CANDLE_REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def load_market_snapshot_from_fixture(path: str | Path) -> MarketSnapshot:
    """Read a local fixture and return a MarketSnapshot."""

    fixture_path = Path(path)
    payload = _read_fixture(fixture_path)
    _validate_required_fields(payload)

    return MarketSnapshot(
        event_id=str(payload["event_id"]),
        timestamp=_parse_timestamp(payload["timestamp"]),
        symbol=str(payload["symbol"]),
        exchange=str(payload["exchange"]),
        timeframe=str(payload["timeframe"]),
        price=_parse_float(payload["price"], "price"),
        ohlcv=_parse_ohlcv(payload["ohlcv"]),
        volume=_parse_float(payload["volume"], "volume"),
        data_source=str(payload["data_source"]),
        data_quality_status=_parse_data_quality_status(payload["data_quality_status"]),
        schema_version=str(payload.get("schema_version", "1.0")),
        optional_market_metrics=dict(payload.get("optional_market_metrics", {})),
        raw_payload_reference=str(fixture_path),
        latency_ms=payload.get("latency_ms"),
        missing_fields=tuple(str(item) for item in payload.get("missing_fields", ())),
    )


def add_market_snapshot_from_fixture(
    event: RuntimeEvent, path: str | Path
) -> RuntimeEvent:
    """Return a new RuntimeEvent with only market_snapshot populated.

    RuntimeEvent.event_id and MarketSnapshot.event_id may differ: RuntimeEvent
    identifies the reasoning cycle, while MarketSnapshot.event_id identifies the
    source fixture snapshot.
    """

    snapshot = load_market_snapshot_from_fixture(path)
    _validate_event_identity(event, snapshot)
    return event.with_sections(market_snapshot=snapshot)


def _read_fixture(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)
    except OSError as exc:
        raise FixtureLoadError(f"Could not read fixture: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FixtureLoadError(f"Fixture is not valid JSON: {path}") from exc

    if not isinstance(payload, Mapping):
        raise FixtureLoadError("Fixture root must be a JSON object.")

    return payload


def _validate_required_fields(payload: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise FixtureLoadError(f"Fixture missing required fields: {', '.join(missing)}")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise FixtureLoadError("timestamp must be an ISO-8601 string.")

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise FixtureLoadError("timestamp must be an ISO-8601 string.") from exc


def _parse_ohlcv(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise FixtureLoadError("ohlcv must be a list of candle objects.")

    candles: list[dict[str, Any]] = []
    for index, candle in enumerate(value):
        if not isinstance(candle, Mapping):
            raise FixtureLoadError("each ohlcv item must be an object.")
        _validate_candle_fields(candle, index)
        candles.append(
            {
                "timestamp": str(candle["timestamp"]),
                "open": _parse_float(candle["open"], f"ohlcv[{index}].open"),
                "high": _parse_float(candle["high"], f"ohlcv[{index}].high"),
                "low": _parse_float(candle["low"], f"ohlcv[{index}].low"),
                "close": _parse_float(candle["close"], f"ohlcv[{index}].close"),
                "volume": _parse_float(candle["volume"], f"ohlcv[{index}].volume"),
            }
        )

    return tuple(candles)


def _parse_data_quality_status(value: Any) -> DataQualityStatus:
    try:
        return DataQualityStatus(str(value))
    except ValueError as exc:
        raise FixtureLoadError(f"unknown data_quality_status: {value}") from exc


def _parse_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise FixtureLoadError(f"{field_name} must be numeric.") from exc


def _validate_candle_fields(candle: Mapping[str, Any], index: int) -> None:
    missing = [field for field in CANDLE_REQUIRED_FIELDS if field not in candle]
    if missing:
        raise FixtureLoadError(
            f"ohlcv[{index}] missing required fields: {', '.join(missing)}"
        )


def _validate_event_identity(event: RuntimeEvent, snapshot: MarketSnapshot) -> None:
    mismatches = []
    if event.symbol != snapshot.symbol:
        mismatches.append("symbol")
    if event.exchange != snapshot.exchange:
        mismatches.append("exchange")
    if event.timeframe != snapshot.timeframe:
        mismatches.append("timeframe")

    if mismatches:
        raise FixtureLoadError(
            "RuntimeEvent identity does not match MarketSnapshot: "
            + ", ".join(mismatches)
        )
