from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.domain import (
    LiveDataQualityStatus,
    NormalizedMarketDataInput,
    SourceMetadata,
    LiveDataTransport,
)
from pumpagent.live_data.rolling_buffer import RollingNumericContextBuffer
from pumpagent.live_data.shadow_attention import ShadowAttentionAdmission


UTC = timezone.utc


def candle(ts: datetime, close: float = 100.0, volume: float = 10.0) -> dict[str, object]:
    return {
        "timestamp": ts,
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": volume,
    }


def normalized(timeframe: str, candles: list[dict[str, object]]) -> NormalizedMarketDataInput:
    now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    return NormalizedMarketDataInput(
        source_event_id=f"fixture:{timeframe}",
        symbol="BTCUSDT",
        exchange="bybit",
        timeframe=timeframe,
        source_timestamp=candles[-1]["timestamp"],
        receive_timestamp=now,
        price=float(candles[-1]["close"]),
        ohlcv=tuple(candles),
        volume=float(candles[-1]["volume"]),
        data_source="fixture",
        quality_status=LiveDataQualityStatus.UNKNOWN,
        source_metadata=SourceMetadata(
            exchange="bybit",
            adapter_name="fixture",
            adapter_version="1",
            source_timestamp=now,
            receive_timestamp=now,
            transport=LiveDataTransport.REST,
            source_symbol="BTCUSDT",
            normalized_symbol="BTCUSDT",
            source_timeframe=timeframe,
            normalized_timeframe=timeframe,
        ),
    )


class RollingBufferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        self.buffer = RollingNumericContextBuffer(
            Path(self.tempdir.name) / "buffer.sqlite3",
            symbol="BTCUSDT",
            min_closed=120,
            now_fn=lambda: self.now,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_1m_closed_bars_append(self) -> None:
        start = self.now - timedelta(minutes=3)
        result = self.buffer.ingest_normalized(
            normalized("1", [candle(start + timedelta(minutes=i)) for i in range(2)])
        )
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(self.buffer.status("1").row_count, 2)

    def test_5m_closed_bars_append(self) -> None:
        start = self.now - timedelta(minutes=15)
        result = self.buffer.ingest_normalized(
            normalized("5", [candle(start + timedelta(minutes=5 * i)) for i in range(2)])
        )
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(self.buffer.status("5").row_count, 2)

    def test_3m_native_rows_append(self) -> None:
        start = self.now - timedelta(minutes=9)
        result = self.buffer.ingest_normalized(
            normalized("3", [candle(start + timedelta(minutes=3 * i)) for i in range(2)])
        )
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(self.buffer.status("3").row_count, 2)

    def test_3m_duplicate_is_noop(self) -> None:
        data = normalized("3", [candle(self.now - timedelta(minutes=6))])
        self.buffer.ingest_normalized(data)
        result = self.buffer.ingest_normalized(data)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(self.buffer.status("3").row_count, 1)

    def test_duplicate_fetch_does_not_advance(self) -> None:
        start = self.now - timedelta(minutes=3)
        data = normalized("1", [candle(start)])
        self.buffer.ingest_normalized(data)
        result = self.buffer.ingest_normalized(data)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["duplicates"], 1)

    def test_out_of_order_and_gap_are_reported(self) -> None:
        start = self.now - timedelta(minutes=5)
        result = self.buffer.ingest_normalized(
            normalized("1", [candle(start + timedelta(minutes=2)), candle(start)])
        )
        self.assertIn("OUT_OF_ORDER_INPUT", result["issues"])
        self.assertIn("MISSING_INTERVAL", result["issues"])

    def test_restart_restores_history(self) -> None:
        start = self.now - timedelta(minutes=3)
        self.buffer.ingest_normalized(normalized("1", [candle(start)]))
        reopened = RollingNumericContextBuffer(
            self.buffer.storage_path,
            symbol="BTCUSDT",
            min_closed=120,
            now_fn=lambda: self.now,
        )
        self.assertEqual(reopened.status("1").row_count, 1)
        result = reopened.ingest_normalized(normalized("1", [candle(start)]))
        self.assertEqual(result["inserted"], 0)

    def test_n120_readiness(self) -> None:
        start = self.now - timedelta(minutes=121)
        data = [candle(start + timedelta(minutes=i)) for i in range(119)]
        self.buffer.ingest_normalized(normalized("1", data))
        self.assertEqual(self.buffer.status("1").warmup_status, "WARMING_UP")
        self.buffer.ingest_normalized(normalized("1", [candle(self.now - timedelta(minutes=1))]))
        self.assertEqual(self.buffer.status("1").warmup_status, "READY")

    def test_missing_optional_fields_are_explicit(self) -> None:
        status = self.buffer.status("1")
        self.assertIn("OI", status.missing_fields)
        self.assertNotIn("OI", status.available_fields)

    def test_timeframes_are_separate(self) -> None:
        self.buffer.ingest_normalized(normalized("1", [candle(self.now - timedelta(minutes=2))]))
        self.buffer.ingest_normalized(normalized("3", [candle(self.now - timedelta(minutes=6))]))
        self.buffer.ingest_normalized(normalized("5", [candle(self.now - timedelta(minutes=10))]))
        self.assertEqual(self.buffer.status("1").row_count, 1)
        self.assertEqual(self.buffer.status("3").row_count, 1)
        self.assertEqual(self.buffer.status("5").row_count, 1)

    def test_open_bar_does_not_count(self) -> None:
        result = self.buffer.ingest_normalized(
            normalized("1", [candle(self.now - timedelta(seconds=30))])
        )
        self.assertEqual(result["open_rows"], 1)
        self.assertEqual(self.buffer.status("1").row_count, 0)

    def test_buffer_has_no_runtime_side_effects(self) -> None:
        self.assertFalse(hasattr(self.buffer, "scanner"))
        self.assertFalse(hasattr(self.buffer, "tracker"))
        self.assertFalse(hasattr(self.buffer, "trade"))

    def test_3m_open_bar_does_not_count(self) -> None:
        result = self.buffer.ingest_normalized(
            normalized("3", [candle(self.now - timedelta(minutes=1))])
        )
        self.assertEqual(result["open_rows"], 1)
        self.assertEqual(self.buffer.status("3").row_count, 0)

    def test_3m_restart_restore(self) -> None:
        self.buffer.ingest_normalized(
            normalized("3", [candle(self.now - timedelta(minutes=6))])
        )
        reopened = RollingNumericContextBuffer(
            self.buffer.storage_path,
            symbol="BTCUSDT",
            min_closed=120,
            now_fn=lambda: self.now,
        )
        self.assertEqual(reopened.status("3").row_count, 1)

    def test_3m_reaches_ready_only_after_120_closed_rows(self) -> None:
        start = self.now - timedelta(minutes=363)
        data = [candle(start + timedelta(minutes=3 * i)) for i in range(119)]
        self.buffer.ingest_normalized(normalized("3", data))
        self.assertEqual(self.buffer.status("3").warmup_status, "WARMING_UP")
        self.buffer.ingest_normalized(
            normalized("3", [candle(self.now - timedelta(minutes=3))])
        )
        self.assertEqual(self.buffer.status("3").warmup_status, "READY")


class ShadowAttentionAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        self.buffer = RollingNumericContextBuffer(
            Path(self.tempdir.name) / "buffer.jsonl",
            symbol="BTCUSDT",
            now_fn=lambda: self.now,
        )
        for timeframe, minutes in (("1", 2), ("3", 6), ("5", 10)):
            self.buffer.ingest_normalized(
                normalized(timeframe, [candle(self.now - timedelta(minutes=minutes))])
            )
        self.admission = ShadowAttentionAdmission(
            self.buffer,
            Path(self.tempdir.name) / "admissions.jsonl",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_manual_target_admits_and_preserves_trade_boundary(self) -> None:
        result = self.admission.admit_manual_target(
            observed_pump_percent=8.0,
            target_pump_percent=5.0,
            admission_timestamp=self.now,
        )
        self.assertTrue(result.admitted)
        self.assertEqual(result.record.scanner_decision, "TAKE_UNDER_OBSERVATION")
        self.assertFalse(result.record.trade_permission)
        self.assertEqual(result.record.available_timeframes, ("1", "3", "5"))

    def test_non_qualifying_manual_target_does_not_admit(self) -> None:
        result = self.admission.admit_manual_target(
            observed_pump_percent=4.0,
            target_pump_percent=5.0,
            admission_timestamp=self.now,
        )
        self.assertFalse(result.admitted)
        self.assertEqual(result.status, "NO_ADMISSION")

    def test_duplicate_admission_is_noop(self) -> None:
        first = self.admission.admit_manual_target(
            observed_pump_percent=8.0,
            target_pump_percent=5.0,
            admission_timestamp=self.now,
        )
        second = self.admission.admit_manual_target(
            observed_pump_percent=9.0,
            target_pump_percent=5.0,
            admission_timestamp=self.now + timedelta(minutes=1),
        )
        self.assertTrue(first.admitted)
        self.assertFalse(second.admitted)
        self.assertEqual(second.status, "DUPLICATE_NOOP")
        self.assertEqual(len(self.admission.admission_path.read_text().splitlines()), 1)

    def test_future_rows_are_not_used(self) -> None:
        future = self.now + timedelta(minutes=5)
        self.buffer._now_fn = lambda: future + timedelta(minutes=10)  # type: ignore[method-assign]
        self.buffer.ingest_normalized(normalized("3", [candle(future)]))
        context = self.buffer.context_snapshot(as_of=self.now)
        self.assertLessEqual(
            context["latest"]["3"]["market_timestamp_utc"],
            self.now.isoformat(),
        )
        result = self.admission.admit_decision(
            discovery_mode="AUTOMATIC_ANOMALY",
            scanner_decision=True,
            admission_timestamp=self.now,
            reason="External governed test-only decision.",
        )
        self.assertTrue(result.admitted)

    def test_restart_preserves_duplicate_admission_state(self) -> None:
        self.admission.admit_manual_target(
            observed_pump_percent=8.0,
            target_pump_percent=5.0,
            admission_timestamp=self.now,
        )
        reopened = ShadowAttentionAdmission(self.buffer, self.admission.admission_path)
        result = reopened.admit_manual_target(
            observed_pump_percent=8.0,
            target_pump_percent=5.0,
            admission_timestamp=self.now,
        )
        self.assertEqual(result.status, "DUPLICATE_NOOP")
