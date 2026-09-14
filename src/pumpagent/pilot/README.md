# Offline Pilot Checkpoint Recorder v1

Scope: one local recorded stream, one run/symbol/provider/market/contract/timeframe.
No data acquisition, candle aggregation, analytical inference, transition policy,
alert selection/delivery, outcome processing, or execution. Importing the module
does not perform I/O. Existing Framework rules and Runtime are not changed.

## Public boundary

`checkpoint.map_recorded_snapshot(input_dict)` is pure and returns immutable
canonical input bytes and a stable checkpoint ID. `journal.append_checkpoint`
accepts that recorded input and an explicitly supplied `committed_at` ISO string.
It returns `AppendResult(APPENDED | DUPLICATE_NOOP, record)`. Schema/link errors
raise `ValidationError`, changed input under the same identity raises
`IdentityConflict`, corrupt committed records raise `CorruptionError`, and OS
permission/persistence errors propagate as `OSError` subclasses. No fake success.

`journal.recover(absolute_directory, symbol=...)` validates the entire journal
and returns records, last_valid, and uncommitted_files. Corruption raises with
the last validated prefix record in `CorruptionError.last_valid`; that diagnostic
is not a successful recovery and must not be used as a silent fallback.

The caller must explicitly supply an existing absolute directory named
`pilot_shadow`, separate from research/cases. No implicit directory creation,
CWD lookup, `.env` loading, or environment mutation. Tests use disposable
temporary directories with that name; no operational journal is shipped.

## Closed input schema: pilot_recorded_snapshot_v1

Every field is required unless explicitly described otherwise. Unknown keys are
rejected at every structured level, including outcome/P&L/future-return/MFE/MAE/
later-observation fields. There is no arbitrary metadata bag or outcome payload.

- schema_version: exact `pilot_recorded_snapshot_v1`.
- run_id, event_id, symbol, provider, market_id, contract_id: nonempty exact text.
  Labels are case-sensitive and are never silently canonicalized or inferred.
- source_evidence: exactly id and lowercase 64-character sha256.
- timeframe: positive duration in s/m/h/d (e.g. 1m); this expresses an interval,
  not an aggregation function. No exchange-calendar anchoring is inferred.
- candle_open_time, candle_close_time, received_time, recorded_time: aware ISO
  timestamps. Close minus open must equal timeframe; close <= received <= recorded.
- provider_event_time: `{status: AVAILABLE, value: aware_ISO}` or
  `{status: UNAVAILABLE, reason: nonempty_text}`. If available it must not exceed
  recorded_time. All other timestamps are mandatory. Clock-skew correction is
  deliberately not performed.
- normalized_values: exactly price, volume, open, high, low, close, open_interest,
  oi_change_pct, cvd, funding_rate, liquidations. The first six are mandatory.
  Each present metric is `{status: AVAILABLE, value: finite_number, unit: text,
  provenance: {source_evidence_id, source_field, kind, derivation_version}}`.
  kind is OBSERVED or DERIVED. Every present metric needs a version, including
  observed values (e.g. literal normalization version). Provenance references the
  supplied source evidence ID. Optional metrics must be present as explicit
  `{status: UNAVAILABLE, reason: text}` when unavailable. No numeric imputation.
- quality: exactly status, missing_metrics, reasons, warnings. Status reuses
  LiveDataQualityStatus (good/delayed/partial/corrupted/unknown). The other fields
  are explicit duplicate-free text lists; missing_metrics must match unavailable
  metrics. Non-good status requires reasons. Optional absence does not itself
  downgrade quality; this recorder preserves the supplied assessment. Recording
  unknown/corrupted quality is allowed and never promotes evidence to usable.
- previous_checkpoint_id: null only for the first committed record, otherwise
  the exact previous checkpoint ID. Link/stream/time checks occur at append/recovery.
- assessments: exactly geometry_state, counter_thesis_state, alert_state. Each
  is an explicitly supplied opaque nonempty label; UNASSESSED is valid. The
  recorder neither supplies defaults nor interprets or changes analytical labels.
- versions: exactly code, config, schema; nonempty IDs. schema must equal
  `pilot_checkpoint_record_v1`. Code/config IDs are recorded claims, not verified
  against external files or automatically read from Git/environment.

Literal composition builds a one-candle NormalizedMarketDataInput and invokes
its existing validator. For that validation projection, source_timestamp is the
explicit candle close (not an invented provider event time); the original optional
provider clock is preserved separately. Pilot adds stricter aware-time, numeric,
closed-field, and provenance validation. No existing loader or quality translator
is needed: a quality promotion or inferred missing values would not be lossless.

## Record schema: pilot_checkpoint_record_v1

PilotCheckpointRecordV1 holds immutable canonical UTF-8 bytes; to_dict returns a
detached copy. Records contain exactly schema_version, checkpoint_id, input,
commit_sequence, committed_at, canonical_digest. The nested input preserves all
validated provenance, missingness, supplied assessments, and previous link.

Canonical serialization is sorted-key compact JSON, ensure_ascii=False,
allow_nan=False, UTF-8, followed by one LF. Aware timestamps normalize to UTC
with six fractional digits. List order and numeric JSON types are preserved:
1 and 1.0 are different content. This is the versioned Python JSON convention,
not a claim of RFC 8785 cross-language number serialization.

checkpoint_id is `pc1:` plus SHA-256 of canonical run_id, event_id, symbol,
provider, market_id, contract_id, timeframe, candle_open_time, candle_close_time.
Outcome, assessments, commit time, source digest, and sequence do not define
identity. Changes to non-identity supplied content under that identity conflict.
canonical_digest is SHA-256 of the canonical full record excluding that digest.
Commit time is supplied by caller, must be >= recorded_time and nondecreasing.

## Append, durability, replay, and recovery

Local POSIX filesystem only (target: macOS). Cooperating readers/writers lock the
directory descriptor with flock. Ordered final filenames are 20-digit sequence
numbers plus .json. Each complete record is staged in the same directory, flushed
and fsynced, then atomically hard-linked into its new exclusive final name.
Directory fsync follows publication. No committed record is replaced or deleted;
only the current call's temporary staging name is cleaned up.

Crash before publication leaves either nothing or a `.pending-*` staging file;
it is not a committed record. Recovery reports such files without deleting,
replaying, or repairing them. Crash after publication exposes a complete record.
An error after publication may mean the commit exists with uncertain durability:
the call raises, and explicit recovery/exact replay resolves that ambiguity.

Exact replay of any existing canonical input returns its original record without
writing a new one, irrespective of a newly supplied commit timestamp. Changed
content raises HARD CONFLICT. New records must reference the latest record,
advance candle close time, and stay within the same stream. Recovery checks every
record's bytes/digest/schema/identity/sequence/link/time ordering. Noncanonical
JSON, truncation, modified content, gaps, and unexpected entries explicitly fail.

Limits: trusted local filesystem and cooperating users; no distributed locking,
network filesystem guarantees, signatures, raw-byte archive, anti-tamper security,
or detection of deliberate removal of a complete journal suffix. Supplied source
digests are format-checked; raw bytes are not fetched or verified. Recovery scans
the entire journal; no daemon, indexing, retry, repair, or compaction exists.

## Fixture and tests

`tests/fixtures/pilot/two_checkpoints_v1.json` contains two consecutive synthetic
DEMOUSDT one-minute recorded inputs with literal numbers and UNASSESSED labels.
Source digests hash UTF-8 `synthetic source 1` and `synthetic source 2`; no live or
historical case/outcome was used. The second input includes the computed first ID.

Tests live in `tests/pilot/test_checkpoint_recorder.py`. They write only disposable
pytest temporary journals, test failure injection and actual permissions, and
verify offline imports/call boundaries. Existing source/test files need no wiring.
