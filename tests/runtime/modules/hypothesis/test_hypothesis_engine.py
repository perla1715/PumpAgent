from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
import json
from math import inf, nan
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
HYPOTHESIS_ENGINE = SRC / "pumpagent" / "runtime" / "modules" / "hypothesis" / "engine.py"
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import (
    AgentState,
    HypothesisEvidenceReference,
    HypothesisLifecycleStatus,
    HypothesisPackage,
    HypothesisSemanticCode,
    ProcessEvidence,
    ProcessEvidenceAvailability,
    ProcessEvidenceFamily,
    ProcessEvidenceItem,
    ProcessEvidenceRelationship,
    ProcessState,
    ProcessTransition,
    RuntimeEvent,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    EvidenceStrength,
    ProcessDirection,
    StateTransitionStatus,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.evidence import EvidenceSummary
from pumpagent.runtime.modules.hypothesis import (
    EVALUATION_NEUTRAL,
    EVALUATION_REINFORCED,
    EVALUATION_UNKNOWN,
    EVALUATION_WEAKENING,
    HistoryTrendAnalyzer,
    HistoryTrendSummary,
    HypothesisError,
    HypothesisEvaluation,
    HypothesisEvaluator,
    HypothesisHistory,
    HypothesisSnapshot,
    TREND_IMPROVING,
    TREND_STABLE,
    TREND_UNKNOWN,
    TREND_WEAKENING,
    add_hypothesis_package,
    build_hypothesis_package,
    build_operational_hypothesis_package,
    build_hypothesis_snapshot,
)
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.market_efficiency import add_market_efficiency_evidence
from pumpagent.runtime.modules.perception import add_observation_package
from pumpagent.runtime.modules.structure import add_structural_evidence


CANONICAL_HYPOTHESIS_INPUT = {
    "episode_id": "episode-test-1",
    "hypothesis_id": "hypothesis-test-1",
    "explanation_confidence_score": 25,
    "lifecycle_status": HypothesisLifecycleStatus.CREATED,
    "hypothesis_change_reason": "Initial hypothesis for the test episode.",
}


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


def make_process_evidence(
    state: ProcessState,
    *,
    transition: ProcessTransition,
    previous_state: ProcessState | None,
) -> ProcessEvidence:
    supporting = ()
    missing_families = frozenset()
    insufficiency_reasons = ()
    if state is ProcessState.UNKNOWN:
        missing_families = frozenset((ProcessEvidenceFamily.PRICE,))
        insufficiency_reasons = ("Process state is unresolved.",)
    else:
        supporting = (
            ProcessEvidenceItem(
                evidence_family=ProcessEvidenceFamily.PRICE,
                evidence_key="classified_process",
                description="Canonical Process Classification result.",
                relationship=ProcessEvidenceRelationship.SUPPORTING,
                source_module="process_classification",
                source_field="current_process_state",
                observation_timestamp=NOW,
                availability_status=ProcessEvidenceAvailability.AVAILABLE,
                normalized_value=state.value,
                timeframe="1m",
            ),
        )
    return ProcessEvidence(
        episode_id="episode-test-1",
        runtime_event_id="runtime-evt-1",
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="1m",
        observation_timestamp=NOW,
        current_process_state=state,
        process_direction=(
            ProcessDirection.UNKNOWN
            if state is ProcessState.UNKNOWN
            else ProcessDirection.UP
        ),
        previous_process_state=previous_state,
        detected_transition=transition,
        process_summary="Canonical Process Classification result.",
        supporting_evidence=supporting,
        contradicting_evidence=(),
        neutral_evidence=(),
        available_evidence_families=(
            frozenset((ProcessEvidenceFamily.PRICE,))
            if supporting
            else frozenset()
        ),
        missing_evidence_families=missing_families,
        insufficiency_reasons=insufficiency_reasons,
        evidence_strength=(
            EvidenceStrength.UNKNOWN
            if state is ProcessState.UNKNOWN
            else EvidenceStrength.MODERATE
        ),
        uncertainty_level=(
            UncertaintyLevel.UNKNOWN
            if state is ProcessState.UNKNOWN
            else UncertaintyLevel.LOW
        ),
    )


def make_agent_state(state: AgentStateType = AgentStateType.UNKNOWN) -> AgentState:
    return AgentState(
        event_id="event-1",
        current_state=state,
        process_direction=ProcessDirection.UNKNOWN,
        previous_state=AgentStateType.UNKNOWN,
        state_transition_status=StateTransitionStatus.UNKNOWN,
        transition_reason="unit test",
        supporting_evidence=(),
        blocking_evidence=(),
        state_confidence_context=ConfidenceLevel.UNKNOWN,
    )


def make_evidence_summary(
    *,
    structural: bool = False,
    market: bool = False,
    temporal: bool = False,
    total_score: float = 0.5,
) -> EvidenceSummary:
    evidence_count = sum((structural, market, temporal))
    return EvidenceSummary(
        structural_score=total_score if structural else None,
        market_score=total_score if market else None,
        temporal_score=total_score if temporal else None,
        total_score=total_score if evidence_count else 0.0,
        evidence_count=evidence_count,
        strongest_evidence_type=None,
        weakest_evidence_type=None,
        has_structural_evidence=structural,
        has_market_evidence=market,
        has_temporal_evidence=temporal,
    )


def make_hypothesis_snapshot(
    *,
    created_at: datetime = NOW,
    state: AgentStateType = AgentStateType.UNKNOWN,
    confidence: int = 0,
    evidence_score: float = 0.5,
    structural: bool = True,
    market: bool = False,
    temporal: bool = False,
) -> HypothesisSnapshot:
    return build_hypothesis_snapshot(
        agent_state=make_agent_state(state),
        confidence=confidence,
        confidence_trend="UNKNOWN",
        evidence_summary=make_evidence_summary(
            structural=structural,
            market=market,
            temporal=temporal,
            total_score=evidence_score,
        ),
        created_at=created_at,
    )


class HypothesisEngineTests(unittest.TestCase):
    def test_empty_summary_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=0,
            confidence_trend="UNKNOWN",
            evidence_summary=make_evidence_summary(),
            created_at=NOW,
        )

        self.assertIsInstance(snapshot, HypothesisSnapshot)
        self.assertEqual(snapshot.state, "UNKNOWN")
        self.assertEqual(snapshot.confidence, 0)
        self.assertEqual(snapshot.confidence_trend, "UNKNOWN")
        self.assertEqual(snapshot.created_at, NOW)
        self.assertEqual(snapshot.label, "unknown")

    def test_structural_only_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(AgentStateType.IGNITION),
            confidence=50,
            confidence_trend="STABLE",
            evidence_summary=make_evidence_summary(structural=True),
            created_at=NOW,
        )

        self.assertEqual(snapshot.state, "IGNITION")
        self.assertEqual(snapshot.label, "structural_only")

    def test_market_only_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=50,
            confidence_trend="STABLE",
            evidence_summary=make_evidence_summary(market=True),
            created_at=NOW,
        )

        self.assertEqual(snapshot.label, "market_only")

    def test_temporal_only_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=50,
            confidence_trend="IMPROVING",
            evidence_summary=make_evidence_summary(temporal=True),
            created_at=NOW,
        )

        self.assertEqual(snapshot.label, "temporal_only")

    def test_mixed_evidence_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=50,
            confidence_trend="STABLE",
            evidence_summary=make_evidence_summary(structural=True, market=True),
            created_at=NOW,
        )

        self.assertEqual(snapshot.label, "mixed_evidence")

    def test_low_evidence_hypothesis_snapshot(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=0,
            confidence_trend="WEAKENING",
            evidence_summary=make_evidence_summary(structural=True, total_score=0.0),
            created_at=NOW,
        )

        self.assertEqual(snapshot.label, "low_evidence")

    def test_hypothesis_snapshot_label_selection_is_deterministic(self) -> None:
        summary = make_evidence_summary(
            structural=True,
            market=True,
            temporal=True,
            total_score=0.5,
        )

        first = build_hypothesis_snapshot(
            agent_state=make_agent_state(AgentStateType.IGNITION),
            confidence=50,
            confidence_trend="UNKNOWN",
            evidence_summary=summary,
            created_at=NOW,
        )
        second = build_hypothesis_snapshot(
            agent_state=make_agent_state(AgentStateType.IGNITION),
            confidence=50,
            confidence_trend="UNKNOWN",
            evidence_summary=summary,
            created_at=NOW,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.label, "mixed_evidence")

    def test_hypothesis_snapshot_accepts_missing_summary(self) -> None:
        snapshot = build_hypothesis_snapshot(
            agent_state=make_agent_state(),
            confidence=0,
            confidence_trend="UNKNOWN",
            evidence_summary=None,
            created_at=NOW,
        )

        self.assertIsNone(snapshot.evidence_summary)
        self.assertEqual(snapshot.label, "unknown")

    def test_empty_hypothesis_history(self) -> None:
        history = HypothesisHistory()

        self.assertIsNone(history.latest())
        self.assertIsNone(history.previous())
        self.assertEqual(history.size(), 0)

    def test_hypothesis_history_append(self) -> None:
        history = HypothesisHistory()
        snapshot = make_hypothesis_snapshot()

        history.append(snapshot)

        self.assertEqual(history.size(), 1)

    def test_hypothesis_history_latest(self) -> None:
        history = HypothesisHistory()
        first = make_hypothesis_snapshot(confidence=10)
        second = make_hypothesis_snapshot(confidence=20)

        history.append(first)
        history.append(second)

        self.assertEqual(history.latest(), second)

    def test_hypothesis_history_previous(self) -> None:
        history = HypothesisHistory()
        first = make_hypothesis_snapshot(confidence=10)
        second = make_hypothesis_snapshot(confidence=20)

        history.append(first)
        history.append(second)

        self.assertEqual(history.previous(), first)

    def test_hypothesis_history_limit_discards_oldest_snapshots(self) -> None:
        history = HypothesisHistory(max_length=2)
        first = make_hypothesis_snapshot(confidence=10)
        second = make_hypothesis_snapshot(confidence=20)
        third = make_hypothesis_snapshot(confidence=30)

        history.append(first)
        history.append(second)
        history.append(third)

        self.assertEqual(history.size(), 2)
        self.assertEqual(history.previous(), second)
        self.assertEqual(history.latest(), third)

    def test_hypothesis_history_ordering_is_deterministic(self) -> None:
        history = HypothesisHistory(max_length=3)
        first = make_hypothesis_snapshot(confidence=10)
        second = make_hypothesis_snapshot(confidence=20)
        third = make_hypothesis_snapshot(confidence=30)

        for snapshot in (first, second, third):
            history.append(snapshot)

        self.assertEqual(history.previous(), second)
        self.assertEqual(history.latest(), third)

    def test_hypothesis_history_clear(self) -> None:
        history = HypothesisHistory()
        history.append(make_hypothesis_snapshot())

        history.clear()

        self.assertEqual(history.size(), 0)
        self.assertIsNone(history.latest())
        self.assertIsNone(history.previous())

    def test_empty_history_trend_summary(self) -> None:
        summary = HistoryTrendAnalyzer.analyze(HypothesisHistory())

        self.assertIsInstance(summary, HistoryTrendSummary)
        self.assertEqual(summary.confidence_trend, TREND_UNKNOWN)
        self.assertEqual(summary.evidence_score_trend, TREND_UNKNOWN)
        self.assertEqual(summary.label_stability, TREND_UNKNOWN)
        self.assertEqual(summary.sample_size, 0)

    def test_single_snapshot_history_trend_summary(self) -> None:
        history = HypothesisHistory()
        history.append(make_hypothesis_snapshot(confidence=10))

        summary = HistoryTrendAnalyzer.analyze(history)

        self.assertEqual(summary.confidence_trend, TREND_UNKNOWN)
        self.assertEqual(summary.evidence_score_trend, TREND_UNKNOWN)
        self.assertEqual(summary.label_stability, TREND_UNKNOWN)
        self.assertEqual(summary.sample_size, 1)

    def test_improving_history_trend_summary(self) -> None:
        history = HypothesisHistory()
        history.append(make_hypothesis_snapshot(confidence=10, evidence_score=0.2))
        history.append(make_hypothesis_snapshot(confidence=30, evidence_score=0.8))

        summary = HistoryTrendAnalyzer.analyze(history)

        self.assertEqual(summary.confidence_trend, TREND_IMPROVING)
        self.assertEqual(summary.evidence_score_trend, TREND_IMPROVING)
        self.assertEqual(summary.label_stability, TREND_STABLE)
        self.assertEqual(summary.sample_size, 2)

    def test_weakening_history_trend_summary(self) -> None:
        history = HypothesisHistory()
        history.append(make_hypothesis_snapshot(confidence=30, evidence_score=0.8))
        history.append(make_hypothesis_snapshot(confidence=10, evidence_score=0.2))

        summary = HistoryTrendAnalyzer.analyze(history)

        self.assertEqual(summary.confidence_trend, TREND_WEAKENING)
        self.assertEqual(summary.evidence_score_trend, TREND_WEAKENING)

    def test_stable_history_trend_summary(self) -> None:
        history = HypothesisHistory()
        history.append(make_hypothesis_snapshot(confidence=20, evidence_score=0.5))
        history.append(make_hypothesis_snapshot(confidence=20, evidence_score=0.505))

        summary = HistoryTrendAnalyzer.analyze(history)

        self.assertEqual(summary.confidence_trend, TREND_STABLE)
        self.assertEqual(summary.evidence_score_trend, TREND_STABLE)

    def test_history_trend_label_stability(self) -> None:
        history = HypothesisHistory()
        history.append(make_hypothesis_snapshot(structural=True))
        history.append(
            make_hypothesis_snapshot(
                structural=False,
                market=True,
            )
        )

        summary = HistoryTrendAnalyzer.analyze(history)

        self.assertEqual(summary.label_stability, TREND_WEAKENING)

    def test_history_trend_output_is_deterministic(self) -> None:
        history = HypothesisHistory()
        history.append(make_hypothesis_snapshot(confidence=10, evidence_score=0.2))
        history.append(make_hypothesis_snapshot(confidence=30, evidence_score=0.8))

        first = HistoryTrendAnalyzer.analyze(history)
        second = HistoryTrendAnalyzer.analyze(history)

        self.assertEqual(first, second)

    def test_empty_hypothesis_evaluation(self) -> None:
        evaluation = HypothesisEvaluator.evaluate(
            snapshot=None,
            history_trend_summary=None,
        )

        self.assertIsInstance(evaluation, HypothesisEvaluation)
        self.assertEqual(evaluation.status, EVALUATION_UNKNOWN)
        self.assertEqual(evaluation.reason, "missing_snapshot_or_history_trend")
        self.assertIsNone(evaluation.created_at)

    def test_improving_hypothesis_evaluation(self) -> None:
        snapshot = make_hypothesis_snapshot(created_at=NOW)
        trend = HistoryTrendSummary(
            confidence_trend=TREND_IMPROVING,
            evidence_score_trend=TREND_IMPROVING,
            label_stability=TREND_STABLE,
            sample_size=2,
        )

        evaluation = HypothesisEvaluator.evaluate(
            snapshot=snapshot,
            history_trend_summary=trend,
        )

        self.assertEqual(evaluation.status, EVALUATION_REINFORCED)
        self.assertEqual(evaluation.reason, "confidence_and_evidence_trends_improving")
        self.assertEqual(evaluation.created_at, NOW)

    def test_stable_hypothesis_evaluation(self) -> None:
        snapshot = make_hypothesis_snapshot()
        trend = HistoryTrendSummary(
            confidence_trend=TREND_STABLE,
            evidence_score_trend=TREND_STABLE,
            label_stability=TREND_STABLE,
            sample_size=2,
        )

        evaluation = HypothesisEvaluator.evaluate(
            snapshot=snapshot,
            history_trend_summary=trend,
        )

        self.assertEqual(evaluation.status, EVALUATION_NEUTRAL)
        self.assertEqual(evaluation.reason, "confidence_and_evidence_trends_stable")

    def test_weakening_hypothesis_evaluation(self) -> None:
        snapshot = make_hypothesis_snapshot()
        trend = HistoryTrendSummary(
            confidence_trend=TREND_STABLE,
            evidence_score_trend=TREND_WEAKENING,
            label_stability=TREND_STABLE,
            sample_size=2,
        )

        evaluation = HypothesisEvaluator.evaluate(
            snapshot=snapshot,
            history_trend_summary=trend,
        )

        self.assertEqual(evaluation.status, EVALUATION_WEAKENING)
        self.assertEqual(evaluation.reason, "confidence_or_evidence_trend_weakening")

    def test_hypothesis_evaluation_output_is_deterministic(self) -> None:
        snapshot = make_hypothesis_snapshot()
        trend = HistoryTrendSummary(
            confidence_trend=TREND_IMPROVING,
            evidence_score_trend=TREND_IMPROVING,
            label_stability=TREND_STABLE,
            sample_size=2,
        )

        first = HypothesisEvaluator.evaluate(
            snapshot=snapshot,
            history_trend_summary=trend,
        )
        second = HypothesisEvaluator.evaluate(
            snapshot=snapshot,
            history_trend_summary=trend,
        )

        self.assertEqual(first, second)

    def test_hypothesis_reads_structural_and_market_efficiency_evidence(self) -> None:
        event = make_event_with_evidence()

        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
            **CANONICAL_HYPOTHESIS_INPUT,
        )

        self.assertEqual(hypothesis.event_id, event.structural_evidence.event_id)
        self.assertIn(
            "insufficient_ohlcv_sequence",
            tuple(item.evidence_key for item in hypothesis.supporting_evidence),
        )
        self.assertIn(
            "volume_available",
            tuple(item.evidence_key for item in hypothesis.supporting_evidence),
        )

    def test_hypothesis_produces_valid_hypothesis_package(self) -> None:
        event = make_event_with_evidence()

        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
            **CANONICAL_HYPOTHESIS_INPUT,
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
        self.assertEqual(hypothesis.explanation_confidence_score, 25)
        self.assertEqual(hypothesis.uncertainty, UncertaintyLevel.HIGH)
        self.assertIs(
            hypothesis.semantic_code,
            HypothesisSemanticCode.UNRESOLVED,
        )
        self.assertIn("does not decide official state", hypothesis.reasoning_notes)

    def test_operational_hypothesis_produces_approved_semantic_codes(self) -> None:
        event = make_event_with_evidence()
        cases = (
            (
                ProcessState.UNKNOWN,
                ProcessTransition.INITIAL,
                None,
                HypothesisSemanticCode.UNRESOLVED,
            ),
            (
                ProcessState.CONTINUATION_ALIVE,
                ProcessTransition.CHANGED,
                ProcessState.UNKNOWN,
                HypothesisSemanticCode.CONTINUATION_EXPLANATION,
            ),
            (
                ProcessState.WEAKENING,
                ProcessTransition.CHANGED,
                ProcessState.CONTINUATION_ALIVE,
                HypothesisSemanticCode.WEAKENING_EXPLANATION,
            ),
            (
                ProcessState.CONTINUATION_ALIVE,
                ProcessTransition.RECOVERED,
                ProcessState.WEAKENING,
                HypothesisSemanticCode.RECOVERY_EXPLANATION,
            ),
        )
        for state, transition, previous_state, expected in cases:
            with self.subTest(state=state, transition=transition):
                hypothesis = build_operational_hypothesis_package(
                    event.market_snapshot,
                    event.structural_evidence,
                    event.market_efficiency_evidence,
                    episode_id="episode-test-1",
                    runtime_event_id="runtime-evt-1",
                    process_evidence=make_process_evidence(
                        state,
                        transition=transition,
                        previous_state=previous_state,
                    ),
                    previous=None,
                    new_hypothesis_id=lambda: "generated-hypothesis",
                )
                self.assertIs(hypothesis.semantic_code, expected)
                self.assertEqual(
                    hypothesis.to_dict()["semantic_code"],
                    expected.value,
                )

    def test_hypothesis_writes_only_hypothesis_package_not_confidence_assessment(
        self,
    ) -> None:
        event = make_event_with_evidence()

        updated = add_hypothesis_package(event, **CANONICAL_HYPOTHESIS_INPUT)

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

        updated = add_hypothesis_package(event, **CANONICAL_HYPOTHESIS_INPUT)

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
            add_hypothesis_package(event, **CANONICAL_HYPOTHESIS_INPUT)

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
            add_hypothesis_package(event, **CANONICAL_HYPOTHESIS_INPUT)

    def test_canonical_hypothesis_serializes_identity_lifecycle_and_evidence(self) -> None:
        event = make_event_with_evidence()
        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
            **CANONICAL_HYPOTHESIS_INPUT,
        )

        serialized = hypothesis.to_dict()

        self.assertEqual(serialized["episode_id"], "episode-test-1")
        self.assertEqual(serialized["hypothesis_id"], "hypothesis-test-1")
        self.assertEqual(serialized["lifecycle_status"], "created")
        self.assertEqual(serialized["explanation_confidence_score"], 25)
        self.assertEqual(serialized["schema_version"], "1.0")
        self.assertEqual(json.loads(json.dumps(serialized)), serialized)
        self.assertIsInstance(
            hypothesis.supporting_evidence[0], HypothesisEvidenceReference
        )
        self.assertEqual(
            serialized["supporting_evidence"][0]["source_event_id"],
            event.event_id,
        )

    def test_canonical_hypothesis_enforces_lifecycle_identity_invariants(self) -> None:
        event = make_event_with_evidence()

        with self.assertRaisesRegex(ValueError, "retain the hypothesis ID"):
            build_hypothesis_package(
                event.structural_evidence,
                event.market_efficiency_evidence,
                episode_id="episode-test-1",
                hypothesis_id="hypothesis-test-1",
                explanation_confidence_score=25,
                lifecycle_status=HypothesisLifecycleStatus.UPDATED,
                hypothesis_change_reason="Explanation was refined.",
                previous_hypothesis_id="different-hypothesis",
                previous_runtime_event_id="runtime-evt-0",
            )

    def test_canonical_hypothesis_replaced_requires_new_identity(self) -> None:
        event = make_event_with_evidence()

        with self.assertRaisesRegex(ValueError, "must receive a new hypothesis ID"):
            build_hypothesis_package(
                event.structural_evidence,
                event.market_efficiency_evidence,
                episode_id="episode-test-1",
                hypothesis_id="hypothesis-test-1",
                explanation_confidence_score=25,
                lifecycle_status=HypothesisLifecycleStatus.REPLACED,
                hypothesis_change_reason="The prior explanation was invalidated.",
                previous_hypothesis_id="hypothesis-test-1",
                previous_runtime_event_id="runtime-evt-0",
            )

    def test_canonical_hypothesis_rejects_empty_schema_version(self) -> None:
        event = make_event_with_evidence()
        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
            **CANONICAL_HYPOTHESIS_INPUT,
        )

        with self.assertRaisesRegex(ValueError, "schema_version"):
            replace(hypothesis, schema_version="")

    def test_canonical_hypothesis_validates_score_range(self) -> None:
        hypothesis = self._canonical_hypothesis()

        for score in (-1, 101):
            with self.subTest(score=score):
                with self.assertRaisesRegex(ValueError, "between 0 and 100"):
                    replace(hypothesis, explanation_confidence_score=score)

    def test_canonical_hypothesis_rejects_non_finite_scores(self) -> None:
        hypothesis = self._canonical_hypothesis()

        for score in (nan, inf, -inf):
            with self.subTest(score=score):
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    replace(hypothesis, explanation_confidence_score=score)

    def test_canonical_hypothesis_requires_integer_score(self) -> None:
        hypothesis = self._canonical_hypothesis()

        for score in (True, "25", 25.0):
            with self.subTest(score=score):
                with self.assertRaisesRegex(
                    ValueError,
                    "must be numeric|must be an integer",
                ):
                    replace(hypothesis, explanation_confidence_score=score)

    def test_canonical_hypothesis_enforces_score_context_mapping(self) -> None:
        hypothesis = self._canonical_hypothesis()
        cases = (
            (0, ConfidenceLevel.UNKNOWN),
            (1, ConfidenceLevel.LOW),
            (49, ConfidenceLevel.LOW),
            (50, ConfidenceLevel.MEDIUM),
            (79, ConfidenceLevel.MEDIUM),
            (80, ConfidenceLevel.HIGH),
            (100, ConfidenceLevel.HIGH),
        )

        for score, context in cases:
            with self.subTest(score=score, context=context):
                updated = replace(
                    hypothesis,
                    explanation_confidence_score=score,
                    current_hypothesis_confidence_context=context,
                )
                self.assertEqual(updated.explanation_confidence_score, score)

        with self.assertRaisesRegex(ValueError, "must match"):
            replace(
                hypothesis,
                explanation_confidence_score=80,
                current_hypothesis_confidence_context=ConfidenceLevel.MEDIUM,
            )

    def _canonical_hypothesis(self) -> HypothesisPackage:
        event = make_event_with_evidence()
        return build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
            **CANONICAL_HYPOTHESIS_INPUT,
        )

    def test_canonical_hypothesis_requires_explicit_nonempty_identity(self) -> None:
        event = make_event_with_evidence()
        hypothesis = build_hypothesis_package(
            event.structural_evidence,
            event.market_efficiency_evidence,
            **CANONICAL_HYPOTHESIS_INPUT,
        )

        for field_name in ("episode_id", "hypothesis_id"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    replace(hypothesis, **{field_name: ""})

    def test_canonical_hypothesis_rejects_misaligned_evidence_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "align with the Runtime event ID"):
            HypothesisPackage(
                event_id="runtime-evt-1",
                episode_id="episode-test-1",
                hypothesis_id="hypothesis-test-1",
                hypothesis_label="current_condition_explanation",
                hypothesis_summary="Current explanation.",
                supporting_evidence=(
                    HypothesisEvidenceReference(
                        source_event_id="runtime-evt-other",
                        source_section="structural_evidence",
                        evidence_key="higher_high",
                        description="Structure reported a higher high.",
                    ),
                ),
                contradicting_evidence=(),
                explanation_confidence_score=50,
                current_hypothesis_confidence_context=ConfidenceLevel.MEDIUM,
                reasoning_notes="Canonical contract test.",
                uncertainty=UncertaintyLevel.MEDIUM,
                semantic_code=HypothesisSemanticCode.UNRESOLVED,
                lifecycle_status=HypothesisLifecycleStatus.CREATED,
                previous_hypothesis_id=None,
                previous_runtime_event_id=None,
                hypothesis_change_reason="Initial explanation.",
            )

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
