# Scanner V2 Data Contract

Contract version: 1.0
Scanner version: Market Participation Scanner V2 MVP
Status: ACCEPTED / CLOSED

## 1. Purpose

Market Participation Scanner V2 collects and validates raw market
participation metrics for selected Coinalyze Bybit USDT futures markets.

Scanner V2 does not classify market states and does not make trading decisions.
It produces basic validated participation evidence and data-quality outcomes
that future modules may consume.

## 2. Module Boundary

Scanner V2 is responsible for:

- market discovery;
- filtering;
- batching;
- fetching OHLCV and Open Interest;
- validating timestamps;
- calculating basic metrics;
- data-quality classification;
- API-rate protection.

Scanner V2 is not responsible for:

- Continuation Alive;
- Freeze Phase;
- First Failure;
- Continuation Death;
- Short Milking;
- Trade Permission;
- Entry Quality;
- Long/Short/Stay Out decisions;
- alerts;
- Telegram;
- execution.

## 3. Input Configuration Contract

| Field | Type | Default | Required | Valid range or format | Fallback behavior | Security considerations |
| --- | --- | --- | --- | --- | --- | --- |
| `COINALYZE_API_KEY` | string | none | yes, unless fallback key exists | non-empty API key string | first priority credential | must never be printed, logged, committed, or included in command history |
| `COINALYZE_KEY` | string | none | no | non-empty API key string | second priority credential | same secret-handling rules as `COINALYZE_API_KEY` |
| `API_KEY` | string | none | no | non-empty API key string | legacy final fallback credential | same secret-handling rules; avoid for new setup |
| `SCAN_LIMIT` | integer | `20` | no | zero or greater | uses default when unset | not secret |
| `SCAN_OFFSET` | integer | `0` | no | zero or greater | uses default when unset | not secret |
| `OHLCV_LOOKBACK_MINUTES` | integer | `90` | no | zero or greater | uses default when unset | not secret |
| `OI_LOOKBACK_MINUTES` | integer | `30` | no | zero or greater | uses default when unset | not secret |
| `DIAGNOSTIC_SYMBOLS` | comma-separated string | empty | no | Coinalyze symbols such as `AGIUSDT.6,AIOZUSDT.6` | absent means normal batch mode | not secret; bypasses normal batch slicing |

The scanner loads `./.env` if present. The loader ignores empty lines and
comments, and it does not overwrite environment variables that already exist.

## 4. Market Selection Contract

- Source endpoint: Coinalyze `/future-markets`.
- Source exchange: Bybit futures as represented by Coinalyze.
- Quote asset: `USDT`.
- Symbol format: Coinalyze Bybit suffix `.6`, for example `WLDUSDT.6`.
- Filter requirements:
  - `symbol` ends with `.6`;
  - `quote_asset` is `USDT`;
  - `exchange` is empty, contains `BYBIT`, or equals `6`;
  - `is_perpetual` is truthy;
  - `has_ohlcv_data` is truthy.
- Stable sort rule: sorted by `symbol` string after filtering.
- Batch slicing rule: `markets[SCAN_OFFSET:SCAN_OFFSET + SCAN_LIMIT]`.
- Out-of-range offset behavior: prints a clear message and performs no market
  history requests when `SCAN_OFFSET` is beyond available markets.
- Diagnostic mode behavior: when `DIAGNOSTIC_SYMBOLS` is set, the scanner
  processes only those symbols and does not apply normal filtering or batch
  slicing.
- Non-overlap guarantee: if filtered ordering is unchanged and offsets are
  incremented by `SCAN_LIMIT`, selected batches do not overlap.

## 5. Metric Contract

### PRICE_5M

- Formula:

```text
(latest completed 5m close - latest completed 5m open)
/
latest completed 5m open
* 100
```

- Data source: Coinalyze OHLCV history.
- Endpoint: `/ohlcv-history`.
- Timeframe: `5min`.
- Records used: one latest completed OHLCV record.
- Closed-candle rule: the latest open 5-minute bucket is excluded.
- Timestamp requirements: latest OHLCV bucket must equal latest OI bucket.
- Unit: percent.
- Expected type: float.
- Invalid-data conditions: missing open, missing close, non-numeric open or
  close, or open equal to zero.
- Known limitations: this is a latest completed 5-minute candle measure only;
  it is not a trend score or state classifier.

### VOLUME_RATIO_5M

- Formula:

```text
latest completed 5m volume
/
average(previous 10 completed 5m volumes)
```

- Data source: Coinalyze OHLCV history.
- Endpoint: `/ohlcv-history`.
- Timeframe: `5min`.
- Records used: latest completed OHLCV record plus previous 10 completed OHLCV
  records.
- Closed-candle rule: the latest open 5-minute bucket is excluded.
- Timestamp requirements: latest OHLCV bucket must equal latest OI bucket.
- Unit: ratio, where `1.0` means equal to the 10-candle baseline average.
- Expected type: float.
- Invalid-data conditions: missing latest volume, fewer than 10 numeric
  baseline volumes, or zero baseline average.
- Known limitations: internal OHLCV gaps can reduce available baseline records;
  the scanner does not synthesize missing candles.

### OI_CHANGE_5M

- Formula:

```text
(latest completed OI - previous completed OI)
/
previous completed OI
* 100
```

- Data source: Coinalyze Open Interest history.
- Endpoint: `/open-interest-history`.
- Timeframe: `5min`.
- Records used: latest two completed OI records.
- Closed-record rule: the latest open 5-minute bucket is excluded.
- Timestamp requirements: latest OI bucket must equal latest OHLCV bucket.
- Unit: percent.
- Expected type: float.
- Invalid-data conditions: missing OI, non-numeric OI, or previous OI equal to
  zero.
- Known limitations: this is single-symbol OI as returned for the selected
  Coinalyze symbol; no aggregated OI is produced yet.

## 6. Timestamp Contract

- Coinalyze history records use Unix timestamps in seconds in field `t`.
- The scanner normalizes timestamps to 5-minute buckets:

```text
bucket = timestamp - (timestamp % 300)
```

- OHLCV and OI records are sorted explicitly by timestamp.
- Completed-record filtering excludes any record where:

```text
timestamp + 300 > current_unix_time
```

- `OHLCV_OI_ALIGNMENT_REQUIRED = True`.
- A market is `VALID` only when latest completed OHLCV and OI records normalize
  to the same 5-minute bucket.
- Timestamp mismatch behavior: the market is `SKIPPED` with reason beginning
  `OHLCV/OI timestamp mismatch`.
- Current decision: no older aligned fallback is used.

## 7. Result Status Contract

Scanner V2 has exactly three top-level per-market outcomes.

### VALID

- all required data exists;
- formulas calculate safely;
- latest OHLCV and OI buckets align;
- output metrics are usable for downstream analysis.

### SKIPPED

- expected data-quality limitation;
- not a scanner crash;
- market is excluded from downstream state analysis for that scan.

### FAILED

- API error;
- timeout;
- malformed response;
- unexpected exception;
- technical failure requiring monitoring or investigation.

The current terminal output uses `STATUS: VALID`, `STATUS: SKIPPED`, and
`STATUS: ERROR`. In the proposed normalized contract, terminal `ERROR` maps to
top-level `FAILED`.

## 8. Skip Reason Enumeration

Current scanner identifiers and display labels:

| Identifier | Display label |
| --- | --- |
| `insufficient_ohlcv_history` | `Skipped insufficient OHLCV history` |
| `insufficient_oi_history` | `Skipped insufficient OI history` |
| `timestamp_mismatch` | `Skipped timestamp mismatch` |
| `invalid_price_data` | `Skipped invalid price data` |
| `invalid_volume_data` | `Skipped invalid volume data` |
| `zero_volume_baseline` | `Skipped zero volume baseline` |
| `invalid_oi_data` | `Skipped invalid OI data` |
| `zero_previous_oi` | `Skipped zero previous OI` |
| `other` | `Skipped other` |

Actual current reason strings include:

- `insufficient closed OHLCV history (...)`;
- `insufficient closed Open Interest history (...)`;
- `OHLCV/OI timestamp mismatch | OHLCV bucket: ... | OI bucket: ...`;
- `invalid OHLCV/OI timestamp data`;
- `invalid price data`;
- `invalid volume data`;
- `zero baseline volume`;
- `invalid OI data`;
- `zero previous OI`.

The reason `invalid OHLCV/OI timestamp data` currently classifies as `other`.

## 9. Failure Contract

- Network timeout: request failure is converted to `request timed out for ...`
  and may become a batch API failure for selected markets.
- Authentication failure: an HTTP/API error from Coinalyze is reported as a
  runtime error or per-market batch API failure, depending on when it occurs.
- HTTP/API failure: `HTTP <code> from <endpoint>: <redacted/short body>`.
- Malformed response:
  - `/future-markets` response must be a list;
  - history responses must be lists;
  - invalid JSON raises `invalid JSON from <endpoint>`.
- Per-market exception handling: unexpected processing exceptions become
  `STATUS: ERROR` for that market and do not stop the batch.
- Whole-run startup failure: missing credential raises a clear error before API
  requests.
- Credential-loading failure: no key found in `COINALYZE_API_KEY`,
  `COINALYZE_KEY`, or `API_KEY` stops the run.

## 10. Batch Summary Contract

Scanner V2 prints these summary values:

- `Markets available`;
- `Scan offset`;
- `Scan limit`;
- `Markets selected`;
- `Markets attempted`;
- `Markets valid`;
- `Markets skipped`;
- `Markets skipped timestamp mismatch`;
- skip-reason counters;
- `Markets failed`;
- `API requests used`;
- `Minimum closed OHLCV records observed`;
- `Maximum closed OHLCV records observed`;
- `Average closed OHLCV records observed`;
- `Rate-limit pauses`;
- rate-limit pause details;
- `Elapsed time`.

Required invariant:

```text
Markets attempted =
Markets valid +
Markets skipped +
Markets failed
```

## 11. Data-Quality Guarantees

Downstream modules may trust:

- stable market ordering after filtering;
- selected batch boundaries;
- closed 5-minute metrics for `VALID` results;
- latest-bucket OHLCV/OI alignment for `VALID` results;
- no metric output from invalid denominators;
- explicit status for every attempted market;
- no hidden fallback to older aligned buckets;
- no silent timestamp mismatch acceptance.

## 12. Non-Guarantees And Limitations

- Coinalyze may have missing OHLCV intervals.
- Recent OHLCV and OI availability may update at different times.
- Sparse or new markets may not have enough records.
- Formulas are reconstructed and validated for MVP, not recovered from the lost
  original scanner.
- Scanner V2 currently uses only Price, Volume, and Open Interest.
- No CVD, Spot participation, Funding, Liquidations, or Taker Buy/Sell Volume
  is produced yet.
- Metric geometry is not yet produced by Scanner V2.
- A `VALID` result does not imply a trade opportunity.

## 13. Future Extension Rules

Future metrics may be added without breaking this contract if:

- existing field names and meanings remain unchanged;
- new fields are optional at first;
- absent future metrics are represented explicitly as unavailable or `UNKNOWN`;
- existing `VALID` / `SKIPPED` / `FAILED` semantics remain stable;
- downstream modules do not infer values from missing fields.

Candidate future fields:

- `aggregated_oi`;
- `cvd`;
- `spot_participation`;
- `taker_buy_volume`;
- `taker_sell_volume`;
- `funding`;
- `liquidations`;
- `metric_geometry`;
- `data_freshness`.

Do not implement these fields until a future checkpoint explicitly approves
them.

## 14. Future Market State Detector Input

PROPOSED NORMALIZED OUTPUT FOR NEXT MODULE

This structure is a contract proposal, not an instruction to refactor Scanner
V2 yet.

```json
{
  "contract_version": "1.0",
  "scanner_version": "Market Participation Scanner V2 MVP",
  "symbol": "WLDUSDT.6",
  "status": "VALID",
  "timestamp_bucket": 1783889100,
  "timeframe": "5m",
  "metrics": {
    "price_5m_pct": -0.64,
    "volume_ratio_5m": 1.42,
    "oi_change_5m_pct": 0.03
  },
  "data_quality": {
    "closed_candles_only": true,
    "ohlcv_oi_aligned": true,
    "skip_reason": null
  },
  "failure_error": null
}
```

Future detector code must treat `SKIPPED` and `FAILED` markets as unavailable
for market-state classification in that scan.

## 15. Versioning

Breaking changes require:

- a new contract version;
- explicit review;
- a migration note;
- a new checkpoint.

Examples of breaking changes include redefining a metric formula, renaming an
existing field, weakening status semantics, or accepting timestamp mismatches as
valid without a new contract.
