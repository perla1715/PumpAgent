"""Bounded TEST_ONLY prospective collector for the two active v1 profiles."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from pumpagent.live_data.contextual_attention_test_v1 import (
    ExperimentJournal, P_PRICE_LED, P_PRICE_VOLUME_CONTEXT,
    evaluate_stage_b_record,
)
from pumpagent.live_data.scanner_funnel_stage_b import evaluate_stage_a_record


ACTIVE_PROFILES = (P_PRICE_LED, P_PRICE_VOLUME_CONTEXT)
SEQUENCE_STATUS = "DEFERRED_NOT_EVALUABLE_V1"


@dataclass
class ProspectiveCollector:
    """One bounded cycle; no background loop and no production side effects."""

    stage_a_runner: Any
    bars_loader: Callable[[str], Sequence[Mapping[str, Any]]]
    journal: ExperimentJournal
    block_id: str

    def run_cycle(self, *, cycle_timestamp: datetime) -> dict[str, Any]:
        cycle = _utc(cycle_timestamp)
        stage_a = self.stage_a_runner.run_cycle(scanner_cycle_timestamp=cycle)
        appended = duplicates = evaluated = sequence_deferred = data_quality = 0
        rows: list[dict[str, Any]] = []
        for source in stage_a.get("records", []):
            if not source.get("trading_universe_eligible") or source.get("top50_status") != "NON_TOP50_ELIGIBLE":
                continue
            symbol = str(source["symbol"]).upper()
            try:
                # Stage A's cache already enforces this boundary.  The loader
                # may also return the provider's current open candle; never
                # pass that partial bar into Stage B.
                live_bars = [bar for bar in self.bars_loader(symbol) if bar.get("closed") is True]
                stage_b = evaluate_stage_a_record(source, closed_1m_bars=live_bars, observation_timestamp=cycle)
                stage_b["rank_freshness_state"] = source.get("rank_freshness_state")
                stage_b["top50_status"] = source.get("top50_status")
                stage_b["trading_universe_eligible"] = source.get("trading_universe_eligible")
                stage_b["stage_a_observation_identity"] = source.get("scanner_cycle_timestamp")
                for profile in ACTIVE_PROFILES:
                    record = evaluate_stage_b_record(stage_b, profile, decision_timestamp=cycle)
                    record.update({
                        "prospective": True,
                        "collection_block_id": self.block_id,
                        "stage_a_observation_identity": source.get("scanner_cycle_timestamp"),
                        "stage_b_observation_identity": stage_b.get("source_evidence_identity"),
                        "rank_freshness_state": source.get("rank_freshness_state"),
                        "top50_status": source.get("top50_status"),
                        "trading_universe_eligible": source.get("trading_universe_eligible"),
                    })
                    result = self.journal.append(record)
                    appended += result == "APPENDED"; duplicates += result == "DUPLICATE_NOOP"; evaluated += 1
                    data_quality += record["decision_state"] == "DEFERRED_DATA_QUALITY"
                    rows.append(record)
            except Exception as exc:
                data_quality += 1
                rows.append({"symbol": symbol, "prospective": True, "collection_block_id": self.block_id, "decision_state": "DEFERRED_DATA_QUALITY", "error": f"{type(exc).__name__}:{exc}"})
        return {
            "block_id": self.block_id, "start_timestamp": cycle.isoformat(), "end_timestamp": datetime.now(timezone.utc).isoformat(),
            "symbols_observed": stage_a.get("symbols_observed", 0), "eligible_non_top50_symbols": len({r.get("symbol") for r in rows}),
            "stage_b_records_evaluated": evaluated, "active_profiles": list(ACTIVE_PROFILES), "sequence_deferred": sequence_deferred,
            "duplicate_noop": duplicates, "data_quality_deferral_count": data_quality, "records_appended": appended,
            "provider_failures": stage_a.get("symbol_failures", 0), "live_data_confirmed": True, "parameter_retuning_performed": False,
            "records": rows,
        }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
