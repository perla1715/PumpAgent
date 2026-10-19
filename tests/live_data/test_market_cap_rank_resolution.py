from datetime import datetime, timezone

import pytest

from pumpagent.live_data.adapters.market_cap_rank import (
    CoinGeckoMarketCapRankSource, RankCatalogError, RankCatalogIncompleteError,
    RankEntry, RankSnapshot,
)
from pumpagent.pilot.runtime_handoff import eligible_instruments

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
DEFAULT_PROVIDER_PAGE_SIZE = 100

def identity(asset_id, symbol): return {"id": asset_id, "symbol": symbol}
def market(asset_id, symbol, rank): return {"id": asset_id, "symbol": symbol, "market_cap_rank": rank}

class FixtureSource(CoinGeckoMarketCapRankSource):
    def __init__(self, identities, market_rows, **kwargs):
        super().__init__(per_page=250, max_pages=20, **kwargs)
        self.identities, self.market_rows, self.calls = identities, market_rows, []

    def _request(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == self.IDENTITY_ENDPOINT:
            return self.identities
        ids = params.get("ids", "").split(",")
        limit = int(params.get("per_page", DEFAULT_PROVIDER_PAGE_SIZE))
        return [row for row in self.market_rows if row.get("id") in ids][:limit]


def targets(count): return tuple(f"TOKEN{i}" for i in range(count))
def fixture_for(count):
    identities = [identity(f"asset-{i}", f"TOKEN{i}") for i in range(count)]
    rows = [market(f"asset-{i}", f"TOKEN{i}", i + 51) for i in range(count)]
    return identities, rows


def test_target_sufficiency_does_not_require_global_catalogue_end_within_old_20_page_limit():
    identities = [identity(f"other-{i}", f"OTHER{i}") for i in range(5_001)] + [identity("hnt", "HNT")]
    source = FixtureSource(identities, [market("hnt", "HNT", 301)])
    snapshot = source.fetch_for_underlyings(("HNT",))
    assert snapshot.global_catalog_complete is False
    assert snapshot.target_universe_sufficient is True
    assert snapshot.acquisition_stop_condition == "TARGET_UNIVERSE_SUFFICIENT"
    assert snapshot.resolution_for("HNT").state == "RESOLVED_GT50"


def test_gt100_target_request_explicitly_sets_per_page_and_accounts_for_every_id():
    identities, rows = fixture_for(150); source = FixtureSource(identities, rows)
    snapshot = source.fetch_for_underlyings(targets(150))
    market_calls = [params for endpoint, params in source.calls if endpoint == source.MARKET_ENDPOINT]
    assert len(market_calls) == 1
    assert market_calls[0]["per_page"] == "150"
    batch = snapshot.target_rank_batches[0]
    assert len(batch[0]) == 150 and len(batch[1]) == 150 and not batch[2] and not batch[3]
    assert snapshot.target_universe_sufficient is True


def test_gt250_targets_use_deterministic_explicitly_sized_batches_and_digest():
    identities, rows = fixture_for(260)
    first = FixtureSource(identities, rows); second = FixtureSource(identities, rows)
    one, two = first.fetch_for_underlyings(targets(260)), second.fetch_for_underlyings(targets(260))
    calls = [params for endpoint, params in first.calls if endpoint == first.MARKET_ENDPOINT]
    assert [call["per_page"] for call in calls] == ["250", "10"]
    assert [len(batch[0]) for batch in one.target_rank_batches] == [250, 10]
    assert all(not batch[2] and not batch[3] for batch in one.target_rank_batches)
    assert one.snapshot_id == two.snapshot_id and one.target_universe_sufficient is True


def test_missing_requested_row_is_source_failure_not_rank_unavailable():
    identities = [identity("a", "A"), identity("b", "B"), identity("c", "C")]
    with pytest.raises(RankCatalogError, match="omitted requested"):
        FixtureSource(identities, [market("a", "A", 75), market("c", "C", 76)]).fetch_for_underlyings(("A", "B", "C"))


def test_explicit_null_rank_is_known_rank_unavailable_and_target_sufficient():
    source = FixtureSource([identity("a", "A"), identity("b", "B")], [market("a", "A", 75), market("b", "B", None)])
    snapshot = source.fetch_for_underlyings(("A", "B"))
    assert snapshot.resolution_for("A").state == "RESOLVED_GT50"
    assert snapshot.resolution_for("B").state == "RANK_UNAVAILABLE"
    assert snapshot.target_universe_sufficient is True


def test_unexpected_and_duplicate_response_ids_fail_safe():
    class Extra(FixtureSource):
        def _request(self, endpoint, params):
            data = super()._request(endpoint, params)
            return data if endpoint == self.IDENTITY_ENDPOINT else data + [market("c", "C", 90)]
    with pytest.raises(RankCatalogError, match="unexpected"):
        Extra([identity("a", "A"), identity("b", "B"), identity("c", "C")], [market("a", "A", 75), market("b", "B", 76)]).fetch_for_underlyings(("A", "B"))
    class Duplicate(FixtureSource):
        def _request(self, endpoint, params):
            data = super()._request(endpoint, params)
            return data if endpoint == self.IDENTITY_ENDPOINT else data + [market("a", "A", 75)]
    with pytest.raises(RankCatalogError, match="duplicate"):
        Duplicate([identity("a", "A"), identity("b", "B")], [market("a", "A", 75), market("b", "B", 76)]).fetch_for_underlyings(("A", "B"))


def test_target_states_are_conservative_and_deterministic():
    identities = [identity("btc", "BTC"), identity("a", "DUP"), identity("b", "DUP"), identity("no-rank", "NORANK")]
    rows = [market("btc", "BTC", 1), market("no-rank", "NORANK", None)]
    snapshot = FixtureSource(identities, rows).fetch_for_underlyings(("BTC", "DUP", "NORANK", "MISSING", "1000BONK"))
    assert snapshot.resolution_for("BTC").state == "RESOLVED_TOP50"
    assert snapshot.resolution_for("DUP").state == "AMBIGUOUS"
    assert snapshot.resolution_for("NORANK").state == "RANK_UNAVAILABLE"
    assert snapshot.resolution_for("MISSING").state == "UNRESOLVED_IDENTITY"
    assert snapshot.resolution_for("1000BONK").state == "UNRESOLVED_IDENTITY"


def test_source_acquisition_failure_is_not_a_target_unresolved_outcome():
    with pytest.raises(RankCatalogError):
        FixtureSource([identity("same", "ONE"), identity("same", "TWO")], []).fetch_for_underlyings(("ONE",))


def test_legacy_global_traversal_remains_bounded_and_never_claims_completion_without_proof():
    class Global(CoinGeckoMarketCapRankSource):
        def __init__(self): super().__init__(per_page=2, max_pages=2)
        def _fetch_market_page(self, page): return [market(f"id{page}a", f"A{page}", page), market(f"id{page}b", f"B{page}", page + 2)]
    with pytest.raises(RankCatalogIncompleteError): Global().fetch()


def test_existing_eligibility_only_admits_unique_gt50_rank():
    snapshot = RankSnapshot("fixture", NOW, (RankEntry("alpha", "ALPHA", 75), RankEntry("btc", "BTC", 1)), "fixture")
    instruments = tuple({"symbol": symbol, "status": "Trading", "category": "linear", "quoteCoin": "USDT"} for symbol in ("ALPHAUSDT", "BTCUSDT", "MISSINGUSDT", "1000BONKUSDT"))
    tickers = {row["symbol"]: {} for row in instruments}
    assert [item.symbol for item in eligible_instruments(instruments, tickers, snapshot)] == ["ALPHAUSDT"]
