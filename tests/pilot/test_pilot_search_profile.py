from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from pumpagent.live_data.adapters.market_cap_rank import RankEntry, RankSnapshot
from pumpagent.pilot.pilot_search_profile import (
    CandidateInput, PilotAdmissionEvidenceJournal, PilotEvidenceError,
    PriceObservation, evaluate_candidate, select_candidate, validate_case_manifest_boundary,
)

NOW = datetime(2026, 10, 18, 12, 20, tzinfo=timezone.utc)
DIGEST = "a" * 64


def candidate(symbol="ALPHAUSDT", prices=(100, 105), *, rank=75, times=None):
    values = tuple(prices)
    timestamps = times or tuple(NOW - timedelta(minutes=(len(values) - 1 - i) * 5) for i in range(len(values)))
    return CandidateInput(
        symbol=symbol, provider_symbol_literal=symbol + ".P",
        observations=tuple(PriceObservation(ts, float(price)) for ts, price in zip(timestamps, values)),
        evaluation_timestamp_utc=NOW,
        rank_snapshot=RankSnapshot("TEST", NOW, (RankEntry(symbol[:-4], symbol[:-4], rank),), "rank-snapshot"),
        instrument_metadata={"market_type": "linear_perpetual", "contract_type": "perpetual", "quote_asset": "USDT", "price_unit": "USDT"},
        raw_evidence_ids=("re1:fixture",), raw_evidence_digests=(DIGEST,),
    )


class PilotSearchProfileTests(unittest.TestCase):
    def test_a_pump_below_five_rejects(self):
        result = evaluate_candidate(candidate(prices=(100, 104.99)))
        self.assertFalse(result.p1_pass); self.assertFalse(result.qualifies)

    def test_b_exactly_five_qualifies(self):
        result = evaluate_candidate(candidate(prices=(100, 105)))
        self.assertTrue(result.p1_pass); self.assertTrue(result.qualifies)

    def test_c_above_five_qualifies_subject_to_other_rules(self):
        result = evaluate_candidate(candidate(prices=(100, 106, 105)))
        self.assertTrue(result.p1_pass); self.assertTrue(result.qualifies)

    def test_d_p4_exactly_five_qualifies(self):
        result = evaluate_candidate(candidate(prices=(100, 110, 104.5)))
        self.assertAlmostEqual(result.distance_from_high_percent, 5.0); self.assertTrue(result.p4_pass)

    def test_e_p4_above_five_rejects(self):
        result = evaluate_candidate(candidate(prices=(100, 110, 104.49)))
        self.assertFalse(result.p4_pass); self.assertFalse(result.qualifies)

    def test_f_p5_exactly_five_qualifies(self):
        result = evaluate_candidate(candidate(prices=(100, 105, 99.75, 104)))
        self.assertAlmostEqual(result.max_running_high_pullback_percent, 5.0); self.assertTrue(result.p5_pass)

    def test_g_p5_above_five_rejects(self):
        result = evaluate_candidate(candidate(prices=(100, 105, 99.74, 104)))
        self.assertFalse(result.p5_pass); self.assertFalse(result.qualifies)

    def test_inner_low_is_selected_as_largest_causal_pump_start(self):
        result = evaluate_candidate(candidate(prices=(100, 95, 101, 104)))
        self.assertEqual(result.start_price, 95.0)
        self.assertEqual(result.local_high, 104.0)
        self.assertAlmostEqual(result.pump_percent, (104.0 - 95.0) / 95.0 * 100.0)
        self.assertTrue(result.p1_pass)

    def test_later_low_cannot_start_an_earlier_high(self):
        result = evaluate_candidate(candidate(prices=(100, 110, 90, 95)))
        self.assertEqual(result.start_price, 100.0)
        self.assertEqual(result.local_high, 110.0)
        self.assertAlmostEqual(result.pump_percent, 10.0)

    def test_largest_pair_tie_uses_first_chronological_pair(self):
        item = candidate(prices=(100, 110, 100, 110))
        result = evaluate_candidate(item)
        self.assertEqual(result.start_timestamp_utc, item.observations[0].timestamp_utc)
        self.assertEqual(result.local_high_timestamp_utc, item.observations[1].timestamp_utc)
        self.assertAlmostEqual(result.pump_percent, 10.0)

    def test_h_largest_qualified_pump_wins(self):
        smaller = candidate("ALPHAUSDT", (100, 106))
        larger = candidate("BETAUSDT", (100, 110))
        self.assertEqual(select_candidate((smaller, larger)).winner.candidate.symbol, "BETAUSDT")

    def test_i_larger_pump_failing_p5_cannot_win(self):
        valid = candidate("ALPHAUSDT", (100, 106))
        invalid = candidate("BETAUSDT", (100, 120, 110, 120))
        result = select_candidate((invalid, valid))
        self.assertEqual(result.winner.candidate.symbol, "ALPHAUSDT")

    def test_j_top50_is_excluded(self):
        result = evaluate_candidate(candidate("BTCUSDT", (100, 110), rank=50))
        self.assertEqual(result.top50_status, "EXCLUDED_TOP50"); self.assertFalse(result.qualifies)

    def test_k_unresolved_rank_fails_closed_without_substitution(self):
        item = candidate("UNKNOWNUSDT", (100, 110))
        item = CandidateInput(**{**item.__dict__, "rank_snapshot": RankSnapshot("TEST", NOW, (), "rank-snapshot")})
        result = select_candidate((item, candidate("ALPHAUSDT", (100, 106))))
        self.assertEqual(result.evaluations[0].top50_status, "TOP50_STATUS_UNRESOLVED")
        self.assertEqual(result.winner.candidate.symbol, "ALPHAUSDT")

    def test_l_evidence_serialization_is_deterministic(self):
        selection = select_candidate((candidate(prices=(100, 106)),))
        self.assertEqual(json.dumps(selection.evidence_dict(), sort_keys=True), json.dumps(selection.evidence_dict(), sort_keys=True))
        with tempfile.TemporaryDirectory() as root:
            journal = PilotAdmissionEvidenceJournal(Path(root) / "evidence")
            first = journal.append(selection)
            self.assertEqual(len(journal.verify()), 1)
            self.assertEqual(first, journal.verify()[0]["record_digest"])

    def test_m_evidence_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "evidence"
            journal = PilotAdmissionEvidenceJournal(path)
            journal.append(select_candidate((candidate(prices=(100, 106)),)))
            record = path / "00000000000000000001.json"
            record.write_text(record.read_text().replace("ALPHAUSDT", "ALTEREDUSDT"), encoding="utf-8")
            with self.assertRaises(PilotEvidenceError):
                journal.verify()

    def test_n_historical_rows_unchanged_and_o_pilot_not_started(self):
        header = "row_id,case_id,role,status\n"
        rows = "1,HNT,DEVELOPMENT,ENVELOPE_ACCEPTED\n2,BOME,DEVELOPMENT,ENVELOPE_ACCEPTED\n3,APR,DEVELOPMENT,ENVELOPE_ACCEPTED\n4,SOPH,DEVELOPMENT,ENVELOPE_ACCEPTED\n5,CHILLGUY,DEVELOPMENT,ENVELOPE_ACCEPTED\n7,PILOT_EVENT_001,LIVE_PILOT,NOT_STARTED\n"
        with tempfile.TemporaryDirectory() as root:
            manifest = Path(root) / "manifest.csv"
            manifest.write_text(header + rows, encoding="utf-8")
            before = manifest.read_bytes()
            outcome = validate_case_manifest_boundary(manifest)
            self.assertTrue(outcome["historical_rows_unchanged"]); self.assertTrue(outcome["pilot_event_not_started"])
            self.assertEqual(before, manifest.read_bytes())

    def test_only_native_1m_and_5m_allowed(self):
        base = candidate()
        with self.assertRaises(PilotEvidenceError):
            CandidateInput(**{**base.__dict__, "native_timeframes": ("1m", "3m", "5m")})


if __name__ == "__main__":
    unittest.main()
