# Hypothesis Engine

## Status

Implemented MVP.

This document is the canonical Hypothesis Engine documentation for the current
MVP.

The Hypothesis Engine consumes upstream evidence and produces current-market
explanations. It may create, update, weaken, or replace hypotheses.

It does not fetch market data directly, mutate upstream evidence, produce final
trading execution commands, own Telegram alerts, or orchestrate Runtime behavior.

---

## Current Paths

The current implementation has two paths.

### Clean HypothesisPackage Path

The clean package path consumes:

- `StructuralEvidence`
- `MarketEfficiencyEvidence`

It produces:

- `HypothesisPackage`

Primary APIs:

- `build_hypothesis_package()`
- `add_hypothesis_package()`

This path combines prepared objective evidence into a current-condition
explanation.

It preserves upstream evidence and writes only `hypothesis_package` when used
through `add_hypothesis_package()`.

### Legacy / Runtime Scanner MarketHypothesis Path

The legacy runtime scanner path is still present for compatibility.

Primary API:

- `build_hypothesis()`

This path consumes arbitrary runtime scanner data and may call:

- `detect_market_state()`
- `calculate_confidence()`
- `collect_evidence()`

It produces:

- `MarketHypothesis`

This path supports the current Runtime loop scanner-style flow. It is not the
clean `HypothesisPackage` evidence contract path.

---

## Public API And Exports

Core exports:

- `HypothesisError`
- `MarketHypothesis`
- `build_hypothesis()`
- `build_hypothesis_package()`
- `add_hypothesis_package()`

Snapshot and history exports:

- `HypothesisSnapshot`
- `HypothesisSnapshotBuilder`
- `HypothesisHistory`
- `HistoryTrendAnalyzer`
- `HistoryTrendSummary`
- `build_hypothesis_snapshot()`

Evaluator exports:

- `HypothesisEvaluator`
- `HypothesisEvaluation`

Trend constants:

- `TREND_IMPROVING`
- `TREND_STABLE`
- `TREND_WEAKENING`
- `TREND_UNKNOWN`

Evaluation constants:

- `EVALUATION_REINFORCED`
- `EVALUATION_NEUTRAL`
- `EVALUATION_WEAKENING`
- `EVALUATION_UNKNOWN`

---

## Clean Package Model

`HypothesisPackage` contains:

- `event_id`
- `hypothesis_label`
- `hypothesis_summary`
- `supporting_evidence`
- `contradicting_evidence`
- `competing_hypotheses`
- `current_hypothesis_confidence_context`
- `reasoning_notes`
- `schema_version`
- `previous_hypothesis`
- `hypothesis_change_reason`
- `invalidated_hypotheses`
- `historical_similarity_notes`
- `uncertainty`
- `assumptions`

The current MVP uses the label:

- `current_condition_explanation`

The package path does not decide official state, final confidence, alerts,
trades, or future scenario probabilities.

---

## Legacy MarketHypothesis Model

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

MVP market-state labels are:

- `IGNITION` -> `Ignition attempt`
- `CONTINUATION_ALIVE` -> `Continuation remains active`
- `WEAKENING` -> `Move is weakening`
- `UNKNOWN` -> `No clear hypothesis`

---

## Lifecycle

Hypothesis lifecycle statuses are deterministic:

- `CREATED`: no previous hypothesis exists.
- `UPDATED`: the label is unchanged and confidence is stable or higher.
- `WEAKENED`: the label is unchanged and confidence is lower.
- `REPLACED`: the label changed, and the previous hypothesis id is preserved.

---

## Confidence Behavior

The legacy `MarketHypothesis` path may contain a numeric `confidence_score`.

`HypothesisPackage` emits `current_hypothesis_confidence_context`.

`current_hypothesis_confidence_context` is not final market confidence and not
trade confidence.

Final confidence remains the responsibility of the Confidence Engine if or when
that layer is used.

---

## Hypothesis Snapshot MVP

`HypothesisSnapshot` records current interpretation context without changing
Runtime behavior.

It contains:

- `state`
- `confidence`
- `confidence_trend`
- `evidence_summary`
- `created_at`
- `label`

Snapshot labels are deterministic and descriptive:

- `unknown`
- `low_evidence`
- `structural_only`
- `market_only`
- `temporal_only`
- `mixed_evidence`

Snapshots do not modify `AgentState`, confidence, hypotheses, alerts,
probabilities, or trading decisions.

---

## Hypothesis History MVP

`HypothesisHistory` is a bounded in-memory container for recent
`HypothesisSnapshot` objects.

It supports:

- `append(snapshot)`
- `latest()`
- `previous()`
- `size()`
- `clear()`

Older snapshots are discarded when the configured maximum history length is
exceeded.

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

Empty or single-snapshot history returns `UNKNOWN`.

History and trend analysis are diagnostic only. They do not modify runtime
behavior, hypotheses, confidence, alerts, probabilities, or trading decisions.

---

## Hypothesis Evaluator MVP

`HypothesisEvaluator` reads the current `HypothesisSnapshot` and
`HistoryTrendSummary`, then returns a diagnostic `HypothesisEvaluation`.

Evaluation statuses are:

- `REINFORCED`
- `NEUTRAL`
- `WEAKENING`
- `UNKNOWN`

The evaluator uses deterministic trend rules:

- improving confidence and improving evidence score -> `REINFORCED`
- stable confidence and stable evidence score -> `NEUTRAL`
- weakening confidence or weakening evidence score -> `WEAKENING`
- missing, unknown, or mixed trend context -> `UNKNOWN`

Evaluation is diagnostic only. It does not modify confidence, hypotheses, state
transitions, runtime decisions, alerts, probabilities, or trading behavior.

---

## Boundaries

The Hypothesis Engine:

- consumes upstream evidence;
- may form, update, weaken, or replace hypotheses;
- may reason about current state context;
- may emit hypothesis confidence context;
- does not fetch market data directly;
- does not mutate upstream evidence;
- does not produce final trading execution commands;
- does not own Telegram alerts;
- does not own Runtime orchestration;
- does not own future scenario probabilities;
- does not own final trade confidence.
