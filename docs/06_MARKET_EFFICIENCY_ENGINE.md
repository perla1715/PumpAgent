# Market Efficiency Engine

## Status

Planned next Runtime alignment milestone.

This document describes the intended Market Efficiency layer. It should not be
read as part of the currently implemented Runtime Core milestone.

---

## Purpose

The planned Market Efficiency Engine studies what is happening behind the
chart.

Price is only the visible result.

The engine measures market participation and capital behavior.

---

## Responsibilities

The planned engine evaluates:

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

## Core Questions

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

## Engine Output

The planned engine produces evidence rather than signals.

Examples:

- Participation expanding
- Participation weakening
- Strong absorption
- Weak participation
- Short squeeze probability increasing
- Continuation quality decreasing

---

## Collaboration

The planned Market Efficiency Engine never works alone.

Its evidence will be combined with:

- Structure Engine

The final interpretation will be performed by:

- Hypothesis Engine
