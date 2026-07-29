from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
MARKET_EFFICIENCY_ENGINE = (
    SRC / "pumpagent" / "runtime" / "modules" / "market_efficiency" / "engine.py"
)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import (
    MarketEfficiencyEvidence,
    ObservationPackage,
    RuntimeEvent,
)
from pumpagent.runtime.domain.enums import (
    DataQualityStatus,
    EvidenceStrength,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import (
    MarketEfficiencyError,
    add_market_efficiency_evidence,
    build_market_efficiency_evidence,
    refine_market_efficiency_evidence,
)
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.structure import add_structural_evidence


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


def make_event_with_structural_evidence() -> RuntimeEvent:
    return add_structural_evidence(make_event_with_observation_package())


def make_observation_package(
    *,
    event_id: str = "runtime-evt-1",
    normalized_metrics: object | None = None,
    normalized_volume: float = 15.0,
) -> ObservationPackage:
    return ObservationPackage(
        event_id=event_id,
        observation_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        normalized_price=102.0,
        normalized_ohlcv=(
            {
                "timestamp": "2026-07-01T12:00:00Z",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": normalized_volume,
            },
        ),
        normalized_volume=normalized_volume,
        available_metrics=("price", "ohlcv", "volume"),
        missing_metrics=(),
        data_quality_status=DataQualityStatus.VALID,
        normalized_metrics=normalized_metrics or {},
    )


class MarketEfficiencyEngineTests(unittest.TestCase):
    def test_market_efficiency_validates_specialized_evidence(self) -> None:
        event = add_market_efficiency_evidence(make_event_with_observation_package())

        evidence = refine_market_efficiency_evidence(
            event.market_efficiency_evidence
        )

        self.assertIsInstance(evidence, MarketEfficiencyEvidence)
        self.assertIs(evidence, event.market_efficiency_evidence)
        self.assertEqual(evidence.event_id, event.event_id)
        self.assertEqual(evidence.efficiency_status, "not_assessed")
        self.assertEqual(
            evidence.market_mechanics_context["source_observation_event_id"],
            event.observation_package.event_id,
        )

    def test_market_efficiency_preserves_valid_existing_evidence(
        self,
    ) -> None:
        event = add_market_efficiency_evidence(make_event_with_structural_evidence())
        snapshot_before = event.market_snapshot.to_dict()
        structure_before = event.structural_evidence.to_dict()

        updated = add_market_efficiency_evidence(event)

        self.assertIsNot(updated, event)
        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertIs(updated.observation_package, event.observation_package)
        self.assertEqual(updated.structural_evidence.to_dict(), structure_before)
        self.assertIs(
            updated.market_efficiency_evidence,
            event.market_efficiency_evidence,
        )
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_market_efficiency_rejects_misaligned_external_evidence(
        self,
    ) -> None:
        event = add_market_efficiency_evidence(make_event_with_observation_package())

        with self.assertRaisesRegex(MarketEfficiencyError, "event_id"):
            refine_market_efficiency_evidence(
                event.market_efficiency_evidence,
                runtime_event_id="different-runtime-event",
            )

    def test_market_efficiency_reads_observation_package(self) -> None:
        event = make_event_with_observation_package()

        evidence = build_market_efficiency_evidence(event.observation_package)

        self.assertEqual(evidence.event_id, event.observation_package.event_id)
        self.assertEqual(
            evidence.market_mechanics_context["source_observation_event_id"],
            event.observation_package.event_id,
        )

    def test_market_efficiency_produces_valid_evidence(self) -> None:
        event = make_event_with_observation_package()

        evidence = build_market_efficiency_evidence(event.observation_package)

        self.assertIsInstance(evidence, MarketEfficiencyEvidence)
        self.assertEqual(evidence.participation_direction, "not_assessed")
        self.assertEqual(evidence.efficiency_status, "not_assessed")
        self.assertIn("open_interest_available", evidence.supporting_evidence)
        self.assertIn("liquidations_available", evidence.supporting_evidence)
        self.assertEqual(evidence.evidence_strength, EvidenceStrength.MODERATE)
        self.assertEqual(evidence.uncertainty, UncertaintyLevel.MEDIUM)

    def test_market_efficiency_writes_only_market_efficiency_evidence(self) -> None:
        event = make_event_with_structural_evidence()

        updated = add_market_efficiency_evidence(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIs(event.observation_package, updated.observation_package)
        self.assertIs(event.structural_evidence, updated.structural_evidence)
        self.assertIsNotNone(updated.market_efficiency_evidence)
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_market_efficiency_does_not_modify_existing_sections(self) -> None:
        event = make_event_with_structural_evidence()
        snapshot_before = event.market_snapshot.to_dict()
        observations_before = event.observation_package.to_dict()
        structure_before = event.structural_evidence.to_dict()

        updated = add_market_efficiency_evidence(event)

        self.assertEqual(updated.market_snapshot.to_dict(), snapshot_before)
        self.assertEqual(updated.observation_package.to_dict(), observations_before)
        self.assertEqual(updated.structural_evidence.to_dict(), structure_before)

    def test_market_efficiency_requires_observation_package(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaises(MarketEfficiencyError):
            add_market_efficiency_evidence(event)

    def test_market_efficiency_handles_missing_optional_participation_metrics(
        self,
    ) -> None:
        observations = make_observation_package(normalized_metrics={})

        evidence = build_market_efficiency_evidence(observations)

        self.assertEqual(
            evidence.participation_summary,
            "Only volume participation metric available.",
        )
        self.assertEqual(evidence.participation_direction, "not_assessed")
        self.assertEqual(evidence.efficiency_status, "not_assessed")
        self.assertIn("volume_available", evidence.supporting_evidence)
        self.assertIn("open_interest_missing", evidence.evidence_against)
        self.assertIn("funding_rate_missing", evidence.evidence_against)
        self.assertIn("cvd_missing", evidence.evidence_against)
        self.assertIn("liquidations_missing", evidence.evidence_against)
        self.assertEqual(evidence.evidence_strength, EvidenceStrength.WEAK)
        self.assertEqual(evidence.uncertainty, UncertaintyLevel.HIGH)

    def test_market_efficiency_participation_metric_order_is_deterministic(
        self,
    ) -> None:
        observations = make_observation_package(
            normalized_metrics={
                "liquidations": {"long": 10.0, "short": 20.0},
                "cvd": 250.0,
                "funding_rate": 0.0001,
                "open_interest": 1200.5,
            }
        )

        evidence = build_market_efficiency_evidence(observations)

        self.assertEqual(
            evidence.market_mechanics_context["available_participation_metrics"],
            ("volume", "open_interest", "funding_rate", "cvd", "liquidations"),
        )
        self.assertEqual(
            evidence.supporting_evidence,
            (
                "volume_available",
                "open_interest_available",
                "funding_rate_available",
                "cvd_available",
                "liquidations_available",
            ),
        )

    def test_market_efficiency_rejects_non_mapping_normalized_metrics_clearly(
        self,
    ) -> None:
        observations = make_observation_package(
            normalized_metrics=("bad-metrics",),
        )

        with self.assertRaisesRegex(MarketEfficiencyError, "must be a mapping"):
            build_market_efficiency_evidence(observations)

    def test_market_efficiency_does_not_import_structure_or_downstream_contracts(
        self,
    ) -> None:
        tree = ast.parse(MARKET_EFFICIENCY_ENGINE.read_text(encoding="utf-8"))
        imports = _imports_from(tree)
        imported_names = _imported_names_from(tree)
        forbidden_modules = (
            "pumpagent.runtime.modules.structure",
            "pumpagent.runtime.modules.hypothesis",
            "pumpagent.runtime.modules.agent_state",
            "pumpagent.runtime.modules.scenario_probability",
            "pumpagent.runtime.modules.confidence",
            "pumpagent.runtime.modules.decision_alert",
            "pumpagent.runtime.modules.trading",
        )
        forbidden_names = {
            "MarketSnapshot",
            "StructuralEvidence",
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

    def test_market_efficiency_evidence_output_stays_evidence_only(self) -> None:
        observations = make_observation_package(
            normalized_metrics={
                "open_interest": 1200.5,
                "funding_rate": 0.0001,
                "cvd": 250.0,
                "liquidations": {"long": 10.0, "short": 20.0},
            }
        )

        evidence = build_market_efficiency_evidence(observations)
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
