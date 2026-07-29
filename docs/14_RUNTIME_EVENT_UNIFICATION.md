# Canonical RuntimeEvent Architecture

## Status

Frozen by F-02.

## Authority

`RuntimeEvent` is the only authoritative aggregate for one Runtime cycle.
`RuntimeOrchestrator.process_market_update` is the only production analytical
orchestrator. A successful event owns the snapshot, observation, specialized
evidence, Process Evidence, Process Quality and baseline continuity,
Hypothesis, Agent State, Scenario Probability, ConfidenceAssessment, and
DecisionAssessment.

Terminal lifecycle states are explicit:

- `COMPLETED` contains every mandatory canonical section.
- `REJECTED` records an intentional pre-analysis admission stop and contains
  no downstream decision.
- `FAILED` records a technical or contract failure and contains no downstream
  decision.

`COMPLETED` is the only terminal-success status. The legacy `FINALIZED` value
is retired rather than retained as a second success meaning.

Only a completed event may advance Episode analytical continuity.

The admitted `MarketSnapshot.timestamp` is the canonical timestamp authority
for the current analytical cycle. `RuntimeEvent.cycle_timestamp`,
`ObservationPackage.observation_timestamp`, current Process Evidence, current
Process Quality, Scenario Probability observation/creation timestamps, and
DecisionAssessment creation time must equal it. Historical Process Quality,
Scenario, Decision, and Healthy Baseline provenance must precede the current
cycle where their domain contracts require history. The public compatibility
parameter `classification_timestamp` is accepted only when it equals the
admitted snapshot timestamp; omission derives it from the snapshot.

## Compatibility projection

`AgentCycleResult` is retained only as a diagnostic compatibility projection.
`project_agent_cycle_result(runtime_event)` accepts a completed RuntimeEvent
and performs a pure, deterministic, one-way field projection. The projection
does not call engines, mutate the event, own persistence, or provide any
reverse conversion.

Canonical persistence and logging serialize `RuntimeEvent` through
`serialize_runtime_event`. The legacy AgentCycleResult serializer remains a
compatibility schema only.

## Fixture runtime

The fixture entry point owns fixture loading, not analytical orchestration.
Market-data-only use returns the created input RuntimeEvent. Any deprecated
analytical stage request delegates the complete cycle to
`RuntimeOrchestrator`. Legacy partial fixture results are compatibility
projections of that one delegated execution; partial parallel analytical
execution is not supported.

Compatibility projections preserve fixture Runtime identity, schema version,
cycle timestamp, and partial section shape. Their analytical section values
come from the production engines. The retired fixture-only synthetic
Hypothesis label is not reproduced because doing so would fabricate or
reinterpret a canonical analytical output.

## Downstream ownership

Observation Lifecycle commits completed RuntimeEvents and preserves prior
context for rejected or failed events. Learning Memory consumes completed
RuntimeEvents and uses DecisionAssessment as the terminal decision authority.
DecisionAlert is not the canonical Runtime result.

## Invariants

1. There is one production orchestrator and one canonical aggregate.
2. Each canonical analytical stage executes at most once per admitted cycle.
3. RuntimeEvent identity and Episode provenance flow unchanged between stages.
4. AgentCycleResult is derived only from RuntimeEvent.
5. Fixture execution cannot form a second stage graph.
6. Rejected and failed cycles cannot mutate Episode continuity.
7. No F-01 evidence ownership boundary or analytical methodology is changed.
