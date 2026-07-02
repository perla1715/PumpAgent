# Perception Engine

## Status

Implemented skeleton.

Perception Engine v0.1 is implemented as a Runtime-safe boundary between
`MarketSnapshot` and the Hypothesis Engine.

It is a contract and evidence milestone only.

---

## Purpose

The Perception Engine is the first Runtime layer after `MarketSnapshot`.

It does not predict.

It does not interpret the market.

It extracts objective evidence from market data so downstream Runtime modules
can reason without depending on exchange, transport, bridge, validation,
normalizer, or Live Data layers.

The quality of every future decision depends on the quality and neutrality of
perception.

---

## Responsibilities

Perception Engine v0.1 reads only:

- MarketSnapshot

It currently produces:

- StructuralEvidence
- MarketEfficiencyEvidence

The evidence is intentionally simple and objective.

Examples of extracted facts include:

- Price
- Volume
- OHLCV availability
- Candle count
- Required OHLCV field presence
- Latest candle timestamp
- Malformed candle indicators
- Latest close
- Observed high
- Observed low
- High / low range
- Open Interest availability
- Funding availability
- CVD
- Liquidations
- Missing participation metrics
- Data quality context

No conclusions are made here.

Everything remains evidence.

Advanced Perception v1 step 1 adds OHLCV integrity facts to structural evidence
context.

These facts describe candle data shape only.

They do not classify market state, create hypotheses, calculate probability,
calculate confidence, generate alerts, or imply trading action.

---

## Observation Principles

The engine never asks:

"Is this bullish?"

Instead it asks:

"What changed?"

Examples:

- OI increased by 8%
- Volume doubled
- EMA spread expanded
- Funding turned negative
- Price rejected Fib 0.236

Only facts.

---

## Runtime Boundaries

Perception Engine v0.1 must not:

- create hypotheses;
- classify Agent State;
- assign scenario probabilities;
- calculate confidence;
- generate decisions;
- generate alerts;
- access Learning Memory;
- access Research Plane;
- access exchange APIs;
- access transport, bridge, validation, normalizer, or Live Data layers;
- use trading execution language such as entry, exit, stop loss, take profit,
  buy, or sell.

Perception is deterministic and side-effect free.

The same `MarketSnapshot` should produce the same evidence contracts.

---

## Output

The current output of Perception Engine is:

- StructuralEvidence
- MarketEfficiencyEvidence

These contracts are passed to the Hypothesis Engine.

Perception does not currently produce advanced structural interpretation or
advanced market efficiency interpretation.

Those are future milestones.
