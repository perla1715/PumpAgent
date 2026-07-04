from __future__ import annotations

from pathlib import Path
import sys
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain.enums import EvidenceStrength
from pumpagent.runtime.modules.evidence import (
    AggregatedEvidenceScore,
    Evidence,
    EvidenceScore,
    aggregate_evidence_score,
    build_evidence_summary,
    collect_evidence,
)


class DomainEvidence:
    def __init__(
        self,
        *,
        evidence_strength: EvidenceStrength,
        structural_score: float | None = None,
        participation_score: float | None = None,
    ) -> None:
        self.evidence_strength = evidence_strength
        self.structural_score = structural_score
        self.participation_score = participation_score


class TemporalEvidence:
    def __init__(self, *, trend: str) -> None:
        self.trend = trend


class EvidenceEngineTests(unittest.TestCase):
    def test_all_positive(self) -> None:
        evidence = collect_evidence(
            {
                "price_change_1m": 0.1,
                "volume_spike_ratio": 2.1,
                "oi_change_1m": 0.1,
            }
        )

        self.assertEqual(
            evidence,
            [
                Evidence("Price", "Price increasing", True),
                Evidence("Volume", "Volume above average", True),
                Evidence("OI", "OI increasing", True),
            ],
        )

    def test_mixed_evidence(self) -> None:
        evidence = collect_evidence(
            {
                "price_change_1m": 0.1,
                "volume_spike_ratio": 1.9,
                "oi_change_1m": 0.0,
            }
        )

        self.assertEqual(
            evidence,
            [
                Evidence("Price", "Price increasing", True),
                Evidence("Volume", "Volume not above average", False),
                Evidence("OI", "OI not increasing", False),
            ],
        )

    def test_all_negative(self) -> None:
        evidence = collect_evidence(
            {
                "price_change_1m": 0.0,
                "volume_spike_ratio": 2.0,
                "oi_change_1m": 0.0,
            }
        )

        self.assertEqual(
            evidence,
            [
                Evidence("Price", "Price not increasing", False),
                Evidence("Volume", "Volume not above average", False),
                Evidence("OI", "OI not increasing", False),
            ],
        )

    def test_empty_evidence_score(self) -> None:
        self.assertEqual(EvidenceScore.total_score(()), 0.0)

        score = aggregate_evidence_score()

        self.assertIsNone(score.structural_score)
        self.assertIsNone(score.market_score)
        self.assertIsNone(score.temporal_score)
        self.assertEqual(score.total_score, 0.0)
        self.assertEqual(score.evidence_count, 0)
        self.assertTrue(score.diagnostic_only)

    def test_single_evidence_score(self) -> None:
        score = EvidenceScore.total_score(
            (
                Evidence(
                    name="Price",
                    value="Price increasing",
                    positive=True,
                    score=0.8,
                ),
            )
        )

        self.assertEqual(score, 0.8)

    def test_multiple_evidence_score(self) -> None:
        score = EvidenceScore.total_score(
            (
                Evidence(
                    name="Price",
                    value="Price increasing",
                    positive=True,
                    score=0.8,
                    confidence=0.5,
                ),
                Evidence(
                    name="Volume",
                    value="Volume above average",
                    positive=True,
                    score=0.6,
                ),
            )
        )

        self.assertEqual(score, 0.5)

    def test_deterministic_aggregation(self) -> None:
        structural = DomainEvidence(
            evidence_strength=EvidenceStrength.WEAK,
            structural_score=0.7,
        )
        market = DomainEvidence(evidence_strength=EvidenceStrength.MODERATE)
        temporal = TemporalEvidence(trend="IMPROVING")

        first = aggregate_evidence_score(
            structural_evidence=structural,
            market_evidence=market,
            temporal_evidence=temporal,
        )
        second = aggregate_evidence_score(
            structural_evidence=structural,
            market_evidence=market,
            temporal_evidence=temporal,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.structural_score, 0.7)
        self.assertEqual(first.market_score, 0.5)
        self.assertEqual(first.temporal_score, 1.0)
        self.assertAlmostEqual(first.total_score, 0.7333333333333334)
        self.assertEqual(first.evidence_count, 3)

    def test_optional_evidence_fields(self) -> None:
        timestamp = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)

        evidence = Evidence(
            name="Temporal",
            value="Confidence improving",
            positive=True,
            source="temporal_confidence",
            timestamp=timestamp,
        )

        self.assertIsNone(evidence.score)
        self.assertIsNone(evidence.confidence)
        self.assertEqual(evidence.source, "temporal_confidence")
        self.assertEqual(evidence.timestamp, timestamp)

    def test_evidence_score_falls_back_to_signed_evidence(self) -> None:
        score = EvidenceScore.total_score(
            (
                Evidence("Price", "Price increasing", True),
                Evidence("Volume", "Volume not above average", False),
            )
        )

        self.assertEqual(score, 0.5)

    def test_empty_evidence_summary(self) -> None:
        summary = build_evidence_summary(
            aggregated_score=AggregatedEvidenceScore(
                structural_score=None,
                market_score=None,
                temporal_score=None,
                total_score=0.0,
                evidence_count=0,
            )
        )

        self.assertIsNone(summary.structural_score)
        self.assertIsNone(summary.market_score)
        self.assertIsNone(summary.temporal_score)
        self.assertEqual(summary.total_score, 0.0)
        self.assertEqual(summary.evidence_count, 0)
        self.assertIsNone(summary.strongest_evidence_type)
        self.assertIsNone(summary.weakest_evidence_type)
        self.assertFalse(summary.has_structural_evidence)
        self.assertFalse(summary.has_market_evidence)
        self.assertFalse(summary.has_temporal_evidence)

    def test_structural_only_evidence_summary(self) -> None:
        structural = DomainEvidence(
            evidence_strength=EvidenceStrength.MODERATE,
            structural_score=0.5,
        )
        score = aggregate_evidence_score(structural_evidence=structural)

        summary = build_evidence_summary(
            aggregated_score=score,
            structural_evidence=structural,
        )

        self.assertEqual(summary.structural_score, 0.5)
        self.assertIsNone(summary.market_score)
        self.assertIsNone(summary.temporal_score)
        self.assertEqual(summary.total_score, 0.5)
        self.assertEqual(summary.strongest_evidence_type, "structural")
        self.assertEqual(summary.weakest_evidence_type, "structural")
        self.assertTrue(summary.has_structural_evidence)
        self.assertFalse(summary.has_market_evidence)
        self.assertFalse(summary.has_temporal_evidence)

    def test_market_only_evidence_summary(self) -> None:
        market = DomainEvidence(
            evidence_strength=EvidenceStrength.STRONG,
            participation_score=1.0,
        )
        score = aggregate_evidence_score(market_evidence=market)

        summary = build_evidence_summary(
            aggregated_score=score,
            market_evidence=market,
        )

        self.assertEqual(summary.market_score, 1.0)
        self.assertEqual(summary.total_score, 1.0)
        self.assertEqual(summary.strongest_evidence_type, "market")
        self.assertEqual(summary.weakest_evidence_type, "market")
        self.assertFalse(summary.has_structural_evidence)
        self.assertTrue(summary.has_market_evidence)
        self.assertFalse(summary.has_temporal_evidence)

    def test_temporal_only_evidence_summary(self) -> None:
        temporal = TemporalEvidence(trend="STABLE")
        score = aggregate_evidence_score(temporal_evidence=temporal)

        summary = build_evidence_summary(
            aggregated_score=score,
            temporal_evidence=temporal,
        )

        self.assertEqual(summary.temporal_score, 0.5)
        self.assertEqual(summary.total_score, 0.5)
        self.assertEqual(summary.strongest_evidence_type, "temporal")
        self.assertEqual(summary.weakest_evidence_type, "temporal")
        self.assertFalse(summary.has_structural_evidence)
        self.assertFalse(summary.has_market_evidence)
        self.assertTrue(summary.has_temporal_evidence)

    def test_mixed_evidence_summary(self) -> None:
        structural = DomainEvidence(
            evidence_strength=EvidenceStrength.WEAK,
            structural_score=0.25,
        )
        market = DomainEvidence(
            evidence_strength=EvidenceStrength.MODERATE,
            participation_score=0.75,
        )
        temporal = TemporalEvidence(trend="STABLE")
        score = aggregate_evidence_score(
            structural_evidence=structural,
            market_evidence=market,
            temporal_evidence=temporal,
        )

        summary = build_evidence_summary(
            aggregated_score=score,
            structural_evidence=structural,
            market_evidence=market,
            temporal_evidence=temporal,
        )

        self.assertEqual(summary.structural_score, 0.25)
        self.assertEqual(summary.market_score, 0.75)
        self.assertEqual(summary.temporal_score, 0.5)
        self.assertEqual(summary.total_score, 0.5)
        self.assertEqual(summary.evidence_count, 3)
        self.assertTrue(summary.has_structural_evidence)
        self.assertTrue(summary.has_market_evidence)
        self.assertTrue(summary.has_temporal_evidence)

    def test_strongest_and_weakest_evidence_type_selection(self) -> None:
        summary = build_evidence_summary(
            aggregated_score=AggregatedEvidenceScore(
                structural_score=0.5,
                market_score=0.9,
                temporal_score=0.1,
                total_score=0.5,
                evidence_count=3,
            )
        )

        self.assertEqual(summary.strongest_evidence_type, "market")
        self.assertEqual(summary.weakest_evidence_type, "temporal")

    def test_summary_output_is_deterministic(self) -> None:
        score = AggregatedEvidenceScore(
            structural_score=0.5,
            market_score=0.5,
            temporal_score=0.5,
            total_score=0.5,
            evidence_count=3,
        )

        first = build_evidence_summary(aggregated_score=score)
        second = build_evidence_summary(aggregated_score=score)

        self.assertEqual(first, second)
        self.assertEqual(first.strongest_evidence_type, "structural")
        self.assertEqual(first.weakest_evidence_type, "structural")


if __name__ == "__main__":
    unittest.main()
