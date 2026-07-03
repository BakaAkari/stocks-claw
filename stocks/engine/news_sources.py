"""多源新闻聚合器 — 并行采集、去重、排序

将多个新闻 Provider 聚合为统一的新闻流，支持：
- 并行获取所有源
- URL 去重（同一新闻仅保留最早来源）
- 时间排序（最新在前）
- 截断输出（控制 LLM 输入长度）

使用方式：
    aggregator = NewsAggregator([
        RSSNewsProvider("https://www.chinanews.com.cn/rss/finance.xml"),
        RSSNewsProvider("https://finance.yahoo.com/news/rssindex"),
    ])
    news = await aggregator.fetch(max_items=20)
"""

from __future__ import annotations

import asyncio
import urllib.parse
from dataclasses import replace
from typing import Optional, Protocol

from stocks.domain.models import NewsItem
from stocks.logging_utils import get_logger
from stocks.providers.rss_news import RSSNewsProvider

logger = get_logger("news_sources")


class NewsProvider(Protocol):
    """新闻提供者接口"""

    async def fetch(self, max_items: int = 10) -> list[NewsItem]:
        ...


class NewsAggregator:
    """多源新闻聚合器

    Args:
        providers: 新闻提供者列表，按优先级排序
        max_source_items: 每个源最多获取条数（防止单源垄断）
    """

    def __init__(self, providers: list[NewsProvider], max_source_items: int = 20):
        self._providers = providers
        self._max_source_items = max_source_items
        self.last_errors: dict[str, str] = {}

    async def fetch(
        self,
        max_items: int = 20,
        sources: Optional[list[str]] = None,
    ) -> list[NewsItem]:
        """获取聚合后的新闻列表

        流程：
        1. 并行调用所有 provider
        2. URL 去重（保留第一个来源的）
        3. 按 published_at 降序排序
        4. 截断至 max_items

        Args:
            sources: 指定保留的新闻源名称/类型列表，None 则不筛选。
        """
        if not self._providers:
            self.last_errors = {}
            return []

        self.last_errors = {}
        tasks = [p.fetch(max_items=self._max_source_items) for p in self._providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_news: list[NewsItem] = []
        for i, result in enumerate(results):
            provider = self._providers[i]
            name = getattr(provider, "name", type(provider).__name__)
            if isinstance(result, Exception):
                logger.warning(f"News provider {i} failed: {result}")
                error_key = str(name)
                if error_key in self.last_errors:
                    error_key = f"{error_key}#{i}"
                self.last_errors[error_key] = f"{type(result).__name__}: {result}"
                continue
            all_news.extend(result or [])
            provider_errors = getattr(provider, "last_errors", None)
            if isinstance(provider_errors, dict):
                for scope, message in provider_errors.items():
                    self.last_errors[f"{name}:{scope}"] = str(message)

        # holding 来源优先参与去重，同 URL 时保留持仓定向版本。
        all_news.sort(key=lambda item: item.scope == "holding", reverse=True)
        seen_urls: set[str] = set()
        unique_news: list[NewsItem] = []
        for item in all_news:
            if not item.url:
                continue
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                unique_news.append(item)

        # 按 sources 过滤（匹配 source_name 或 source_type）
        if sources:
            source_set = set(sources)
            unique_news = [
                item for item in unique_news
                if item.source_name in source_set or item.source_type in source_set
            ]

        # 排序：按 published_at 降序（最新在前），无时间放最后
        def _sort_key(item: NewsItem) -> float:
            if item.published_at is None:
                return 0.0  # 1970-01-01 的时间戳，确保放最后
            return item.published_at.timestamp()

        unique_news.sort(key=_sort_key, reverse=True)

        if max_items <= 0:
            return []
        holding = [item for item in unique_news if item.scope == "holding"]
        general = [item for item in unique_news if item.scope != "holding"]
        holding.sort(
            key=lambda item: (
                item.source_type == "filing",
                _sort_key(item),
            ),
            reverse=True,
        )
        if holding and general:
            holding_limit = max(1, int(max_items * 0.6))
            selected = holding[:holding_limit] + general[: max_items - holding_limit]
            if len(selected) < max_items:
                used_urls = {item.url for item in selected}
                selected.extend(
                    item
                    for item in unique_news
                    if item.url not in used_urls
                )
            selected = selected[:max_items]
            selected.sort(key=_sort_key, reverse=True)
        else:
            selected = unique_news[:max_items]

        logger.info(
            f"News aggregated: {len(all_news)} raw, {len(unique_news)} unique, "
            f"output {len(selected)} items"
        )

        return selected

class WatchlistGoogleNewsProvider:
    """按 watchlist 动态生成 Google News RSS 定向源。"""

    name = "google_news_watchlist"

    def __init__(self, instruments_getter):
        self._instruments_getter = instruments_getter

    @staticmethod
    def build_url(instrument) -> str:
        query = urllib.parse.quote(f"{instrument.name} OR {instrument.code}")
        if instrument.market == "us":
            locale = "hl=en-US&gl=US&ceid=US:en"
        else:
            locale = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        return f"https://news.google.com/rss/search?q={query}&{locale}"

    async def fetch(self, max_items: int = 10) -> list[NewsItem]:
        instruments = list(self._instruments_getter())
        if not instruments or max_items <= 0:
            return []
        per_symbol = max(1, (max_items + len(instruments) - 1) // len(instruments))

        async def _fetch(instrument):
            provider = RSSNewsProvider(
                self.build_url(instrument),
                source_name=f"Google News 持仓:{instrument.code}",
                language="en" if instrument.market == "us" else "zh",
                scope="holding",
            )
            items = await provider.fetch(max_items=per_symbol)
            return [
                replace(
                    item,
                    tags=list(dict.fromkeys([*item.tags, instrument.code])),
                    raw_metadata={
                        **item.raw_metadata,
                        "market": instrument.market,
                        "symbol": instrument.code,
                        "instrument_name": instrument.name,
                    },
                )
                for item in items
            ]

        results = await asyncio.gather(
            *[_fetch(instrument) for instrument in instruments]
        )
        items = [item for result in results for item in result]
        items.sort(
            key=lambda item: item.published_at.timestamp() if item.published_at else 0,
            reverse=True,
        )
        return items[:max_items]
