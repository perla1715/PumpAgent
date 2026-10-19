from datetime import datetime, timezone

import pytest

from pumpagent.live_data.adapters.market_cap_rank import (
    CoinGeckoMarketCapRankSource,
    RankCatalogError,
    RankCatalogIncompleteError,
    RankEntry,
    RankSnapshot,
)
from pumpagent.pilot.runtime_handoff import eligible_instruments


NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


class FixtureSource(CoinGeckoMarketCapRankSource):
    def __init__(self, pages, **kwargs):
        super().__init__(per_page=2, max_pages=3, **kwargs)
        self.pages = pages
        self.requested = []

    def _fetch_page(self, page):
        self.requested.append(page)
        return self.pages[page - 1]


def asset(asset_id, symbol, rank):
    return {"id": asset_id, "symbol": symbol, "market_cap_rank": rank}


def test_paginated_catalogue_resolves_later_page_asset_and_is_deterministic():
    pages = [[asset("one", "ONE", 1), asset("two", "TWO", 2)], [asset("hnt", "HNT", 301)]]
    first = FixtureSource(pages).fetch()
    second = FixtureSource(pages).fetch()
    resolution = first.resolution_for("HNT")
    assert first.endpoint_pages == (1, 2)
    assert first.coverage_complete is True
    assert resolution.state == "RESOLVED_GT50"
    assert resolution.entry == RankEntry("hnt", "HNT", 301)
    assert first.snapshot_id == second.snapshot_id


def test_top50_unresolved_ambiguous_unavailable_and_multiplier_remain_fail_closed():
    snapshot = RankSnapshot(
        "fixture", NOW,
        (RankEntry("btc", "BTC", 1), RankEntry("a", "DUP", 100), RankEntry("b", "DUP", 200), RankEntry("no-rank", "NORANK", None)),
        "fixture",
    )
    assert snapshot.resolution_for("BTC").state == "RESOLVED_TOP50"
    assert snapshot.resolution_for("DUP").state == "AMBIGUOUS"
    assert snapshot.resolution_for("NORANK").state == "RANK_UNAVAILABLE"
    assert snapshot.resolution_for("MISSING").state == "UNRESOLVED_IDENTITY"
    assert snapshot.resolution_for("1000BONK").state == "UNRESOLVED_IDENTITY"
    assert snapshot.resolve("DUP") == (None, "AMBIGUOUS")


def test_partial_malformed_and_duplicate_catalogues_fail_safe():
    with pytest.raises(RankCatalogIncompleteError):
        FixtureSource([[asset("one", "ONE", 1), asset("two", "TWO", 2)], [asset("three", "THREE", 3), asset("four", "FOUR", 4)], [asset("five", "FIVE", 5), asset("six", "SIX", 6)]]).fetch()
    with pytest.raises(RankCatalogError):
        FixtureSource([[asset("same", "ONE", 1), asset("same", "TWO", 2)]]).fetch()
    with pytest.raises(RankCatalogError):
        FixtureSource([[{"id": "missing-symbol", "market_cap_rank": 75}]]).fetch()


def test_existing_eligibility_only_admits_unique_gt50_rank():
    snapshot = RankSnapshot("fixture", NOW, (RankEntry("alpha", "ALPHA", 75), RankEntry("btc", "BTC", 1)), "fixture")
    instruments = (
        {"symbol": "ALPHAUSDT", "status": "Trading", "category": "linear", "quoteCoin": "USDT"},
        {"symbol": "BTCUSDT", "status": "Trading", "category": "linear", "quoteCoin": "USDT"},
        {"symbol": "MISSINGUSDT", "status": "Trading", "category": "linear", "quoteCoin": "USDT"},
        {"symbol": "1000BONKUSDT", "status": "Trading", "category": "linear", "quoteCoin": "USDT"},
    )
    tickers = {row["symbol"]: {} for row in instruments}
    assert [item.symbol for item in eligible_instruments(instruments, tickers, snapshot)] == ["ALPHAUSDT"]
