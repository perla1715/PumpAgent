# Perception Engine

## Status

Implemented MVP.

Perception Engine v0.1 is a Runtime-safe boundary after `MarketSnapshot`.

It has two currently supported surfaces:

- Clean Perception Evidence Pipeline;
- Legacy Scanner Compatibility Layer.

The clean evidence pipeline is the architectural path for Runtime evidence.

The legacy scanner helpers remain available for compatibility only.

---

## Purpose

The Perception Engine prepares objective market data for downstream Runtime
reasoning.

It extracts neutral facts from `MarketSnapshot` so downstream modules do not
depend on exchange, transport, bridge, validation, normalizer, or Live Data
layers.

The clean evidence pipeline does not predict.

The clean evidence pipeline does not interpret market state.

The clean evidence pipeline does not assign confidence.

The clean evidence pipeline does not produce trading signals.

---

## Clean Perception Evidence Pipeline

Primary MVP API:

```python
build_perception_evidence(snapshot)
```

Input:

- `MarketSnapshot`

Output:

- `PerceptionEvidenceResult`
  - `StructuralEvidence`
  - `MarketEfficiencyEvidence`

This path produces objective evidence only.

It does not:

- interpret market state;
- classify Agent State;
- generate hypotheses;
- assign scenario probabilities;
- calculate confidence;
- generate decisions;
- generate alerts;
- produce trading signals;
- orchestrate runtime behavior.

### Structural Evidence Facts

The clean evidence pipeline currently adds objective structural context:

- OHLCV availability;
- candle count;
- required OHLCV field presence;
- malformed candle indicators;
- latest candle timestamp;
- latest candle open;
- latest candle high;
- latest candle low;
- latest candle close;
- latest candle volume;
- latest close;
- first close;
- close delta;
- close delta percent when safe;
- observed high;
- observed low;
- observed range size;
- first candle timestamp;
- last candle timestamp;
- high / low range;
- data quality context.

These facts describe candle data shape and values only.

They do not classify direction, state, strength, weakness, continuation,
failure, pump, dump, probability, confidence, alerts, or trading action.

### Market Efficiency Evidence Facts

The clean evidence pipeline currently adds objective participation availability
context:

- volume availability;
- open interest availability;
- funding availability;
- CVD availability;
- liquidations availability;
- missing participation metrics;
- data quality context.

These facts report whether participation metrics exist.

They do not evaluate metric quality, infer participation strength, generate
hypotheses, calculate probability, calculate confidence, generate alerts, or
imply trading action.

---

## ObservationPackage APIs

Perception also exposes an ObservationPackage preparation path.

Public APIs:

```python
build_observation_package(snapshot)
add_observation_package(event)
```

Input:

- `MarketSnapshot`

Output:

- `ObservationPackage`

`ObservationPackage` is currently part of the public Perception output.

It carries normalized Runtime-facing market data:

- normalized price;
- normalized OHLCV;
- normalized volume;
- available metrics;
- missing metrics;
- data quality status;
- optional normalized metrics;
- source `MarketSnapshot` reference.

This path prepares data for downstream Runtime modules such as Structure.

It does not interpret market state, assign confidence, generate hypotheses,
produce trading signals, or orchestrate runtime behavior.

---

## Event Compatibility APIs

Perception can write its owned sections into an immutable `RuntimeEvent`.

Compatibility APIs:

```python
add_perception_evidence(event)
add_observation_package(event)
```

`add_perception_evidence(event)` writes only:

- `structural_evidence`;
- `market_efficiency_evidence`.

`add_observation_package(event)` writes only:

- `observation_package`.

These helpers preserve existing RuntimeEvent sections and do not run the
Runtime Orchestrator.

---

## Legacy Scanner Compatibility Layer

Perception currently exports scanner helpers for compatibility:

```python
detect_market_state(data)
format_market_state_scan_line(data)
print_market_state_scan(markets)
```

These helpers are not part of the clean Perception evidence pipeline.

`detect_market_state(data)` reads scanner-style metrics:

- `price_change_1m`;
- `price_change_3m`;
- `volume_spike_ratio`;
- `oi_change_1m`.

It returns scanner labels:

- `IGNITION`;
- `CONTINUATION_ALIVE`;
- `WEAKENING`;
- `UNKNOWN`.

`format_market_state_scan_line(data)` formats scanner output such as:

```text
SYMBOL | STATE | CONF=78% | price | volume | oi | Evidence: ...
```

The following belong only to the legacy scanner compatibility layer:

- scanner state labels;
- `CONF` formatting;
- market-state scan output.

They are not part of `build_perception_evidence()`.

They are not part of `build_observation_package()`.

They do not change `StructuralEvidence`, `MarketEfficiencyEvidence`, or
`ObservationPackage` behavior.

---

## Runtime Boundaries

The clean Perception evidence pipeline must not:

- create hypotheses;
- classify Agent State;
- assign scenario probabilities;
- calculate confidence;
- generate decisions;
- generate alerts;
- produce trading signals;
- orchestrate runtime behavior;
- access Learning Memory;
- access Research Plane;
- access exchange APIs;
- access transport, bridge, validation, normalizer, or Live Data layers;
- use trading execution language such as entry, exit, stop loss, take profit,
  buy, or sell.

Perception is deterministic and side-effect free.

The same `MarketSnapshot` should produce the same clean evidence contracts.

---

## Output Summary

Current public Perception outputs:

- `PerceptionEvidenceResult`
  - `StructuralEvidence`
  - `MarketEfficiencyEvidence`
- `ObservationPackage`

Legacy scanner output is compatibility-only text/classification output and is
not part of the clean evidence pipeline.

---

## Future Cleanup

The legacy scanner helpers should eventually move out of Perception Engine into
a dedicated scanner module, such as:

- `scanner.py`;
- `market_state_scan.py`.

That future cleanup should separate scanner labels and `CONF` formatting from
the clean Runtime evidence pipeline without changing current behavior.
