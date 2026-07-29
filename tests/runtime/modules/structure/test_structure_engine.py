from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
STRUCTURE_ENGINE = SRC / "pumpagent" / "runtime" / "modules" / "structure" / "engine.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import ObservationPackage, RuntimeEvent, StructuralEvidence
from pumpagent.runtime.domain.enums import (
    DataQualityStatus,
    EvidenceStrength,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.structure import (
    StructureError,
    add_structural_evidence,
    build_structural_evidence,
    refine_structural_evidence,
)
from pumpagent.runtime.modules.structure.candles import to_structure_candles
from pumpagent.runtime.modules.structure.fibonacci import calculate_fibonacci_levels
from pumpagent.runtime.modules.structure.indicators import calculate_emas
from pumpagent.runtime.modules.structure.swings import (
    detect_swings,
    latest_valid_impulse,
)


def make_event_with_observation_package() -> RuntimeEvent:
    event = RuntimeEvent(
        event_id="runtime-evt-1",
        schema_version="1.0",
        cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
    )
    event = add_market_snapshot_from_fixture(event, FIXTURE)
    return add_observation_package(event)


def make_observation_package(
    *,
    event_id: str = "runtime-evt-1",
    ohlcv: tuple[object, ...],
) -> ObservationPackage:
    return ObservationPackage(
        event_id=event_id,
        observation_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        normalized_price=102.0,
        normalized_ohlcv=ohlcv,
        normalized_volume=15.0,
        available_metrics=("price", "ohlcv", "volume"),
        missing_metrics=(),
        data_quality_status=DataQualityStatus.VALID,
    )


class StructureEngineTests(unittest.TestCase):
    def test_structure_validates_specialized_structural_evidence(self) -> None:
        event = add_structural_evidence(make_event_with_observation_package())

        evidence = refine_structural_evidence(event.structural_evidence)

        self.assertIsInstance(evidence, StructuralEvidence)
        self.assertIs(evidence, event.structural_evidence)
        self.assertEqual(evidence.event_id, event.event_id)
        self.assertEqual(
            evidence.technical_context["source_observation_event_id"],
            event.observation_package.event_id,
        )

    def test_structure_preserves_valid_existing_structural_evidence(
        self,
    ) -> None:
        event = add_structural_evidence(make_event_with_observation_package())
        snapshot_before = event.market_snapshot.to_dict()

        updated = add_structural_evidence(event)

        self.assertIsNot(updated, event)
        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertIs(updated.observation_package, event.observation_package)
        self.assertIs(updated.structural_evidence, event.structural_evidence)
        self.assertIsNone(updated.market_efficiency_evidence)
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_structure_rejects_misaligned_external_structural_evidence(
        self,
    ) -> None:
        event = add_structural_evidence(make_event_with_observation_package())

        with self.assertRaisesRegex(StructureError, "event_id"):
            refine_structural_evidence(
                event.structural_evidence,
                runtime_event_id="different-runtime-event",
            )

    def test_structure_reads_observation_package(self) -> None:
        event = make_event_with_observation_package()

        evidence = build_structural_evidence(event.observation_package)

        self.assertEqual(evidence.event_id, event.observation_package.event_id)
        self.assertEqual(
            evidence.technical_context["source_observation_event_id"],
            event.observation_package.event_id,
        )

    def test_structure_produces_valid_structural_evidence(self) -> None:
        observations = make_observation_package(
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 10.0,
                },
                {
                    "timestamp": "2026-07-01T12:01:00Z",
                    "open": 100.0,
                    "high": 103.0,
                    "low": 99.5,
                    "close": 102.0,
                    "volume": 15.0,
                },
            )
        )

        evidence = build_structural_evidence(observations)

        self.assertIsInstance(evidence, StructuralEvidence)
        self.assertEqual(evidence.trend_structure, "rising_close_sequence")
        self.assertEqual(evidence.structural_bias, "not_assessed")
        self.assertIn("ema_7_unavailable", evidence.structural_events)
        self.assertIn("no_valid_swing_impulse", evidence.structural_events)
        self.assertEqual(evidence.evidence_strength, EvidenceStrength.WEAK)
        self.assertEqual(evidence.uncertainty, UncertaintyLevel.HIGH)

    def test_structure_writes_only_structural_evidence(self) -> None:
        event = make_event_with_observation_package()

        updated = add_structural_evidence(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIs(event.observation_package, updated.observation_package)
        self.assertIsNotNone(updated.structural_evidence)
        self.assertIsNone(updated.market_efficiency_evidence)
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_structure_does_not_modify_market_snapshot_or_observation_package(self) -> None:
        event = make_event_with_observation_package()
        snapshot_before = event.market_snapshot.to_dict()
        observations_before = event.observation_package.to_dict()

        updated = add_structural_evidence(event)

        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertEqual(updated.observation_package.to_dict(), observations_before)

    def test_structure_requires_observation_package(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaises(StructureError):
            add_structural_evidence(event)

    def test_structure_preserves_uncertainty_when_ohlcv_is_insufficient(self) -> None:
        event = make_event_with_observation_package()

        updated = add_structural_evidence(event)

        self.assertEqual(
            updated.structural_evidence.trend_structure,
            "insufficient_sequence",
        )
        self.assertEqual(
            updated.structural_evidence.evidence_strength,
            EvidenceStrength.UNKNOWN,
        )
        self.assertEqual(
            updated.structural_evidence.uncertainty,
            UncertaintyLevel.HIGH,
        )

    def test_structure_rejects_malformed_ohlcv_candle(self) -> None:
        observations = make_observation_package(
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                },
            )
        )

        with self.assertRaisesRegex(StructureError, "volume"):
            build_structural_evidence(observations)

    def test_structure_rejects_non_mapping_ohlcv_candle_clearly(self) -> None:
        observations = make_observation_package(ohlcv=("bad-candle",))

        with self.assertRaisesRegex(StructureError, "must be a mapping"):
            build_structural_evidence(observations)

    def test_structure_does_not_import_market_snapshot_or_downstream_contracts(
        self,
    ) -> None:
        tree = ast.parse(STRUCTURE_ENGINE.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        imported_names = _imported_names_from(tree)
        forbidden_modules = (
            "pumpagent.runtime.modules.hypothesis",
            "pumpagent.runtime.modules.agent_state",
            "pumpagent.runtime.modules.scenario_probability",
            "pumpagent.runtime.modules.confidence",
            "pumpagent.runtime.modules.decision_alert",
            "pumpagent.runtime.modules.trading",
        )
        forbidden_names = {
            "MarketSnapshot",
            "HypothesisPackage",
            "AgentState",
            "AgentStateType",
            "ConfidenceAssessment",
            "ConfidenceLevel",
            "ScenarioProbability",
            "DecisionAlert",
            "DecisionType",
            "AlertLevel",
        }

        self.assertFalse(
            any(
                imported == module or imported.startswith(f"{module}.")
                for imported in imports
                for module in forbidden_modules
            )
        )
        self.assertTrue(forbidden_names.isdisjoint(imported_names))

    def test_structural_evidence_output_stays_evidence_only(self) -> None:
        observations = make_observation_package(
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 10.0,
                },
                {
                    "timestamp": "2026-07-01T12:01:00Z",
                    "open": 100.0,
                    "high": 103.0,
                    "low": 99.5,
                    "close": 102.0,
                    "volume": 15.0,
                },
            )
        )

        evidence = build_structural_evidence(observations)
        output_text = " ".join(_flatten_text(evidence.to_dict())).lower()
        forbidden_terms = (
            "agent_state",
            "hypothesis",
            "confidence",
            "decision",
            "alert",
            "trade",
            "trading_signal",
        )
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, output_text)

    def test_structure_candle_conversion_normalizes_numeric_values(self) -> None:
        candles = to_structure_candles(
            (
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": "100",
                    "high": "102",
                    "low": "99",
                    "close": "101",
                    "volume": "42",
                },
            )
        )

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].candle_index, 0)
        self.assertEqual(candles[0].open, 100.0)
        self.assertEqual(candles[0].high, 102.0)
        self.assertEqual(candles[0].low, 99.0)
        self.assertEqual(candles[0].close, 101.0)
        self.assertEqual(candles[0].volume, 42.0)

    def test_ema_warmup_requires_full_periods(self) -> None:
        six_candles = to_structure_candles(_linear_ohlcv(6))
        seven_candles = to_structure_candles(_linear_ohlcv(7))
        fourteen_candles = to_structure_candles(_linear_ohlcv(14))
        twenty_one_candles = to_structure_candles(_linear_ohlcv(21))

        self.assertEqual(calculate_emas(six_candles).available_periods, ())
        self.assertEqual(calculate_emas(seven_candles).available_periods, (7,))
        self.assertEqual(calculate_emas(fourteen_candles).available_periods, (7, 14))
        self.assertEqual(
            calculate_emas(twenty_one_candles).available_periods,
            (7, 14, 21),
        )

    def test_ema_calculation_uses_sma_seed_after_full_warmup(self) -> None:
        emas = calculate_emas(to_structure_candles(_linear_ohlcv(21)))

        self.assertEqual(emas.ema_7, 18.0)
        self.assertEqual(emas.ema_14, 14.5)
        self.assertEqual(emas.ema_21, 11.0)

    def test_swing_detection_uses_two_left_two_right_pivots(self) -> None:
        candles = to_structure_candles(_pivot_ohlcv())

        swing_highs, swing_lows = detect_swings(candles)

        self.assertEqual([point.candle_index for point in swing_highs], [3])
        self.assertEqual([point.price for point in swing_highs], [15.0])
        self.assertEqual([point.candle_index for point in swing_lows], [5])
        self.assertEqual([point.price for point in swing_lows], [5.0])

    def test_impulse_detection_uses_latest_opposite_swing_pair(self) -> None:
        candles = to_structure_candles(_pivot_ohlcv())
        swing_highs, swing_lows = detect_swings(candles)

        impulse = latest_valid_impulse(swing_highs, swing_lows)

        self.assertTrue(impulse.is_valid)
        self.assertEqual(impulse.direction, "down")
        self.assertEqual(impulse.high, 15.0)
        self.assertEqual(impulse.low, 5.0)
        self.assertEqual(impulse.start.candle_index, 3)
        self.assertEqual(impulse.end.candle_index, 5)

    def test_fibonacci_levels_are_calculated_from_valid_impulse(self) -> None:
        candles = to_structure_candles(_pivot_ohlcv())
        swing_highs, swing_lows = detect_swings(candles)
        impulse = latest_valid_impulse(swing_highs, swing_lows)

        levels = calculate_fibonacci_levels(impulse)

        self.assertEqual(
            [level.ratio for level in levels],
            [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0],
        )
        self.assertEqual(levels[0].price, 5.0)
        self.assertEqual(levels[3].price, 10.0)
        self.assertEqual(levels[-1].price, 15.0)

    def test_build_structural_evidence_serializes_chart_structure(self) -> None:
        observations = make_observation_package(ohlcv=_structure_ohlcv())

        evidence = build_structural_evidence(observations)
        chart_structure = evidence.technical_context["chart_structure"]

        self.assertEqual(
            evidence.trend_structure,
            "ema_swing_fibonacci_structure_available",
        )
        self.assertIn("ema_21_available", evidence.structural_events)
        self.assertIn("valid_impulse_detected", evidence.structural_events)
        self.assertIn("fibonacci_levels_available", evidence.structural_events)
        self.assertEqual(chart_structure["schema_version"], "structure_chart_v1")
        self.assertEqual(
            set(chart_structure["emas"]),
            {
                "ema_7",
                "ema_14",
                "ema_21",
                "available_periods",
                "unavailable_periods",
            },
        )
        self.assertEqual(chart_structure["latest_impulse"]["direction"], "down")
        self.assertTrue(chart_structure["fibonacci_levels"])
        self.assertEqual(evidence.structural_bias, "not_assessed")

    def test_insufficient_candle_data_returns_partial_structure(self) -> None:
        observations = make_observation_package(ohlcv=_linear_ohlcv(1))

        evidence = build_structural_evidence(observations)
        chart_structure = evidence.technical_context["chart_structure"]

        self.assertEqual(evidence.trend_structure, "insufficient_sequence")
        self.assertEqual(evidence.evidence_strength, EvidenceStrength.UNKNOWN)
        self.assertEqual(evidence.uncertainty, UncertaintyLevel.HIGH)
        self.assertIn("insufficient_ohlcv_sequence", evidence.structural_events)
        self.assertIn("ema_7_unavailable", evidence.structural_events)
        self.assertEqual(chart_structure["warnings"][0], "insufficient_ohlcv_sequence")


def _imports_from(tree: ast.AST) -> tuple[str, ...]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(imports)


def _imported_names_from(tree: ast.AST) -> set[str]:
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def _flatten_text(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        items: list[str] = []
        for key, item in value.items():
            items.extend(_flatten_text(key))
            items.extend(_flatten_text(item))
        return tuple(items)
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            items.extend(_flatten_text(item))
        return tuple(items)
    if value is None:
        return ()
    return (str(value),)


def _linear_ohlcv(count: int) -> tuple[dict[str, object], ...]:
    candles = []
    for index in range(count):
        close = float(index + 1)
        candles.append(
            {
                "timestamp": f"2026-07-01T12:{index:02d}:00Z",
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 10.0 + index,
            }
        )
    return tuple(candles)


def _pivot_ohlcv() -> tuple[dict[str, object], ...]:
    highs = (10, 11, 12, 15, 13, 12, 12, 11, 10)
    lows = (8, 7, 7, 7, 8, 5, 7, 8, 9)
    candles = []
    for index, (high, low) in enumerate(zip(highs, lows)):
        candles.append(
            {
                "timestamp": f"2026-07-01T12:{index:02d}:00Z",
                "open": float(low + 1),
                "high": float(high),
                "low": float(low),
                "close": float((high + low) / 2),
                "volume": 10.0,
            }
        )
    return tuple(candles)


def _structure_ohlcv() -> tuple[dict[str, object], ...]:
    base = list(_linear_ohlcv(21))
    pivot = list(_pivot_ohlcv())
    for index, candle in enumerate(pivot, start=21):
        updated = dict(candle)
        updated["timestamp"] = f"2026-07-01T12:{index:02d}:00Z"
        base.append(updated)
    return tuple(base)


if __name__ == "__main__":
    unittest.main()
