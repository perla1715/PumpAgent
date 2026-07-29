# Structure Engine

## Status

Implemented MVP.

The Structure Engine transforms normalized candle data from
`ObservationPackage.normalized_ohlcv` into objective `StructuralEvidence`.

It remains deterministic, side-effect free, and evidence-only.

It does not perform market interpretation.

It is not a trading signal engine.

---

## Purpose

The Structure Engine owns structural chart analysis.

It reads normalized OHLCV candles and emits structural facts about:

- EMA availability and latest EMA values;
- price position relative to EMA values;
- 2-left / 2-right pivot swings;
- Higher High / Higher Low facts;
- Lower High / Lower Low facts;
- latest valid impulse;
- Fibonacci levels from that impulse;
- price position relative to Fibonacci levels.

The engine serializes those facts into `StructuralEvidence`.

Downstream modules consume `StructuralEvidence`, not internal structure models.

---

## Public API

Preferred public API:

```python
build_structural_evidence(observations)
```

Compatibility exports remain available:

```python
add_structural_evidence(event)
refine_structural_evidence(evidence)
```

`build_structural_evidence()` is the preferred primary API for constructing
Structure Engine MVP output from normalized observations.

`add_structural_evidence()` remains available for the existing immutable
`RuntimeEvent` handoff path.

`refine_structural_evidence()` validates already-present externally supplied
evidence for immutable RuntimeEvent handoff. It does not construct or replace
evidence.

Structure Engine is the sole production owner of `StructuralEvidence`.

---

## Internal Boundary

`ChartStructure` is internal by convention.

It is not exported from `pumpagent.runtime.modules.structure`.

The internal chart structure is serialized into:

```python
StructuralEvidence.technical_context["chart_structure"]
```

The serialized payload is stable structural evidence, not a loose dump of
runtime objects.

Serialized EMA evidence includes only:

- latest EMA7;
- latest EMA14;
- latest EMA21;
- available EMA periods;
- unavailable EMA periods.

Full EMA series are not serialized into `StructuralEvidence`.

---

## Implemented MVP Responsibilities

The implemented MVP:

- validates the minimum normalized OHLCV candle shape required by Structure;
- converts normalized candle mappings into internal `StructureCandle` objects;
- calculates EMA7, EMA14, and EMA21;
- emits EMA values only after full-period warmup;
- detects swings using a deterministic 2-left / 2-right pivot rule;
- detects latest Higher High / Higher Low and Lower High / Lower Low facts;
- detects the latest valid impulse from opposite swing points;
- emits Fibonacci levels only when a valid latest impulse exists;
- builds internal `ChartStructure`;
- serializes structural facts into `StructuralEvidence`;
- preserves `RuntimeEvent` immutability when used through the compatibility
  event API.

---

## EMA Warmup

EMA values use full-period availability only:

- EMA7 appears only after 7 candles;
- EMA14 appears only after 14 candles;
- EMA21 appears only after 21 candles.

Early EMA values are not seeded from the first close.

When an EMA period is unavailable, Structure reports that as objective
availability evidence.

---

## Swing Detection

Swing detection uses a deterministic 2-left / 2-right pivot rule.

A swing high requires the candle high to be strictly greater than the two highs
to its left and the two highs to its right.

A swing low requires the candle low to be strictly lower than the two lows to
its left and the two lows to its right.

This intentionally favors fewer, cleaner swing points over noisy early
detection.

---

## Fibonacci Levels

Fibonacci levels are emitted only when Structure detects a valid latest
opposite-swing impulse.

If no valid impulse exists:

- no Fibonacci levels are emitted;
- structural events include Fibonacci unavailability;
- the chart structure warning records that no valid swing impulse exists.

The MVP emits standard retracement ratios:

- 0.0;
- 0.236;
- 0.382;
- 0.5;
- 0.618;
- 0.786;
- 1.0.

---

## `trend_structure`

The MVP may emit structural availability labels such as:

- `ema_swing_fibonacci_structure_available`;
- `ema_swing_structure_available`;
- `insufficient_sequence`.

Legacy close-sequence labels remain as compatibility fallback:

- `rising_close_sequence`;
- `falling_close_sequence`;
- `flat_close_sequence`.

These fallback labels are objective close-sequence facts.

They are not market interpretation.

---

## Explicit Non-Responsibilities

Structure Engine does not:

- create trading signals;
- create hypotheses;
- calculate confidence;
- classify Agent State;
- decide Continuation Alive;
- decide Continuation Death;
- decide First Failure;
- read or interpret OI;
- read or interpret Funding;
- read or interpret CVD;
- interpret volume;
- create a Validation Layer;
- perform Quality Translation;
- change Runtime orchestration;
- call exchanges, adapters, normalizers, validators, bridge components, storage,
  Telegram, or external services.

---

## Output

The output remains `StructuralEvidence`.

Important fields:

- `trend_structure`: structural availability or legacy close-sequence fact;
- `structural_bias`: remains `not_assessed`;
- `key_levels`: observed high/low, latest close, available EMA values, and
  Fibonacci levels when available;
- `structural_events`: objective facts about EMA availability, swing detection,
  impulse availability, and Fibonacci availability;
- `technical_context["chart_structure"]`: serialized internal chart structure;
- `evidence_strength`: coverage-oriented only;
- `uncertainty`: structural availability-oriented only.

The output never recommends buying, selling, holding, entering, exiting, or
changing state.

---

## Remaining Limitations

- `ChartStructure` is internal by convention, but Python does not enforce that
  against direct module imports.
- `ObservationPackage` does not currently carry symbol, exchange, or timeframe,
  so internal chart identity fields may be empty when built directly from
  observations.
- Equal highs or lows are not pivots in the MVP.
- Fibonacci levels require a valid latest opposite-swing impulse.
- More detailed structural interpretation remains downstream and future-scoped.
