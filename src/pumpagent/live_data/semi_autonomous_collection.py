"""One bounded, user-scheduled Contextual Attention Test v1 collection run."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator

from pumpagent.live_data.adapters.market_cap_rank import CoinGeckoMarketCapRankSource
from pumpagent.live_data.adapters.bybit_public_market import (
    fetch_closed_1m_bars,
    fetch_instruments,
    fetch_tickers,
)
from pumpagent.live_data.contextual_attention_prospective import ProspectiveCollector
from pumpagent.live_data.contextual_attention_test_v1 import ExperimentJournal
from pumpagent.live_data.scanner_funnel_stage_a import ClosedOHLCVCache, StageABybitSweep


CONTRACT_VERSION = "CONTEXTUAL_ATTENTION_TEST_V1"
ACTIVE_PROFILES = ("P_PRICE_LED", "P_PRICE_VOLUME_CONTEXT")
DEFERRED_PROFILE = "P_PRICE_VOLUME_SEQUENCE"
JOURNAL_RELATIVE = Path("checkpoints/contextual_attention_test_v1_activity_mapper_live_cycle_2026-10-08/prospective_records.jsonl")
LEDGER_RELATIVE = Path("checkpoints/PampAgent_CONTEXTUAL_ATTENTION_TEST_V1_PROSPECTIVE_COLLECTION_PROGRESS_2026-10-08.txt")
SUMMARY_RELATIVE = Path("checkpoints/PampAgent_CONTEXTUAL_ATTENTION_TEST_V1_PROSPECTIVE_COLLECTION_PROGRESS_SUMMARY_2026-10-08.json")
SERVICE_RELATIVE = Path("checkpoints/contextual_attention_test_v1_semi_autonomous")
SESSION_SIZE = 10


def checkpoint_reached(summary: dict[str, Any]) -> bool:
    return (
        int(summary.get("independently_reviewable_episodes", 0)) >= 12
        and int(summary.get("unique_non_top50_symbols", 0)) >= 10
        and int(summary.get("calendar_days_covered", 0)) >= 14
    )


@contextmanager
def session_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_once(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    service = root / SERVICE_RELATIVE
    for name in ("sessions", "logs", "incidents", "state", "cache"):
        (service / name).mkdir(parents=True, exist_ok=True)
    summary_path = root / SUMMARY_RELATIVE
    ledger_path = root / LEDGER_RELATIVE
    summary = _read_json(summary_path)
    now = datetime.now(timezone.utc)
    session_id = "CONTEXTUAL_ATTENTION_TEST_V1_SCHEDULED_" + now.strftime("%Y%m%dT%H%M%SZ")
    with session_lock(service / "state" / "collector.lock") as acquired:
        if not acquired:
            result = _skipped(session_id, now)
            _write_json(service / "sessions" / f"{session_id}.json", result)
            _append_log(service, result)
            return result
        if checkpoint_reached(summary):
            result = _paused(session_id, now)
            _write_json(service / "sessions" / f"{session_id}.json", result)
            _write_state(service, summary, result, checkpoint=True)
            _append_log(service, result)
            return result
        result = _run_with_retry(root, service, session_id)
        _write_json(service / "sessions" / f"{session_id}.json", result)
        if result["status"] == "SUCCESS":
            _update_ledger(root, ledger_path, summary_path, result)
        _write_state(service, _read_json(summary_path), result, checkpoint=False)
        _append_log(service, result)
        return result


def _run_with_retry(root: Path, service: Path, session_id: str) -> dict[str, Any]:
    failures: list[str] = []
    for attempt in (1, 2):
        try:
            result = _run_live_cycle(root, service, session_id)
            result["attempt_count"] = attempt
            result["status"] = "SUCCESS"
            return result
        except Exception as exc:
            failures.append(f"{type(exc).__name__}:{exc}")
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "session_id": session_id, "status": "PROVIDER_FAILURE_AFTER_TWO_ATTEMPTS",
        "scheduled_timestamp": now, "actual_start_timestamp": now, "end_timestamp": now,
        "attempt_count": 2, "provider_failures": 2, "failures": failures,
        "records_appended": 0, "new_episode_count": 0, "checkpoint_reached": False,
    }
    _write_json(service / "incidents" / f"{session_id}.json", result)
    return result


def _run_live_cycle(root: Path, service: Path, session_id: str) -> dict[str, Any]:
    scheduled = datetime.now(timezone.utc)
    rank = CoinGeckoMarketCapRankSource(timeout_seconds=20).fetch()
    instruments = fetch_instruments()
    ticker_payload = fetch_tickers()
    tickers = {row["symbol"].upper(): row for row in ticker_payload["result"]["list"] if row.get("symbol")}
    markets = []
    for row in instruments["result"]["list"]:
        symbol = str(row.get("symbol", "")).upper()
        asset = symbol[:-4] if symbol.endswith("USDT") else symbol
        entry, _ = rank.resolve(asset)
        if entry is not None and entry.rank > 50 and symbol in tickers and row.get("status") == "Trading":
            markets.append({"symbol": symbol, "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"})
    markets = sorted(markets, key=lambda value: value["symbol"])[:SESSION_SIZE]
    if len(markets) < SESSION_SIZE:
        raise RuntimeError("fresh_ranked_non_top50_universe_too_small")

    def ticker_loader(symbols: list[str]) -> dict[str, dict[str, str]]:
        stamp = datetime.now(timezone.utc).isoformat()
        return {symbol: {"timestamp": stamp} for symbol in symbols if symbol in tickers}

    def bars_loader(symbol: str) -> list[dict[str, Any]]:
        return fetch_closed_1m_bars(symbol)

    def cache_factory(symbol: str) -> ClosedOHLCVCache:
        return ClosedOHLCVCache(path=service / "cache" / f"{symbol}.jsonl")

    sweep = StageABybitSweep(lambda: markets, rank, ticker_loader, bars_loader, cache_factory)
    journal = ExperimentJournal(root / JOURNAL_RELATIVE)
    collector = ProspectiveCollector(sweep, bars_loader, journal, session_id)
    cycle = collector.run_cycle(cycle_timestamp=datetime.now(timezone.utc))
    counts = {state: sum(row.get("decision_state") == state for row in cycle["records"]) for state in (
        "TEST_ATTENTION_YES", "TEST_ATTENTION_NO", "INSUFFICIENT_EVIDENCE", "DEFERRED_DATA_QUALITY"
    )}
    return {
        "session_id": session_id, "scheduled_timestamp": scheduled.isoformat(),
        "actual_start_timestamp": cycle["start_timestamp"], "end_timestamp": cycle["end_timestamp"],
        "symbols_observed": cycle["symbols_observed"],
        "eligible_non_top50_symbols": cycle["eligible_non_top50_symbols"],
        "profile_records_evaluated": cycle["stage_b_records_evaluated"],
        "records_appended": cycle["records_appended"], "duplicate_noop": cycle["duplicate_noop"],
        "provider_failures": cycle["provider_failures"], "decision_counts": counts,
        "new_episode_count": 0, "checkpoint_reached": False,
    }


def _update_ledger(root: Path, ledger_path: Path, summary_path: Path, result: dict[str, Any]) -> None:
    rows = _journal_rows(root / JOURNAL_RELATIVE)
    symbols = sorted({str(row["symbol"]) for row in rows if row.get("trading_universe_eligible")})
    days = {str(row.get("decision_timestamp", ""))[:10] for row in rows if row.get("decision_timestamp")}
    counts = {state: sum(row.get("decision_state") == state for row in rows) for state in (
        "TEST_ATTENTION_YES", "TEST_ATTENTION_NO", "INSUFFICIENT_EVIDENCE", "DEFERRED_DATA_QUALITY"
    )}
    existing = _read_json(summary_path)
    episodes = int(existing.get("independently_reviewable_episodes", 0))
    total_duplicates = int(existing.get("duplicate_noop_count", 0)) + int(result["duplicate_noop"])
    total_failures = int(existing.get("provider_failures", 0)) + int(result["provider_failures"])
    summary = {
        **existing,
        "status": "IN_PROGRESS", "verdict": "COLLECTION_IN_PROGRESS",
        "contract_version": CONTRACT_VERSION, "active_profiles": list(ACTIVE_PROFILES),
        "deferred_profiles": {DEFERRED_PROFILE: "DEFERRED_NOT_EVALUABLE_V1"},
        "collection_started": True, "collection_continuing": True, "checkpoint_reached": False,
        "calendar_days_covered": len(days), "unique_non_top50_symbols": len(symbols),
        "independently_reviewable_episodes": episodes, "upper_checkpoint_progress": f"{episodes}/20",
        "test_attention_yes_count": counts["TEST_ATTENTION_YES"],
        "test_attention_no_count": counts["TEST_ATTENTION_NO"],
        "insufficient_evidence_count": counts["INSUFFICIENT_EVIDENCE"],
        "deferred_data_quality_count": counts["DEFERRED_DATA_QUALITY"],
        "duplicate_noop_count": total_duplicates, "provider_failures": total_failures,
        "last_successful_session_id": result["session_id"],
        "last_successful_session_timestamp": result["end_timestamp"],
        "last_session": result,
        "next_task": "CONTINUE BOUNDED PROSPECTIVE COLLECTION UNDER THE SAME FROZEN CONTRACT WITHOUT PARAMETER RETUNING",
    }
    history = list(summary.get("session_history", []))
    history.append({key: result[key] for key in ("session_id", "end_timestamp", "records_appended", "new_episode_count")})
    summary["session_history"] = history
    _atomic_json(summary_path, summary)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\nSCHEDULED_SESSION\n"
            f"SESSION_ID={result['session_id']}\nSTART={result['actual_start_timestamp']}\nEND={result['end_timestamp']}\n"
            f"SYMBOLS_OBSERVED={result['symbols_observed']}\nELIGIBLE_NON_TOP50_SYMBOLS={result['eligible_non_top50_symbols']}\n"
            f"RECORDS_APPENDED={result['records_appended']}\nDUPLICATE_NOOP={result['duplicate_noop']}\n"
            f"DECISIONS=TEST_ATTENTION_YES:{result['decision_counts']['TEST_ATTENTION_YES']},TEST_ATTENTION_NO:{result['decision_counts']['TEST_ATTENTION_NO']},INSUFFICIENT_EVIDENCE:{result['decision_counts']['INSUFFICIENT_EVIDENCE']},DEFERRED_DATA_QUALITY:{result['decision_counts']['DEFERRED_DATA_QUALITY']}\n"
            f"NEW_INDEPENDENTLY_REVIEWABLE_EPISODES=0\nTOTAL_EPISODES={episodes}\nUNIQUE_SYMBOLS={len(symbols)}\nCALENDAR_DAYS={len(days)}\n"
            "CHECKPOINT_REACHED=NO\n"
        )


def _journal_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp = Path(handle.name)
    os.replace(temp, path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_json(path, payload)


def _write_state(service: Path, summary: dict[str, Any], result: dict[str, Any], *, checkpoint: bool) -> None:
    previous = _read_json(service / "state" / "service_state.json")
    status = "CHECKPOINT_REACHED_PAUSED" if checkpoint else ("ACTIVE" if result["status"] == "SUCCESS" else result["status"])
    _atomic_json(service / "state" / "service_state.json", {
        "service_status": status, "last_attempt_timestamp": result["end_timestamp"],
        "last_success_timestamp": result["end_timestamp"] if result["status"] == "SUCCESS" else previous.get("last_success_timestamp"),
        "last_session_id": result["session_id"],
        "consecutive_provider_failures": 0 if result["status"] == "SUCCESS" else int(previous.get("consecutive_provider_failures", 0)) + 1,
        "total_sessions": int(previous.get("total_sessions", 0)) + 1,
        "successful_sessions": int(previous.get("successful_sessions", 0)) + (result["status"] == "SUCCESS"),
        "skipped_overlap_sessions": int(previous.get("skipped_overlap_sessions", 0)) + (result["status"] == "SESSION_SKIPPED_ALREADY_RUNNING"),
        "episodes": summary.get("independently_reviewable_episodes", 0),
        "unique_symbols": summary.get("unique_non_top50_symbols", 0),
        "calendar_days": summary.get("calendar_days_covered", 0),
        "checkpoint_reached": checkpoint,
        "unresolved_owner_blocker": None,
    })


def _skipped(session_id: str, now: datetime) -> dict[str, Any]:
    stamp = now.isoformat()
    return {"session_id": session_id, "status": "SESSION_SKIPPED_ALREADY_RUNNING", "scheduled_timestamp": stamp, "actual_start_timestamp": stamp, "end_timestamp": stamp, "records_appended": 0, "new_episode_count": 0, "checkpoint_reached": False}


def _paused(session_id: str, now: datetime) -> dict[str, Any]:
    stamp = now.isoformat()
    return {"session_id": session_id, "status": "CHECKPOINT_REACHED_PAUSED", "scheduled_timestamp": stamp, "actual_start_timestamp": stamp, "end_timestamp": stamp, "records_appended": 0, "new_episode_count": 0, "checkpoint_reached": True}


def _append_log(service: Path, result: dict[str, Any]) -> None:
    with (service / "logs" / "runner.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")
