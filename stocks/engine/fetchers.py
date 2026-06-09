"""数据获取编排 — 并行获取行情和新闻"""

from typing import Optional
import asyncio
from stocks.domain.models import Instrument, Quote, NewsItem
from stocks.providers.registry import ProviderRegistry
from stocks.providers.rss_news import RSSNewsProvider


class DataFetcher:
    """数据获取器 — 负责并行获取行情数据，按市场分组返回"""

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry

    async def fetch_quotes(
        self,
        instruments: list[Instrument],
        preferred_provider: Optional[str] = None
    ) -> dict[str, list[Quote]]:
        """获取行情，按市场分组并行获取，返回 {"a": [Quote, ...], "us": [Quote, ...]}"""
        if not instruments:
            return {}

        # 按市场分组
        by_market: dict[str, list[Instrument]] = {}
        for inst in instruments:
            by_market.setdefault(inst.market, []).append(inst)

        # 为每个市场选择 Provider 并并行获取
        tasks = []
        market_keys = []
        for market, insts in by_market.items():
            provider = self._pick_provider(market, preferred_provider)
            if provider is None:
                continue
            tasks.append(self._fetch_with_provider(provider, insts))
            market_keys.append(market)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        quotes_by_market: dict[str, list[Quote]] = {}
        for market, result in zip(market_keys, results):
            if isinstance(result, Exception):
                # 获取失败时该市场返回空列表，不阻断其他市场
                quotes_by_market[market] = []
            else:
                quotes_by_market[market] = result

        return quotes_by_market

    async def fetch_news(
        self,
        keywords: list[str],
        sources: list[str],
        max_items: int = 20
    ) -> list[NewsItem]:
        """获取新闻 — 使用 RSS News Provider 获取财经新闻。"""
        # 使用 RSSNewsProvider 获取新闻
        provider = RSSNewsProvider()
        try:
            items = await provider.fetch(max_items=max_items)
            return items
        except Exception:
            return []

    def _pick_provider(
        self, market: str, preferred: Optional[str] = None
    ) -> Optional[object]:
        """为指定市场选择合适的 Provider"""
        if preferred:
            p = self.registry.get(preferred)
            if p and market in p.supported_markets:
                return p

        # 使用 markets.json 配置的默认 Provider
        return self.registry.get_default_for_market(market)

    async def _fetch_with_provider(self, provider, instruments: list[Instrument]) -> list[Quote]:
        """使用指定 Provider 批量获取行情"""
        try:
            return await provider.fetch_batch(instruments)
        except Exception:
            # 批量失败时降级为逐个获取
            quotes = []
            for inst in instruments:
                try:
                    q = await provider.fetch(inst)
                    if q is not None:
                        quotes.append(q)
                except Exception:
                    pass
            return quotes
