# Learning Case Pipeline v1

## Boundary

F-03 is an offline data-collection and attribution path:

```text
historical MarketSnapshot
→ RuntimeOrchestrator
→ completed canonical RuntimeEvent
→ LearningCase
→ later observations
→ OutcomeRecord
→ objective research label
→ deterministic JSONL dataset
```

`RuntimeEvent` remains the sole analytical authority. Learning persistence is
explicitly invoked after Runtime completion; it is not inserted into
`RuntimeOrchestrator`. Outcomes, reviews, labels, and export metadata are
separate versioned records. They never rewrite the stored canonical event,
execute trades, emit live signals, or update Runtime configuration.

## LearningCase lifecycle

The versioned `learning_case_v1` contract preserves Runtime identity, schema,
immutable serialized payload, market and Episode identity, UTC timestamps,
LearningMetadata, provenance, eligibility, and exclusions.

```text
PENDING_OUTCOME
→ OUTCOME_PARTIAL
→ OUTCOME_COMPLETE
→ REVIEWED
```

Excluded cases use `EXCLUDED`. This lifecycle is independent of RuntimeStatus.

## SQLite storage

`SQLiteLearningCaseRepository` creates a local transactional database with:

- `learning_cases`;
- `outcome_records`;
- `review_records`;
- `schema_metadata`.

Case and Runtime event IDs are unique. One OutcomeRecord is allowed per case
and horizon. Identical retries are accepted; conflicting content under an
existing identity raises an explicit conflict. Canonical Runtime payloads are
never silently overwritten. SHA-256 payload digests support integrity checks.

## Outcome definitions

`outcome_metrics_v1` supports 5, 15, 30, and 60 minute horizons. It calculates:

- close-to-close return;
- maximum favorable and adverse close excursion;
- maximum high and minimum low return;
- time to favorable and adverse excursion;
- realized volatility;
- volume change when source and outcome volume are available.

Windows contain only observations strictly after the Runtime cycle and no
later than the selected horizon. Identity mismatches, duplicate timestamps,
out-of-order candles, and observations at or before the source boundary are
rejected. Missing candles remain explicit and make a window incomplete.

## Replay and leakage prevention

Historical replay requires strictly chronological, single-market snapshots.
Each snapshot is passed once to the production `RuntimeOrchestrator`.
Deterministic replay Hypothesis IDs and Runtime event identities make reruns
safe. Future observations are retained only by the offline runner and are
attached after Runtime has completed; they are never included in Runtime
inputs. Failed and rejected Runtime cycles are recorded in the run summary and
are not persisted as completed cases.

## Research labels

`objective_outcome_labels_v1` derives only from one configured OutcomeRecord:

- `PUMP_CONTINUATION`;
- `PUMP_FAILURE`;
- `DUMP_CONTINUATION`;
- `DUMP_RECOVERY`;
- `RANGE_OR_CONTROL`;
- `INSUFFICIENT_OUTCOME`.

Thresholds are explicit `LabelPolicyConfig` values. Labels are research data,
not Runtime decisions.

## Dataset export

JSONL rows are ordered by cycle timestamp and case ID. Each row includes the
canonical Runtime event, Scenario Probability, Confidence, Decision, outcomes,
labels, review state, Runtime version, and schema/policy versions.

The adjacent manifest records export identity, deterministic content digest,
case count, markets, source date range, Runtime version, schema versions,
label policy, horizons, included IDs, and exclusion counts. Identical cases
and configuration produce semantically identical rows and manifest.

## Commands

```bash
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 init
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 replay \
  --input snapshots.json --symbol BTCUSDT --exchange binance --timeframe 5m \
  --runtime-version 526e72f
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 \
  complete-outcomes --input future-observations.json
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 counts
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 export \
  --output dataset.jsonl --runtime-version 526e72f
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 validate
```

Invalid configuration or corrupted storage returns a non-zero exit status.

## Current limitations

- SQLite and JSONL only;
- minute timeframes only for outcome attribution;
- no model training;
- no Research Agent execution;
- no live persistence hook;
- no trading execution;
- no automatic self-modification;
- Human Review remains required before research findings can affect Runtime.
