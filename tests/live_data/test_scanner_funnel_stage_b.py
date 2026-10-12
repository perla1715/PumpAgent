from datetime import datetime, timezone, timedelta
import unittest

from pumpagent.live_data.scanner_funnel_stage_b import evaluate_stage_a_batch, evaluate_stage_a_record


NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def stage_a(**overrides):
    value = {
        "symbol": "ALPHAUSDT", "scanner_cycle_timestamp": NOW.isoformat(), "market_data_timestamp": NOW.isoformat(),
        "latest_closed_1m_timestamp": (NOW - timedelta(minutes=1)).isoformat(), "market_venue": "BYBIT",
        "data_transport_provider": "bybit_public_rest", "timeframe": "1", "native_or_reconstructed": "NATIVE",
        "top50_status": "NON_TOP50_ELIGIBLE", "rank_freshness_state": "FRESH", "trading_universe_eligible": True,
        "closed_1m_cache_status": "READY", "data_gap_state": "CONTIGUOUS",
    }
    value.update(overrides)
    return value


def bar(minute=1, **overrides):
    value = {"timestamp": (NOW - timedelta(minutes=minute)).isoformat(), "open": 1, "high": 1.1, "low": .9, "close": 1.05, "volume": 10, "closed": True, "native_or_reconstructed": "NATIVE"}
    value.update(overrides)
    return value


class StageBSkeletonTests(unittest.TestCase):
    def test_eligible_fresh_stage_a_produces_context_skeleton(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertEqual(result["status"], "CONTEXT_AVAILABLE"); self.assertEqual(result["evidence_completeness"], "COMPLETE_FOR_STAGE_B_SKELETON")

    def test_top50_plumbing_only_cannot_be_candidate(self):
        result = evaluate_stage_a_record(stage_a(top50_status="PLUMBING_ONLY", trading_universe_eligible=False), closed_1m_bars=[bar()])
        self.assertEqual(result["status"], "NOT_EVALUATED"); self.assertEqual(result["attention_candidate_state"], "NOT_EVALUATED")

    def test_unknown_or_stale_rank_fails_closed(self):
        for status in ("UNKNOWN", "STALE"):
            with self.subTest(status=status):
                result = evaluate_stage_a_record(stage_a(top50_status="UNKNOWN", rank_freshness_state=status, trading_universe_eligible=False), closed_1m_bars=[bar()])
                self.assertEqual(result["evidence_completeness"], "INSUFFICIENT_EVIDENCE")

    def test_stale_rank_inconsistent_eligible_input_fails_closed(self):
        result = evaluate_stage_a_record(stage_a(rank_freshness_state="STALE"), closed_1m_bars=[bar()])
        self.assertEqual(result["status"], "NOT_EVALUATED"); self.assertEqual(result["evidence_completeness"], "INSUFFICIENT_EVIDENCE")

    def test_missing_rank_freshness_fails_closed(self):
        value = stage_a(); value.pop("rank_freshness_state")
        result = evaluate_stage_a_record(value, closed_1m_bars=[bar()])
        self.assertEqual(result["status"], "NOT_EVALUATED")

    def test_stale_closed_bar_is_explicit(self):
        result = evaluate_stage_a_record(stage_a(closed_1m_cache_status="STALE"), closed_1m_bars=[bar(10)])
        self.assertEqual(result["status"], "STALE"); self.assertEqual(result["price_activity_state"], "NOT_EVALUATED")

    def test_missing_price_context_is_insufficient_not_negative(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[])
        self.assertEqual(result["price_activity_state"], "INSUFFICIENT_EVIDENCE"); self.assertNotEqual(result["price_activity_state"], "NO_ACTIVITY_OBSERVED")

    def test_missing_volume_context_is_explicit(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar(volume=None)])
        self.assertEqual(result["volume_activity_state"], "INSUFFICIENT_EVIDENCE")

    def test_missing_oi_does_not_reject_price_volume(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertEqual(result["status"], "CONTEXT_AVAILABLE"); self.assertEqual(result["optional_oi_activity_state"], "NOT_REQUESTED")

    def test_no_numerical_threshold_or_decision_formula(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertEqual(result["price_activity_state"], "INSUFFICIENT_EVIDENCE"); self.assertEqual(result["attention_candidate_state"], "NOT_EVALUATED")

    def test_no_universal_volume_multiplier(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar(volume=1000000)])
        self.assertEqual(result["volume_activity_state"], "CONTEXT_AVAILABLE"); self.assertEqual(result["attention_candidate_state"], "NOT_EVALUATED")

    def test_no_ranking_or_bands(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertNotIn("attention_band", result); self.assertNotIn("ordinal_rank", result); self.assertNotIn("ranking", result)

    def test_no_h2_invocation_or_state(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertNotIn("h2_state", result); self.assertNotIn("h2_first_seen_timestamp", result)

    def test_no_hybrid_baseline_invocation(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertNotIn("baseline_confirmation_state", result); self.assertNotIn("START_CONFIRMED", result)

    def test_no_tracker_wiring(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertNotIn("tracker_handoff", result); self.assertNotIn("trade_permission", result)

    def test_no_trade_entry_setup_authority(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        for field in ("trade_permission", "entry_permission", "setup_permission", "true_pump_acceptance", "can_trade"):
            self.assertNotIn(field, result)

    def test_only_completed_native_bars_are_context(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar(closed=False)])
        self.assertEqual(result["evidence_completeness"], "INSUFFICIENT_EVIDENCE")

    def test_source_native_provenance_is_preserved(self):
        result = evaluate_stage_a_record(stage_a(native_or_reconstructed="NATIVE"), closed_1m_bars=[bar()])
        self.assertEqual(result["native_or_reconstructed"], "NATIVE"); self.assertEqual(result["status"], "CONTEXT_AVAILABLE")

    def test_source_reconstructed_provenance_fails_closed_and_is_not_relabelled(self):
        result = evaluate_stage_a_record(stage_a(native_or_reconstructed="RECONSTRUCTED"), closed_1m_bars=[bar()])
        self.assertEqual(result["native_or_reconstructed"], "RECONSTRUCTED"); self.assertEqual(result["status"], "NOT_EVALUATED"); self.assertEqual(result["evidence_completeness"], "INSUFFICIENT_EVIDENCE")

    def test_source_unknown_provenance_fails_closed(self):
        value = stage_a(); value.pop("native_or_reconstructed")
        result = evaluate_stage_a_record(value, closed_1m_bars=[bar()])
        self.assertEqual(result["native_or_reconstructed"], "UNKNOWN"); self.assertEqual(result["status"], "NOT_EVALUATED")

    def test_omitted_bar_provenance_fails_closed_without_native_inference(self):
        value = bar(); value.pop("native_or_reconstructed")
        result = evaluate_stage_a_record(stage_a(native_or_reconstructed="NATIVE"), closed_1m_bars=[value])
        self.assertEqual(result["status"], "NOT_EVALUATED"); self.assertEqual(result["evidence_completeness"], "INSUFFICIENT_EVIDENCE")

    def test_malformed_stage_a_failure_does_not_emit_native(self):
        result = evaluate_stage_a_record({"symbol": ""}, closed_1m_bars=[])
        self.assertEqual(result["native_or_reconstructed"], "UNKNOWN")

    def test_source_native_with_non_native_bar_fails_closed(self):
        result = evaluate_stage_a_record(stage_a(native_or_reconstructed="NATIVE"), closed_1m_bars=[bar(native_or_reconstructed="RECONSTRUCTED")])
        self.assertEqual(result["status"], "NOT_EVALUATED"); self.assertEqual(result["native_or_reconstructed"], "NATIVE")

    def test_ticker_is_not_substituted_for_bar_context(self):
        result = evaluate_stage_a_record(stage_a(market_data_timestamp=NOW.isoformat()), closed_1m_bars=None)
        self.assertEqual(result["status"], "NOT_EVALUATED"); self.assertEqual(result["context_window_provenance"], "CLOSED_NATIVE_1M")

    def test_repeated_identical_evidence_is_idempotent(self):
        first = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()]); second = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertEqual(first["source_stage_a_identity"], second["source_stage_a_identity"]); self.assertEqual(first["source_evidence_identity"], second["source_evidence_identity"])

    def test_malformed_symbol_isolated_in_batch(self):
        results = evaluate_stage_a_batch([stage_a(symbol=""), stage_a(symbol="BETUSDT")], bars_by_symbol={"BETUSDT": [bar()]})
        self.assertEqual(len(results), 2); self.assertEqual(results[0]["status"], "INSUFFICIENT_EVIDENCE"); self.assertEqual(results[1]["status"], "CONTEXT_AVAILABLE")

    def test_provenance_survives_stage_a_to_b(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()])
        self.assertEqual(result["market_venue"], "BYBIT"); self.assertEqual(result["data_transport_provider"], "bybit_public_rest"); self.assertEqual(result["native_or_reconstructed"], "NATIVE")

    def test_data_gap_is_not_negative_activity(self):
        result = evaluate_stage_a_record(stage_a(data_gap_state="DATA_GAP"), closed_1m_bars=[bar()])
        self.assertEqual(result["status"], "DATA_GAP"); self.assertNotEqual(result["price_activity_state"], "NO_ACTIVITY_OBSERVED")

    def test_optional_oi_states_are_metadata_only(self):
        result = evaluate_stage_a_record(stage_a(), closed_1m_bars=[bar()], optional_oi={"status": "AVAILABLE"})
        self.assertEqual(result["optional_oi_activity_state"], "AVAILABLE"); self.assertEqual(result["attention_candidate_state"], "NOT_EVALUATED")


if __name__ == "__main__": unittest.main()
