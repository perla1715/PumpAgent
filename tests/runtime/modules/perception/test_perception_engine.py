from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "market_data" / "btcusdt_1m_snapshot.json"
PERCEPTION_ENGINE = SRC / "pumpagent" / "runtime" / "modules" / "perception" / "engine.py"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pumpagent.runtime.domain import (
    MarketEfficiencyEvidence,
    MarketSnapshot,
    ObservationPackage,
    RuntimeEvent,
    StructuralEvidence,
)
from pumpagent.runtime.domain.enums import DataQualityStatus
from pumpagent.runtime.modules.market_data import add_market_snapshot_from_fixture
from pumpagent.runtime.modules.perception import (
    PerceptionError,
    add_observation_package,
    add_perception_evidence,
    build_observation_package,
    build_perception_evidence,
    detect_market_state,
    format_market_state_scan_line,
)


def make_event_with_market_snapshot() -> RuntimeEvent:
    event = RuntimeEvent(
        event_id="runtime-evt-1",
        schema_version="1.0",
        cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        symbol="BTCUSDT",
        exchange="binance",
        timeframe="1m",
    )
    return add_market_snapshot_from_fixture(event, FIXTURE)


class PerceptionEngineTests(unittest.TestCase):
    def test_ignition(self) -> None:
        state = detect_market_state(
            {
                "price_change_1m": 1.1,
                "price_change_3m": 1.5,
                "volume_spike_ratio": 8.1,
                "oi_change_1m": 0.1,
            }
        )

        self.assertEqual(state, "IGNITION")

    def test_continuation_alive(self) -> None:
        state = detect_market_state(
            {
                "price_change_1m": 0.5,
                "price_change_3m": 2.1,
                "volume_spike_ratio": 2.1,
                "oi_change_1m": 0.0,
            }
        )

        self.assertEqual(state, "CONTINUATION_ALIVE")

    def test_weakening(self) -> None:
        state = detect_market_state(
            {
                "price_change_1m": 0.2,
                "price_change_3m": 0.1,
                "volume_spike_ratio": 1.9,
                "oi_change_1m": 0.0,
            }
        )

        self.assertEqual(state, "WEAKENING")

    def test_unknown(self) -> None:
        state = detect_market_state(
            {
                "price_change_1m": 0.0,
                "price_change_3m": 0.0,
                "volume_spike_ratio": 1.0,
                "oi_change_1m": 0.0,
            }
        )

        self.assertEqual(state, "UNKNOWN")

    def test_market_state_scan_line_includes_symbol_state_price_volume_and_oi(
        self,
    ) -> None:
        line = format_market_state_scan_line(
            {
                "symbol": "BTCUSDT",
                "price": 100.0,
                "volume": 42.0,
                "open_interest": 1200.5,
                "price_change_1m": 1.1,
                "price_change_3m": 1.5,
                "volume_spike_ratio": 8.1,
                "oi_change_1m": 0.1,
            }
        )

        self.assertEqual(
            line,
            "BTCUSDT | IGNITION | CONF=50% | 100.0 | 42.0 | 1200.5 "
            "| Evidence: + Price increasing; + Volume above average; "
            "+ OI increasing",
        )

    def test_perception_evidence_reads_market_snapshot(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)

        self.assertEqual(
            result.structural_evidence.technical_context["source_snapshot_event_id"],
            event.market_snapshot.event_id,
        )
        self.assertEqual(
            result.market_efficiency_evidence.market_mechanics_context[
                "source_snapshot_event_id"
            ],
            event.market_snapshot.event_id,
        )

    def test_perception_evidence_produces_runtime_evidence_contracts(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)

        self.assertIsInstance(result.structural_evidence, StructuralEvidence)
        self.assertIsInstance(
            result.market_efficiency_evidence,
            MarketEfficiencyEvidence,
        )
        self.assertEqual(result.structural_evidence.trend_structure, "not_assessed")
        self.assertEqual(
            result.market_efficiency_evidence.efficiency_status,
            "not_assessed",
        )

    def test_perception_evidence_includes_ohlcv_integrity_facts(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)
        integrity = result.structural_evidence.technical_context["ohlcv_integrity"]

        self.assertTrue(integrity["ohlcv_present"])
        self.assertEqual(integrity["candle_count"], len(event.market_snapshot.ohlcv))
        self.assertEqual(
            integrity["required_candle_fields"],
            ("timestamp", "open", "high", "low", "close", "volume"),
        )
        self.assertTrue(integrity["all_required_candle_fields_present"])
        self.assertEqual(
            integrity["latest_candle_timestamp"],
            event.market_snapshot.ohlcv[-1]["timestamp"],
        )
        self.assertEqual(integrity["malformed_candle_indexes"], ())
        self.assertEqual(integrity["missing_fields_by_candle_index"], {})

    def test_perception_evidence_includes_latest_candle_facts(self) -> None:
        event = make_event_with_market_snapshot()
        latest_candle = event.market_snapshot.ohlcv[-1]

        result = build_perception_evidence(event.market_snapshot)
        facts = result.structural_evidence.technical_context["latest_candle_facts"]

        self.assertEqual(facts["timestamp"], latest_candle["timestamp"])
        self.assertEqual(facts["open"], float(latest_candle["open"]))
        self.assertEqual(facts["high"], float(latest_candle["high"]))
        self.assertEqual(facts["low"], float(latest_candle["low"]))
        self.assertEqual(facts["close"], float(latest_candle["close"]))
        self.assertEqual(facts["volume"], float(latest_candle["volume"]))

    def test_latest_candle_facts_do_not_change_ohlcv_integrity_facts(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)
        integrity = result.structural_evidence.technical_context["ohlcv_integrity"]

        self.assertEqual(
            set(integrity),
            {
                "ohlcv_present",
                "candle_count",
                "required_candle_fields",
                "all_required_candle_fields_present",
                "latest_candle_timestamp",
                "malformed_candle_indexes",
                "missing_fields_by_candle_index",
            },
        )

    def test_latest_candle_facts_reject_invalid_numeric_field_clearly(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": "bad-open",
                    "high": 101.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 42.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(PerceptionError, "field open must be numeric"):
            build_perception_evidence(snapshot)

    def test_perception_evidence_includes_data_quality_context(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-quality-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 42.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.MISSING,
            optional_market_metrics={
                "quality_reasons": ("optional_metric_missing",),
                "validation_warnings": ("source_delay_observed",),
                "source_timestamp": "2026-07-01T12:00:00Z",
                "receive_timestamp": "2026-07-01T12:00:01Z",
                "adapter_name": "fixture_adapter",
                "adapter_version": "0.1",
                "normalizer_version": "0.1",
                "validator_version": "0.1",
                "transport": "fixture",
                "correlation_id": "corr-1",
                "source_metadata": {
                    "exchange": "binance",
                    "source_symbol": "BTCUSDT",
                    "source_timeframe": "1m",
                },
                "open_interest": 1200.5,
            },
            raw_payload_reference="fixture://quality-case",
            latency_ms=15,
            missing_fields=("funding_rate",),
        )

        result = build_perception_evidence(snapshot)
        context = result.structural_evidence.technical_context["data_quality_context"]

        self.assertEqual(context["data_quality_status"], "missing")
        self.assertEqual(context["missing_fields"], ("funding_rate",))
        self.assertEqual(context["missing_field_count"], 1)
        self.assertTrue(context["has_missing_fields"])
        self.assertEqual(context["latency_ms"], 15)
        self.assertEqual(context["raw_payload_reference"], "fixture://quality-case")
        self.assertEqual(context["data_source"], "fixture")
        self.assertEqual(context["schema_version"], snapshot.schema_version)
        self.assertFalse(context["required_snapshot_fields_present"])
        self.assertEqual(
            context["existing_quality_metadata"]["quality_reasons"],
            ("optional_metric_missing",),
        )
        self.assertEqual(
            context["existing_quality_metadata"]["validation_warnings"],
            ("source_delay_observed",),
        )
        self.assertEqual(
            context["existing_quality_metadata"]["source_metadata"]["source_symbol"],
            "BTCUSDT",
        )
        self.assertNotIn("open_interest", context["existing_quality_metadata"])

    def test_data_quality_context_is_shared_by_perception_evidence_outputs(
        self,
    ) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)
        structural_context = result.structural_evidence.technical_context[
            "data_quality_context"
        ]
        efficiency_context = (
            result.market_efficiency_evidence.market_mechanics_context[
                "data_quality_context"
            ]
        )

        self.assertEqual(efficiency_context, structural_context)

    def test_perception_evidence_includes_observed_range_facts(self) -> None:
        event = make_event_with_market_snapshot()
        candles = event.market_snapshot.ohlcv
        expected_high = max(float(candle["high"]) for candle in candles)
        expected_low = min(float(candle["low"]) for candle in candles)

        result = build_perception_evidence(event.market_snapshot)
        context = result.structural_evidence.technical_context
        facts = context["observed_range_facts"]

        self.assertEqual(facts["observed_high"], expected_high)
        self.assertEqual(facts["observed_low"], expected_low)
        self.assertEqual(facts["observed_range_size"], expected_high - expected_low)
        self.assertEqual(facts["candle_count_used"], len(candles))
        self.assertEqual(facts["first_candle_timestamp"], candles[0]["timestamp"])
        self.assertEqual(facts["last_candle_timestamp"], candles[-1]["timestamp"])

    def test_observed_range_facts_preserve_existing_range_context_fields(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)
        context = result.structural_evidence.technical_context
        facts = context["observed_range_facts"]

        self.assertEqual(context["observed_high"], facts["observed_high"])
        self.assertEqual(context["observed_low"], facts["observed_low"])
        self.assertEqual(context["high_low_range"], facts["observed_range_size"])

    def test_observed_range_facts_reject_invalid_numeric_field_clearly(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "high": "bad-high",
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 42.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(PerceptionError, "field high must be numeric"):
            build_perception_evidence(snapshot)

    def test_observed_range_facts_reject_missing_range_field_clearly(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 42.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(PerceptionError, "missing required fields: high"):
            build_perception_evidence(snapshot)

    def test_market_efficiency_includes_participation_availability_facts(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)
        context = result.market_efficiency_evidence.market_mechanics_context
        facts = context["participation_availability_facts"]

        self.assertTrue(facts["volume_available"])
        self.assertTrue(facts["open_interest_available"])
        self.assertTrue(facts["funding_available"])
        self.assertTrue(facts["cvd_available"])
        self.assertTrue(facts["liquidations_available"])
        self.assertEqual(facts["missing_participation_metrics"], ())

    def test_participation_availability_reports_missing_optional_metrics(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-participation-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 42.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        result = build_perception_evidence(snapshot)
        facts = (
            result.market_efficiency_evidence.market_mechanics_context[
                "participation_availability_facts"
            ]
        )

        self.assertTrue(facts["volume_available"])
        self.assertFalse(facts["open_interest_available"])
        self.assertFalse(facts["funding_available"])
        self.assertFalse(facts["cvd_available"])
        self.assertFalse(facts["liquidations_available"])
        self.assertEqual(
            facts["missing_participation_metrics"],
            ("open_interest", "funding_rate", "cvd", "liquidations"),
        )

    def test_participation_availability_preserves_existing_context_fields(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)
        context = result.market_efficiency_evidence.market_mechanics_context
        facts = context["participation_availability_facts"]

        self.assertEqual(context["volume_available"], facts["volume_available"])
        self.assertEqual(
            context["open_interest_available"],
            facts["open_interest_available"],
        )
        self.assertEqual(context["funding_rate_available"], facts["funding_available"])
        self.assertEqual(context["cvd_available"], facts["cvd_available"])
        self.assertEqual(
            context["liquidations_available"],
            facts["liquidations_available"],
        )
        self.assertEqual(
            context["missing_participation_metrics"],
            facts["missing_participation_metrics"],
        )

    def test_participation_availability_output_stays_neutral(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)
        facts = (
            result.market_efficiency_evidence.market_mechanics_context[
                "participation_availability_facts"
            ]
        )
        output_text = " ".join(_flatten_text(facts)).lower()

        forbidden_terms = (
            "agent_state",
            "hypothesis",
            "confidence",
            "decision",
            "alert",
            "trade",
            "trading_signal",
            "bullish",
            "bearish",
            "continuation",
            "breakout",
            "reversal",
        )
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, output_text)

    def test_perception_evidence_includes_close_sequence_facts(self) -> None:
        event = make_event_with_market_snapshot()
        candles = event.market_snapshot.ohlcv
        first_close = float(candles[0]["close"])
        latest_close = float(candles[-1]["close"])
        close_delta = latest_close - first_close

        result = build_perception_evidence(event.market_snapshot)
        facts = result.structural_evidence.technical_context["close_sequence_facts"]

        self.assertEqual(facts["first_close"], first_close)
        self.assertEqual(facts["latest_close"], latest_close)
        self.assertEqual(facts["close_delta"], close_delta)
        self.assertEqual(
            facts["close_delta_percent"],
            (close_delta / first_close) * 100.0,
        )
        self.assertEqual(facts["candle_count_used"], len(candles))

    def test_close_sequence_percent_is_none_when_first_close_is_zero(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-close-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": 0.0,
                    "volume": 42.0,
                },
                {
                    "timestamp": "2026-07-01T12:01:00Z",
                    "open": 100.0,
                    "high": 102.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 43.0,
                },
            ),
            volume=43.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        result = build_perception_evidence(snapshot)
        facts = result.structural_evidence.technical_context["close_sequence_facts"]

        self.assertEqual(facts["first_close"], 0.0)
        self.assertEqual(facts["latest_close"], 100.0)
        self.assertEqual(facts["close_delta"], 100.0)
        self.assertIsNone(facts["close_delta_percent"])
        self.assertEqual(facts["candle_count_used"], 2)

    def test_close_sequence_facts_reject_invalid_close_field_clearly(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-close-2",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": "bad-close",
                    "volume": 42.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(PerceptionError, "field close must be numeric"):
            build_perception_evidence(snapshot)

    def test_close_sequence_facts_output_stays_neutral(self) -> None:
        event = make_event_with_market_snapshot()

        result = build_perception_evidence(event.market_snapshot)
        facts = result.structural_evidence.technical_context["close_sequence_facts"]
        output_text = " ".join(_flatten_text(facts)).lower()

        forbidden_terms = (
            "bullish",
            "bearish",
            "trend",
            "strength",
            "weakness",
            "continuation",
            "failure",
            "pump",
            "dump",
            "long",
            "short",
            "hypothesis",
            "confidence",
            "decision",
            "alert",
            "trade",
        )
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, output_text)

    def test_perception_evidence_writes_only_owned_evidence_sections(self) -> None:
        event = make_event_with_market_snapshot()

        updated = add_perception_evidence(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIsNone(updated.observation_package)
        self.assertIsNotNone(updated.structural_evidence)
        self.assertIsNotNone(updated.market_efficiency_evidence)
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_perception_evidence_uses_runtime_event_id(self) -> None:
        event = make_event_with_market_snapshot()

        updated = add_perception_evidence(event)

        self.assertEqual(updated.structural_evidence.event_id, event.event_id)
        self.assertEqual(
            updated.market_efficiency_evidence.event_id,
            event.event_id,
        )

    def test_perception_evidence_does_not_modify_market_snapshot(self) -> None:
        event = make_event_with_market_snapshot()
        before = event.market_snapshot.to_dict()

        updated = add_perception_evidence(event)

        self.assertEqual(updated.market_snapshot.to_dict(), before)

    def test_perception_evidence_requires_market_snapshot(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaises(PerceptionError):
            add_perception_evidence(event)

    def test_perception_evidence_output_stays_neutral(self) -> None:
        event = make_event_with_market_snapshot()

        updated = add_perception_evidence(event)
        output_text = " ".join(
            _flatten_text(
                {
                    "structural_evidence": updated.structural_evidence.to_dict(),
                    "market_efficiency_evidence": (
                        updated.market_efficiency_evidence.to_dict()
                    ),
                }
            )
        ).lower()

        forbidden_terms = (
            "agent_state",
            "hypothesis",
            "confidence",
            "decision",
            "alert",
            "entry",
            "exit",
            "stop_loss",
            "take_profit",
            "long",
            "short",
            "buy",
            "sell",
        )
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, output_text)

    def test_perception_reads_market_snapshot(self) -> None:
        event = make_event_with_market_snapshot()

        observations = build_observation_package(event.market_snapshot)

        self.assertEqual(observations.event_id, event.market_snapshot.event_id)
        self.assertEqual(observations.observation_timestamp, event.market_snapshot.timestamp)

    def test_perception_produces_valid_observation_package(self) -> None:
        event = make_event_with_market_snapshot()

        observations = build_observation_package(event.market_snapshot)

        self.assertIsInstance(observations, ObservationPackage)
        self.assertEqual(observations.normalized_price, 100.0)
        self.assertEqual(observations.normalized_volume, 42.0)
        self.assertIn("price", observations.available_metrics)
        self.assertIn("open_interest", observations.available_metrics)
        self.assertEqual(observations.data_quality_status, DataQualityStatus.VALID)

    def test_observation_preserves_market_snapshot_values(self) -> None:
        event = make_event_with_market_snapshot()

        observations = build_observation_package(event.market_snapshot)

        self.assertEqual(observations.normalized_price, event.market_snapshot.price)
        self.assertEqual(observations.normalized_ohlcv, event.market_snapshot.ohlcv)
        self.assertEqual(observations.normalized_volume, event.market_snapshot.volume)
        self.assertEqual(
            observations.data_quality_status,
            event.market_snapshot.data_quality_status,
        )

    def test_optional_metrics_are_preserved_in_normalized_metrics(self) -> None:
        event = make_event_with_market_snapshot()

        observations = build_observation_package(event.market_snapshot)

        self.assertEqual(
            observations.normalized_metrics,
            event.market_snapshot.optional_market_metrics,
        )
        self.assertEqual(observations.normalized_metrics["open_interest"], 1200.5)
        self.assertEqual(observations.normalized_metrics["funding_rate"], 0.0001)
        self.assertEqual(observations.normalized_metrics["cvd"], 250.0)
        self.assertEqual(
            observations.normalized_metrics["liquidations"],
            {"long": 10.0, "short": 20.0},
        )

    def test_available_and_missing_metrics_are_deterministic(self) -> None:
        event = make_event_with_market_snapshot()

        observations = build_observation_package(event.market_snapshot)

        self.assertEqual(
            observations.available_metrics,
            (
                "price",
                "ohlcv",
                "volume",
                "open_interest",
                "funding_rate",
                "cvd",
                "liquidations",
            ),
        )
        self.assertEqual(observations.missing_metrics, ())

    def test_perception_writes_only_observation_package(self) -> None:
        event = make_event_with_market_snapshot()

        updated = add_observation_package(event)

        self.assertIsNot(updated, event)
        self.assertIs(event.market_snapshot, updated.market_snapshot)
        self.assertIsNotNone(updated.observation_package)
        self.assertIsNone(updated.structural_evidence)
        self.assertIsNone(updated.market_efficiency_evidence)
        self.assertIsNone(updated.hypothesis_package)
        self.assertIsNone(updated.agent_state)
        self.assertIsNone(updated.scenario_probability)
        self.assertIsNone(updated.confidence_assessment)
        self.assertIsNone(updated.decision_alert)
        self.assertIsNone(updated.learning_metadata)

    def test_inserted_observation_uses_runtime_event_id(self) -> None:
        event = make_event_with_market_snapshot()

        updated = add_observation_package(event)

        self.assertEqual(updated.observation_package.event_id, event.event_id)

    def test_original_market_snapshot_event_id_is_preserved_as_source_reference(
        self,
    ) -> None:
        event = make_event_with_market_snapshot()

        updated = add_observation_package(event)

        self.assertEqual(
            updated.observation_package.previous_snapshot_reference,
            event.market_snapshot.event_id,
        )

    def test_perception_does_not_modify_market_snapshot(self) -> None:
        event = make_event_with_market_snapshot()
        before = event.market_snapshot.to_dict()

        updated = add_observation_package(event)

        self.assertEqual(updated.market_snapshot.to_dict(), before)

    def test_perception_requires_market_snapshot(self) -> None:
        event = RuntimeEvent(
            event_id="runtime-evt-1",
            schema_version="1.0",
            cycle_timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
        )

        with self.assertRaises(PerceptionError):
            add_observation_package(event)

    def test_perception_rejects_malformed_market_snapshot(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(PerceptionError, "ohlcv"):
            build_observation_package(snapshot)

    def test_perception_rejects_ohlcv_candle_missing_required_field(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "close": 100.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(PerceptionError, "volume"):
            build_observation_package(snapshot)

    def test_perception_rejects_malformed_ohlcv_with_clear_field_list(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=(
                {
                    "timestamp": "2026-07-01T12:00:00Z",
                    "open": 99.0,
                    "close": 100.0,
                },
            ),
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(
            PerceptionError,
            "high, low, volume",
        ):
            build_observation_package(snapshot)

    def test_perception_rejects_non_mapping_ohlcv_candle_clearly(self) -> None:
        snapshot = MarketSnapshot(
            event_id="snapshot-1",
            timestamp=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="1m",
            price=100.0,
            ohlcv=("bad-candle",),  # type: ignore[arg-type]
            volume=42.0,
            data_source="fixture",
            data_quality_status=DataQualityStatus.VALID,
        )

        with self.assertRaisesRegex(PerceptionError, "must be a mapping"):
            build_observation_package(snapshot)

    def test_perception_does_not_import_downstream_reasoning_contracts(self) -> None:
        imports = _imports_from(ast.parse(PERCEPTION_ENGINE.read_text(encoding="utf-8")))
        imported_names = {
            alias.name
            for node in ast.walk(ast.parse(PERCEPTION_ENGINE.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        forbidden_modules = (
            "pumpagent.runtime.modules.hypothesis",
            "pumpagent.runtime.modules.agent_state",
            "pumpagent.runtime.modules.scenario_probability",
            "pumpagent.runtime.modules.confidence",
            "pumpagent.runtime.modules.decision_alert",
            "pumpagent.runtime.modules.learning_memory",
            "pumpagent.live_data",
            "pumpagent.research",
        )
        forbidden_names = {
            "AgentState",
            "AgentStateType",
            "HypothesisPackage",
            "ConfidenceAssessment",
            "ConfidenceLevel",
            "ScenarioProbability",
            "DecisionAlert",
            "DecisionType",
            "AlertLevel",
        }

        self.assertFalse(
            any(
                imported == module or imported.startswith(f"{module}.")
                for imported in imports
                for module in forbidden_modules
            )
        )
        self.assertTrue(forbidden_names.isdisjoint(imported_names))

    def test_observation_output_stays_neutral(self) -> None:
        event = make_event_with_market_snapshot()

        observations = build_observation_package(event.market_snapshot)
        output_text = " ".join(_flatten_text(observations.to_dict())).lower()

        forbidden_terms = (
            "agent_state",
            "hypothesis",
            "confidence",
            "decision",
            "alert",
            "trade",
            "trading_signal",
            "bullish",
            "bearish",
            "continuation",
            "breakout",
            "reversal",
        )
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, output_text)

    def test_observation_package_serializes_with_market_snapshot_context(self) -> None:
        event = make_event_with_market_snapshot()

        serialized = add_observation_package(event).to_dict()

        self.assertEqual(serialized["observation_package"]["normalized_price"], 100.0)
        self.assertEqual(
            serialized["observation_package"]["normalized_metrics"]["liquidations"][
                "short"
            ],
            20.0,
        )
        self.assertIsNone(serialized["structural_evidence"])


def _imports_from(tree: ast.AST) -> tuple[str, ...]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(imports)


def _flatten_text(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        items: list[str] = []
        for key, item in value.items():
            items.extend(_flatten_text(key))
            items.extend(_flatten_text(item))
        return tuple(items)
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            items.extend(_flatten_text(item))
        return tuple(items)
    if value is None:
        return ()
    return (str(value),)


if __name__ == "__main__":
    unittest.main()
