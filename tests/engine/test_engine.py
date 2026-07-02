"""StocksEngine 测试 — 覆盖初始化、配置加载、健康检查、资产 CRUD

Mock 策略：patch load_engine_config 返回最小配置，避免依赖真实文件系统。
"""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from stocks.domain.models import FinancialAsset
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
    },
    "fetcher": {"max_retries": 1, "retry_delay": 1.0},
    "cache": {
        "enabled": True,
        "history_ttl": 7776000,
        "history_dir": None,
        "max_snapshots": 30,
        "save_to_file": True,
    },
    "news_sources": {
        "rss": ["https://www.36kr.com/feed"],
        "max_source_items": 20,
    },
    "macro": {"enabled": True, "static_config": {}},
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
    engine._constraints = {}
    engine._profile = {}
    return engine


# ------------------------------------------------------------------
# 初始化测试
# ------------------------------------------------------------------

class TestEngineInit:
    """Engine 初始化测试"""

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
        assert len(health["providers"]) == 3
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
        assert len(minimal_engine.news_aggregator._providers) == 1

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
        assert health["news_providers"] == 1
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
        assert health["news_providers"] == 1  # news 不受 cache 影响


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
        minimal_engine.news_aggregator.fetch.assert_called_once_with(max_items=5)

    async def test_fetch_news_empty(self, minimal_engine):
        """NewsAggregator 返回空列表"""
        minimal_engine.news_aggregator.fetch = AsyncMock(return_value=[])
        result = await minimal_engine.fetch_news()
        assert result == []
