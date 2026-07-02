# Market Data

## Purpose

The first responsibility of PumpAgent is not analysis.

The first responsibility is perception.

The agent must continuously collect raw market information before forming any hypothesis.

---

# Raw Market Data

PumpAgent receives:

- Price
- Volume
- Open Interest (OI)
- Aggregated Open Interest
- Funding Rate
- CVD
- Liquidations
- Order Book (future)
- Trades (future)

These are raw observations.

No interpretation happens at this stage.

---

# Timeframes

The agent may observe multiple timeframes simultaneously.

Primary:

- 1m
- 3m
- 5m

Context:

- 15m
- 1H
- 4H

---

# Data Quality

Every incoming update must be checked.

Possible states:

- valid
- delayed
- missing
- corrupted

The agent must never make strong conclusions from poor-quality data.

---

# Live Data Boundary

Live Data is responsible for acquisition before Runtime reasoning begins.

The approved Live Data flow is:

Exchange

↓

Bybit Transport

↓

Normalizer

↓

Validation

↓

Quality Translation

↓

Runtime Bridge

↓

MarketSnapshot

Runtime modules must not communicate directly with exchanges.

Live Data must not create RuntimeEvent, run the Runtime Orchestrator, invoke
Runtime reasoning modules, generate trading signals, execute trades, create
orders, persist cases, run Research Agent work, or produce autonomous decisions.

After `MarketSnapshot`, Runtime has no dependency on exchange adapters,
transport code, normalizers, validation, quality translation, or the Runtime
Bridge.

---

# Runtime Bridge

Current state:

- Runtime Bridge converts validated and quality-approved `NormalizedMarketDataInput`
  into Runtime `MarketSnapshot`.
- It runs validation as a safety gate.
- It runs Quality Translation as a safety gate.
- It maps translated quality into Runtime `DataQualityStatus`.
- It preserves source event id, source timestamp, OHLCV, price, volume, symbol,
  exchange, timeframe, source metadata, missing fields, and approved diagnostic
  metadata.
- It returns `RuntimeMarketDataBridgeResult`.

Runtime Bridge does not:

- create RuntimeEvent;
- run the Runtime Orchestrator;
- invoke Perception, Structure, Market Efficiency, Hypothesis, Agent State,
  Scenario Probability, Confidence, Decision / Alert, or Learning Memory;
- communicate with exchanges;
- normalize raw exchange payloads;
- execute trades;
- create orders;
- produce autonomous signals.

---

# Bybit Public Kline Transport

Current state:

- `BybitAdapter` performs only the approved public REST Kline transport.
- The only endpoint in scope is `GET /v5/market/kline`.
- The only approved market category is `linear`.
- The adapter returns raw acquisition output through `LiveDataResult.raw_data`.
- The adapter does not normalize Bybit payloads.
- The adapter does not call the Live Data Validation Layer.
- The adapter does not call Quality Translation.
- The adapter does not call the Runtime Bridge.
- The adapter does not create MarketSnapshot or RuntimeEvent.

Explicitly out of scope:

- authentication;
- private API calls;
- WebSocket streaming;
- retries;
- rate-limit scheduling;
- normalization;
- Runtime integration;
- Runtime reasoning;
- trading execution;
- orders;
- persistence;
- autonomous signals;
- Research Agent work.

`BybitAdapter` supports configurable request timeout through:

```text
timeout_seconds: float = 10.0
```

The adapter maps transport and payload failures into structured Live Data errors.

Handled cases include:

- request timeout;
- HTTP 429;
- HTTP 5xx;
- invalid JSON;
- invalid UTF-8 response body;
- missing `retCode`;
- missing `result`;
- missing `result.list`;
- malformed `result.list`;
- non-zero Bybit `retCode`.

Current test coverage includes:

- public Kline URL and request parameter behavior;
- configurable timeout propagation;
- timeout error mapping;
- malformed payload handling;
- invalid UTF-8 handling;
- non-zero Bybit `retCode` mapping;
- strict boundary checks proving no Runtime objects or Runtime reasoning modules
  are used.

---

# Bybit Raw Payload Normalizer

Current state:

- The Bybit Raw Payload Normalizer transforms Bybit Public REST Kline raw
  payloads into the generic Live Data contract.
- It reads successful `LiveDataResult.raw_data` output produced by the Bybit
  public Kline transport.
- It creates `NormalizedMarketDataInput`.
- It converts Bybit millisecond timestamps into the internal timestamp
  representation.
- It converts OHLCV numeric string values into numeric values.
- It selects the latest candle deterministically by candle timestamp.
- It maps Bybit candle rows into generic exchange-independent OHLCV fields.
- It maps the latest close into `price`.
- It maps the latest volume into `volume`.
- It preserves exchange, symbol, timeframe, data source, source metadata, and
  approved diagnostic metadata.
- It uses `LiveDataQualityStatus.UNKNOWN` only as a neutral technical
  placeholder required by the current contract.

The normalizer is purely transformational.

It does not:

- perform semantic validation;
- evaluate data quality;
- translate quality states;
- call the Live Data Validation Layer;
- call Quality Translation;
- call the Runtime Bridge;
- import Runtime modules;
- create MarketSnapshot;
- create RuntimeEvent;
- communicate with exchanges;
- perform networking;
- retry requests;
- persist data;
- execute trades;
- generate signals;
- perform autonomous reasoning.

Transformation failures return:

```text
LiveDataResult(success=False, error=LiveDataError(...))
```

Current transformation failures are reported as `MALFORMED_PAYLOAD`.

Handled transformation failure cases include:

- failed input `LiveDataResult`;
- missing `raw_data`;
- missing raw payload;
- missing request metadata;
- missing `payload.result`;
- missing `payload.result.list`;
- empty `payload.result.list`;
- malformed candle row;
- candle row with insufficient fields;
- invalid timestamp conversion;
- invalid OHLCV numeric conversion.

Current test coverage includes:

- Happy Path normalization;
- timestamp conversion;
- OHLCV conversion;
- latest candle selection;
- latest close to `price` mapping;
- latest volume mapping;
- symbol and timeframe mapping;
- SourceMetadata preservation;
- transformation failure handling;
- Runtime boundary checks proving no Runtime imports or Runtime objects are
  used.

---

# Perception Output

Status: planned next Runtime alignment milestone.

The Perception Engine does not generate trading signals.

The planned Perception Engine reads `MarketSnapshot` and produces
`ObservationPackage`.

`ObservationPackage` becomes the neutral observation input for higher-level
evidence and reasoning modules.

Output example:

Price:
Volume:
OI:
Funding:
CVD:
Liquidations:

Timestamp:
Exchange:
Symbol:

---

# Design Principle

Perception must remain objective.

Interpretation belongs to higher modules.

The planned Perception Engine only answers one question:

"What is happening right now?"
