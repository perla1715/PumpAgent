# Perception Engine

## Purpose

The Perception Engine is the sensory system of PumpAgent.

It does not predict.

It does not analyze.

It only observes the market exactly as it is.

The quality of every future decision depends on the quality of perception.

---

## Responsibilities

The engine continuously collects:

- Price
- Volume
- Open Interest
- Aggregated Open Interest
- Funding
- CVD
- Liquidations
- Order Book
- Trades
- EMA
- Bollinger Bands
- Fibonacci Levels

No conclusions are made here.

Everything is stored as observations.

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

## Continuous Observation

The engine never sleeps.

Every new candle updates the current observations.

Nothing is ignored.

Everything becomes evidence for future reasoning.

---

## Output

The output of Perception Engine is a clean observation package.

This package is sent to:

- Structure Engine
- Market Efficiency Engine
- Memory
