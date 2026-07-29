"""Tests for the Observation Episode domain foundation."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import unittest

from pumpagent.runtime.domain.enums import ObservationEpisodeStatus
from pumpagent.runtime.domain.observation_episode import (
    OBSERVATION_EPISODE_SCHEMA_VERSION,
    ObservationEpisode,
    generate_episode_id,
)


OPENED = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
TRIGGERED = datetime(2026, 7, 15, 11, 59, tzinfo=timezone.utc)


def make_episode(**overrides: object) -> ObservationEpisode:
    values: dict[str, object] = {
        "episode_id": generate_episode_id("bybit", "BTCUSDT", "5m", OPENED),
        "exchange": "bybit",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "opening_timestamp": OPENED,
        "status": ObservationEpisodeStatus.ACTIVE,
        "scanner_trigger_timestamp": TRIGGERED,
        "trigger_reasons": ["volume_growth", "open_interest_growth"],
        "trigger_metrics": {"volume_ratio": 2.4, "nested": {"scores": [1, 2]}},
    }
    values.update(overrides)
    return ObservationEpisode(**values)  # type: ignore[arg-type]


class ObservationEpisodeTests(unittest.TestCase):
    def test_valid_active_episode(self) -> None:
        episode = make_episode()
        self.assertEqual(episode.status, ObservationEpisodeStatus.ACTIVE)
        self.assertIsNone(episode.latest_accepted_candle_timestamp)
        self.assertEqual(episode.observation_cycle_count, 0)

    def test_valid_closed_episode(self) -> None:
        closed = OPENED + timedelta(minutes=25)
        episode = make_episode(
            status=ObservationEpisodeStatus.CLOSED,
            closing_timestamp=closed,
            closure_reason="market context no longer relevant",
            latest_accepted_candle_timestamp=closed,
            observation_cycle_count=5,
        )
        self.assertEqual(episode.closing_timestamp, closed)

    def test_episode_id_is_deterministic_and_canonicalizes_market_text(self) -> None:
        first = generate_episode_id(" Bybit ", "btcusdt", "5M", OPENED)
        second = generate_episode_id("bybit", "BTCUSDT", "5m", OPENED)
        self.assertEqual(first, second)

        equivalent_instant = OPENED.astimezone(timezone(timedelta(hours=2)))
        self.assertEqual(
            second,
            generate_episode_id("bybit", "BTCUSDT", "5m", equivalent_instant),
        )

    def test_opening_timestamp_distinguishes_episodes_for_same_market(self) -> None:
        later = OPENED + timedelta(minutes=5)
        self.assertNotEqual(
            generate_episode_id("bybit", "BTCUSDT", "5m", OPENED),
            generate_episode_id("bybit", "BTCUSDT", "5m", later),
        )

    def test_episode_and_nested_diagnostics_are_immutable(self) -> None:
        episode = make_episode()
        with self.assertRaises(FrozenInstanceError):
            episode.symbol = "ETHUSDT"  # type: ignore[misc]
        self.assertIsInstance(episode.trigger_reasons, tuple)
        self.assertIsInstance(episode.trigger_metrics["nested"]["scores"], tuple)
        with self.assertRaises(TypeError):
            episode.trigger_metrics["new"] = 1  # type: ignore[index]
        with self.assertRaises(TypeError):
            episode.trigger_metrics["nested"]["scores"][0] = 9  # type: ignore[index]

    def test_serialization_returns_plain_primitives(self) -> None:
        serialized = make_episode().to_dict()
        self.assertEqual(serialized["status"], "active")
        self.assertEqual(serialized["opening_timestamp"], OPENED.isoformat())
        self.assertEqual(
            serialized["trigger_reasons"],
            ["volume_growth", "open_interest_growth"],
        )
        self.assertEqual(serialized["trigger_metrics"]["nested"]["scores"], [1, 2])
        self.assertEqual(serialized["schema_version"], OBSERVATION_EPISODE_SCHEMA_VERSION)

    def test_empty_market_identity_is_rejected(self) -> None:
        for field_name in ("exchange", "symbol", "timeframe"):
            with self.subTest(field_name=field_name), self.assertRaises(ValueError):
                make_episode(**{field_name: " "})

    def test_negative_observation_count_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            make_episode(observation_cycle_count=-1)

    def test_active_episode_with_closing_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            make_episode(closing_timestamp=OPENED + timedelta(minutes=5))

    def test_closed_episode_without_closing_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            make_episode(
                status=ObservationEpisodeStatus.CLOSED,
                closure_reason="context ended",
            )

    def test_closed_episode_without_closure_reason_is_rejected(self) -> None:
        for reason in (None, " "):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                make_episode(
                    status=ObservationEpisodeStatus.CLOSED,
                    closing_timestamp=OPENED + timedelta(minutes=5),
                    closure_reason=reason,
                )

    def test_naive_timestamps_are_rejected(self) -> None:
        naive = datetime(2026, 7, 15, 12, 0)
        for field_name in (
            "opening_timestamp",
            "scanner_trigger_timestamp",
            "closing_timestamp",
            "latest_accepted_candle_timestamp",
        ):
            overrides: dict[str, object] = {field_name: naive}
            if field_name == "closing_timestamp":
                overrides.update(
                    status=ObservationEpisodeStatus.CLOSED,
                    closure_reason="context ended",
                )
            with self.subTest(field_name=field_name), self.assertRaises(ValueError):
                make_episode(**overrides)


if __name__ == "__main__":
    unittest.main()
