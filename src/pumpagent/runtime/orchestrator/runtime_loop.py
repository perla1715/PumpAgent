"""Agent Runtime Loop MVP.

The runtime loop coordinates one deterministic reasoning cycle for a market
snapshot. It does not make trading decisions, persist data, or emit side
effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable
from typing import Any

from pumpagent.runtime.domain import (
    AgentState,
    ConfidenceAssessment,
    DecisionAssessment,
    HypothesisPackage,
    MarketEfficiencyEvidence,
    MarketSnapshot,
    ObservationPackage,
    ProcessQualityAssessment,
    ProcessQualityAssessmentReference,
    RuntimeEvent,
    HealthyBaselineDesignation,
    HealthyBaselineReference,
    ScenarioProbability,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import AgentStateType, RuntimeStatus
from pumpagent.runtime.domain.process_evidence import ProcessEvidence, ProcessState, ProcessTransition
from pumpagent.runtime.modules.evidence import (
    Evidence,
    EvidenceSummary,
    aggregate_evidence_score,
    build_evidence_summary,
    collect_evidence,
)
from pumpagent.runtime.modules.agent_state import build_agent_state_from_hypothesis_package
from pumpagent.runtime.modules.hypothesis import (
    HistoryTrendAnalyzer,
    HistoryTrendSummary,
    HypothesisEvaluation,
    HypothesisEvaluator,
    HypothesisHistory,
    HypothesisSnapshot,
    build_operational_hypothesis_package,
    generate_hypothesis_id,
    build_hypothesis_snapshot,
)
from pumpagent.runtime.modules.market_efficiency import build_market_efficiency_evidence
from pumpagent.runtime.modules.market_eligibility import (
    MarketEligibilityFilter,
    MarketEligibilityResult,
)
from pumpagent.runtime.modules.confidence import build_confidence_assessment
from pumpagent.runtime.modules.decision import (
    DecisionEngineInput,
    build_decision_assessment,
)
from pumpagent.runtime.modules.perception import build_observation_package
from pumpagent.runtime.modules.process_classification import (
    ProcessClassificationInput,
    classify_market_process,
)
from pumpagent.runtime.modules.process_quality import (
    HealthyBaselineDesignationPolicyInput,
    ProcessQualityAssessmentInput,
    build_process_quality_assessment,
    designate_healthy_baseline,
)
from pumpagent.runtime.modules.scenario_probability import build_scenario_probability
from pumpagent.runtime.modules.structure import build_structural_evidence
from pumpagent.runtime.modules.temporal_confidence import (
    CONFIDENCE_TREND_UNKNOWN,
    TemporalConfidenceManager,
    TemporalConfidenceState,
)
from pumpagent.runtime.modules.watchlist import WatchlistManager
from pumpagent.runtime.orchestrator.diagnostic_report import (
    DiagnosticRuntimeReport,
    build_diagnostic_runtime_report,
)
from pumpagent.runtime.orchestrator.cycle_projection import (
    AgentCycleResult,
    project_agent_cycle_result,
)


@dataclass(frozen=True)
class _ContinuitySnapshot:
    watchlist_entries: dict[tuple[str, str, str], object]
    temporal_states: dict[tuple[str, str, str], object]
    hypothesis_snapshots: tuple[HypothesisSnapshot, ...]


class RuntimeOrchestrator:
    """Coordinate one side-effect-free agent reasoning cycle."""

    def __init__(
        self,
        watchlist: WatchlistManager | None = None,
        temporal_confidence: TemporalConfidenceManager | None = None,
        hypothesis_history: HypothesisHistory | None = None,
        market_eligibility_filter: MarketEligibilityFilter | None = None,
        hypothesis_id_generator: Callable[[], str] = generate_hypothesis_id,
    ) -> None:
        self.watchlist = watchlist or WatchlistManager()
        self.temporal_confidence = temporal_confidence or TemporalConfidenceManager()
        self.hypothesis_history = hypothesis_history or HypothesisHistory()
        self.market_eligibility_filter = market_eligibility_filter or MarketEligibilityFilter()
        self.hypothesis_id_generator = hypothesis_id_generator
        self._observation_episode_id: str | None = None
        self._pending_continuity: dict[str, _ContinuitySnapshot] = {}

    def bind_observation_episode(self, episode_id: str) -> None:
        """Keep mutable analytical helpers isolated to one Observation Episode.

        The legacy Runtime helpers are process-local and are not Episode-aware.
        Rebinding therefore replaces them when an orchestration component moves
        to another Episode.  This does not alter any analytical rule.
        """
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError("episode_id must be a non-empty string.")
        if self._observation_episode_id == episode_id:
            return
        self.watchlist = WatchlistManager()
        self.temporal_confidence = TemporalConfidenceManager()
        self.hypothesis_history = HypothesisHistory()
        self._observation_episode_id = episode_id
        self._pending_continuity.clear()

    def commit_runtime_continuity(self, runtime_event_id: str) -> None:
        """Commit already-staged continuity after external cycle completion."""

        self._pending_continuity.pop(runtime_event_id, None)

    def rollback_runtime_continuity(self, runtime_event_id: str) -> None:
        """Restore continuity staged by a completed but uncommitted cycle."""

        snapshot = self._pending_continuity.pop(runtime_event_id, None)
        if snapshot is not None:
            self._restore_continuity(snapshot)

    def _snapshot_continuity(self) -> _ContinuitySnapshot:
        return _ContinuitySnapshot(
            watchlist_entries=dict(self.watchlist._entries),  # noqa: SLF001
            temporal_states=dict(self.temporal_confidence._states),  # noqa: SLF001
            hypothesis_snapshots=tuple(
                self.hypothesis_history._snapshots  # noqa: SLF001
            ),
        )

    def _restore_continuity(self, snapshot: _ContinuitySnapshot) -> None:
        self.watchlist._entries = dict(snapshot.watchlist_entries)  # noqa: SLF001
        self.temporal_confidence._states = dict(  # noqa: SLF001
            snapshot.temporal_states
        )
        self.hypothesis_history._snapshots = list(  # noqa: SLF001
            snapshot.hypothesis_snapshots
        )

    def process_market_update(
        self,
        snapshot: MarketSnapshot,
        *,
        previous_state: str = "UNKNOWN",
        previous_hypothesis: HypothesisPackage | None = None,
        episode_id: str | None = None,
        previous_process_evidence: ProcessEvidence | None = None,
        previous_process_quality_assessments: tuple[
            ProcessQualityAssessment, ...
        ] = (),
        healthy_baseline_reference: HealthyBaselineReference | None = None,
        healthy_baseline_designation: HealthyBaselineDesignation | None = None,
        previous_scenario_probability: ScenarioProbability | None = None,
        classification_timestamp: datetime | None = None,
        runtime_event_id: str | None = None,
    ) -> RuntimeEvent:
        """Return the one canonical Runtime aggregate for every cycle outcome."""

        # A previous direct caller had no external completion boundary; its
        # successfully returned event therefore becomes committed before the
        # next cycle. Observation Lifecycle commits or rolls back explicitly.
        self._pending_continuity.clear()
        continuity_before = self._snapshot_continuity()
        try:
            event = self._process_market_update(
                snapshot,
                previous_state=previous_state,
                previous_hypothesis=previous_hypothesis,
                episode_id=episode_id,
                previous_process_evidence=previous_process_evidence,
                previous_process_quality_assessments=previous_process_quality_assessments,
                healthy_baseline_reference=healthy_baseline_reference,
                healthy_baseline_designation=healthy_baseline_designation,
                previous_scenario_probability=previous_scenario_probability,
                classification_timestamp=classification_timestamp,
                runtime_event_id=runtime_event_id,
            )
            if event.runtime_status is RuntimeStatus.COMPLETED:
                self._pending_continuity[event.event_id] = continuity_before
            else:
                self._restore_continuity(continuity_before)
            return event
        except Exception as exc:
            self._restore_continuity(continuity_before)
            return _initial_runtime_event(
                snapshot,
                episode_id=episode_id,
                runtime_event_id=runtime_event_id,
                runtime_status=RuntimeStatus.FAILED,
                errors_or_warnings=(f"{type(exc).__name__}: {exc}",),
            )

    def _process_market_update(
        self,
        snapshot: MarketSnapshot,
        *,
        previous_state: str = "UNKNOWN",
        previous_hypothesis: HypothesisPackage | None = None,
        episode_id: str | None = None,
        previous_process_evidence: ProcessEvidence | None = None,
        previous_process_quality_assessments: tuple[
            ProcessQualityAssessment, ...
        ] = (),
        healthy_baseline_reference: HealthyBaselineReference | None = None,
        healthy_baseline_designation: HealthyBaselineDesignation | None = None,
        previous_scenario_probability: ScenarioProbability | None = None,
        classification_timestamp: datetime | None = None,
        runtime_event_id: str | None = None,
    ) -> RuntimeEvent:
        eligibility = self.market_eligibility_filter.evaluate(snapshot)
        if not eligibility.eligible:
            return _initial_runtime_event(
                snapshot,
                episode_id=episode_id,
                runtime_event_id=runtime_event_id,
                runtime_status=RuntimeStatus.REJECTED,
                errors_or_warnings=(
                    f"market_eligibility:{eligibility.reason.value}",
                ),
                compatibility_context={"eligibility_result": eligibility},
            )
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError(
                "A Lifecycle-owned episode_id is required for canonical hypothesis production."
            )
        if (healthy_baseline_reference is None) != (
            healthy_baseline_designation is None
        ):
            raise ValueError(
                "Healthy Baseline reference and designation must be supplied together."
            )
        if (
            healthy_baseline_designation is not None
            and healthy_baseline_designation.to_reference()
            != healthy_baseline_reference
        ):
            raise ValueError(
                "Healthy Baseline reference must match its canonical designation."
            )

        analytical_timestamp = _canonical_analytical_timestamp(
            snapshot,
            classification_timestamp,
        )
        event_id = runtime_event_id or _cycle_event_id(snapshot)
        event = _initial_runtime_event(
            snapshot,
            episode_id=episode_id,
            runtime_event_id=event_id,
            runtime_status=RuntimeStatus.IN_PROGRESS,
        )
        observations = build_observation_package(snapshot, runtime_event_id=event_id)
        event = event.with_sections(observation_package=observations)
        structure_result = build_structural_evidence(observations, runtime_event_id=event_id)
        event = event.with_sections(structural_evidence=structure_result)
        market_result = build_market_efficiency_evidence(observations, runtime_event_id=event_id)
        event = event.with_sections(market_efficiency_evidence=market_result)
        process_input = prepare_process_classification_input(
            episode_id=episode_id, runtime_event_id=event_id,
            observations=observations, structural_evidence=structure_result,
            market_efficiency_evidence=market_result,
            previous_process_evidence=previous_process_evidence,
            exchange=snapshot.exchange, symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            classification_timestamp=analytical_timestamp,
        )
        process_evidence = classify_market_process(process_input)
        event = event.with_sections(process_evidence=process_evidence)
        process_quality_assessment = build_process_quality_assessment(
            ProcessQualityAssessmentInput(
                process_evidence=process_evidence,
                structural_evidence=structure_result,
                market_efficiency_evidence=market_result,
                data_quality_status=snapshot.data_quality_status,
                previous_assessments=previous_process_quality_assessments,
                healthy_baseline=healthy_baseline_reference,
            )
        )
        process_quality_history = (
            previous_process_quality_assessments + (process_quality_assessment,)
        )
        previous_process_quality_reference = (
            previous_process_quality_assessments[-1].to_reference()
            if previous_process_quality_assessments
            else None
        )
        event = event.with_sections(
            process_quality_assessment=process_quality_assessment,
            previous_process_quality_reference=previous_process_quality_reference,
            process_quality_history=process_quality_history,
        )
        selected_baseline_designation = designate_healthy_baseline(
            HealthyBaselineDesignationPolicyInput(
                current_assessment=process_quality_assessment,
                process_evidence=process_evidence,
                data_quality_status=snapshot.data_quality_status,
                previous_assessments=previous_process_quality_assessments,
                existing_designation=healthy_baseline_designation,
            )
        )
        event = event.with_sections(
            healthy_baseline_reference=(
                selected_baseline_designation.to_reference()
                if selected_baseline_designation is not None
                else None
            ),
            healthy_baseline_designation=selected_baseline_designation,
        )
        hypothesis_input = _combine_hypothesis_input(
            snapshot,
            structure_result=structure_result,
            market_result=market_result,
        )
        hypothesis = build_operational_hypothesis_package(
            hypothesis_input,
            structure_result,
            market_result,
            episode_id=episode_id,
            runtime_event_id=event_id,
            process_evidence=process_evidence,
            previous=previous_hypothesis,
            new_hypothesis_id=self.hypothesis_id_generator,
        )
        event = event.with_sections(hypothesis_package=hypothesis)
        # Temporary compatibility projection for legacy diagnostic consumers.
        # Final analytical-chain reliability is confidence_assessment below.
        confidence = hypothesis.explanation_confidence_score
        evidence = tuple(collect_evidence(hypothesis_input))
        agent_state = build_agent_state_from_hypothesis_package(
            hypothesis,
            event_id=event_id,
            previous_state=_agent_state_type_from_value(previous_state),
            canonical_process_state=process_evidence.current_process_state.name,
            canonical_process_direction=process_evidence.process_direction,
            supporting_evidence=tuple(item.value for item in evidence if item.positive),
            contradicting_evidence=tuple(item.value for item in evidence if not item.positive),
        )
        event = event.with_sections(agent_state=agent_state)
        scenario_probability = build_scenario_probability(
            hypothesis,
            agent_state,
            process_evidence,
            process_quality_assessment,
            healthy_baseline_reference=healthy_baseline_reference,
            previous_scenario_probability=previous_scenario_probability,
            runtime_event_id=event_id,
            active_episode_id=episode_id,
        )
        event = event.with_sections(scenario_probability=scenario_probability)
        confidence_assessment = build_confidence_assessment(
            hypothesis,
            agent_state,
            scenario_probability,
            runtime_event_id=event_id,
            active_episode_id=episode_id,
            data_quality_impact=(
                "market_snapshot_data_quality:"
                f"{snapshot.data_quality_status.value}"
            ),
        )
        event = event.with_sections(confidence_assessment=confidence_assessment)
        decision_assessment = build_decision_assessment(
            DecisionEngineInput(
                process_quality_assessment=process_quality_assessment,
                process_evidence=process_evidence,
                hypothesis=hypothesis,
                scenario_probability=scenario_probability,
                confidence_assessment=confidence_assessment,
                healthy_baseline_reference=healthy_baseline_reference,
            )
        )
        event = event.with_sections(decision_assessment=decision_assessment)
        previous_state_name = agent_state.previous_state.name
        new_state_name = agent_state.current_state.name
        watchlist_action, watchlist_observation_count = self.watchlist.track_cycle(
            symbol=snapshot.symbol,
            exchange=snapshot.exchange,
            timeframe=snapshot.timeframe,
            timestamp=snapshot.timestamp,
            agent_state=agent_state,
            hypothesis=hypothesis,
            confidence=confidence,
            event_id=event_id,
        )
        temporal_confidence = _update_temporal_confidence(
            self.temporal_confidence,
            self.watchlist.get(
                symbol=snapshot.symbol,
                exchange=snapshot.exchange,
                timeframe=snapshot.timeframe,
            ),
        )
        aggregated_evidence_score = aggregate_evidence_score(
            structural_evidence=structure_result,
            market_evidence=market_result,
            temporal_evidence=temporal_confidence,
        )
        evidence_summary = build_evidence_summary(
            aggregated_score=aggregated_evidence_score,
            structural_evidence=structure_result,
            market_evidence=market_result,
            temporal_evidence=temporal_confidence,
        )
        confidence_trend = (
            temporal_confidence.trend
            if temporal_confidence is not None
            else CONFIDENCE_TREND_UNKNOWN
        )
        confidence_delta = (
            temporal_confidence.confidence_delta
            if temporal_confidence is not None
            else None
        )
        hypothesis_snapshot = build_hypothesis_snapshot(
            agent_state=agent_state,
            confidence=confidence,
            confidence_trend=confidence_trend,
            evidence_summary=evidence_summary,
            created_at=snapshot.timestamp,
        )
        self.hypothesis_history.append(hypothesis_snapshot)
        hypothesis_history_size = self.hypothesis_history.size()
        history_trend_summary = HistoryTrendAnalyzer.analyze(self.hypothesis_history)
        hypothesis_evaluation = HypothesisEvaluator.evaluate(
            snapshot=hypothesis_snapshot,
            history_trend_summary=history_trend_summary,
        )
        diagnostic_report = build_diagnostic_runtime_report(
            state=agent_state,
            confidence=confidence,
            confidence_trend=confidence_trend,
            temporal_confidence=temporal_confidence,
            evidence_summary=evidence_summary,
            hypothesis_snapshot=hypothesis_snapshot,
            hypothesis_history_size=hypothesis_history_size,
            history_trend_summary=history_trend_summary,
            created_at=snapshot.timestamp,
        )

        return event.with_sections(
            runtime_status=RuntimeStatus.COMPLETED,
            compatibility_context={
                "confidence": confidence,
                "evidence": evidence,
                "watchlist_action": watchlist_action,
                "watchlist_observation_count": watchlist_observation_count,
                "temporal_confidence": temporal_confidence,
                "evidence_summary": evidence_summary,
                "hypothesis_snapshot": hypothesis_snapshot,
                "confidence_trend": confidence_trend,
                "confidence_delta": confidence_delta,
                "log_messages": _cycle_log_messages(
                previous_state=previous_state_name,
                new_state=new_state_name,
                hypothesis=hypothesis,
                scenario_probability=scenario_probability,
                confidence_assessment=confidence_assessment,
            ),
                "hypothesis_history_size": hypothesis_history_size,
                "history_trend_summary": history_trend_summary,
                "diagnostic_report": diagnostic_report,
                "hypothesis_evaluation": hypothesis_evaluation,
                "previous_process_evidence_used": previous_process_evidence is not None,
            },
        )


def prepare_process_classification_input(
    *, episode_id: str, runtime_event_id: str, observations: ObservationPackage,
    structural_evidence: StructuralEvidence,
    market_efficiency_evidence: MarketEfficiencyEvidence,
    previous_process_evidence: ProcessEvidence | None,
    exchange: str, symbol: str, timeframe: str,
    classification_timestamp: datetime,
) -> ProcessClassificationInput:
    """Build and validate the immutable Process boundary input."""
    return ProcessClassificationInput(
        episode_id=episode_id, runtime_event_id=runtime_event_id,
        exchange=exchange, symbol=symbol, timeframe=timeframe,
        observations=observations,
        structural_evidence=structural_evidence,
        market_efficiency_evidence=market_efficiency_evidence,
        previous_process_evidence=previous_process_evidence,
        classification_timestamp=classification_timestamp,
    )


def _initial_runtime_event(
    snapshot: MarketSnapshot,
    *,
    episode_id: str | None,
    runtime_event_id: str | None,
    runtime_status: RuntimeStatus,
    errors_or_warnings: tuple[str, ...] = (),
    compatibility_context: dict[str, Any] | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=runtime_event_id or _cycle_event_id(snapshot),
        schema_version="runtime_event_v2",
        cycle_timestamp=snapshot.timestamp,
        symbol=snapshot.symbol,
        exchange=snapshot.exchange,
        timeframe=snapshot.timeframe,
        episode_id=episode_id,
        runtime_status=runtime_status,
        market_snapshot=snapshot,
        errors_or_warnings=errors_or_warnings,
        compatibility_context=compatibility_context or {},
    )


def run_agent_cycle(
    snapshot: MarketSnapshot,
    *,
    previous_state: str = "UNKNOWN",
    episode_id: str,
    previous_hypothesis: HypothesisPackage | None = None,
) -> RuntimeEvent:
    """Convenience entry point for one runtime reasoning cycle."""

    return RuntimeOrchestrator().process_market_update(
        snapshot,
        previous_state=previous_state,
        previous_hypothesis=previous_hypothesis,
        episode_id=episode_id,
    )


def run_agent_cycle_compatibility(
    snapshot: MarketSnapshot,
    *,
    previous_state: str = "UNKNOWN",
    episode_id: str,
    previous_hypothesis: HypothesisPackage | None = None,
) -> AgentCycleResult:
    """Deprecated compatibility entry point projected from canonical RuntimeEvent."""

    return project_agent_cycle_result(
        run_agent_cycle(
            snapshot,
            previous_state=previous_state,
            episode_id=episode_id,
            previous_hypothesis=previous_hypothesis,
        )
    )


def _cycle_log_messages(
    *,
    previous_state: str,
    new_state: str,
    hypothesis: HypothesisPackage,
    scenario_probability: ScenarioProbability,
    confidence_assessment: ConfidenceAssessment,
) -> tuple[str, ...]:
    return (
        f"state:{previous_state}->{new_state}",
        f"hypothesis:{hypothesis.lifecycle_status.name}:{hypothesis.hypothesis_label}",
        "explanation_confidence_compatibility_score:"
        f"{hypothesis.explanation_confidence_score}",
        f"scenario_primary:{scenario_probability.primary_scenario.value}",
        "scenario_alternatives:"
        + ",".join(
            item.scenario.value
            for item in scenario_probability.distribution
            if item.scenario is not scenario_probability.primary_scenario
        ),
        "scenario_deterministic_policy_weights:"
        + ",".join(
            f"{item.scenario.value}={item.probability}"
            for item in scenario_probability.distribution
        ),
        f"scenario_uncertainty:{scenario_probability.uncertainty.value}",
        "scenario_reason_codes:"
        + ",".join(item.value for item in scenario_probability.reason_codes),
        f"scenario_event_id:{scenario_probability.runtime_event_id}",
        f"scenario_episode_id:{scenario_probability.episode_id}",
        "scenario_source_hypothesis_id:"
        f"{scenario_probability.source_hypothesis_id}",
        f"final_confidence_level:{confidence_assessment.final_confidence_level.value}",
        f"confidence_event_id:{confidence_assessment.event_id}",
        f"confidence_episode_id:{confidence_assessment.episode_id}",
        "confidence_source_hypothesis_id:"
        f"{confidence_assessment.source_hypothesis_id}",
        f"confidence_data_quality:{confidence_assessment.data_quality_impact}",
    )


def _combine_hypothesis_input(
    snapshot: MarketSnapshot,
    *,
    structure_result: StructuralEvidence,
    market_result: MarketEfficiencyEvidence,
) -> dict[str, Any]:
    data = {
        "symbol": snapshot.symbol,
        "price": snapshot.price,
        "volume": snapshot.volume,
        "structure_summary": structure_result.structure_summary,
        "structural_events": structure_result.structural_events,
        "market_summary": market_result.participation_summary,
        "market_supporting_evidence": market_result.supporting_evidence,
        "market_evidence_against": market_result.evidence_against,
    }
    data.update(snapshot.optional_market_metrics)
    return data


def _update_temporal_confidence(
    manager: TemporalConfidenceManager,
    entry: object | None,
) -> TemporalConfidenceState | None:
    if entry is None:
        return None
    return manager.update(entry)


def _cycle_event_id(snapshot: MarketSnapshot) -> str:
    timestamp = snapshot.timestamp.isoformat()
    return f"agent-cycle:{snapshot.exchange}:{snapshot.symbol}:{snapshot.timeframe}:{snapshot.event_id}:{timestamp}"


def _canonical_analytical_timestamp(
    snapshot: MarketSnapshot,
    classification_timestamp: datetime | None,
) -> datetime:
    """Resolve the admitted cycle timestamp used by every analytical stage."""

    if classification_timestamp is None:
        return snapshot.timestamp
    if classification_timestamp != snapshot.timestamp:
        raise ValueError(
            "classification_timestamp must equal the admitted "
            "MarketSnapshot.timestamp."
        )
    return snapshot.timestamp


def _agent_state_type_from_value(value: str | AgentStateType) -> AgentStateType:
    if isinstance(value, AgentStateType):
        return value

    normalized = str(value).lower()
    for state in AgentStateType:
        if normalized in (state.name.lower(), state.value):
            return state

    return AgentStateType.UNKNOWN
