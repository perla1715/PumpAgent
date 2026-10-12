"""Shadow-mode Scanner Attention admission boundary.

This module accepts an already-qualified discovery input.  It deliberately does
not detect anomalies, start Tracker, invoke Runtime, or authorize trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import os
from typing import Any

from pumpagent.live_data.rolling_buffer import RollingNumericContextBuffer


@dataclass(frozen=True)
class ShadowAdmissionRecord:
    symbol: str
    admission_timestamp_utc: datetime
    discovery_mode: str
    scanner_decision: str
    source_provider: str
    available_timeframes: tuple[str, ...]
    rolling_context_readiness: dict[str, str]
    input_evidence_reference: str
    reason_basis: str
    shadow_mode: bool = True
    trade_permission: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "admission_timestamp_utc": self.admission_timestamp_utc.isoformat(),
            "discovery_mode": self.discovery_mode,
            "scanner_decision": self.scanner_decision,
            "source_provider": self.source_provider,
            "available_timeframes": list(self.available_timeframes),
            "rolling_context_readiness": self.rolling_context_readiness,
            "input_evidence_reference": self.input_evidence_reference,
            "reason_basis": self.reason_basis,
            "shadow_mode": self.shadow_mode,
            "trade_permission": self.trade_permission,
        }


@dataclass(frozen=True)
class ShadowAdmissionResult:
    status: str
    admitted: bool
    record: ShadowAdmissionRecord | None
    reason: str


class ShadowAttentionAdmission:
    """One-symbol, duplicate-safe, append-only shadow admission store."""

    def __init__(
        self,
        buffer: RollingNumericContextBuffer,
        admission_path: str | Path,
    ) -> None:
        self.buffer = buffer
        self.admission_path = Path(admission_path)
        self.admission_path.parent.mkdir(parents=True, exist_ok=True)
        self.admission_path.touch(exist_ok=True)
        self._active = self._load_active()

    def admit_manual_target(
        self,
        *,
        observed_pump_percent: float,
        target_pump_percent: float,
        admission_timestamp: datetime | None = None,
        reason: str = "Manual Target preference satisfied.",
    ) -> ShadowAdmissionResult:
        if target_pump_percent <= 0:
            raise ValueError("target_pump_percent must be positive")
        if observed_pump_percent < target_pump_percent:
            return ShadowAdmissionResult(
                "NO_ADMISSION", False, None,
                "Observed pump percentage is below the runtime target preference.",
            )
        return self.admit_decision(
            discovery_mode="MANUAL_TARGET",
            scanner_decision=True,
            admission_timestamp=admission_timestamp,
            reason=(
                f"{reason} observed={observed_pump_percent}; "
                f"target={target_pump_percent}."
            ),
        )

    def admit_decision(
        self,
        *,
        discovery_mode: str,
        scanner_decision: bool,
        admission_timestamp: datetime | None = None,
        reason: str,
    ) -> ShadowAdmissionResult:
        """Accept a qualified Manual Target or external test-only decision."""

        if not discovery_mode.strip():
            raise ValueError("discovery_mode must be non-empty")
        if not reason.strip():
            raise ValueError("reason must be non-empty")
        timestamp = _utc(admission_timestamp or datetime.now(timezone.utc))
        if not scanner_decision:
            return ShadowAdmissionResult("NO_ADMISSION", False, None, reason)
        if self._active:
            return ShadowAdmissionResult(
                "DUPLICATE_NOOP", False, self._active,
                "Symbol is already under observation; duplicate opening suppressed.",
            )
        context = self.buffer.context_snapshot(as_of=timestamp)
        if not context["available_timeframes"]:
            return ShadowAdmissionResult(
                "INSUFFICIENT_EVIDENCE", False, None,
                "No closed rolling context is available at the admission time.",
            )
        record = ShadowAdmissionRecord(
            symbol=self.buffer.symbol,
            admission_timestamp_utc=timestamp,
            discovery_mode=discovery_mode,
            scanner_decision="TAKE_UNDER_OBSERVATION",
            source_provider=self.buffer.provider,
            available_timeframes=tuple(context["available_timeframes"]),
            rolling_context_readiness={
                timeframe: details["warmup_status"]
                for timeframe, details in context["statuses"].items()
            },
            input_evidence_reference=str(self.buffer.storage_path),
            reason_basis=reason,
        )
        self._append(record)
        self._active = record
        return ShadowAdmissionResult("ADMITTED", True, record, reason)

    def _load_active(self) -> ShadowAdmissionRecord | None:
        if not self.admission_path.exists():
            return None
        lines = [line for line in self.admission_path.read_text(encoding="utf-8").splitlines() if line]
        if not lines:
            return None
        data = json.loads(lines[-1])
        return ShadowAdmissionRecord(
            symbol=data["symbol"],
            admission_timestamp_utc=_utc(datetime.fromisoformat(data["admission_timestamp_utc"])),
            discovery_mode=data["discovery_mode"],
            scanner_decision=data["scanner_decision"],
            source_provider=data["source_provider"],
            available_timeframes=tuple(data["available_timeframes"]),
            rolling_context_readiness=dict(data["rolling_context_readiness"]),
            input_evidence_reference=data["input_evidence_reference"],
            reason_basis=data["reason_basis"],
            shadow_mode=bool(data["shadow_mode"]),
            trade_permission=bool(data["trade_permission"]),
        )

    def _append(self, record: ShadowAdmissionRecord) -> None:
        with self.admission_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)

