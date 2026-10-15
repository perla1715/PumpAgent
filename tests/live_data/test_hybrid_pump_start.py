from datetime import datetime, timedelta, timezone

import pytest

from pumpagent.live_data.hybrid_pump_start import (
    HybridPumpStartDetector, NO_PUMP_START, START_CANDIDATE, START_CONFIRMED,
    INSUFFICIENT_EVIDENCE,
)


def bar(i, *, r=0.1, d3=0.2, d5=0.6, close=None, high=None, open_=100.0,
        low=None, body_support=True, range_support=False):
    ts = datetime(2026, 10, 7, 13, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    close = float(close if close is not None else open_ * (1 + r / 100))
    high = float(high if high is not None else close)
    low = float(low if low is not None else min(open_, close))
    return {"timestamp": ts.isoformat(), "open": open_, "high": high, "low": low,
            "close": close, "signed_return_pct": r, "signed_3m_displacement_pct": d3,
            "signed_5m_displacement_pct": d5, "body_support": body_support,
            "range_support": range_support}


def warm(det, n=5):
    for i in range(n):
        det.process_bar(bar(i, r=0.05, d3=0.1, d5=0.1, close=100.1 + i * .01,
                            high=100.2 + i * .01))


def test_br_like_start_confirms_and_magnitude_is_start_anchored():
    det = HybridPumpStartDetector("BRUSDT")
    for i, high in enumerate([.5370, .5378, .5382, .5387, .53908]):
        det.process_bar(bar(i, r=.05, d3=.1, d5=.1, open_=.5360,
                            close=.5365, high=high, low=.5358))
    result = det.process_bar(bar(6, r=0.7164045447, d3=0.5681818182, d5=0.7408375009,
                                 open_=0.53601, close=0.53985, high=0.53985,
                                 low=0.53601))
    assert result["status"] == START_CONFIRMED
    assert result["timestamp"] == "2026-10-07T13:06:00+00:00"
    assert result["start_price"] == pytest.approx(0.53601)
    later = det.process_bar(bar(11, r=1.67, d3=1.96, d5=2.22, open_=0.55,
                                close=0.553, high=0.55466, low=0.55))
    assert later["status"] == START_CONFIRMED
    assert later["current_event_pump_pct"] == pytest.approx((0.55466 / .53601 - 1) * 100)


def test_controls_remain_unconfirmed():
    for symbol, r, d3, d5 in [("1INCHUSDT", .1732042792, .3060599878, .2242609582),
                              ("ATOMUSDT", .2963841138, .3558718861, .5945303210),
                              ("AKEUSDT", .4783163265, .6887981537, .5923666288)]:
        det = HybridPumpStartDetector(symbol); warm(det)
        result = det.process_bar(bar(5, r=r, d3=d3, d5=d5, open_=100, close=100.6, high=100.7))
        assert result["status"] in (NO_PUMP_START, START_CANDIDATE)


@pytest.mark.parametrize("field", ["signed_return_pct", "signed_3m_displacement_pct", "signed_5m_displacement_pct"])
def test_negative_direction_blocks_candidate(field):
    det = HybridPumpStartDetector("X"); warm(det)
    row = bar(5); row[field] = -0.6
    assert det.process_bar(row)["status"] == NO_PUMP_START


def test_floor_and_acceleration_strictness():
    det = HybridPumpStartDetector("X"); warm(det)
    row = bar(5, r=.499, d5=.6)
    assert det.process_bar(row)["status"] == NO_PUMP_START
    det = HybridPumpStartDetector("X"); warm(det)
    row = bar(5, r=.5, d5=.5)
    assert det.process_bar(row)["status"] in (START_CANDIDATE, START_CONFIRMED)


def test_micro_range_excludes_current_and_support_is_required():
    det = HybridPumpStartDetector("X"); warm(det)
    row = bar(5, r=.7, d3=.7, d5=.7, close=100.2, high=100.2,
              body_support=False, range_support=False)
    assert det.process_bar(row)["status"] == START_CANDIDATE


def test_duplicate_and_restart_safety():
    det = HybridPumpStartDetector("X"); warm(det)
    row = bar(5, r=.7, d3=.7, d5=.7, close=101, high=101)
    first = det.process_bar(row)
    assert first["status"] == START_CONFIRMED
    assert det.process_bar(row)["action"] == "DUPLICATE_NOOP"
    restored = HybridPumpStartDetector("X", det.snapshot())
    assert restored.process_bar(row)["action"] == "DUPLICATE_NOOP"
    assert restored.state.event_id == first["event_id"]


def test_insufficient_evidence():
    det = HybridPumpStartDetector("X")
    assert det.process_bar(bar(0))["status"] == INSUFFICIENT_EVIDENCE
