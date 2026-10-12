from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.live_data.scanner_funnel_stage_a import ClosedOHLCVCache, StageABybitSweep


NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def bar(minute: int, *, closed=True):
    ts = NOW - timedelta(minutes=minute)
    return {"timestamp": ts, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 10.0, "closed": closed}


class StageATests(unittest.TestCase):
    def snapshot(self, *symbols):
        return RankSnapshot("TEST", NOW, tuple(RankEntry(s, s, i + 51) for i, s in enumerate(symbols)), "snap")

    def markets(self):
        return [
            {"symbol": "GOODUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"},
            {"symbol": "SPOTUSDT", "quote_asset": "USDT", "is_perpetual": False, "active": True, "category": "linear"},
            {"symbol": "INVUSDT", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "inverse", "inverse": True},
            {"symbol": "OFFUSDT", "quote_asset": "USDT", "is_perpetual": True, "active": False, "category": "linear"},
            {"symbol": "ALTUSDC", "quote_asset": "USDC", "is_perpetual": True, "active": True, "category": "linear"},
        ]

    def test_only_active_usdt_linear_perpetuals_enumerated(self):
        sweep = StageABybitSweep(lambda: self.markets(), self.snapshot("GOOD"), lambda s: {}, lambda s: [bar(2)])
        result = sweep.run_cycle(scanner_cycle_timestamp=NOW)
        self.assertEqual([r["symbol"] for r in result["records"]], ["GOODUSDT"])

    def test_top50_plumbing_only_and_ineligible(self):
        market = [{"symbol": "BTCUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        rank = RankSnapshot("TEST", NOW, (RankEntry("BTC", "BTC", 50),), "snap")
        result = StageABybitSweep(lambda: market, rank, lambda s: {"BTCUSDT": {"timestamp": NOW}}, lambda s: [bar(2)]).run_cycle(scanner_cycle_timestamp=NOW)
        rec = result["records"][0]
        self.assertEqual(rec["top50_status"], "PLUMBING_ONLY"); self.assertFalse(rec["trading_universe_eligible"])

    def test_stale_rank_fails_closed(self):
        market = [{"symbol": "ALPHAUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        stale = RankSnapshot("TEST", NOW - timedelta(days=2), (RankEntry("ALPHA", "ALPHA", 75),), "snap")
        rec = StageABybitSweep(lambda: market, stale, lambda s: {"ALPHAUSDT": {"timestamp": NOW}}, lambda s: [bar(2)]).run_cycle(scanner_cycle_timestamp=NOW)["records"][0]
        self.assertEqual(rec["rank_freshness_state"], "STALE"); self.assertEqual(rec["top50_status"], "UNKNOWN"); self.assertFalse(rec["trading_universe_eligible"])

    def test_fresh_non_top50_rank_is_eligible(self):
        market = [{"symbol": "ALPHAUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        fresh = RankSnapshot("TEST", NOW, (RankEntry("ALPHA", "ALPHA", 75),), "snap")
        rec = StageABybitSweep(lambda: market, fresh, lambda s: {"ALPHAUSDT": {"timestamp": NOW}}, lambda s: [bar(2)]).run_cycle(scanner_cycle_timestamp=NOW)["records"][0]
        self.assertEqual(rec["rank_freshness_state"], "FRESH"); self.assertEqual(rec["top50_status"], "NON_TOP50_ELIGIBLE"); self.assertTrue(rec["trading_universe_eligible"])

    def test_unknown_rank_fails_closed(self):
        market = [{"symbol": "UNKNOWNUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        rec = StageABybitSweep(lambda: market, self.snapshot(), lambda s: {}, lambda s: [bar(2)]).run_cycle(scanner_cycle_timestamp=NOW)["records"][0]
        self.assertEqual(rec["top50_status"], "UNKNOWN"); self.assertFalse(rec["trading_universe_eligible"])

    def test_closed_bars_only(self):
        cache = ClosedOHLCVCache(); out = cache.ingest([bar(2), bar(0, closed=False)], as_of=NOW)
        self.assertEqual(out["open_bars"], 1); self.assertEqual(len(cache.bars()), 1)

    def test_duplicate_timestamp_idempotent(self):
        cache = ClosedOHLCVCache(); self.assertEqual(cache.ingest([bar(2)], as_of=NOW)["inserted"], 1); self.assertEqual(cache.ingest([bar(2)], as_of=NOW)["duplicates"], 1); self.assertEqual(len(cache.bars()), 1)

    def test_out_of_order_is_deterministic(self):
        cache = ClosedOHLCVCache(); out = cache.ingest([bar(1), bar(3)], as_of=NOW)
        self.assertIn("OUT_OF_ORDER_INPUT", out["issues"]); self.assertEqual([x["timestamp"] for x in cache.bars()], sorted(x["timestamp"] for x in cache.bars()))

    def test_missing_ohlcv_does_not_fabricate(self):
        cache = ClosedOHLCVCache(); out = cache.ingest([], as_of=NOW); self.assertEqual(out["inserted"], 0); self.assertEqual(cache.bars(), [])

    def test_symbol_bar_failure_does_not_crash_cycle(self):
        markets = [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}, {"symbol": "BUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        def load(symbol):
            if symbol == "AUSDT": raise RuntimeError("temporary")
            return [bar(2)]
        result = StageABybitSweep(lambda: markets, self.snapshot("A", "B"), lambda s: {}, load).run_cycle(scanner_cycle_timestamp=NOW)
        self.assertEqual(len(result["records"]), 2); self.assertEqual(result["symbol_failures"], 1)

    def test_no_attention_ranking_h2_or_baseline(self):
        result = StageABybitSweep(lambda: [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}], self.snapshot("A"), lambda s: {}, lambda s: [bar(2)]).run_cycle(scanner_cycle_timestamp=NOW)
        self.assertEqual(result["attention_decisions"], []); self.assertEqual(result["ranking"], []); self.assertEqual(result["h2"], []); self.assertEqual(result["baseline"], [])
        self.assertEqual(result["records"][0]["attention_state"], "NOT_EVALUATED")

    def test_provenance_is_bybit_native(self):
        result = StageABybitSweep(lambda: [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}], self.snapshot("A"), lambda s: {"AUSDT": {"timestamp": NOW}}, lambda s: [bar(2)]).run_cycle(scanner_cycle_timestamp=NOW)
        rec = result["records"][0]; self.assertEqual(rec["market_venue"], "BYBIT"); self.assertEqual(rec["native_or_reconstructed"], "NATIVE"); self.assertEqual(rec["timeframe"], "1")

    def test_repeated_cycle_is_idempotent_with_persistent_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            def factory(symbol): return ClosedOHLCVCache(path=Path(tmp) / f"{symbol}.jsonl")
            sweep = StageABybitSweep(lambda: [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}], self.snapshot("A"), lambda s: {"AUSDT": {"timestamp": NOW}}, lambda s: [bar(2)], factory)
            sweep.run_cycle(scanner_cycle_timestamp=NOW); second = sweep.run_cycle(scanner_cycle_timestamp=NOW)
            self.assertEqual(len(ClosedOHLCVCache(path=Path(tmp) / "AUSDT.jsonl").bars()), 1); self.assertEqual(second["records"][0]["stage_a_status"], "READY")

    def test_stale_latest_closed_bar_is_explicit(self):
        market = [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        rec = StageABybitSweep(lambda: market, self.snapshot("A"), lambda s: {"AUSDT": {"timestamp": NOW}}, lambda s: [bar(10)]).run_cycle(scanner_cycle_timestamp=NOW)["records"][0]
        self.assertEqual(rec["closed_1m_cache_status"], "STALE"); self.assertEqual(rec["data_gap_state"], "STALE"); self.assertEqual(rec["stage_a_status"], "STALE_DATA")

    def test_fresh_latest_closed_bar_is_ready(self):
        market = [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        rec = StageABybitSweep(lambda: market, self.snapshot("A"), lambda s: {"AUSDT": {"timestamp": NOW}}, lambda s: [bar(1)]).run_cycle(scanner_cycle_timestamp=NOW)["records"][0]
        self.assertEqual(rec["closed_1m_cache_status"], "READY"); self.assertNotEqual(rec["stage_a_status"], "STALE_DATA")

    def test_stage_a_has_no_trade_authority_field(self):
        market = [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        rec = StageABybitSweep(lambda: market, self.snapshot("A"), lambda s: {}, lambda s: [bar(1)]).run_cycle(scanner_cycle_timestamp=NOW)["records"][0]
        self.assertNotIn("trade_permission", rec)

    def test_gap_is_explicit(self):
        cache = ClosedOHLCVCache(); out = cache.ingest([bar(1), bar(4)], as_of=NOW); self.assertEqual(out["gap_state"], "DATA_GAP")

    def test_bounded_memory(self):
        cache = ClosedOHLCVCache(max_bars=2); cache.ingest([bar(3), bar(2), bar(1)], as_of=NOW); self.assertEqual(len(cache.bars()), 2)

    def test_ticker_failure_is_auditable(self):
        market = [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        result = StageABybitSweep(lambda: market, self.snapshot("A"), lambda s: (_ for _ in ()).throw(RuntimeError("ticker")), lambda s: [bar(2)]).run_cycle(scanner_cycle_timestamp=NOW)
        self.assertEqual(result["records"][0]["ticker_available"], False); self.assertIn("ticker_unknown", result["records"][0]["stage_a_reason"])

    def test_no_reconstructed_timeframes(self):
        result = StageABybitSweep(lambda: [{"symbol": "AUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}], self.snapshot("A"), lambda s: {}, lambda s: [bar(2)]).run_cycle(scanner_cycle_timestamp=NOW)
        self.assertEqual(result["records"][0]["timeframe"], "1"); self.assertEqual(result["records"][0]["data_form"], "TICKER_SNAPSHOT_PLUS_CLOSED_OHLCV")


if __name__ == "__main__": unittest.main()
