# Offline Coinalyze Raw Evidence Archive & Replay v1

This isolated Pilot package accepts local bytes and explicit provenance. It has
no acquisition, provider client, network, runtime, checkpoint, alert or execution
integration. It does not create events or claim market-data completeness.

## API

Create a dedicated, empty local archive directory explicitly, then use
`RawEvidenceArchive(path).archive(capture)` and `.replay()`. Construction does no
I/O. Missing roots and symlink roots are rejected; the library never creates a
root or deletes anything. Each operation locks the root and validates every
committed bundle. No caller-provided index is trusted.

`archive()` returns `ArchiveResult` with a `Status` value: `ARCHIVED`,
`DUPLICATE_NOOP`, `IDENTITY_CONFLICT`, `VALIDATION_ERROR`, `PERSISTENCE_ERROR` or
`CORRUPTION_ERROR`. Errors omit supplied values. `evidence` contains deterministic
metadata and its digest on success; `pending` names all unknown/uncommitted root
entries observed. A duplicate remains a no-op even when pending entries exist,
and reports those entries. Pending entries block new appends.

`replay()` returns `ReplayResult.records` in manifest order, each containing the
evidence envelope and exact `raw_response_bytes`. It returns no records if any
committed bundle fails validation. `error_status` is `None` on successful
validation, otherwise `CORRUPTION_ERROR` or `PERSISTENCE_ERROR`. Unknown/pending
entries are reported separately, never replayed, accepted, repaired or deleted.
`complete` means archive recovery has no errors or pending entries; it is **not**
a market-data completeness claim.

## Input and identity

The input is a plain Python dictionary with all fields listed in the Task 2
prompt. `raw_response_bytes` must be `bytes`. Schema version is
`coinalyze-raw-evidence/v1`; provider is `coinalyze`. V1 supports a single explicit
symbol per GET history capture. `endpoint_identity` is an absolute path such as
`/v1/ohlcv-history`, with no host, credentials or query string. Identity/version
tokens use letters, digits, underscore, dot, colon or hyphen.

`request_parameters` contains exactly one of `symbol` or `symbols`, plus
`interval`; each must agree with explicit requested identity. Optional supported
parameters are `from`, `to` (nonnegative integer provider epoch seconds),
`convert_to_usd` (boolean), `exchange` and `contract`. Unsupported parameters,
headers and top-level fields are rejected. Exchange and contract, when present
in request parameters or parseable response objects, must match provenance.

`provider_api_version_or_unavailable_reason` and
`content_type_or_unavailable_reason` each have exactly `value` and
`unavailable_reason`: one nonempty string and one `None`.
`http_status_or_transport_error` has exactly `http_status` and `transport_error`:
an integer HTTP status from 100 through 599 with a null error, or a null status
with a nonempty error. Error text must not contain credentials.

All three envelope timestamps are required ISO-8601 strings with seconds, up to
six fractional digits and `Z` or an explicit offset. Canonical timestamps use UTC
and six fractional digits. Request start must precede or equal response receipt,
which must precede or equal recording. File times are irrelevant. Provider `t`
values remain untouched inside raw bytes; no provider timestamp is synthesized.

Two additional explicit fields implement the prompt's status/quality rules:

* `capture_status`: `VALID_RESPONSE`, `MALFORMED_RESPONSE`, `INCOMPLETE_CAPTURE`
  or `PROVIDER_ERROR`. Valid means a parseable history envelope passed these
  checks, not that its market data is complete, closed, correct or eligible.
* `quality_flags`: unique strings, stored in sorted order. Always include
  `COMPLETENESS_NOT_ASSERTED`. Additional supported flags are `MISSING_RECORDS`,
  `GAPS`, `DUPLICATE_RECORDS`, `CONFLICTING_DUPLICATES`,
  `OPEN_OR_INCOMPLETE_CANDLES`, `UNAVAILABLE_METRICS` and `LATE_RECORDS`.

The parser checks the history envelope (`[{symbol, history: [{t, ...}]}]`) only
to reject mismatched identity and require conservative status/quality markers.
Provider history `t` must be a nonnegative integer epoch second. Missing or
malformed `t` requires `MALFORMED_RESPONSE`. Invalid JSON/encoding, duplicate JSON
object keys or non-finite JSON numbers require `INCOMPLETE_CAPTURE` or
`PROVIDER_ERROR`, since truncation/ambiguous decoding cannot be ruled out.
An HTTP/transport failure cannot be `VALID_RESPONSE`.

Observed duplicate timestamps, differing records at the same timestamp, empty
histories, gaps over 60 seconds for `1m`/`1min`, explicit false `closed`/`complete`
values and null metric values require their corresponding flags. Checks include
records across repeated history groups. Caller flags may describe additional
known defects. These checks do not verify every endpoint-specific metric,
calendar boundary, expected request-range record, or implicit candle closure.
Missing information is never synthesized or interpreted as zero. Out-of-order
records, duplicate records, raw field order, whitespace and number spelling
remain exactly as received. The parser never emits transformed market data.

Canonical request identity includes provider, endpoint, method, request
parameters, market, exchange, contract, symbol, interval and request start.
`raw_evidence_id` is `re1:` plus SHA-256 of its canonical JSON bytes. Raw SHA-256
is computed separately from the original response bytes. Changed response bytes
or provenance under the same identity is `IDENTITY_CONFLICT`, with no overwrite.
Equivalent UTC timestamp spellings and metadata dictionary ordering canonicalize
identically. A later request start creates a new capture identity; changed
recording time alone is a provenance conflict.

Metadata is UTF-8 JSON with sorted keys, compact separators, a trailing newline,
and no NaN/Infinity. The supplied bytes are never re-encoded. The envelope stores
canonical input provenance, identity, raw hash/size, relative object references,
sequence, transport status and content type. `metadata_digest` is SHA-256 of the
entire metadata file, stored in the manifest and returned evidence, not inside
the file it hashes. Sequences and references depend on append order; replay of
the same archive is deterministic.

## Persistence and recovery

The archive root contains ordered 20-digit directories. Each committed directory
contains exactly `raw.bin`, `metadata.json` and `manifest.json`. Together, ordered
manifest entries form an append-only manifest with a previous-manifest SHA-256
chain. There is no separately rewritten index or mutable manifest head.

A writer holds a POSIX advisory lock on the root directory, creates an exclusive
`.pending-<uuid>` directory, fsyncs the root, writes and fsyncs each exclusive
object, fsyncs the pending directory, then publishes the entire directory with
one rename and fsyncs the root. Publication is the atomic visibility point: no
committed name is exposed with only some of its objects. The API never opens
committed objects for writing. A nonempty committed destination cannot be
overwritten by rename, and cooperating writers serialize under the root lock.

Failure before publication leaves explicitly reported pending work. Failure
after rename but before successful root fsync returns `PERSISTENCE_ERROR`, since
durability was not acknowledged. Replay may find the whole capture committed;
an exact retry then returns `DUPLICATE_NOOP`. There is no automatic pending
cleanup or promotion API.

Replay verifies contiguous sequence numbers, regular files with no symlinks,
the exact bundle file set, canonical envelope bytes, recomputed request identity,
raw hash/size, metadata digest, references, unique capture identities and the
manifest chain. It revalidates provenance and payload status. Corruption blocks
appends as well as replay; a seemingly valid prefix is never returned.

## Limits

This is a local POSIX filesystem protocol (tested with directory locking, rename
and fsync), not a distributed/object-storage protocol. Root ancestors and the
local account are trusted. Do not use a network filesystem without independently
establishing these semantics. Power-loss testing is not claimed; fault injection
checks software failure paths. Immutability is enforced by this API, not by WORM
hardware or signatures. Checksums detect inconsistent edits; an attacker who
rewrites all objects and hashes, or deletes the entire archive/trailing bundles,
cannot be detected without an external trusted anchor. Each call scans the
archive and loads raw objects in memory; this v1 favors correctness over scale.

Metadata uses closed fields/parameter allowlists and credential key/text
rejection. Recognizable credentials in response bodies are rejected too. This
is not general secret discovery: opaque secrets cannot be distinguished from
ordinary arbitrary values. Callers must supply credential-free response bytes
and provenance. Rejecting sensitive input never persists or echoes its values.

No Task 1 component is imported or modified. Only Python standard-library
components are reused. See the completion report for acceptance mapping,
verification commands, protected-scope checks and results.
