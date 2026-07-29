from __future__ import annotations

import copy
from dataclasses import replace
from unittest import TestCase

from pumpagent.runtime.orchestrator import (
    RuntimeOrchestrator,
    serialize_runtime_event,
)
from tests.runtime.orchestrator.test_runtime_loop import (
    TEST_EPISODE_ID,
    make_snapshot,
)


def forged(value, **changes):  # type: ignore[no-untyped-def]
    result = copy.copy(value)
    for name, replacement in changes.items():
        object.__setattr__(result, name, replacement)
    return result


class RuntimeEventIdentityTests(TestCase):
    def setUp(self) -> None:
        self.event = RuntimeOrchestrator().process_market_update(
            make_snapshot(), episode_id=TEST_EPISODE_ID
        )

    def test_each_canonical_identity_boundary_is_authenticated(self) -> None:
        event = self.event
        cases = (
            ("market_snapshot", forged(event.market_snapshot, symbol="ETHUSDT")),
            (
                "market_snapshot",
                forged(event.market_snapshot, timestamp=event.cycle_timestamp.replace(hour=1)),
            ),
            ("observation_package", forged(event.observation_package, event_id="forged")),
            ("structural_evidence", forged(event.structural_evidence, event_id="forged")),
            (
                "market_efficiency_evidence",
                forged(event.market_efficiency_evidence, event_id="forged"),
            ),
            ("process_evidence", forged(event.process_evidence, runtime_event_id="forged")),
            ("process_evidence", forged(event.process_evidence, episode_id="forged")),
            ("process_quality_assessment", forged(event.process_quality_assessment, runtime_event_id="forged")),
            ("process_quality_assessment", forged(event.process_quality_assessment, episode_id="forged")),
            ("hypothesis_package", forged(event.hypothesis_package, event_id="forged")),
            ("hypothesis_package", forged(event.hypothesis_package, episode_id="forged")),
            ("agent_state", forged(event.agent_state, event_id="forged")),
            ("scenario_probability", forged(event.scenario_probability, runtime_event_id="forged")),
            ("scenario_probability", forged(event.scenario_probability, episode_id="forged")),
            ("scenario_probability", forged(event.scenario_probability, source_hypothesis_id="forged")),
            ("confidence_assessment", forged(event.confidence_assessment, event_id="forged")),
            ("confidence_assessment", forged(event.confidence_assessment, episode_id="forged")),
            ("confidence_assessment", forged(event.confidence_assessment, source_hypothesis_id="forged")),
            ("decision_assessment", forged(event.decision_assessment, runtime_event_id="forged")),
            ("decision_assessment", forged(event.decision_assessment, episode_id="forged")),
            ("decision_assessment", forged(event.decision_assessment, hypothesis_reference="forged")),
            (
                "decision_assessment",
                forged(event.decision_assessment, scenario_probability_reference="forged"),
            ),
        )
        for section, replacement in cases:
            with self.subTest(section=section), self.assertRaises(ValueError):
                replace(event, **{section: replacement})

    def test_completed_event_sections_cannot_be_replaced_publicly(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be replaced"):
            self.event.with_sections(
                agent_state=forged(self.event.agent_state, event_id="forged")
            )

    def test_canonical_serialization_reauthenticates_forged_objects(self) -> None:
        forged_event = copy.copy(self.event)
        object.__setattr__(
            forged_event,
            "agent_state",
            forged(self.event.agent_state, event_id="forged"),
        )

        with self.assertRaisesRegex(ValueError, "AgentState"):
            serialize_runtime_event(forged_event)

        if self.event.learning_metadata is not None:
            self.fail("Canonical production event unexpectedly contained metadata.")

    def test_process_quality_history_and_baseline_are_authenticated(self) -> None:
        with self.assertRaisesRegex(ValueError, "history"):
            replace(self.event, process_quality_history=())
        with self.assertRaisesRegex(ValueError, "reference and designation"):
            replace(
                self.event,
                healthy_baseline_reference=forged(
                    self.event.process_quality_assessment.to_reference(),
                    assessment_id="forged",
                ),
            )
