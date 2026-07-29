# Hypothesis Engine

## Status

Implemented MVP. `HypothesisPackage` is the single canonical hypothesis domain
contract and the sole hypothesis object produced by the controlled Runtime.

The engine explains the current market condition from prepared evidence. It
does not fetch market data, mutate upstream evidence, assign final Runtime
confidence, calculate Scenario Probability, produce alerts, or execute trades.

## Inputs And Output

The canonical producer consumes:

- the Observation Lifecycle-owned `episode_id`;
- the current Runtime event identity;
- `StructuralEvidence`;
- `MarketEfficiencyEvidence`;
- current `ProcessEvidence` for the operational interpretation;
- the previous committed `HypothesisPackage`, when one exists in the same
  Observation Episode;
- an injected zero-argument hypothesis ID generator.

It produces exactly one immutable `HypothesisPackage`.

Primary APIs:

- `build_hypothesis_package()`;
- `add_hypothesis_package()`;
- `build_operational_hypothesis_package()`;
- `generate_hypothesis_id()`.

## Canonical Contract

The package owns:

- Runtime and Observation Episode identity;
- opaque hypothesis identity;
- label and summary;
- typed supporting and contradicting evidence references;
- exact `explanation_confidence_score` and its categorical context;
- uncertainty and reasoning notes;
- lifecycle status, predecessor references, and change reason;
- schema version.

`explanation_confidence_score` is the numeric strength of the explanation. It
uses the existing integer range from 0 through 100 and is distinct from final
Runtime `ConfidenceAssessment`.

## Identity Ownership

Observation Lifecycle exclusively creates and owns `episode_id`. Runtime passes
that value unchanged to the Hypothesis Engine.

The Hypothesis Engine decides whether identity is retained or replaced. A
minimal injected generator performs only mechanical creation of a new opaque
ID. The production generator returns a UUIDv4 string and receives no semantic
inputs.

Identity behavior:

- `CREATED` requests a new ID;
- `REPLACED` requests a new ID;
- `UPDATED` keeps the existing ID;
- `WEAKENED` keeps the existing ID.

Hypothesis identity never derives from event ID, episode ID, market identity,
label, confidence, or timestamp. Continuity cannot cross Observation Episode
boundaries.

## Lifecycle

Lifecycle comparison is deterministic:

- no previous package in the active episode: `CREATED`;
- changed hypothesis label: `REPLACED`;
- unchanged label with a lower exact explanation score: `WEAKENED`;
- unchanged label with an equal or higher exact explanation score: `UPDATED`.

Every non-created package records `previous_hypothesis_id` and
`previous_runtime_event_id`. The previous Runtime event must differ from the
current event.

Only successful Observation Cycle completion commits the package as
`EpisodeAnalyticalContext.latest_hypothesis` and projects its ID into the
Watchlist. Ineligible or failed cycles do not advance hypothesis continuity.

## Agent State Boundary

The controlled Agent State bridge consumes the canonical package together with
the already-produced Process State and operational evidence values. It preserves
the existing conservative state mapping and does not add hypothesis, state, or
trading rules.

## Diagnostics

`HypothesisSnapshot`, `HypothesisHistory`, `HistoryTrendAnalyzer`, and
`HypothesisEvaluator` remain deterministic diagnostic helpers. They do not
modify the canonical package, Runtime state, confidence, probabilities, alerts,
or trading decisions.

## Boundaries

The Hypothesis Engine must not:

- own Observation Episode identity;
- infer continuity across episodes;
- create IDs from semantic inputs;
- calculate final confidence;
- calculate Scenario Probability;
- decide alerts or execution;
- persist or export completed events.
