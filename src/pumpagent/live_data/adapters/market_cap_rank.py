"""Public market-cap rank transport used only for fail-closed universe gating."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from urllib import parse, request

@dataclass(frozen=True)
class RankEntry:
    asset_id: str
    symbol: str
    rank: int

@dataclass(frozen=True)
class RankSnapshot:
    source: str
    fetched_at: datetime
    entries: tuple[RankEntry, ...]
    snapshot_id: str
    def resolve(self, underlying: str) -> tuple[RankEntry | None, str]:
        key = underlying.upper().replace("-", "")
        matches = [e for e in self.entries if e.symbol.upper().replace("-", "") == key]
        if len(matches) == 1: return matches[0], "SYMBOL_EXACT"
        if len(matches) > 1: return None, "AMBIGUOUS"
        return None, "UNRESOLVED"

class CoinGeckoMarketCapRankSource:
    SOURCE = "COINGECKO_MARKET_CAP_DESC"
    def __init__(self, timeout_seconds: float = 20.0, per_page: int = 250) -> None:
        self.timeout_seconds, self.per_page = timeout_seconds, per_page
    def fetch(self) -> RankSnapshot:
        params = {"vs_currency":"usd", "order":"market_cap_desc", "per_page":str(self.per_page), "page":"1", "sparkline":"false"}
        url = "https://api.coingecko.com/api/v3/coins/markets?" + parse.urlencode(params)
        with request.urlopen(request.Request(url, headers={"Accept":"application/json"}), timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        entries = tuple(RankEntry(str(i.get("id")), str(i.get("symbol")).upper(), n) for n, i in enumerate(payload if isinstance(payload, list) else [], 1) if isinstance(i, dict) and i.get("id") and i.get("symbol"))
        fetched = datetime.now(timezone.utc)
        raw = self.SOURCE + fetched.isoformat() + ",".join(f"{e.asset_id}:{e.rank}" for e in entries)
        return RankSnapshot(self.SOURCE, fetched, entries, hashlib.sha256(raw.encode()).hexdigest()[:16])
