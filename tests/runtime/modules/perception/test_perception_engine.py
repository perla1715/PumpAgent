from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
PERCEPTION_ENGINE = SRC / "pumpagent" / "runtime" / "modules" / "perception" / "engine.py"
RUNTIME_SRC = SRC / "pumpagent" / "runtime"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import MarketSnapshot, ObservationPackage, RuntimeEvent
from pumpagent.runtime.domain.enums import DataQualityStatus
from pumpagent.runtime.modules import perception
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.perception import (
    PerceptionError,
    add_observation_package,
    build_observation_package,
    detect_market_state,
    format_market_state_scan_line,
)


def make_event_with_market_snapshot() -> RuntimeEvent:
    event = RuntimeEvent(
        event_id="runtime-evt-1",
        schema_version="1.0",
        cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
    )
    return add_market_snapshot_from_fixture(event, FIXTURE)


class PerceptionEngineTests(unittest.TestCase):
    def test_scanner_helpers_remain_available(self) -> None:
        data = {
            "symbol": "BTCUSDT",
            "price": 100.0,
            "volume": 42.0,
            "open_interest": 1200.5,
            "price_change_1m": 1.1,
            "price_change_3m": 1.5,
            "volume_spike_ratio": 8.1,
            "oi_change_1m": 0.1,
        }

        self.assertEqual(detect_market_state(data), "IGNITION")
        self.assertIn("BTCUSDT | IGNITION", format_market_state_scan_line(data))

    def test_retired_evidence_apis_are_not_public(self) -> None:
        for name in (
            "build_perception_evidence",
            "add_perception_evidence",
            "PerceptionEvidenceResult",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(perception, name))
                self.assertNotIn(name, perception.__all__)

    def test_perception_engine_contains_no_final_evidence_constructor(self) -> None:
        tree = ast.parse(PERCEPTION_ENGINE.read_text(encoding="utf-8"))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertNotIn("StructuralEvidence", calls)
        self.assertNotIn("MarketEfficiencyEvidence", calls)

    def test_specialized_engines_are_only_production_constructor_paths(self) -> None:
        constructor_files: dict[str, set[Path]] = {
            "StructuralEvidence": set(),
            "MarketEfficiencyEvidence": set(),
        }
        for path in RUNTIME_SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in constructor_files
                ):
                    constructor_files[node.func.id].add(path.relative_to(SRC))

        self.assertEqual(
            constructor_files["StructuralEvidence"],
            {Path("pumpagent/runtime/modules/structure/engine.py")},
        )
        self.assertEqual(
            constructor_files["MarketEfficiencyEvidence"],
            {Path("pumpagent/runtime/modules/market_efficiency/engine.py")},
        )

    def test_perception_produces_valid_observation_package(self) -> None:
        event = make_event_with_market_snapshot()

        observations = build_observation_package(event.market_snapshot)

        self.assertIsInstance(observations, ObservationPackage)
        self.assertEqual(observations.normalized_price, 100.0)
        self.assertEqual(observations.normalized_volume, 42.0)
        self.assertEqual(observations.data_quality_status, DataQualityStatus.VALID)
        self.assertEqual(
            observations.normalized_metrics,
            event.market_snapshot.optional_market_metrics,
        )

    def test_observation_preserves_source_values_and_identity(self) -> None:
        event = make_event_with_market_snapshot()

        updated = add_observation_package(event)

        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertEqual(updated.observation_package.event_id, event.event_id)
        self.assertEqual(
            updated.observation_package.previous_snapshot_reference,
            event.market_snapshot.event_id,
        )
        self.assertEqual(
            updated.observation_package.normalized_ohlcv,
            event.market_snapshot.ohlcv,
        )

    def test_perception_writes_only_observation_package(self) -> None:
        updated = add_observation_package(make_event_with_market_snapshot())

        self.assertIsNotNone(updated.observation_package)
        self.assertIsNone(updated.structural_evidence)
        self.assertIsNone(updated.market_efficiency_evidence)
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)

    def test_available_and_missing_metrics_are_deterministic(self) -> None:
        observations = build_observation_package(
            make_event_with_market_snapshot().market_snapshot
        )

        self.assertEqual(
            observations.available_metrics,
            (
                "price",
                "ohlcv",
                "volume",
                "open_interest",
                "funding_rate",
                "cvd",
                "liquidations",
            ),
        )
        self.assertEqual(observations.missing_metrics, ())

    def test_perception_requires_market_snapshot(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaises(PerceptionError):
            add_observation_package(event)

    def test_perception_rejects_invalid_ohlcv(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "close": 100.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(PerceptionError, "high, low, volume"):
            build_observation_package(snapshot)

    def test_perception_imports_no_final_evidence_contracts(self) -> None:
        tree = ast.parse(PERCEPTION_ENGINE.read_text(encoding="utf-8"))
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }

        self.assertNotIn("StructuralEvidence", imported_names)
        self.assertNotIn("MarketEfficiencyEvidence", imported_names)


if __name__ == "__main__":
    unittest.main()
