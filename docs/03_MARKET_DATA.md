# Market Data

## Purpose

The first responsibility of PumpAgent is not analysis.

The first responsibility is perception.

The agent must continuously collect raw market information before forming any hypothesis.

---

# Raw Market Data

PumpAgent receives:

- Price
- Volume
- Open Interest (OI)
- Aggregated Open Interest
- Funding Rate
- CVD
- Liquidations
- Order Book (future)
- Trades (future)

These are raw observations.

No interpretation happens at this stage.

---

# Timeframes

The agent may observe multiple timeframes simultaneously.

Primary:

- 1m
- 3m
- 5m

Context:

- 15m
- 1H
- 4H

---

# Data Quality

Every incoming update must be checked.

Possible states:

- valid
- delayed
- missing
- corrupted

The agent must never make strong conclusions from poor-quality data.

---

# Perception Output

The Perception Engine does not generate trading signals.

It produces a clean market snapshot that becomes the input for higher-level reasoning.

Output example:

Price:
Volume:
OI:
Funding:
CVD:
Liquidations:

Timestamp:
Exchange:
Symbol:

---

# Design Principle

Perception must remain objective.

Interpretation belongs to higher modules.

The Perception Engine only answers one question:

"What is happening right now?"
