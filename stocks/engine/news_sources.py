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
from typing import Protocol

from stocks.domain.models import NewsItem
from stocks.logging_utils import get_logger

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

    async def fetch(self, max_items: int = 20) -> list[NewsItem]:
        """获取聚合后的新闻列表

        流程：
        1. 并行调用所有 provider
        2. URL 去重（保留第一个来源的）
        3. 按 published_at 降序排序
        4. 截断至 max_items
        """
        if not self._providers:
            return []

        # 并行获取
        tasks = [self._fetch_safe(p) for p in self._providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_news: list[NewsItem] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"News provider {i} failed: {result}")
                continue
            all_news.extend(result)

        # 去重（基于 URL，保留第一个来源）
        seen_urls: set[str] = set()
        unique_news: list[NewsItem] = []
        for item in all_news:
            if not item.url:
                continue
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                unique_news.append(item)

        # 排序：按 published_at 降序（最新在前），无时间放最后
        def _sort_key(item: NewsItem) -> float:
            if item.published_at is None:
                return 0.0  # 1970-01-01 的时间戳，确保放最后
            return item.published_at.timestamp()

        unique_news.sort(key=_sort_key, reverse=True)

        logger.info(
            f"News aggregated: {len(all_news)} raw, {len(unique_news)} unique, "
            f"output {min(max_items, len(unique_news))} items"
        )

        return unique_news[:max_items]

    async def _fetch_safe(self, provider: NewsProvider) -> list[NewsItem]:
        """安全获取，捕获异常"""
        try:
            items = await provider.fetch(max_items=self._max_source_items)
            return items if items else []
        except Exception as e:
            logger.warning(f"News provider {type(provider).__name__} failed: {e}")
            return []
