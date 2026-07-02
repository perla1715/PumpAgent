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
- Latest candle open
- Latest candle high
- Latest candle low
- Latest candle close
- Latest candle volume
- Malformed candle indicators
- Data quality status
- Missing fields
- Latency when available
- Source metadata references when available
- Latest close
- Observed high
- Observed low
- Observed range size
- First candle timestamp
- Last candle timestamp
- High / low range
- Open Interest availability
- Funding availability
- CVD
- Liquidations
- Participation availability facts
- Missing participation metrics
- Data quality context

No conclusions are made here.

Everything remains evidence.

Advanced Perception v1 step 1 adds OHLCV integrity facts to structural evidence
context.

These facts describe candle data shape only.

They do not classify market state, create hypotheses, calculate probability,
calculate confidence, generate alerts, or imply trading action.

Advanced Perception v1 step 2 adds latest candle facts to structural evidence
context.

These facts expose the latest candle timestamp, open, high, low, close, and
volume as objective values only.

They do not classify market state, create hypotheses, calculate probability,
calculate confidence, generate alerts, or imply trading action.

Advanced Perception v1 step 3 adds data quality context to evidence context.

These facts carry existing `MarketSnapshot` quality metadata, including data
quality status, missing fields, latency, raw payload reference, data source,
schema version, and source metadata references when available.

They do not create synthetic quality scores and do not classify market state,
create hypotheses, calculate probability, calculate confidence, generate
alerts, or imply trading action.

Advanced Perception v1 step 4 adds observed range facts to structural evidence
context.

These facts are derived only from available OHLCV candles and include observed
high, observed low, observed range size, candle count used, first candle
timestamp, and last candle timestamp.

They do not infer support, resistance, trend, breakout, volatility regime,
market structure, probability, confidence, alerts, or trading action.

Advanced Perception v1 step 5 adds participation availability facts to market
efficiency evidence context.

These facts report whether volume, open interest, funding, CVD, and
liquidations are available, and which optional participation metrics are
missing.

They do not evaluate metric quality, infer participation strength, create
hypotheses, calculate probability, calculate confidence, generate alerts, or
imply trading action.

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
