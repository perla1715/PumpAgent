# PumpAgent Research Architecture

## Purpose

This folder defines the research architecture of PumpAgent.

The research layer exists to help PumpAgent learn from historical cases,
evaluate new ideas, compare market structures, and propose improvements to the
runtime system.

Research documentation is not runtime implementation.

It describes how learning and experimentation should be organized before any
idea is accepted into the live architecture.

---

## Base Architecture Map

This map represents the approved target research-aware architecture.

It may include concepts that are not yet fully documented in the current
Runtime architecture files.

Runtime Plane:

```text
Market Data
-> Perception
-> Structure Engine + Market Efficiency Engine
-> Hypothesis Engine
-> Scenario Probability Engine
-> Confidence Engine
-> Decision / Alert
-> Learning Memory
```

Research Plane:

```text
Learning Memory
-> Research Agent
-> Findings
-> Human Review
-> Accepted Improvements
-> Runtime Architecture
```

---

## Runtime Plane

The Runtime Plane is responsible for live market observation and operational
reasoning.

It receives market data, converts it into observations, evaluates structure and
participation, builds hypotheses, estimates future scenarios, updates
confidence, and produces decisions or alerts.

The Runtime Plane is the only plane that may participate in live market
reasoning.

Research documents may reference runtime components, but they do not redefine
them.

---

## Research Plane

The Research Plane is responsible for learning.

It analyzes historical cases, compares market structures, evaluates metrics,
simulates possible improvements, and produces findings.

The Research Plane may analyze, simulate, evaluate, and propose improvements,
but it cannot automatically modify Runtime behavior.

Every change to Runtime behavior must be explicitly approved through Human
Review before implementation.

---

## Governance Rule

This README is the source of truth for Research Plane governance.

Research Plane cannot automatically change Runtime Architecture.

Research findings are proposals, not production changes.

An improvement may enter Runtime Architecture only after:

- the finding is documented;
- the evidence is reviewed;
- the expected impact is understood;
- the human explicitly approves the change;
- the implementation is performed intentionally.

This rule protects PumpAgent from unreviewed self-modification and keeps the
human as the final decision maker.

---

## Non-Execution Principle

Research Plane must never:

- execute trades;
- produce live trading signals;
- trigger live alerts directly;
- bypass Human Review;
- mutate Runtime behavior automatically.

Research Plane may:

- analyze historical cases;
- compare similar structures;
- evaluate metrics;
- simulate ideas;
- generate findings;
- propose improvements.

---

## Architectural Anti-Patterns

These anti-patterns must be avoided:

- Scenario Probability Engine must not become a second Hypothesis Engine.
- Market Scoring System must not become a hidden Decision Engine.
- Research Agent must not become a live trading agent.
- Research findings must not bypass Human Review through automation.

Each document in this folder must preserve clear ownership boundaries.

---

## Documents

### 01_SCENARIO_PROBABILITY_ENGINE.md

Defines the Runtime Plane module that estimates probabilities for possible next
market scenarios after the Hypothesis Engine has produced the current market
explanation.

### 02_RESEARCH_AGENT.md

Defines the Research Plane agent responsible for historical analysis, similarity
search, metric evaluation, findings, and improvement proposals.

### 03_MARKET_SCORING_SYSTEM.md

Defines the multidimensional scoring system used to improve explainability and
communication between agents, while keeping scoring separate from decisions.

---

## Integration Rule

Research outputs do not become runtime behavior automatically.

The path from research to runtime is:

```text
Finding
-> Human Review
-> Accepted Improvement
-> Runtime Architecture Update
```

If a finding does not pass Human Review, it remains a research artifact.

This keeps PumpAgent learning-first without making it uncontrolled.
