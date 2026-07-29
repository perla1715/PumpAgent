# Agent States

## Independent State Dimensions

Canonical `AgentState` carries two independent current-process conclusions:

1. `current_state` describes process health or stage.
2. `process_direction` describes observed process orientation.

`ProcessDirection` has exactly four values:

- `UP` — valid structured Price comparison ends above its starting close;
- `DOWN` — valid structured Price comparison ends below its starting close;
- `NEUTRAL` — valid structured Price comparison is flat over the comparison;
- `UNKNOWN` — orientation cannot be established from valid available evidence.

Process Classification is the single mechanical source of orientation. Agent
State transports that conclusion without recalculating candles or parsing
Hypothesis text. Process health and direction remain orthogonal: a state name
does not imply a direction.

`ProcessDirection` is not a trading Decision. `UP` does not mean
`LOOK_FOR_LONG`, and `DOWN` does not mean `LOOK_FOR_SHORT`. Directional Decision
policy remains a later bounded slice after trader-approved rules exist.

## Core Principle

PumpAgent never predicts with certainty.

The agent always maintains a current hypothesis about the market.

Each incoming update may:

- increase confidence;
- decrease confidence;
- invalidate the hypothesis;
- create a new hypothesis.

The agent is allowed to change its mind.

---

# State 0 — UNKNOWN

The market cannot yet be classified.

Confidence: very low.

Action:
Observe.

---

# State 1 — IGNITION

Participation begins to expand.

Possible signals:

- volume expansion;
- OI growth;
- first breakout;
- liquidation spike.

Goal:

Detect that something unusual has started.

---

# State 2 — CONTINUATION ALIVE

The trend is healthy.

Typical characteristics:

- higher highs;
- healthy pullbacks;
- strong participation;
- expanding volatility.

Confidence increases.

---

# State 3 — CONTINUATION SATURATION

The move is still alive but begins losing quality.

Typical characteristics:

- slower expansion;
- weaker participation;
- momentum reduction;
- less efficient continuation.

Agent enters Warning Mode.

---

# State 4 — FIRST FAILURE CANDIDATE

The first important warning appears.

Evidence starts contradicting continuation.

Confidence decreases.

The agent does NOT predict reversal.

The agent prepares for possible failure.

---

# State 5 — FIRST FAILURE

Continuation quality is broken.

Typical characteristics:

- failed reclaim;
- structural weakness;
- participation deterioration.

The previous hypothesis becomes invalid.

---

# State 6 — CONTINUATION DEATH

The previous trend is no longer active.

The agent searches for a completely new market hypothesis.

---

# State Transition Principle

States are never changed because of a single indicator.

Transitions are decided by:

- Evidence;
- Confidence;
- Current Hypothesis.

This makes PumpAgent adaptive instead of rule-based.
