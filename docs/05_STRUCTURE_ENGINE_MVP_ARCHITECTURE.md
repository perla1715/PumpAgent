# Structure Engine MVP Architecture

## Status

Implemented.

This document records the implemented MVP architecture for expanding the
Structure Engine from its earlier evidence-boundary skeleton into a structural
analysis module.

The implementation remains structure-only and evidence-only.

---

## Objective

The Structure Engine transforms normalized candle data into an objective
description of current chart structure.

It is responsible only for structural analysis.

It does not interpret the market, infer state, create hypotheses, calculate
confidence, produce trading signals, or read participation metrics.

---

## MVP Scope

The MVP Structure Engine computes and exposes:

- EMA calculations for periods 7, 14, and 21;
- Fibonacci levels from the latest valid impulse;
- price position relative to EMA values;
- price position relative to Fibonacci levels;
- Higher High / Higher Low detection;
- Lower High / Lower Low detection;
- a structured output object representing the current chart structure.

Implemented MVP decisions:

- `ChartStructure` remains internal to the Structure module;
- `build_structural_evidence()` is the preferred primary API;
- compatibility exports remain for `add_structural_evidence()` and
  `refine_structural_evidence()`;
- downstream modules consume `StructuralEvidence`, not `ChartStructure`;
- EMA values are emitted only after their full periods are available;
- serialized EMA evidence includes latest values and availability metadata,
  not full EMA series;
- swing detection uses a deterministic 2-left / 2-right pivot rule.

Out of scope:

- Market interpretation;
- Continuation Alive;
- Continuation Death;
- First Failure;
- Confidence;
- Hypothesis;
- Trading signals;
- OI;
- Funding;
- CVD;
- Volume interpretation;
- State transitions.

---

## Implemented Directory Structure

```text
src/pumpagent/runtime/modules/structure/
├── __init__.py
├── engine.py
├── models.py
├── candles.py
├── indicators.py
├── fibonacci.py
└── swings.py
```

Optional future split if the module grows:

```text
tests/runtime/modules/structure/
├── test_structure_engine.py
├── test_structure_indicators.py
├── test_structure_fibonacci.py
└── test_structure_swings.py
```

The existing `engine.py` remains the public entry point. Helper modules stay
private to the Structure module and should not be imported by downstream
Runtime modules.

---

## Main Modules

### `engine.py`

Public orchestration layer for Structure.

Responsibilities:

- expose the public Structure Engine interfaces;
- accept `ObservationPackage` or `RuntimeEvent`;
- validate the minimum candle contract needed by Structure;
- call structure-only calculators;
- assemble `ChartStructure`;
- convert `ChartStructure` into `StructuralEvidence`;
- preserve RuntimeEvent immutability by returning a new event section only.

Non-responsibilities:

- no market state classification;
- no confidence scoring;
- no hypothesis creation;
- no participation analysis;
- no Runtime loop policy.

### `models.py`

Structure-owned data objects.

Responsibilities:

- define explicit structural output models;
- make the `technical_context` payload predictable;
- keep chart facts separate from Runtime interpretation fields.

Proposed models:

- `StructureCandle`
- `EmaSet`
- `SwingPoint`
- `Impulse`
- `FibonacciLevel`
- `ChartStructure`

These models should be immutable dataclasses and serializable through plain
Python dictionaries, matching existing Runtime domain style.

Do not add `EmaPosition`, `SwingSequence`, or `FibonacciStructure` in the MVP
unless implementation proves they remove real complexity. Their facts can be
represented directly inside `ChartStructure` as plain fields.

### `candles.py`

Input preparation layer.

Responsibilities:

- convert normalized OHLCV mappings into `StructureCandle` objects;
- enforce required candle fields;
- convert numeric fields into floats;
- preserve candle order;
- raise `StructureError` for malformed structural inputs.

This module does not decide data quality. It assumes validation and quality
translation have already happened upstream.

### `indicators.py`

Indicator calculation layer for structure-only indicators.

Responsibilities:

- compute EMA series for periods 7, 14, and 21;
- expose latest EMA values and availability metadata;
- report unavailable EMA periods when there are not enough candles.

Warmup policy:

- EMA7 appears only after 7 candles;
- EMA14 appears only after 14 candles;
- EMA21 appears only after 21 candles;
- no early EMA values are seeded from the first close.

Non-responsibilities:

- no interpretation such as "bullish", "bearish", "strong", or "weak";
- no signal generation.

### `swings.py`

Swing extraction layer.

Responsibilities:

- identify structural swing highs and swing lows;
- detect the latest Higher High, Higher Low, Lower High, and Lower Low facts;
- identify the latest valid impulse candidate for Fibonacci anchoring.

The MVP uses a deterministic 2-left / 2-right pivot rule. This intentionally
prefers fewer, cleaner swings over noisy early detection.

### `fibonacci.py`

Fibonacci calculation layer.

Responsibilities:

- receive the latest valid impulse;
- compute standard retracement levels;
- identify the latest price position relative to those levels.

Proposed MVP levels:

- 0.0;
- 0.236;
- 0.382;
- 0.5;
- 0.618;
- 0.786;
- 1.0.

The module should not infer continuation, failure, targets, entries, or exits.

### Relative Positioning

Relative positioning is implemented inside the Structure Engine assembly path
for MVP simplicity.

Responsibilities:

- compare latest price to available EMA values;
- compare latest price to Fibonacci levels when they exist;
- return neutral structural facts such as `above`, `below`, `at`, `between`,
  or `unavailable`.

Non-responsibilities:

- no trend label decisions beyond raw structural relationship facts;
- no confidence weighting.

---

## Public Interfaces

The current public shape should remain centered on `StructuralEvidence`.
`ChartStructure` is an internal assembly object, not the main public API.

Preferred public function:

```python
def build_structural_evidence(
    observations: ObservationPackage,
    *,
    runtime_event_id: str | None = None,
) -> StructuralEvidence:
    """Build Runtime StructuralEvidence from chart structure facts."""
```

Compatibility functions remain available:

```python
def add_structural_evidence(event: RuntimeEvent) -> RuntimeEvent:
    """Return a new RuntimeEvent with only structural_evidence added."""

def refine_structural_evidence(
    evidence: StructuralEvidence,
    *,
    runtime_event_id: str | None = None,
) -> StructuralEvidence:
    """Validate existing StructuralEvidence without interpretation."""
```

Public import surface in `structure/__init__.py`:

```python
from pumpagent.runtime.modules.structure.engine import (
    StructureError,
    add_structural_evidence,
    build_structural_evidence,
    refine_structural_evidence,
)
```

Downstream modules should consume `StructuralEvidence`, not helper calculators
or `ChartStructure`.

---

## Data Models

### `StructureCandle`

Canonical candle representation for structure calculations.

Fields:

- `timestamp`;
- `open`;
- `high`;
- `low`;
- `close`;
- `volume`.

Volume is carried through because it exists in the normalized candle contract,
but the Structure MVP must not interpret it.

### `EmaSet`

Latest EMA values and optional metadata.

Fields:

- `ema_7`;
- `ema_14`;
- `ema_21`;
- `available_periods`;
- `unavailable_periods`.

Full EMA series are not serialized into `StructuralEvidence`.

### `SwingPoint`

One detected swing point.

Fields:

- `kind`: `high` or `low`;
- `timestamp`;
- `price`;
- `candle_index`.

### `Impulse`

Latest valid impulse used for Fibonacci anchoring.

Fields:

- `direction`: `up`, `down`, or `unknown`;
- `start`;
- `end`;
- `high`;
- `low`;
- `is_valid`;
- `invalid_reason`.

### `FibonacciLevel`

One Fibonacci level.

Fields:

- `ratio`;
- `price`;
- `label`.

### `ChartStructure`

Primary structured output object for the Structure Engine.

Fields:

- `event_id`;
- `symbol`;
- `exchange`;
- `timeframe`;
- `candle_count`;
- `latest_price`;
- `emas`;
- `ema_positions`;
- `swing_highs`;
- `swing_lows`;
- `latest_higher_high`;
- `latest_higher_low`;
- `latest_lower_high`;
- `latest_lower_low`;
- `latest_impulse`;
- `fibonacci_levels`;
- `fibonacci_position`;
- `structural_events`;
- `key_levels`;
- `warnings`;
- `schema_version`.

`ChartStructure` is the internal rich structure-owned output.
`StructuralEvidence` is the Runtime boundary object.

---

## Data Flow

```text
ObservationPackage.normalized_ohlcv
    ↓
candles.to_structure_candles()
    ↓
indicators.calculate_emas()
    ↓
swings.detect_swings()
    ↓
swings.latest_valid_impulse()
    ↓
fibonacci.calculate_fibonacci_levels()
    ↓
engine._ema_positions()
fibonacci.describe_fibonacci_position()
    ↓
ChartStructure
    ↓
StructuralEvidence
    ↓
RuntimeEvent.structural_evidence
```

The engine assembles `StructuralEvidence` as follows:

- `trend_structure`: compact structural summary label, or a legacy
  close-sequence compatibility fallback;
- `structural_bias`: keep `not_assessed` for the MVP;
- `key_levels`: observed high/low, latest close, EMA values, and Fibonacci
  levels;
- `structural_events`: raw structural facts from EMA, swing, and Fibonacci
  modules;
- `technical_context`: serialized internal `ChartStructure`;
- `evidence_strength`: coverage-oriented only, not confidence-oriented;
- `uncertainty`: based on structural availability, not market prediction.

Legacy `trend_structure` compatibility fallback labels remain:

- `rising_close_sequence`;
- `falling_close_sequence`;
- `flat_close_sequence`.

They are objective close-sequence facts, not market interpretation.

---

## Dependency Relationships

Allowed dependencies:

```text
runtime.modules.structure.engine
    -> runtime.domain.ObservationPackage
    -> runtime.domain.RuntimeEvent
    -> runtime.domain.StructuralEvidence
    -> runtime.modules.structure.models
    -> runtime.modules.structure.candles
    -> runtime.modules.structure.indicators
    -> runtime.modules.structure.swings
    -> runtime.modules.structure.fibonacci
```

Helper dependencies:

```text
candles.py      -> models.py
indicators.py   -> models.py
swings.py       -> models.py
fibonacci.py    -> models.py
```

Forbidden dependencies:

- Live Data adapters;
- Live Data normalizers;
- Live Data validation;
- Live Data quality translation;
- Runtime Bridge;
- Market Efficiency Engine;
- Hypothesis Engine;
- Agent State;
- Scenario Probability;
- Confidence;
- Decision Alert;
- Watchlist;
- Persistence;
- external exchange clients.

The Structure Engine should remain deterministic and side-effect free.

---

## Structured Output Boundary

The MVP should not add a new RuntimeEvent section.

Recommended boundary:

- `ChartStructure` lives inside the Structure module;
- `StructuralEvidence` remains the Runtime output contract;
- serialized `ChartStructure` is stored in
  `StructuralEvidence.technical_context["chart_structure"]`.

This preserves the existing architecture while making the richer structural
output available to downstream modules.

Downstream modules should treat this payload as evidence, not as a decision.
They should not import or depend on the `ChartStructure` class directly.

---

## Error Handling

Structure should raise `StructureError` only for malformed structural inputs
that prevent analysis.

Examples:

- no candles;
- candle is not a mapping;
- required OHLCV field is missing;
- OHLC numeric field is not numeric.

Insufficient data for a specific structural calculation should usually produce
a partial `ChartStructure` with warnings rather than fail the whole engine.

Examples:

- fewer candles than required for EMA7, EMA14, or EMA21;
- no confirmed swing pair;
- no valid impulse for Fibonacci levels.

These cases should be visible in:

- `ChartStructure.warnings`;
- `StructuralEvidence.structural_events`;
- `StructuralEvidence.uncertainty`.

---

## Extensibility Considerations

### Future Structural Features

The proposed module split allows adding future structure-only features without
changing the Runtime boundary:

- Bollinger Bands can be added to `indicators.py`;
- compression/expansion facts can be added to a future `volatility.py`;
- reclaim/failure facts can be added to a future `levels.py`;
- trendline or channel facts can be added to a future `geometry.py`.

Each new feature should add objective facts to `ChartStructure` and
`StructuralEvidence.structural_events`.

### Future Interpretation Modules

Interpretive concepts should remain downstream of Structure.

Examples that should not enter Structure:

- Continuation Alive;
- Continuation Death;
- First Failure;
- hypothesis labels;
- confidence scores;
- trading decisions.

Those modules may consume `StructuralEvidence`, including the serialized
`ChartStructure`, after this MVP is implemented.

### Versioning

`ChartStructure.schema_version` should start at `structure_chart_v1`.

If the shape of serialized chart structure changes incompatibly, introduce a
new schema version rather than silently changing existing fields.

### Testing Strategy

Recommended unit test groups:

- candle conversion and malformed input errors;
- EMA calculations for 7, 14, and 21;
- EMA availability only after full period warmup;
- swing high / swing low detection with the 2-left / 2-right pivot rule;
- HH / HL / LH / LL detection;
- latest valid impulse selection;
- Fibonacci level generation for up and down impulses;
- price position relative to EMA values;
- price position relative to Fibonacci levels;
- `StructuralEvidence` assembly remains evidence-only;
- no forbidden downstream imports.

Integration tests should verify:

- `add_structural_evidence(event)` writes only `structural_evidence`;
- `MarketSnapshot` and `ObservationPackage` are not modified;
- downstream Runtime sections remain unset;
- existing Runtime skeleton compatibility remains intact.

---

## Implemented Decision

Fibonacci levels are included only when `Impulse.is_valid` is true.
