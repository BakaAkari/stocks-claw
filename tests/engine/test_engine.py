"""StocksEngine 测试 — 覆盖初始化、配置加载、健康检查、资产 CRUD

Mock 策略：patch load_engine_config 返回最小配置，避免依赖真实文件系统。
"""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from stocks.domain.models import (
    Classification,
    FinancialAsset,
    Holding,
    Instrument,
    Liquidity,
    Position,
    Quote,
    ValuationInput,
)
from stocks.engine import StocksEngine
from stocks.engine.exchange_rate import ConversionResult

# 最小配置，用于测试初始化
MINIMAL_CONFIG = {
    "paths": {
        "config_dir": None,
        "data_dir": None,
        "local_data_dir": None,
        "secret_dir": None,
    },
    "providers": {
        "tencent_a": {"enabled": True},
        "eastmoney_a": {"enabled": True},
        "finnhub": {"enabled": True},
        "binance": {"enabled": True},
    },
    "fetcher": {"max_retries": 1, "retry_delay": 1.0},
    "cache": {
        "enabled": True,
        "history_ttl": 7776000,
        "history_dir": None,
        "max_snapshots": 30,
        "save_to_file": True,
    },
    "macro": {"enabled": True, "static_config": {}},
    "calendar": {
        "enabled": False,
        "lookahead_days": 14,
        "earnings": {"enabled": False},
    },
    "llm": {
        "analysis_enabled": False,
        "analysis_model": "",
    },
    "logging": {"level": "INFO", "desensitize": True},
}


@pytest.fixture
def minimal_engine(tmp_path):
    """返回使用最小配置初始化的 StocksEngine，并清空已加载的真实数据"""
    config = deepcopy(MINIMAL_CONFIG)
    config["paths"]["local_data_dir"] = str(tmp_path / "local")
    with patch("stocks.engine.load_engine_config", return_value=config):
        engine = StocksEngine()
    # 清除可能从真实文件加载的数据，保证测试隔离性
    engine._assets = []
    engine._watchlist = []
    engine._sector_scan = []
    engine._constraints = {}
    engine._profile = {}
    return engine


# ------------------------------------------------------------------
# 初始化测试
# ------------------------------------------------------------------

class TestEngineInit:
    """Engine 初始化测试"""

    def test_sec_user_agent_falls_back_to_secret_file(self, tmp_path, monkeypatch):
        config = deepcopy(MINIMAL_CONFIG)
        secret_dir = tmp_path / "secret"
        secret_dir.mkdir()
        (secret_dir / "sec-user-agent.md").write_text(
            "stocks-claw test@example.com", encoding="utf-8"
        )
        config["paths"]["local_data_dir"] = str(tmp_path / "local")
        config["paths"]["secret_dir"] = str(secret_dir)
        config["filings"] = {"enabled": True, "sec": {"enabled": True}}
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)

        with patch("stocks.engine.load_engine_config", return_value=config):
            engine = StocksEngine()

        sec = next(
            provider
            for provider in engine.news_aggregator._providers
            if getattr(provider, "name", "") == "sec_edgar"
        )
        assert sec._config_error is None
        assert sec._headers["User-Agent"] == "stocks-claw test@example.com"

    def test_init_loads_minimal_config(self, minimal_engine):
        """最小配置下成功初始化"""
        assert minimal_engine._config is not None
        assert minimal_engine._config["fetcher"]["max_retries"] == 1

    def test_init_registers_providers(self, minimal_engine):
        """Provider 根据配置注册"""
        providers = minimal_engine.registry.all()
        names = [p.name for p in providers]
        assert "tencent_a" in names
        assert "eastmoney_a" in names
        assert "finnhub" in names
        assert "binance" in names

    def test_init_llm_disabled_by_config(self, minimal_engine):
        """配置中 LLM 禁用时，初始化后 LLM 模块禁用"""
        assert minimal_engine.llm_analysis.enabled is False

    def test_init_empty_assets(self, minimal_engine):
        """无资产文件时返回空列表"""
        assets = minimal_engine.load_assets()
        assert assets == []
        assert minimal_engine._assets == []

    def test_init_empty_watchlist(self, minimal_engine):
        """无 watchlist 文件时返回空列表"""
        assert minimal_engine._watchlist == []

    def test_init_empty_constraints(self, minimal_engine):
        """无约束文件时返回空字典"""
        assert minimal_engine._constraints == {}

    def test_init_fetcher_params(self, minimal_engine):
        """Fetcher 使用配置参数初始化"""
        assert minimal_engine.fetcher.max_retries == 1
        assert minimal_engine.fetcher.retry_delay == 1.0

    def test_init_applies_logging_config(self):
        config = deepcopy(MINIMAL_CONFIG)
        config["logging"] = {"level": "DEBUG", "desensitize": False}

        with patch("stocks.engine.load_engine_config", return_value=config):
            with patch("stocks.engine.setup_logging") as setup:
                StocksEngine()

        setup.assert_called_once_with(level="DEBUG", desensitize=False)

    def test_init_custom_config_dir(self, tmp_path):
        """自定义配置目录"""
        with patch("stocks.engine.load_engine_config", return_value=MINIMAL_CONFIG):
            engine = StocksEngine(config_dir=str(tmp_path))
        assert str(engine.config_dir) == str(tmp_path)

    def test_init_custom_data_dir(self, tmp_path):
        """自定义数据目录"""
        with patch("stocks.engine.load_engine_config", return_value=MINIMAL_CONFIG):
            engine = StocksEngine(data_dir=str(tmp_path))
        assert str(engine.data_dir) == str(tmp_path)


class TestEngineInitProviderDisabled:
    """Provider 禁用场景"""

    def test_tencent_disabled(self):
        config = deepcopy(MINIMAL_CONFIG)
        config["providers"] = {
            "tencent_a": {"enabled": False},
            "eastmoney_a": {"enabled": True},
            "finnhub": {"enabled": True},
            "binance": {"enabled": True},
        }
        with patch("stocks.engine.load_engine_config", return_value=config):
            engine = StocksEngine()

        names = [p.name for p in engine.registry.all()]
        assert "tencent_a" not in names
        assert "eastmoney_a" in names

    def test_all_providers_disabled(self):
        config = deepcopy(MINIMAL_CONFIG)
        config["providers"] = {
            "tencent_a": {"enabled": False},
            "eastmoney_a": {"enabled": False},
            "finnhub": {"enabled": False},
            "binance": {"enabled": False},
            "polygon": {"enabled": False},
        }
        with patch("stocks.engine.load_engine_config", return_value=config):
            engine = StocksEngine()

        assert engine.registry.all() == []


class TestEngineInitFetcherParams:
    """Fetcher 参数配置"""

    def test_custom_retry_params(self):
        config = deepcopy(MINIMAL_CONFIG)
        config["fetcher"] = {"max_retries": 5, "retry_delay": 3.0}
        with patch("stocks.engine.load_engine_config", return_value=config):
            engine = StocksEngine()

        assert engine.fetcher.max_retries == 5
        assert engine.fetcher.retry_delay == 3.0


# ------------------------------------------------------------------
# 健康检查测试
# ------------------------------------------------------------------

class TestHealthCheck:
    """health_check 接口测试"""

    def test_health_check_ok(self, minimal_engine):
        health = minimal_engine.health_check()
        assert health["status"] == "ok"
        assert len(health["providers"]) == 5
        assert health["assets_loaded"] == 0
        assert health["watchlist_loaded"] == 0
        assert health["llm_analysis_enabled"] is False

    def test_health_check_with_assets(self, minimal_engine):
        minimal_engine._assets = [
            FinancialAsset(name="测试", platform="x", amount=10000, asset_type="现金管理"),
        ]
        health = minimal_engine.health_check()
        assert health["assets_loaded"] == 1


# ------------------------------------------------------------------
# 资产 CRUD 测试
# ------------------------------------------------------------------

class TestAssetCRUD:
    """资产增删改测试"""

    def test_add_asset(self, minimal_engine, tmp_path):
        """添加资产并持久化"""
        minimal_engine._local_data_dir = tmp_path
        asset = FinancialAsset(name="新资产", platform="银行", amount=50000, asset_type="现金管理")
        minimal_engine.add_asset(asset)

        assert len(minimal_engine._assets) == 1
        assert minimal_engine._assets[0].name == "新资产"
        # 验证文件已写入
        assert (tmp_path / "financial_assets.json").exists()

    def test_remove_asset(self, minimal_engine, tmp_path):
        """移除资产"""
        minimal_engine._local_data_dir = tmp_path
        minimal_engine._assets = [
            FinancialAsset(name="保留", platform="x", amount=10000, asset_type="现金管理"),
            FinancialAsset(name="删除", platform="x", amount=20000, asset_type="现金管理"),
        ]
        result = minimal_engine.remove_asset("删除")

        assert result is True
        assert len(minimal_engine._assets) == 1
        assert minimal_engine._assets[0].name == "保留"

    def test_remove_asset_not_found(self, minimal_engine):
        """移除不存在的资产"""
        result = minimal_engine.remove_asset("不存在")
        assert result is False

    def test_update_asset(self, minimal_engine, tmp_path):
        """更新资产"""
        minimal_engine._local_data_dir = tmp_path
        minimal_engine._assets = [
            FinancialAsset(name="测试", platform="x", amount=10000, asset_type="现金管理"),
        ]
        result = minimal_engine.update_asset("测试", amount=20000)

        assert result is True
        assert minimal_engine._assets[0].amount == 20000

    def test_update_asset_not_found(self, minimal_engine):
        """更新不存在的资产"""
        result = minimal_engine.update_asset("不存在", amount=100)
        assert result is False

    def test_usd_asset_round_trip_preserves_original_value(self, minimal_engine, tmp_path):
        """加载、保存、重载后不得用 CNY 派生值覆盖 USD 原值。"""
        minimal_engine._local_data_dir = tmp_path
        asset_path = tmp_path / "financial_assets.json"
        asset_path.write_text(
            json.dumps([
                {
                    "name": "美元现金",
                    "platform": "IBKR",
                    "amount": 100.0,
                    "asset_type": "现金",
                    "currency": "USD",
                }
            ]),
            encoding="utf-8",
        )

        conversion = ConversionResult(700.0, 7.0, "cache", "ok")
        with patch("stocks.engine.convert_to_cny", return_value=conversion):
            minimal_engine._assets = minimal_engine._load_assets_from_file()
            loaded = minimal_engine._assets[0]
            assert loaded.amount == 100.0
            assert loaded.currency == "USD"
            assert loaded.amount_cny == 700.0

            minimal_engine._save_assets()
            stored = json.loads(asset_path.read_text(encoding="utf-8"))[0]
            assert stored["amount"] == 100.0
            assert stored["currency"] == "USD"
            assert "amount_cny" not in stored

            reloaded = minimal_engine._load_assets_from_file()[0]
            assert reloaded.amount == 100.0
            assert reloaded.currency == "USD"
            assert reloaded.amount_cny == 700.0

    def test_asset_instrument_mapping_legacy_and_round_trip(
        self,
        minimal_engine,
        tmp_path,
    ):
        """旧资产缺映射字段可加载；新映射字段保存/重载不丢失。"""
        minimal_engine._local_data_dir = tmp_path
        asset_path = tmp_path / "financial_assets.json"
        asset_path.write_text(
            json.dumps([
                {
                    "name": "旧现金",
                    "platform": "银行",
                    "amount": 1000.0,
                    "asset_type": "现金",
                    "currency": "CNY",
                }
            ]),
            encoding="utf-8",
        )

        legacy = minimal_engine._load_assets_from_file()[0]
        assert legacy.instrument_key is None
        assert legacy.quantity is None
        assert legacy.tradable is None

        minimal_engine._assets = [
            legacy,
            FinancialAsset(
                name="科创50ETF",
                platform="券商",
                amount=3000,
                asset_type="股票ETF",
                currency="cny",
                instrument_key="A:588000",
                quantity=1800,
                tradable=True,
            ),
        ]
        minimal_engine._save_assets()
        stored = json.loads(asset_path.read_text(encoding="utf-8"))
        assert "instrument_key" not in stored[0]
        assert stored[1]["currency"] == "CNY"
        assert stored[1]["instrument_key"] == "a:588000"
        assert stored[1]["quantity"] == 1800.0
        assert stored[1]["tradable"] is True

        reloaded = minimal_engine._load_assets_from_file()[1]
        assert reloaded.instrument_key == "a:588000"
        assert reloaded.quantity == 1800.0
        assert reloaded.tradable is True


class TestAssetV2RuntimeContext:
    async def test_detailed_holding_is_auto_included_in_quote_universe(
        self,
        minimal_engine,
    ):
        position = Position(
            position_id="broker_588000",
            account_id="broker",
            display_name="科创50ETF",
            currency="CNY",
            classification=Classification(
                asset_class="equity",
                product_type="exchange_traded_fund",
                exposure_tags=["star50"],
            ),
            instrument={"instrument_key": "a:588000"},
            holding=Holding(quantity=1800, unit="share"),
            valuation_input=ValuationInput(method="market_quote"),
            liquidity=Liquidity(tradable=True, rebalance_eligible=True, tier="t1"),
        )
        minimal_engine._asset_schema_version = 2
        minimal_engine._asset_positions_v2 = [position]
        minimal_engine._assets = []
        minimal_engine._watchlist = []
        minimal_engine._history_warmed = True
        minimal_engine.macro_provider = None
        minimal_engine.context_builder.macro_provider = None
        quote = Quote(
            instrument=Instrument(code="588000", name="科创50ETF", market="a"),
            price=2.0,
            source="fixture",
            as_of="2026-07-04T00:00:00+00:00",
        )
        minimal_engine.fetcher.fetch_quotes = AsyncMock(return_value={"a": [quote]})

        context = await minimal_engine.build_context(
            include_news=False,
            include_quotes=True,
            include_history=False,
        )

        requested = minimal_engine.fetcher.fetch_quotes.call_args.args[0]
        assert [f"{item.market}:{item.code}" for item in requested] == ["a:588000"]
        assert context.data_quality["auto_included_holdings"]["items"] == ["a:588000"]
        assert context.position_valuations[0]["market_value_cny"] == 3600.0

    def test_portfolio_uses_cny_valuation(self, minimal_engine):
        assets = [
            FinancialAsset(
                name="美元现金",
                platform="IBKR",
                amount=100.0,
                asset_type="现金",
                currency="USD",
                amount_cny=700.0,
            ),
            FinancialAsset(
                name="人民币股票",
                platform="券商",
                amount=300.0,
                asset_type="股票",
                currency="CNY",
                amount_cny=300.0,
            ),
        ]

        mapping = minimal_engine.analyze_portfolio(assets)

        assert mapping.ratios["现金"] == 0.7
        assert mapping.ratios["权益"] == 0.3

    def test_legacy_converted_asset_is_recovered(self, minimal_engine, tmp_path):
        """旧版写进 notes 的原始 USD 数据应在加载时恢复。"""
        minimal_engine._local_data_dir = tmp_path
        (tmp_path / "financial_assets.json").write_text(
            json.dumps([
                {
                    "name": "美元现金",
                    "platform": "IBKR",
                    "amount": 700.0,
                    "asset_type": "现金",
                    "notes": "USD现金余额 | 原始: 100.0 USD (汇率 7.0000)",
                    "currency": "CNY",
                }
            ]),
            encoding="utf-8",
        )

        conversion = ConversionResult(680.0, 6.8, "cache", "ok")
        with patch("stocks.engine.convert_to_cny", return_value=conversion):
            recovered = minimal_engine._load_assets_from_file()[0]

        assert recovered.amount == 100.0
        assert recovered.currency == "USD"
        assert recovered.amount_cny == 680.0
        assert recovered.notes == "USD现金余额"


# ------------------------------------------------------------------
# 组合分析测试
# ------------------------------------------------------------------

class TestPortfolioAnalysis:
    """组合分析接口测试"""

    def test_analyze_portfolio_empty(self, minimal_engine):
        """空资产组合分析"""
        mapping = minimal_engine.analyze_portfolio([])
        assert mapping.buckets == {}
        assert mapping.ratios == {}

    def test_analyze_portfolio_with_assets(self, minimal_engine):
        """有资产时分析"""
        assets = [
            FinancialAsset(name="现金", platform="银行", amount=100000, asset_type="现金管理"),
            FinancialAsset(name="ETF", platform="券商", amount=200000, asset_type="股票ETF"),
        ]
        mapping = minimal_engine.analyze_portfolio(assets)
        assert "现金" in mapping.ratios
        assert "权益" in mapping.ratios

    def test_detect_drift_no_constraints(self, minimal_engine):
        """无约束时无偏离"""
        from stocks.engine.scaffolds import PortfolioScaffold
        scaffold = PortfolioScaffold()
        mapping = scaffold.build([], {})
        drift = minimal_engine.detect_drift(mapping)
        assert drift == []


# ------------------------------------------------------------------
# 新模块集成测试（Phase 2）
# ------------------------------------------------------------------

class TestNewModuleIntegration:
    """新模块集成测试 — HistoryCache / NewsAggregator / MacroProvider"""

    def test_history_cache_initialized(self, minimal_engine):
        """默认启用 history cache"""
        assert minimal_engine.history_cache is not None
        assert minimal_engine.history_cache._base_dir == minimal_engine._local_data_dir / "history"

    def test_history_cache_custom_dir(self, tmp_path):
        """cache.history_dir 显式配置时优先使用指定目录"""
        config = deepcopy(MINIMAL_CONFIG)
        custom_history_dir = tmp_path / "runtime-history"
        config["cache"]["history_dir"] = str(custom_history_dir)
        with patch("stocks.engine.load_engine_config", return_value=config):
            engine = StocksEngine()

        assert engine.history_cache is not None
        assert engine.history_cache._base_dir == custom_history_dir

    def test_history_cache_disabled(self):
        """cache 禁用时 history_cache 为 None"""
        from copy import deepcopy
        config = deepcopy(MINIMAL_CONFIG)
        config["cache"]["enabled"] = False
        with patch("stocks.engine.load_engine_config", return_value=config):
            engine = StocksEngine()
        assert engine.history_cache is None

    def test_news_aggregator_initialized(self, minimal_engine):
        """NewsAggregator 正确初始化"""
        assert minimal_engine.news_aggregator is not None
        assert len(minimal_engine.news_aggregator._providers) == 3

    def test_macro_provider_initialized(self, minimal_engine):
        """默认启用 macro provider"""
        assert minimal_engine.macro_provider is not None

    def test_macro_provider_disabled(self):
        """macro 禁用时 macro_provider 为 None"""
        from copy import deepcopy
        config = deepcopy(MINIMAL_CONFIG)
        config["macro"]["enabled"] = False
        with patch("stocks.engine.load_engine_config", return_value=config):
            engine = StocksEngine()
        assert engine.macro_provider is None

    def test_context_builder_has_new_modules(self, minimal_engine):
        """ContextBuilder 接收了新模块"""
        assert minimal_engine.context_builder.history_cache is minimal_engine.history_cache
        assert minimal_engine.context_builder.macro_provider is minimal_engine.macro_provider


class TestHealthCheckNewModules:
    """健康检查新字段测试"""

    def test_health_check_new_fields(self, minimal_engine):
        """health_check 包含新组件状态"""
        health = minimal_engine.health_check()
        assert "history_cache_enabled" in health
        assert health["history_cache_enabled"] is True
        assert "news_providers" in health
        assert health["news_providers"] == 3
        assert "macro_provider_enabled" in health
        assert health["macro_provider_enabled"] is True

    def test_health_check_cache_disabled(self):
        from copy import deepcopy
        config = deepcopy(MINIMAL_CONFIG)
        config["cache"]["enabled"] = False
        config["macro"]["enabled"] = False
        with patch("stocks.engine.load_engine_config", return_value=config):
            engine = StocksEngine()
        health = engine.health_check()
        assert health["history_cache_enabled"] is False
        assert health["macro_provider_enabled"] is False
        assert health["news_providers"] == 3  # news 不受 cache 影响


class TestFetchNewsIntegration:
    """fetch_news 使用 NewsAggregator 测试"""

    async def test_fetch_news_uses_aggregator(self, minimal_engine):
        """fetch_news 调用 NewsAggregator 而非 fetcher"""
        from datetime import datetime, timezone

        from stocks.domain.models import NewsItem

        # Mock NewsAggregator.fetch
        mock_news = [
            NewsItem(
                title="Test",
                url="https://example.com",
                source_name="test",
                source_type="test",
                published_at=datetime.now(timezone.utc),
                summary="Summary",
                language="zh",
            )
        ]
        minimal_engine.news_aggregator.fetch = AsyncMock(return_value=mock_news)

        result = await minimal_engine.fetch_news(limit=5)
        assert len(result) == 1
        assert result[0].title == "Test"
        minimal_engine.news_aggregator.fetch.assert_called_once_with(max_items=5, sources=None)

    async def test_fetch_news_empty(self, minimal_engine):
        """NewsAggregator 返回空列表"""
        minimal_engine.news_aggregator.fetch = AsyncMock(return_value=[])
        result = await minimal_engine.fetch_news()
        assert result == []

    async def test_periodic_history_refresh_rewarms_scan_pool(
        self, minimal_engine, monkeypatch
    ):
        """P2-2: 首次 warm 之后,scan 池标的 K 线可能停在 warm 首次拉取日。
        定期刷新(history_refresh_interval 到点)应重新调用 warm_history_cache,
        让过时 K 线被 stale_days 判定强制重拉,而不是永远 skipped_cached。"""
        from datetime import timedelta

        import stocks.engine as engine_mod

        # 首次 warm 已完成
        minimal_engine._history_warmed = True
        minimal_engine._history_last_refresh = None  # 从未刷新 → 到点
        # 缩短刷新间隔,保证本次 build 判定 due
        minimal_engine._history_refresh_interval = timedelta(hours=1)

        calls: list[list] = []

        async def fake_warm(cache, provider, instruments, lookback_days=60):
            calls.append(list(instruments))
            return [{"symbol": f"{i.market}:{i.code}", "status": "skipped_cached",
                     "rows": 60} for i in instruments]

        # __init__ 通过 from ... import warm_history_cache 绑定符号,
        # 必须 patch engine 模块属性而不是 history_provider 模块。
        monkeypatch.setattr(engine_mod, "warm_history_cache", fake_warm)

        # 需要一个带 scan_instruments 的 build;构造最小 scan 池
        scan_inst = Instrument(code="513770", name="港股互联网ETF", market="a")
        minimal_engine._sector_scan = [scan_inst]

        # 最小化外部依赖:不拉 news/quotes,走 build_context
        minimal_engine.macro_provider = None
        minimal_engine.context_builder.macro_provider = None
        minimal_engine.news_aggregator.fetch = AsyncMock(return_value=[])
        minimal_engine.fetcher.fetch_quotes = AsyncMock(return_value={})
        minimal_engine.fetcher.fetch_history = AsyncMock(return_value=[])

        await minimal_engine.build_context(
            include_news=False, include_quotes=False, include_history=False,
        )

        # 定期刷新应触发 warm(至少一次),且目标是 scan 池标的
        assert len(calls) >= 1
        flat = [f"{i.market}:{i.code}" for sub in calls for i in sub]
        assert "a:513770" in flat
        # 刷新成功后 _history_last_refresh 被更新
        assert minimal_engine._history_last_refresh is not None
