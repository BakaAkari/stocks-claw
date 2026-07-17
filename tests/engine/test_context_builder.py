"""ContextBuilder 测试 — 覆盖基础构建、技术指标注入、宏观数据注入、降级

测试策略：
- Mock 所有依赖（fetcher, scaffolds, history_cache, macro_provider）
- 验证 AnalysisContext 结构完整性
- 验证 raw_prompt 包含技术指标和宏观数据段落
- 验证 macro_provider 失败不阻断
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from stocks.domain.models import (
    Account,
    AnalysisContext,
    Classification,
    CostBasis,
    FinancialAsset,
    Holding,
    Instrument,
    Liquidity,
    MarketState,
    NewsItem,
    PortfolioMapping,
    Position,
    Quote,
    UpcomingEvent,
    ValuationInput,
)
from stocks.engine.context_builder import ContextBuilder
from stocks.engine.history_cache import HistoryCache
from stocks.engine.news_intelligence_store import (
    EventCluster,
    IntelligenceSignal,
    IntelligenceSnapshot,
    NewsIntelligenceStore,
)


def test_intelligence_digest_preserves_complete_signal_payload(
    tmp_path: Path, mock_fetcher, mock_scaffolds
):
    portfolio_scaffold, market_scaffold = mock_scaffolds
    intelligence_dir = tmp_path / "intelligence"
    store = NewsIntelligenceStore(intelligence_dir)
    signal = IntelligenceSignal(
        symbol="a:588000",
        name="科创50ETF",
        direction="hold",
        horizon="short_term",
        rationale="无直接利空",
        falsification="重大事件改变格局",
        risk_source="主题切换或突发事件",
        confidence=0.55,
        urgency="low",
        generated_at=datetime.now(timezone.utc),
    )
    store.save_signals([signal], generated_at=signal.generated_at)
    builder = ContextBuilder(
        mock_fetcher,
        portfolio_scaffold,
        market_scaffold,
        config={"intelligence_dir": str(intelligence_dir)},
    )
    digest = builder._build_intelligence_digest(repo_root=tmp_path)
    assert digest["top_signals"] == [signal.to_dict()]


def test_intelligence_digest_preserves_source_rich_clusters(
    tmp_path: Path, mock_fetcher, mock_scaffolds
):
    portfolio_scaffold, market_scaffold = mock_scaffolds
    intelligence_dir = tmp_path / "intelligence"
    store = NewsIntelligenceStore(intelligence_dir)

    snapshot = IntelligenceSnapshot(
        collected_at=datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc),
        sources={"Reuters": "ok"},
        articles=[{"title": "test"}],
        macro={},
        quotes={},
        data_quality={},
    )
    store.save_snapshot(snapshot)

    cluster = EventCluster(
        cluster_id="cluster-oil",
        theme="\u6cb9\u4ef7\u6ce2\u52a8",
        event_type="geopolitical",
        summary="\u4e2d\u4e1c\u5c40\u52bf\u63a8\u9ad8\u6cb9\u4ef7",
        articles=[
            {
                "source": "Reuters",
                "title": "Oil rises as shipping risk increases",
                "url": "https://example.test/reuters-oil",
                "published_at": "2026-07-17T07:30:00+00:00",
            },
            {
                "source": "Bloomberg",
                "title": "Traders hedge oil exposure",
                "url": "https://example.test/bloomberg-hedge",
                "published_at": "2026-07-17T07:45:00+00:00",
                "raw_html": "<p>secret</p>",
            },
        ],
        affected_markets=["crude_oil"],
        affected_symbols=["a:600028"],
        sentiment="bearish",
        urgency="high",
        confidence=0.85,
        formed_at=datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc),
    )
    store.save_clusters([cluster], formed_at=cluster.formed_at)

    builder = ContextBuilder(
        mock_fetcher,
        portfolio_scaffold,
        market_scaffold,
        config={"intelligence_dir": str(intelligence_dir)},
    )
    digest = builder._build_intelligence_digest(repo_root=tmp_path)

    assert digest["status"] == "ok"
    cluster_out = digest["top_clusters"][0]
    assert cluster_out["cluster_id"] == "cluster-oil"
    assert cluster_out["event_type"] == "geopolitical"
    assert cluster_out["formed_at"] == "2026-07-17T08:00:00+00:00"
    assert cluster_out["articles"] == [
        {
            "source": "Reuters",
            "title": "Oil rises as shipping risk increases",
            "url": "https://example.test/reuters-oil",
            "published_at": "2026-07-17T07:30:00+00:00",
        },
        {
            "source": "Bloomberg",
            "title": "Traders hedge oil exposure",
            "url": "https://example.test/bloomberg-hedge",
            "published_at": "2026-07-17T07:45:00+00:00",
        },
    ]
    import json as _json
    assert "raw_html" not in _json.dumps(cluster_out)


def test_intelligence_digest_returns_empty_clusters_when_stale(
    tmp_path: Path, mock_fetcher, mock_scaffolds
):
    portfolio_scaffold, market_scaffold = mock_scaffolds
    intelligence_dir = tmp_path / "intelligence"
    store = NewsIntelligenceStore(intelligence_dir)

    old_formed_at = datetime(2026, 7, 14, 8, 0, 0, tzinfo=timezone.utc)
    cluster = EventCluster(
        cluster_id="cluster-oil",
        theme="\u6cb9\u4ef7\u6ce2\u52a8",
        event_type="geopolitical",
        summary="\u4e2d\u4e1c\u5c40\u52bf\u63a8\u9ad8\u6cb9\u4ef7",
        articles=[],
        affected_markets=["crude_oil"],
        affected_symbols=["a:600028"],
        sentiment="bearish",
        urgency="high",
        confidence=0.85,
        formed_at=old_formed_at,
    )
    store.save_clusters([cluster], formed_at=old_formed_at)

    builder = ContextBuilder(
        mock_fetcher,
        portfolio_scaffold,
        market_scaffold,
        config={"intelligence_dir": str(intelligence_dir)},
    )
    digest = builder._build_intelligence_digest(repo_root=tmp_path)
    assert digest["top_clusters"] == []


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
        assert context.schema_version == 12
        assert context.asset_count == 2
        assert context.raw_prompt_input != ""
        assert "【投资组合分析上下文】" in context.raw_prompt_input
        assert "risk_tolerance: moderate" in context.raw_prompt_input
        assert "50,000.00 CNY" in context.raw_prompt_input
        assert "30,000.00 CNY" in context.raw_prompt_input
        assert "占比" in context.raw_prompt_input
        assert context.to_dict()["assets"][0]["amount"] == 50000
        assert context.macro_snapshot is None
        assert context.technical_indicators["a:000001"]["status"] == "missing"
        assert context.data_quality["schema_version"] == 10
        assert context.data_quality["quotes"]["status"] == "ok"
        assert context.data_quality["quotes"]["item_count"] == 1
        assert context.data_quality["news"]["status"] == "not_requested"
        assert context.data_quality["macro"]["status"] == "not_configured"
        assert context.data_quality["technical_indicators"]["status"] == "missing"
        assert context.data_quality["market_events"]["status"] == "not_requested"

    def test_schema_versions_match_data_model_document(self):
        """AnalysisContext/data_quality 版本必须与权威数据模型同步。"""
        assert AnalysisContext.__dataclass_fields__["schema_version"].default == 12
        data_model = (
            Path(__file__).resolve().parents[2] / "stocks" / "DATA_MODEL.md"
        ).read_text(encoding="utf-8")
        assert "`AnalysisContext.schema_version` 当前为 `12`" in data_model
        assert "## data_quality v10" in data_model

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

    async def test_failed_currency_conversion_is_visible(
        self,
        mock_fetcher,
        mock_scaffolds,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        hkd_asset = FinancialAsset(
            name="港币现金",
            platform="银行",
            amount=1000,
            asset_type="cash",
            currency="HKD",
            amount_cny=None,
            conversion_status="failed",
            conversion_source="unsupported_currency",
            conversion_rate=None,
        )

        context = await builder.build(
            assets=[hkd_asset],
            constraints={},
            profile={},
            instruments=[],
            recent_snapshots=[],
        )

        quality = context.data_quality["currency_conversion"]
        assert quality["status"] == "failed"
        assert quality["failed_count"] == 1
        assert quality["items"][0]["source"] == "unsupported_currency"
        assert "换算失败（未计入合计）" in context.raw_prompt_input

    async def test_stale_currency_conversion_is_visible(
        self,
        mock_fetcher,
        mock_scaffolds,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        usd_asset = FinancialAsset(
            name="美元现金",
            platform="银行",
            amount=100,
            asset_type="cash",
            currency="USD",
            amount_cny=675,
            conversion_status="degraded",
            conversion_source="stale_cache",
            conversion_rate=6.75,
        )

        context = await builder.build(
            assets=[usd_asset],
            constraints={},
            profile={},
            instruments=[],
            recent_snapshots=[],
        )

        quality = context.data_quality["currency_conversion"]
        assert quality["status"] == "degraded"
        assert quality["degraded_count"] == 1
        assert quality["items"][0]["source"] == "stale_cache"


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
        # 数据点只有 1 条(刚写入),核心指标 MA/RSI/MACD/Bollinger 应全为 None
        assert q.indicators.get("data_points") == 1
        assert q.indicators.get("ma_5") is None  # 数据不足
        # D0-1:data_points=1 < 15 → 单项 missing,聚合 missing(不再报假 ok)
        assert context.technical_indicators["a:000001"]["status"] == "missing"
        assert context.technical_indicators["a:000001"]["source"] == "history_cache"
        assert context.technical_indicators["a:000001"]["data_points"] == 1
        assert "ma_20" in context.technical_indicators["a:000001"]["unavailable"]
        assert context.data_quality["technical_indicators"]["status"] == "missing"
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


class TestIndicatorClassification:
    """D0-1:技术指标按 data_points 三态判级(单元级,不走 build)。"""

    def _make_builder(self, mock_fetcher, mock_scaffolds):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        return ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)

    def test_missing_when_single_bar(self, mock_fetcher, mock_scaffolds):
        """单 bar 输入(D0-1 复现):所有指标为 None,单项判 missing,聚合 missing。"""
        builder = self._make_builder(mock_fetcher, mock_scaffolds)
        indicators = {
            "ma_5": None, "ma_20": None, "ma_60": None,
            "rsi_14": None,
            "macd": {"macd": None, "signal": None, "hist": None},
            "bollinger": {"upper": None, "middle": None, "lower": None, "bandwidth": None},
            "volume_ratio": None, "price_position": None, "volatility_20": None,
            "data_points": 1,
        }
        status, unavailable = builder._classify_indicator_item(indicators)
        assert status == "missing"
        assert set(unavailable) == set(ContextBuilder._INDICATOR_CORE_KEYS)

        # 聚合:全 missing → missing / freshness=missing
        inst = Instrument(code="000001", name="平安银行", market="a")
        q = Quote(instrument=inst, price=10.0, indicators=indicators)
        by_symbol = builder._collect_technical_indicators({"a": [q]})
        assert by_symbol["a:000001"]["status"] == "missing"
        quality = builder._indicator_quality(
            "2026-07-02T00:00:00+00:00", [inst], {"a": [q]}, by_symbol,
        )
        assert quality["status"] == "missing"
        assert quality["freshness"] == "missing"
        assert "a:000001" in quality["missing_symbols"]

    def test_partial_when_20_bars(self, mock_fetcher, mock_scaffolds):
        """20 bars:MA20/RSI14/Bollinger 可算,MACD(需 26+9=35)不可用 → 单项 partial。"""
        builder = self._make_builder(mock_fetcher, mock_scaffolds)
        indicators = {
            "ma_5": 10.2, "ma_20": 10.0, "ma_60": None,
            "rsi_14": 55.0,
            "macd": {"macd": None, "signal": None, "hist": None},
            "bollinger": {"upper": 10.5, "middle": 10.0, "lower": 9.5, "bandwidth": 0.1},
            "volume_ratio": 1.1, "price_position": 50.0, "volatility_20": 0.2,
            "data_points": 20,
        }
        status, unavailable = builder._classify_indicator_item(indicators)
        assert status == "partial"
        assert "macd.hist" in unavailable
        assert "ma_20" not in unavailable

    def test_ok_when_60_bars(self, mock_fetcher, mock_scaffolds):
        """60 bars:核心指标全部可用 → 单项 ok,聚合 ok / freshness=fresh。"""
        builder = self._make_builder(mock_fetcher, mock_scaffolds)
        indicators = {
            "ma_5": 10.2, "ma_20": 10.0, "ma_60": 9.8,
            "rsi_14": 55.0,
            "macd": {"macd": 0.1, "signal": 0.05, "hist": 0.05},
            "bollinger": {"upper": 10.5, "middle": 10.0, "lower": 9.5, "bandwidth": 0.1},
            "volume_ratio": 1.1, "price_position": 50.0, "volatility_20": 0.2,
            "data_points": 60,
        }
        status, unavailable = builder._classify_indicator_item(indicators)
        assert status == "ok"
        assert unavailable == []

        inst = Instrument(code="000001", name="平安银行", market="a")
        q = Quote(instrument=inst, price=10.0, indicators=indicators)
        by_symbol = builder._collect_technical_indicators({"a": [q]})
        assert by_symbol["a:000001"]["status"] == "ok"
        quality = builder._indicator_quality(
            "2026-07-02T00:00:00+00:00", [inst], {"a": [q]}, by_symbol,
        )
        assert quality["status"] == "ok"
        assert quality["freshness"] == "fresh"
        assert quality["missing_symbols"] == []

    def test_aggregate_partial_when_mixed(self, mock_fetcher, mock_scaffolds):
        """混合(至少一个 partial 或一个 ok) → 聚合 partial。"""
        builder = self._make_builder(mock_fetcher, mock_scaffolds)
        by_symbol = {
            "a:000001": {"status": "ok"},
            "a:000002": {"status": "partial"},
        }
        inst1 = Instrument(code="000001", name="A", market="a")
        inst2 = Instrument(code="000002", name="B", market="a")
        q1 = Quote(instrument=inst1, price=10.0, indicators={"data_points": 60})
        q2 = Quote(instrument=inst2, price=10.0, indicators={"data_points": 20})
        quality = builder._indicator_quality(
            "2026-07-02T00:00:00+00:00", [inst1, inst2], {"a": [q1, q2]}, by_symbol,
        )
        assert quality["status"] == "partial"
        assert quality["freshness"] == "fresh"
        assert quality["missing_symbols"] == ["a:000002"]

    async def test_raw_prompt_annotates_partial_and_missing(self, mock_fetcher, mock_scaffolds, sample_assets, sample_instruments, temp_dir):
        """raw_prompt 行情段:partial/missing 标的显式标注 bars 数与不可用等级。"""
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

        prompt = context.raw_prompt_input
        # 单 bar 场景 → missing 标注必须出现
        assert "指标不可用" in prompt


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
                "field_sources": {
                    "vix": {"source": "test", "as_of": "2026-07-02"},
                    "usd_cny": {"source": "test", "as_of": "2026-07-02"},
                    "us_10y_yield": {"source": "test", "as_of": "2026-07-02"},
                },
                "official_stats": {},
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
        assert "市场定价代理" in context.raw_prompt_input
        assert "官方统计（滞后月度，不代表实时）" in context.raw_prompt_input

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

    def test_macro_quality_uses_oldest_field_as_of(self, mock_fetcher, mock_scaffolds):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        market_fields = {
            "usd_cny": 6.8,
            "vix": 18.0,
            "us_10y_yield": 4.2,
            "dxy": 120.0,
            "gold": 2400.0,
            "crude_oil": 72.0,
        }
        official = {
            "cpi_yoy": 2.4,
            "us_unemployment": 4.1,
            "fed_funds_rate": 3.64,
        }
        field_sources = {
            field_name: {"source": "fred:test", "as_of": "2026-07-02"}
            for field_name in market_fields
        }
        field_sources.update({
            f"official_stats.{field_name}": {
                "source": "fred:test",
                "as_of": "2026-05-01",
            }
            for field_name in official
        })

        quality = builder._macro_quality(
            "2026-07-03T08:00:00+00:00",
            {
                **market_fields,
                "official_stats": official,
                "field_sources": field_sources,
                "source": "composite",
                "errors": {},
                "market_as_of": "2026-07-02T00:00:00+00:00",
                "official_as_of": "2026-05-01T00:00:00+00:00",
                "next_official_release": "2026-07-12",
            },
            None,
        )

        assert quality["status"] == "ok"
        assert quality["as_of"] == "2026-05-01T00:00:00+00:00"
        assert quality["freshness"] == "old"
        assert quality["filled_fields"] == 9
        assert quality["missing_as_of"] == 0
        # New: tiered freshness
        assert quality["market"]["as_of"] == "2026-07-02T00:00:00+00:00"
        assert quality["market"]["freshness"] == "old"
        assert quality["official"]["as_of"] == "2026-05-01T00:00:00+00:00"
        assert quality["official"]["next_release"] == "2026-07-12"


class TestEarningsEventProjection:
    async def test_upcoming_earnings_projects_into_market_events_without_news(
        self, mock_fetcher, mock_scaffolds, sample_assets
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        calendar = Mock()
        calendar.fetch = AsyncMock(return_value=(
            [
                UpcomingEvent(
                    date="2026-07-10",
                    name="Apple 财报",
                    event_type="earnings",
                    market="us",
                    source="finnhub_earnings",
                    affected_symbols=["us:AAPL"],
                    days_until=2,
                    status="scheduled",
                    time_precision="date",
                )
            ],
            {
                "status": "ok",
                "event_count": 1,
                "expired_count": 0,
                "cache": {"hits": 1, "misses": 0},
                "sources": {"finnhub_earnings": 1},
                "errors": {},
            },
        ))
        builder = ContextBuilder(
            mock_fetcher,
            portfolio_scaffold,
            market_scaffold,
            event_calendar=calendar,
        )

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=[Instrument("AAPL", "Apple", "us")],
            recent_snapshots=[],
            news=None,
            news_requested=False,
        )

        earnings = [event for event in context.market_events if event.event_type == "earnings"]
        assert len(earnings) == 1
        assert earnings[0].source_type == "calendar"
        assert earnings[0].affected_symbols == ["us:AAPL"]
        assert context.data_quality["market_events"]["status"] == "ok"
        assert context.data_quality["market_events"]["calendar_event_count"] == 1


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
        assert "【未来催化剂日历】" in prompt
        assert "【板块轮动排名】" in prompt
        assert "【引擎动作信号】" in prompt
        assert "【新闻事件摘要】" in prompt
        assert "【相关新闻】" in prompt
        assert "按 personal_advice_prompt 的决策导向契约输出" in prompt
        assert "带触发条件的调仓清单与下一个机会提名" in prompt

    async def test_v12_position_valuation_exposure_liquidity_and_boundaries(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_instruments,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        accounts = [
            Account(
                account_id="broker",
                display_name="券商",
                institution_type="brokerage",
                base_currency="CNY",
            ),
            Account(
                account_id="fund",
                display_name="基金平台",
                institution_type="fund_platform",
                base_currency="CNY",
            ),
        ]
        positions = [
            Position(
                position_id="broker_000001",
                account_id="broker",
                display_name="平安银行持仓",
                currency="CNY",
                classification=Classification(
                    asset_class="equity",
                    product_type="stock",
                    exposure_tags=["cn_equity"],
                ),
                instrument={"instrument_key": "a:000001"},
                holding=Holding(
                    quantity=100,
                    unit="share",
                    cost_basis=CostBasis(unit_cost=8.0, currency="CNY"),
                ),
                valuation_input=ValuationInput(method="market_quote"),
                liquidity=Liquidity(tradable=True, rebalance_eligible=True, tier="t1"),
            ),
            Position(
                position_id="fund_nasdaq",
                account_id="fund",
                display_name="纳指基金",
                currency="CNY",
                classification=Classification(
                    asset_class="equity",
                    product_type="qdii_fund",
                    exposure_tags=["nasdaq100"],
                ),
                valuation_input=ValuationInput(
                    method="manual_amount",
                    manual_amount=2000,
                    as_of="2026-01-01",
                ),
                liquidity=Liquidity(tradable=False, rebalance_eligible=True, tier="t2_plus"),
            ),
            Position(
                position_id="bank_cash",
                account_id="bank",
                display_name="现金",
                currency="CNY",
                classification=Classification(
                    asset_class="cash",
                    product_type="cash",
                    exposure_tags=["cash_like"],
                ),
                valuation_input=ValuationInput(
                    method="manual_amount",
                    manual_amount=500,
                    as_of="2026-07-04",
                ),
                liquidity=Liquidity(tradable=True, rebalance_eligible=True, tier="cash"),
            ),
        ]

        context = await builder.build(
            assets=[],
            constraints={},
            profile={},
            instruments=sample_instruments,
            scan_instruments=[Instrument(code="QQQ", name="QQQ", market="us")],
            recent_snapshots=[],
            asset_schema_version=2,
            asset_accounts_v2=accounts,
            asset_positions_v2=positions,
            exposure_proxy={
                "nasdaq100": {
                    "instrument_key": "us:QQQ",
                    "note": "纳指100代理",
                }
            },
        )

        by_id = {item["position_id"]: item for item in context.position_valuations}
        assert context.schema_version == 12
        assert context.data_quality["schema_version"] == 10
        assert context.data_quality["asset_format"]["schema_version"] == 2
        assert by_id["broker_000001"]["market_value_cny"] == 1050.0
        assert by_id["broker_000001"]["pnl_pct"] == 31.25
        assert by_id["fund_nasdaq"]["advice_granularity"] == "sector"
        assert by_id["fund_nasdaq"]["proxy"]["instrument_key"] == "us:QQQ"
        assert "stale_manual" in by_id["fund_nasdaq"]["flags"]
        assert context.exposure_summary["exposures"]["nasdaq100"]["value_cny"] == 2000.0
        assert context.liquidity_summary["buckets"]["cash_or_t0"]["value_cny"] == 500.0
        assert context.liquidity_summary["buckets"]["t1_t2"]["value_cny"] == 1050.0
        assert context.liquidity_summary["buckets"]["locked_or_ineligible"]["value_cny"] == 2000.0
        assert context.asset_data_boundaries["issue_count"] >= 1
        assert "【逐持仓估值】" in context.raw_prompt_input
        assert "未实现盈亏 250.00 CNY (+31.25%)" in context.raw_prompt_input
        assert "【暴露集中度】" in context.raw_prompt_input
        assert "nasdaq100: 2,000.00 CNY" in context.raw_prompt_input
        assert "【可动用资金】" in context.raw_prompt_input
        assert "代理参考: nasdaq100 -> us:QQQ" in context.raw_prompt_input

    async def test_prompt_marks_mapped_holding_by_instrument_key(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_instruments,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        assets = [
            FinancialAsset(
                name="平安银行持仓",
                platform="券商",
                amount=12000,
                asset_type="股票",
                instrument_key="a:000001",
                quantity=1200,
                tradable=True,
            )
        ]

        context = await builder.build(
            assets=assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        prompt = context.raw_prompt_input
        assert "标的: a:000001 | 当前持有 1200 | 可交易" in prompt
        assert "平安银行 (000001): 10.50 (+2.94%) | 当前持有 1200" in prompt

    async def test_review_section_orders_advice_execution_and_forecasts(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_assets,
        sample_instruments,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        advice_id = "2026-07-01T00:00:00+00:00"
        recent_advice = [{
            "created_at": advice_id,
            "instruments": [{"market": "a", "code": "000001", "name": "平安银行"}],
            "direction": {"a:000001": "watch"},
            "rationale_summary": "等待价格触发后再处理。",
            "based_on": ["quotes", "portfolio"],
            "boundary": [{"type": "fact", "text": "现金偏高"}],
            "actions": [{
                "target": "a:000001",
                "action": "increase",
                "size_hint": "一成",
                "trigger": "收盘站上12.5",
                "invalidation": "跌破11.8",
                "horizon": "short",
            }],
            "triggers": [{
                "instrument": "a:000001",
                "type": "price_above",
                "level": 12.5,
                "action": "增加一成",
                "invalidation": "跌破11.8",
            }],
        }]
        forecast_summary = {
            "open_count": 0,
            "sample_count": 1,
            "hit_count": 1,
            "hit_rate": None,
            "sample_note": "样本不足",
            "recent_settlements": [{
                "deadline": "2026-07-02",
                "target": "a:000001",
                "status": "hit",
                "statement": "平安银行收盘高于12.5",
                "resolution_note": "deadline_close=12.8 at 2026-07-02",
            }],
            "recent_records": [],
        }

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
            recent_advice=recent_advice,
            execution_records=[{
                "advice_id": advice_id,
                "target": "a:000001",
                "action": "increase",
                "extent": "full",
                "note": "已执行",
            }],
            forecast_summary=forecast_summary,
        )

        prompt = context.raw_prompt_input
        assert "【复盘】" in prompt
        positions = [
            prompt.index("1. 上期建议 actions"),
            prompt.index("2. 触发核对"),
            prompt.index("3. 执行对照"),
            prompt.index("4. 到期预测结算"),
        ]
        assert positions == sorted(positions)
        assert "a:000001 | increase | 一成 | short" in prompt
        assert "a:000001 price_above 12.5 → no_data" in prompt
        assert "a:000001 | 建议 increase → executed | 记录 increase/full" in prompt
        assert "a:000001 | hit | 平安银行收盘高于12.5" in prompt

    async def test_review_section_marks_missing_segments(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_assets,
        sample_instruments,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )

        prompt = context.raw_prompt_input
        assert "缺失: 无已确认保存的上期建议" in prompt
        assert "缺失: 无上期建议，无法核对触发器" in prompt
        assert "缺失: 无上期建议，无法匹配执行记录" in prompt
        assert "缺失: 未加载预测台账或暂无预测记录" in prompt

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
            news=news,
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
    def test_news_quality_reports_scopes_and_provider_errors(
        self, mock_fetcher, mock_scaffolds
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        news = [
            NewsItem(
                title="Holding news",
                url="https://example.com/holding",
                source_name="holding",
                source_type="rss",
                published_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
                summary=None,
                scope="holding",
            ),
            NewsItem(
                title="General news",
                url="https://example.com/general",
                source_name="general",
                source_type="rss",
                published_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
                summary=None,
            ),
        ]

        quality = builder._news_quality(
            "2026-07-03T01:00:00+00:00",
            news,
            True,
            {"sec_edgar": "ProviderNetworkError: unavailable"},
        )

        assert quality["status"] == "partial"
        assert quality["scopes"] == {"holding": 1, "general": 1}
        assert "sec_edgar" in quality["errors"]

    async def test_us_single_source_failure_uses_stale_history(
        self,
        mock_scaffolds,
        sample_assets,
        temp_dir,
    ):
        instrument = Instrument(code="AAPL", name="Apple", market="us")
        fetcher = Mock()
        fetcher.fetch_quotes = AsyncMock(return_value={"us": []})
        fetcher.get_degradation_log = Mock(return_value=[{
            "market": "us",
            "primary_provider": "finnhub",
            "fallback_provider": None,
            "result": "empty",
            "error_type": "ProviderNetworkError",
            "message": "network failed",
        }])
        cache = HistoryCache(base_dir=temp_dir, ttl=86400)
        historical_at = datetime.now(timezone.utc) - timedelta(hours=6)
        await cache.warm(
            instrument,
            pd.DataFrame([{
                "timestamp": historical_at,
                "code": "AAPL",
                "name": "Apple",
                "market": "us",
                "price": 210.0,
                "open_price": 208.0,
                "high": 212.0,
                "low": 207.0,
                "prev_close": 209.0,
                "volume_lot": 1000,
            }]),
        )
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(
            fetcher,
            portfolio_scaffold,
            market_scaffold,
            history_cache=cache,
        )

        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=[instrument],
            recent_snapshots=[],
        )
        await cache.close()

        quote = context.quotes["us"][0]
        assert quote.price == 210.0
        assert quote.stale is True
        assert quote.source == "history_cache"
        quality = context.data_quality["quotes"]
        assert quality["status"] == "degraded"
        assert quality["freshness"] == "stale"
        assert quality["us_quotes"] == "single_source_failed"
        assert quality["by_market"]["us"]["status"] == "stale_fallback"
        assert "[stale历史收盘]" in context.raw_prompt_input

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

    def test_quote_quality_uses_oldest_as_of_per_market_and_counts_missing(
        self,
        mock_fetcher,
        mock_scaffolds,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        a_old = Instrument("000001", "平安银行", "a")
        a_missing = Instrument("000002", "万科", "a")
        us_newer = Instrument("AAPL", "Apple", "us")
        quotes = {
            "a": [
                Quote(a_old, price=10.0, as_of="2026-07-01T07:00:00+00:00"),
                Quote(a_missing, price=11.0, as_of=None),
            ],
            "us": [Quote(us_newer, price=210.0, as_of="2026-07-02T20:00:00+00:00")],
        }

        quality = builder._quote_quality(
            "2026-07-03T08:00:00+00:00",
            [a_old, a_missing, us_newer],
            quotes,
            [],
        )

        assert quality["as_of"] == "2026-07-01T07:00:00+00:00"
        assert quality["freshness"] == "old"
        assert quality["missing_as_of"] == 1
        assert quality["by_market"]["a"]["as_of"] == "2026-07-01T07:00:00+00:00"
        assert quality["by_market"]["us"]["as_of"] == "2026-07-02T20:00:00+00:00"

    def test_quote_quality_after_close_is_not_fresh(
        self,
        mock_fetcher,
        mock_scaffolds,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        instrument = Instrument("000001", "平安银行", "a")
        quotes = {
            "a": [Quote(instrument, price=10.0, as_of="2026-07-02T07:00:00+00:00")]
        }

        quality = builder._quote_quality(
            "2026-07-02T23:00:00+00:00",
            [instrument],
            quotes,
            [],
        )

        assert quality["freshness"] == "stale"

    def test_quote_quality_all_missing_as_of_is_unknown(
        self,
        mock_fetcher,
        mock_scaffolds,
    ):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        instrument = Instrument("000001", "平安银行", "a")

        quality = builder._quote_quality(
            "2026-07-02T23:00:00+00:00",
            [instrument],
            {"a": [Quote(instrument, price=10.0)]},
            [],
        )

        assert quality["as_of"] is None
        assert quality["freshness"] == "unknown"
        assert quality["missing_as_of"] == 1
        assert quality["by_market"]["a"]["as_of"] is None

    def test_quote_quality_reports_single_source_by_market(self, mock_scaffolds):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        fetcher = Mock()
        fetcher.is_single_source = Mock(
            side_effect=lambda market, primary: market in {"us", "crypto"}
        )
        builder = ContextBuilder(fetcher, portfolio_scaffold, market_scaffold)
        instruments = [
            Instrument("000001", "平安银行", "a"),
            Instrument("AAPL", "Apple", "us"),
            Instrument("BINANCE:BTCUSDT", "Bitcoin", "crypto"),
        ]
        quotes = {
            instrument.market: [Quote(instrument, price=10.0)]
            for instrument in instruments
        }
        degradation = [
            {"market": "a", "primary_provider": "tencent_a", "result": "success"},
            {"market": "us", "primary_provider": "finnhub", "result": "success"},
            {"market": "crypto", "primary_provider": "finnhub", "result": "success"},
        ]

        quality = builder._quote_quality(
            "2026-07-03T08:00:00+00:00", instruments, quotes, degradation
        )

        assert quality["by_market"]["a"]["single_source"] is False
        assert quality["by_market"]["us"]["single_source"] is True
        assert quality["by_market"]["crypto"]["single_source"] is True

    def test_quote_quality_reports_freshness_per_market(
        self,
        mock_fetcher,
        mock_scaffolds,
    ):
        """by_market 各市场应有独立 freshness，A 股当日前、US 前收不互相污染。"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        a_inst = Instrument("000001", "平安银行", "a")
        us_inst = Instrument("AAPL", "Apple", "us")
        # A 股当日盘中 14:45 CST = 06:45 UTC，报价 06:30 UTC（15分钟前→fresh）
        # US 前日收盘 ~20:00 UTC（约 11h 前→stale/old）
        quotes = {
            "a": [Quote(a_inst, price=10.0, as_of="2026-07-15T06:30:00+00:00")],
            "us": [Quote(us_inst, price=210.0, as_of="2026-07-14T20:00:00+00:00")],
        }
        quality = builder._quote_quality(
            "2026-07-15T06:45:00+00:00",
            [a_inst, us_inst],
            quotes,
            [],
        )
        # 全局 freshness 被最老的 US 拖慢
        assert quality["freshness"] in ("stale", "old")
        # A 股市场独立 freshness 应为 fresh
        assert quality["by_market"]["a"]["freshness"] == "fresh"
        # US 市场独立 freshness 应为 stale 或 old
        assert quality["by_market"]["us"]["freshness"] in ("stale", "old")


class TestAnalysisContextSerialization:
    async def test_to_dict_includes_schema_v10_decision_inputs_and_data_quality(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_assets,
        sample_instruments,
        temp_dir,
    ):
        """to_dict 输出 schema v10、前瞻输入、指标、建议与 data_quality。"""
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
        assert data["schema_version"] == 12
        assert "market_events" in data
        assert "news_digest" in data
        assert "technical_indicators" in data
        assert "data_quality" in data
        assert data["recent_advice"] == []
        assert data["forecast_summary"] == {}
        # D0-1:data_points=1 → 单项 missing,聚合 missing(不再报假 ok)
        assert data["technical_indicators"]["a:000001"]["status"] == "missing"
        assert data["technical_indicators"]["a:000001"]["data_points"] == 1
        assert data["data_quality"]["technical_indicators"]["status"] == "missing"


class TestHistoryBackfillQuality:
    """D0-3:_history_backfill_quality 聚合语义(纯单元,不走 build)。"""

    def test_not_requested_when_empty_report(self, mock_fetcher, mock_scaffolds):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        node = builder._history_backfill_quality([])
        assert node["status"] == "not_requested"
        assert node["requested_count"] == 0
        assert node["ok_count"] == 0
        assert node["failed_count"] == 0
        assert node["items"] == []

    def test_ok_when_all_ok_or_skipped(self, mock_fetcher, mock_scaffolds):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        report = [
            {"symbol": "a:000300", "market": "a", "source": "eastmoney_kline",
             "rows": 60, "status": "ok", "error": None},
            {"symbol": "us:QQQ", "market": "us", "source": "yahoo_kline",
             "rows": 50, "status": "skipped_cached", "error": None},
        ]
        node = builder._history_backfill_quality(report)
        assert node["status"] == "ok"
        assert node["requested_count"] == 2
        assert node["ok_count"] == 1
        assert node["skipped_cached_count"] == 1
        assert node["failed_count"] == 0

    def test_partial_when_mixed(self, mock_fetcher, mock_scaffolds):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        report = [
            {"symbol": "a:000300", "market": "a", "source": "eastmoney_kline",
             "rows": 60, "status": "ok", "error": None},
            {"symbol": "us:QQQ", "market": "us", "source": "yahoo_kline",
             "rows": 0, "status": "failed", "error": "HTTPError: 429"},
        ]
        node = builder._history_backfill_quality(report)
        assert node["status"] == "partial"
        assert node["ok_count"] == 1
        assert node["failed_count"] == 1

    def test_failed_when_all_failed(self, mock_fetcher, mock_scaffolds):
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        report = [
            {"symbol": "a:000300", "market": "a", "source": "eastmoney_kline",
             "rows": 0, "status": "failed", "error": "Timeout"},
            {"symbol": "us:QQQ", "market": "us", "source": "yahoo_kline",
             "rows": 0, "status": "failed", "error": "Timeout"},
        ]
        node = builder._history_backfill_quality(report)
        assert node["status"] == "failed"
        assert node["ok_count"] == 0
        assert node["failed_count"] == 2

    async def test_build_emits_schema_v3_with_history_backfill(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_assets,
        sample_instruments,
    ):
        """未传 report 时 history_backfill=not_requested,data_quality schema=4。"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
        )
        assert context.data_quality["schema_version"] == 10
        assert "history_backfill" in context.data_quality
        assert context.data_quality["history_backfill"]["status"] == "not_requested"

    async def test_build_propagates_history_backfill_report(
        self,
        mock_fetcher,
        mock_scaffolds,
        sample_assets,
        sample_instruments,
    ):
        """D0-3: build() 传入 report,data_quality.history_backfill 反映聚合状态与 items"""
        portfolio_scaffold, market_scaffold = mock_scaffolds
        builder = ContextBuilder(mock_fetcher, portfolio_scaffold, market_scaffold)
        report = [
            {"symbol": "a:000001", "market": "a", "source": "eastmoney_kline",
             "rows": 60, "status": "ok", "error": None},
            {"symbol": "a:000002", "market": "a", "source": "eastmoney_kline",
             "rows": 0, "status": "failed", "error": "ConnectError"},
        ]
        context = await builder.build(
            assets=sample_assets,
            constraints={},
            profile={},
            instruments=sample_instruments,
            recent_snapshots=[],
            history_backfill_report=report,
        )
        node = context.data_quality["history_backfill"]
        assert node["status"] == "partial"
        assert node["ok_count"] == 1
        assert node["failed_count"] == 1
        assert len(node["items"]) == 2
