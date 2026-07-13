# Market Efficiency Engine

## Status

Implemented MVP.

Market Efficiency Engine builds objective `MarketEfficiencyEvidence` from
`ObservationPackage`.

The Runtime loop currently imports and calls the Market Efficiency Engine.

The engine itself does not orchestrate Runtime behavior. It only produces
evidence.

Advanced market efficiency reasoning remains a future milestone.

---

## Purpose

The current Market Efficiency Engine MVP protects the participation evidence
boundary.

It extracts metric availability and raw participation context from normalized
observations.

It does not interpret what is happening behind the chart yet.

Price is only the visible result.

Future versions may measure market participation and capital behavior.

---

## Public API

Preferred MVP API:

- `build_market_efficiency_evidence()`

Compatibility and Runtime helpers:

- `add_market_efficiency_evidence()`
- `refine_market_efficiency_evidence()`
- `MarketEfficiencyError`

---

## Current Responsibilities

The current MVP:

- reads `ObservationPackage`;
- builds objective `MarketEfficiencyEvidence`;
- records available and missing participation metrics;
- stores raw context values for volume, Open Interest, Funding Rate, CVD, and
  liquidations when present;
- can validate existing Perception-produced `MarketEfficiencyEvidence` through
  the compatibility refinement path;
- preserves source observation identity stored in `market_mechanics_context`;
- remains deterministic;
- remains side-effect free;
- does not modify `MarketSnapshot`;
- does not modify `StructuralEvidence`;
- does not create hypotheses, states, probabilities, trading confidence,
  decisions, trading signals, or alerts.

Refinement must not change ownership of the evidence.

Perception may still create `MarketEfficiencyEvidence` for compatibility.

The cleaner current path is:

`ObservationPackage` -> `build_market_efficiency_evidence()` ->
`MarketEfficiencyEvidence`

---

## Runtime Flow

The Runtime loop currently calls `build_market_efficiency_evidence()` after
building an `ObservationPackage`.

Market Efficiency does not own the Runtime loop.

It does not call downstream modules, transition state, generate hypotheses,
calculate trade confidence, or produce trading signals.

---

## Current Metric Behavior

Current MVP metric handling is evidence-only.

The engine records:

- whether Open Interest is available;
- whether Funding Rate is available;
- whether CVD is available;
- whether liquidations are available;
- whether volume is available;
- raw context values for available metrics.

It does not interpret these values.

It does not assign trading meaning to Open Interest, Funding Rate, CVD,
liquidations, or volume.

---

## Evidence Coverage Metadata

`EvidenceStrength` and `UncertaintyLevel` describe evidence coverage and data
availability.

They are not market confidence.

They are not trade confidence.

They do not express whether a setup is good, bad, alive, dead, likely, or
actionable.

---

## Future Responsibilities

Future versions may evaluate:

- Open Interest
- Aggregated Open Interest
- Funding Rate
- CVD
- Liquidations
- Volume Participation
- Price Efficiency
- Participation Efficiency
- Absorption evidence
- Absorption reserve evidence

---

## Future Core Questions

Future versions may support evidence questions such as:

- Which participation metrics are available?
- Is participation data expanding or contracting?
- Are raw participation inputs aligned or mixed?
- Is price movement supported by objective participation evidence?
- Is participation evidence sufficient for downstream evaluation?

---

## Future Engine Output

Future versions may produce deeper evidence rather than signals.

Examples:

- Participation metric availability summary
- Participation data expansion evidence
- Participation data contraction evidence
- Absorption-related evidence context
- Price efficiency evidence context
- Participation efficiency evidence context

---

## Collaboration

The current MVP produces market efficiency evidence.

It never makes trading decisions.

It never interprets market state.

It never generates hypotheses.

It never assigns trading confidence.

It never produces trading signals.

It never orchestrates Runtime behavior.

Its evidence remains available to:

- Hypothesis Engine
