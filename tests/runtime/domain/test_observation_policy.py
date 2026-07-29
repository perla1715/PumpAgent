"""Tests for the pure Observation Policy transition contract."""

from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
import unittest

from pumpagent.runtime.domain.base import FrozenDict
from pumpagent.runtime.domain.enums import (
    DataQualityStatus,
    ObservationLifecycleDecision,
    ObservationTriggerRelation,
)
from pumpagent.runtime.domain.observation_episode import (
    ObservationEpisodeIdentity,
    generate_episode_id,
)
from pumpagent.runtime.domain.observation_policy import (
    OBSERVATION_POLICY_DECISION_SCHEMA_VERSION,
    ObservationMarketIdentity,
    ObservationPolicyContext,
    ObservationPolicyDecision,
    ObservationRequest,
    evaluate_observation_policy,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
TRIGGERED = NOW - timedelta(minutes=1)
OPENED = NOW - timedelta(minutes=20)


def make_request(**overrides: object) -> ObservationRequest:
    values: dict[str, object] = {
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "request_timestamp": NOW,
        "trigger_timestamp": TRIGGERED,
        "trigger_reasons": ["volume_growth"],
        "trigger_metrics": {"volume_ratio": 2.4, "nested": {"values": [1, 2]}},
        "data_quality_status": DataQualityStatus.VALID,
        "eligible": True,
        "triggering_closed_candle_timestamp": TRIGGERED,
    }
    values.update(overrides)
    return ObservationRequest(**values)  # type: ignore[arg-type]


def active_episode(**overrides: object) -> ObservationEpisodeIdentity:
    values: dict[str, object] = {
        "episode_id": generate_episode_id("bybit", "BTCUSDT", "5m", OPENED),
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "opening_timestamp": OPENED,
    }
    values.update(overrides)
    return ObservationEpisodeIdentity(**values)  # type: ignore[arg-type]


def active_context(**overrides: object) -> ObservationPolicyContext:
    values: dict[str, object] = {
        "active_episode": active_episode(),
        "trigger_relation": ObservationTriggerRelation.NEWER,
    }
    values.update(overrides)
    return ObservationPolicyContext(**values)  # type: ignore[arg-type]


class ObservationPolicyTests(unittest.TestCase):
    def test_valid_request_opens_without_active_episode(self) -> None:
        result = evaluate_observation_policy(make_request(), ObservationPolicyContext())
        self.assertEqual(result.decision, ObservationLifecycleDecision.OPEN)
        self.assertTrue(result.create_new_episode)
        self.assertFalse(result.close_active_episode_first)

    def test_invalid_or_ineligible_request_produces_no_action(self) -> None:
        requests = (
            make_request(data_quality_status=DataQualityStatus.CORRUPTED),
            make_request(eligible=False),
        )
        for request in requests:
            with self.subTest(request=request):
                result = evaluate_observation_policy(request, ObservationPolicyContext())
                self.assertEqual(result.decision, ObservationLifecycleDecision.NO_ACTION)
                self.assertFalse(result.create_new_episode)

    def test_newer_request_continues_same_active_episode(self) -> None:
        result = evaluate_observation_policy(make_request(), active_context())
        self.assertEqual(result.decision, ObservationLifecycleDecision.CONTINUE)
        self.assertEqual(result.active_episode_id, active_episode().episode_id)
        self.assertTrue(result.associate_with_active_episode)
        self.assertFalse(result.create_new_episode)

    def test_duplicate_and_older_requests_produce_no_action(self) -> None:
        for relation in (
            ObservationTriggerRelation.DUPLICATE,
            ObservationTriggerRelation.OLDER,
        ):
            with self.subTest(relation=relation):
                result = evaluate_observation_policy(
                    make_request(), active_context(trigger_relation=relation)
                )
                self.assertEqual(result.decision, ObservationLifecycleDecision.NO_ACTION)
                self.assertFalse(result.associate_with_active_episode)

    def test_explicit_replacement_closes_first_and_opens_new(self) -> None:
        result = evaluate_observation_policy(
            make_request(),
            active_context(
                replacement_requested=True,
                closure_reason="upstream lifecycle boundary",
            ),
        )
        self.assertEqual(result.decision, ObservationLifecycleDecision.REPLACE)
        self.assertTrue(result.close_active_episode_first)
        self.assertTrue(result.create_new_episode)
        self.assertEqual(result.closure_reason, "upstream lifecycle boundary")

    def test_replacement_trigger_is_not_associated_with_old_episode(self) -> None:
        result = evaluate_observation_policy(
            make_request(),
            active_context(
                replacement_requested=True,
                closure_reason="explicit replacement",
            ),
        )
        self.assertFalse(result.associate_with_active_episode)

    def test_explicit_closure_closes_without_opening(self) -> None:
        result = evaluate_observation_policy(
            make_request(),
            active_context(
                closure_requested=True,
                closure_reason="observation no longer meaningful",
            ),
        )
        self.assertEqual(result.decision, ObservationLifecycleDecision.CLOSE)
        self.assertTrue(result.close_active_episode_first)
        self.assertFalse(result.create_new_episode)
        self.assertFalse(result.associate_with_active_episode)

    def test_closure_and_replacement_without_reason_are_rejected(self) -> None:
        for field_name in ("closure_requested", "replacement_requested"):
            for reason in (None, " "):
                with self.subTest(field_name=field_name, reason=reason):
                    with self.assertRaises(ValueError):
                        active_context(**{field_name: True, "closure_reason": reason})

    def test_different_market_never_continues_or_replaces_active_episode(self) -> None:
        request = make_request(symbol="ETHUSDT")
        contexts = (
            active_context(),
            active_context(
                replacement_requested=True,
                closure_reason="explicit replacement",
            ),
        )
        for context in contexts:
            with self.subTest(context=context):
                result = evaluate_observation_policy(request, context)
                self.assertEqual(result.decision, ObservationLifecycleDecision.NO_ACTION)
                self.assertFalse(result.create_new_episode)
                self.assertFalse(result.close_active_episode_first)

    def test_identical_inputs_produce_identical_output(self) -> None:
        request = make_request()
        context = active_context()
        self.assertEqual(
            evaluate_observation_policy(request, context),
            evaluate_observation_policy(request, context),
        )

    def test_contracts_and_nested_request_values_are_immutable(self) -> None:
        request = make_request()
        context = active_context()
        result = evaluate_observation_policy(request, context)
        for instance, field_name, value in (
            (request, "symbol", "ETHUSDT"),
            (context, "closure_requested", True),
            (result, "decision_reason", "changed"),
        ):
            with self.subTest(instance=type(instance).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(instance, field_name, value)

        self.assertIsInstance(request.trigger_reasons, tuple)
        self.assertIsInstance(request.trigger_metrics, FrozenDict)
        self.assertIsInstance(request.trigger_metrics["nested"], FrozenDict)
        self.assertIsInstance(request.trigger_metrics["nested"]["values"], tuple)
        with self.assertRaises(TypeError):
            request.trigger_metrics["nested"]["values"][0] = 9  # type: ignore[index]

    def test_request_and_decision_serialize_to_plain_primitives(self) -> None:
        request_data = make_request().to_dict()
        self.assertEqual(request_data["data_quality_status"], "valid")
        self.assertEqual(request_data["trigger_metrics"]["nested"]["values"], [1, 2])
        self.assertEqual(request_data["request_timestamp"], NOW.isoformat())

        result_data = evaluate_observation_policy(
            make_request(), ObservationPolicyContext()
        ).to_dict()
        self.assertEqual(result_data["decision"], "open")
        self.assertEqual(result_data["incoming_market_identity"]["symbol"], "BTCUSDT")
        self.assertEqual(
            result_data["schema_version"],
            OBSERVATION_POLICY_DECISION_SCHEMA_VERSION,
        )

    def test_naive_timestamps_are_rejected(self) -> None:
        naive = datetime(2026, 7, 15, 12, 0)
        for field_name in (
            "request_timestamp",
            "trigger_timestamp",
            "triggering_closed_candle_timestamp",
        ):
            with self.subTest(field_name=field_name), self.assertRaises(ValueError):
                make_request(**{field_name: naive})

        with self.assertRaises(ValueError):
            ObservationPolicyDecision(
                decision=ObservationLifecycleDecision.OPEN,
                decision_reason="eligible request",
                incoming_market_identity=ObservationMarketIdentity(
                    "bybit", "BTCUSDT", "5m"
                ),
                request_timestamp=naive,
                create_new_episode=True,
            )

    def test_analytical_states_do_not_appear_in_lifecycle_contracts(self) -> None:
        forbidden = {
            "unknown",
            "continuation_alive",
            "weakening",
            "long",
            "short",
            "confidence",
            "process_state",
            "agent_state",
            "entry_permission",
            "trading_recommendation",
        }
        contract_names = {
            field.name.lower()
            for contract in (
                ObservationRequest,
                ObservationPolicyContext,
                ObservationPolicyDecision,
            )
            for field in fields(contract)
        }
        decision_values = {item.value for item in ObservationLifecycleDecision}
        self.assertTrue(forbidden.isdisjoint(contract_names))
        self.assertTrue(forbidden.isdisjoint(decision_values))

    def test_invalid_decision_combinations_are_rejected(self) -> None:
        identity = ObservationMarketIdentity("bybit", "BTCUSDT", "5m")
        with self.assertRaises(ValueError):
            ObservationPolicyDecision(
                decision=ObservationLifecycleDecision.NO_ACTION,
                decision_reason="ignored",
                incoming_market_identity=identity,
                request_timestamp=NOW,
                create_new_episode=True,
            )
        with self.assertRaises(ValueError):
            ObservationPolicyDecision(
                decision=ObservationLifecycleDecision.CONTINUE,
                decision_reason="new evidence",
                incoming_market_identity=identity,
                request_timestamp=NOW,
            )


if __name__ == "__main__":
    unittest.main()
