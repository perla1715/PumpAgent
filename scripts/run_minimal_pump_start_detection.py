#!/usr/bin/env python3
"""One bounded invocation of the isolated TEST_ONLY Pump Start stream.

The first invocation primes prior native context only.  Later invocations use
only the newest bar after the persisted cursor, so missed bars are never
written as retrospective prospective observations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

PROJECT = Path("/Users/oksanachovgan/PumpAgent")
sys.path.insert(0, str(PROJECT / "src"))

from pumpagent.live_data.adapters.bybit_public_market import fetch_completed_native_bars
from pumpagent.live_data.adapters.coinalyze_oi import CoinalyzeOISideChannel
from pumpagent.live_data.minimal_pump_start_stream import MinimalPumpStartStream
from pumpagent.live_data.minimal_pump_start_stream import CONTRACT_VERSION


def run_once(symbol: str, root: Path = PROJECT) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    # V2 has immutable active-event fields.  Keep the V1 detection-only
    # journal/state historical rather than reinterpret it under this contract.
    stream_root = root / "checkpoints" / "minimal_pump_start_detection_test_only_v2_active_event"
    stream = MinimalPumpStartStream(stream_root / "prospective_records.jsonl", stream_root / "state.json")
    one = fetch_completed_native_bars(symbol, "1", limit=16, asof=now)
    three = fetch_completed_native_bars(symbol, "3", limit=8, asof=now)
    five = fetch_completed_native_bars(symbol, "5", limit=8, asof=now)
    if not one:
        raise RuntimeError("no_closed_native_1m_bar")
    prior = stream.store.get(symbol)
    if prior is None:
        stream.prime_context(symbol, one)
        return {"status": "CONTEXT_PRIMED_NO_HISTORICAL_BACKFILL", "symbol": symbol.upper(), "records_appended": 0,
                "latest_native_1m": one[-1]["timestamp"], "contract_version": CONTRACT_VERSION}
    oi = []
    oi_error = None
    try:
        oi = CoinalyzeOISideChannel().fetch_closed_5m_oi(symbol, start=now - timedelta(minutes=15), end=now, now=now)
    except Exception as exc:  # OI is observational context, never a price-start gate.
        oi_error = f"{type(exc).__name__}:{exc}"
    newest = [one[-1]]
    rows = stream.process(symbol, bars_1m=newest, bars_3m=three, bars_5m=five, oi_records=oi, asof=now)
    return {"status": "SUCCESS", "symbol": symbol.upper(), "records_appended": sum(r["journal_result"] == "APPENDED" for r in rows),
            "rows": rows, "oi_provider_error": oi_error, "contract_version": CONTRACT_VERSION}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("symbol")
    print(json.dumps(run_once(parser.parse_args().symbol), sort_keys=True, default=str))
