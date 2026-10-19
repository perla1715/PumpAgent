"""Public, fail-closed CoinGecko rank evidence for Pilot universe gating."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib import parse, request


class RankCatalogError(RuntimeError):
    """The required rank catalogue cannot safely be used as a snapshot."""


class RankCatalogIncompleteError(RankCatalogError):
    """A bounded traversal ended before CoinGecko established completion."""


@dataclass(frozen=True)
class RankEntry:
    asset_id: str
    symbol: str
    rank: int | None


@dataclass(frozen=True)
class RankResolution:
    """Identity and rank outcome for one Bybit underlying, never a guess."""

    state: str
    entry: RankEntry | None
    method: str
    reason: str | None = None


@dataclass(frozen=True)
class RankSnapshot:
    source: str
    fetched_at: datetime
    entries: tuple[RankEntry, ...]
    snapshot_id: str
    page_count: int = 1
    coverage_complete: bool = True
    endpoint_pages: tuple[int, ...] = ()

    def resolution_for(self, underlying: str) -> RankResolution:
        """Resolve only a unique normalized CoinGecko ticker identity."""
        raw = underlying.upper().strip()
        key = _normalise_symbol(raw)
        if _MULTIPLIER.match(raw):
            return RankResolution(
                "UNRESOLVED_IDENTITY", None, "NO_AUTHORITATIVE_MULTIPLIER_MAPPING",
                "Bybit multiplier contract requires authoritative underlying mapping.",
            )
        matches = [entry for entry in self.entries if _normalise_symbol(entry.symbol) == key]
        if not matches:
            return RankResolution("UNRESOLVED_IDENTITY", None, "NORMALIZED_TICKER_EXACT", "No CoinGecko identity matched.")
        if len(matches) != 1:
            return RankResolution("AMBIGUOUS", None, "NORMALIZED_TICKER_EXACT", "Multiple CoinGecko asset identities matched.")
        entry = matches[0]
        if entry.rank is None:
            return RankResolution("RANK_UNAVAILABLE", entry, "NORMALIZED_TICKER_EXACT", "Resolved asset has no usable market-cap rank.")
        return RankResolution(
            "RESOLVED_TOP50" if entry.rank <= 50 else "RESOLVED_GT50",
            entry,
            "NORMALIZED_TICKER_EXACT",
        )

    def resolve(self, underlying: str) -> tuple[RankEntry | None, str]:
        """Compatibility surface used by accepted Pilot eligibility code."""
        resolution = self.resolution_for(underlying)
        if resolution.state.startswith("RESOLVED"):
            return resolution.entry, resolution.method
        if resolution.state == "AMBIGUOUS":
            return None, "AMBIGUOUS"
        return None, "UNRESOLVED"


class CoinGeckoMarketCapRankSource:
    SOURCE = "COINGECKO_MARKET_CAP_DESC"
    ENDPOINT = "https://api.coingecko.com/api/v3/coins/markets"

    def __init__(
        self,
        timeout_seconds: float = 20.0,
        per_page: int = 250,
        max_pages: int = 20,
    ) -> None:
        if per_page <= 0 or max_pages <= 0:
            raise ValueError("per_page and max_pages must be positive")
        self.timeout_seconds = timeout_seconds
        self.per_page = per_page
        self.max_pages = max_pages

    def fetch(self) -> RankSnapshot:
        """Fetch a bounded complete paginated catalogue or fail closed."""
        entries: list[RankEntry] = []
        seen_ids: set[str] = set()
        pages: list[int] = []
        for page in range(1, self.max_pages + 1):
            payload = self._fetch_page(page)
            if not isinstance(payload, list):
                raise RankCatalogError("CoinGecko market catalogue response is not a list")
            pages.append(page)
            for item in payload:
                if not isinstance(item, dict) or not item.get("id") or not item.get("symbol"):
                    raise RankCatalogError("CoinGecko catalogue contains malformed asset identity")
                asset_id = str(item["id"])
                if asset_id in seen_ids:
                    raise RankCatalogError("CoinGecko catalogue contains duplicate asset identity")
                seen_ids.add(asset_id)
                rank = item.get("market_cap_rank")
                if rank is not None and (not isinstance(rank, int) or rank <= 0):
                    raise RankCatalogError("CoinGecko catalogue contains invalid market-cap rank")
                entries.append(RankEntry(asset_id, str(item["symbol"]).upper(), rank))
            if len(payload) < self.per_page:
                return self._snapshot(tuple(entries), tuple(pages))
        raise RankCatalogIncompleteError(
            f"CoinGecko catalogue did not establish completion within {self.max_pages} pages"
        )

    def _fetch_page(self, page: int) -> object:
        params = {
            "vs_currency": "usd", "order": "market_cap_desc", "per_page": str(self.per_page),
            "page": str(page), "sparkline": "false",
        }
        url = self.ENDPOINT + "?" + parse.urlencode(params)
        with request.urlopen(request.Request(url, headers={"Accept": "application/json"}), timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _snapshot(self, entries: tuple[RankEntry, ...], pages: tuple[int, ...]) -> RankSnapshot:
        material = {
            "source": self.SOURCE,
            "per_page": self.per_page,
            "pages": pages,
            "entries": [(entry.asset_id, entry.symbol, entry.rank) for entry in entries],
        }
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        return RankSnapshot(self.SOURCE, datetime.now(timezone.utc), entries, digest, len(pages), True, pages)


_MULTIPLIER = re.compile(r"^\d+(?:[A-Z].*)$")


def _normalise_symbol(value: str) -> str:
    return value.upper().replace("-", "")
