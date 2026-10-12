from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pumpagent.live_data.semi_autonomous_collection import (
    _run_with_retry,
    checkpoint_reached,
    session_lock,
)


class SemiAutonomousCollectionTests(unittest.TestCase):
    def test_checkpoint_requires_all_frozen_conditions(self):
        self.assertFalse(checkpoint_reached({"independently_reviewable_episodes": 12, "unique_non_top50_symbols": 10, "calendar_days_covered": 13}))
        self.assertTrue(checkpoint_reached({"independently_reviewable_episodes": 12, "unique_non_top50_symbols": 10, "calendar_days_covered": 14}))

    def test_lock_prevents_overlapping_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector.lock"
            with session_lock(path) as first:
                self.assertTrue(first)
                with session_lock(path) as second:
                    self.assertFalse(second)

    def test_provider_failure_retries_and_next_attempt_can_recover(self):
        success = {
            "session_id": "S", "scheduled_timestamp": "x", "actual_start_timestamp": "x",
            "end_timestamp": "x", "records_appended": 0, "duplicate_noop": 0,
            "provider_failures": 0, "decision_counts": {}, "new_episode_count": 0,
            "checkpoint_reached": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "pumpagent.live_data.semi_autonomous_collection._run_live_cycle",
                side_effect=[RuntimeError("temporary"), success],
            ) as live:
                result = _run_with_retry(Path(directory), Path(directory), "S")
        self.assertEqual(live.call_count, 2)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["attempt_count"], 2)


if __name__ == "__main__":
    unittest.main()
