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
→ persisted LearningReadinessAssessment
→ deterministic JSONL dataset
```

`RuntimeEvent` remains the sole analytical authority. Learning persistence is
explicitly invoked after Runtime completion; it is not inserted into
`RuntimeOrchestrator`. Outcomes, reviews, labels, and export metadata are
separate versioned records. They never rewrite the stored canonical event,
execute trades, emit live signals, or update Runtime configuration.

A stored case is not automatically suitable for learning. Dataset admission is
owned exclusively by the persisted readiness assessment described below.

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
- `readiness_assessments`;
- `schema_metadata`.

Case and Runtime event IDs are unique. Outcome versions are immutable and the
repository selects one authoritative record per case and horizon
deterministically. Identical retries are accepted; conflicting content under an
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
not Runtime decisions. Deterministic precedence is pump failure, dump recovery,
positive continuation, negative continuation, then range/control; incomplete
input produces `INSUFFICIENT_OUTCOME`.

## Learning readiness quality gate

`learning_readiness_assessment_v1` is an immutable, persisted audit record.
Its technical status is one of:

- `PENDING`: reserved for a scheduled assessment that has not run;
- `NOT_READY`: required information is missing, incomplete, or unsupported;
- `LEARNING_READY`: every mandatory technical check passed;
- `INVALID`: stored identities, digests, canonical content, or provenance are
  inconsistent or corrupt.

The active and supported validator registry contains
`learning_readiness_validator_v2`. It checks the completed canonical
RuntimeEvent, required sections, cross-section event/Episode/market identities,
F-02 timestamp invariants, canonical digest, finite numeric values, supported
schemas and Runtime version, replay or ingestion provenance, repository
integrity, authoritative outcome identity/completeness/horizon/version, and a
reproducible sufficient `objective_outcome_labels_v1` label.

Technical readiness and human review are deliberately separate. An assessment
records `technically_ready`, `approved_for_evaluation`,
`approved_for_training`, `review_status`, and `manually_excluded`. A manual or
administrative exclusion blocks every export even when technical checks pass.
Pending human review permits the `evaluation` policy but does not permit the
`training` policy. Training explicitly accepts only `reviewed`, `approved`, or
`not_required`.

Assessment identity incorporates the canonical payload digest, selected
OutcomeRecord, Runtime schema, outcome computation version, label policy,
validator version, review state, and exclusion state. Repeating identical input
is idempotent. A relevant version, authoritative outcome, review, or exclusion
change creates a new immutable assessment while preserving history. The latest
assessment matching the current dependency fingerprint is selected by
assessment timestamp and canonical assessment ID. SQLite insertion order and
`rowid` have no semantic role.

The dependency fingerprint binds case and RuntimeEvent identity, current case
digest, authoritative outcome ID and horizon, outcome computation version,
label policy, validator version, review state, manual exclusion, and
administrative block. F-03.1 `learning_readiness_validator_v1` records remain
immutable audit history but are not authoritative under the v2 registry.

`LearningReadinessService` derives claims, but the repository independently
recomputes them from current stored facts before insertion. Public constructors
and public identity builders therefore cannot certify readiness. Dataset
authorization again derives current facts and requires an exact authenticated,
fresh assessment.

## Dataset export

JSONL export requires a named readiness policy and explicit outcome horizon.
It never infers readiness from
case or outcome status. Cases without an authoritative assessment, cases with
`NOT_READY`/`INVALID` status, manual exclusions, and training cases without an
allowed review status are omitted with deterministic manifest reasons.
Wrong horizons, unsupported validators or label policies, stale case digests,
stale outcomes, and unauthenticated assessments are also excluded with stable
machine-readable reason codes.

Rows are ordered by cycle timestamp and case ID. Each row includes the
canonical Runtime event, Scenario Probability, Confidence, Decision, outcomes,
labels, readiness assessment identity and policy, review state, Runtime
version, and schema/policy versions.

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
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 \
  assess-readiness --case-id case-agent-cycle-id --horizon 60
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 \
  assess-all --horizon 60
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 \
  readiness-counts
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 \
  explain-readiness --case-id case-agent-cycle-id
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 export \
  --output dataset.jsonl --runtime-version 526e72f --policy evaluation \
  --horizon 60
PYTHONPATH=src python3 -m pumpagent.learning --store cases.sqlite3 validate
```

Invalid configuration or corrupted storage returns a non-zero exit status.

Replay admission validates embedded candles before Runtime invocation:
timestamps must be aware, unique, chronological, and no later than the
snapshot; OHLCV values must be numeric, finite, and internally coherent.
Outcome persistence independently authenticates source identity and enforces
post-cycle boundaries no later than the declared horizon, with complete
outcomes ending exactly at that horizon.

## Current limitations

- SQLite and JSONL only;
- minute timeframes only for outcome attribution;
- no model training;
- no Research Agent execution;
- no live persistence hook;
- no trading execution;
- no automatic self-modification;
- Human Review remains required before research findings can affect Runtime.
