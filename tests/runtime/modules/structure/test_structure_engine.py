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
from pumpagent.runtime.modules.perception import (
    add_observation_package,
    add_perception_evidence,
)
from pumpagent.runtime.modules.structure import (
    StructureError,
    add_structural_evidence,
    build_structural_evidence,
    refine_structural_evidence,
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


def make_event_with_perception_evidence() -> RuntimeEvent:
    event = RuntimeEvent(
        event_id="runtime-evt-1",
        schema_version="1.0",
        cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
    )
    event = add_market_snapshot_from_fixture(event, FIXTURE)
    return add_perception_evidence(event)


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
    def test_structure_refines_perception_structural_evidence(self) -> None:
        event = make_event_with_perception_evidence()

        evidence = refine_structural_evidence(event.structural_evidence)

        self.assertIsInstance(evidence, StructuralEvidence)
        self.assertIs(evidence, event.structural_evidence)
        self.assertEqual(evidence.event_id, event.event_id)
        self.assertEqual(evidence.structural_bias, "not_assessed")
        self.assertEqual(
            evidence.technical_context["source_snapshot_event_id"],
            event.market_snapshot.event_id,
        )

    def test_structure_can_run_after_perception_without_touching_other_sections(
        self,
    ) -> None:
        event = make_event_with_perception_evidence()
        snapshot_before = event.market_snapshot.to_dict()
        efficiency_before = event.market_efficiency_evidence.to_dict()

        updated = add_structural_evidence(event)

        self.assertIsNot(updated, event)
        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertIsNone(updated.observation_package)
        self.assertIs(updated.structural_evidence, event.structural_evidence)
        self.assertEqual(
            updated.market_efficiency_evidence.to_dict(),
            efficiency_before,
        )
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_structure_rejects_misaligned_perception_structural_evidence(
        self,
    ) -> None:
        event = make_event_with_perception_evidence()

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
        self.assertIn("higher_final_close", evidence.structural_events)
        self.assertEqual(evidence.evidence_strength, EvidenceStrength.MODERATE)
        self.assertEqual(evidence.uncertainty, UncertaintyLevel.MEDIUM)

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


if __name__ == "__main__":
    unittest.main()
