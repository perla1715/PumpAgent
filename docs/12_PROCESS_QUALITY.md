# Process Quality and Healthy Baseline v1

Process Quality runs after Process Classification and before Hypothesis. It
interprets the current canonical `ProcessEvidence`; it does not classify the
market again.

## Healthy Active Process semantics

`CONTINUATION_ALIVE` and Healthy Active Process are related but are not the same
domain concept. `CONTINUATION_ALIVE` is the Process Classification conclusion.
Healthy Active Process is the Process Quality conclusion drawn from that
classification together with Structure, Market Efficiency, and data-quality
provenance.

For MVP, Healthy Active Process is `SUPPORTED` exactly when valid current data
has produced `CONTINUATION_ALIVE`. `UNKNOWN` inhibits the assessment and
`WEAKENING` produces `NOT_ESTABLISHED`. Process Quality must not independently
reclassify these states.

## Canonical MVP baseline eligibility

The first assessment in an Observation Episode becomes eligible to be the
Healthy Baseline candidate only when all of the following hold:

1. data quality is `VALID`;
2. canonical Process Classification is `CONTINUATION_ALIVE`;
3. Process evidence strength is `MODERATE` or `STRONG`;
4. Healthy Active Process is `SUPPORTED`;
5. the Healthy assessment has no inhibiting evidence;
6. it has supporting evidence;
7. its supporting provenance contains Process, Structure, and Market
   Efficiency evidence.

These are canonical MVP rules. `UNKNOWN` and `WEAK` evidence strength are
insufficient. The three provenance families are mandatory because Process
Quality is required to consume Classification and the referenced Structure and
Market Efficiency evidence rather than treat classification alone as a
baseline.

Assessment uncertainty is preserved but is not a separate eligibility veto in
MVP. It is derived from the same Process, Structure, Market Efficiency, and
data-quality inputs already gated above. A separate uncertainty threshold would
be a future policy rule and is deferred; it must not be inferred in MVP.

No alternative evidence thresholds, provenance families, or replacement
heuristics are active or implicitly defaulted.

## Identity, activation, and persistence

The canonical baseline identity formula is:

`healthy-baseline:{episode_id}:{assessment_id}`

Generation, `HealthyBaselineDesignation`, and `HealthyBaselineReference`
validation enforce this exact identity. Process Quality input validates it
again before assessment execution. A designation or reference with an
externally supplied inconsistent identity is invalid.

A qualifying assessment produces a candidate after Process Quality completes.
The candidate becomes the active Healthy Baseline only when the completed
analytical context is committed atomically to the active Observation Episode.
The creating assessment therefore cannot consume its own candidate. Only later
successfully admitted and committed cycles can consume it.

The first authenticated designation is permanent for the lifetime of its
Observation Episode. Replacement and successor baselines are forbidden in MVP.
The designation survives later Process Quality assessments and downstream
analytical changes, and it is discarded when continuity moves to another
Observation Episode.

## Deferred and removed rules

- A separate uncertainty cutoff is deferred.
- Baseline replacement, succession, and replacement heuristics are removed from
  the MVP contract.
- No Decision, Early Watch, Stay Out, alert, or trading semantics belong to this
  stage.
