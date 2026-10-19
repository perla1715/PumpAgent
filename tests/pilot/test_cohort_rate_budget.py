from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from email.message import Message
import tempfile
import unittest

from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.live_data.adapters.coinalyze_pilot_price import CoinalyzeRateLimitError
from pumpagent.pilot.cohort_rate_budget import CoinalyzeCohortRateBudget
from pumpagent.pilot.pilot_search_profile import PilotAdmissionEvidenceJournal, evaluate_candidate
from pumpagent.pilot.runtime_handoff import NativeCandle, NativeCandleBatch, PilotRuntimeInputAssembler

NOW = datetime(2026, 10, 19, 12, 20, tzinfo=timezone.utc)
DIGEST = "e" * 64


def instruments(*symbols: str):
    return [{"symbol": symbol, "status": "Trading", "category": "linear", "contractType": "Perpetual", "quoteCoin": "USDT"} for symbol in symbols]


def ranks(symbols):
    return RankSnapshot("COINGECKO_MARKET_CAP_DESC", NOW, tuple(RankEntry(symbol[:-4].lower(), symbol[:-4], 100 + index) for index, symbol in enumerate(symbols)), "rank-fixture")


class FakeClock:
    def __init__(self): self.value = 0.0
    def monotonic(self): return self.value
    def sleep(self, seconds): self.value += seconds
    def utc(self): return NOW + timedelta(seconds=self.value)


class Source:
    def __init__(self, series, *, rate_limit=False):
        self.series, self.rate_limit = series, rate_limit
        self.calls = []
        self.active = False
    def begin_discovery_cycle(self): self.active = True; self.calls.append(("future", None)); return object()
    def end_discovery_cycle(self): self.active = False
    def resolve_bybit_symbol(self, symbol):
        if symbol not in self.series: raise ValueError("missing fixture")
        return symbol + ".6"
    def fetch_closed_native(self, *, bybit_symbol, provider_symbol, timeframe, evaluation_timestamp_utc):
        self.calls.append((timeframe, bybit_symbol, evaluation_timestamp_utc))
        if self.rate_limit:
            raise CoinalyzeRateLimitError("Coinalyze HTTP 429 from /ohlcv-history", 17.0)
        values = self.series[bybit_symbol]
        if timeframe == "1m":
            candles = tuple(NativeCandle(NOW - timedelta(minutes=20-index), value, value, value, value, 1, "1m", True) for index, value in enumerate(values))
        else:
            candles = (NativeCandle(NOW - timedelta(minutes=5), values[-1], values[-1], values[-1], values[-1], 1, "5m", True),)
        return NativeCandleBatch(bybit_symbol, provider_symbol, timeframe, candles, f"raw:{bybit_symbol}:{timeframe}", DIGEST)


class CohortRateBudgetTests(unittest.TestCase):
    def make_budget(self):
        clock = FakeClock()
        return CoinalyzeCohortRateBudget(monotonic_clock=clock.monotonic, utc_clock=clock.utc, sleeper=clock.sleep), clock

    def prepare(self, *, count=59, survivors=0, rate_limit=False):
        symbols = tuple(f"TOKEN{index:02d}USDT" for index in range(count))
        series = {symbol: tuple([100.0] * 21) for symbol in symbols}
        for symbol in symbols[:survivors]:
            series[symbol] = tuple([100.0] * 16 + [95.0, 100.0, 106.0, 106.0, 106.0])
        source = Source(series, rate_limit=rate_limit)
        budget, clock = self.make_budget()
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        prep = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"), rate_budget=budget).prepare_discovery(
            rank_snapshot=ranks(symbols), instruments=instruments(*symbols), tickers={symbol: {} for symbol in symbols}, evaluation_timestamp_utc=NOW,
        )
        return prep, source, budget, clock

    def test_40_units_per_minute_paces_the_41st_dispatch(self):
        budget, clock = self.make_budget()
        for _ in range(41): budget.succeed(budget.reserve(endpoint_class="/ohlcv-history"))
        self.assertEqual(len(budget.ledger), 41)
        self.assertEqual(budget.ledger[40].wait_seconds, 1.5)
        self.assertEqual(clock.value, 60.0)
        self.assertEqual(sum(entry.quota_units for entry in budget.ledger), 41)

    def test_n59_q0_uses_60_quota_units_and_no_5m(self):
        prep, source, budget, _ = self.prepare(survivors=0)
        self.assertEqual(len(prep.stage1_non_survivors), 59)
        self.assertEqual(len(prep.candidates), 0)
        self.assertEqual(len(budget.ledger), 60)
        self.assertEqual(sum(entry.endpoint_class == "/future-markets" for entry in budget.ledger), 1)
        self.assertEqual(sum(entry.timeframe == "1m" for entry in budget.ledger), 59)
        self.assertEqual(sum(entry.timeframe == "5m" for entry in budget.ledger), 0)
        self.assertFalse(any(call[0] == "5m" for call in source.calls))
        self.assertTrue(all(call[2] == NOW for call in source.calls if call[0] in {"1m", "5m"}))

    def test_n59_q1_and_q5_use_expected_quota_units(self):
        for survivors, expected in ((1, 61), (5, 65)):
            with self.subTest(survivors=survivors):
                prep, _, budget, _ = self.prepare(survivors=survivors)
                self.assertEqual(len(prep.stage1_survivors), survivors)
                self.assertEqual(len(prep.candidates), survivors)
                self.assertEqual(len(budget.ledger), expected)
                self.assertEqual(sum(entry.timeframe == "5m" for entry in budget.ledger), survivors)

    def test_stage_one_profile_is_the_same_canonical_final_profile(self):
        prep, _, _, _ = self.prepare(count=2, survivors=1)
        candidate = prep.candidates[0]
        final = evaluate_candidate(candidate)
        screen = prep.stage1_survivors[0].profile
        self.assertEqual((screen.p1_pass, screen.p2_pass, screen.p4_pass, screen.p5_pass, screen.pump_percent), (final.p1_pass, final.p2_pass, final.p4_pass, final.p5_pass, final.pump_percent))
        self.assertEqual(final.candidate.symbol, "TOKEN00USDT")

    def test_progressive_winner_matches_existing_selector_for_complete_evidence(self):
        symbols = ("ALPHAUSDT", "BETAUSDT")
        values = {
            "ALPHAUSDT": tuple([100.0] * 16 + [95.0, 100.0, 106.0, 106.0, 106.0]),
            "BETAUSDT": tuple([100.0] * 16 + [95.0, 105.0, 112.0, 112.0, 112.0]),
        }
        source = Source(values); budget, _ = self.make_budget(); root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        prep = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"), rate_budget=budget).prepare_discovery(
            rank_snapshot=ranks(symbols), instruments=instruments(*symbols), tickers={symbol: {} for symbol in symbols}, evaluation_timestamp_utc=NOW,
        )
        self.assertEqual(prep.selection.winner.candidate.symbol, "BETAUSDT")
        self.assertEqual(len(prep.candidates), 2)

    def test_adapter_captures_retry_after_without_exposing_credentials(self):
        from pumpagent.live_data.adapters.coinalyze_pilot_price import CoinalyzeNativePilotPriceClient
        headers = Message(); headers["Retry-After"] = "23"
        error = HTTPError("https://api.coinalyze.net/v1/ohlcv-history", 429, "too many", headers, None)
        client = CoinalyzeNativePilotPriceClient(api_key="test-key")
        with patch("pumpagent.live_data.adapters.coinalyze_pilot_price.request.urlopen", side_effect=error):
            with self.assertRaises(CoinalyzeRateLimitError) as caught:
                client._get_json("/ohlcv-history", {})
        self.assertEqual(caught.exception.retry_after_seconds, 23.0)

    def test_429_is_systemic_and_records_retry_after(self):
        symbols = ("ALPHAUSDT",)
        source = Source({"ALPHAUSDT": tuple([100.0] * 21)}, rate_limit=True)
        budget, _ = self.make_budget()
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        with self.assertRaisesRegex(Exception, "429"):
            PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"), rate_budget=budget).prepare_discovery(
                rank_snapshot=ranks(symbols), instruments=instruments(*symbols), tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW,
            )
        self.assertEqual(budget.ledger[-1].outcome, "ERROR")
        self.assertEqual(budget.ledger[-1].retry_after_seconds, 17.0)
        with self.assertRaises(RuntimeError): budget.reserve(endpoint_class="/ohlcv-history")

    def test_survivor_5m_failure_is_local_and_reconciliation_completes(self):
        class FiveFail(Source):
            def fetch_closed_native(self, **kwargs):
                if kwargs['timeframe'] == '5m':
                    from pumpagent.pilot.runtime_handoff import PilotRuntimeInputError
                    raise PilotRuntimeInputError('insufficient native closed 5m evidence for the rolling 20-minute profile')
                return super().fetch_closed_native(**kwargs)
        source = FiveFail({"ALPHAUSDT": tuple([100.0] * 16 + [95.0, 100.0, 106.0, 106.0, 106.0])})
        budget, _ = self.make_budget(); root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        prep = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name)/'journal'), rate_budget=budget).prepare_discovery(rank_snapshot=ranks(("ALPHAUSDT",)), instruments=instruments("ALPHAUSDT"), tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW)
        self.assertTrue(prep.full_universe_evaluation_complete)
        self.assertEqual(len(prep.candidates), 0)
        self.assertEqual(prep.candidate_failures[0].stage, 'native_5m_evidence')


if __name__ == '__main__': unittest.main()
