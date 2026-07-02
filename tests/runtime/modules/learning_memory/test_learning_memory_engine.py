from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import LearningMetadata, RuntimeEvent
from pumpagent.runtime.domain.enums import ReviewStatus
from pumpagent.runtime.modules.agent_state import add_agent_state
from pumpagent.runtime.modules.confidence import add_confidence_assessment
from pumpagent.runtime.modules.decision_alert import add_decision_alert
from pumpagent.runtime.modules.hypothesis import add_hypothesis_package
from pumpagent.runtime.modules.learning_memory import (
    LearningMemoryError,
    add_learning_metadata,
    build_learning_metadata,
)
from pumpagent.runtime.modules.learning_memory.engine import (
    REQUIRED_COMPLETED_EVENT_SECTIONS,
    RUNTIME_OWNED_EVENT_ID_SECTIONS,
)
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.scenario_probability import add_scenario_probability
from pumpagent.runtime.modules.structure import add_structural_evidence


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def make_base_event() -> RuntimeEvent:
    return RuntimeEvent(
        event_id="runtime-evt-1",
        schema_version="1.0",
        cycle_timestamp=NOW,
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
    )


def make_event_with_decision_alert() -> RuntimeEvent:
    event = make_base_event()
    event = add_market_snapshot_from_fixture(event, FIXTURE)
    event = add_observation_package(event)
    event = add_structural_evidence(event)
    event = add_market_efficiency_evidence(event)
    event = add_hypothesis_package(event)
    event = add_agent_state(event)
    event = add_scenario_probability(event)
    event = add_confidence_assessment(event)
    return add_decision_alert(event)


class LearningMemoryEngineTests(unittest.TestCase):
    def test_learning_memory_requires_completed_runtime_event_for_metadata(
        self,
    ) -> None:
        event = make_event_with_decision_alert()

        metadata = build_learning_metadata(event, created_at=NOW)

        for section in REQUIRED_COMPLETED_EVENT_SECTIONS:
            with self.subTest(section=section):
                self.assertIsNotNone(getattr(event, section))
        self.assertEqual(metadata.event_id, event.event_id)
        self.assertEqual(metadata.schema_version, event.schema_version)
        self.assertEqual(metadata.case_id, "case-runtime-evt-1")
        self.assertIn(event.decision_alert.decision_type.value, metadata.storage_reason)

    def test_learning_memory_rejects_each_missing_completed_event_section(
        self,
    ) -> None:
        event = make_event_with_decision_alert()

        for section in REQUIRED_COMPLETED_EVENT_SECTIONS:
            with self.subTest(section=section):
                incomplete_event = event.with_sections(**{section: None})

                with self.assertRaisesRegex(LearningMemoryError, section):
                    build_learning_metadata(incomplete_event, created_at=NOW)

    def test_learning_memory_rejects_mismatched_runtime_owned_event_ids(
        self,
    ) -> None:
        event = make_event_with_decision_alert()

        for section in RUNTIME_OWNED_EVENT_ID_SECTIONS:
            with self.subTest(section=section):
                section_value = getattr(event, section)
                mismatched_section = replace(section_value, event_id="other-event")
                mismatched_event = event.with_sections(**{section: mismatched_section})

                with self.assertRaisesRegex(LearningMemoryError, section):
                    build_learning_metadata(mismatched_event, created_at=NOW)

    def test_learning_memory_allows_source_specific_market_snapshot_event_id(
        self,
    ) -> None:
        event = make_event_with_decision_alert()

        metadata = build_learning_metadata(event, created_at=NOW)

        self.assertNotEqual(event.market_snapshot.event_id, event.event_id)
        self.assertEqual(metadata.event_id, event.event_id)

    def test_learning_memory_rejects_market_snapshot_identity_mismatch(
        self,
    ) -> None:
        event = make_event_with_decision_alert()
        mismatched_snapshot = replace(event.market_snapshot, symbol="ETHUSDT")
        mismatched_event = event.with_sections(market_snapshot=mismatched_snapshot)

        with self.assertRaisesRegex(LearningMemoryError, "symbol"):
            build_learning_metadata(mismatched_event, created_at=NOW)

    def test_learning_memory_produces_valid_learning_metadata(self) -> None:
        event = make_event_with_decision_alert()

        metadata = build_learning_metadata(event, created_at=NOW)

        self.assertIsInstance(metadata, LearningMetadata)
        self.assertTrue(metadata.should_store)
        self.assertEqual(metadata.review_status, ReviewStatus.PENDING)
        self.assertTrue(metadata.outcome_pending)
        self.assertIsNone(metadata.outcome_summary)
        self.assertIsNone(metadata.human_annotation)

    def test_learning_memory_writes_only_learning_metadata(self) -> None:
        event = make_event_with_decision_alert()

        updated = add_learning_metadata(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIs(event.observation_package, updated.observation_package)
        self.assertIs(event.structural_evidence, updated.structural_evidence)
        self.assertIs(
            event.market_efficiency_evidence,
            updated.market_efficiency_evidence,
        )
        self.assertIs(event.hypothesis_package, updated.hypothesis_package)
        self.assertIs(event.agent_state, updated.agent_state)
        self.assertIs(event.scenario_probability, updated.scenario_probability)
        self.assertIs(event.confidence_assessment, updated.confidence_assessment)
        self.assertIs(event.decision_alert, updated.decision_alert)
        self.assertIsNotNone(updated.learning_metadata)

    def test_learning_memory_does_not_modify_previous_sections_or_decision_alert(
        self,
    ) -> None:
        event = make_event_with_decision_alert()
        previous_sections = {
            "market_snapshot": event.market_snapshot.to_dict(),
            "observation_package": event.observation_package.to_dict(),
            "structural_evidence": event.structural_evidence.to_dict(),
            "market_efficiency_evidence": event.market_efficiency_evidence.to_dict(),
            "hypothesis_package": event.hypothesis_package.to_dict(),
            "agent_state": event.agent_state.to_dict(),
            "scenario_probability": event.scenario_probability.to_dict(),
            "confidence_assessment": event.confidence_assessment.to_dict(),
            "decision_alert": event.decision_alert.to_dict(),
        }

        updated = add_learning_metadata(event)

        for section, expected in previous_sections.items():
            with self.subTest(section=section):
                self.assertEqual(getattr(updated, section).to_dict(), expected)

    def test_learning_memory_does_not_trigger_research_or_runtime_changes(self) -> None:
        event = make_event_with_decision_alert()

        updated = add_learning_metadata(event)

        self.assertEqual(updated.decision_alert.to_dict(), event.decision_alert.to_dict())
        self.assertEqual(updated.learning_metadata.research_tags, ())
        self.assertEqual(updated.learning_metadata.similarity_tags, ())
        self.assertIsNone(updated.learning_metadata.lesson_learned)
        self.assertIsNone(updated.learning_metadata.follow_up_event_id)
        self.assertIsNone(updated.learning_metadata.reviewed_by)

    def test_learning_memory_requires_decision_alert(self) -> None:
        event = make_base_event()
        event = add_market_snapshot_from_fixture(event, FIXTURE)
        event = add_observation_package(event)
        event = add_structural_evidence(event)
        event = add_market_efficiency_evidence(event)
        event = add_hypothesis_package(event)
        event = add_agent_state(event)
        event = add_scenario_probability(event)
        event = add_confidence_assessment(event)

        with self.assertRaisesRegex(LearningMemoryError, "decision_alert"):
            add_learning_metadata(event)

    def test_learning_memory_serialization_is_storage_ready(self) -> None:
        event = make_event_with_decision_alert()

        updated = add_learning_metadata(event)
        serialized = updated.to_dict()

        self.assertEqual(
            serialized["learning_metadata"]["review_status"],
            ReviewStatus.PENDING.value,
        )
        self.assertTrue(serialized["learning_metadata"]["should_store"])
        self.assertIn("created_at", serialized["learning_metadata"])


if __name__ == "__main__":
    unittest.main()
