# Structure Engine

## Status

Implemented expansion skeleton.

Structure Engine currently validates Perception-produced `StructuralEvidence`
without adding market interpretation.

It is not part of the current Runtime Orchestrator flow.

Advanced structural reasoning remains a future milestone.

---

## Purpose

The current Structure Engine skeleton protects the structural evidence boundary.

It validates that structural evidence belongs to the current Runtime event.

It does not interpret the visual structure of the market yet.

Future versions may study how price behaves.

It does not study participation.

It studies how price behaves.

Its responsibility is to understand whether the current structure is becoming stronger or weaker.

---

## Current Responsibilities

The current skeleton:

- reads `StructuralEvidence`;
- verifies Runtime event identity;
- returns evidence unchanged;
- remains deterministic;
- remains side-effect free;
- does not modify MarketSnapshot;
- does not modify MarketEfficiencyEvidence;
- does not create hypotheses, states, probabilities, confidence, decisions, or
  alerts.

---

## Future Structural Elements

Future versions may evaluate:

- EMA structure
- Fibonacci levels
- Bollinger Bands
- Higher Highs
- Higher Lows
- Lower Highs
- Lower Lows
- Compression
- Expansion
- Breakouts
- Reclaims
- Failed Reclaims

---

## Future Structural Questions

Instead of asking:

"Should we buy?"

The engine asks:

- Is continuation healthy?
- Is structure weakening?
- Is the trend accelerating?
- Is the market compressing?
- Has the first structural failure appeared?

---

## Future Structural Events

Examples include:

- Vertical Expansion
- High Plateau
- Weak Reclaim
- First Failure Candidate
- First Failure
- Continuation Alive
- Continuation Saturation
- Instant Failure

---

## Output

The current skeleton preserves structural evidence.

It never makes trading decisions.

Its evidence remains available to the Hypothesis Engine.
