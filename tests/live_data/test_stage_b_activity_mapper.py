from datetime import datetime, timedelta, timezone
import unittest

from pumpagent.live_data.contextual_attention_test_v1 import (
    P_PRICE_LED,
    P_PRICE_VOLUME_CONTEXT,
    P_PRICE_VOLUME_SEQUENCE,
    TEST_ATTENTION_NO,
    TEST_ATTENTION_YES,
    evaluate_stage_b_record,
)
from pumpagent.live_data.scanner_funnel_stage_b import evaluate_stage_a_record
from pumpagent.live_data.stage_b_activity_mapper import (
    ACTIVITY_OBSERVED,
    INSUFFICIENT_EVIDENCE,
    NO_ACTIVITY_OBSERVED,
    map_closed_native_1m_activity,
)


NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def stage_a(**overrides):
    value = {
        "symbol": "ALPHAUSDT", "scanner_cycle_timestamp": NOW.isoformat(),
        "market_data_timestamp": NOW.isoformat(),
        "latest_closed_1m_timestamp": (NOW - timedelta(minutes=1)).isoformat(),
        "market_venue": "BYBIT", "data_transport_provider": "bybit_public_rest",
        "timeframe": "1", "native_or_reconstructed": "NATIVE",
        "top50_status": "NON_TOP50_ELIGIBLE", "rank_freshness_state": "FRESH",
        "trading_universe_eligible": True, "closed_1m_cache_status": "READY",
        "data_gap_state": "CONTIGUOUS",
    }
    value.update(overrides)
    return value


def bars(*, positive: bool) -> list[dict[str, object]]:
    result = []
    for index in range(121):
        close = 100.0 + (index % 2)
        volume = 10.0 + (index % 2)
        result.append({
            "timestamp": (NOW - timedelta(minutes=121 - index)).isoformat(),
            "open": close - 0.25, "high": close + 0.5, "low": close - 0.5,
            "close": close, "volume": volume, "closed": True,
            "native_or_reconstructed": "NATIVE",
        })
    result[-1]["close"] = 110.0 if positive else 101.0
    result[-1]["volume"] = 30.0 if positive else 11.0
    return result


class StageBActivityMapperTests(unittest.TestCase):
    def test_complete_valid_context_positive_frozen_evidence_maps_to_activity(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=bars(positive=True))
        self.assertEqual(result["price_activity_state"], ACTIVITY_OBSERVED)
        self.assertEqual(result["activity_evaluation_state"], ACTIVITY_OBSERVED)
        self.assertEqual(
            result["activity_semantic_profile_id"],
            "automatic_anomaly_test_only_median_mad_structure_a_v1",
        )

    def test_complete_valid_context_completed_no_positive_maps_to_no_activity(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=bars(positive=False))
        self.assertEqual(result["price_activity_state"], NO_ACTIVITY_OBSERVED)
        self.assertEqual(result["activity_evaluation_state"], NO_ACTIVITY_OBSERVED)

    def test_insufficient_warmup_is_not_no_activity(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=bars(positive=False)[:120])
        self.assertEqual(result["price_activity_state"], INSUFFICIENT_EVIDENCE)
        self.assertNotEqual(result["price_activity_state"], NO_ACTIVITY_OBSERVED)

    def test_missing_malformed_stale_and_non_native_are_not_no_activity(self):
        cases = (
            (stage_a(), []),
            (stage_a(), [{**bars(positive=False)[0], "close": None}]),
            (stage_a(closed_1m_cache_status="STALE"), bars(positive=False)),
            (stage_a(native_or_reconstructed="RECONSTRUCTED"), bars(positive=False)),
        )
        for source, context in cases:
            with self.subTest(source=source):
                result = evaluate_stage_a_record(source, closed_1m_bars=context)
                self.assertNotEqual(result["price_activity_state"], NO_ACTIVITY_OBSERVED)
                self.assertIn(result["price_activity_state"], {"NOT_EVALUATED", INSUFFICIENT_EVIDENCE})

    def test_direct_mapper_invalid_context_fails_closed(self):
        mapped = map_closed_native_1m_activity(
            symbol="ALPHAUSDT", bars=[{"timestamp": NOW.isoformat()}],
            observation_timestamp=NOW,
        )
        self.assertEqual(mapped["price_activity_state"], INSUFFICIENT_EVIDENCE)

    def test_evaluator_receives_mapper_state_for_yes_no_and_volume_context(self):
        yes_stage_b = evaluate_stage_a_record(stage_a(), closed_1m_bars=bars(positive=True))
        no_stage_b = evaluate_stage_a_record(stage_a(), closed_1m_bars=bars(positive=False))
        for record in (yes_stage_b, no_stage_b):
            record.update({
                "top50_status": "NON_TOP50_ELIGIBLE",
                "rank_freshness_state": "FRESH",
                "trading_universe_eligible": True,
            })
        self.assertEqual(
            evaluate_stage_b_record(yes_stage_b, P_PRICE_LED, decision_timestamp=NOW)["decision_state"],
            TEST_ATTENTION_YES,
        )
        self.assertEqual(
            evaluate_stage_b_record(no_stage_b, P_PRICE_LED, decision_timestamp=NOW)["decision_state"],
            TEST_ATTENTION_NO,
        )
        volume_context = evaluate_stage_b_record(
            yes_stage_b, P_PRICE_VOLUME_CONTEXT, decision_timestamp=NOW,
        )
        self.assertEqual(volume_context["decision_state"], TEST_ATTENTION_YES)
        self.assertEqual(volume_context["volume_evidence_state"], "CONTEXT_AVAILABLE")
        self.assertEqual(
            evaluate_stage_b_record(yes_stage_b, P_PRICE_VOLUME_SEQUENCE, decision_timestamp=NOW)["decision_state"],
            "INSUFFICIENT_EVIDENCE",
        )

    def test_mapper_has_no_new_activity_parameter(self):
        mapped = map_closed_native_1m_activity(
            symbol="ALPHAUSDT", bars=bars(positive=True), observation_timestamp=NOW,
        )
        self.assertEqual(set(mapped), {
            "price_activity_state", "activity_evaluation_state",
            "activity_evaluation_reason", "activity_semantic_profile_id",
        })


if __name__ == "__main__":
    unittest.main()
