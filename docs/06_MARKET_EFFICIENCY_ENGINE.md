# Market Efficiency Engine

## Status

Implemented expansion skeleton.

Market Efficiency Engine currently validates Perception-produced
`MarketEfficiencyEvidence` without adding market interpretation.

It is not part of the current Runtime Orchestrator flow.

Advanced market efficiency reasoning remains a future milestone.

---

## Purpose

The current Market Efficiency Engine skeleton protects the participation
evidence boundary.

It validates that market efficiency evidence belongs to the current Runtime
event.

It does not interpret what is happening behind the chart yet.

Price is only the visible result.

Future versions may measure market participation and capital behavior.

---

## Current Responsibilities

The current skeleton:

- reads `MarketEfficiencyEvidence`;
- verifies Runtime event identity;
- returns `MarketEfficiencyEvidence`;
- may later return an updated `MarketEfficiencyEvidence` with objective context
  enrichment only;
- preserves source snapshot identity stored in `market_mechanics_context`;
- remains deterministic;
- remains side-effect free;
- does not modify MarketSnapshot;
- does not modify StructuralEvidence;
- does not create hypotheses, states, probabilities, confidence, decisions, or
  alerts.

Refinement must not change ownership of the evidence.

Perception owns initial `MarketEfficiencyEvidence` creation in the current
Runtime flow.

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
- Absorption
- Absorption Reserve

---

## Future Core Questions

Instead of asking:

"Is price going up?"

The engine asks:

- Who is pushing price?
- Is new money entering?
- Are shorts trapped?
- Are longs trapped?
- Is participation increasing?
- Is participation fading?
- Is the move becoming inefficient?

---

## Future Engine Output

Future versions may produce deeper evidence rather than signals.

Examples:

- Participation expanding
- Participation weakening
- Strong absorption
- Weak participation
- Short squeeze probability increasing
- Continuation quality decreasing

---

## Collaboration

The current skeleton preserves market efficiency evidence.

It never makes trading decisions.

Its evidence remains available to:

- Hypothesis Engine
