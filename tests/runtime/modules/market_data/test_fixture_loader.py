from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import MarketSnapshot, RuntimeEvent
from pumpagent.runtime.domain.enums import DataQualityStatus
from pumpagent.runtime.modules.market_data import (
    FixtureLoadError,
    add_market_snapshot_from_fixture,
    load_market_snapshot_from_fixture,
)


class FixtureMarketDataTests(unittest.TestCase):
    def write_fixture(self, payload: object) -> Path:
        self.temp_dir = tempfile.TemporaryDirectory()
        fixture = Path(self.temp_dir.name) / "fixture.json"
        fixture.write_text(json.dumps(payload), encoding="utf-8")
        return fixture

    def tearDown(self) -> None:
        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()

    def valid_payload(self) -> dict[str, object]:
        return {
            "event_id": "evt-fixture-test",
            "timestamp": "2026-07-01T12:00:00+00:00",
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "timeframe": "1m",
            "price": 100.0,
            "ohlcv": [
                {
                    "timestamp": "2026-07-01T11:59:00+00:00",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.5,
                    "close": 100.0,
                    "volume": 42.0,
                }
            ],
            "volume": 42.0,
            "data_source": "fixture",
            "data_quality_status": "valid",
        }

    def test_successful_fixture_loading(self) -> None:
        snapshot = load_market_snapshot_from_fixture(FIXTURE)

        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertEqual(snapshot.event_id, "evt-fixture-1")
        self.assertEqual(snapshot.symbol, "BTCUSDT")
        self.assertEqual(snapshot.exchange, "binance")
        self.assertEqual(snapshot.timeframe, "1m")
        self.assertEqual(snapshot.data_quality_status, DataQualityStatus.VALID)

    def test_invalid_fixture_handling(self) -> None:
        invalid_fixture = self.write_fixture({"event_id": "bad-fixture"})

        with self.assertRaises(FixtureLoadError):
            load_market_snapshot_from_fixture(invalid_fixture)

    def test_invalid_json_handling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            invalid_fixture = Path(temp_dir) / "invalid.json"
            invalid_fixture.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(FixtureLoadError):
                load_market_snapshot_from_fixture(invalid_fixture)

    def test_invalid_fixture_shape_handling(self) -> None:
        invalid_fixture = self.write_fixture(["not", "an", "object"])

        with self.assertRaises(FixtureLoadError):
            load_market_snapshot_from_fixture(invalid_fixture)

    def test_invalid_numeric_field_handling(self) -> None:
        payload = self.valid_payload()
        payload["price"] = "not-a-number"
        invalid_fixture = self.write_fixture(payload)

        with self.assertRaisesRegex(FixtureLoadError, "price must be numeric"):
            load_market_snapshot_from_fixture(invalid_fixture)

    def test_malformed_ohlcv_candle_handling(self) -> None:
        payload = self.valid_payload()
        payload["ohlcv"] = [{"open": 99.0, "high": 101.0}]
        invalid_fixture = self.write_fixture(payload)

        with self.assertRaisesRegex(FixtureLoadError, "ohlcv\\[0\\] missing"):
            load_market_snapshot_from_fixture(invalid_fixture)

    def test_market_snapshot_creation_from_fixture(self) -> None:
        snapshot = load_market_snapshot_from_fixture(FIXTURE)

        self.assertEqual(
            snapshot.timestamp,
            datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(snapshot.price, 100.0)
        self.assertEqual(snapshot.volume, 42.0)
        self.assertEqual(snapshot.ohlcv[0]["close"], 100.0)
        self.assertEqual(snapshot.optional_market_metrics["open_interest"], 1200.5)
        self.assertEqual(snapshot.raw_payload_reference, str(FIXTURE))

    def test_market_snapshot_insertion_into_runtime_event(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        updated = add_market_snapshot_from_fixture(event, FIXTURE)

        self.assertIsNot(updated, event)
        self.assertIsNone(event.market_snapshot)
        self.assertIsNotNone(updated.market_snapshot)
        self.assertIsNone(updated.observation_package)
        self.assertIsNone(updated.structural_evidence)
        self.assertIsNone(updated.market_efficiency_evidence)
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_mismatched_runtime_event_identity_fields_are_rejected(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="ETHUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaisesRegex(FixtureLoadError, "symbol"):
            add_market_snapshot_from_fixture(event, FIXTURE)

    def test_runtime_event_and_snapshot_event_ids_may_differ(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        updated = add_market_snapshot_from_fixture(event, FIXTURE)

        self.assertEqual(updated.event_id, "runtime-evt-1")
        self.assertEqual(updated.market_snapshot.event_id, "evt-fixture-1")

    def test_serialization_compatibility(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        serialized = add_market_snapshot_from_fixture(event, FIXTURE).to_dict()

        self.assertEqual(serialized["market_snapshot"]["symbol"], "BTCUSDT")
        self.assertEqual(serialized["market_snapshot"]["data_quality_status"], "valid")
        self.assertEqual(
            serialized["market_snapshot"]["optional_market_metrics"]["liquidations"][
                "short"
            ],
            20.0,
        )
        self.assertIsNone(serialized["observation_package"])


if __name__ == "__main__":
    unittest.main()
