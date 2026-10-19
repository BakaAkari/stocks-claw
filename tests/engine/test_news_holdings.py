"""持仓定向新闻：持仓纳入、每标的配额、旧闻过滤。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from stocks.domain.models import Instrument, NewsItem
from stocks.engine.news_sources import NewsAggregator, WatchlistGoogleNewsProvider


def _item(url, *, days_ago=0.0, scope="general"):
    return NewsItem(
        title=url, url=url, source_name="s", source_type="rss",
        published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        summary=None, scope=scope,
    )


class _Provider:
    name = "fake"

    def __init__(self, items):
        self.items = items

    async def fetch(self, max_items=10):
        return list(self.items)


async def test_aggregator_drops_news_older_than_max_age():
    naive_old = NewsItem(title="n", url="naive", source_name="s", source_type="rss",
                         published_at=datetime.now() - timedelta(days=30), summary=None)
    undated = NewsItem(title="u", url="undated", source_name="s", source_type="rss",
                       published_at=None, summary=None)
    aggregator = NewsAggregator(
        [_Provider([_item("fresh", days_ago=1), _item("old", days_ago=10), naive_old, undated])],
        max_age_days=7,
    )
    urls = {item.url for item in await aggregator.fetch(max_items=10)}
    assert urls == {"fresh", "undated"}


async def test_per_symbol_quota_is_not_diluted_and_labels_holdings():
    instruments = [Instrument(code=f"C{i}", name=f"N{i}", market="a") for i in range(10)]
    instruments[0] = Instrument(code="512480", name="半导体ETF", market="a", pool="holding")
    provider = WatchlistGoogleNewsProvider(lambda: instruments, per_symbol=3)
    seen = {}

    async def fake_fetch(self, max_items=10):
        seen[self.source_name] = max_items
        code = self.source_name.rsplit(":", 1)[-1]
        return [_item(f"{code} 新闻 {k} - 来源", scope="holding") for k in range(max_items)]

    with patch("stocks.engine.news_sources.RSSNewsProvider.fetch", fake_fetch):
        items = await provider.fetch(max_items=20)  # 聚合器单源上限不再摊薄到 2 条
    assert len(items) == 30
    assert set(seen.values()) == {3 * WatchlistGoogleNewsProvider.CANDIDATE_MULTIPLIER}
    assert "Google News 持仓:512480" in seen
    assert "Google News 关注:C1" in seen


def test_news_instruments_include_holdings_outside_watchlist():
    from stocks.engine import StocksEngine

    engine = StocksEngine.__new__(StocksEngine)
    engine._watchlist = [
        Instrument(code="510300", name="沪深300ETF", market="a"),
        Instrument(code="QQQ", name="QQQ", market="us"),
    ]

    class _Pos:
        def __init__(self, key, method="market_quote"):
            self.instrument_key = key
            self.valuation_input = type("V", (), {"method": method})()
            self.holding = {"quantity": 1}

    positions = [_Pos("a:510300"), _Pos("a:512480"), _Pos("bank:wmp", method="manual")]

    def fake_instrument(position):
        market, code = position.instrument_key.split(":")
        return Instrument(code=code, name=code, market=market, pool="holding")

    engine._current_asset_positions = lambda: positions
    engine._instrument_from_position = fake_instrument
    result = {(i.code, i.pool) for i in engine._news_instruments()}
    assert result == {("510300", "holding"), ("QQQ", None), ("512480", "holding")}


def test_headline_relevance_filters_other_funds_and_vague_matches():
    from stocks.engine.news_sources import headline_source, is_relevant_headline

    semi = Instrument(code="512480", name="半导体ETF", market="a")
    assert is_relevant_headline("半导体ETF（512480）资金净流入 - 新浪财经", semi)
    assert not is_relevant_headline("半导体ETF博时(159582)近10日吸金 - Jiemian.com", semi)
    assert is_relevant_headline("半导体ETF放量上涨 - 证券时报", semi)
    assert not is_relevant_headline("芯片股集体走强 - 证券时报", semi)

    nem = Instrument(code="NEM", name="Newmont Corporation", market="us")
    assert is_relevant_headline("Barclays Adjusts Price Target on Newmont - marketscreener.com", nem)
    assert is_relevant_headline("NEM shares rise - Reuters", nem)
    assert not is_relevant_headline("Gold miners rally - Reuters", nem)
    assert not is_relevant_headline("NEMO launches product - PR", nem)

    ita = Instrument(code="ITA", name="iShares U.S. Aerospace & Defense ETF", market="us")
    assert not is_relevant_headline("Aerospace stocks climb on defense budget - CNBC", ita)
    assert is_relevant_headline("ITA ETF sees inflows - ETF.com", ita)

    btc = Instrument(code="BTCUSDT", name="比特币", market="crypto")
    assert is_relevant_headline("Bitcoin extends rebound - CoinDesk", btc)
    assert headline_source("标题 - 子标题 - Moomoo") == "Moomoo"


async def test_targeted_provider_filters_and_blocks_sources():
    inst = Instrument(code="NVDA", name="NVIDIA Corporation", market="us", pool="holding")
    provider = WatchlistGoogleNewsProvider(lambda: [inst], per_symbol=2, blocked_sources=["Moomoo"])
    titles = ["NVDA hits record - Reuters", "$NVDA$ 感恩 - Moomoo", "AI stocks mixed - CNBC",
              "Nvidia unveils chip - Bloomberg", "NVIDIA earnings preview - WSJ"]

    async def fake_fetch(self, max_items=10):
        assert max_items == 10  # per_symbol × 候选倍数
        return [_item(t, scope="holding") for t in titles]

    with patch("stocks.engine.news_sources.RSSNewsProvider.fetch", fake_fetch):
        items = await provider.fetch()
    assert {i.title for i in items} == {"NVDA hits record - Reuters", "Nvidia unveils chip - Bloomberg"}
