# Market Scoring System

## Purpose

The Market Scoring System defines a multidimensional scoring layer for
PumpAgent.

Scores help explain market conditions, compare cases, communicate between
agents, and track quality changes over time.

Scores support reasoning.

They do not replace states, hypotheses, confidence, or decisions.

---

## Architectural Role

The Market Scoring System may be used in the Runtime Plane as an explanatory
and analytical layer.

Scores may support:

- Structure Engine;
- Market Efficiency Engine;
- Hypothesis Engine;
- Scenario Probability Engine;
- Confidence Engine.

Scoring design, scoring evaluation, and scoring improvements may be studied in
the Research Plane before any accepted change reaches Runtime Architecture.

---

## Runtime Usage

In Runtime Plane, scores may help agents express the quality of current market
conditions.

Example:

```text
Current State: CONTINUATION ALIVE

Scores:
- Structure Score: 78
- Participation Score: 71
- Momentum Score: 64
- Continuation Quality: 69
- Risk Score: 32
- Data Quality Score: 91
- Confidence Score: 76
```

These scores make the reasoning easier to inspect.

They should not create automatic decisions by themselves.

---

## Research Usage

In Research Plane, scores may be analyzed to understand whether they helped or
misled the system.

Research Agent may evaluate:

- whether a score dimension is useful;
- whether a score drifted over time;
- whether normalization is consistent;
- whether weights are biased;
- whether scores explain historical outcomes;
- whether new score dimensions should be proposed.

Any scoring improvement must pass Human Review before it changes Runtime
behavior.

---

## Score Dimensions

Initial score dimensions:

- Structure Score
- Participation Score
- Momentum Score
- Continuation Quality
- Risk Score
- Data Quality Score
- Confidence Score

Confidence Score is owned or validated by the Confidence Engine.

Market Scoring may represent confidence only for explainability, historical
review, and cross-agent communication.

Each score must have its own definition.

Different scores must not be treated as interchangeable.

For example, `Risk Score: 80` does not mean the same thing as
`Structure Score: 80`.

---

## State + Score Model

Discrete states remain the primary market state labels.

Scores add detail.

Correct:

```text
Current State: CONTINUATION SATURATION

Scores:
- Structure Score: 62
- Participation Score: 48
- Momentum Score: 41
- Continuation Quality: 39
- Risk Score: 74
```

Incorrect:

```text
Overall Market Score: 68
Decision: Enter trade
```

Scores do not replace reasoning.

Scores support reasoning.

---

## Normalization Rules

Scores should use a clear numeric range, such as `0-100`.

All public scores should use `0-100` unless a score explicitly declares a
different range.

However, every score requires its own semantic contract.

Each score should define:

- what low values mean;
- what medium values mean;
- what high values mean;
- which evidence influences the score;
- how data quality affects the score;
- how uncertainty is represented.

Raw values should be preserved when possible.

Normalized scores should not hide the original evidence.

---

## Evidence Requirement

Every score should be explainable.

A score should include:

- supporting evidence;
- evidence against;
- reason for change;
- data quality;
- uncertainty;
- timestamp;
- market context;
- source module.

A score without evidence is weak.

A score without uncertainty is dangerous.

---

## Overall Market Score

An Overall Market Score is optional.

It should be avoided until individual score dimensions are stable.

If introduced, it may exist only as a summary.

It must not become the main decision mechanism.

If used, it should remain secondary to:

- current state;
- current hypothesis;
- scenario probabilities;
- confidence;
- evidence;
- human review.

The system should avoid hiding complex market conditions behind one aggregate
number.

---

## Governance

This follows the Governance Rule defined in `README.md`.

This includes changes to:

- score dimensions;
- score formulas;
- normalization rules;
- score weights;
- aggregate score logic;
- score usage in runtime agents.

---

## Risks

### False Precision

Scores may appear more objective than they are.

Every score must remain evidence-linked.

### Hidden Decision Logic

Market Scoring System must not become a hidden Decision Engine.

Scores should not trigger decisions directly.

### Normalization Drift

Score meaning may change over time if definitions are not stable.

Score formulas should be versioned when implemented.

### Over-Aggregation

One overall number can hide important disagreement between dimensions.

For example, strong structure and weak participation should remain visible.

### Cross-Score Confusion

Scores should not be compared without context.

Each score measures a different dimension of the market.

---

## Design Principles

The Market Scoring System must follow these principles:

- explainability over simplification;
- dimensions over one aggregate number;
- evidence over clean-looking scores;
- normalized values with raw context;
- score history over isolated values;
- human review over automatic adoption;
- support reasoning, never replace it.
