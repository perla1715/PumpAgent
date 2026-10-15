from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pumpagent.live_data.adapters.coinalyze_oi import (
    CoinalyzeOISideChannel,
    CoinalyzeOIRecord,
    OISidecarStore,
)


UTC = timezone.utc
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def test_closed_native_five_minute_records_only() -> None:
    adapter = CoinalyzeOISideChannel(api_key="fixture")
    records = [
        {"t": int((NOW - timedelta(minutes=10)).timestamp()), "c": "120"},
        {"t": int((NOW - timedelta(minutes=2)).timestamp()), "c": "121"},
    ]

    parsed = adapter.fetch_closed_5m_oi(
        "ALTUSDT",
        start=NOW - timedelta(minutes=20),
        end=NOW,
        now=NOW,
        market_symbol="ALTUSDT.6",
        records=records,
    )

    assert [item.value for item in parsed] == [120.0]
    assert parsed[0].closed_record_status == "CLOSED_NATIVE_5M"
    assert parsed[0].timeframe == "5m"


def test_alignment_requires_exact_closed_timestamp_and_no_future_fetch() -> None:
    adapter = CoinalyzeOISideChannel(api_key="fixture")
    target = NOW - timedelta(minutes=10)
    aligned = CoinalyzeOIRecord("ALTUSDT", "ALTUSDT.6", target, NOW, 120.0)
    future_fetch = CoinalyzeOIRecord("ALTUSDT", "ALTUSDT.6", target, NOW + timedelta(minutes=1), 121.0)
    unaligned = CoinalyzeOIRecord("ALTUSDT", "ALTUSDT.6", target + timedelta(minutes=5), NOW, 122.0)

    assert adapter.align_to_bybit_5m([aligned], bybit_timestamp=target, asof=NOW) == aligned
    assert adapter.align_to_bybit_5m([future_fetch], bybit_timestamp=target, asof=NOW) is None
    assert adapter.align_to_bybit_5m([unaligned], bybit_timestamp=target, asof=NOW) is None


def test_sidecar_is_append_only_and_deduplicates_exact_identity(tmp_path) -> None:
    record = CoinalyzeOIRecord("ALTUSDT", "ALTUSDT.6", NOW - timedelta(minutes=10), NOW, 120.0)
    store = OISidecarStore(tmp_path / "oi.jsonl")

    assert store.append(record) == "APPENDED"
    assert store.append(record) == "DUPLICATE_NOOP"
    assert store.records() == [record]
