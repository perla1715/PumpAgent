from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.live_data.domain import (
    LiveDataMode,
    LiveDataQualityStatus,
    LiveDataTransport,
    NormalizedMarketDataInput,
    SourceMetadata,
)
from pumpagent.live_data.quality import translate_quality_status
from pumpagent.runtime.domain.enums import DataQualityStatus


NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def make_input(quality_status: LiveDataQualityStatus) -> NormalizedMarketDataInput:
    metadata = SourceMetadata(
        exchange="binance",
        adapter_name="fixture_adapter",
        adapter_version="0.1",
        source_timestamp=NOW,
        receive_timestamp=NOW,
        latency_ms=15.0,
        transport=LiveDataTransport.FIXTURE,
        correlation_id="corr-1",
        source_symbol="BTCUSDT",
        normalized_symbol="BTCUSDT",
        source_timeframe="1m",
        normalized_timeframe="1m",
    )
    return NormalizedMarketDataInput(
        source_event_id="source-evt-1",
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
        source_timestamp=NOW,
        receive_timestamp=NOW,
        price=100.0,
        ohlcv=(
            {
                "timestamp": "2026-07-01T12:00:00+00:00",
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 10.0,
            },
        ),
        volume=10.0,
        data_source="fixture",
        quality_status=quality_status,
        source_metadata=metadata,
        optional_market_metrics={
            "normalizer_version": "0.1",
            "validator_version": "0.1",
        },
        quality_reasons=("quality_reason",),
        missing_fields=("open_interest",),
        validation_warnings=("optional_metric_missing",),
    )


class QualityTranslatorTests(unittest.TestCase):
    def test_good_maps_to_runtime_valid_and_allowed(self) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.GOOD),
            mode=LiveDataMode.LIVE,
        )

        self.assertEqual(result.runtime_quality_status, DataQualityStatus.VALID)
        self.assertTrue(result.allowed)
        self.assertIsNone(result.block_reason)

    def test_delayed_maps_to_runtime_delayed_and_allowed(self) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.DELAYED),
            mode=LiveDataMode.LIVE,
        )

        self.assertEqual(result.runtime_quality_status, DataQualityStatus.DELAYED)
        self.assertTrue(result.allowed)

    def test_partial_maps_to_runtime_missing_and_allowed_when_required_fields_valid(
        self,
    ) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.PARTIAL),
            mode=LiveDataMode.LIVE,
            required_fields_valid=True,
        )

        self.assertEqual(result.runtime_quality_status, DataQualityStatus.MISSING)
        self.assertTrue(result.allowed)

    def test_partial_maps_to_runtime_missing_and_blocks_when_required_fields_invalid(
        self,
    ) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.PARTIAL),
            mode=LiveDataMode.LIVE,
            required_fields_valid=False,
        )

        self.assertEqual(result.runtime_quality_status, DataQualityStatus.MISSING)
        self.assertFalse(result.allowed)
        self.assertEqual(
            result.block_reason,
            "partial_data_missing_required_fields",
        )

    def test_corrupted_maps_to_runtime_corrupted_and_blocks(self) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.CORRUPTED),
            mode=LiveDataMode.LIVE,
        )

        self.assertEqual(result.runtime_quality_status, DataQualityStatus.CORRUPTED)
        self.assertFalse(result.allowed)
        self.assertEqual(result.block_reason, "corrupted_data_blocked")

    def test_unknown_maps_to_runtime_missing_and_blocks_in_live_mode(self) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.LIVE,
        )

        self.assertEqual(result.runtime_quality_status, DataQualityStatus.MISSING)
        self.assertFalse(result.allowed)
        self.assertEqual(result.block_reason, "unknown_quality_blocked")

    def test_unknown_non_live_requires_explicit_allowance(self) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.REPLAY,
        )

        self.assertEqual(result.runtime_quality_status, DataQualityStatus.MISSING)
        self.assertFalse(result.allowed)

    def test_unknown_can_be_allowed_in_replay_when_explicitly_enabled(self) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.REPLAY,
            allow_unknown_non_live=True,
        )

        self.assertEqual(result.runtime_quality_status, DataQualityStatus.MISSING)
        self.assertTrue(result.allowed)
        self.assertIsNone(result.block_reason)

    def test_unknown_can_be_allowed_in_simulation_when_explicitly_enabled(
        self,
    ) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.SIMULATION,
            allow_unknown_non_live=True,
        )

        self.assertTrue(result.allowed)

    def test_unknown_can_be_allowed_in_testing_when_explicitly_enabled(self) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.UNKNOWN),
            mode=LiveDataMode.TESTING,
            allow_unknown_non_live=True,
        )

        self.assertTrue(result.allowed)

    def test_metadata_preservation(self) -> None:
        result = translate_quality_status(
            make_input(LiveDataQualityStatus.PARTIAL),
            mode=LiveDataMode.LIVE,
        )
        metadata = result.preserved_metadata

        self.assertEqual(metadata["quality_reasons"], ("quality_reason",))
        self.assertEqual(metadata["missing_fields"], ("open_interest",))
        self.assertEqual(
            metadata["validation_warnings"],
            ("optional_metric_missing",),
        )
        self.assertEqual(metadata["source_timestamp"], NOW)
        self.assertEqual(metadata["receive_timestamp"], NOW)
        self.assertEqual(metadata["latency_ms"], 15.0)
        self.assertEqual(metadata["adapter_name"], "fixture_adapter")
        self.assertEqual(metadata["adapter_version"], "0.1")
        self.assertEqual(metadata["normalizer_version"], "0.1")
        self.assertEqual(metadata["validator_version"], "0.1")
        self.assertEqual(metadata["transport"], "fixture")
        self.assertEqual(metadata["correlation_id"], "corr-1")


if __name__ == "__main__":
    unittest.main()
