from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.adapters.coinalyze_pilot_price import CoinalyzeNativePilotPriceClient
from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.pilot.pilot_search_profile import CandidateInput, PilotAdmissionEvidenceJournal, PilotEvidenceError, PriceObservation, validate_case_manifest_boundary
from pumpagent.pilot.runtime_handoff import (
    EligibleInstrument, NativeCandle, NativeCandleBatch, PilotRuntimeInputAssembler,
    PilotRuntimeInputError, _validate_batches,
)

NOW = datetime(2026, 10, 18, 12, 20, tzinfo=timezone.utc)
DIGEST = "b" * 64


def instruments(*symbols: str) -> list[dict[str, str]]:
    return [{"symbol": symbol, "status": "Trading", "category": "linear", "contractType": "Perpetual", "quoteCoin": "USDT"} for symbol in symbols]


def ranks(**items: int) -> RankSnapshot:
    return RankSnapshot("COINGECKO_MARKET_CAP_DESC", NOW, tuple(RankEntry(name.lower(), name, rank) for name, rank in items.items()), "rank-fixture")


class FakeCoinalyze:
    def __init__(self, series: dict[str, tuple[float, ...]]) -> None:
        self.series = series
        self.calls: list[tuple[str, str, str]] = []

    def resolve_bybit_symbol(self, symbol: str) -> str:
        self.calls.append(("resolve", symbol, ""))
        return symbol + ".6"

    def fetch_closed_native(self, *, bybit_symbol: str, provider_symbol: str, timeframe: str, evaluation_timestamp_utc: datetime) -> NativeCandleBatch:
        self.calls.append(("fetch", bybit_symbol, timeframe))
        values = self.series[bybit_symbol]
        if timeframe == "1m":
            candles = tuple(NativeCandle(NOW - timedelta(minutes=20 - index), value, value, value, value, 1.0, "1m", True) for index, value in enumerate(values))
        else:
            candles = (NativeCandle(NOW - timedelta(minutes=5), values[-1], values[-1], values[-1], values[-1], 1.0, "5m", True),)
        return NativeCandleBatch(bybit_symbol, provider_symbol, timeframe, candles, f"raw:{bybit_symbol}:{timeframe}", DIGEST)


class PartialCoinalyze(FakeCoinalyze):
    def __init__(self, series, *, insufficient=(), unresolved=(), systemic=None):
        super().__init__(series)
        self.insufficient, self.unresolved, self.systemic = set(insufficient), set(unresolved), systemic

    def resolve_bybit_symbol(self, symbol: str) -> str:
        if self.systemic:
            raise PilotRuntimeInputError(self.systemic)
        if symbol in self.unresolved:
            raise PilotRuntimeInputError(f"No Coinalyze Bybit perpetual mapping for {symbol}")
        return super().resolve_bybit_symbol(symbol)

    def fetch_closed_native(self, *, bybit_symbol: str, provider_symbol: str, timeframe: str, evaluation_timestamp_utc: datetime) -> NativeCandleBatch:
        if self.systemic:
            raise PilotRuntimeInputError(self.systemic)
        if (bybit_symbol, timeframe) in self.insufficient:
            raise PilotRuntimeInputError(f"insufficient native closed {timeframe} evidence for the rolling 20-minute profile")
        return super().fetch_closed_native(bybit_symbol=bybit_symbol, provider_symbol=provider_symbol, timeframe=timeframe, evaluation_timestamp_utc=evaluation_timestamp_utc)


class TamperedJournal(PilotAdmissionEvidenceJournal):
    def verify(self):  # type: ignore[override]
        records = super().verify()
        return tuple({**record, "record_digest": "c" * 64} for record in records)


class PilotRuntimeHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.series = {
            "ALPHAUSDT": tuple([100.0] * 16 + [95.0, 100.0, 104.0, 104.0, 104.0]),
            "BETAUSDT": tuple([100.0] * 16 + [95.0, 100.0, 110.0, 109.0, 109.0]),
        }

    def prepare(self, *, rank=None, symbols=("ALPHAUSDT", "BETAUSDT"), journal_type=PilotAdmissionEvidenceJournal, selector=None):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = FakeCoinalyze(self.series)
        kwargs = {"selector": selector} if selector is not None else {}
        assembler = PilotRuntimeInputAssembler(source, journal_type(Path(root.name) / "journal"), **kwargs)
        result = assembler.prepare(rank_snapshot=rank or ranks(ALPHA=75, BETA=76), instruments=instruments(*symbols), tickers={symbol: {} for symbol in symbols}, evaluation_timestamp_utc=NOW)
        return result, source, Path(root.name) / "journal"

    def test_automatic_universe_requires_no_owner_symbol_and_evaluates_all(self):
        received = []
        def selector(candidates):
            received.extend(candidates)
            from pumpagent.pilot.pilot_search_profile import select_candidate
            return select_candidate(candidates)
        handoff, source, _ = self.prepare(selector=selector)
        self.assertEqual([candidate.symbol for candidate in received], ["ALPHAUSDT", "BETAUSDT"])
        self.assertEqual(handoff.selected.candidate.symbol, "BETAUSDT")
        self.assertNotIn("owner", " ".join(str(call) for call in source.calls).lower())

    def test_top50_candidate_never_enters_candidate_assembly(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = FakeCoinalyze(self.series)
        assembler = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
        handoff = assembler.prepare(rank_snapshot=ranks(ALPHA=75, BETA=50), instruments=instruments("ALPHAUSDT", "BETAUSDT"), tickers={"ALPHAUSDT": {}, "BETAUSDT": {}}, evaluation_timestamp_utc=NOW)
        self.assertEqual(handoff.selected.candidate.symbol, "ALPHAUSDT")
        self.assertNotIn(("resolve", "BETAUSDT", ""), source.calls)

    def test_unresolved_rank_fails_closed(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = FakeCoinalyze(self.series)
        assembler = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
        with self.assertRaisesRegex(PilotRuntimeInputError, "no current non-Top-50 eligible"):
            assembler.prepare(rank_snapshot=ranks(BETA=76), instruments=instruments("ALPHAUSDT"), tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW)
        self.assertEqual(source.calls, [])

    def test_coinalyze_native_symbol_and_timeframe_provenance_are_bound(self):
        handoff, _, _ = self.prepare(symbols=("ALPHAUSDT",))
        candidate = handoff.selected.candidate
        self.assertEqual(candidate.provider_symbol_literal, "ALPHAUSDT.6")
        self.assertEqual(candidate.provider, "coinalyze")
        self.assertEqual(candidate.venue, "BYBIT")
        self.assertEqual(candidate.native_timeframes, ("1m", "5m"))
        self.assertEqual(len(candidate.observations), 21)
        self.assertEqual(candidate.raw_evidence_ids, ("raw:ALPHAUSDT:1m", "raw:ALPHAUSDT:5m"))

    def test_open_or_constructed_timeframe_is_rejected(self):
        with self.assertRaises(PilotRuntimeInputError):
            NativeCandle(NOW, 1, 1, 1, 1, 1, "1m", False)
        with self.assertRaises(PilotRuntimeInputError):
            NativeCandle(NOW, 1, 1, 1, 1, 1, "3m", True)

    def test_missing_metadata_or_raw_identity_or_digest_fails_closed(self):
        with self.assertRaises(PilotEvidenceError):
            CandidateInput("ALPHAUSDT", "ALPHAUSDT.6", (PriceObservation(NOW - timedelta(minutes=1), 1), PriceObservation(NOW, 1)), NOW, ranks(ALPHA=75), {}, ("raw",), (DIGEST,))
        candle = NativeCandle(NOW - timedelta(minutes=5), 1, 1, 1, 1, 1, "5m", True)
        with self.assertRaises(PilotRuntimeInputError):
            NativeCandleBatch("ALPHAUSDT", "ALPHAUSDT.6", "5m", (candle,), "", DIGEST)
        with self.assertRaises(PilotRuntimeInputError):
            NativeCandleBatch("ALPHAUSDT", "ALPHAUSDT.6", "5m", (candle,), "raw", "bad")
        instrument = EligibleInstrument("ALPHAUSDT", {"market_type": "linear"})
        batch = NativeCandleBatch("ALPHAUSDT", "ALPHAUSDT.6", "5m", (candle,), "raw", DIGEST)
        with self.assertRaises(PilotRuntimeInputError):
            _validate_batches(instrument, "ALPHAUSDT.6", batch, batch, NOW)

    def test_journal_append_and_verify_are_mandatory_before_handoff(self):
        handoff, _, journal_path = self.prepare(symbols=("ALPHAUSDT",))
        records = PilotAdmissionEvidenceJournal(journal_path).verify()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["record_digest"], handoff.journal_record_digest)
        self.assertEqual(handoff.admission_state, "PRE_ADMISSION_NOT_STARTED")
        self.assertFalse(handoff.actual_pilot_admission_occurred)

    def test_digest_mismatch_and_no_qualifying_winner_prevent_handoff(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = FakeCoinalyze(self.series)
        with self.assertRaisesRegex(PilotRuntimeInputError, "verification"):
            PilotRuntimeInputAssembler(source, TamperedJournal(Path(root.name) / "journal")).prepare(rank_snapshot=ranks(ALPHA=75), instruments=instruments("ALPHAUSDT"), tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW)
        source = FakeCoinalyze({"ALPHAUSDT": tuple([100.0] * 21)})
        with self.assertRaisesRegex(PilotRuntimeInputError, "no candidate passed"):
            PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "empty")).prepare(rank_snapshot=ranks(ALPHA=75), instruments=instruments("ALPHAUSDT"), tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW)

    def test_candidate_local_insufficient_or_unresolved_symbols_do_not_stop_universe(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = PartialCoinalyze(self.series, insufficient={("ALPHAUSDT", "1m")}, unresolved={"GAMMAUSDT"})
        assembler = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
        preparation = assembler.prepare_discovery(
            rank_snapshot=ranks(ALPHA=75, BETA=76, GAMMA=77),
            instruments=instruments("ALPHAUSDT", "BETAUSDT", "GAMMAUSDT"),
            tickers={"ALPHAUSDT": {}, "BETAUSDT": {}, "GAMMAUSDT": {}}, evaluation_timestamp_utc=NOW,
        )
        self.assertEqual([candidate.symbol for candidate in preparation.candidates], ["BETAUSDT"])
        self.assertTrue(preparation.full_universe_evaluation_complete)
        self.assertEqual([(failure.symbol, failure.classification) for failure in preparation.candidate_failures], [
            ("ALPHAUSDT", "CANDIDATE_LOCAL_INSUFFICIENT_EVIDENCE"),
            ("GAMMAUSDT", "CANDIDATE_LOCAL_PROVIDER_SYMBOL_UNAVAILABLE"),
        ])
        self.assertFalse(preparation.selection.winner is None)

    def test_systemic_provider_failure_remains_terminal(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = PartialCoinalyze(self.series, systemic="Coinalyze HTTP 401 from /future-markets")
        assembler = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
        with self.assertRaisesRegex(PilotRuntimeInputError, "HTTP 401"):
            assembler.prepare_discovery(rank_snapshot=ranks(ALPHA=75, BETA=76), instruments=instruments("ALPHAUSDT", "BETAUSDT"), tickers={"ALPHAUSDT": {}, "BETAUSDT": {}}, evaluation_timestamp_utc=NOW)

    def test_insufficient_5m_is_candidate_local_and_does_not_create_profile_failure(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = PartialCoinalyze(self.series, insufficient={("ALPHAUSDT", "5m")})
        preparation = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal")).prepare_discovery(
            rank_snapshot=ranks(ALPHA=75, BETA=76), instruments=instruments("ALPHAUSDT", "BETAUSDT"),
            tickers={"ALPHAUSDT": {}, "BETAUSDT": {}}, evaluation_timestamp_utc=NOW,
        )
        self.assertEqual([failure.symbol for failure in preparation.candidate_failures], ["ALPHAUSDT"])
        self.assertEqual(preparation.candidate_failures[0].stage, "native_5m_evidence")
        self.assertEqual([candidate.symbol for candidate in preparation.candidates], ["BETAUSDT"])

    def test_all_local_insufficiencies_complete_without_profile_gate_counts(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = PartialCoinalyze(self.series, insufficient={("ALPHAUSDT", "1m"), ("BETAUSDT", "1m")})
        preparation = PilotRuntimeInputAssembler(source, PilotAdmissionEvidenceJournal(Path(root.name) / "journal")).prepare_discovery(
            rank_snapshot=ranks(ALPHA=75, BETA=76), instruments=instruments("ALPHAUSDT", "BETAUSDT"),
            tickers={"ALPHAUSDT": {}, "BETAUSDT": {}}, evaluation_timestamp_utc=NOW,
        )
        self.assertTrue(preparation.full_universe_evaluation_complete)
        self.assertEqual(preparation.candidates, ())
        self.assertEqual(preparation.selection.evaluations, ())
        self.assertEqual(len(preparation.candidate_failures), 2)

    def test_legacy_admission_and_shadow_runtime_are_not_called_and_manifest_stays_unchanged(self):
        import inspect
        import pumpagent.pilot.runtime_handoff as module
        self.prepare(symbols=("ALPHAUSDT",))
        source = inspect.getsource(module)
        self.assertNotIn("run_shadow_event", source)
        self.assertNotIn("ShadowAttentionAdmission", source)
        self.assertNotIn("place_order", source)
        self.assertNotIn("execute_order", source)
        header = "row_id,case_id,role,status\n"
        rows = "1,HNT,DEVELOPMENT,ENVELOPE_ACCEPTED\n2,BOME,DEVELOPMENT,ENVELOPE_ACCEPTED\n3,APR,DEVELOPMENT,ENVELOPE_ACCEPTED\n4,SOPH,DEVELOPMENT,ENVELOPE_ACCEPTED\n5,CHILLGUY,DEVELOPMENT,ENVELOPE_ACCEPTED\n7,PILOT_EVENT_001,LIVE_PILOT,NOT_STARTED\n"
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        manifest = Path(root.name) / "manifest.csv"; manifest.write_text(header + rows, encoding="utf-8")
        before = manifest.read_bytes(); result = validate_case_manifest_boundary(manifest)
        self.assertTrue(result["pilot_event_not_started"]); self.assertEqual(before, manifest.read_bytes())

    def test_cycle_local_market_resolution_fetches_once_for_59_candidates_and_118_native_batches(self):
        symbols = tuple(f"TOKEN{index:02d}USDT" for index in range(59))
        calls: list[str] = []
        markets = [
            {"symbol": f"{symbol}.6", "quote_asset": "USDT", "exchange": "BYBIT"}
            for symbol in symbols
        ]
        history = [
            {"t": int((NOW - timedelta(minutes=25 - index)).timestamp()), "o": 100, "h": 100,
             "l": 100, "c": 100, "v": 1}
            for index in range(25)
        ]
        def request_json(path, _params):
            calls.append(path)
            return markets if path == "/future-markets" else [{"history": history}]
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        client = CoinalyzeNativePilotPriceClient(request_json=request_json)
        preparation = PilotRuntimeInputAssembler(
            client, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"),
        ).prepare_discovery(
            rank_snapshot=ranks(**{symbol[:-4]: 100 + index for index, symbol in enumerate(symbols)}),
            instruments=instruments(*symbols), tickers={symbol: {} for symbol in symbols},
            evaluation_timestamp_utc=NOW,
        )
        self.assertEqual(len(preparation.candidates), 59)
        self.assertEqual(calls.count("/future-markets"), 1)
        self.assertEqual(calls.count("/ohlcv-history"), 118)
        self.assertEqual(preparation.market_resolution_provenance["market_count"], 59)
        self.assertEqual(preparation.market_resolution_provenance["endpoint"], "/future-markets")

    def test_cycle_resolution_ends_after_discovery_and_missing_mapping_is_candidate_local(self):
        calls: list[str] = []
        history = [
            {"t": int((NOW - timedelta(minutes=25 - index)).timestamp()), "o": 100, "h": 100,
             "l": 100, "c": 100, "v": 1}
            for index in range(25)
        ]
        def request_json(path, _params):
            calls.append(path)
            if path == "/future-markets":
                return [{"symbol": "BETAUSDT.6", "quote_asset": "USDT", "exchange": "BYBIT"}]
            return [{"history": history}]
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        client = CoinalyzeNativePilotPriceClient(request_json=request_json)
        assembler = PilotRuntimeInputAssembler(client, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
        preparation = assembler.prepare_discovery(
            rank_snapshot=ranks(ALPHA=75, BETA=76), instruments=instruments("ALPHAUSDT", "BETAUSDT"),
            tickers={"ALPHAUSDT": {}, "BETAUSDT": {}}, evaluation_timestamp_utc=NOW,
        )
        self.assertEqual(calls.count("/future-markets"), 1)
        self.assertEqual([candidate.symbol for candidate in preparation.candidates], ["BETAUSDT"])
        self.assertEqual(preparation.candidate_failures[0].classification, "CANDIDATE_LOCAL_PROVIDER_SYMBOL_UNAVAILABLE")
        client.resolve_bybit_symbol("BETAUSDT")
        self.assertEqual(calls.count("/future-markets"), 2)

    def test_shared_market_resolution_rate_limit_and_malformed_response_are_terminal(self):
        for payload in (PilotRuntimeInputError("Coinalyze HTTP 429 from /future-markets"), {}):
            root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
            def request_json(_path, _params, payload=payload):
                if isinstance(payload, Exception):
                    raise payload
                return payload
            client = CoinalyzeNativePilotPriceClient(request_json=request_json)
            assembler = PilotRuntimeInputAssembler(client, PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
            with self.assertRaises(PilotRuntimeInputError):
                assembler.prepare_discovery(
                    rank_snapshot=ranks(ALPHA=75), instruments=instruments("ALPHAUSDT"),
                    tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW,
                )

    def test_injected_coinalyze_client_parses_native_closed_price_without_live_network(self):
        def request_json(path, _params):
            if path == "/future-markets":
                return [{"symbol": "ALPHAUSDT.6", "quote_asset": "USDT", "exchange": "BYBIT"}]
            return [{"history": [
                {"t": int((NOW - timedelta(minutes=21)).timestamp()), "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 3},
                {"t": int((NOW - timedelta(minutes=5)).timestamp()), "o": 2, "h": 3, "l": 2, "c": 2.5, "v": 4},
            ]}]
        client = CoinalyzeNativePilotPriceClient(request_json=request_json)
        symbol = client.resolve_bybit_symbol("ALPHAUSDT")
        batch = client.fetch_closed_native(bybit_symbol="ALPHAUSDT", provider_symbol=symbol, timeframe="5m", evaluation_timestamp_utc=NOW)
        self.assertEqual(symbol, "ALPHAUSDT.6")
        self.assertEqual(batch.timeframe, "5m")
        self.assertEqual(len(batch.candles), 2)
        self.assertEqual(batch.candles[-1].close, 2.5)


if __name__ == "__main__":
    unittest.main()
