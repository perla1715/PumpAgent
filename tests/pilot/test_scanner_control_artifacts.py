from datetime import datetime, timedelta, timezone

from pumpagent.pilot.launch_orchestrator import FirstShadowPilotLaunchResult, LaunchStatus
from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.pilot.pilot_search_profile import CandidateInput, PriceObservation, select_candidate
from pumpagent.pilot.runtime_handoff import GuardedPreAdmissionHandoff
from pumpagent.pilot.scanner_control_artifacts import (
    AuthoritativeScannerResult,
    deterministic_near_misses,
    generate_control_artifacts,
)


def _captured(status=LaunchStatus.NO_QUALIFYING_CANDIDATE):
    result = FirstShadowPilotLaunchResult(status, "accepted authoritative result")
    return AuthoritativeScannerResult.capture(
        run_id="offline-remediation-replay",
        evaluation_timestamp_utc=datetime(2026, 10, 19, 11, 7, tzinfo=timezone.utc),
        result=result,
    )


def _selected_result():
    now = datetime(2026, 10, 19, 11, 7, tzinfo=timezone.utc)
    rank = RankSnapshot("fixture", now, (RankEntry("alpha", "ALPHA", 75),), "rank-fixture")
    candidate = CandidateInput(
        symbol="ALPHAUSDT", provider_symbol_literal="ALPHAUSDT.6",
        observations=tuple(PriceObservation(now - timedelta(minutes=20 - index), price) for index, price in enumerate([100.0] * 16 + [95.0, 100.0, 106.0, 106.0, 106.0])),
        evaluation_timestamp_utc=now, rank_snapshot=rank,
        instrument_metadata={"market_type": "linear", "contract_type": "perpetual", "quote_asset": "USDT", "price_unit": "USDT"},
        raw_evidence_ids=("raw:1m", "raw:5m"), raw_evidence_digests=("a" * 64, "b" * 64),
    )
    winner = select_candidate((candidate,)).winner
    assert winner is not None
    handoff = GuardedPreAdmissionHandoff(winner, "c" * 64, 1, "00000000000000000001.json")
    return FirstShadowPilotLaunchResult(LaunchStatus.ADMITTED_WAITING_FOR_FRESH_SNAPSHOT, "selected", handoff=handoff, admission=object())


def test_nullable_pump_metric_remains_unknown_without_float_crash():
    rows = ({"SYMBOL": "NULLUSDT", "20M_PUMP_PERCENT": None},)
    artifact = generate_control_artifacts(_captured(), rows)
    assert artifact.status == "COMPLETE"
    assert artifact.near_misses[0]["20M_PUMP_PERCENT"] is None


def test_missing_metrics_sort_after_numeric_rows_deterministically():
    rows = (
        {"SYMBOL": "ZUSDT", "20M_PUMP_PERCENT": None},
        {"SYMBOL": "BUSDT", "20M_PUMP_PERCENT": 2.0},
        {"SYMBOL": "AUSDT", "20M_PUMP_PERCENT": None},
        {"SYMBOL": "CUSDT", "20M_PUMP_PERCENT": 8.0},
    )
    assert [row["SYMBOL"] for row in deterministic_near_misses(rows)] == ["CUSDT", "BUSDT", "AUSDT", "ZUSDT"]


def test_writer_failure_preserves_authoritative_no_candidate_result():
    captured = _captured()
    artifact = generate_control_artifacts(captured, ({"SYMBOL": "X", "20M_PUMP_PERCENT": 1.0},), near_miss_writer=lambda _: (_ for _ in ()).throw(OSError("disk failed")))
    assert artifact.status == "FAILED"
    assert artifact.error == "OSError: disk failed"
    assert artifact.authoritative is captured
    assert artifact.authoritative.discovery_status == LaunchStatus.NO_QUALIFYING_CANDIDATE.value


def test_diagnostic_sort_does_not_invoke_or_change_selector():
    # The diagnostic utility accepts prebuilt rows only; its result is independent
    # of accepted selection and preserves the input's original diagnostic values.
    rows = ({"SYMBOL": "B", "20M_PUMP_PERCENT": None}, {"SYMBOL": "A", "20M_PUMP_PERCENT": 3.0})
    assert [row["SYMBOL"] for row in deterministic_near_misses(rows)] == ["A", "B"]
    assert rows[0]["20M_PUMP_PERCENT"] is None


def test_selected_candidate_result_is_preserved_regardless_of_nullable_diagnostics():
    result = _selected_result()
    captured = AuthoritativeScannerResult.capture(
        run_id="selected-offline", evaluation_timestamp_utc=datetime(2026, 10, 19, 11, 7, tzinfo=timezone.utc), result=result,
    )
    artifact = generate_control_artifacts(captured, ({"SYMBOL": "OTHER", "20M_PUMP_PERCENT": None},))
    assert artifact.status == "COMPLETE"
    assert artifact.authoritative.discovery_status == LaunchStatus.ADMITTED_WAITING_FOR_FRESH_SNAPSHOT.value
    assert artifact.authoritative.selected_symbol == "ALPHAUSDT"


def test_historical_stopped_package_can_be_read_without_mutation():
    # The known stopped run is deliberately represented as offline input only.
    rows = ({"SYMBOL": "0GUSDT", "20M_PUMP_PERCENT": None},)
    before = tuple(dict(row) for row in rows)
    deterministic_near_misses(rows)
    assert rows == before
