from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.pilot.pilot_search_profile import PilotAdmissionEvidenceJournal
from pumpagent.pilot.runtime_handoff import NativeCandle, NativeCandleBatch, PilotRuntimeInputAssembler
from pumpagent.pilot.shadow_admission import (
    ShadowPilotAdmissionError,
    admit_guarded_handoff,
    process_admitted_shadow_cycle,
)
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus
from pumpagent.runtime.modules.watchlist.manager import WatchlistManager
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator

NOW = datetime(2026, 10, 19, 12, 20, tzinfo=timezone.utc)
DIGEST = "a" * 64


class FakeCoinalyze:
    def resolve_bybit_symbol(self, symbol: str) -> str:
        return symbol.upper() + ".6"

    def fetch_closed_native(self, *, bybit_symbol: str, provider_symbol: str, timeframe: str, evaluation_timestamp_utc: datetime) -> NativeCandleBatch:
        values = tuple([100.0] * 16 + [95.0, 100.0, 106.0, 106.0, 106.0])
        if timeframe == "1m":
            candles = tuple(
                NativeCandle(NOW - timedelta(minutes=20 - index), value, value, value, value, 1.0, "1m", True)
                for index, value in enumerate(values)
            )
        else:
            candles = (NativeCandle(NOW - timedelta(minutes=5), 106.0, 106.0, 106.0, 106.0, 1.0, "5m", True),)
        return NativeCandleBatch(bybit_symbol, provider_symbol, timeframe, candles, f"raw:{bybit_symbol}:{timeframe}", DIGEST)


def ranks() -> RankSnapshot:
    return RankSnapshot("COINGECKO_MARKET_CAP_DESC", NOW, (RankEntry("alpha", "ALPHA", 75),), "rank-fixture")


def instrument() -> list[dict[str, str]]:
    return [{"symbol": "ALPHAUSDT", "status": "Trading", "category": "linear", "contractType": "Perpetual", "quoteCoin": "USDT"}]


def snapshot() -> MarketSnapshot:
    closes = (100.0, 101.0, 103.0)
    rows = []
    for index, close in enumerate(closes):
        ts = NOW + timedelta(minutes=5 + index * 5)
        rows.append({"timestamp": ts, "open": close - 1, "high": close + 1, "low": close - 2, "close": close, "volume": 50 + index, "is_closed": True})
    return MarketSnapshot(
        event_id="post-admission-observation", timestamp=rows[-1]["timestamp"], symbol="ALPHAUSDT", exchange="bybit", timeframe="5m",
        price=103.0, ohlcv=tuple(rows), volume=52.0, data_source="coinalyze", data_quality_status=DataQualityStatus.VALID,
    )


class ShadowAdmissionTests(unittest.TestCase):
    def make_handoff(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        journal = PilotAdmissionEvidenceJournal(Path(root.name) / "journal")
        handoff = PilotRuntimeInputAssembler(FakeCoinalyze(), journal).prepare(
            rank_snapshot=ranks(), instruments=instrument(), tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW,
        )
        return handoff, journal

    def test_verified_handoff_opens_one_lifecycle_owned_episode_and_binds_receipt(self):
        handoff, journal = self.make_handoff()
        watchlist = WatchlistManager()
        result = admit_guarded_handoff(handoff, journal, watchlist, admission_timestamp_utc=NOW)
        self.assertEqual(result.receipt.symbol, handoff.selected.candidate.symbol)
        self.assertEqual(result.receipt.venue, "BYBIT")
        self.assertEqual(result.receipt.provider_symbol_literal, handoff.selected.candidate.provider_symbol_literal)
        self.assertEqual(result.receipt.journal_record_digest, handoff.journal_record_digest)
        self.assertEqual(result.receipt.native_timeframes, ("1m", "5m"))
        self.assertEqual(result.receipt.raw_evidence_ids, handoff.selected.candidate.raw_evidence_ids)
        self.assertEqual(result.receipt.raw_evidence_digests, handoff.selected.candidate.raw_evidence_digests)
        self.assertEqual(result.receipt.rank_snapshot_id, handoff.selected.candidate.rank_snapshot.snapshot_id)
        self.assertEqual(result.episode.episode_id, result.receipt.episode_id)
        self.assertEqual(result.watchlist_entry.active_episode_id, result.episode.episode_id)
        self.assertEqual(result.episode.timeframe, "5m")
        self.assertTrue(result.receipt.shadow_only)
        self.assertFalse(result.receipt.automated_order_execution)

    def test_tampered_or_missing_verified_journal_never_admits(self):
        handoff, journal = self.make_handoff()
        record = journal.directory / handoff.journal_record_name
        record.write_text("{}", encoding="utf-8")
        watchlist = WatchlistManager()
        with self.assertRaisesRegex(ShadowPilotAdmissionError, "verification"):
            admit_guarded_handoff(handoff, journal, watchlist, admission_timestamp_utc=NOW)
        self.assertIsNone(watchlist.get(exchange="bybit", symbol="ALPHAUSDT", timeframe="5m"))

    def test_provenance_mismatch_fails_before_lifecycle_registration(self):
        handoff, journal = self.make_handoff()
        altered = replace(handoff, journal_record_name="00000000000000000002.json")
        watchlist = WatchlistManager()
        with self.assertRaises(ShadowPilotAdmissionError):
            admit_guarded_handoff(altered, journal, watchlist, admission_timestamp_utc=NOW)
        self.assertEqual(watchlist.list_active(), ())
        altered_candidate = replace(handoff.selected.candidate, provider_symbol_literal="OTHERUSDT.6")
        altered_selection = replace(handoff.selected, candidate=altered_candidate)
        with self.assertRaisesRegex(ShadowPilotAdmissionError, "provenance"):
            admit_guarded_handoff(replace(handoff, selected=altered_selection), journal, watchlist, admission_timestamp_utc=NOW)
        self.assertEqual(watchlist.list_active(), ())

    def test_runtime_entrypoint_uses_registered_lifecycle_episode_id(self):
        handoff, journal = self.make_handoff()
        watchlist = WatchlistManager()
        admitted = admit_guarded_handoff(handoff, journal, watchlist, admission_timestamp_utc=NOW)
        result = process_admitted_shadow_cycle(admitted, snapshot(), watchlist, RuntimeOrchestrator())
        self.assertEqual(result.episode_id, admitted.episode.episode_id)
        self.assertTrue(result.runtime_invoked)
        self.assertEqual(result.status.value, "COMPLETED")

    def test_no_manual_or_shadow_fixture_shortcuts_are_imported(self):
        import inspect
        import pumpagent.pilot.shadow_admission as module
        source = inspect.getsource(module)
        self.assertNotIn("ShadowAttentionAdmission", source)
        self.assertNotIn("run_shadow_event", source)
        self.assertNotIn("place_order", source)
        self.assertNotIn("execute_order", source)

    def test_runtime_rejects_snapshot_before_admission_or_wrong_identity(self):
        handoff, journal = self.make_handoff()
        watchlist = WatchlistManager()
        admitted = admit_guarded_handoff(handoff, journal, watchlist, admission_timestamp_utc=NOW)
        early = replace(snapshot(), timestamp=NOW - timedelta(minutes=1))
        with self.assertRaises(ShadowPilotAdmissionError):
            process_admitted_shadow_cycle(admitted, early, watchlist, RuntimeOrchestrator())


if __name__ == "__main__":
    unittest.main()
