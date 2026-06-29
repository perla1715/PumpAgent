# PumpAgent Architecture

## Core Principle

PumpAgent is not a rule-based bot.

PumpAgent is an AI Learning-First Trading Agent.

The system thinks in hypotheses rather than fixed rules.

Every market update either:

- increases confidence,
- decreases confidence,
- or creates a new hypothesis.

---

# Main Processing Flow

Market Data

↓

Perception Engine

↓

Structure Engine

↓

Market Efficiency Engine

↓

Hypothesis Engine

↓

Confidence Engine

↓

Decision Engine

↓

Learning Memory

---

# Modules

## 1. Perception Engine

Reads raw market data.

Examples:

- Price
- OHLCV
- Open Interest
- Aggregated Open Interest
- Funding
- CVD
- Liquidations

No interpretation.

Only observation.

---

## 2. Structure Engine

Reads chart structure.

Examples:

- EMA
- Bollinger
- Fib
- Higher High
- Lower High
- Compression
- Expansion
- Reclaim
- Breakdown

---

## 3. Market Efficiency Engine

Reads internal market mechanics.

Examples:

- Participation
- OI Growth
- Funding
- Absorption
- Price Efficiency
- Volume Efficiency

---

## 4. Hypothesis Engine

The brain.

Creates the current explanation of the market.

Example:

"The continuation is still alive."

or

"Continuation is weakening."

or

"First Failure is becoming likely."

---

## 5. Confidence Engine

Every new observation changes confidence.

Confidence can:

Increase

Decrease

Stay unchanged

The agent never falls in love with its own prediction.

---

## 6. Decision Engine

Produces actions.

Examples:

Observe

Wait

Warning

High Probability

Unknown

---

## 7. Learning Memory

Stores every case.

Each new market teaches the agent something.

Knowledge grows continuously.
