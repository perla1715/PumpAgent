from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from pumpagent.runtime.domain import (
    MarketEfficiencyEvidence,
    ObservationPackage,
    ProcessEvidence,
    ProcessEvidenceFamily,
    ProcessState,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import (
    DataQualityStatus,
    EvidenceStrength,
    ProcessDirection,
    UncertaintyLevel,
)
from pumpagent.runtime.modules.process_classification import (
    ProcessClassificationInput,
    classify_market_process,
)


NOW = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)


def observations(closes=(100, 102, 105), volumes=(10, 11, 12), metrics=None,
                 quality=DataQualityStatus.VALID, warnings=()):
    candles = tuple({"open": close, "high": close, "low": close, "close": close,
                     "volume": volume} for close, volume in zip(closes, volumes))
    return ObservationPackage(
        event_id="event-2", observation_timestamp=NOW, normalized_price=closes[-1],
        normalized_ohlcv=candles, normalized_volume=volumes[-1],
        available_metrics=tuple((metrics or {}).keys()), missing_metrics=(),
        data_quality_status=quality, validation_warnings=warnings,
        normalized_metrics=metrics or {},
    )


def structure(events=(), warnings=()):
    return StructuralEvidence(
        event_id="event-2", structure_summary="facts", trend_structure="facts",
        structural_bias="not_assessed", key_levels=(), structural_events=events,
        evidence_strength=EvidenceStrength.WEAK, evidence_against=(),
        uncertainty=UncertaintyLevel.HIGH,
        technical_context={"source_observation_event_id": "event-2",
                           "chart_structure": {"warnings": warnings}},
    )


def efficiency():
    return MarketEfficiencyEvidence(
        event_id="event-2", participation_summary="facts",
        participation_direction="not_assessed", efficiency_summary="facts",
        efficiency_status="not_assessed", supporting_evidence=(), evidence_against=(),
        evidence_strength=EvidenceStrength.WEAK, uncertainty=UncertaintyLevel.HIGH,
        market_mechanics_context={"source_observation_event_id": "event-2"},
    )


def prior(state=ProcessState.UNKNOWN, episode="episode-1"):
    from pumpagent.runtime.domain import ProcessEvidenceAvailability, ProcessEvidenceItem
    from pumpagent.runtime.domain import ProcessEvidenceRelationship, ProcessTransition
    item = ProcessEvidenceItem(
        evidence_family=ProcessEvidenceFamily.PRICE, evidence_key="prior_price",
        description="Prior factual evidence.",
        relationship=ProcessEvidenceRelationship.SUPPORTING,
        source_module="test", source_field="prior", observation_timestamp=NOW,
        availability_status=ProcessEvidenceAvailability.AVAILABLE, normalized_value=1,
        timeframe="5m",
    )
    previous = None if state is ProcessState.UNKNOWN else ProcessState.UNKNOWN
    transition = ProcessTransition.INITIAL if state is ProcessState.UNKNOWN else ProcessTransition.CHANGED
    return ProcessEvidence(
        episode_id=episode, runtime_event_id="event-1", exchange="bybit",
        symbol="BTCUSDT", timeframe="5m", observation_timestamp=NOW,
        current_process_state=state, previous_process_state=previous,
        process_direction=ProcessDirection.UNKNOWN,
        detected_transition=transition, process_summary="Prior result.",
        supporting_evidence=(item,) if state is not ProcessState.UNKNOWN else (),
        contradicting_evidence=(), neutral_evidence=(),
        available_evidence_families={ProcessEvidenceFamily.PRICE},
        missing_evidence_families={ProcessEvidenceFamily.VOLUME},
        insufficiency_reasons=("Prior initial result.",) if state is ProcessState.UNKNOWN else (),
        evidence_strength=EvidenceStrength.WEAK, uncertainty_level=UncertaintyLevel.HIGH,
    )


def classify(*, obs=None, structural=None, previous=prior()):
    value = ProcessClassificationInput(
        episode_id="episode-1", runtime_event_id="event-2", exchange="bybit",
        symbol="BTCUSDT", timeframe="5m", observations=obs or observations(),
        structural_evidence=structural or structure(),
        market_efficiency_evidence=efficiency(), previous_process_evidence=previous,
        classification_timestamp=NOW,
    )
    return classify_market_process(value)


def test_first_cycle_is_unknown_but_exposes_current_facts_and_baseline_reason():
    result = classify(obs=observations(metrics={"oi_change_5m_pct": 2}), previous=None)
    assert result.current_process_state is ProcessState.UNKNOWN
    assert result.process_direction is ProcessDirection.UP
    assert "No previous Process baseline." in result.insufficiency_reasons
    assert {item.evidence_family for item in result.supporting_evidence} >= {
        ProcessEvidenceFamily.PRICE, ProcessEvidenceFamily.VOLUME,
        ProcessEvidenceFamily.OPEN_INTEREST,
    }


@pytest.mark.parametrize("confirmation", ["oi", "structure"])
def test_alive_requires_price_volume_and_independent_confirmation(confirmation):
    obs = observations(metrics={"oi_change_5m_pct": 1} if confirmation == "oi" else {})
    structural = structure(("higher_high_detected",)) if confirmation == "structure" else structure()
    assert classify(obs=obs, structural=structural).current_process_state is ProcessState.CONTINUATION_ALIVE


@pytest.mark.parametrize("obs", [
    observations(closes=(100, 102), volumes=(10,)),
    observations(closes=(100, 102, 104), volumes=(10, 10, 40), metrics={"oi_change_5m_pct": 1}),
    observations(closes=(100, 100, 100), volumes=(10, 15, 20), metrics={"oi_change_5m_pct": 1}),
])
def test_missing_volume_isolated_spike_or_no_price_progress_is_unknown(obs):
    assert classify(obs=obs).current_process_state is ProcessState.UNKNOWN


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        ((100, 102, 105), ProcessDirection.UP),
        ((105, 102, 100), ProcessDirection.DOWN),
        ((100, 100, 100), ProcessDirection.NEUTRAL),
        ((100,), ProcessDirection.UNKNOWN),
    ],
)
def test_process_direction_uses_structured_close_orientation(closes, expected):
    volumes = tuple(10 + index for index in range(len(closes)))
    result = classify(obs=observations(closes=closes, volumes=volumes))
    assert result.process_direction is expected


def test_invalid_quality_makes_orientation_unknown_not_neutral():
    result = classify(obs=observations(quality=DataQualityStatus.CORRUPTED))
    assert result.process_direction is ProcessDirection.UNKNOWN


def test_process_direction_is_independent_of_process_state():
    first_cycle = classify(
        obs=observations(metrics={"oi_change_5m_pct": 2}),
        previous=None,
    )
    later_cycle = classify(obs=observations(metrics={"oi_change_5m_pct": 2}))
    assert first_cycle.current_process_state is ProcessState.UNKNOWN
    assert later_cycle.current_process_state is ProcessState.CONTINUATION_ALIVE
    assert first_cycle.process_direction is ProcessDirection.UP
    assert later_cycle.process_direction is ProcessDirection.UP


def test_missing_optional_context_does_not_block_alive_and_negative_funding_cannot_veto():
    result = classify(obs=observations(metrics={"oi_change_5m_pct": 1, "funding_rate": -1}))
    assert result.current_process_state is ProcessState.CONTINUATION_ALIVE


def test_negative_cvd_is_recorded_without_vetoing_aligned_mandatory_families():
    result = classify(obs=observations(metrics={"oi_change_5m_pct": 1, "cvd": -3}))
    assert result.current_process_state is ProcessState.CONTINUATION_ALIVE
    assert any(item.evidence_family is ProcessEvidenceFamily.CVD for item in result.contradicting_evidence)


def test_weakening_requires_prior_alive_and_two_deterioration_families():
    result = classify(obs=observations(closes=(100, 105, 105), volumes=(20, 18, 15),
                                       metrics={"oi_change_5m_pct": -1}),
                      previous=prior(ProcessState.CONTINUATION_ALIVE))
    assert result.current_process_state is ProcessState.WEAKENING


def test_plateau_volume_contraction_and_lower_high_is_weakening():
    result = classify(obs=observations(closes=(100, 105, 105), volumes=(20, 18, 15)),
                      structural=structure(("lower_high_detected",)),
                      previous=prior(ProcessState.CONTINUATION_ALIVE))
    assert result.current_process_state is ProcessState.WEAKENING


def test_one_volume_dip_or_disappearing_participation_is_not_weakening():
    one_dip = classify(obs=observations(closes=(100, 103, 106), volumes=(20, 22, 18),
                                            metrics={"oi_change_5m_pct": 1}),
                       previous=prior(ProcessState.CONTINUATION_ALIVE))
    missing = classify(obs=observations(closes=(100, 105), volumes=(20,)),
                       previous=prior(ProcessState.CONTINUATION_ALIVE))
    assert one_dip.current_process_state is ProcessState.UNKNOWN
    assert missing.current_process_state is ProcessState.UNKNOWN


def test_no_prior_alive_or_poor_data_cannot_create_weakening():
    weak = observations(closes=(105, 104), volumes=(20, 15), metrics={"oi_change_5m_pct": -1})
    assert classify(obs=weak).current_process_state is ProcessState.UNKNOWN
    bad = observations(quality=DataQualityStatus.CORRUPTED)
    assert classify(obs=bad, previous=prior(ProcessState.CONTINUATION_ALIVE)).current_process_state is ProcessState.UNKNOWN


def test_previous_weakening_can_recover_to_alive():
    result = classify(obs=observations(metrics={"oi_change_5m_pct": 1}),
                      previous=prior(ProcessState.WEAKENING))
    assert result.current_process_state is ProcessState.CONTINUATION_ALIVE
    assert result.detected_transition.value == "recovered"


def test_lower_high_blocks_alive_even_with_price_volume_and_oi():
    result = classify(obs=observations(metrics={"oi_change_5m_pct": 1}),
                      structural=structure(("lower_high_detected",)))
    assert result.current_process_state is ProcessState.UNKNOWN


def test_validation_rejects_cross_episode_event_mismatch_and_naive_time():
    with pytest.raises(ValueError, match="Episode boundary"):
        classify(previous=prior(episode="episode-other"))
    with pytest.raises(ValueError, match="event IDs"):
        ProcessClassificationInput(
            episode_id="episode-1", runtime_event_id="wrong", exchange="bybit",
            symbol="BTCUSDT", timeframe="5m", observations=observations(),
            structural_evidence=structure(), market_efficiency_evidence=efficiency(),
            previous_process_evidence=prior(), classification_timestamp=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        ProcessClassificationInput(
            episode_id="episode-1", runtime_event_id="event-2", exchange="bybit",
            symbol="BTCUSDT", timeframe="5m", observations=observations(),
            structural_evidence=structure(), market_efficiency_evidence=efficiency(),
            previous_process_evidence=prior(), classification_timestamp=NOW.replace(tzinfo=None),
        )


def test_output_is_deterministic_immutable_serializable_and_sources_unchanged():
    obs = observations(metrics={"oi_change_5m_pct": 1})
    before = obs.to_dict()
    first = classify(obs=obs)
    second = classify(obs=obs)
    assert first == second
    assert obs.to_dict() == before
    assert first.to_dict()["current_process_state"] == "continuation_alive"
    assert first.to_dict()["process_direction"] == "up"
    with pytest.raises(FrozenInstanceError):
        first.process_summary = "changed"
