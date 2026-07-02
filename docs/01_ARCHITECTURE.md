# PumpAgent Architecture

## Core Principle

PumpAgent is not a rule-based bot.

PumpAgent is an AI Learning-First Trading Agent.

The system thinks in hypotheses rather than fixed rules.

Every market update may:

- increase confidence;
- decrease confidence;
- invalidate the current hypothesis;
- create a new hypothesis;
- change the probability of possible next scenarios.

---

# Runtime Plane

The Runtime Plane is responsible for market observation and operational
reasoning after market data has been converted into a Runtime `MarketSnapshot`.

It observes the market, builds the current explanation, estimates possible next
scenarios, evaluates confidence, and produces non-execution decisions or alerts.

---

# Implemented Data-To-Runtime Flow

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

The Live Data side owns acquisition, normalization, validation, and quality
translation.

The Runtime Bridge is the only boundary component that creates a Runtime
`MarketSnapshot`.

Runtime reasoning modules do not communicate with exchanges, transports,
normalizers, validators, quality translators, or bridge components.

---

# Runtime Core Processing Flow

MarketSnapshot

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

The current Runtime Orchestrator coordinates this flow only.

It performs orchestration and immutable `RuntimeEvent` handoff. It does not
perform market analysis, create hypotheses, calculate confidence, classify
alerts, access Live Data, or execute trades.

Runtime Core currently ends at Decision / Alert.

Learning Memory is not orchestrated by the Runtime Orchestrator.

---

# Thinking Sequence

The implemented Runtime Core milestone is:

MarketSnapshot

↓

HypothesisPackage

↓

AgentState

↓

ScenarioProbability

↓

ConfidenceAssessment

↓

DecisionAlert

Each Runtime module is deterministic, side-effect free, and owns exactly one
RuntimeEvent section.

No Runtime module mutates previous sections.

---

# Planned Evidence Preparation Milestones

Perception Engine, Structure Engine, and Market Efficiency Engine are planned
next Runtime alignment milestones around the current Runtime Core.

They define the intended evidence-preparation path:

MarketSnapshot

↓

ObservationPackage

↓

StructuralEvidence + MarketEfficiencyEvidence

These documents and contracts should not be read as the implemented Runtime
Core milestone unless explicitly connected through an approved implementation
step.

In that planned path, Structure Engine and Market Efficiency Engine are
parallel evidence producers.

They both receive observations from the Perception Engine.

Structure Engine studies how price behaves.

Market Efficiency Engine studies participation and internal market mechanics.

Their evidence is combined by the Hypothesis Engine.

---

# Modules

## 1. Perception Engine

Status: planned next Runtime alignment milestone.

The planned Perception Engine reads the Runtime `MarketSnapshot` and produces
neutral observations.

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

Status: planned next Runtime alignment milestone.

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

The engine produces structural evidence.

It does not make trading decisions.

---

## 3. Market Efficiency Engine

Status: planned next Runtime alignment milestone.

Reads internal market mechanics.

Examples:

- Participation
- OI Growth
- Funding
- Absorption
- Price Efficiency
- Volume Efficiency

The engine produces participation and efficiency evidence.

It does not make trading decisions.

---

## 4. Hypothesis Engine

Creates the current explanation of the market.

Example:

"The continuation is still alive, but participation quality is weakening."

The Hypothesis Engine explains what is most likely happening now.

It does not own future scenario probability distribution.

---

## 5. Scenario Probability Engine

Estimates possible next scenarios after the current hypothesis and official
current Agent State have been built.

Example:

- Continuation persists
- Continuation degrades into saturation
- First failure emerges

The Scenario Probability Engine does not make trading decisions.

It supports reasoning before confidence is evaluated.

---

## 6. Confidence Engine

Evaluates the final reliability of the current hypothesis, Agent State, and
scenario probabilities.

Confidence can:

- increase;
- decrease;
- stay unchanged.

The agent never falls in love with its own prediction.

---

## 7. Decision / Alert

Produces non-execution operational outputs.

Examples:

- Observe
- Wait
- Warning
- Alert
- Review Required
- Human Decision Required
- Unknown

Decision / Alert does not execute trades.

The human always has the final decision.

---

## 8. Learning Memory

Learning Memory is not part of the current Runtime Orchestrator path.

It remains a separate boundary for future storage and Research Plane workflows.

It may prepare important cases for later review when explicitly connected, but
it is not currently orchestrated as part of Runtime Core.

Learning Memory must not change Runtime behavior automatically.

Research Plane work starts only from reviewed or stored cases and remains
separate from live Runtime decisions.

---

# Research Plane

Research Plane is separate from Runtime Plane.

It starts from Learning Memory and is responsible for historical analysis,
findings, and improvement proposals.

Research Plane cannot automatically modify Runtime behavior.

Every change to Runtime behavior must pass Human Review before implementation.

See [Research Architecture](research/README.md).
