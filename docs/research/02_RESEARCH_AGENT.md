# Research Agent

## Purpose

The Research Agent is responsible for learning from historical market cases.

It compares cases, evaluates metrics, studies failed hypotheses, and proposes
improvements to PumpAgent's reasoning.

The Research Agent does not participate in live decisions.

It exists to improve the quality of future reasoning, not to execute trades or
produce live signals.

---

## Research Plane Position

The Research Agent belongs to the Research Plane.

Its position is:

```text
Learning Memory
-> Research Agent
-> Findings
-> Human Review
-> Accepted Improvements
-> Runtime Architecture
```

The Research Agent uses stored cases and historical evidence.

It does not have direct access to:

```text
Decision / Alert
```

It must never read directly from the live Decision / Alert path.

---

## Responsibilities

The Research Agent may:

- compare historical cases;
- search for similar market structures;
- evaluate new metrics;
- analyze failed hypotheses;
- analyze successful hypotheses;
- study state transitions;
- review scenario probability outcomes;
- review score behavior over time;
- generate research findings;
- suggest improvements to runtime agents;
- document lessons learned.

---

## Forbidden Responsibilities

The Research Agent must never:

- execute trades;
- produce live trading signals;
- trigger live alerts;
- change Runtime behavior automatically;
- modify Runtime Architecture without approval;
- override the Decision / Alert layer;
- bypass Human Review.

The Research Agent can propose.

The human decides.

---

## Inputs

The Research Agent may use:

- Learning Memory;
- historical market cases;
- observation templates;
- state transition history;
- hypothesis outcomes;
- scenario probability outcomes;
- score history;
- human annotations;
- post-case reviews.

Inputs should preserve context.

A case without context is weak evidence.

---

## Outputs

The Research Agent may produce:

- research findings;
- similarity reports;
- failed hypothesis analysis;
- metric evaluation notes;
- proposed score improvements;
- proposed scenario model improvements;
- architecture improvement proposals;
- lessons learned.

Outputs should explain the evidence behind the conclusion.

They should not be formatted as trading instructions.

---

## Research Workflow

The standard workflow is:

```text
Select historical cases
-> Compare structures and participation
-> Identify similarities and differences
-> Evaluate hypotheses and outcomes
-> Generate finding
-> Propose improvement
-> Submit to Human Review
```

The workflow is intentionally separate from live market decisions.

---

## Human Review Gate

This follows the Governance Rule defined in `README.md`.

An accepted improvement may become part of Runtime Architecture only after human
approval.

Rejected or unreviewed findings remain research artifacts.

---

## Interaction With Runtime Plane

The Research Agent may inspect outputs from Runtime Plane after they are stored
in Learning Memory.

This includes decisions or alerts only after the event has been stored in
Learning Memory, never directly from the live Decision / Alert path.

It may study:

- what the agent observed;
- what hypothesis was created;
- how confidence changed;
- what scenario probabilities were estimated;
- what decision or alert was produced;
- what actually happened later.

It may not alter live runtime behavior directly.

---

## Design Principles

The Research Agent must follow these principles:

- learning over execution;
- evidence over opinion;
- historical review over live intervention;
- findings over signals;
- human review over automatic adoption;
- improvement proposals over self-modification.

The Research Agent is successful when it improves future reasoning quality
without weakening runtime safety.
