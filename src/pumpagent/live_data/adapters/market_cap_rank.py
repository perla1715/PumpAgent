"""Public, fail-closed CoinGecko rank evidence for Pilot universe gating."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib import parse, request


class RankCatalogError(RuntimeError):
    """Required CoinGecko evidence was malformed, inconsistent, or unavailable."""


class RankCatalogIncompleteError(RankCatalogError):
    """Legacy global catalogue traversal did not establish completion."""


@dataclass(frozen=True)
class RankEntry:
    asset_id: str
    symbol: str
    rank: int | None


@dataclass(frozen=True)
class RankResolution:
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
    global_catalog_complete: bool = True
    target_universe_sufficient: bool = False
    acquisition_stop_condition: str = "GLOBAL_CATALOG_COMPLETE"
    target_resolution_counts: tuple[tuple[str, int], ...] = ()
    endpoints: tuple[str, ...] = ()
    target_rank_batches: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = ()
    _target_resolutions: tuple[tuple[str, RankResolution], ...] = ()

    def resolution_for(self, underlying: str) -> RankResolution:
        raw = underlying.upper().strip()
        key = _normalise_symbol(raw)
        saved = dict(self._target_resolutions).get(key)
        if saved is not None:
            return saved
        if _MULTIPLIER.match(raw):
            return RankResolution("UNRESOLVED_IDENTITY", None, "NO_AUTHORITATIVE_MULTIPLIER_MAPPING", "Bybit multiplier contract requires authoritative underlying mapping.")
        matches = [entry for entry in self.entries if _normalise_symbol(entry.symbol) == key]
        return _resolution_from_matches(matches)

    def resolve(self, underlying: str) -> tuple[RankEntry | None, str]:
        resolution = self.resolution_for(underlying)
        if resolution.state.startswith("RESOLVED"):
            return resolution.entry, resolution.method
        if resolution.state == "AMBIGUOUS":
            return None, "AMBIGUOUS"
        return None, "UNRESOLVED"


class CoinGeckoMarketCapRankSource:
    """Produce rank evidence sufficient for a finite Bybit identity target set.

    ``/coins/list`` establishes the complete identity catalogue.  ``/coins/markets``
    is then queried only for uniquely identified target asset IDs.  This avoids
    pretending that an arbitrarily truncated global rank catalogue is complete.
    """
    SOURCE = "COINGECKO_MARKET_CAP_DESC"
    IDENTITY_ENDPOINT = "https://api.coingecko.com/api/v3/coins/list"
    MARKET_ENDPOINT = "https://api.coingecko.com/api/v3/coins/markets"

    def __init__(self, timeout_seconds: float = 20.0, per_page: int = 250, max_pages: int = 20) -> None:
        if per_page <= 0 or max_pages <= 0:
            raise ValueError("per_page and max_pages must be positive")
        self.timeout_seconds, self.per_page, self.max_pages = timeout_seconds, per_page, max_pages

    def fetch_for_underlyings(self, underlyings: tuple[str, ...] | list[str] | set[str]) -> RankSnapshot:
        """Acquire complete identity and bounded target-specific rank evidence."""
        targets = tuple(sorted({_normalise_symbol(value) for value in underlyings if str(value).strip()}))
        identities = self._fetch_identity_catalogue()
        by_symbol: dict[str, list[tuple[str, str]]] = {}
        seen_ids: set[str] = set()
        for item in identities:
            if not isinstance(item, dict) or not item.get("id") or not item.get("symbol"):
                raise RankCatalogError("CoinGecko identity catalogue contains malformed asset identity")
            asset_id, symbol = str(item["id"]), str(item["symbol"]).upper()
            if asset_id in seen_ids:
                raise RankCatalogError("CoinGecko identity catalogue contains duplicate asset identity")
            seen_ids.add(asset_id)
            by_symbol.setdefault(_normalise_symbol(symbol), []).append((asset_id, symbol))

        resolutions: dict[str, RankResolution] = {}
        unique: dict[str, tuple[str, str]] = {}
        for key in targets:
            if _MULTIPLIER.match(key):
                resolutions[key] = RankResolution("UNRESOLVED_IDENTITY", None, "NO_AUTHORITATIVE_MULTIPLIER_MAPPING", "Bybit multiplier contract requires authoritative underlying mapping.")
                continue
            matches = by_symbol.get(key, [])
            if not matches:
                resolutions[key] = RankResolution("UNRESOLVED_IDENTITY", None, "IDENTITY_CATALOG_EXACT", "No CoinGecko identity matched.")
            elif len(matches) > 1:
                resolutions[key] = RankResolution("AMBIGUOUS", None, "IDENTITY_CATALOG_EXACT", "Multiple CoinGecko asset identities matched.")
            else:
                unique[key] = matches[0]

        market_rows, batch_provenance = self._fetch_target_market_rows(tuple(asset_id for asset_id, _ in unique.values()))
        ranks: dict[str, int | None] = {}
        for item in market_rows:
            if not isinstance(item, dict) or not item.get("id"):
                raise RankCatalogError("CoinGecko target rank response contains malformed asset identity")
            asset_id = str(item["id"])
            if asset_id in ranks:
                raise RankCatalogError("CoinGecko target rank response contains duplicate asset identity")
            rank = item.get("market_cap_rank")
            if rank is not None and (not isinstance(rank, int) or rank <= 0):
                raise RankCatalogError("CoinGecko target rank response contains invalid market-cap rank")
            ranks[asset_id] = rank

        entries: list[RankEntry] = []
        for key, (asset_id, symbol) in unique.items():
            entry = RankEntry(asset_id, symbol, ranks.get(asset_id))
            entries.append(entry)
            resolutions[key] = _resolution_from_matches([entry], method="IDENTITY_CATALOG_EXACT+TARGET_MARKET_RANK")
        counts = _counts(resolutions.values())
        return self._snapshot(
            tuple(sorted(entries, key=lambda item: item.asset_id)),
            endpoint_pages=(), global_complete=False, target_sufficient=True,
            stop="TARGET_UNIVERSE_SUFFICIENT", resolutions=resolutions, counts=counts,
            endpoints=tuple(endpoint for endpoint in (self.IDENTITY_ENDPOINT, self.MARKET_ENDPOINT) if endpoint == self.IDENTITY_ENDPOINT or unique),
            batches=batch_provenance,
        )

    def fetch(self) -> RankSnapshot:
        """Compatibility global traversal, retained for non-target-aware callers."""
        entries: list[RankEntry] = []
        seen_ids: set[str] = set()
        pages: list[int] = []
        for page in range(1, self.max_pages + 1):
            payload = self._fetch_market_page(page)
            if not isinstance(payload, list):
                raise RankCatalogError("CoinGecko market catalogue response is not a list")
            pages.append(page)
            for item in payload:
                entry = _entry_from_market_item(item)
                if entry.asset_id in seen_ids:
                    raise RankCatalogError("CoinGecko catalogue contains duplicate asset identity")
                seen_ids.add(entry.asset_id); entries.append(entry)
            if len(payload) < self.per_page:
                return self._snapshot(tuple(entries), tuple(pages), True, False, "GLOBAL_CATALOG_COMPLETE", {}, (), (self.MARKET_ENDPOINT,), ())
        raise RankCatalogIncompleteError(f"CoinGecko catalogue did not establish completion within {self.max_pages} pages")

    def _fetch_identity_catalogue(self) -> object:
        return self._request(self.IDENTITY_ENDPOINT, {})

    def _fetch_target_market_rows(self, asset_ids: tuple[str, ...]) -> tuple[list[object], tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...]]:
        rows: list[object] = []
        batches = []
        for start in range(0, len(asset_ids), self.per_page):
            batch = tuple(asset_ids[start:start + self.per_page])
            if not batch:
                continue
            payload = self._request(self.MARKET_ENDPOINT, {"vs_currency": "usd", "ids": ",".join(batch), "per_page": str(len(batch)), "sparkline": "false"})
            if not isinstance(payload, list):
                raise RankCatalogError("CoinGecko target rank response is not a list")
            returned: list[str] = []
            for item in payload:
                if not isinstance(item, dict) or not item.get("id"):
                    raise RankCatalogError("CoinGecko target rank response contains malformed asset identity")
                returned.append(str(item["id"]))
            if len(returned) != len(set(returned)):
                raise RankCatalogError("CoinGecko target rank response contains duplicate asset identity")
            requested_set, returned_set = set(batch), set(returned)
            missing, unexpected = tuple(sorted(requested_set - returned_set)), tuple(sorted(returned_set - requested_set))
            batches.append((tuple(sorted(batch)), tuple(sorted(returned)), missing, unexpected))
            if missing:
                raise RankCatalogError("CoinGecko target rank response omitted requested asset identity")
            if unexpected:
                raise RankCatalogError("CoinGecko target rank response included unexpected asset identity")
            rows.extend(payload)
        return rows, tuple(batches)

    def _fetch_market_page(self, page: int) -> object:
        return self._request(self.MARKET_ENDPOINT, {"vs_currency": "usd", "order": "market_cap_desc", "per_page": str(self.per_page), "page": str(page), "sparkline": "false"})

    def _request(self, endpoint: str, params: dict[str, str]) -> object:
        url = endpoint + ("?" + parse.urlencode(params) if params else "")
        with request.urlopen(request.Request(url, headers={"Accept": "application/json"}), timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def _snapshot(self, entries, endpoint_pages, global_complete, target_sufficient, stop, resolutions, counts, endpoints=(), batches=()) -> RankSnapshot:
        material = {"source": self.SOURCE, "entries": [(e.asset_id, e.symbol, e.rank) for e in entries], "endpoint_pages": endpoint_pages, "global_catalog_complete": global_complete, "target_universe_sufficient": target_sufficient, "stop": stop, "resolutions": [(k, v.state, v.entry.asset_id if v.entry else None, v.method) for k, v in sorted(resolutions.items())], "endpoints": endpoints, "target_rank_batches": batches}
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
        return RankSnapshot(self.SOURCE, datetime.now(timezone.utc), tuple(entries), digest, len(endpoint_pages), global_complete, tuple(endpoint_pages), global_complete, target_sufficient, stop, tuple(sorted(counts.items())), tuple(endpoints), tuple(batches), tuple(sorted(resolutions.items())))


def _entry_from_market_item(item: object) -> RankEntry:
    if not isinstance(item, dict) or not item.get("id") or not item.get("symbol"):
        raise RankCatalogError("CoinGecko catalogue contains malformed asset identity")
    rank = item.get("market_cap_rank")
    if rank is not None and (not isinstance(rank, int) or rank <= 0):
        raise RankCatalogError("CoinGecko catalogue contains invalid market-cap rank")
    return RankEntry(str(item["id"]), str(item["symbol"]).upper(), rank)


def _resolution_from_matches(matches: list[RankEntry], method: str = "NORMALIZED_TICKER_EXACT") -> RankResolution:
    if not matches:
        return RankResolution("UNRESOLVED_IDENTITY", None, method, "No CoinGecko identity matched.")
    if len(matches) > 1:
        return RankResolution("AMBIGUOUS", None, method, "Multiple CoinGecko asset identities matched.")
    entry = matches[0]
    if entry.rank is None:
        return RankResolution("RANK_UNAVAILABLE", entry, method, "Resolved asset has no usable market-cap rank.")
    return RankResolution("RESOLVED_TOP50" if entry.rank <= 50 else "RESOLVED_GT50", entry, method)


def _counts(resolutions) -> dict[str, int]:
    result: dict[str, int] = {}
    for resolution in resolutions:
        result[resolution.state] = result.get(resolution.state, 0) + 1
    return result


_MULTIPLIER = re.compile(r"^\d+(?:[A-Z].*)$")


def _normalise_symbol(value: str) -> str:
    return value.upper().replace("-", "")
