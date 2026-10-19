from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.pilot.pilot_search_profile import PilotAdmissionEvidenceJournal
from pumpagent.pilot.runtime_handoff import NativeCandle, NativeCandleBatch, PilotRuntimeInputAssembler, PilotRuntimeInputError, _coverage_from_batch
from pumpagent.pilot.scanner_evidence_provenance import (
    evidence_digest, evidence_payload, replay_canonical_profile, write_stage_one_evidence_bundle,
)

NOW = datetime(2026, 10, 19, 18, 30, tzinfo=timezone.utc)
DIGEST = "a" * 64


def rank_snapshot():
    return RankSnapshot("fixture", NOW, (RankEntry("alpha", "ALPHA", 75),), "rank-fixture")


def instrument():
    return {"symbol": "ALPHAUSDT", "status": "Trading", "category": "linear", "contractType": "Perpetual", "quoteCoin": "USDT"}


def batch(values, *, future=False):
    candles = tuple(
        NativeCandle(NOW - timedelta(minutes=20 - index) + (timedelta(minutes=2) if future and index == len(values)-1 else timedelta()), value, value, value, value, 1.0, "1m", True)
        for index, value in enumerate(values)
    )
    return NativeCandleBatch("ALPHAUSDT", "ALPHAUSDT.6", "1m", candles, "coinalyze:fixture", DIGEST)


class Source:
    def __init__(self, one): self.one = one
    def resolve_bybit_symbol(self, symbol): return symbol + ".6"
    def fetch_closed_native(self, *, timeframe, **kwargs):
        if timeframe == "1m": return self.one
        return NativeCandleBatch("ALPHAUSDT", "ALPHAUSDT.6", "5m", (NativeCandle(NOW - timedelta(minutes=5), 1,1,1,1,1,"5m",True),), "coinalyze:five", "b" * 64)


def prepare(one):
    root = tempfile.TemporaryDirectory()
    assembler = PilotRuntimeInputAssembler(Source(one), PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
    prep = assembler.prepare_discovery(rank_snapshot=rank_snapshot(), instruments=[instrument()], tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW)
    return root, prep


def test_evaluable_evidence_is_deterministic_and_replays_through_canonical_evaluator():
    root, prep = prepare(batch([1.0] * 21))
    try:
        record = prep.stage1_evidence[0]
        profile = prep.stage1_non_survivors[0].profile
        payload = evidence_payload(record, profile=profile, rank_snapshot=prep.rank_snapshot)
        assert evidence_digest(payload) == evidence_digest(payload)
        changed = dict(payload); changed["symbol"] = "OTHERUSDT"
        assert evidence_digest(payload) != evidence_digest(changed)
        replay = replay_canonical_profile(payload)
        assert (replay.p1_pass, replay.p2_pass, replay.p4_pass, replay.p5_pass, replay.pump_percent, replay.max_running_high_pullback_percent) == (profile.p1_pass, profile.p2_pass, profile.p4_pass, profile.p5_pass, profile.pump_percent, profile.max_running_high_pullback_percent)
        package = Path(root.name) / "package"; package.mkdir(); (package / "00_RUN_SUMMARY.txt").write_text("safe")
        result = write_stage_one_evidence_bundle(package, prep)
        assert result["entries"][0]["evidence_reference"] == "stage1_evidence/ALPHAUSDT.json"
        artifact = json.loads((package / result["entries"][0]["evidence_reference"]).read_text())
        assert artifact["candles"][0]["closed"] is True
        assert artifact["coverage"]["admissible_closed_bucket_count"] == 21
        assert "test-key" not in json.dumps(artifact)
        assert "15_STAGE1_EVIDENCE_INDEX.json" in (package / "MANIFEST.sha256").read_text()
        assert "stage1_evidence/ALPHAUSDT.json" in (package / "MANIFEST.sha256").read_text()
    finally:
        root.cleanup()


def test_insufficient_and_future_evidence_preserve_coverage_without_profile_outcome():
    root, prep = prepare(batch([1.0] * 5))
    try:
        assert not prep.stage1_non_survivors
        record = prep.stage1_evidence[0]
        assert record.batch is None
        assert record.coverage.returned_record_count == 5
        assert record.coverage.admissible_closed_bucket_count == 5
        assert record.coverage.expected_required_bucket_count == 20
        assert record.coverage.first_admissible_bucket_utc is not None
        assert record.failure_reason == "insufficient native closed 1m evidence for the rolling 20-minute profile"
        payload = evidence_payload(record, profile=None, rank_snapshot=prep.rank_snapshot)
        assert payload["canonical_evaluation"] is None
        assert payload["coverage"]["complete_for_profile"] is False
    finally:
        root.cleanup()


def test_future_bucket_is_not_admissible_and_remains_systemic():
    future_batch = batch([1.0] * 20, future=True)
    coverage = _coverage_from_batch(future_batch, NOW)
    assert coverage.returned_record_count == 20
    assert coverage.admissible_closed_bucket_count == 19
    assert coverage.complete_for_profile is False
    root = tempfile.TemporaryDirectory()
    try:
        assembler = PilotRuntimeInputAssembler(Source(future_batch), PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
        try:
            assembler.prepare_discovery(rank_snapshot=rank_snapshot(), instruments=[instrument()], tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW)
        except PilotRuntimeInputError as exc:
            assert "future evidence" in str(exc)
        else:
            raise AssertionError("future evidence must not become a candidate-local profile result")
    finally:
        root.cleanup()

def test_control_index_covers_provider_symbol_unavailable_without_fake_evidence():
    class MissingMapping(Source):
        def resolve_bybit_symbol(self, symbol):
            raise PilotRuntimeInputError("No Coinalyze Bybit perpetual mapping for ALPHAUSDT")
    root = tempfile.TemporaryDirectory()
    try:
        assembler = PilotRuntimeInputAssembler(MissingMapping(batch([1.0] * 21)), PilotAdmissionEvidenceJournal(Path(root.name) / "journal"))
        prep = assembler.prepare_discovery(rank_snapshot=rank_snapshot(), instruments=[instrument()], tickers={"ALPHAUSDT": {}}, evaluation_timestamp_utc=NOW)
        package = Path(root.name) / "package"; package.mkdir()
        result = write_stage_one_evidence_bundle(package, prep)
        entry = result["entries"][0]
        assert entry["terminal_state"] == "CANDIDATE_LOCAL_PROVIDER_SYMBOL_UNAVAILABLE"
        payload = json.loads((package / entry["evidence_reference"]).read_text())
        assert payload["provider_symbol"] is None
        assert payload["candles"] == []
        assert payload["canonical_evaluation"] is None
    finally:
        root.cleanup()
