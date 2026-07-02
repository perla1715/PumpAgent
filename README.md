# PumpAgent

PumpAgent is an AI Learning-First Trading Agent for market state detection,
market structure reasoning, and continuous learning from explosive price
movements.

PumpAgent is not a trading bot.

It does not blindly execute fixed rules. Its purpose is to observe the market,
build hypotheses, update confidence, and learn from every significant market
case.

---

## Core Philosophy

Price is only the visible result.

The real market exists behind the chart.

PumpAgent studies both:

- Market Structure
- Market Participation

Neither one is sufficient alone.

Only together can they explain what the market is doing.

---

## Processing Flow

Exchange

↓

Bybit Transport

↓

Normalizer

↓

Validation

↓

Quality Translation

↓

Runtime Bridge

↓

MarketSnapshot

↓

Perception Engine

↓

Hypothesis Engine

↓

Agent State Engine

↓

Scenario Probability Engine

↓

Confidence Engine

↓

Decision / Alert

---

## Core Modules

Implemented Runtime Core milestone:

- Perception Engine
- Hypothesis Engine
- Agent State Engine
- Scenario Probability Engine
- Confidence Engine
- Decision / Alert

Planned next Runtime alignment milestones:

- Advanced Perception reasoning
- Advanced Structure reasoning
- Advanced Market Efficiency reasoning

Learning Memory is not part of the current Runtime Orchestrator path.

---

## Documentation

- [Project Vision](docs/00_PROJECT_VISION.md)
- [Architecture](docs/01_ARCHITECTURE.md)
- [Agent States](docs/02_AGENT_STATES.md)
- [Market Data](docs/03_MARKET_DATA.md)
- [Perception Engine](docs/04_PERCEPTION_ENGINE.md)
- [Structure Engine](docs/05_STRUCTURE_ENGINE.md)
- [Market Efficiency Engine](docs/06_MARKET_EFFICIENCY_ENGINE.md)
- [Hypothesis Engine](docs/07_HYPOTHESIS_ENGINE.md)
- [Research Architecture](docs/research/README.md)

---

## Runtime and Research

Runtime Plane is responsible for market observation, hypothesis, agent state,
scenario probability, confidence, and non-execution decisions or alerts.

The current Runtime Orchestrator performs orchestration only and ends at
Decision / Alert. It does not orchestrate Learning Memory.

After `MarketSnapshot`, Runtime modules have no exchange, transport,
normalizer, bridge, or live-data dependencies.

Research Plane is separate from Runtime Plane.

Research Plane analyzes Learning Memory and proposes improvements, but it cannot
modify Runtime behavior without Human Review.

---

## Project Status

PumpAgent is pre-MVP and under active development.

Architecture and documentation are in place for the approved Runtime and
Research separation.

Implemented and tested foundations include:

- Runtime v0.1 domain contracts and deterministic reasoning pipeline through
  Decision / Alert;
- Perception Engine v0.1 skeleton producing objective structural and market
  efficiency evidence;
- Structure Engine expansion skeleton validating Perception-produced structural
  evidence without interpretation;
- Market Efficiency Engine expansion skeleton validating Perception-produced
  market efficiency evidence without interpretation;
- Live Data v0.2 contracts, validation, quality translation, fixture source,
  and fixture flow;
- Bybit public REST Kline transport as a strictly scoped raw acquisition
  adapter;
- Bybit raw payload normalizer;
- Runtime Bridge from validated Live Data input to `MarketSnapshot`.

Current limitations:

- no trading execution;
- no autonomous trading signals;
- no WebSocket streaming;
- no private exchange API access;
- no persistence;
- no Research Agent execution;
- no Learning Memory orchestration inside the Runtime Orchestrator.

Documentation is synchronized after major architecture and implementation
milestones.

---

## Disclaimer

PumpAgent is a research and learning project.

It is not financial advice and should not be used as an autonomous trading
system without human review.

The human always has the final decision.
