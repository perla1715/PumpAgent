from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.live_data.contextual_attention_prospective import ProspectiveCollector
from pumpagent.live_data.contextual_attention_test_v1 import ExperimentJournal
from pumpagent.live_data.scanner_funnel_stage_a import StageABybitSweep

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def bar(minute=1):
    return {"timestamp": NOW - timedelta(minutes=minute), "open": 1, "high": 1.1, "low": .9, "close": 1.05, "volume": 10, "closed": True, "native_or_reconstructed": "NATIVE"}


class ProspectiveCollectorTests(unittest.TestCase):
    def test_two_active_profiles_and_append_only_dedupe(self):
        market = [{"symbol": "ALPHAUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        rank = RankSnapshot("TEST", NOW, (RankEntry("ALPHA", "ALPHA", 75),), "rank")
        sweep = StageABybitSweep(lambda: market, rank, lambda symbols: {"ALPHAUSDT": {"timestamp": NOW}}, lambda symbol: [bar()])
        with tempfile.TemporaryDirectory() as directory:
            journal = ExperimentJournal(Path(directory) / "prospective.jsonl")
            collector = ProspectiveCollector(sweep, lambda symbol: [bar()], journal, "CONTEXTUAL_ATTENTION_TEST_V1_PROSPECTIVE_BLOCK_001_2026-10-08")
            first = collector.run_cycle(cycle_timestamp=NOW)
            second = collector.run_cycle(cycle_timestamp=NOW)
            self.assertEqual(first["records_appended"], 2); self.assertEqual(second["duplicate_noop"], 2)
            self.assertEqual(len(journal.records()), 2)
            self.assertEqual({r["profile_id"] for r in journal.records()}, {"P_PRICE_LED", "P_PRICE_VOLUME_CONTEXT"})
            self.assertTrue(all(r["prospective"] and r["test_only"] for r in journal.records()))

    def test_open_provider_bar_is_not_passed_to_stage_b(self):
        market = [{"symbol": "ALPHAUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        rank = RankSnapshot("TEST", NOW, (RankEntry("ALPHA", "ALPHA", 75),), "rank")
        sweep = StageABybitSweep(lambda: market, rank, lambda symbols: {"ALPHAUSDT": {"timestamp": NOW}}, lambda symbol: [bar(), {**bar(2), "closed": False}])
        with tempfile.TemporaryDirectory() as directory:
            journal = ExperimentJournal(Path(directory) / "prospective.jsonl")
            result = ProspectiveCollector(sweep, lambda symbol: [bar(), {**bar(2), "closed": False}], journal, "B").run_cycle(cycle_timestamp=NOW)
            self.assertEqual(result["stage_b_records_evaluated"], 2)
            self.assertTrue(all(r["evidence_completeness"] == "COMPLETE_FOR_STAGE_B_SKELETON" for r in journal.records()))

    def test_top50_is_not_evaluated(self):
        market = [{"symbol": "BTCUSDT.6", "quote_asset": "USDT", "is_perpetual": True, "active": True, "category": "linear"}]
        rank = RankSnapshot("TEST", NOW, (RankEntry("BTC", "BTC", 1),), "rank")
        sweep = StageABybitSweep(lambda: market, rank, lambda symbols: {"BTCUSDT": {"timestamp": NOW}}, lambda symbol: [bar()])
        with tempfile.TemporaryDirectory() as directory:
            journal = ExperimentJournal(Path(directory) / "prospective.jsonl")
            result = ProspectiveCollector(sweep, lambda symbol: [bar()], journal, "B").run_cycle(cycle_timestamp=NOW)
            self.assertEqual(result["stage_b_records_evaluated"], 0); self.assertEqual(journal.records(), [])


if __name__ == "__main__": unittest.main()
