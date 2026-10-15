from datetime import datetime, timedelta, timezone

from pumpagent.live_data.adapters.coinalyze_oi import CoinalyzeOIRecord
from pumpagent.live_data.minimal_pump_start_stream import (
    MinimalPumpStartStream, INSUFFICIENT_EVIDENCE, NO_PUMP_START, START_CANDIDATE,
    START_CONFIRMED, ACTIVE_PUMP_EVENT, PARTICIPATION_CONTEXT_AVAILABLE, PARTICIPATION_INSUFFICIENT_EVIDENCE,
)

UTC = timezone.utc

def bar(ts, *, open_=100, close=100.1, high=None, low=None, closed=True, native="NATIVE"):
    return {"timestamp": ts.isoformat(), "open": open_, "close": close, "high": close if high is None else high,
            "low": min(open_, close) if low is None else low, "closed": closed, "native_or_reconstructed": native}

def inputs(start, *, candidate=True, break_high=True):
    one = [bar(start + timedelta(minutes=i), open_=100, close=100.05, high=100.2 if break_high else 101.0) for i in range(5)]
    one.append(bar(start + timedelta(minutes=5), open_=100, close=100.7 if candidate else 100.2,
                   high=100.8 if break_high else 100.2))
    three = [bar(start, open_=100, close=100.6), bar(start + timedelta(minutes=3), open_=100, close=100.7)]
    five = [bar(start, open_=100, close=100.6)]
    return one, three, five

def stream(tmp_path): return MinimalPumpStartStream(tmp_path / "j.jsonl", tmp_path / "s.json")

def test_candidate_and_confirmed_with_native_context(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC); one, three, five = inputs(start)
    s.prime_context("ALTUSDT", one[:5])
    rows = s.process("ALTUSDT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=6))
    assert rows[-1]["state"] == START_CONFIRMED
    assert rows[-1]["START_PRICE"] == 100

def test_no_break_is_candidate_not_confirmed(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC); one, three, five = inputs(start, break_high=False)
    s.prime_context("ALT", one[:5])
    assert s.process("ALT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=6))[-1]["state"] == START_CANDIDATE

def test_small_or_rebound_rejected(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC); one, three, five = inputs(start, candidate=False)
    s.prime_context("ALT", one[:5])
    assert s.process("ALT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=6))[-1]["state"] == NO_PUMP_START

def test_forming_missing_native_gap_and_warmup_fail_closed(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC)
    assert s.process("A", bars_1m=[bar(start, closed=False)], bars_3m=[], bars_5m=[], asof=start + timedelta(minutes=1))[0]["state"] == INSUFFICIENT_EVIDENCE
    s = stream(tmp_path); one, _, _ = inputs(start)
    assert s.process("B", bars_1m=one, bars_3m=[], bars_5m=[], asof=start + timedelta(minutes=6))[-1]["state"] == INSUFFICIENT_EVIDENCE
    s = stream(tmp_path); s.prime_context("C", one[:5]); gap = bar(start + timedelta(minutes=7), open_=100, close=101)
    assert s.process("C", bars_1m=[gap], bars_3m=[], bars_5m=[], asof=start + timedelta(minutes=8))[0]["reason"] == "TIMESTAMP_GAP"

def test_duplicate_and_oi_alignment_no_future_leakage(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC); one, three, five = inputs(start)
    oi = CoinalyzeOIRecord("ALT", "ALT.6", start, start + timedelta(minutes=5), 1.0)
    s.prime_context("ALT", one[:5])
    rows = s.process("ALT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, oi_records=[oi], asof=start + timedelta(minutes=6))
    assert rows[-1]["participation_state"] == PARTICIPATION_CONTEXT_AVAILABLE
    assert s.process("ALT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, oi_records=[oi], asof=start + timedelta(minutes=6)) == []
    s = stream(tmp_path / "future"); future = CoinalyzeOIRecord("ALT", "ALT.6", start, start + timedelta(minutes=7), 1.0); s.prime_context("ALT", one[:5])
    assert s.process("ALT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, oi_records=[future], asof=start + timedelta(minutes=6))[-1]["participation_state"] == PARTICIPATION_INSUFFICIENT_EVIDENCE

def test_priming_writes_no_historical_backfill(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC); one, three, five = inputs(start)
    s.prime_context("ALT", one)
    assert s.process("ALT", bars_1m=one, bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=6)) == []

def test_active_event_tracks_pullback_new_high_and_five_percent_once(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC); one, three, five = inputs(start)
    s.prime_context("ALT", one[:5])
    first = s.process("ALT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=6))[-1]
    assert first["state"] == START_CONFIRMED and first["active_event_state"] == ACTIVE_PUMP_EVENT
    event_id, start_price = first["event_id"], first["START_PRICE"]
    # Pullback is a valid closed observation and must retain the same identity/max.
    pullback = bar(start + timedelta(minutes=6), open_=100.7, close=100.2, high=100.75)
    r = s.process("ALT", bars_1m=[pullback], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=7))[-1]
    assert r["state"] == ACTIVE_PUMP_EVENT and r["event_id"] == event_id and r["START_PRICE"] == start_price
    old_max = r["MAX_PUMP_PCT"]
    # A later high crosses 5%; event remains active and the milestone is immutable.
    higher = bar(start + timedelta(minutes=7), open_=100.2, close=104.9, high=105.1)
    r = s.process("ALT", bars_1m=[higher], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=8))[-1]
    milestone = r["FIRST_5PCT_OBSERVATION_TIMESTAMP"]
    assert r["event_id"] == event_id and r["MAX_PUMP_PCT"] > old_max and milestone == higher["timestamp"]
    # +7/+9/+20 and later weakness never cap, split, or terminate the event.
    for minute, high in ((8, 107.2), (9, 109.2), (10, 120.5)):
        row = bar(start + timedelta(minutes=minute), open_=105, close=105.1, high=high)
        result = s.process("ALT", bars_1m=[row], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=minute + 1))[-1]
        assert result["state"] == ACTIVE_PUMP_EVENT and result["event_id"] == event_id
        assert result["FIRST_5PCT_OBSERVATION_TIMESTAMP"] == milestone

def test_active_event_survives_insufficient_and_missing_oi(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC); one, three, five = inputs(start)
    s.prime_context("ALT", one[:5])
    first = s.process("ALT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=6))[-1]
    event_id = first["event_id"]
    missing = bar(start + timedelta(minutes=6), open_=100.7, close=100.8)
    result = s.process("ALT", bars_1m=[missing], bars_3m=[], bars_5m=[], asof=start + timedelta(minutes=7))[-1]
    assert result["state"] == INSUFFICIENT_EVIDENCE and result["event_id"] == event_id
    assert result["active_event_state"] == ACTIVE_PUMP_EVENT

def test_malformed_and_stale_bars_fail_closed_without_destroying_event(tmp_path):
    s = stream(tmp_path); start = datetime(2026, 10, 15, 10, tzinfo=UTC); one, three, five = inputs(start)
    s.prime_context("ALT", one[:5])
    first = s.process("ALT", bars_1m=[one[-1]], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=6))[-1]
    malformed = bar(start + timedelta(minutes=6)); malformed.pop("close")
    result = s.process("ALT", bars_1m=[malformed], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=7))[-1]
    assert result["state"] == INSUFFICIENT_EVIDENCE and result["event_id"] == first["event_id"]
    stale = bar(start + timedelta(minutes=7), open_=100, close=101)
    result = s.process("ALT", bars_1m=[stale], bars_3m=three, bars_5m=five, asof=start + timedelta(minutes=11))[-1]
    assert result["state"] == INSUFFICIENT_EVIDENCE and result["reason"] == "STALE_CLOSED_1M"
