from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.contextual_attention_test_v1 import (
    CONTRACT_VERSION, ExperimentJournal, P_PRICE_LED, P_PRICE_VOLUME_CONTEXT,
    P_PRICE_VOLUME_SEQUENCE, TEST_ATTENTION_NO, TEST_ATTENTION_YES,
    evaluate_stage_b_record,
)

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def stage_b(**overrides):
    value = {
        "symbol": "ALPHAUSDT", "observation_timestamp": NOW.isoformat(),
        "market_data_timestamp": NOW.isoformat(),
        "latest_closed_1m_timestamp": NOW.isoformat(),
        "market_venue": "BYBIT", "data_transport_provider": "bybit_public_rest",
        "metric_provider": None, "timeframe": "1m", "native_or_reconstructed": "NATIVE",
        "top50_status": "NON_TOP50_ELIGIBLE", "rank_freshness_state": "FRESH",
        "trading_universe_eligible": True,
        "price_activity_state": "ACTIVITY_OBSERVED", "volume_activity_state": "CONTEXT_AVAILABLE",
        "optional_oi_activity_state": "NOT_REQUESTED",
        "evidence_completeness": "COMPLETE_FOR_STAGE_B_SKELETON",
        "source_evidence_identity": "evidence-1", "source_stage_a_identity": "stage-a-1",
    }
    value.update(overrides)
    return value


class ContextualAttentionTests(unittest.TestCase):
    def test_price_led_positive_and_no_activity(self):
        yes = evaluate_stage_b_record(stage_b(), P_PRICE_LED, decision_timestamp=NOW)
        no = evaluate_stage_b_record(stage_b(price_activity_state="NO_ACTIVITY_OBSERVED"), P_PRICE_LED, decision_timestamp=NOW)
        self.assertEqual(yes["decision_state"], TEST_ATTENTION_YES)
        self.assertEqual(no["decision_state"], TEST_ATTENTION_NO)

    def test_accepted_stage_b_status_is_usable_without_duplicating_stage_a_fields(self):
        source = stage_b(status="CONTEXT_AVAILABLE")
        for key in ("trading_universe_eligible", "top50_status", "rank_freshness_state"):
            source.pop(key)
        out = evaluate_stage_b_record(source, P_PRICE_LED, decision_timestamp=NOW)
        self.assertEqual(out["decision_state"], TEST_ATTENTION_YES)

    def test_quality_and_eligibility_fail_closed(self):
        for changes in ({"evidence_completeness": "STALE"}, {"rank_freshness_state": "STALE"}, {"top50_status": "PLUMBING_ONLY", "trading_universe_eligible": False}):
            with self.subTest(changes=changes):
                out = evaluate_stage_b_record(stage_b(**changes), P_PRICE_LED, decision_timestamp=NOW)
                self.assertIn(out["decision_state"], {"INSUFFICIENT_EVIDENCE", "DEFERRED_DATA_QUALITY"})

    def test_volume_optional_and_oi_nonblocking(self):
        out = evaluate_stage_b_record(stage_b(volume_activity_state="INSUFFICIENT_EVIDENCE", optional_oi_activity_state="NOT_AVAILABLE"), P_PRICE_VOLUME_CONTEXT, decision_timestamp=NOW)
        self.assertEqual(out["decision_state"], TEST_ATTENTION_YES)
        self.assertEqual(out["volume_evidence_state"], "INSUFFICIENT_EVIDENCE")

    def test_sequence_without_context_is_safe_and_with_context_maps(self):
        missing = evaluate_stage_b_record(stage_b(), P_PRICE_VOLUME_SEQUENCE, decision_timestamp=NOW)
        present = evaluate_stage_b_record(stage_b(sequence_activity_state="ACTIVITY_OBSERVED"), P_PRICE_VOLUME_SEQUENCE, decision_timestamp=NOW)
        self.assertEqual(missing["decision_state"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(present["decision_state"], TEST_ATTENTION_YES)

    def test_provenance_and_timestamps_preserved(self):
        out = evaluate_stage_b_record(stage_b(), P_PRICE_LED, decision_timestamp=NOW)
        self.assertEqual(out["provenance"]["market_venue"], "BYBIT")
        self.assertEqual(out["provenance"]["native_or_reconstructed"], "NATIVE")
        self.assertEqual(out["market_data_timestamp"], NOW.isoformat())
        self.assertEqual(out["stage_b_observation_timestamp"], NOW.isoformat())
        self.assertEqual(out["decision_timestamp"], NOW.isoformat())
        self.assertEqual(out["experiment_contract_version"], CONTRACT_VERSION)
        self.assertTrue(out["test_only"])

    def test_missing_provenance_and_forbidden_authority_fields(self):
        out = evaluate_stage_b_record(stage_b(native_or_reconstructed="UNKNOWN"), P_PRICE_LED, decision_timestamp=NOW)
        self.assertEqual(out["decision_state"], "INSUFFICIENT_EVIDENCE")
        for field in ("trade_permission", "entry", "setup", "true_pump_acceptance", "attention_band", "ranking", "h2_state", "baseline_confirmation_state"):
            self.assertNotIn(field, out)

    def test_no_numerical_thresholds_or_ranking(self):
        out = evaluate_stage_b_record(stage_b(), P_PRICE_VOLUME_CONTEXT, decision_timestamp=NOW)
        self.assertNotIn("score", out); self.assertNotIn("probability", out); self.assertNotIn("ordinal_rank", out)

    def test_journal_is_append_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExperimentJournal(Path(directory) / "records.jsonl")
            record = evaluate_stage_b_record(stage_b(), P_PRICE_LED, decision_timestamp=NOW)
            self.assertEqual(journal.append(record), "APPENDED")
            self.assertEqual(journal.append(record), "DUPLICATE_NOOP")
            later = evaluate_stage_b_record(stage_b(source_evidence_identity="evidence-2"), P_PRICE_LED, decision_timestamp=NOW.replace(minute=1))
            self.assertEqual(journal.append(later), "APPENDED")
            self.assertEqual(len(journal.records()), 2)
            self.assertEqual(journal.records()[0]["decision_timestamp"], NOW.isoformat())


if __name__ == "__main__":
    unittest.main()
