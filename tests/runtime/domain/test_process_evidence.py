from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from pumpagent.runtime.domain import (
    ProcessEvidence,
    ProcessEvidenceAvailability,
    ProcessEvidenceFamily,
    ProcessEvidenceItem,
    ProcessEvidenceRelationship,
    ProcessState,
    ProcessTransition,
)
from pumpagent.runtime.domain.enums import (
    AgentStateType,
    ConfidenceLevel,
    EvidenceStrength,
    ObservationEpisodeStatus,
    ProcessDirection,
    UncertaintyLevel,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def item(
    family=ProcessEvidenceFamily.PRICE,
    relationship=ProcessEvidenceRelationship.SUPPORTING,
    availability=ProcessEvidenceAvailability.AVAILABLE,
    *,
    key="close_delta",
    source_field="technical_context.close_delta",
    normalized_value=1.2,
):
    return ProcessEvidenceItem(
        evidence_family=family,
        evidence_key=key,
        description="Price advanced over the observed window.",
        relationship=relationship,
        source_module="perception",
        source_field=source_field,
        normalized_value=normalized_value,
        unit="percent",
        timeframe="5m",
        observation_timestamp=NOW,
        availability_status=availability,
    )


def result(
    current=ProcessState.UNKNOWN,
    previous=None,
    transition=ProcessTransition.INITIAL,
    supporting=(),
    contradicting=(),
    neutral=(),
    reasons=("Participation evidence is incomplete.",),
    episode_id="episode-1",
    **updates,
):
    values = dict(
        episode_id=episode_id,
        runtime_event_id="event-1",
        exchange="bybit",
        symbol="BTCUSDT",
        timeframe="5m",
        observation_timestamp=NOW,
        current_process_state=current,
        process_direction=ProcessDirection.UNKNOWN,
        previous_process_state=previous,
        detected_transition=transition,
        process_summary="Evidence does not yet support a bounded interpretation.",
        supporting_evidence=supporting,
        contradicting_evidence=contradicting,
        neutral_evidence=neutral,
        available_evidence_families={ProcessEvidenceFamily.PRICE},
        missing_evidence_families={ProcessEvidenceFamily.OPEN_INTEREST},
        insufficiency_reasons=reasons,
        evidence_strength=EvidenceStrength.WEAK,
        uncertainty_level=UncertaintyLevel.HIGH,
    )
    values.update(updates)
    return ProcessEvidence(**values)


def test_valid_unknown_is_a_complete_serializable_result():
    unavailable = item(
        ProcessEvidenceFamily.OPEN_INTEREST,
        ProcessEvidenceRelationship.UNAVAILABLE,
        ProcessEvidenceAvailability.UNAVAILABLE,
        key="open_interest",
        source_field="available_metrics.open_interest",
        normalized_value=None,
    )
    evidence = result(neutral=[unavailable])
    primitive = evidence.to_dict()
    assert primitive["current_process_state"] == "unknown"
    assert primitive["process_direction"] == "unknown"
    assert primitive["evidence_strength"] == "weak"
    assert primitive["uncertainty_level"] == "high"
    assert primitive["observation_timestamp"].endswith("+00:00")
    assert primitive["neutral_evidence"][0]["evidence_family"] == "open_interest"


@pytest.mark.parametrize("state", [ProcessState.CONTINUATION_ALIVE, ProcessState.WEAKENING])
def test_valid_non_unknown_states_require_and_accept_support(state):
    evidence = result(
        current=state,
        previous=ProcessState.UNKNOWN,
        transition=ProcessTransition.CHANGED,
        supporting=[item()],
        reasons=(),
    )
    assert evidence.current_process_state is state


@pytest.mark.parametrize(
    ("previous", "current", "transition"),
    [
        (None, ProcessState.UNKNOWN, ProcessTransition.INITIAL),
        (ProcessState.UNKNOWN, ProcessState.UNKNOWN, ProcessTransition.UNCHANGED),
        (ProcessState.CONTINUATION_ALIVE, ProcessState.WEAKENING, ProcessTransition.CHANGED),
        (ProcessState.WEAKENING, ProcessState.CONTINUATION_ALIVE, ProcessTransition.RECOVERED),
        (ProcessState.CONTINUATION_ALIVE, ProcessState.UNKNOWN, ProcessTransition.BECAME_UNKNOWN),
    ],
)
def test_transition_vocabulary(previous, current, transition):
    result(
        current=current,
        previous=previous,
        transition=transition,
        supporting=() if current is ProcessState.UNKNOWN else [item()],
        reasons=("Evidence became insufficient.",) if current is ProcessState.UNKNOWN else (),
    )


def test_inconsistent_transition_is_rejected():
    with pytest.raises(ValueError, match="detected_transition"):
        result(previous=ProcessState.UNKNOWN, transition=ProcessTransition.CHANGED)


def test_unknown_without_insufficiency_reason_is_rejected():
    with pytest.raises(ValueError, match="UNKNOWN requires"):
        result(reasons=())


def test_non_unknown_without_support_is_rejected():
    with pytest.raises(ValueError, match="requires supporting"):
        result(current=ProcessState.CONTINUATION_ALIVE, previous=ProcessState.UNKNOWN,
               transition=ProcessTransition.CHANGED, reasons=())


@pytest.mark.parametrize(
    "invalid_state",
    ["UNKNOWN", AgentStateType.UNKNOWN, ObservationEpisodeStatus.ACTIVE, "LONG", "SHORT"],
)
def test_only_dedicated_process_states_are_accepted(invalid_state):
    with pytest.raises(ValueError, match="ProcessState"):
        result(current=invalid_state)


@pytest.mark.parametrize("direction", tuple(ProcessDirection))
def test_process_direction_values_are_typed_and_serializable(direction):
    evidence = result(process_direction=direction)
    assert evidence.to_dict()["process_direction"] == direction.value


def test_process_direction_rejects_free_form_values():
    with pytest.raises(ValueError, match="ProcessDirection"):
        result(process_direction="up")


def test_unavailable_item_cannot_be_supporting():
    with pytest.raises(ValueError, match="unavailable relationship"):
        item(availability=ProcessEvidenceAvailability.UNAVAILABLE, normalized_value=None)


def test_available_and_missing_family_overlap_is_rejected():
    with pytest.raises(ValueError, match="cannot overlap"):
        result(missing_evidence_families={ProcessEvidenceFamily.PRICE})


def test_duplicate_provenance_is_rejected_but_same_family_distinct_provenance_is_valid():
    first = item()
    duplicate = item()
    with pytest.raises(ValueError, match="Duplicate evidence"):
        result(current=ProcessState.CONTINUATION_ALIVE, previous=ProcessState.UNKNOWN,
               transition=ProcessTransition.CHANGED, supporting=[first, duplicate], reasons=())
    second = item(key="latest_close", source_field="technical_context.latest_close")
    evidence = result(current=ProcessState.CONTINUATION_ALIVE, previous=ProcessState.UNKNOWN,
                      transition=ProcessTransition.CHANGED, supporting=[first, second], reasons=())
    assert {value.evidence_family for value in evidence.supporting_evidence} == {
        ProcessEvidenceFamily.PRICE
    }


def test_nested_inputs_are_recursively_frozen_without_mutating_sources():
    source_value = {"window": [1, {"close": 2}]}
    evidence_item = item(normalized_value=source_value)
    assert source_value == {"window": [1, {"close": 2}]}
    assert evidence_item.normalized_value["window"] == (1, {"close": 2})
    with pytest.raises(TypeError):
        evidence_item.normalized_value["new"] = 3
    evidence = result(neutral=[], supporting=[])
    assert isinstance(evidence.supporting_evidence, tuple)
    assert isinstance(evidence.available_evidence_families, frozenset)
    with pytest.raises(FrozenInstanceError):
        evidence.process_summary = "changed"


def test_naive_timestamps_and_empty_identity_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        result(observation_timestamp=datetime(2026, 7, 15, 12, 0))
    for field in ("episode_id", "runtime_event_id", "exchange", "symbol", "timeframe"):
        with pytest.raises(ValueError, match=field):
            result(**{field: " "})


def test_item_requires_identity_description_source_and_provenance():
    for field in ("evidence_key", "description", "source_module", "source_field"):
        values = {field: ""}
        with pytest.raises(ValueError, match=field):
            ProcessEvidenceItem(
                evidence_family=ProcessEvidenceFamily.PRICE,
                evidence_key=values.get("evidence_key", "close"),
                description=values.get("description", "Observed close."),
                relationship=ProcessEvidenceRelationship.NEUTRAL,
                source_module=values.get("source_module", "perception"),
                source_field=values.get("source_field", "latest_close"),
                observation_timestamp=NOW,
                availability_status=ProcessEvidenceAvailability.AVAILABLE,
            )


def test_final_confidence_enum_is_not_evidence_strength():
    with pytest.raises(ValueError, match="not final confidence"):
        result(evidence_strength=ConfidenceLevel.LOW)


def test_normalized_value_rejects_non_serializable_objects():
    with pytest.raises(ValueError, match="serializable primitive"):
        item(normalized_value=object())


def test_unknown_makes_missing_unavailable_or_contradictory_evidence_explicit():
    with pytest.raises(ValueError, match="must make missing"):
        result(missing_evidence_families=set())


def test_episode_isolation_and_previous_state_consistency():
    previous = result()
    current = result(
        current=ProcessState.CONTINUATION_ALIVE,
        previous=ProcessState.UNKNOWN,
        transition=ProcessTransition.CHANGED,
        supporting=[item()],
        reasons=(),
        runtime_event_id="event-2",
    )
    current.validate_previous_evidence(previous)
    replacement = result(episode_id="episode-2")
    with pytest.raises(ValueError, match="Episode boundary"):
        replacement.validate_previous_evidence(previous)
    with pytest.raises(ValueError, match="requires previous Process evidence"):
        current.validate_previous_evidence(None)


def test_replacement_episode_starts_without_inherited_previous_evidence():
    replacement = result(episode_id="episode-2")
    replacement.validate_previous_evidence(None)
    assert replacement.previous_process_state is None
    assert replacement.detected_transition is ProcessTransition.INITIAL


def test_contract_has_no_runtime_lifecycle_classifier_or_trading_output_fields():
    fields = set(ProcessEvidence.__dataclass_fields__)
    assert not fields & {
        "runtime_status", "episode_status", "classification_rule", "threshold",
        "trade_recommendation", "direction", "entry", "stop", "target", "position_size",
    }
