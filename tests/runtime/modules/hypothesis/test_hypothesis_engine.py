from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
HYPOTHESIS_ENGINE = SRC / "pumpagent" / "runtime" / "modules" / "hypothesis" / "engine.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import HypothesisPackage, RuntimeEvent
from pumpagent.runtime.domain.enums import ConfidenceLevel, UncertaintyLevel
from pumpagent.runtime.modules.hypothesis import (
    HypothesisError,
    add_hypothesis_package,
    build_hypothesis_package,
)
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.structure import add_structural_evidence


def make_event_with_evidence() -> RuntimeEvent:
    event = RuntimeEvent(
        event_id="runtime-evt-1",
        schema_version="1.0",
        cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
    )
    event = add_market_snapshot_from_fixture(event, FIXTURE)
    event = add_observation_package(event)
    event = add_structural_evidence(event)
    return add_market_efficiency_evidence(event)


class HypothesisEngineTests(unittest.TestCase):
    def test_hypothesis_reads_structural_and_market_efficiency_evidence(self) -> None:
        event = make_event_with_evidence()

        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
        )

        self.assertEqual(hypothesis.event_id, event.structural_evidence.event_id)
        self.assertIn(
            "structure:insufficient_ohlcv_sequence",
            hypothesis.supporting_evidence,
        )
        self.assertIn(
            "market_efficiency:volume_available",
            hypothesis.supporting_evidence,
        )

    def test_hypothesis_produces_valid_hypothesis_package(self) -> None:
        event = make_event_with_evidence()

        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
        )

        self.assertIsInstance(hypothesis, HypothesisPackage)
        self.assertEqual(
            hypothesis.hypothesis_label,
            "current_condition_explanation",
        )
        self.assertEqual(
            hypothesis.current_hypothesis_confidence_context,
            ConfidenceLevel.LOW,
        )
        self.assertEqual(hypothesis.uncertainty, UncertaintyLevel.HIGH)
        self.assertIn("does not decide official state", hypothesis.reasoning_notes)

    def test_hypothesis_writes_only_hypothesis_package_not_confidence_assessment(
        self,
    ) -> None:
        event = make_event_with_evidence()

        updated = add_hypothesis_package(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIs(event.observation_package, updated.observation_package)
        self.assertIs(event.structural_evidence, updated.structural_evidence)
        self.assertIs(
            event.market_efficiency_evidence,
            updated.market_efficiency_evidence,
        )
        self.assertIsNotNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)
        self.assertIsNotNone(
            updated.hypothesis_package.current_hypothesis_confidence_context
        )

    def test_hypothesis_does_not_modify_existing_sections(self) -> None:
        event = make_event_with_evidence()
        snapshot_before = event.market_snapshot.to_dict()
        observations_before = event.observation_package.to_dict()
        structure_before = event.structural_evidence.to_dict()
        efficiency_before = event.market_efficiency_evidence.to_dict()

        updated = add_hypothesis_package(event)

        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertEqual(updated.observation_package.to_dict(), observations_before)
        self.assertEqual(updated.structural_evidence.to_dict(), structure_before)
        self.assertEqual(
            updated.market_efficiency_evidence.to_dict(),
            efficiency_before,
        )

    def test_hypothesis_requires_structural_evidence(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )
        event = add_market_snapshot_from_fixture(event, FIXTURE)
        event = add_observation_package(event)
        event = add_market_efficiency_evidence(event)

        with self.assertRaisesRegex(HypothesisError, "structural_evidence"):
            add_hypothesis_package(event)

    def test_hypothesis_requires_market_efficiency_evidence(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )
        event = add_market_snapshot_from_fixture(event, FIXTURE)
        event = add_observation_package(event)
        event = add_structural_evidence(event)

        with self.assertRaisesRegex(HypothesisError, "market_efficiency_evidence"):
            add_hypothesis_package(event)

    def test_hypothesis_does_not_import_downstream_runtime_contracts(self) -> None:
        tree = ast.parse(HYPOTHESIS_ENGINE.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        imported_names = _imported_names_from(tree)
        forbidden_modules = (
            "pumpagent.runtime.modules.agent_state",
            "pumpagent.runtime.modules.scenario_probability",
            "pumpagent.runtime.modules.confidence",
            "pumpagent.runtime.modules.decision_alert",
            "pumpagent.runtime.modules.trading",
        )
        forbidden_names = {
            "AgentState",
            "AgentStateType",
            "ScenarioProbability",
            "ConfidenceAssessment",
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


if __name__ == "__main__":
    unittest.main()
