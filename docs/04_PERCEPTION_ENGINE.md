# Perception Engine

## Status

Planned next Runtime alignment milestone.

This document describes the intended Perception layer. It should not be read as
part of the currently implemented Runtime Core milestone.

---

## Purpose

The planned Perception Engine is the sensory system of PumpAgent.

It does not predict.

It does not analyze.

It only observes the market exactly as it is.

The quality of every future decision depends on the quality of perception.

---

## Responsibilities

The planned engine collects and normalizes observations such as:

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

In the future live Runtime, the engine must continuously process new market
updates.

Every new candle updates the current observations.

Nothing is ignored.

Everything becomes evidence for future reasoning.

---

## Output

The planned output of Perception Engine is a clean observation package.

This package will be sent to:

- Structure Engine
- Market Efficiency Engine
