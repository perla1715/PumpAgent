# PumpAgent Observation Lifecycle Contract v1

## Purpose

This document defines the canonical lifecycle of a market observation inside
PumpAgent MVP.

Its purpose is to specify:

- who starts an observation;
- who owns it;
- how repeated evaluations are associated with it;
- when it ends;
- what belongs to it;
- what must never cross Observation Episode boundaries.

This document defines architectural behaviour only.

It does **not** define trading rules, market classification logic, or
implementation details.

---

## 1. Observation Episode

An Observation Episode is the canonical temporal context for one continuous
market process.

It represents one market episode that begins after a Scanner attention request
and continues until Observation Policy explicitly closes it.

Every analytical result belongs to exactly one Observation Episode.

## 2. Scanner responsibility

Scanner is responsible only for detecting early market attention.

Scanner may detect:

- abnormal Volume participation;
- beginning Open Interest growth;
- meaningful initial price activity.

Scanner never:

- opens an Observation Episode;
- closes an Observation Episode;
- classifies the market;
- produces trading recommendations.

Scanner only submits an Observation Request.

## 3. Observation Policy responsibility

Observation Policy owns the Observation Lifecycle.

Only Observation Policy may:

- accept or reject a Scanner request;
- open a new Observation Episode;
- continue an existing Observation Episode;
- associate a new Observation Cycle with an active Episode;
- close an Observation Episode;
- replace one Episode with another.

Observation Policy never determines:

- market direction;
- continuation quality;
- weakening;
- confidence;
- trading recommendations.

## 4. Episode identity

Each Observation Episode receives a unique Episode ID.

Every Runtime evaluation generated during the Episode references that Episode
ID.

At minimum the Episode contains:

- Episode ID;
- Exchange;
- Symbol;
- Timeframe (5m);
- Scanner trigger information;
- Opening timestamp;
- Closing timestamp (when completed).

Episode ID is the canonical identity used by all downstream modules.

## 5. Observation opening

Scanner submits an Observation Request.

Observation Policy evaluates whether:

- there is no active Episode;
- an existing Episode should continue;
- the active Episode should be replaced.

Only Observation Policy may create a new Episode.

Creating a new Episode never implies a trading signal.

## 6. Observation Cycle

Each Observation Cycle corresponds to one newly closed and valid 5-minute
candle.

Observation Cycles are initiated by the Runtime when a new eligible closed
candle becomes available.

Scanner is **not** responsible for triggering repeated Observation Cycles.

Each Observation Cycle is processed exactly once.

Duplicate or invalid cycles are rejected before entering the analytical
pipeline.

## 7. Episode continuation

If an Episode remains active, every eligible Observation Cycle is associated
with that Episode.

Each new cycle provides additional evidence.

Observation continues while the original market context remains valid.

`UNKNOWN` does not interrupt Episode continuity.

## 8. Episode replacement

Only Observation Policy may determine that the current market activity no
longer belongs to the active Episode.

When replacement occurs:

1. the current Episode is closed;
2. its final lifecycle metadata is recorded;
3. a new Episode is opened;
4. the triggering Observation Cycle belongs only to the new Episode.

There must never be two simultaneously active Episodes for the same:

- Exchange;
- Symbol;
- 5m timeframe.

## 9. Episode closure

Observation Policy closes an Episode only when:

- the original market context is no longer relevant;
- a replacement Episode is required;
- observation is no longer meaningful according to Observation Policy.

`UNKNOWN` alone never closes an Episode.

Elapsed time alone never closes an Episode.

Fixed candle counts never close an Episode.

## 10. Analytical ownership

Within an active Episode:

Process Engine owns:

- market process interpretation.

Hypothesis Engine owns:

- analytical hypotheses.

Agent State owns:

- official process state.

Confidence owns:

- confidence estimation.

Trading Recommendation owns:

- trader-facing recommendations.

Observation Policy owns only lifecycle.

## 11. Episode isolation

Every analytical artifact belongs exclusively to one Observation Episode.

This includes:

- Runtime evaluations;
- Process results;
- Hypotheses;
- Agent States;
- Confidence history;
- Trading Recommendations;
- Final Outcome.

No analytical history is automatically inherited by a later Observation
Episode.

### Episode-scoped analytical continuity

The controlled Observation Runtime flow stores one immutable, bounded
`EpisodeAnalyticalContext` on the active Watchlist entry. It contains only the
latest completed cycle's Runtime event, candle timestamp, Process evidence and
state, hypothesis, official Agent State, canonical Scenario Probability,
canonical ConfidenceAssessment, temporary numeric explanation-confidence
compatibility projection, lightweight evidence summary, and cycle count.

Scenario Probability is rebuilt after Agent State on every valid analytical
cycle and is committed only through successful cycle completion. Ineligible,
failed, identity-invalid, and completion-rejected cycles retain the previously
committed scenario output unchanged. Its weights remain deterministic policy
weights rather than calibrated forecasts; structured confirmation and
invalidation rules are outside the MVP.

ConfidenceAssessment is produced after Scenario Probability and committed by
the same atomic completion boundary. Failures before or after Confidence
production, identity failures, ineligible cycles, and completion rejection do
not replace the previously committed assessment. The temporary numeric
`latest_confidence` remains an explanation-confidence compatibility projection;
it is not canonical final reliability.

The exact Episode ID and market identity are required before its Process
evidence, hypothesis, or state can be supplied to Runtime. `OPEN` and `REPLACE` start without a context;
`CLOSE` removes it from active use; Scanner `CONTINUE` and `NO_ACTION` preserve
it unchanged. Runtime's legacy mutable Watchlist, Temporal Confidence, and
Hypothesis History helpers remain for compatibility, but they are rebound at
Episode boundaries and are not the canonical previous hypothesis/state source
for this controlled flow.

## 12. Lifecycle invariant

For every Observation Episode the following invariant must always hold:

```text
Scanner Request
    ↓
Observation Policy
    ↓
Observation Episode
    ↓
Observation Cycles
    ↓
Process Reasoning
    ↓
Hypothesis
    ↓
Agent State
    ↓
Confidence
    ↓
Trading Recommendation
    ↓
Episode Closure
    ↓
Completed Observation Episode
```

No component may bypass this lifecycle.

---

## MVP Architectural Invariants

The following statements must always remain true:

- Scanner requests attention but never controls lifecycle.
- Observation Policy owns lifecycle but never interprets the market.
- Process Engine interprets evidence but never owns lifecycle.
- `UNKNOWN` is an analytical state only.
- Classification depends on evidence quality, never on elapsed time.
- Only one active Observation Episode exists for one (Exchange, Symbol, 5m).
- Every Observation Cycle belongs to exactly one Observation Episode.
- Every analytical result belongs to exactly one Observation Episode.
- Observation history never leaks into a different Observation Episode.
- Trading Recommendation is always the final analytical output.

## Architecture Status

Observation Lifecycle Contract v1 defines the canonical temporal architecture
for PumpAgent MVP.

It intentionally excludes:

- persistence;
- distributed execution;
- databases;
- concurrency;
- versioning;
- replay;
- post-MVP research modules.

Those concerns may be addressed after MVP without changing the conceptual
Observation Lifecycle defined here.
