from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from pumpagent.pilot.journal import recover
from pumpagent.runtime.pilot_shadow_event import ShadowEventStatus, run_shadow_event
from pumpagent.runtime.adapters.scanner_observation import (
    ScannerAttentionDecision,
    ScannerTriggerReason,
)
from pumpagent.runtime.domain import MarketSnapshot
from pumpagent.runtime.domain.enums import DataQualityStatus
from pumpagent.runtime.orchestrator.runtime_loop import RuntimeOrchestrator


START = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
ATTENTION = ScannerAttentionDecision(True, (ScannerTriggerReason.PRICE_ACTIVITY,))


def scanner(candle: datetime) -> dict:
    return {
        "status": "VALID", "provider": "coinalyze", "exchange": "BYBIT",
        "symbol": "BTCUSDT.6", "timeframe": "5m",
        "timestamp_bucket": int(candle.timestamp()),
        "metrics": {"price_5m_pct": 2.0, "volume_ratio_5m": 2.0, "oi_change_5m_pct": 1.0},
        "data_quality": {"closed_candles_only": True, "ohlcv_oi_aligned": True},
    }


def snapshot(offset: int = 10, *, closed: bool = True) -> MarketSnapshot:
    rows = []
    for index, close in enumerate((100.0, 101.0, 103.0)):
        timestamp = START + timedelta(minutes=offset - 10 + index * 5)
        rows.append({"timestamp": timestamp, "open": close - 1, "high": close + 1,
                     "low": close - 2, "close": close, "volume": 50 + index * 10,
                     "is_closed": closed})
    latest = rows[-1]["timestamp"]
    return MarketSnapshot(
        event_id=f"evt-{offset}", timestamp=latest, symbol="BTCUSDT", exchange="bybit",
        timeframe="5m", price=103.0, ohlcv=tuple(rows), volume=70.0,
        data_source="coinalyze", data_quality_status=DataQualityStatus.VALID,
    )


class FailingRuntime(RuntimeOrchestrator):
    def process_market_update(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("controlled runtime failure")


class InvalidDecisionRuntime(RuntimeOrchestrator):
    def process_market_update(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return object()


class ShadowEventTests(unittest.TestCase):
    def journal(self):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "pilot_shadow"
        path.mkdir()
        self.addCleanup(temp.cleanup)
        return path

    def test_success_is_one_append_and_stay_out_is_valid(self):
        path = self.journal(); value = snapshot(); result = run_shadow_event(value, scanner(value.timestamp), ATTENTION, path)
        self.assertIs(result.status, ShadowEventStatus.COMPLETED)
        self.assertEqual(result.checkpoint_append_status, "APPENDED")
        self.assertEqual(result.decision_assessment_type, "stay_out")
        self.assertEqual(len(recover(path, symbol="BTCUSDT").records), 1)

    def test_false_attention_never_appends(self):
        path = self.journal(); value = snapshot()
        result = run_shadow_event(value, scanner(value.timestamp), ScannerAttentionDecision(False), path)
        self.assertIs(result.status, ShadowEventStatus.FAILED)
        self.assertEqual(len(tuple(path.iterdir())), 0)

    def test_checkpoint_linkage_and_duplicate_protection(self):
        path = self.journal(); value = snapshot(); first = run_shadow_event(value, scanner(value.timestamp), ATTENTION, path)
        second = run_shadow_event(value, scanner(value.timestamp), ATTENTION, path)
        self.assertEqual(first.checkpoint_id, recover(path, symbol="BTCUSDT").records[0].checkpoint_id)
        self.assertIs(second.status, ShadowEventStatus.FAILED)
        self.assertEqual(len(recover(path, symbol="BTCUSDT").records), 1)

    def test_wrong_exchange_timeframe_and_provider_fail_before_append(self):
        value = snapshot()
        for field, replacement in (("exchange", "BINANCE"), ("timeframe", "1m"), ("provider", "bybit")):
            with self.subTest(field=field):
                path = self.journal()
                source = dict(scanner(value.timestamp), **{field: replacement})
                result = run_shadow_event(value, source, ATTENTION, path)
                self.assertIs(result.status, ShadowEventStatus.FAILED)
                self.assertEqual(len(tuple(path.iterdir())), 0)

    def test_older_candle_is_rejected_without_second_append(self):
        path = self.journal(); current = snapshot(); run_shadow_event(current, scanner(current.timestamp), ATTENTION, path)
        older = snapshot(offset=9)
        result = run_shadow_event(older, scanner(older.timestamp), ATTENTION, path)
        self.assertIs(result.status, ShadowEventStatus.FAILED)
        self.assertEqual(len(recover(path, symbol="BTCUSDT").records), 1)

    def test_open_candle_and_journal_failure_fail_closed(self):
        value = snapshot(closed=False); path = self.journal()
        self.assertIs(run_shadow_event(value, scanner(value.timestamp), ATTENTION, path).status, ShadowEventStatus.FAILED)
        missing = path.parent / "pilot_shadow_missing"
        self.assertIs(run_shadow_event(snapshot(), scanner(snapshot().timestamp), ATTENTION, missing).status, ShadowEventStatus.FAILED)

    def test_runtime_and_decision_failures_do_not_claim_completion(self):
        path = self.journal(); value = snapshot()
        runtime_failure = run_shadow_event(value, scanner(value.timestamp), ATTENTION, path, runtime=FailingRuntime())
        self.assertIs(runtime_failure.status, ShadowEventStatus.FAILED)
        self.assertEqual(len(recover(path, symbol="BTCUSDT").records), 1)
        retry = run_shadow_event(value, scanner(value.timestamp), ATTENTION, path)
        self.assertIs(retry.status, ShadowEventStatus.FAILED)
        self.assertEqual(len(recover(path, symbol="BTCUSDT").records), 1)
        # A fresh journal isolates the invalid-result contract check.
        path2 = self.journal(); value2 = snapshot(offset=11)
        decision_failure = run_shadow_event(value2, scanner(value2.timestamp), ATTENTION, path2, runtime=InvalidDecisionRuntime())
        self.assertIs(decision_failure.status, ShadowEventStatus.FAILED)

    def test_invalid_input_and_identity_fail_closed(self):
        path = self.journal(); value = snapshot()
        self.assertIs(run_shadow_event(object(), scanner(value.timestamp), ATTENTION, path).status, ShadowEventStatus.FAILED)
        mismatched = dict(scanner(value.timestamp), symbol="ETHUSDT.6")
        self.assertIs(run_shadow_event(value, mismatched, ATTENTION, path).status, ShadowEventStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
