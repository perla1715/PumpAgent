"""Safe, diagnostic-only control artifacts for a Pilot scanner invocation.

This module deliberately has no role in eligibility, selection, journaling,
admission, or runtime processing.  It preserves the authoritative launch
result before optional Owner-review presentation is attempted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class AuthoritativeScannerResult:
    """Frozen launch outcome, independent of diagnostic artifact success."""

    run_id: str
    evaluation_timestamp_utc: datetime
    discovery_status: str
    discovery_reason: str
    selected_symbol: str | None

    @classmethod
    def capture(
        cls,
        *,
        run_id: str,
        evaluation_timestamp_utc: datetime,
        result: Any,
    ) -> "AuthoritativeScannerResult":
        if not run_id.strip():
            raise ValueError("run_id is required")
        if evaluation_timestamp_utc.tzinfo is None or evaluation_timestamp_utc.utcoffset() is None:
            raise ValueError("evaluation timestamp must be timezone-aware")
        selected = result.handoff.selected.candidate.symbol if result.handoff else None
        return cls(
            run_id=run_id,
            evaluation_timestamp_utc=evaluation_timestamp_utc.astimezone(timezone.utc),
            discovery_status=result.status.value,
            discovery_reason=result.reason,
            selected_symbol=selected,
        )


@dataclass(frozen=True)
class ControlArtifactResult:
    """Diagnostic result that cannot alter the frozen scanner outcome."""

    authoritative: AuthoritativeScannerResult
    status: str
    near_misses: tuple[Mapping[str, Any], ...]
    error: str | None = None


def deterministic_near_misses(
    rows: Sequence[Mapping[str, Any]], *, limit: int = 5,
) -> tuple[Mapping[str, Any], ...]:
    """Order review rows by present pump metric, then an explicit unknown bucket.

    A missing metric remains missing in the returned record.  The unknown
    bucket is diagnostic only and has no implication for Pilot qualification.
    """
    if limit < 0:
        raise ValueError("limit cannot be negative")

    def key(row: Mapping[str, Any]) -> tuple[int, float, str]:
        value = row.get("20M_PUMP_PERCENT")
        if isinstance(value, bool):
            return (1, 0.0, _symbol(row))
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return (0, -float(value), _symbol(row))
        return (1, 0.0, _symbol(row))

    return tuple(sorted((dict(row) for row in rows), key=key)[:limit])


def generate_control_artifacts(
    authoritative: AuthoritativeScannerResult,
    rows: Sequence[Mapping[str, Any]],
    *,
    near_miss_writer: Callable[[tuple[Mapping[str, Any], ...]], None] | None = None,
) -> ControlArtifactResult:
    """Generate optional diagnostics without losing the captured scanner result."""
    try:
        near_misses = deterministic_near_misses(rows)
        if near_miss_writer is not None:
            near_miss_writer(near_misses)
        return ControlArtifactResult(authoritative, "COMPLETE", near_misses)
    except Exception as exc:
        return ControlArtifactResult(
            authoritative=authoritative,
            status="FAILED",
            near_misses=(),
            error=f"{type(exc).__name__}: {exc}",
        )


def _symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("SYMBOL", "")).upper()
