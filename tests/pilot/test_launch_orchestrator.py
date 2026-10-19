from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.pilot.launch_orchestrator import (
    FirstShadowPilotLaunchOrchestrator,
    LaunchStatus,
    build_fresh_coinalyze_5m_snapshot,
)
from pumpagent.pilot.pilot_search_profile import PilotAdmissionEvidenceJournal
from pumpagent.pilot.runtime_handoff import NativeCandle, NativeCandleBatch, PilotRuntimeInputAssembler, PilotRuntimeInputError
from pumpagent.runtime.modules.watchlist.manager import WatchlistManager
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator

NOW = datetime(2026, 10, 19, 12, 20, tzinfo=timezone.utc)
DIGEST = "d" * 64


def row(symbol: str) -> dict[str, str]:
    return {"symbol": symbol, "status": "Trading", "category": "linear", "contractType": "Perpetual", "quoteCoin": "USDT"}


class Ranks:
    def __init__(self, **values: int) -> None:
        self.snapshot = RankSnapshot("COINGECKO_MARKET_CAP_DESC", NOW, tuple(RankEntry(name.lower(), name, rank) for name, rank in values.items()), "rank-fixture")

    def fetch(self) -> RankSnapshot:
        return self.snapshot


class FailingRanks:
    def fetch(self) -> RankSnapshot:
        raise RuntimeError("rank transport unavailable")


class FixedRanks:
    def __init__(self, fetched_at, **values):
        self.snapshot = RankSnapshot("COINGECKO_MARKET_CAP_DESC", fetched_at, tuple(RankEntry(name.lower(), name, rank) for name, rank in values.items()), "rank-fixture")

    def fetch(self):
        return self.snapshot


class FakeCoinalyze:
    def __init__(self, values: dict[str, tuple[float, ...]], *, fail: bool = False) -> None:
        self.values, self.fail = values, fail
        self.calls: list[tuple[str, str]] = []

    def resolve_bybit_symbol(self, symbol: str) -> str:
        if self.fail: raise RuntimeError("provider unavailable")
        self.calls.append(("resolve", symbol))
        return symbol.upper() + ".6"

    def fetch_closed_native(self, *, bybit_symbol: str, provider_symbol: str, timeframe: str, evaluation_timestamp_utc: datetime) -> NativeCandleBatch:
        if self.fail: raise RuntimeError("provider unavailable")
        self.calls.append((timeframe, bybit_symbol))
        values = self.values[bybit_symbol]
        if timeframe == "1m":
            candles = tuple(NativeCandle(NOW - timedelta(minutes=20 - i), v, v, v, v, 1.0, "1m", True) for i, v in enumerate(values))
        else:
            closes = (values[-1] - 2.0, values[-1] - 1.0, values[-1])
            candles = tuple(NativeCandle(evaluation_timestamp_utc - timedelta(minutes=15 - i * 5), close, close, close, close, 1.0, "5m", True) for i, close in enumerate(closes))
        return NativeCandleBatch(bybit_symbol, provider_symbol, timeframe, candles, f"raw:{bybit_symbol}:{timeframe}:{int(evaluation_timestamp_utc.timestamp())}", DIGEST)


class LocallyInsufficientCoinalyze(FakeCoinalyze):
    def fetch_closed_native(self, *, bybit_symbol, provider_symbol, timeframe, evaluation_timestamp_utc):
        if bybit_symbol == "ALPHAUSDT" and timeframe == "1m":
            raise PilotRuntimeInputError("insufficient native closed 1m evidence for the rolling 20-minute profile")
        return super().fetch_closed_native(bybit_symbol=bybit_symbol, provider_symbol=provider_symbol, timeframe=timeframe, evaluation_timestamp_utc=evaluation_timestamp_utc)


class LaunchOrchestratorTests(unittest.TestCase):
    def make_owner(self, *, ranks: Ranks | None = None, source: FakeCoinalyze | None = None, symbols=("ALPHAUSDT",)):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        source = source or FakeCoinalyze({"ALPHAUSDT": tuple([100.0] * 16 + [95.0, 100.0, 106.0, 106.0, 106.0])})
        journal = PilotAdmissionEvidenceJournal(Path(root.name) / "journal")
        owner = FirstShadowPilotLaunchOrchestrator(
            rank_source=ranks or Ranks(ALPHA=75),
            instruments_loader=lambda: {"result": {"list": [row(symbol) for symbol in symbols]}},
            tickers_loader=lambda: {"result": {"list": [{"symbol": symbol} for symbol in symbols]}},
            price_source=source,
            journal=journal,
            watchlist=WatchlistManager(),
            runtime=RuntimeOrchestrator(),
            clock=lambda: NOW,
        )
        return owner, journal, source

    def test_happy_path_is_automatic_then_uses_fresh_runtime_snapshot(self):
        owner, _, source = self.make_owner()
        phase_a = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
        self.assertIs(phase_a.status, LaunchStatus.ADMITTED_WAITING_FOR_FRESH_SNAPSHOT)
        self.assertIsNotNone(phase_a.admission)
        self.assertTrue(phase_a.manifest_transition_required)
        self.assertNotIn("owner", " ".join(str(value) for value in source.calls).lower())
        phase_b = owner.continue_once(phase_a.admission, evaluation_timestamp_utc=NOW + timedelta(minutes=10))
        self.assertIs(phase_b.status, LaunchStatus.RUNTIME_CYCLE_COMPLETED, phase_b.reason)
        self.assertIsNotNone(phase_b.runtime_cycle)
        self.assertEqual(phase_b.runtime_cycle.episode_id, phase_a.admission.episode.episode_id)

    def test_no_candidate_creates_no_admission_or_runtime_cycle(self):
        source = FakeCoinalyze({"ALPHAUSDT": tuple([100.0] * 21)})
        owner, _, _ = self.make_owner(source=source)
        result = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
        self.assertIs(result.status, LaunchStatus.NO_QUALIFYING_CANDIDATE)
        self.assertIsNone(result.admission)
        self.assertEqual(owner.watchlist.list_active(), ())

    def test_full_universe_with_local_insufficiency_and_nonqualifier_is_no_candidate(self):
        source = LocallyInsufficientCoinalyze({"ALPHAUSDT": tuple([100.0] * 21), "BETAUSDT": tuple([100.0] * 21)})
        owner, _, _ = self.make_owner(ranks=Ranks(ALPHA=75, BETA=76), source=source, symbols=("ALPHAUSDT", "BETAUSDT"))
        result = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
        self.assertIs(result.status, LaunchStatus.NO_QUALIFYING_CANDIDATE)
        assert result.discovery_preparation is not None
        self.assertTrue(result.discovery_preparation.full_universe_evaluation_complete)
        self.assertEqual(len(result.discovery_preparation.candidate_failures), 1)
        self.assertEqual(len(result.discovery_preparation.selection.evaluations), 1)

    def test_rank_fetched_during_discovery_is_not_false_future_relative_to_requested_time(self):
        owner, _, _ = self.make_owner(ranks=FixedRanks(NOW, ALPHA=75))
        result = owner.discover_and_admit(evaluation_timestamp_utc=NOW - timedelta(seconds=1))
        self.assertIs(result.status, LaunchStatus.ADMITTED_WAITING_FOR_FRESH_SNAPSHOT)
        self.assertIsNotNone(result.discovery_preparation)
        self.assertTrue(result.discovery_preparation.full_universe_evaluation_complete)

    def test_genuinely_future_rank_snapshot_remains_terminal(self):
        owner, _, _ = self.make_owner(ranks=FixedRanks(NOW + timedelta(minutes=1), ALPHA=75))
        result = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
        self.assertIs(result.status, LaunchStatus.DISCOVERY_FAILED)
        self.assertIn("later than", result.reason)

    def test_stale_rank_snapshot_remains_not_qualifying(self):
        owner, _, _ = self.make_owner(ranks=FixedRanks(NOW - timedelta(days=2), ALPHA=75))
        result = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
        self.assertIs(result.status, LaunchStatus.NO_QUALIFYING_CANDIDATE)
        assert result.discovery_preparation is not None
        self.assertEqual(result.discovery_preparation.selection.evaluations[0].top50_status, "TOP50_STATUS_UNRESOLVED")

    def test_top50_and_unresolved_rank_fail_closed_before_provider_assembly(self):
        for ranks in (Ranks(ALPHA=50), Ranks(BETA=75)):
            with self.subTest(ranks=ranks.snapshot.entries):
                owner, _, source = self.make_owner(ranks=ranks)
                result = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
                self.assertIs(result.status, LaunchStatus.EVIDENCE_ASSEMBLY_FAILED)
                self.assertEqual(source.calls, [])
                self.assertEqual(owner.watchlist.list_active(), ())

    def test_multiple_candidates_use_accepted_deterministic_winner(self):
        source = FakeCoinalyze({
            "ALPHAUSDT": tuple([100.0] * 16 + [95.0, 100.0, 106.0, 106.0, 106.0]),
            "BETAUSDT": tuple([100.0] * 16 + [95.0, 105.0, 112.0, 112.0, 112.0]),
        })
        owner, _, _ = self.make_owner(ranks=Ranks(ALPHA=75, BETA=76), source=source, symbols=("ALPHAUSDT", "BETAUSDT"))
        result = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
        self.assertEqual(result.admission.receipt.symbol, "BETAUSDT")

    def test_provider_failure_is_not_no_candidate(self):
        owner, _, _ = self.make_owner(source=FakeCoinalyze({"ALPHAUSDT": tuple([100.0] * 21)}, fail=True))
        result = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
        self.assertIs(result.status, LaunchStatus.EVIDENCE_ASSEMBLY_FAILED)
        self.assertEqual(owner.watchlist.list_active(), ())

    def test_tampered_journal_and_duplicate_handoff_cannot_admit_twice(self):
        owner, journal, source = self.make_owner()
        handoff = PilotRuntimeInputAssembler(source, journal).prepare(rank_snapshot=Ranks(ALPHA=75).fetch(), instruments=[row("ALPHAUSDT")], tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW)
        (journal.directory / handoff.journal_record_name).write_text("{}", encoding="utf-8")
        tampered = owner.admit_verified_handoff(handoff, admission_timestamp_utc=NOW)
        self.assertIs(tampered.status, LaunchStatus.PRE_ADMISSION_VERIFICATION_FAILED)
        self.assertEqual(owner.watchlist.list_active(), ())
        owner, _, _ = self.make_owner()
        admitted = owner.discover_and_admit(evaluation_timestamp_utc=NOW)
        duplicate = owner.admit_verified_handoff(admitted.handoff, admission_timestamp_utc=NOW)
        self.assertIs(duplicate.status, LaunchStatus.ADMISSION_FAILED)
        self.assertEqual(len(owner.watchlist.list_active()), 1)

    def test_fresh_closed_identity_matched_snapshot_is_required(self):
        owner, _, source = self.make_owner()
        admitted = owner.discover_and_admit(evaluation_timestamp_utc=NOW).admission
        batch = source.fetch_closed_native(bybit_symbol="ALPHAUSDT", provider_symbol="ALPHAUSDT.6", timeframe="5m", evaluation_timestamp_utc=NOW)
        with self.assertRaises(Exception):
            build_fresh_coinalyze_5m_snapshot(admitted, batch, NOW)
        phase_b = owner.continue_once(admitted, evaluation_timestamp_utc=NOW + timedelta(minutes=10))
        self.assertIs(phase_b.status, LaunchStatus.RUNTIME_CYCLE_COMPLETED, phase_b.reason)

    def test_no_live_or_manifest_or_order_interfaces_are_part_of_owner(self):
        import inspect
        import pumpagent.pilot.launch_orchestrator as module
        source = inspect.getsource(module)
        for forbidden in ("place_order", "create_order", "submit_order", "ShadowAttentionAdmission", "run_shadow_event", "CASE_MANIFEST"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
