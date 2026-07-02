"""pytest 全局配置和通用 fixtures"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from stocks.domain.models import (
    FinancialAsset,
    Instrument,
    Quote,
)
from stocks.engine.scaffolds import MarketScaffold, PortfolioScaffold

# ------------------------------------------------------------------
# 通用 fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sample_assets() -> list[FinancialAsset]:
    """标准测试资产组合 — 4 个 bucket 各覆盖"""
    return [
        FinancialAsset(name="现金", platform="银行", amount=100000, asset_type="现金管理"),
        FinancialAsset(name="理财", platform="银行", amount=200000, asset_type="理财"),
        FinancialAsset(name="沪深300ETF", platform="券商", amount=150000, asset_type="股票ETF"),
        FinancialAsset(name="华安黄金ETF", platform="支付宝", amount=50000, asset_type="黄金ETF"),
    ]


@pytest.fixture
def sample_constraints() -> dict:
    """标准约束配置"""
    return {
        "权益": {"min": 0.20, "max": 0.60},
        "固收": {"min": 0.15, "max": 0.50},
        "现金": {"min": 0.05, "max": 0.30},
        "黄金": {"min": 0.00, "max": 0.15},
    }


@pytest.fixture
def sample_instruments() -> list[Instrument]:
    """标准 watchlist 标的"""
    return [
        Instrument(code="000300", name="沪深300", market="a", exchange="sz_index", category="equity_cn"),
        Instrument(code="518880", name="华安黄金ETF", market="a", exchange="sh", category="gold"),
        Instrument(code="QQQ", name="纳斯达克100", market="us", category="tech"),
    ]


@pytest.fixture
def sample_quotes() -> dict[str, list[Quote]]:
    """标准测试行情 — A股和美股各两只"""
    return {
        "a": [
            Quote(
                instrument=Instrument(code="000300", name="沪深300", market="a", category="equity_cn"),
                price=3542.33,
                change=12.45,
                pct_change=0.35,
            ),
            Quote(
                instrument=Instrument(code="518880", name="华安黄金ETF", market="a", category="gold"),
                price=4.55,
                change=-0.02,
                pct_change=-0.44,
            ),
        ],
        "us": [
            Quote(
                instrument=Instrument(code="QQQ", name="纳斯达克100", market="us", category="tech"),
                price=385.20,
                change=5.30,
                pct_change=1.40,
            ),
        ],
    }


@pytest.fixture
def portfolio_scaffold() -> PortfolioScaffold:
    return PortfolioScaffold()


@pytest.fixture
def market_scaffold() -> MarketScaffold:
    return MarketScaffold()


# ------------------------------------------------------------------
# Mock fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_tencent_provider():
    """Mock 腾讯 Provider — 返回固定行情"""
    provider = Mock()
    provider.name = "tencent_a"
    provider.supported_markets = ["a"]

    async def _fetch_batch(instruments):
        return [
            Quote(
                instrument=inst,
                price=3542.33,
                change=12.45,
                pct_change=0.35,
            )
            for inst in instruments
        ]

    provider.fetch_batch = AsyncMock(side_effect=_fetch_batch)
    provider.fetch = AsyncMock(return_value=Quote(
        instrument=Instrument(code="000300", name="沪深300", market="a"),
        price=3542.33,
        change=12.45,
        pct_change=0.35,
    ))
    return provider


@pytest.fixture
def mock_finnhub_provider():
    """Mock Finnhub Provider — 返回固定行情"""
    provider = Mock()
    provider.name = "finnhub"
    provider.supported_markets = ["us", "crypto"]

    async def _fetch_batch(instruments):
        return [
            Quote(
                instrument=inst,
                price=385.20,
                change=5.30,
                pct_change=1.40,
            )
            for inst in instruments
        ]

    provider.fetch_batch = AsyncMock(side_effect=_fetch_batch)
    provider.fetch = AsyncMock(return_value=Quote(
        instrument=Instrument(code="QQQ", name="纳斯达克100", market="us"),
        price=385.20,
        change=5.30,
        pct_change=1.40,
    ))
    return provider


@pytest.fixture
def mock_registry(mock_tencent_provider, mock_finnhub_provider):
    """Mock ProviderRegistry — 预注册两个 Provider"""
    from stocks.providers.registry import ProviderRegistry

    registry = ProviderRegistry()
    registry.register(mock_tencent_provider)
    registry.register(mock_finnhub_provider)
    return registry


# ------------------------------------------------------------------
# pytest 配置
# ------------------------------------------------------------------


pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line("markers", "integration: 集成测试（需要网络）")
    config.addinivalue_line("markers", "slow: 慢速测试（> 1s）")
