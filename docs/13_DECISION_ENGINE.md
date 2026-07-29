# Decision Engine MVP v1

Decision is a pure, deterministic boundary after Confidence. It consumes only
completed analytical outputs and produces one non-executing directional
preparation decision. This module is not integrated into Runtime by this
checkpoint.

## Inputs and validation

Required inputs are `ProcessQualityAssessment`, `ProcessEvidence`,
`HypothesisPackage`, `ScenarioProbability`, and `ConfidenceAssessment`.
`HealthyBaselineReference` and a prior `DecisionReference` are optional.

All current inputs must share one Episode and Runtime event. Scenario
Probability and Confidence must reference the active Hypothesis. Process
Quality and Process Evidence must share an observation timestamp. Scenario
weights must be finite values in `[0, 1]`, cover the complete scenario set, and
sum to `1.0` within absolute tolerance `1e-9`. A primary scenario must exist.
Process, Hypothesis, and Scenario provenance must be present. An `UNKNOWN`
Process state is accepted only for the canonical initial lifecycle, where
`previous_process_state` is `None` and `detected_transition` is `INITIAL`.
Decision then executes through the unchanged deterministic rule ordering. Every
other `UNKNOWN` Process state is incomplete and is rejected. A non-`UNKNOWN`
state that claims the initial lifecycle is also rejected.

The optional baseline must match the reference already authenticated by Process
Quality. A prior Decision must be earlier and from the same Episode. It is
stored as transition provenance only and never participates in analytical
selection.

Invalid inputs raise `DecisionValidationError`; they are never converted into
`STAY_OUT`.

For the canonical initial lifecycle, the current upstream outputs naturally
produce `STAY_OUT` with `UPSTREAM_INHIBITION`. This is a valid completed
Decision, not conversion of invalid input and not a special-case selection
rule.

## Outputs

The only MVP decisions are:

- `LOOK_FOR_LONG`
- `LOOK_FOR_SHORT`
- `STAY_OUT`

The immutable `DecisionAssessment` carries canonical source references, ordered
reason codes, provenance, a timezone-aware creation timestamp, optional prior
Decision and Healthy Baseline references, and an invariant non-execution
confirmation.

Decision identity is:

`decision:{episode_id}:{runtime_event_id}`

## Deterministic rules

The existing MVP Confidence threshold is `MEDIUM`. `MEDIUM`, `HIGH`, and
`VERY_HIGH` satisfy the Decision threshold. `HIGH` and `VERY_HIGH` remain valid
contract values even though the current Confidence Engine intentionally caps
operational output at `MEDIUM`.

`HIGH` or `UNKNOWN` uncertainty from Process Quality, Process Evidence,
Hypothesis, Scenario Probability, or Confidence blocks directional preparation.
An inhibited Process Quality outcome, missing required analytical evidence,
upstream contradictions, tied scenario dominance, low Confidence, or
directional disagreement produces `STAY_OUT`.

`LOOK_FOR_LONG` requires all of:

- uniquely dominant bullish or upward-continuation primary scenario;
- Process direction `UP`;
- Process state `CONTINUATION_ALIVE`;
- Healthy Active Process `SUPPORTED`;
- Loss of Efficiency `NOT_ESTABLISHED`;
- existing Hypothesis label `Continuation remains active`, `Bullish
  continuation`, or `Recovery`;
- Confidence at or above `MEDIUM`;
- no blocking uncertainty, inhibition, missing required evidence, or
  contradiction.

`LOOK_FOR_SHORT` requires all of:

- uniquely dominant bearish, breakdown, dump, or failure primary scenario;
- Process direction `DOWN`;
- Process state `WEAKENING`;
- Loss of Efficiency `SUPPORTED`;
- Healthy Active Process `NOT_ESTABLISHED`;
- existing Hypothesis label `Move is weakening`, `Bearish continuation`, or
  `Transition toward dump`;
- Confidence at or above `MEDIUM`;
- no blocking uncertainty, inhibition, missing required evidence, or
  contradiction.

Scenario labels are matched only against the explicit finite sets in the
Decision Engine. No raw metric, free-form summary, or monitoring instruction is
parsed.

## Boundary

Decision does not classify Process, recalculate Process Quality, modify a
Healthy Baseline, create or change a Hypothesis, recalculate probability or
Confidence, invent evidence, generate an entry signal, execute a trade, manage
positions, log, notify, or control Scanner or Observation lifecycle.

The older `DecisionAlert` path is a separate legacy contract. Its states and
alert behavior are not used by this Decision Engine.
