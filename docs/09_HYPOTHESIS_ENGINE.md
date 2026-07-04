# Hypothesis Engine

## Status

MVP implemented.

---

## Purpose

The Hypothesis Engine creates a working interpretation of the current market.

It explains what appears to be happening now. It does not predict what will
happen next, make decisions, or imply trading action.

---

## Core Principle

`Agent = State + Confidence + Evidence + Hypothesis`

The MVP hypothesis consumes:

- market state;
- confidence score;
- evidence.

It returns an interpretation that can be created, updated, weakened, or
replaced as new market data arrives.

---

## MVP Model

`MarketHypothesis` contains:

- `id`
- `label`
- `summary`
- `market_state`
- `confidence_score`
- `evidence`
- `supporting_evidence`
- `contradicting_evidence`
- `status`
- `lifecycle_reason`
- `previous_hypothesis_id`

## Hypothesis Snapshot v1

`HypothesisSnapshot` records the current interpretation context without changing
runtime behavior.

It contains:

- `state`
- `confidence`
- `confidence_trend`
- `evidence_summary`
- `created_at`
- `label`

Snapshot labels are deterministic and descriptive only:

- `unknown`
- `low_evidence`
- `structural_only`
- `market_only`
- `temporal_only`
- `mixed_evidence`

The snapshot does not modify `AgentState`, `Confidence`, or existing
hypotheses. It does not make decisions, assign probabilities, or imply trading
action.

## Hypothesis History v1

`HypothesisHistory` is a bounded in-memory container for recent
`HypothesisSnapshot` objects.

It supports:

- `append(snapshot)`
- `latest()`
- `previous()`
- `size()`
- `clear()`

Older snapshots are discarded automatically when the configured maximum history
length is exceeded.

History is diagnostic only. It does not modify `AgentState`, `Confidence`,
hypothesis logic, alerts, probabilities, or trading decisions.

## History Trend Analyzer v1

`HistoryTrendAnalyzer` reads `HypothesisHistory` and returns a
`HistoryTrendSummary` with:

- `confidence_trend`
- `evidence_score_trend`
- `label_stability`
- `sample_size`

Supported trend values are:

- `IMPROVING`
- `STABLE`
- `WEAKENING`
- `UNKNOWN`

Empty or single-snapshot history returns `UNKNOWN`. The analyzer is deterministic
and diagnostic only; it does not modify runtime behavior or generate decisions.

---

## MVP Labels

Market state maps to hypothesis labels:

- `IGNITION` -> `Ignition attempt`
- `CONTINUATION_ALIVE` -> `Continuation remains active`
- `WEAKENING` -> `Move is weakening`
- `UNKNOWN` -> `No clear hypothesis`

---

## Lifecycle

If no previous hypothesis exists, status is `CREATED`.

If the label remains the same and confidence is stable or higher, status is
`UPDATED`.

If the label remains the same and confidence is lower, status is `WEAKENED`.

If the label changes, status is `REPLACED` and the previous hypothesis id is
preserved.

---

## Boundaries

The Hypothesis Engine does not:

- collect raw market data;
- classify market state;
- calculate confidence;
- collect evidence;
- assign scenario probabilities;
- make decisions;
- generate alerts;
- use trading instructions.

It consumes prepared state, confidence, and evidence, then produces a current
interpretation.
