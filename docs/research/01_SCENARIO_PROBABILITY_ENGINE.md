# Scenario Probability Engine

## Purpose

The Scenario Probability Engine estimates probabilities for possible next
market scenarios.

It answers one question:

"Given the current market hypothesis, what scenarios may happen next?"

The engine does not replace current state classification.

It does not replace the Hypothesis Engine.

It does not make trading decisions.

---

## Runtime Plane Position

The Scenario Probability Engine belongs to the Runtime Plane.

Its position is:

```text
Hypothesis Engine
-> Scenario Probability Engine
-> Confidence Engine
```

The Hypothesis Engine explains the current market condition.

The Scenario Probability Engine estimates possible next scenarios.

The Confidence Engine evaluates the reliability of those scenario probabilities.

Final scenario reliability is evaluated after Scenario Probability Engine runs.

Scenario Probability Engine may use prior confidence context, but it does not
produce the final confidence judgment.

---

## Ownership Boundaries

### Agent States

Agent States define the current official market state.

Examples:

- UNKNOWN
- IGNITION
- CONTINUATION ALIVE
- CONTINUATION SATURATION
- FIRST FAILURE CANDIDATE
- FIRST FAILURE
- CONTINUATION DEATH

### Hypothesis Engine

Hypothesis Engine builds the best current explanation of the market.

Example:

"Continuation is still alive, but participation quality is weakening."

### Scenario Probability Engine

Scenario Probability Engine estimates the probability of possible next paths.

Example:

```text
Continuation persists: 65%
Continuation degrades into saturation: 25%
First failure emerges: 10%
```

### Confidence Engine

Confidence Engine evaluates how reliable the hypothesis and scenario
probabilities are.

---

## Current State vs Future Scenario

Current state and future scenario must not be mixed.

Correct:

```text
Current State: CONTINUATION ALIVE

Next Scenario Probabilities:
- Continuation persists: 65%
- Continuation degrades into saturation: 25%
- First failure emerges: 10%
```

Incorrect:

```text
Current State:
- Continuation: 65%
- Saturation: 25%
- Failure: 10%
```

The state describes what the market is now.

Scenario probabilities describe what may happen next.

---

## Inputs

The Scenario Probability Engine may use:

- current hypothesis;
- current market state;
- structure evidence;
- market efficiency evidence;
- current hypothesis confidence;
- prior confidence context;
- data quality;
- score dimensions;
- recent state transitions;
- historical priors, when available.

Historical priors must be treated carefully. They may support reasoning, but
they must not override current evidence.

---

## Outputs

The engine produces:

- list of possible next scenarios;
- probability for each scenario;
- supporting evidence;
- evidence against each scenario;
- uncertainty level;
- monitoring focus;
- notes for Confidence Engine.

The output should remain explanatory.

It should not produce trading signals.

---

## Example Output

This example is illustrative only.

It is not a formula, required scenario set, or target probability distribution.

```text
Current State: CONTINUATION ALIVE
Current Hypothesis: Continuation remains valid, but quality is weakening.

Next Scenario Probabilities:
- Continuation persists: 55%
- Continuation saturation develops: 30%
- First failure emerges: 15%

Supporting Evidence:
- structure remains intact;
- participation is still present;
- momentum is slowing;
- risk is rising.

Monitoring Focus:
- failed reclaim;
- participation deterioration;
- CVD divergence;
- liquidation spike.
```

---

## Design Principles

The engine must follow these principles:

- scenarios over predictions;
- probabilities over certainty;
- evidence-linked probabilities;
- uncertainty must be visible;
- current state must remain separate from future scenario;
- no direct decision output;
- no execution signals.

---

## Risks

### Duplicate Hypothesis Engine

If this engine starts explaining the current market, it becomes a second
Hypothesis Engine.

That must be avoided.

### False Precision

Probabilities can appear more precise than they really are.

Every probability should be connected to evidence and uncertainty.

### Overfitting

Historical cases may influence probabilities, but current market evidence must
remain primary.

### State Confusion

The engine must not convert scenario probabilities into current state labels.

---

## Non-Execution Rule

This follows the Governance Rule defined in `README.md`.

Scenario Probability Engine must never:

- execute trades;
- generate live trading signals;
- directly trigger Decision / Alert;
- bypass Confidence Engine;
- replace human judgment.

It supports reasoning.

It does not decide.
