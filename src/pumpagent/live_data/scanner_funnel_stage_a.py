"""TEST_ONLY Scanner Funnel v1 Stage A.

This module acquires a cheap Bybit market snapshot and a bounded native closed
1m cache.  It deliberately produces no attention, ranking, H2, confirmation,
Tracker, or trading decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from pumpagent.live_data.adapters.market_cap_rank import RankSnapshot
from pumpagent.live_data.automatic_anomaly import N_PRIOR


SCANNER_ROLE = "ATTENTION_DISCOVERY_ENGINE"
TIMEFRAME = "1"
BAR_MINUTES = 1
# The Activity Mapper reuses the frozen Median/MAD test profile, which needs
# N_PRIOR strictly prior bars plus the current completed bar.
DEFAULT_CACHE_BARS = N_PRIOR + 1


def underlying_for_market(symbol: str) -> str:
    value = symbol.upper().split(".", 1)[0]
    return value[:-4] if value.endswith("USDT") else value


@dataclass(frozen=True)
class StageARecord:
    symbol: str
    scanner_cycle_timestamp: datetime
    market_data_timestamp: datetime | None
    market_venue: str
    data_transport_provider: str
    timeframe: str
    data_form: str
    top50_status: str
    rank_freshness_state: str
    trading_universe_eligible: bool
    ticker_available: bool
    closed_1m_cache_status: str
    latest_closed_1m_timestamp: datetime | None
    data_gap_state: str
    stage_a_status: str
    stage_a_reason: str
    attention_state: str = "NOT_EVALUATED"
    ranking_state: str = "NOT_EVALUATED"
    h2_state: str = "NOT_EVALUATED"
    baseline_state: str = "NOT_EVALUATED"

    def to_dict(self) -> dict[str, Any]:
        def stamp(value: datetime | None) -> str | None:
            return value.isoformat() if value else None
        return {
            "symbol": self.symbol,
            "scanner_cycle_timestamp": stamp(self.scanner_cycle_timestamp),
            "market_data_timestamp": stamp(self.market_data_timestamp),
            "market_venue": self.market_venue,
            "data_transport_provider": self.data_transport_provider,
            "timeframe": self.timeframe,
            "data_form": self.data_form,
            "native_or_reconstructed": "NATIVE",
            "top50_status": self.top50_status,
            "rank_freshness_state": self.rank_freshness_state,
            "trading_universe_eligible": self.trading_universe_eligible,
            "ticker_available": self.ticker_available,
            "closed_1m_cache_status": self.closed_1m_cache_status,
            "latest_closed_1m_timestamp": stamp(self.latest_closed_1m_timestamp),
            "data_gap_state": self.data_gap_state,
            "stage_a_status": self.stage_a_status,
            "stage_a_reason": self.stage_a_reason,
            "attention_state": self.attention_state,
            "ranking_state": self.ranking_state,
            "h2_state": self.h2_state,
            "baseline_state": self.baseline_state,
        }


@dataclass
class ClosedOHLCVCache:
    """Bounded, idempotent native 1m closed-bar cache."""

    max_bars: int = DEFAULT_CACHE_BARS
    path: Path | None = None
    _bars: dict[datetime, dict[str, Any]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.max_bars < 1:
            raise ValueError("max_bars must be positive")
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        bar = json.loads(line)
                        self._bars[_timestamp(bar["timestamp"])] = bar
                self._trim()

    def ingest(self, bars: Sequence[Mapping[str, Any]], *, as_of: datetime) -> dict[str, Any]:
        now = _utc(as_of)
        inserted = duplicates = open_bars = 0
        issues: set[str] = set()
        parsed: list[dict[str, Any]] = []
        for source in bars:
            try:
                bar = dict(source)
                ts = _timestamp(bar["timestamp"])
                if ts + timedelta(minutes=BAR_MINUTES) > now or bar.get("closed") is False:
                    open_bars += 1
                    continue
                for field_name in ("open", "high", "low", "close", "volume"):
                    if field_name not in bar or bar[field_name] is None:
                        raise ValueError(f"missing_{field_name}")
                bar["timestamp"] = ts.isoformat()
                bar["closed"] = True
                parsed.append(bar)
            except (KeyError, TypeError, ValueError) as exc:
                issues.add(f"INVALID_BAR:{exc}")
        if [x["timestamp"] for x in parsed] != sorted(x["timestamp"] for x in parsed):
            issues.add("OUT_OF_ORDER_INPUT")
        for bar in sorted(parsed, key=lambda x: x["timestamp"]):
            ts = _timestamp(bar["timestamp"])
            if ts in self._bars:
                duplicates += 1
            else:
                self._bars[ts] = bar
                inserted += 1
        self._trim()
        gap_state = self.gap_state()
        if gap_state == "DATA_GAP":
            issues.add("DATA_GAP")
        self._persist()
        return {"inserted": inserted, "duplicates": duplicates, "open_bars": open_bars, "issues": sorted(issues), "gap_state": gap_state}

    def bars(self) -> list[dict[str, Any]]:
        return [self._bars[key] for key in sorted(self._bars)]

    def latest_timestamp(self) -> datetime | None:
        return max(self._bars) if self._bars else None

    def gap_state(self) -> str:
        keys = sorted(self._bars)
        if len(keys) < 2:
            return "UNKNOWN" if not keys else "NO_GAP_DETERMINABLE"
        return "DATA_GAP" if any(b - a != timedelta(minutes=BAR_MINUTES) for a, b in zip(keys, keys[1:])) else "CONTIGUOUS"

    def _trim(self) -> None:
        for key in sorted(self._bars)[:-self.max_bars]:
            del self._bars[key]

    def _persist(self) -> None:
        if self.path:
            self.path.write_text("".join(json.dumps(self._bars[key], sort_keys=True) + "\n" for key in sorted(self._bars)), encoding="utf-8")


@dataclass
class StageABybitSweep:
    market_enumerator: Callable[[], Sequence[Mapping[str, Any]]]
    rank_snapshot: RankSnapshot
    ticker_loader: Callable[[Sequence[str]], Mapping[str, Mapping[str, Any]]]
    ohlcv_loader: Callable[[str], Sequence[Mapping[str, Any]]]
    cache_factory: Callable[[str], ClosedOHLCVCache] = lambda _symbol: ClosedOHLCVCache()
    transport_provider: str = "bybit_public_rest"
    venue: str = "BYBIT"
    rank_max_age: timedelta = timedelta(hours=24)
    bar_max_age: timedelta = timedelta(minutes=2)

    def run_cycle(self, *, scanner_cycle_timestamp: datetime) -> dict[str, Any]:
        cycle_ts = _utc(scanner_cycle_timestamp)
        raw_markets = list(self.market_enumerator())
        markets = [m for m in raw_markets if self._eligible_market_shape(m)]
        symbols = [str(m["symbol"]).split(".", 1)[0].upper() for m in markets]
        try:
            tickers = dict(self.ticker_loader(symbols))
            ticker_error = None
        except Exception as exc:  # symbol-level output remains auditable
            tickers, ticker_error = {}, f"{type(exc).__name__}:{exc}"
        records: list[dict[str, Any]] = []
        failures = 0
        for symbol in symbols:
            entry, _ = self.rank_snapshot.resolve(underlying_for_market(symbol))
            rank_freshness = self._rank_freshness(cycle_ts)
            if entry is None or rank_freshness != "FRESH":
                top_status, eligible = "UNKNOWN", False
            elif entry.rank <= 50:
                top_status, eligible = "PLUMBING_ONLY", False
            else:
                top_status, eligible = "NON_TOP50_ELIGIBLE", True
            ticker = tickers.get(symbol)
            cache = self.cache_factory(symbol)
            bar_status = "NOT_REQUESTED"; bar_error = None; latest = None; gap = "UNKNOWN"
            if eligible:
                try:
                    result = cache.ingest(self.ohlcv_loader(symbol), as_of=cycle_ts)
                    bar_status = "READY" if result["inserted"] or result["duplicates"] else "INSUFFICIENT_EVIDENCE"
                    latest, gap = cache.latest_timestamp(), result["gap_state"]
                    if result["issues"]: bar_status = "DATA_GAP" if "DATA_GAP" in result["issues"] else bar_status
                    if latest is not None and cycle_ts - latest > self.bar_max_age:
                        bar_status, gap = "STALE", "STALE"
                except Exception as exc:
                    failures += 1; bar_status = "UNKNOWN"; bar_error = f"{type(exc).__name__}:{exc}"
            reason = "eligible_native_market" if eligible else ("top50_plumbing_only" if top_status == "PLUMBING_ONLY" else "rank_unknown_or_stale")
            if ticker_error: reason += ";ticker_unknown:" + ticker_error
            if bar_error: reason += ";ohlcv_unknown:" + bar_error
            stage_status = "READY" if ticker is not None else "UNKNOWN"
            if bar_status == "STALE": stage_status = "STALE_DATA"
            records.append(StageARecord(symbol, cycle_ts, _ticker_time(ticker), self.venue, self.transport_provider, TIMEFRAME, "TICKER_SNAPSHOT_PLUS_CLOSED_OHLCV" if eligible else "TICKER_SNAPSHOT", top_status, rank_freshness, eligible, ticker is not None, bar_status, latest, gap, stage_status, reason).to_dict())
        return {"scanner_role": SCANNER_ROLE, "stage_a_only": True, "scanner_cycle_timestamp": cycle_ts.isoformat(), "market_venue": self.venue, "data_transport_provider": self.transport_provider, "symbols_observed": len(records), "symbol_failures": failures, "attention_decisions": [], "ranking": [], "h2": [], "baseline": [], "records": records}

    def _rank_freshness(self, cycle_ts: datetime) -> str:
        fetched = self.rank_snapshot.fetched_at
        try:
            fetched = _utc(fetched)
        except (TypeError, ValueError):
            return "UNKNOWN"
        age = cycle_ts - fetched
        if age < timedelta(0) or age > self.rank_max_age:
            return "STALE"
        return "FRESH"

    @staticmethod
    def _eligible_market_shape(market: Mapping[str, Any]) -> bool:
        symbol = str(market.get("symbol", "")).upper()
        return bool(market.get("active", market.get("status", "TRADING") == "TRADING")) and bool(market.get("is_perpetual", market.get("contract_type") in {"PERPETUAL", "PERPETUAL_SWAP"})) and str(market.get("quote_asset", "")).upper() == "USDT" and str(market.get("category", "linear")).lower() == "linear" and not bool(market.get("inverse", False)) and not bool(market.get("spot", False)) and symbol


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime): return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _ticker_time(ticker: Mapping[str, Any] | None) -> datetime | None:
    if not ticker: return None
    for key in ("market_data_timestamp", "timestamp", "ts"):
        if ticker.get(key) is not None:
            try: return _timestamp(ticker[key])
            except ValueError: return None
    return None
