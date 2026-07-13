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

# Lightweight Runtime Loop MVP

MarketSnapshot

↓

Structure Engine

↓

Market Efficiency Engine

↓

Hypothesis Engine

↓

State Update

↓

AgentCycleResult

The Runtime Orchestrator coordinates this reasoning loop for each market
update. It receives one `MarketSnapshot`, builds structure and market
efficiency evidence, combines them with snapshot metrics, builds the current
hypothesis, maps the hypothesis state into canonical `AgentState`, and returns
an `AgentCycleResult`.

The Runtime Orchestrator does not make trading decisions, persist state,
communicate with users, or call external services.

The older `RuntimeEvent` contract modules for Agent State, Scenario Probability,
Confidence Assessment, and Decision / Alert remain available as separate
section-owning contracts while the lightweight runtime loop is established.

The fixture Runtime Orchestrator still supports immutable `RuntimeEvent`
handoff for the older module contract path. It does not perform market
analysis, classify alerts, access Live Data, or execute trades.

The older `RuntimeEvent` contract path currently ends at Decision / Alert.

Learning Memory is not orchestrated by the Runtime Orchestrator.

---

# RuntimeEvent Contract Path

The older immutable `RuntimeEvent` contract path is:

MarketSnapshot

↓

StructuralEvidence + MarketEfficiencyEvidence

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

Each module in this contract path is deterministic, side-effect free, and owns
exactly one RuntimeEvent section.

No module in this contract path mutates previous sections.

## Agent Runtime Loop MVP

The first complete agent reasoning cycle returns `AgentCycleResult` with:

- runtime event id;
- snapshot;
- structure result;
- market efficiency result;
- previous state;
- new state;
- canonical agent state;
- hypothesis;
- confidence;
- evidence;
- timestamp;
- watchlist action;
- watchlist observation count;
- temporal confidence;
- confidence trend;
- confidence delta;
- diagnostic report;
- log messages.

This result is a deterministic in-memory object. Runtime logging is represented
as returned messages only; the loop does not write logs, use a database, call
Telegram, or execute trades.

### Diagnostic Runtime Report v1

`DiagnosticRuntimeReport` packages the current cycle's diagnostic-only outputs
into one immutable object:

- state;
- confidence;
- confidence trend;
- temporal confidence;
- evidence summary;
- hypothesis snapshot;
- hypothesis history size;
- history trend summary;
- created timestamp.

The report is output-only. It does not modify `AgentState`, `Confidence`,
Hypothesis logic, alerts, probabilities, or trading decisions.

### Runtime Logging MVP

Runtime logging is currently a side-effect-free serialization layer.

`serialize_agent_cycle_result(result)` converts an `AgentCycleResult` into a
plain dictionary with cycle identity, timestamp, market identity, state,
hypothesis, confidence, evidence, and agent state identity fields.

The current schema version is `runtime_cycle_v1`. Future changes to the log
shape should create a new schema version instead of silently changing existing
fields.

The serializer does not write files, use a database, call external services, or
modify the cycle result. Persistence can be added later behind a separate
storage boundary.

`AgentState` is the canonical state object for the runtime loop. The
compatibility `previous_state` and `new_state` strings are derived from
`AgentState.previous_state` and `AgentState.current_state`.

Each runtime loop cycle has a deterministic event id derived from snapshot
identity, symbol, exchange, timeframe, and timestamp. This cycle event id is
passed into `AgentState.event_id`, while `MarketHypothesis.id` remains a
semantic hypothesis identifier.

Detected `WEAKENING` is staged by previous official state. From `UNKNOWN` or
`IGNITION`, it remains `UNKNOWN`. From `CONTINUATION_ALIVE`, it becomes
`CONTINUATION_SATURATION`. From `CONTINUATION_SATURATION`, it becomes
`FIRST_FAILURE_CANDIDATE`. Repeated weakening while already in
`FIRST_FAILURE_CANDIDATE` remains `FIRST_FAILURE_CANDIDATE`.

The clean `HypothesisPackage` path remains conservative and returns
`AgentStateType.UNKNOWN` until the clean hypothesis contract contains explicit
state context. Agent State must translate upstream state context; it must not
infer official state from generic evidence.

### Dynamic Watchlist MVP

The Dynamic Watchlist tracks interesting markets across runtime cycles in
memory only.

Markets are registered when canonical `AgentState.current_state` is not
`UNKNOWN`. Repeated interesting cycles update the existing watchlist entry,
increment `observation_count`, and replace the latest state, hypothesis,
confidence, event id, and `last_updated` timestamp.

Unknown states are ignored. Expiration policies are not implemented in the MVP;
only explicit removal is supported. The watchlist does not persist data, use a
database, call Telegram, or run asynchronously.

### Temporal Confidence Engine MVP

The Temporal Confidence Engine tracks confidence evolution for watchlisted
markets across runtime cycles.

Runtime order:

`Runtime -> Watchlist -> Temporal Confidence -> AgentCycleResult`

Temporal confidence compares the previous confidence for a watchlist entry with
the current confidence and reports:

- current confidence;
- previous confidence;
- confidence delta;
- trend;
- update count;
- last updated timestamp.

Supported trends are `IMPROVING`, `STABLE`, `WEAKENING`, and `UNKNOWN`.

Temporal confidence is diagnostic only. It does not change Agent State
transitions, create decisions, persist data, use a database, call Telegram, or
run asynchronously.

---

# Implemented Perception MVP

Perception Engine v0.1 is implemented as a Runtime-safe MVP.

The clean Perception evidence pipeline reads only `MarketSnapshot` and produces
objective evidence contracts:

MarketSnapshot

↓

StructuralEvidence + MarketEfficiencyEvidence

The primary clean evidence API is:

- `build_perception_evidence()`

Perception also exposes ObservationPackage preparation APIs:

- `build_observation_package()`
- `add_observation_package()`

`ObservationPackage` is currently part of the public Perception output and
prepares normalized Runtime-facing market data for downstream modules such as
Structure.

The clean Perception evidence pipeline does not perform market interpretation.

It does not:

- create hypotheses;
- classify Agent State;
- assign scenario probabilities;
- calculate confidence;
- generate decisions or alerts;
- produce trading signals;
- orchestrate runtime behavior;
- access Learning Memory;
- access Research Plane;
- access exchange, transport, bridge, validation, normalizer, or Live Data
  layers.

Perception also retains a legacy scanner compatibility layer:

- `detect_market_state()`
- `format_market_state_scan_line()`
- `print_market_state_scan()`

Scanner state labels, `CONF` formatting, and market-state scan output belong
only to that legacy compatibility layer. They are not part of
`build_perception_evidence()` or `build_observation_package()`.

Structure Engine has an implemented MVP that can build objective
`StructuralEvidence` from `ObservationPackage.normalized_ohlcv` without adding
interpretation.

It computes EMA7/14/21 with full-period warmup, detects 2-left / 2-right pivot
swings, derives the latest valid impulse, and emits Fibonacci levels only when
that impulse exists.

It keeps `ChartStructure` internal by convention and serializes stable
structural facts into `StructuralEvidence.technical_context["chart_structure"]`.

Serialized EMA evidence includes latest EMA values and availability metadata,
not full EMA series.

Market Efficiency Engine has an implemented MVP that builds objective
`MarketEfficiencyEvidence` from `ObservationPackage` without adding
interpretation.

## MVP Refinement Contract

Perception owns clean observation packaging and may still create evidence for
legacy compatibility.

Structure Engine and Market Efficiency Engine do not replace evidence ownership.

They may refine evidence by returning an updated evidence object of the same
domain type:

- `StructuralEvidence` -> `StructuralEvidence`
- `MarketEfficiencyEvidence` -> `MarketEfficiencyEvidence`

MVP refinement may enrich evidence context with objective facts only.

Refinement must preserve:

- Runtime `event_id`;
- source snapshot identity stored in evidence context;
- evidence domain type;
- downstream Runtime boundaries.

Refinement must not:

- classify market state;
- create hypotheses;
- calculate scenario probabilities;
- calculate confidence;
- generate decisions or alerts;
- use trading language;
- access Learning Memory, Research Plane, Live Data, exchange adapters, bridge,
  validation, normalizers, or transport layers.

In future expansion, Structure Engine and Market Efficiency Engine may deepen
objective evidence before that evidence is consumed by the Hypothesis Engine.

---

# Modules

## 1. Perception Engine

Status: implemented MVP.

Perception Engine v0.1 reads the Runtime `MarketSnapshot` and produces objective
`StructuralEvidence` and `MarketEfficiencyEvidence`.

Examples:

- Price
- OHLCV
- Open Interest
- Aggregated Open Interest
- Funding
- CVD
- Liquidations

No interpretation.

Only objective evidence.

---

## 2. Structure Engine

Status: implemented MVP.

The current Structure Engine MVP builds objective `StructuralEvidence` from
normalized OHLCV candles.

It is not part of the current Runtime Orchestrator flow.

Preferred public API:

- `build_structural_evidence()`

Compatibility exports remain:

- `add_structural_evidence()`
- `refine_structural_evidence()`

Implemented structural facts:

- EMA7, EMA14, and EMA21 with full-period warmup;
- latest price position relative to available EMAs;
- deterministic 2-left / 2-right swing pivots;
- Higher High / Higher Low and Lower High / Lower Low facts;
- latest valid impulse;
- Fibonacci levels only when the latest impulse is valid.

Legacy `trend_structure` close-sequence labels remain as compatibility
fallback:

- `rising_close_sequence`
- `falling_close_sequence`
- `flat_close_sequence`

The engine produces structural evidence.

It does not make trading decisions.

It also does not create hypotheses, calculate confidence, interpret OI,
Funding, CVD, or volume, add a Validation Layer, perform Quality Translation,
or change Runtime orchestration.

---

## 3. Market Efficiency Engine

Status: implemented MVP.

The current Market Efficiency Engine MVP builds objective
`MarketEfficiencyEvidence` from `ObservationPackage`.

The Runtime loop currently imports and calls the Market Efficiency Engine.

The engine itself does not orchestrate Runtime behavior; it only produces
evidence.

Preferred public API:

- `build_market_efficiency_evidence()`

Compatibility exports remain:

- `add_market_efficiency_evidence()`
- `refine_market_efficiency_evidence()`
- `MarketEfficiencyError`

Current MVP metric handling records availability and raw context values for:

- Volume
- Open Interest
- Funding Rate
- CVD
- Liquidations

These values are not interpreted and are not assigned trading meaning.

`EvidenceStrength` and `UncertaintyLevel` describe evidence coverage and data
availability only. They are not market confidence or trade confidence.

Perception may still create `MarketEfficiencyEvidence` for compatibility, but
the cleaner current path is:

`ObservationPackage` -> `build_market_efficiency_evidence()` ->
`MarketEfficiencyEvidence`

Advanced market efficiency reasoning remains planned.

Future examples:

- Participation
- OI Growth
- Funding
- Absorption evidence
- Price efficiency evidence
- Volume efficiency evidence

The engine produces participation and efficiency evidence.

It does not make trading decisions.

It also does not interpret market state, generate hypotheses, assign trading
confidence, produce trading signals, or orchestrate Runtime behavior.

---

## 4. Hypothesis Engine

Status: implemented MVP.

Creates current-market explanations from prepared Runtime context.

The current implementation has two paths:

- clean `HypothesisPackage` path;
- legacy / Runtime scanner `MarketHypothesis` path.

### Clean HypothesisPackage Path

The clean package path consumes:

- `StructuralEvidence`
- `MarketEfficiencyEvidence`

It produces `HypothesisPackage`.

Preferred package APIs:

- `build_hypothesis_package()`
- `add_hypothesis_package()`

This path combines upstream objective evidence into a current-condition
explanation. It does not mutate upstream evidence.

### Legacy / Runtime Scanner MarketHypothesis Path

The legacy Runtime scanner path remains for compatibility.

API:

- `build_hypothesis()`

This path consumes arbitrary Runtime scanner data and may call:

- `detect_market_state()`
- `calculate_confidence()`
- `collect_evidence()`

It produces `MarketHypothesis` and supports the main Runtime scanner-style flow.

### Public Hypothesis Exports

Core exports:

- `HypothesisError`
- `MarketHypothesis`
- `build_hypothesis()`
- `build_hypothesis_package()`
- `add_hypothesis_package()`

Snapshot, history, and evaluator exports:

- `HypothesisSnapshot`
- `HypothesisSnapshotBuilder`
- `HypothesisHistory`
- `HistoryTrendAnalyzer`
- `HistoryTrendSummary`
- `build_hypothesis_snapshot()`
- `HypothesisEvaluator`
- `HypothesisEvaluation`
- trend constants
- evaluation constants

### Lifecycle And Confidence

Hypothesis lifecycle statuses are:

- `CREATED`
- `UPDATED`
- `WEAKENED`
- `REPLACED`

The legacy `MarketHypothesis` path may contain a numeric `confidence_score`.

The clean package path emits `current_hypothesis_confidence_context`.

`current_hypothesis_confidence_context` is not final market confidence and not
trade confidence. Final confidence remains the responsibility of the Confidence
Engine if or when that layer is used.

### Diagnostic Context

The MVP also includes diagnostic support objects:

- `HypothesisSnapshot`
- `HypothesisHistory`
- `HistoryTrendAnalyzer`
- `HypothesisEvaluator`

These are deterministic diagnostic helpers. They do not modify Runtime behavior,
confidence, hypotheses, alerts, probabilities, or trading decisions.

### Boundaries

The Hypothesis Engine may form, update, weaken, or replace hypotheses.

It does not fetch market data directly, mutate upstream evidence, produce final
trading execution commands, own Telegram alerts, own Runtime orchestration, own
future scenario probabilities, or own final trade confidence.

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

Scenario Probability v0.1 translates official `AgentState.current_state` into
deterministic MVP next-scenario weights. These weights are not calibrated
predictions and are not final confidence.

Current state-aware policy:

- `UNKNOWN`:
  - `continue_observation`: 0.40
  - `insufficient_evidence_persists`: 0.35
  - `state_clarifies_after_more_data`: 0.25
  - uncertainty: `HIGH`
- `CONTINUATION_ALIVE`:
  - `continuation_persists`: 0.55
  - `continuation_degrades_to_saturation`: 0.30
  - `first_failure_candidate_emerges`: 0.15
  - uncertainty: `MEDIUM`
- `CONTINUATION_SATURATION`:
  - `saturation_resolves_to_continuation`: 0.25
  - `saturation_persists`: 0.45
  - `first_failure_risk_increases`: 0.30
  - uncertainty: `MEDIUM`
- `FIRST_FAILURE_CANDIDATE`:
  - `failure_candidate_invalidated`: 0.20
  - `failure_candidate_persists`: 0.45
  - `first_failure_confirms`: 0.35
  - uncertainty: `MEDIUM`

The engine must not inspect raw market data, reinterpret Structure or Market
Efficiency evidence, decide final confidence, generate alerts, or make trading
decisions.

---

## 6. Confidence Engine

Evaluates the final reliability of the current hypothesis, Agent State, and
scenario probabilities.

Confidence can:

- increase;
- decrease;
- stay unchanged.

The agent never falls in love with its own prediction.

### Confidence Engine (MVP)

Runtime Confidence v0.1 assesses reliability of the current Runtime reasoning
chain:

`HypothesisPackage + AgentState + ScenarioProbability + data quality context`
-> `ConfidenceAssessment`

It does not predict the market, choose actions, generate alerts, inspect raw
market metrics, or reuse legacy scanner numeric confidence as final
`RuntimeEvent` confidence.

`HIGH` confidence is not allowed in this MVP. Final Runtime confidence is capped
at `MEDIUM` until reliability can be calibrated or historically validated.

Current policy:

- `UNKNOWN` Agent State -> `LOW`;
- missing Scenario Probability -> `LOW`;
- Scenario Probability uncertainty `HIGH` or `UNKNOWN` -> `LOW`;
- missing or generic-only hypothesis context -> `LOW`;
- hypothesis or scenario contradictions -> `LOW`;
- incomplete, missing, delayed, or corrupted data quality -> `LOW`;
- non-`UNKNOWN` Agent State plus available Scenario Probability with
  non-high uncertainty, supporting hypothesis evidence, no contradictions, and
  acceptable data quality -> `MEDIUM`.

Confidence drivers include known Agent State, Scenario Probability availability,
non-high scenario uncertainty, valid scenario weight sum, supporting hypothesis
evidence, and acceptable data quality.

Confidence reducers include unknown Agent State, missing Scenario Probability,
high or unknown scenario uncertainty, missing or generic hypothesis context,
contradictions, incomplete or poor data quality, and the MVP cap that prevents
`HIGH` confidence.

The legacy `calculate_confidence()` helper remains a separate scanner
compatibility heuristic. It is not the final `RuntimeEvent.confidence_assessment`
numeric score.

---

## 7. Evidence Engine

Explains which observed market metrics supported or weakened the current scan
result.

Evidence can:

- mark price as increasing or not increasing;
- mark volume as above average or not above average;
- mark open interest as increasing or not increasing.

The Evidence Engine is an explanation layer only. It does not classify market
state, calculate confidence, create hypotheses, or make decisions.

In the MVP, Evidence supports scanner output and is not yet a dedicated
`RuntimeEvent` section.

---

## 8. Decision / Alert

Produces non-execution operational outputs.

Decision / Alert v0.1 translates official Runtime reasoning into conservative
human attention guidance:

`AgentState + ScenarioProbability + ConfidenceAssessment` -> `DecisionAlert`

It does not reinterpret market data, inspect raw metrics, infer trades, decide
execution, call Telegram, persist state, or access Learning Memory.

Current MVP policy:

- missing `ConfidenceAssessment` blocks with the existing contract error;
- missing `ScenarioProbability` -> `REVIEW_REQUIRED`, `INFO`, `WATCH`;
- `UNKNOWN` Agent State -> `REVIEW_REQUIRED`, `INFO`, `WATCH`;
- `LOW` Confidence -> `REVIEW_REQUIRED`, `INFO`, `WATCH`;
- `MEDIUM` Confidence + `CONTINUATION_ALIVE` -> `OBSERVE`, `NONE`,
  `NO_ACTION`;
- `MEDIUM` Confidence + `CONTINUATION_SATURATION` -> `WARNING`, `WARNING`,
  `WARNING`;
- `MEDIUM` Confidence + `FIRST_FAILURE_CANDIDATE` -> `WARNING`, `WARNING`,
  `HIGH_ATTENTION`;
- unsupported future states -> `REVIEW_REQUIRED`, `INFO`, `WATCH`.

`HUMAN_DECISION_REQUIRED` and `CRITICAL` are reserved for future approved
escalation rules and are not used by default in this MVP.

Decision / Alert messages may ask the human to continue observation, review the
reasoning chain, monitor the primary scenario, or increase attention. They must
not include entries, long/short commands, execution advice, or autonomous
trading instructions.

Decision / Alert does not execute trades.

The human always has the final decision.

---

## 9. Learning Memory

Learning Memory is not part of the current Runtime Orchestrator path.

It remains a separate boundary for future storage and Research Plane workflows.

When explicitly invoked, it classifies a completed `RuntimeEvent` as
`CASE_READY` or `REVIEW_ONLY`; invalid or inconsistent events are rejected.
`ObservationPackage` is optional because the RuntimeEvent contract path already
contains `MarketSnapshot`, `StructuralEvidence`, and
`MarketEfficiencyEvidence`. A missing Scenario Probability produces a
`REVIEW_ONLY` case rather than rejection when Confidence and Decision / Alert
are present.

`LearningMetadata.should_store` means only that a complete case is eligible for
future storage after human review. It does not persist the case. Review-only
events set `should_store=False`.

Learning Memory must not change Runtime behavior automatically.

It does not perform automatic learning, trigger Research Agent, call Telegram,
or modify Runtime behavior. Research Plane work starts only from reviewed or
stored cases and remains separate from live Runtime decisions.

---

# Research Plane

Research Plane is separate from Runtime Plane.

It starts from Learning Memory and is responsible for historical analysis,
findings, and improvement proposals.

Research Plane cannot automatically modify Runtime behavior.

Every change to Runtime behavior must pass Human Review before implementation.

See [Research Architecture](research/README.md).
