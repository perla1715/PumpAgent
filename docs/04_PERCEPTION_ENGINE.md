# Perception Engine

## Status

Implemented MVP with strict specialized evidence ownership.

## Purpose

Perception is the Runtime boundary between `MarketSnapshot` and normalized
observations. It validates the snapshot and produces `ObservationPackage`.

Canonical flow:

```text
MarketSnapshot
→ ObservationPackage
→ Structure Engine
→ StructuralEvidence
→ Market Efficiency Engine
→ MarketEfficiencyEvidence
```

Perception does not independently construct `StructuralEvidence` or
`MarketEfficiencyEvidence`. Structure Engine is the sole production owner of
`StructuralEvidence`; Market Efficiency Engine is the sole production owner of
`MarketEfficiencyEvidence`.

## Public observation APIs

```python
build_observation_package(snapshot)
add_observation_package(event)
```

`build_observation_package()` validates `MarketSnapshot` and preserves:

- normalized price, OHLCV, and volume;
- available and missing metrics;
- data-quality status;
- optional normalized metrics;
- the source snapshot reference.

`add_observation_package()` adds only `observation_package` to a new immutable
`RuntimeEvent`. It does not populate either final evidence section.

## Retired evidence APIs

Perception no longer exports or implements:

- `build_perception_evidence`;
- `add_perception_evidence`;
- `PerceptionEvidenceResult`.

There is no Perception compatibility path for constructing final evidence
contracts.

## Legacy scanner helpers

The unrelated scanner helpers remain available for compatibility:

```python
detect_market_state(data)
format_market_state_scan_line(data)
print_market_state_scan(markets)
```

Their scanner labels and console formatting are not Runtime evidence contracts
and do not affect `ObservationPackage`.

## Runtime ownership

Both Runtime paths use the same ownership model:

- Main Runtime: Perception observation packaging, then specialized Structure
  and Market Efficiency construction.
- Fixture Runtime: Perception observation adapter, then the Structure and
  Market Efficiency RuntimeEvent adapters.

Perception does not create hypotheses, classify Process or Agent State, assign
probabilities or confidence, generate decisions or alerts, or orchestrate the
Runtime.
