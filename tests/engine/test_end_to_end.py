"""端到端流水线测试 — 验证完整 build_context 链路

测试策略：
- Mock 所有外部依赖（fetcher, news_aggregator, macro_provider），但保持内部模块真实协作
- 预热 HistoryCache 使技术指标计算有意义
- 验证 AnalysisContext 结构完整、raw_prompt 包含所有段落、JSON 可序列化
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest

from stocks.domain.models import (
    FinancialAsset,
    Instrument,
    NewsItem,
    Quote,
)
from stocks.engine import StocksEngine
from tests.engine.test_engine import MINIMAL_CONFIG

# 预设测试数据
SAMPLE_INSTRUMENT = Instrument(code="000001", name="平安银行", market="a")
SAMPLE_ASSETS = [
    FinancialAsset(name="股票基金", platform="支付宝", amount=50000, asset_type="equity", confirmed=True),
    FinancialAsset(name="余额宝", platform="支付宝", amount=30000, asset_type="cash", confirmed=True),
]
SAMPLE_CONSTRAINTS = {
    "权益": {"min": 0.4, "max": 0.7},
    "现金": {"min": 0.2, "max": 0.5},
}
SAMPLE_PROFILE = {"risk_tolerance": "moderate", "investment_horizon": "medium"}
SAMPLE_WATCHLIST = [SAMPLE_INSTRUMENT]

SAMPLE_QUOTE = Quote(
    instrument=SAMPLE_INSTRUMENT,
    price=10.5,
    change=0.3,
    pct_change=2.94,
    volume_lot=1000000,
    open_price=10.2,
    high=10.6,
    low=10.1,
    prev_close=10.2,
)

SAMPLE_NEWS = [
    NewsItem(
        title="测试新闻标题",
        url="https://example.com/news/1",
        source_name="36kr",
        source_type="rss",
        published_at=datetime.now(timezone.utc),
        summary="这是一条测试新闻摘要",
        language="zh",
    )
]

SAMPLE_MACRO = Mock()
SAMPLE_MACRO.to_dict = Mock(return_value={
    "vix": 22.5,
    "usd_cny": 7.25,
    "us_10y_yield": 4.2,
    "dxy": 104.0,
    "gold": 2350.0,
    "crude_oil": 78.5,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "source": "yahoo_finance",
    "errors": {},
})


def _build_e2e_config():
    """构建端到端测试专用配置（LLM 禁用以避免网络依赖）"""
    config = deepcopy(MINIMAL_CONFIG)
    config["cache"]["enabled"] = True
    config["macro"]["enabled"] = True
    config["llm"]["analysis_enabled"] = False
    return config


@pytest.fixture
def e2e_engine(tmp_path):
    """返回预配置的端到端测试 Engine（Mock 外部依赖，保留内部模块）"""
    config = _build_e2e_config()
    config["paths"]["local_data_dir"] = str(tmp_path / "local")
    with patch("stocks.engine.load_engine_config", return_value=config):
        engine = StocksEngine()

    # 清空真实文件加载的数据，注入测试数据
    engine._assets = list(SAMPLE_ASSETS)
    engine._constraints = dict(SAMPLE_CONSTRAINTS)
    engine._profile = dict(SAMPLE_PROFILE)
    engine._watchlist = list(SAMPLE_WATCHLIST)

    # Mock 外部依赖
    engine.fetcher.fetch_quotes = AsyncMock(return_value={
        "a": [SAMPLE_QUOTE],
    })
    engine.news_aggregator.fetch = AsyncMock(return_value=list(SAMPLE_NEWS))
    engine.macro_provider.fetch = AsyncMock(return_value=SAMPLE_MACRO)

    yield engine

    # 清理：关闭 history_cache
    if engine.history_cache:
        import asyncio
        try:
            asyncio.get_running_loop().run_until_complete(engine.history_cache.close())
        except RuntimeError:
            pass


@pytest.fixture
async def e2e_engine_with_history(e2e_engine):
    """预热 HistoryCache 使技术指标计算有意义(D0-1:MACD 需 ≥35 bars 才可用,写 60 天使核心指标全部 ok)"""
    engine = e2e_engine
    if engine.history_cache:
        # 写入 60 天历史数据(足以让 MACD/Bollinger/RSI/MA20 全部计算成功 → status=ok)
        import pandas as pd
        records = []
        base = datetime.now(timezone.utc)
        for i in range(60):
            records.append({
                "timestamp": base - timedelta(days=i),
                "code": SAMPLE_INSTRUMENT.code,
                "name": SAMPLE_INSTRUMENT.name,
                "market": SAMPLE_INSTRUMENT.market,
                "price": 10.0 + i * 0.05,
                "open_price": 10.0 + i * 0.05,
                "high": 10.0 + i * 0.05 + 0.2,
                "low": 10.0 + i * 0.05 - 0.1,
                "prev_close": 10.0 + (i - 1) * 0.05 if i > 0 else 10.0,
                "volume_lot": 1_000_000,
            })
        df = pd.DataFrame(records)
        await engine.history_cache.warm(SAMPLE_INSTRUMENT, df)
    return engine


# ------------------------------------------------------------------
# 端到端构建测试
# ------------------------------------------------------------------

class TestBuildContextEndToEnd:
    async def test_build_context_returns_analysis_context(self, e2e_engine):
        """build_context 返回正确的 AnalysisContext 类型"""
        context = await e2e_engine.build_context()

        assert context is not None
        assert context.asset_count == 2
        assert len(context.assets) == 2
        assert context.generated_at != ""

    async def test_build_context_contains_quotes(self, e2e_engine):
        """quotes 包含行情数据"""
        context = await e2e_engine.build_context()

        assert "a" in context.quotes
        assert len(context.quotes["a"]) == 1
        q = context.quotes["a"][0]
        assert q.price == 10.5
        assert q.pct_change == 2.94

    async def test_build_context_contains_news(self, e2e_engine):
        """news 包含新闻数据"""
        context = await e2e_engine.build_context()

        assert context.news_count == 1
        assert len(context.news) == 1
        assert context.news[0].title == "测试新闻标题"
        assert len(context.market_events) == 1
        assert context.news_digest["event_count"] == 1

    async def test_second_build_contains_previous_snapshot(self, e2e_engine):
        first = await e2e_engine.build_context(include_news=False, include_quotes=False)
        second = await e2e_engine.build_context(include_news=False, include_quotes=False)

        assert first.recent_snapshots == []
        assert len(second.recent_snapshots) == 1
        assert second.recent_snapshots[0]["asset_count"] == 2
        assert "【上次快照】" in second.raw_prompt_input

    async def test_build_context_contains_recent_advice(self, e2e_engine):
        first = await e2e_engine.build_context(include_news=False, include_quotes=False)
        assert first.recent_advice == []

        e2e_engine.save_advice({
            "instruments": [{"market": "a", "code": "000001", "name": "平安银行"}],
            "direction": {"a:000001": "watch"},
            "rationale_summary": "现金占比较高，平安银行继续观察。",
            "based_on": ["quotes", "portfolio", "profile"],
            "boundary": [
                {"type": "fact", "text": "现金占比较高"},
                {"type": "inference", "text": "平安银行继续观察"},
            ],
        })

        second = await e2e_engine.build_context(include_news=False, include_quotes=False)

        assert len(second.recent_advice) == 1
        assert second.recent_advice[0]["direction"]["a:000001"] == "watch"
        assert second.recent_advice[0]["performance"][0]["status"] == "no_data"
        assert "【上次建议】" in second.raw_prompt_input
        assert "平安银行继续观察" in second.raw_prompt_input
        assert "status: no_data" in second.raw_prompt_input

    async def test_build_context_contains_macro(self, e2e_engine):
        """macro_snapshot 包含宏观数据"""
        context = await e2e_engine.build_context()

        assert context.macro_snapshot is not None
        assert context.macro_snapshot["vix"] == 22.5
        assert context.macro_snapshot["usd_cny"] == 7.25

    async def test_build_context_portfolio_mapping(self, e2e_engine):
        """portfolio_mapping 正确计算"""
        context = await e2e_engine.build_context()

        assert "权益" in context.portfolio_mapping.ratios
        assert "现金" in context.portfolio_mapping.ratios
        total = context.portfolio_mapping.ratios["权益"] + context.portfolio_mapping.ratios["现金"]
        assert abs(total - 1.0) < 0.01

    async def test_build_context_drift_checks(self, e2e_engine):
        """drift_checks 正确计算"""
        context = await e2e_engine.build_context()

        # equity 比例 = 50000/80000 = 0.625，在 [0.4, 0.7] 范围内
        equity_drift = [d for d in context.drift_checks if d.bucket == "权益"]
        assert len(equity_drift) == 1
        assert equity_drift[0].status == "within_range"

    async def test_build_context_raw_prompt_not_empty(self, e2e_engine):
        """raw_prompt 非空"""
        context = await e2e_engine.build_context()
        assert len(context.raw_prompt_input) > 200


# ------------------------------------------------------------------
# raw_prompt 内容验证
# ------------------------------------------------------------------

class TestRawPromptContent:
    async def test_raw_prompt_contains_market_section(self, e2e_engine):
        """raw_prompt 包含市场行情段落"""
        context = await e2e_engine.build_context()
        assert "【市场行情与技术指标】" in context.raw_prompt_input

    async def test_raw_prompt_contains_macro_section(self, e2e_engine):
        """raw_prompt 包含宏观环境段落"""
        context = await e2e_engine.build_context()
        assert "【宏观环境】" in context.raw_prompt_input

    async def test_raw_prompt_contains_news_section(self, e2e_engine):
        """raw_prompt 包含新闻段落"""
        context = await e2e_engine.build_context()
        assert "【相关新闻】" in context.raw_prompt_input

    async def test_raw_prompt_contains_quote_data(self, e2e_engine):
        """raw_prompt 包含标的行情数据"""
        context = await e2e_engine.build_context()
        assert "平安银行" in context.raw_prompt_input
        assert "000001" in context.raw_prompt_input

    async def test_raw_prompt_contains_macro_data(self, e2e_engine):
        """raw_prompt 包含宏观数据值"""
        context = await e2e_engine.build_context()
        assert "VIX 恐慌指数" in context.raw_prompt_input

    async def test_raw_prompt_contains_news_data(self, e2e_engine):
        """raw_prompt 包含新闻标题"""
        context = await e2e_engine.build_context()
        assert "测试新闻标题" in context.raw_prompt_input

    async def test_raw_prompt_with_indicators(self, e2e_engine_with_history):
        """有历史数据时 raw_prompt 包含技术指标"""
        context = await e2e_engine_with_history.build_context()

        assert "【市场行情与技术指标】" in context.raw_prompt_input
        # 数据点足够时，指标应出现在 quote 行下方
        assert "平安银行" in context.raw_prompt_input
        # 检查 quotes 对象有 indicators
        q = context.quotes["a"][0]
        assert q.indicators is not None
        assert q.indicators.get("data_points") >= 60
        # MA、RSI、MACD、Bollinger 应有值（60条数据满足 MACD 26+9 要求）
        assert q.indicators.get("ma_5") is not None
        assert q.indicators.get("rsi_14") is not None
        assert context.technical_indicators["a:000001"]["status"] == "ok"
        assert context.technical_indicators["a:000001"]["data_points"] >= 60


# ------------------------------------------------------------------
# JSON 序列化验证
# ------------------------------------------------------------------

class TestJSONSerialization:
    async def test_to_dict_serializable(self, e2e_engine):
        """AnalysisContext.to_dict() 返回可 JSON 序列化的字典"""
        context = await e2e_engine.build_context()
        d = context.to_dict()

        # 验证无异常序列化
        json_str = json.dumps(d, ensure_ascii=False, default=str)
        assert len(json_str) > 100

        # 反序列化验证
        parsed = json.loads(json_str)
        assert parsed["asset_count"] == 2
        assert parsed["schema_version"] == 6
        assert "quotes" in parsed
        assert "a" in parsed["quotes"]
        assert parsed["news_count"] == 1
        assert "market_events" in parsed
        assert parsed["news_digest"]["event_count"] == 1
        assert parsed["macro_snapshot"] is not None
        assert "technical_indicators" in parsed
        # D0-1:e2e_engine 无历史预热,SAMPLE_QUOTE 不携带 indicators → missing 是正确判级
        assert parsed["technical_indicators"]["a:000001"]["status"] == "missing"
        assert "data_quality" in parsed
        assert "recent_advice" in parsed
        assert parsed["data_quality"]["quotes"]["status"] == "ok"
        assert parsed["data_quality"]["news"]["status"] == "ok"
        assert parsed["data_quality"]["macro"]["status"] == "ok"
        assert parsed["data_quality"]["market_events"]["status"] == "ok"

    async def test_quotes_with_indicators_serializable(self, e2e_engine_with_history):
        """带 indicators 的 Quote 可 JSON 序列化"""
        context = await e2e_engine_with_history.build_context()
        d = context.to_dict()

        quote_dict = d["quotes"]["a"][0]
        assert "indicators" in quote_dict
        json_str = json.dumps(quote_dict, ensure_ascii=False, default=str)
        assert "indicators" in json_str

    async def test_top_level_indicators_serializable(self, e2e_engine_with_history):
        """顶层 technical_indicators 可 JSON 序列化，方便 Agent 结构化读取"""
        context = await e2e_engine_with_history.build_context()
        d = context.to_dict()

        indicators = d["technical_indicators"]["a:000001"]
        assert indicators["status"] == "ok"
        assert indicators["source"] == "history_cache"
        json_str = json.dumps(d["technical_indicators"], ensure_ascii=False, default=str)
        assert "history_cache" in json_str

    async def test_data_quality_serializable(self, e2e_engine_with_history):
        """data_quality 可 JSON 序列化，包含行情/新闻/宏观/指标状态"""
        context = await e2e_engine_with_history.build_context()
        d = context.to_dict()

        quality = d["data_quality"]
        assert quality["schema_version"] == 3
        assert quality["quotes"]["item_count"] == 1
        assert quality["news"]["sources"] == {"rss:36kr": 1}
        assert quality["market_events"]["event_count"] == 1
        assert quality["macro"]["source"] == "yahoo_finance"
        assert quality["technical_indicators"]["status"] == "ok"
        json_str = json.dumps(quality, ensure_ascii=False, default=str)
        assert "technical_indicators" in json_str

    async def test_macro_snapshot_serializable(self, e2e_engine):
        """macro_snapshot 可 JSON 序列化"""
        context = await e2e_engine.build_context()
        d = context.to_dict()

        assert "macro_snapshot" in d
        assert d["macro_snapshot"]["vix"] == 22.5


# ------------------------------------------------------------------
# 降级与容错测试
# ------------------------------------------------------------------

class TestDegradation:
    async def test_macro_provider_failure_does_not_block(self, e2e_engine):
        """宏观数据失败不阻断整体流程"""
        e2e_engine.macro_provider.fetch = AsyncMock(side_effect=Exception("API down"))

        context = await e2e_engine.build_context()
        assert context is not None
        assert context.macro_snapshot is None
        assert context.data_quality["macro"]["status"] == "missing"
        assert "API down" in context.data_quality["macro"]["errors"]["provider"]
        assert "【宏观环境】" not in context.raw_prompt_input

    async def test_news_fetch_failure_does_not_block(self, e2e_engine):
        """新闻获取失败不阻断整体流程"""
        e2e_engine.news_aggregator.fetch = AsyncMock(side_effect=Exception("RSS down"))

        context = await e2e_engine.build_context()
        assert context is not None
        assert context.news_count == 0
        assert "暂无新闻" in context.raw_prompt_input

    async def test_quotes_fetch_failure_does_not_block(self, e2e_engine):
        """行情获取失败不阻断整体流程"""
        e2e_engine.fetcher.fetch_quotes = AsyncMock(return_value={})

        context = await e2e_engine.build_context()
        assert context is not None
        assert context.quotes == {}
        assert "暂无行情数据" in context.raw_prompt_input

    async def test_empty_assets_still_works(self, e2e_engine):
        """空资产时仍能构建 context"""
        e2e_engine._assets = []
        e2e_engine._constraints = {}

        context = await e2e_engine.build_context()
        assert context is not None
        assert context.asset_count == 0
        assert context.raw_prompt_input != ""


class TestHistoryBackfillCooldown:
    """D0-3:历史回填失败后进入冷却,冷却期不再打上游;冷却过期后允许重试。"""

    async def test_backfill_failure_sets_cooldown_and_reports_failed(self, e2e_engine):
        """全部失败时:_history_warm_last_failed_at 被设置,data_quality.history_backfill.status=failed"""
        # 缩短冷却便于验证过期分支
        e2e_engine._history_warm_retry_cooldown = timedelta(milliseconds=50)

        failed_report = [
            {"symbol": f"{inst.market}:{inst.code}", "market": inst.market,
             "source": "eastmoney_kline" if inst.market == "a" else "yahoo_kline",
             "rows": 0, "status": "failed", "error": "HTTPError: 429"}
            for inst in e2e_engine._watchlist
        ]

        with patch(
            "stocks.engine.warm_history_cache",
            new=AsyncMock(return_value=failed_report),
        ) as mocked:
            context = await e2e_engine.build_context()
            assert mocked.await_count == 1
            assert e2e_engine._history_warmed is False
            assert e2e_engine._history_warm_last_failed_at is not None
            node = context.data_quality["history_backfill"]
            assert node["status"] == "failed"
            assert node["failed_count"] == len(failed_report)

            # 冷却期内再次 build:不再调用 warm_history_cache
            context2 = await e2e_engine.build_context()
            assert mocked.await_count == 1
            # 报告字段沿用上次;data_quality 节点仍反映"上次全失败"的历史
            assert context2.data_quality["history_backfill"]["status"] == "failed"

    async def test_cooldown_expiry_allows_retry(self, e2e_engine):
        """冷却过期后允许重试;成功后 _history_warmed=True 且不再打上游"""
        e2e_engine._history_warm_retry_cooldown = timedelta(milliseconds=1)

        ok_report = [
            {"symbol": f"{inst.market}:{inst.code}", "market": inst.market,
             "source": "eastmoney_kline" if inst.market == "a" else "yahoo_kline",
             "rows": 60, "status": "ok", "error": None}
            for inst in e2e_engine._watchlist
        ]

        # 让上一次失败在遥远过去,冷却肯定过期
        e2e_engine._history_warm_last_failed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        e2e_engine._history_warmed = False

        with patch(
            "stocks.engine.warm_history_cache",
            new=AsyncMock(return_value=ok_report),
        ) as mocked:
            context = await e2e_engine.build_context()
            assert mocked.await_count == 1  # 冷却已过,重新尝试
            assert e2e_engine._history_warmed is True
            assert e2e_engine._history_warm_last_failed_at is None
            assert context.data_quality["history_backfill"]["status"] == "ok"

            # 已 warmed,再次 build 不再调用
            await e2e_engine.build_context()
            assert mocked.await_count == 1
