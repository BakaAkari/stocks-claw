"""ContextBuilder 测试 — 覆盖基础构建、技术指标注入、宏观数据注入、降级

测试策略：
- Mock 所有依赖（fetcher, scaffolds, history_cache, macro_provider）
- 验证 AnalysisContext 结构完整性
- 验证 raw_prompt 包含技术指标和宏观数据段落
- 验证 macro_provider 失败不阻断
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from stocks.domain.models import (
    AnalysisContext,
    FinancialAsset,
    Instrument,
    MarketState,
    NewsItem,
    PortfolioMapping,
    Quote,
)
from stocks.engine.context_builder import ContextBuilder
from stocks.engine.history_cache import HistoryCache


@pytest.fixture
def temp_dir():
    import shutil
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_assets():
    return [
        FinancialAsset(name="股票基金", platform="支付宝", amount=50000, asset_type="equity", confirmed=True),
        FinancialAsset(name="余额宝", platform="支付宝", amount=30000, asset_type="cash", confirmed=True),
    ]


@pytest.fixture
def sample_instruments():
    return [
        Instrument(code="000001", name="平安银行", market="a"),
    ]


@pytest.fixture
def sample_quotes(sample_instruments):
    inst = sample_instruments[0]
    return {
        "a": [
            Quote(
                instrument=inst,
                price=10.5,
                change=0.3,
                pct_change=2.94,
                volume_lot=1000000,
                open_price=10.2,
                high=10.6,
                low=10.1,
                prev_close=10.2,
            )
        ]
    }


@pytest.fixture
def mock_fetcher(sample_quotes):
    """模拟 DataFetcher，返回预设行情"""
    fetcher = Mock()
    fetcher.fetch_quotes = AsyncMock(return_value=sample_quotes)
    return fetcher


@pytest.fixture
def mock_scaffolds():
    """模拟 PortfolioScaffold 和 MarketScaffold"""
    portfolio_scaffold = Mock()
    portfolio_scaffold.build = Mock(return_value=PortfolioMapping(
        buckets={"equity": [], "cash": []},
        ratios={"equity": 0.625, "cash": 0.375},
        dominant_layers=["equity"],
    ))
    portfolio_scaffold.check_drift = Mock(return_value=[])

    market_scaffold = Mock()
    market_scaffold.build = Mock(return_value=MarketState(
        risk_appetite="risk_on",
        tech_state="expanding",
        safe_haven_state="weakening",
        china_state="stable_positive",
        rates_state="neutral",
    ))

    return portfolio_scaffold, market_scaffold


# ------------------------------------------------------------------
# 基础构建
# ------------------------------------------------------------------

class TestBasicBuild:
    async def test_build_minimal(self, mock_fetcher, mock_scaffolds, sample_assets, sample_instruments):
        """最简构建：无 cache，无 macro provider"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={"risk_tolerance": "moderate"},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        assert isinstance(context, AnalysisContext)
        assert context.schema_version == 5
        assert context.asset_count == 2
        assert context.raw_prompt_input != ""
        assert "【投资组合分析上下文】" in context.raw_prompt_input
        assert context.macro_snapshot is None
        assert context.technical_indicators["a:000001"]["status"] == "missing"
        assert context.data_quality["schema_version"] == 1
        assert context.data_quality["quotes"]["status"] == "ok"
        assert context.data_quality["quotes"]["item_count"] == 1
        assert context.data_quality["news"]["status"] == "not_requested"
        assert context.data_quality["macro"]["status"] == "not_configured"
        assert context.data_quality["technical_indicators"]["status"] == "missing"
        assert context.data_quality["market_events"]["status"] == "not_requested"

    async def test_build_no_instruments(self, mock_fetcher, mock_scaffolds, sample_assets):
        """无 instruments 时 quotes 为空"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=[],
            recent_snapshots=[],
        )

        assert context.quotes == {}
        assert context.technical_indicators == {}
        assert context.data_quality["quotes"]["status"] == "not_requested"
        assert context.data_quality["technical_indicators"]["status"] == "not_requested"
        assert context.data_quality["market_events"]["status"] == "not_requested"
        assert "暂无行情数据" in context.raw_prompt_input


# ------------------------------------------------------------------
# 技术指标注入
# ------------------------------------------------------------------

class TestIndicatorEnrichment:
    async def test_build_with_history_cache(self, mock_fetcher, mock_scaffolds, sample_assets, sample_instruments, sample_quotes, temp_dir):
        """有 history_cache 时，quotes 应附加技术指标"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold, history_cache=cache)

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        await cache.close()

        # 验证 quote 有 indicators 字段
        quotes = context.quotes["a"]
        assert len(quotes) == 1
        q = quotes[0]
        assert q.indicators is not None
        # 数据点只有 1 条（刚写入），所以 MA 等应为 None
        assert q.indicators.get("data_points") == 1
        assert q.indicators.get("ma_5") is None  # 数据不足
        assert context.technical_indicators["a:000001"]["status"] == "ok"
        assert context.technical_indicators["a:000001"]["source"] == "history_cache"
        assert context.technical_indicators["a:000001"]["data_points"] == 1
        assert context.data_quality["technical_indicators"]["status"] == "ok"
        assert context.data_quality["technical_indicators"]["source"] == "history_cache"

    async def test_raw_prompt_with_indicators(self, mock_fetcher, mock_scaffolds, sample_assets, sample_instruments, sample_quotes, temp_dir):
        """raw_prompt 应包含技术指标段落（即使数据不足也有 data_points）"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold, history_cache=cache)

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        await cache.close()

        assert "【市场行情与技术指标】" in context.raw_prompt_input
        assert "平安银行" in context.raw_prompt_input


# ------------------------------------------------------------------
# 宏观数据注入
# ------------------------------------------------------------------

class TestMacroData:
    async def test_build_with_macro_provider(self, mock_fetcher, mock_scaffolds, sample_assets, sample_instruments):
        """有 macro_provider 时，context 应包含宏观数据"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        macro_provider = Mock()
        macro_provider.fetch = AsyncMock(return_value=Mock(
            to_dict=Mock(return_value={
                "vix": 25.5,
                "usd_cny": 7.25,
                "us_10y_yield": 4.2,
                "errors": {},
            })
        ))

        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold, macro_provider=macro_provider)
        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        assert context.macro_snapshot is not None
        assert context.macro_snapshot["vix"] == 25.5
        assert context.data_quality["macro"]["status"] == "partial"
        assert context.data_quality["macro"]["filled_fields"] == 3
        assert "【宏观环境】" in context.raw_prompt_input
        assert "VIX 恐慌指数" in context.raw_prompt_input

    async def test_macro_provider_failure(self, mock_fetcher, mock_scaffolds, sample_assets, sample_instruments):
        """macro_provider 失败不应阻断整体流程"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        macro_provider = Mock()
        macro_provider.fetch = AsyncMock(side_effect=Exception("Network error"))

        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold, macro_provider=macro_provider)
        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        # 宏观数据应为 None，但不应抛异常
        assert context.macro_snapshot is None
        assert context.data_quality["macro"]["status"] == "missing"
        assert "Network error" in context.data_quality["macro"]["errors"]["provider"]
        assert "【宏观环境】" not in context.raw_prompt_input
        assert context.raw_prompt_input != ""


# ------------------------------------------------------------------
# raw_prompt 结构验证
# ------------------------------------------------------------------

class TestRawPromptStructure:
    async def test_prompt_sections(self, mock_fetcher, mock_scaffolds, sample_assets, sample_instruments):
        """raw_prompt 应包含所有预期段落"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)

        context = await builder.build(
            assets=sample_assets,
            constraints={"equity": {"min": 0.5, "max": 0.8}},
            profile={"risk_tolerance": "moderate"},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        prompt = context.raw_prompt_input
        assert "【用户画像】" in prompt
        assert "【资产明细】" in prompt
        assert "【组合结构】" in prompt
        assert "【约束偏离检查】" in prompt
        assert "【约束配置】" in prompt
        assert "【市场行情与技术指标】" in prompt
        assert "【市场状态】" in prompt
        assert "【新闻事件摘要】" in prompt
        assert "【相关新闻】" in prompt
        assert "请基于以上上下文给出投资组合分析和建议" in prompt

    async def test_prompt_with_news(self, mock_fetcher, mock_scaffolds, sample_assets, sample_instruments):
        """有新闻时 prompt 应包含新闻"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)

        news = [
            NewsItem(
                title="Test News",
                url="https://example.com/news",
                source_name="test",
                source_type="test",
                published_at=datetime.now(timezone.utc),
                summary="Summary",
                language="zh",
            )
        ]

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
            enhanced_news=news,
        )

        assert context.news_count == 1
        assert len(context.market_events) == 1
        assert context.news_digest["event_count"] == 1
        assert context.data_quality["market_events"]["status"] == "ok"
        assert context.data_quality["news"]["status"] == "ok"
        assert context.data_quality["news"]["sources"] == {"test:test": 1}
        assert "【新闻事件摘要】" in context.raw_prompt_input
        assert "Test News" in context.raw_prompt_input


class TestDataQuality:
    async def test_quotes_quality_includes_degradation_log(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_assets,
        sample_instruments,
    ):
        """行情质量层带 provider 降级记录"""
        mock_fetcher.get_degradation_log = Mock(return_value=[{
            "market": "a",
            "primary_provider": "eastmoney_a",
            "fallback_provider": None,
            "result": "success",
            "message": "ok",
        }])
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        quotes_quality = context.data_quality["quotes"]
        assert quotes_quality["status"] == "ok"
        assert quotes_quality["providers"] == ["eastmoney_a"]
        assert quotes_quality["by_market"]["a"]["primary_provider"] == "eastmoney_a"
        assert quotes_quality["degradation"][0]["result"] == "success"


class TestAnalysisContextSerialization:
    async def test_to_dict_includes_schema_v4_indicators_and_data_quality(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_assets,
        sample_instruments,
        temp_dir,
    ):
        """to_dict 输出 schema v5、顶层事件、指标与 data_quality"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold, history_cache=cache)

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        await cache.close()

        data = context.to_dict()
        assert data["schema_version"] == 5
        assert "market_events" in data
        assert "news_digest" in data
        assert "technical_indicators" in data
        assert "data_quality" in data
        assert data["technical_indicators"]["a:000001"]["status"] == "ok"
        assert data["technical_indicators"]["a:000001"]["data_points"] == 1
        assert data["data_quality"]["technical_indicators"]["status"] == "ok"
