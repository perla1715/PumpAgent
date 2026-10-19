from datetime import datetime, timezone

import pytest

from pumpagent.live_data.adapters.market_cap_rank import (
    CoinGeckoMarketCapRankSource, RankCatalogError, RankCatalogIncompleteError,
    RankEntry, RankSnapshot,
)
from pumpagent.pilot.runtime_handoff import eligible_instruments

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def identity(asset_id, symbol): return {"id": asset_id, "symbol": symbol}
def market(asset_id, symbol, rank): return {"id": asset_id, "symbol": symbol, "market_cap_rank": rank}


class FixtureSource(CoinGeckoMarketCapRankSource):
    def __init__(self, identities, market_rows, **kwargs):
        super().__init__(per_page=2, max_pages=20, **kwargs)
        self.identities, self.market_rows, self.calls = identities, market_rows, []

    def _request(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == self.IDENTITY_ENDPOINT:
            return self.identities
        ids = set(params.get("ids", "").split(","))
        return [row for row in self.market_rows if row.get("id") in ids]


def test_target_sufficiency_does_not_require_global_catalogue_end_within_old_20_page_limit():
    # Represents a provider identity catalogue much larger than 20 old 250-row pages.
    identities = [identity(f"other-{i}", f"OTHER{i}") for i in range(5_001)] + [identity("hnt", "HNT")]
    source = FixtureSource(identities, [market("hnt", "HNT", 301)])
    snapshot = source.fetch_for_underlyings(("HNT",))
    assert snapshot.global_catalog_complete is False
    assert snapshot.target_universe_sufficient is True
    assert snapshot.acquisition_stop_condition == "TARGET_UNIVERSE_SUFFICIENT"
    assert snapshot.resolution_for("HNT").state == "RESOLVED_GT50"
    assert len(source.calls) == 2


def test_target_states_are_conservative_and_deterministic():
    identities = [identity("btc", "BTC"), identity("a", "DUP"), identity("b", "DUP"), identity("no-rank", "NORANK")]
    rows = [market("btc", "BTC", 1), market("no-rank", "NORANK", None)]
    first = FixtureSource(identities, rows).fetch_for_underlyings(("BTC", "DUP", "NORANK", "MISSING", "1000BONK"))
    second = FixtureSource(identities, rows).fetch_for_underlyings(("BTC", "DUP", "NORANK", "MISSING", "1000BONK"))
    assert first.snapshot_id == second.snapshot_id
    assert first.resolution_for("BTC").state == "RESOLVED_TOP50"
    assert first.resolution_for("DUP").state == "AMBIGUOUS"
    assert first.resolution_for("NORANK").state == "RANK_UNAVAILABLE"
    assert first.resolution_for("MISSING").state == "UNRESOLVED_IDENTITY"
    assert first.resolution_for("1000BONK").state == "UNRESOLVED_IDENTITY"
    assert dict(first.target_resolution_counts) == {"AMBIGUOUS": 1, "RANK_UNAVAILABLE": 1, "RESOLVED_TOP50": 1, "UNRESOLVED_IDENTITY": 2}


def test_source_acquisition_failure_is_not_a_target_unresolved_outcome():
    with pytest.raises(RankCatalogError):
        FixtureSource([identity("same", "ONE"), identity("same", "TWO")], []).fetch_for_underlyings(("ONE",))
    class Broken(FixtureSource):
        def _request(self, endpoint, params): raise OSError("transport unavailable")
    with pytest.raises(OSError):
        Broken([], []).fetch_for_underlyings(("ONE",))


def test_legacy_global_traversal_remains_bounded_and_never_claims_completion_without_proof():
    class Global(CoinGeckoMarketCapRankSource):
        def __init__(self): super().__init__(per_page=2, max_pages=2)
        def _fetch_market_page(self, page): return [market(f"id{page}a", f"A{page}", page), market(f"id{page}b", f"B{page}", page + 2)]
    with pytest.raises(RankCatalogIncompleteError):
        Global().fetch()


def test_existing_eligibility_only_admits_unique_gt50_rank():
    snapshot = RankSnapshot("fixture", NOW, (RankEntry("alpha", "ALPHA", 75), RankEntry("btc", "BTC", 1)), "fixture")
    instruments = tuple({"symbol": symbol, "status": "Trading", "category": "linear", "quoteCoin": "USDT"} for symbol in ("ALPHAUSDT", "BTCUSDT", "MISSINGUSDT", "1000BONKUSDT"))
    tickers = {row["symbol"]: {} for row in instruments}
    assert [item.symbol for item in eligible_instruments(instruments, tickers, snapshot)] == ["ALPHAUSDT"]
