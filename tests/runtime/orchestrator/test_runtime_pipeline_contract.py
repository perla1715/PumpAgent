from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
FIXTURE_ORCHESTRATOR = (
    SRC / "pumpagent" / "runtime" / "orchestrator" / "fixture_orchestrator.py"
)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import HypothesisLifecycleStatus, RuntimeEvent
from pumpagent.runtime.domain.enums import AlertCategory, ProcessDirection
from pumpagent.runtime.modules.agent_state import add_agent_state
from pumpagent.runtime.modules.hypothesis import add_hypothesis_package
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.structure import add_structural_evidence


RUNTIME_SECTIONS = (
    "market_snapshot",
    "observation_package",
    "structural_evidence",
    "market_efficiency_evidence",
    "hypothesis_package",
    "agent_state",
    "scenario_probability",
    "confidence_assessment",
    "decision_alert",
    "learning_metadata",
)


class RuntimePipelineContractTests(unittest.TestCase):
    def test_runtime_reasoning_order_keeps_confidence_after_state_and_scenario(
        self,
    ) -> None:
        stages = (
            "Market Data",
            "Perception",
            "Hypothesis",
            "Agent State",
            "Scenario Probability",
            "Confidence",
            "Decision / Alert",
        )

        self.assertLess(stages.index("Hypothesis"), stages.index("Agent State"))
        self.assertLess(stages.index("Agent State"), stages.index("Scenario Probability"))
        self.assertLess(stages.index("Scenario Probability"), stages.index("Confidence"))
        self.assertLess(stages.index("Confidence"), stages.index("Decision / Alert"))

    def test_runtime_orchestrator_does_not_import_forbidden_layers(self) -> None:
        imports = _imports_from(
            ast.parse(FIXTURE_ORCHESTRATOR.read_text(encoding="utf-8"))
        )
        forbidden_modules = (
            "pumpagent.live_data",
            "pumpagent.runtime.modules.learning_memory",
            "pumpagent.runtime.modules.trading",
        )

        self.assertFalse(
            any(
                imported == module or imported.startswith(f"{module}.")
                for imported in imports
                for module in forbidden_modules
            )
        )

    def test_runtime_pipeline_adds_one_owned_section_per_stage(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )
        active_sections: list[str] = []

        stages = (
            (
                "Market Data",
                ("market_snapshot",),
                lambda current: add_market_snapshot_from_fixture(current, FIXTURE),
            ),
            (
                "Perception",
                ("observation_package",),
                add_observation_package,
            ),
            (
                "Structure",
                ("structural_evidence",),
                add_structural_evidence,
            ),
            (
                "Market Efficiency",
                ("market_efficiency_evidence",),
                add_market_efficiency_evidence,
            ),
            (
                "Hypothesis",
                ("hypothesis_package",),
                lambda current: add_hypothesis_package(
                    current,
                    episode_id="episode-test-1",
                    hypothesis_id="hypothesis-test-1",
                    explanation_confidence_score=50,
                    lifecycle_status=HypothesisLifecycleStatus.CREATED,
                    hypothesis_change_reason=(
                        "Initial hypothesis for the pipeline contract test."
                    ),
                ),
            ),
            (
                "Agent State",
                ("agent_state",),
                lambda current: add_agent_state(
                    current,
                    process_direction=ProcessDirection.UNKNOWN,
                ),
            ),
        )

        for stage_name, added_sections, stage_function in stages:
            before = event
            before_sections = _section_values(before)

            event = stage_function(event)

            self.assertIsNot(event, before, stage_name)
            for added_section in added_sections:
                self.assertIsNotNone(getattr(event, added_section), stage_name)

            for section in active_sections:
                self.assertEqual(
                    _section_values(event)[section],
                    before_sections[section],
                    f"{stage_name} modified previous section {section}",
                )

            active_sections.extend(added_sections)
            for section in RUNTIME_SECTIONS:
                if section not in active_sections:
                    self.assertIsNone(
                        getattr(event, section),
                        f"{stage_name} populated future section {section}",
                    )

        self.assertIsNone(event.scenario_probability)
        self.assertIsNone(event.confidence_assessment)
        self.assertIsNone(event.decision_alert)
        self.assertIsNone(event.learning_metadata)


def _section_values(event: RuntimeEvent) -> dict[str, object]:
    values: dict[str, object] = {}
    for section in RUNTIME_SECTIONS:
        section_value = getattr(event, section)
        if hasattr(section_value, "to_dict"):
            values[section] = section_value.to_dict()
        else:
            values[section] = section_value
    return values


def _imports_from(tree: ast.AST) -> tuple[str, ...]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(imports)


if __name__ == "__main__":
    unittest.main()
