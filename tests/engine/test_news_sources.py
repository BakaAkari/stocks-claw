"""NewsAggregator 测试 — 覆盖聚合、去重、排序、截断、降级

测试策略：
- 使用 Mock Provider 模拟不同数据源，无需外部网络
- 验证去重逻辑（URL 去重）
- 验证排序逻辑（时间降序）
- 验证降级逻辑（单个 provider 失败不影响其他）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stocks.domain.models import NewsItem
from stocks.engine.news_sources import NewsAggregator


# Mock Provider 辅助类
class MockNewsProvider:
    """模拟新闻提供者，用于测试"""

    def __init__(self, items: list[NewsItem], raise_error: bool = False):
        self._items = items
        self._raise_error = raise_error

    async def fetch(self, max_items: int = 10) -> list[NewsItem]:
        if self._raise_error:
            raise Exception("Provider error")
        return self._items[:max_items]


def make_news(title: str, url: str, minutes_ago: int = 0, source: str = "test") -> NewsItem:
    """构造测试新闻"""
    return NewsItem(
        title=title,
        url=url,
        source_name=source,
        source_type="test",
        published_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        summary=f"Summary of {title}",
        language="zh",
    )


# ------------------------------------------------------------------
# 基础功能
# ------------------------------------------------------------------

class TestBasic:
    async def test_empty_providers(self):
        """空 provider 列表返回空"""
        aggregator = NewsAggregator([])
        result = await aggregator.fetch(max_items=10)
        assert result == []

    async def test_single_provider(self):
        """单 provider 正常返回（按时间降序）"""
        items = [
            make_news("News 1", "https://example.com/1", 10),
            make_news("News 2", "https://example.com/2", 5),
        ]
        provider = MockNewsProvider(items)
        aggregator = NewsAggregator([provider])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 2
        assert result[0].title == "News 2"  # 更新（5分钟前）
        assert result[1].title == "News 1"  # 更旧（10分钟前）

    async def test_multiple_providers(self):
        """多 provider 数据合并"""
        items1 = [make_news("News A", "https://a.com/1", 10, "src1")]
        items2 = [make_news("News B", "https://b.com/1", 5, "src2")]

        aggregator = NewsAggregator([
            MockNewsProvider(items1),
            MockNewsProvider(items2),
        ])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 2
        titles = [n.title for n in result]
        assert "News A" in titles
        assert "News B" in titles


# ------------------------------------------------------------------
# 去重
# ------------------------------------------------------------------

class TestDeduplication:
    async def test_url_deduplication(self):
        """相同 URL 的新闻应去重，保留第一个来源"""
        items1 = [make_news("News 1", "https://example.com/1", 10, "src1")]
        items2 = [make_news("News 1 Duplicate", "https://example.com/1", 5, "src2")]

        aggregator = NewsAggregator([
            MockNewsProvider(items1),
            MockNewsProvider(items2),
        ])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 1
        # 保留第一个来源的标题
        assert result[0].title == "News 1"
        assert result[0].source_name == "src1"

    async def test_different_urls_kept(self):
        """不同 URL 的新闻应保留"""
        items1 = [make_news("News 1", "https://example.com/1", 10, "src1")]
        items2 = [make_news("News 2", "https://example.com/2", 5, "src2")]

        aggregator = NewsAggregator([
            MockNewsProvider(items1),
            MockNewsProvider(items2),
        ])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 2

    async def test_empty_url_skipped(self):
        """空 URL 的新闻应跳过"""
        items = [
            make_news("Valid", "https://example.com/1", 10),
            NewsItem(
                title="No URL",
                url="",
                source_name="test",
                source_type="test",
                published_at=datetime.now(timezone.utc),
                summary=None,
            ),
        ]
        aggregator = NewsAggregator([MockNewsProvider(items)])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 1
        assert result[0].title == "Valid"


# ------------------------------------------------------------------
# 排序
# ------------------------------------------------------------------

class TestSorting:
    async def test_sort_by_time_desc(self):
        """新闻应按时间降序排列（最新在前）"""
        items = [
            make_news("Old", "https://example.com/old", 60),
            make_news("New", "https://example.com/new", 5),
            make_news("Middle", "https://example.com/mid", 30),
        ]
        aggregator = NewsAggregator([MockNewsProvider(items)])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 3
        assert result[0].title == "New"    # 5 分钟前
        assert result[1].title == "Middle" # 30 分钟前
        assert result[2].title == "Old"    # 60 分钟前

    async def test_no_time_last(self):
        """无 published_at 的新闻应排在最后"""
        items = [
            make_news("With Time", "https://example.com/1", 10),
            NewsItem(
                title="No Time",
                url="https://example.com/2",
                source_name="test",
                source_type="test",
                published_at=None,
                summary=None,
            ),
        ]
        aggregator = NewsAggregator([MockNewsProvider(items)])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 2
        assert result[0].title == "With Time"
        assert result[1].title == "No Time"


# ------------------------------------------------------------------
# 截断
# ------------------------------------------------------------------

class TestTruncation:
    async def test_truncation(self):
        """max_items 应正确截断"""
        items = [make_news(f"News {i}", f"https://example.com/{i}", i) for i in range(50)]
        aggregator = NewsAggregator([MockNewsProvider(items)])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 10

    async def test_max_source_items(self):
        """max_source_items 应限制单个源的数据量"""
        items = [make_news(f"News {i}", f"https://example.com/{i}", i) for i in range(50)]
        aggregator = NewsAggregator(
            [MockNewsProvider(items)],
            max_source_items=5,
        )
        result = await aggregator.fetch(max_items=20)

        assert len(result) == 5  # 单源截断到 5 条


# ------------------------------------------------------------------
# 降级与容错
# ------------------------------------------------------------------

class TestFallback:
    async def test_provider_error(self):
        """单个 provider 失败不应影响其他"""
        items1 = [make_news("News A", "https://a.com/1", 10, "src1")]
        items2 = [make_news("News B", "https://b.com/1", 5, "src2")]

        aggregator = NewsAggregator([
            MockNewsProvider(items1, raise_error=True),
            MockNewsProvider(items2),
        ])
        result = await aggregator.fetch(max_items=10)

        assert len(result) == 1
        assert result[0].title == "News B"

    async def test_all_providers_fail(self):
        """全部 provider 失败返回空列表"""
        aggregator = NewsAggregator([
            MockNewsProvider([], raise_error=True),
            MockNewsProvider([], raise_error=True),
        ])
        result = await aggregator.fetch(max_items=10)
        assert result == []

    async def test_provider_returns_none(self):
        """provider 返回 None 应视为空列表"""
        class NoneProvider:
            async def fetch(self, max_items: int = 10) -> list[NewsItem]:
                return None  # type: ignore[return-value]

        aggregator = NewsAggregator([NoneProvider()])
        result = await aggregator.fetch(max_items=10)
        assert result == []
