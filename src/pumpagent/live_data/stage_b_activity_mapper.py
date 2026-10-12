"""TEST_ONLY adapter from the frozen Automatic Anomaly result to Stage B states.

This mapper owns no activity definition.  It evaluates complete native closed
1m context with the existing frozen Median/MAD Structure A evaluator, then
translates only its completed ATTENTION/NO_ATTENTION outcomes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from pumpagent.live_data.automatic_anomaly import (
    PROFILE_ID,
    MedianMadStructureAEvaluator,
)


ACTIVITY_OBSERVED = "ACTIVITY_OBSERVED"
NO_ACTIVITY_OBSERVED = "NO_ACTIVITY_OBSERVED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def map_closed_native_1m_activity(
    *,
    symbol: str,
    bars: Sequence[Mapping[str, Any]],
    observation_timestamp: datetime,
) -> dict[str, str]:
    """Translate one completed frozen-test evaluation without semantic changes."""
    try:
        rows = [
            {
                "market_timestamp_utc": str(bar["timestamp"]),
                "price": bar["close"],
                "volume": bar["volume"],
                "timeframe": "1",
                "provider": "bybit",
                "raw_or_derived_status": "RAW_NATIVE_CLOSED_BAR",
            }
            for bar in bars
        ]
        decision = MedianMadStructureAEvaluator().evaluate(
            symbol=symbol,
            timeframe="1",
            rows=rows,
            as_of=observation_timestamp,
            provider="bybit",
            source_provenance="bybit_native_closed_1m_stage_b_context",
            top50_status="PASS",
        )
    except (KeyError, TypeError, ValueError):
        return _result(INSUFFICIENT_EVIDENCE, "ACTIVITY_EVALUATION_INVALID_INPUT")

    if decision.decision == "ATTENTION":
        return _result(ACTIVITY_OBSERVED, "FROZEN_TEST_ACTIVITY_EVIDENCE_OBSERVED")
    if decision.decision == "NO_ATTENTION":
        return _result(NO_ACTIVITY_OBSERVED, "FROZEN_TEST_NO_ACTIVITY_EVIDENCE")
    return _result(INSUFFICIENT_EVIDENCE, "FROZEN_TEST_ACTIVITY_EVIDENCE_INSUFFICIENT")


def _result(state: str, reason: str) -> dict[str, str]:
    return {
        "price_activity_state": state,
        "activity_evaluation_state": state,
        "activity_evaluation_reason": reason,
        "activity_semantic_profile_id": PROFILE_ID,
    }
