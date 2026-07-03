from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.modules.evidence import Evidence, collect_evidence


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


if __name__ == "__main__":
    unittest.main()
