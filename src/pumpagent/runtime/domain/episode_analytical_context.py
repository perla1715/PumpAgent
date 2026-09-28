"""Bounded previous-cycle analytical continuity for one Observation Episode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pumpagent.runtime.domain.agent_state import AgentState
from pumpagent.runtime.domain.confidence_assessment import ConfidenceAssessment
from pumpagent.runtime.domain.base import SerializableMixin, freeze_dataclass_fields
from pumpagent.runtime.domain.enums import AgentStateType, ObservationEpisodeStatus
from pumpagent.runtime.domain.observation_episode import ObservationEpisode
from pumpagent.runtime.domain.process_evidence import ProcessEvidence, ProcessState
from pumpagent.runtime.domain.process_quality import (
    HealthyBaselineDesignation,
    HealthyBaselineReference,
    ProcessQualityAssessment,
    ProcessQualityAssessmentReference,
)
from pumpagent.runtime.domain.hypothesis_package import HypothesisPackage
from pumpagent.runtime.domain.scenario_probability import ScenarioProbability
from pumpagent.runtime.modules.evidence import EvidenceSummary


EPISODE_ANALYTICAL_CONTEXT_SCHEMA_VERSION = "episode_analytical_context_v5"


@dataclass(frozen=True)
class EpisodeAnalyticalContext(SerializableMixin):
    episode_id: str
    exchange: str
    symbol: str
    timeframe: str
    latest_runtime_event_id: str
    latest_completed_closed_candle_timestamp: datetime
    latest_process_evidence: ProcessEvidence
    latest_process_state: ProcessState
    latest_process_runtime_event_id: str
    latest_process_observation_timestamp: datetime
    latest_hypothesis: HypothesisPackage | None
    latest_agent_state: AgentState | None
    latest_scenario_probability: ScenarioProbability
    latest_confidence_assessment: ConfidenceAssessment
    latest_confidence: int | None
    latest_evidence_summary: EvidenceSummary | None
    latest_process_quality_assessment: ProcessQualityAssessment
    previous_process_quality_reference: ProcessQualityAssessmentReference | None
    process_quality_history: tuple[ProcessQualityAssessment, ...]
    healthy_baseline_reference: HealthyBaselineReference | None
    healthy_baseline_designation: HealthyBaselineDesignation | None
    updated_at: datetime
    completed_analytical_cycle_count: int
    schema_version: str = EPISODE_ANALYTICAL_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        freeze_dataclass_fields(self)
        for name in ("episode_id", "exchange", "symbol", "timeframe",
                     "latest_runtime_event_id", "schema_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")
        _require_aware("latest_completed_closed_candle_timestamp",
                       self.latest_completed_closed_candle_timestamp)
        _require_aware("updated_at", self.updated_at)
        _require_aware("latest_process_observation_timestamp",
                       self.latest_process_observation_timestamp)
        if self.completed_analytical_cycle_count < 1:
            raise ValueError("completed_analytical_cycle_count must be positive.")
        if self.latest_hypothesis is not None and not isinstance(self.latest_hypothesis, HypothesisPackage):
            raise ValueError("latest_hypothesis must be a HypothesisPackage when present.")
        if self.latest_hypothesis is not None and self.latest_hypothesis.episode_id != self.episode_id:
            raise ValueError("Latest hypothesis must belong to the analytical context Episode.")
        if (
            self.latest_hypothesis is not None
            and self.latest_hypothesis.event_id != self.latest_runtime_event_id
        ):
            raise ValueError(
                "Latest hypothesis must belong to the latest Runtime event."
            )
        if self.latest_agent_state is not None and not isinstance(self.latest_agent_state, AgentState):
            raise ValueError("latest_agent_state must be an AgentState when present.")
        if (
            self.latest_agent_state is not None
            and self.latest_agent_state.event_id != self.latest_runtime_event_id
        ):
            raise ValueError(
                "Latest Agent State must belong to the latest Runtime event."
            )
        _validate_scenario_probability(self)
        _validate_confidence_assessment(self)
        if self.latest_confidence is not None and not isinstance(self.latest_confidence, int):
            raise ValueError("latest_confidence must be an int when present.")
        if self.latest_evidence_summary is not None and not isinstance(self.latest_evidence_summary, EvidenceSummary):
            raise ValueError("latest_evidence_summary must be an EvidenceSummary when present.")
        if not isinstance(self.latest_process_evidence, ProcessEvidence):
            raise ValueError("latest_process_evidence must be ProcessEvidence.")
        _validate_process_quality(self)
        if (
            self.latest_agent_state is not None
            and self.latest_agent_state.process_direction
            is not self.latest_process_evidence.process_direction
        ):
            raise ValueError(
                "Latest Agent State process direction must match Process evidence."
            )
        if not isinstance(self.latest_process_state, ProcessState):
            raise ValueError("latest_process_state must be ProcessState.")
        if self.latest_process_evidence.episode_id != self.episode_id:
            raise ValueError("Process evidence must belong to the analytical context Episode.")
        if self.latest_process_evidence.current_process_state is not self.latest_process_state:
            raise ValueError("Latest Process state must match the retained Process evidence.")
        if self.latest_process_evidence.runtime_event_id != self.latest_process_runtime_event_id:
            raise ValueError("Latest Process Runtime event ID must match Process evidence.")
        if self.latest_process_evidence.observation_timestamp != self.latest_process_observation_timestamp:
            raise ValueError("Latest Process timestamp must match Process evidence.")
        if not _same_market(self, self.latest_process_evidence):
            raise ValueError("Process evidence market identity must match the analytical context.")


@dataclass(frozen=True)
class RuntimePreviousContext:
    previous_hypothesis: HypothesisPackage | None
    previous_state: str
    previous_process_evidence: ProcessEvidence | None
    previous_process_quality_assessments: tuple[ProcessQualityAssessment, ...]
    healthy_baseline_reference: HealthyBaselineReference | None
    healthy_baseline_designation: HealthyBaselineDesignation | None
    previous_scenario_probability: ScenarioProbability | None


def prepare_runtime_previous_context(
    active_episode: ObservationEpisode,
    stored_context: EpisodeAnalyticalContext | None,
) -> RuntimePreviousContext:
    """Return Runtime inputs only from the exact active Episode scope."""
    _require_active_episode(active_episode)
    if stored_context is None:
        return RuntimePreviousContext(
            None, AgentStateType.UNKNOWN.name, None, (), None, None, None
        )
    _validate_context_identity(active_episode, stored_context)
    state = (stored_context.latest_agent_state.current_state.name
             if stored_context.latest_agent_state is not None
             else AgentStateType.UNKNOWN.name)
    return RuntimePreviousContext(
        stored_context.latest_hypothesis,
        state,
        stored_context.latest_process_evidence,
        stored_context.process_quality_history,
        stored_context.healthy_baseline_reference,
        stored_context.healthy_baseline_designation,
        stored_context.latest_scenario_probability,
    )


def build_episode_analytical_context_from_runtime_result(
    runtime_result: Any,
    active_episode: ObservationEpisode,
    accepted_closed_candle_timestamp: datetime,
    *,
    updated_at: datetime,
) -> EpisodeAnalyticalContext:
    """Purely extract supported outputs from one successful Runtime result."""
    _require_active_episode(active_episode)
    _require_aware("accepted_closed_candle_timestamp", accepted_closed_candle_timestamp)
    _require_aware("updated_at", updated_at)
    snapshot = getattr(runtime_result, "snapshot", None)
    if snapshot is None or not _same_market(active_episode, snapshot):
        raise ValueError("Runtime result market identity must match the active Episode.")
    event_id = getattr(runtime_result, "event_id", None)
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("Runtime result event ID must be non-empty.")
    process_evidence = getattr(runtime_result, "process_evidence", None)
    if not isinstance(process_evidence, ProcessEvidence):
        raise ValueError("Runtime result must contain Process evidence.")
    if process_evidence.episode_id != active_episode.episode_id:
        raise ValueError("Runtime Process evidence must belong to the active Episode.")
    if process_evidence.runtime_event_id != event_id:
        raise ValueError("Runtime Process evidence event ID must align with Runtime.")
    if not _same_market(active_episode, process_evidence):
        raise ValueError("Runtime Process evidence market identity must match the active Episode.")
    return EpisodeAnalyticalContext(
        episode_id=active_episode.episode_id,
        exchange=active_episode.exchange,
        symbol=active_episode.symbol,
        timeframe=active_episode.timeframe,
        latest_runtime_event_id=event_id,
        latest_completed_closed_candle_timestamp=accepted_closed_candle_timestamp,
        latest_process_evidence=process_evidence,
        latest_process_state=process_evidence.current_process_state,
        latest_process_runtime_event_id=process_evidence.runtime_event_id,
        latest_process_observation_timestamp=process_evidence.observation_timestamp,
        latest_hypothesis=getattr(runtime_result, "hypothesis", None),
        latest_agent_state=getattr(runtime_result, "agent_state", None),
        latest_scenario_probability=_scenario_probability_from_result(
            runtime_result,
            event_id=event_id,
            episode_id=active_episode.episode_id,
        ),
        latest_confidence_assessment=_confidence_assessment_from_result(
            runtime_result,
            event_id=event_id,
            episode_id=active_episode.episode_id,
        ),
        latest_confidence=getattr(runtime_result, "confidence", None),
        latest_evidence_summary=getattr(runtime_result, "evidence_summary", None),
        latest_process_quality_assessment=getattr(
            runtime_result, "process_quality_assessment", None
        ),
        previous_process_quality_reference=getattr(
            runtime_result, "previous_process_quality_reference", None
        ),
        process_quality_history=getattr(
            runtime_result, "process_quality_history", ()
        ),
        healthy_baseline_reference=getattr(
            runtime_result, "healthy_baseline_reference", None
        ),
        healthy_baseline_designation=getattr(
            runtime_result, "healthy_baseline_designation", None
        ),
        updated_at=updated_at,
        completed_analytical_cycle_count=active_episode.observation_cycle_count + 1,
    )


def _validate_process_quality(context: EpisodeAnalyticalContext) -> None:
    assessment = context.latest_process_quality_assessment
    if not isinstance(assessment, ProcessQualityAssessment):
        raise ValueError(
            "latest_process_quality_assessment must be a ProcessQualityAssessment."
        )
    if assessment.episode_id != context.episode_id:
        raise ValueError("Process Quality assessment must belong to the context Episode.")
    if assessment.runtime_event_id != context.latest_runtime_event_id:
        raise ValueError("Process Quality assessment must belong to the latest Runtime event.")
    history = context.process_quality_history
    if not isinstance(history, tuple) or not history or history[-1] != assessment:
        raise ValueError(
            "Process Quality history must end with the latest assessment."
        )
    if any(
        not isinstance(item, ProcessQualityAssessment)
        or item.episode_id != context.episode_id
        for item in history
    ):
        raise ValueError("Process Quality history must remain inside one Episode.")
    expected_previous = history[-2].to_reference() if len(history) > 1 else None
    if context.previous_process_quality_reference != expected_previous:
        raise ValueError(
            "Previous Process Quality reference must identify the preceding assessment."
        )
    baseline = context.healthy_baseline_reference
    designation = context.healthy_baseline_designation
    if (baseline is None) != (designation is None):
        raise ValueError(
            "Healthy Baseline reference and designation must be present together."
        )
    if baseline is None:
        return
    if baseline.episode_id != context.episode_id:
        raise ValueError("Healthy Baseline must remain inside the context Episode.")
    if designation.to_reference() != baseline:
        raise ValueError("Healthy Baseline reference does not match its designation.")
    if designation.predecessor_baseline is not None:
        raise ValueError("MVP Healthy Baseline replacement is forbidden.")
    if not any(item.to_reference() == baseline.source_assessment for item in history):
        raise ValueError(
            "Healthy Baseline must reference authenticated Process Quality history."
        )


def _scenario_probability_from_result(
    runtime_result: Any,
    *,
    event_id: str,
    episode_id: str,
) -> ScenarioProbability:
    scenario = getattr(runtime_result, "scenario_probability", None)
    hypothesis = getattr(runtime_result, "hypothesis", None)
    if not isinstance(scenario, ScenarioProbability):
        raise ValueError("Runtime result must contain Scenario Probability.")
    if not isinstance(hypothesis, HypothesisPackage):
        raise ValueError("Runtime result must contain a canonical HypothesisPackage.")
    if scenario.runtime_event_id != event_id:
        raise ValueError("Runtime Scenario Probability event ID must align with Runtime.")
    if scenario.episode_id != episode_id:
        raise ValueError(
            "Runtime Scenario Probability must belong to the active Episode."
        )
    if scenario.source_hypothesis_id != hypothesis.hypothesis_id:
        raise ValueError(
            "Runtime Scenario Probability source must match the canonical Hypothesis."
        )
    return scenario


def _validate_scenario_probability(context: EpisodeAnalyticalContext) -> None:
    scenario = context.latest_scenario_probability
    if not isinstance(scenario, ScenarioProbability):
        raise ValueError(
            "latest_scenario_probability must be a ScenarioProbability."
        )
    if scenario.runtime_event_id != context.latest_runtime_event_id:
        raise ValueError(
            "Latest Scenario Probability must belong to the latest Runtime event."
        )
    if scenario.episode_id != context.episode_id:
        raise ValueError(
            "Latest Scenario Probability must belong to the analytical context Episode."
        )
    if context.latest_hypothesis is None:
        raise ValueError(
            "Latest Scenario Probability requires the latest canonical Hypothesis."
        )
    if scenario.source_hypothesis_id != context.latest_hypothesis.hypothesis_id:
        raise ValueError(
            "Latest Scenario Probability source must match the latest Hypothesis."
        )


def _confidence_assessment_from_result(
    runtime_result: Any,
    *,
    event_id: str,
    episode_id: str,
) -> ConfidenceAssessment:
    assessment = getattr(runtime_result, "confidence_assessment", None)
    hypothesis = getattr(runtime_result, "hypothesis", None)
    if not isinstance(assessment, ConfidenceAssessment):
        raise ValueError("Runtime result must contain ConfidenceAssessment.")
    if not isinstance(hypothesis, HypothesisPackage):
        raise ValueError("Runtime result must contain a canonical HypothesisPackage.")
    if assessment.event_id != event_id:
        raise ValueError("Runtime ConfidenceAssessment event ID must align with Runtime.")
    if assessment.episode_id != episode_id:
        raise ValueError("Runtime ConfidenceAssessment must belong to the active Episode.")
    if assessment.source_hypothesis_id != hypothesis.hypothesis_id:
        raise ValueError(
            "Runtime ConfidenceAssessment source must match the canonical Hypothesis."
        )
    return assessment


def _validate_confidence_assessment(context: EpisodeAnalyticalContext) -> None:
    assessment = context.latest_confidence_assessment
    if not isinstance(assessment, ConfidenceAssessment):
        raise ValueError(
            "latest_confidence_assessment must be a ConfidenceAssessment."
        )
    if assessment.event_id != context.latest_runtime_event_id:
        raise ValueError(
            "Latest ConfidenceAssessment must belong to the latest Runtime event."
        )
    if assessment.episode_id != context.episode_id:
        raise ValueError(
            "Latest ConfidenceAssessment must belong to the analytical context Episode."
        )
    if context.latest_hypothesis is None:
        raise ValueError(
            "Latest ConfidenceAssessment requires the latest canonical Hypothesis."
        )
    if assessment.source_hypothesis_id != context.latest_hypothesis.hypothesis_id:
        raise ValueError(
            "Latest ConfidenceAssessment source must match the latest Hypothesis."
        )


def _require_active_episode(episode: ObservationEpisode) -> None:
    if not isinstance(episode, ObservationEpisode):
        raise ValueError("active_episode must be an ObservationEpisode.")
    if episode.status is not ObservationEpisodeStatus.ACTIVE:
        raise ValueError("Analytical context may only be used with an active Episode.")
    if not episode.episode_id.strip():
        raise ValueError("Active Episode ID must be non-empty.")


def _validate_context_identity(episode: ObservationEpisode, context: EpisodeAnalyticalContext) -> None:
    if not isinstance(context, EpisodeAnalyticalContext):
        raise ValueError("stored_context must be an EpisodeAnalyticalContext.")
    if context.episode_id != episode.episode_id:
        raise ValueError("Analytical context Episode ID does not match the active Episode.")
    if not _same_market(episode, context):
        raise ValueError("Analytical context market identity does not match the active Episode.")
    if context.completed_analytical_cycle_count != episode.observation_cycle_count:
        raise ValueError("Analytical context cycle count does not match the active Episode.")


def _same_market(left: object, right: object) -> bool:
    return (getattr(left, "exchange").strip().lower(), getattr(left, "symbol").strip().upper(),
            getattr(left, "timeframe").strip().lower()) == (
            getattr(right, "exchange").strip().lower(), getattr(right, "symbol").strip().upper(),
            getattr(right, "timeframe").strip().lower())


def _require_aware(name: str, value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
