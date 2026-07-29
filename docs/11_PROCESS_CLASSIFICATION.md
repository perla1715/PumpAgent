# Process Classification v1

Process Classification is a pure analytical boundary between objective evidence
production and downstream reasoning. Scanner attention is not a Process state.

The immutable `ProcessClassificationInput` carries exact Episode, Runtime event,
market identity, current ObservationPackage, StructuralEvidence,
MarketEfficiencyEvidence, optional prior ProcessEvidence from the same Episode,
and an explicit timezone-aware classification timestamp. The classifier uses no
clock, history manager, random value, or downstream analytical state.

The only states are `UNKNOWN`, `CONTINUATION_ALIVE`, and `WEAKENING`. An initial
classification is always `UNKNOWN`. Alive requires comparable positive Price,
sustained/expanding Volume, and independent rising OI or constructive Structure,
without a blocking mandatory-family contradiction. Weakening requires prior
alive, retained/stalled/deteriorating Price, contracting Volume, and declining or
stagnant OI or deteriorating Structure. Missing optional evidence is neutral.

The result also carries the independent typed `process_direction` dimension.
For valid Price evidence, the final structured close above, below, or equal to
the first close produces `UP`, `DOWN`, or `NEUTRAL`. Unavailable Price evidence
or invalid Process data quality produces `UNKNOWN`. This comparison adds no
trading threshold and does not depend on `ProcessState`.

Process direction is observational orientation only. `UP` is not
`LOOK_FOR_LONG`, `DOWN` is not `LOOK_FOR_SHORT`, and no Directional Decision is
produced by Process Classification.

Evidence is grouped into Price, Volume, Open Interest, Structure, CVD, Funding,
Liquidations, and Data Quality. Each family contributes at most one directional
classification fact, preventing correlated transformations from being counted
as independent confirmation. Funding and liquidations remain context only; CVD
is optional context or contradiction and cannot classify by itself.

Invalid quality, event/Episode mismatch, unusable OHLCV comparisons, missing
mandatory Price or Volume, unsupported Structure inference, or unresolved
conflict produces `UNKNOWN`. The module does not invoke Runtime and does not own
lifecycle, Hypothesis, Agent State, Confidence, recommendations, alerts, or
trading decisions.

In the controlled Episode Runtime, this classifier runs once after Structure
and Market Efficiency and before Hypothesis and Agent State. Hypothesis receives
the resulting `ProcessEvidence` as its canonical state. The legacy
`detect_market_state()` path remains only for standalone compatibility calls
that do not provide Episode Process evidence.
