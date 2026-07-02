# Hypothesis Engine

## Purpose

The Hypothesis Engine is the reasoning center of PumpAgent.

It combines all available evidence and builds the current understanding of the
market.

The engine never predicts the future.

It explains what is most likely happening now.

Future scenario probabilities belong to the Scenario Probability Engine.

---

## Inputs

In the implemented Runtime Core milestone, the engine receives prepared evidence
contracts:

- StructuralEvidence
- MarketEfficiencyEvidence

Perception Engine, Structure Engine, and Market Efficiency Engine are planned
Runtime alignment milestones around this evidence path.

Learning Memory is not an input to the current Runtime Core Hypothesis Engine.

---

## Core Principle

Every hypothesis has:

- Evidence
- Current hypothesis confidence context
- Alternative explanations

Current hypothesis confidence context is never fixed.

Every market update may:

- increase current hypothesis confidence context;
- decrease current hypothesis confidence context;
- invalidate the hypothesis;
- create a better hypothesis.

Final reliability evaluation belongs to the Confidence Engine after the
Scenario Probability Engine runs.

The Hypothesis Engine does not own final confidence scoring.

---

## Thinking Process

Instead of asking:

"What will happen?"

The engine asks:

"What is currently the most probable explanation?"

It does not ask:

"What will probably happen next?"

That question belongs to the Scenario Probability Engine.

---

## Example

Hypothesis:

Continuation Alive

Confidence:

82%

Evidence:

- Healthy structure
- Rising Open Interest
- Strong participation
- Positive price efficiency

Alternative:

First Failure Candidate

Confidence:

18%

---

## Dynamic Thinking

The market changes.

The hypothesis must change with it.

The engine is allowed to change its mind whenever new evidence appears.

Changing a hypothesis is not a mistake.

Ignoring new evidence is.

---

## Output

The engine produces:

- Current hypothesis
- Current hypothesis confidence
- Supporting evidence
- Competing hypotheses

It does not produce the official Agent State.

The Hypothesis Engine may compare competing current explanations.

It does not own the probability distribution of possible next scenarios.

It also does not own final confidence scoring.
