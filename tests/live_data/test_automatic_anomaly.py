from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.automatic_anomaly import (
    AUTHORIZED_SYMBOLS,
    AutomaticAnomalyJournal,
    MedianMadStructureAEvaluator,
    admit_attention,
    check_universe,
    evaluate_bounded_universe,
)
from pumpagent.live_data.domain import LiveDataQualityStatus, LiveDataTransport, NormalizedMarketDataInput, SourceMetadata
from pumpagent.live_data.rolling_buffer import RollingNumericContextBuffer
from pumpagent.live_data.shadow_attention import ShadowAttentionAdmission

UTC = timezone.utc
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def rows(timeframe: str = "1", count: int = 121, *, current_price: float = 110.0, current_volume: float = 30.0) -> list[dict[str, object]]:
    step = {"1": 1, "3": 3, "5": 5}[timeframe]
    start = NOW - timedelta(minutes=step * count)
    result = []
    for index in range(count):
        result.append({
            "market_timestamp_utc": (start + timedelta(minutes=step * index)).isoformat(),
            "price": 100.0 + (index % 2),
            "volume": 10.0 + (index % 2),
            "timeframe": timeframe,
            "provider": "bybit",
            "raw_or_derived_status": "RAW_NATIVE_CLOSED_BAR",
        })
    result[-1]["price"] = current_price
    result[-1]["volume"] = current_volume
    return result


def normalized(symbol: str, timeframe: str, at: datetime, value: float = 100.0, volume: float = 10.0) -> NormalizedMarketDataInput:
    candle = {"timestamp": at, "open": value - 1, "high": value + 1, "low": value - 2, "close": value, "volume": volume}
    return NormalizedMarketDataInput(
        source_event_id=f"fixture:{symbol}:{timeframe}:{at.isoformat()}", symbol=symbol, exchange="bybit", timeframe=timeframe,
        source_timestamp=at, receive_timestamp=NOW, price=value, ohlcv=(candle,), volume=volume,
        data_source="fixture", quality_status=LiveDataQualityStatus.UNKNOWN,
        source_metadata=SourceMetadata(exchange="bybit", adapter_name="fixture", adapter_version="1", source_timestamp=at,
            receive_timestamp=NOW, transport=LiveDataTransport.REST, source_symbol=symbol, normalized_symbol=symbol,
            source_timeframe=timeframe, normalized_timeframe=timeframe),
    )


class AutomaticAnomalyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = MedianMadStructureAEvaluator()

    def test_owner_universe_and_unauthorized_symbol(self) -> None:
        self.assertEqual(check_universe("BOMEUSDT").status, "PASS")
        self.assertEqual(check_universe("BTCUSDT").status, "UNAUTHORIZED")
        self.assertEqual(set(AUTHORIZED_SYMBOLS), {"BOMEUSDT", "NIGHTUSDT", "MOVRUSDT", "BEAMUSDT"})

    def test_top50_and_unresolved_gate(self) -> None:
        self.assertEqual(check_universe("BOMEUSDT", top50_status="EXCLUDED_TOP50").status, "EXCLUDED_TOP50")
        self.assertEqual(check_universe("BOMEUSDT", top50_status="TOP50_STATUS_UNRESOLVED").status, "TOP50_STATUS_UNRESOLVED")

    def test_n_less_120_is_insufficient(self) -> None:
        decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=rows(count=120), as_of=NOW)
        self.assertEqual(decision.decision, "INSUFFICIENT_EVIDENCE")

    def test_price_and_volume_attention(self) -> None:
        decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=rows(), as_of=NOW)
        self.assertEqual(decision.decision, "ATTENTION")
        self.assertGreaterEqual(decision.price_robust_score or 0, 2.5)
        self.assertGreaterEqual(decision.participation_robust_score or 0, 2.0)

    def test_price_below_cutoff_no_attention(self) -> None:
        decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=rows(current_price=101.0), as_of=NOW)
        self.assertEqual(decision.decision, "NO_ATTENTION")

    def test_participation_below_cutoff_no_attention(self) -> None:
        decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=rows(current_volume=11.0), as_of=NOW)
        self.assertEqual(decision.decision, "NO_ATTENTION")

    def test_current_observation_excluded_and_future_excluded(self) -> None:
        data = rows()
        future = dict(data[-1]); future["market_timestamp_utc"] = (NOW + timedelta(minutes=1)).isoformat(); future["price"] = 999.0
        decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=data + [future], as_of=NOW)
        self.assertEqual(decision.decision, "ATTENTION")
        self.assertEqual(decision.as_of_timestamp_utc, NOW - timedelta(minutes=1))

    def test_degenerate_mad_and_missing_participation(self) -> None:
        flat = [{"market_timestamp_utc": (NOW - timedelta(minutes=121 - i)).isoformat(), "price": 100.0, "volume": 10.0} for i in range(121)]
        self.assertEqual(self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=flat, as_of=NOW).decision, "INSUFFICIENT_EVIDENCE")
        missing = rows(); missing[-1]["volume"] = None
        self.assertEqual(self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=missing, as_of=NOW).decision, "INSUFFICIENT_EVIDENCE")

    def test_native_timeframe_provenance_is_preserved(self) -> None:
        for timeframe in ("1", "3", "5"):
            decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe=timeframe, rows=rows(timeframe), as_of=NOW)
            self.assertEqual(decision.timeframe, timeframe)
            self.assertIn("native_closed", decision.source_provenance)

    def test_optional_oi_does_not_veto_and_is_recorded(self) -> None:
        decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="5", rows=rows("5"), as_of=NOW, oi_status="AVAILABLE")
        self.assertEqual(decision.decision, "ATTENTION")
        self.assertEqual(decision.oi_status, "AVAILABLE")
        missing = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=rows("1"), as_of=NOW, oi_status="NOT_AVAILABLE")
        self.assertEqual(missing.decision, "ATTENTION")

    def test_journal_append_restart_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = AutomaticAnomalyJournal(Path(directory) / "discovery.jsonl")
            decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=rows(), as_of=NOW)
            self.assertEqual(journal.append(decision), "APPENDED")
            self.assertEqual(journal.append(decision), "DUPLICATE_NOOP")
            restored = AutomaticAnomalyJournal(Path(directory) / "discovery.jsonl")
            self.assertEqual(len(restored.records()), 1)
            self.assertFalse(restored.records()[0]["trade_permission"])

    def test_admission_and_duplicate_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            buffer = RollingNumericContextBuffer(root / "rolling.jsonl", symbol="BOMEUSDT", now_fn=lambda: NOW)
            for timeframe, minutes in (("1", 1), ("3", 3), ("5", 5)):
                buffer.ingest_normalized(normalized("BOMEUSDT", timeframe, NOW - timedelta(minutes=minutes)))
            admission = ShadowAttentionAdmission(buffer, root / "admission.jsonl")
            decision = self.evaluator.evaluate(symbol="BOMEUSDT", timeframe="1", rows=rows(), as_of=NOW)
            first = admit_attention(admission, decision)
            second = admit_attention(admission, decision)
            self.assertTrue(first.admitted)
            self.assertEqual(second.status, "DUPLICATE_NOOP")
            self.assertFalse(first.record.trade_permission if first.record else True)

    def test_bounded_runner_evaluates_all_authorized_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            buffers = {}
            for symbol in AUTHORIZED_SYMBOLS:
                buffer = RollingNumericContextBuffer(root / f"{symbol}.jsonl", symbol=symbol, now_fn=lambda: NOW)
                buffers[symbol] = buffer
                for timeframe in ("1", "3", "5"):
                    buffer.ingest_normalized(normalized(symbol, timeframe, NOW - timedelta(minutes={"1": 1, "3": 3, "5": 5}[timeframe])))
            decisions = evaluate_bounded_universe(buffers=buffers, journal=AutomaticAnomalyJournal(root / "aa.jsonl"), as_of=NOW)
            self.assertEqual(len(decisions), 12)
            self.assertTrue(all(decision.symbol in AUTHORIZED_SYMBOLS for decision in decisions))

    def test_manual_target_path_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            buffer = RollingNumericContextBuffer(root / "rolling.jsonl", symbol="BOMEUSDT", now_fn=lambda: NOW)
            buffer.ingest_normalized(normalized("BOMEUSDT", "1", NOW - timedelta(minutes=1)))
            admission = ShadowAttentionAdmission(buffer, root / "admission.jsonl")
            result = admission.admit_manual_target(observed_pump_percent=8, target_pump_percent=5, admission_timestamp=NOW)
            self.assertTrue(result.admitted)
            self.assertEqual(result.record.discovery_mode, "MANUAL_TARGET")


if __name__ == "__main__":
    unittest.main()
