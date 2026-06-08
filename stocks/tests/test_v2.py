"""stocks-claw v2 单元测试"""

import sys
from pathlib import Path

# 确保项目根目录在路径中
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stocks.domain.models import (
    AnalysisContext,
    FinancialAsset,
    Instrument,
    PortfolioMapping,
    Quote,
)
from stocks.engine.scaffolds import PortfolioScaffold, MarketScaffold
from stocks.providers.tencent_a import TencentAQuoteProvider
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider
from stocks.providers.finnhub_quote import FinnhubQuoteProvider
from stocks.providers.registry import ProviderRegistry


def test_domain_models():
    """测试 Domain 模型创建和序列化"""
    print("\n=== Domain Models ===")
    
    # Instrument
    inst = Instrument(code="000001", name="平安银行", market="a", exchange="sz")
    assert inst.code == "000001"
    print("✓ Instrument")
    
    # Quote
    quote = Quote(
        instrument=inst,
        price=12.5,
        change=0.3,
        pct_change=2.45,
    )
    assert quote.price == 12.5
    assert quote.to_dict()["price"] == 12.5
    print("✓ Quote")
    
    # FinancialAsset
    asset = FinancialAsset(
        name="测试基金",
        platform="支付宝",
        amount=100000,
        asset_type="股票ETF",
    )
    assert asset.amount == 100000
    print("✓ FinancialAsset")
    
    # PortfolioMapping
    mapping = PortfolioMapping(
        buckets={"权益": [asset]},
        ratios={"权益": 0.5},
        dominant_layers=["权益"],
        growth_exposure="moderate",
    )
    assert mapping.growth_exposure == "moderate"
    print("✓ PortfolioMapping")
    
    print("Domain Models: 全部通过")


def test_scaffolds():
    """测试脚手架计算"""
    print("\n=== Scaffolds ===")
    
    assets = [
        FinancialAsset(name="现金", platform="银行", amount=100000, asset_type="现金管理"),
        FinancialAsset(name="理财", platform="银行", amount=200000, asset_type="理财"),
        FinancialAsset(name="股票ETF", platform="券商", amount=50000, asset_type="股票ETF"),
        FinancialAsset(name="黄金", platform="支付宝", amount=30000, asset_type="黄金ETF"),
    ]
    
    # Portfolio Scaffold
    ps = PortfolioScaffold()
    mapping = ps.build(assets, {})
    
    assert "现金" in mapping.buckets
    assert "固收" in mapping.buckets
    assert "权益" in mapping.buckets
    assert "黄金" in mapping.buckets
    assert mapping.growth_exposure == "light"
    assert mapping.buffer_strength == "strong"
    assert mapping.liquidity_status == "ample"
    print("✓ PortfolioScaffold.build")
    
    # Drift check
    constraints = {"权益": {"min": 0.2, "max": 0.5}}
    drift = ps.check_drift(mapping, constraints)
    assert len(drift) == 1
    assert drift[0].bucket == "权益"
    assert drift[0].status == "below_min"
    print("✓ PortfolioScaffold.check_drift")
    
    # Market Scaffold
    ms = MarketScaffold()
    quotes = {
        "a": [
            Quote(Instrument("000001", "平安", "a"), price=10, pct_change=1.5),
            Quote(Instrument("000002", "万科", "a"), price=15, pct_change=-0.5),
        ]
    }
    state = ms.build(quotes)
    assert state.risk_appetite == "mixed"
    assert state.china_state == "stable"
    print("✓ MarketScaffold.build")
    
    print("Scaffolds: 全部通过")


def test_providers():
    """测试 Provider 注册和基本属性"""
    print("\n=== Providers ===")
    
    registry = ProviderRegistry()
    registry.register(TencentAQuoteProvider())
    registry.register(EastmoneyAQuoteProvider())
    registry.register(FinnhubQuoteProvider())
    
    assert len(registry.all()) == 3
    print("✓ ProviderRegistry.register")
    
    tencent = registry.get("tencent_a")
    assert tencent is not None
    assert tencent.supported_markets == ["a"]
    print("✓ ProviderRegistry.get")
    
    a_providers = registry.list_for_market("a")
    assert len(a_providers) == 2
    print("✓ ProviderRegistry.list_for_market")
    
    us_providers = registry.list_for_market("us")
    assert len(us_providers) == 1
    print("✓ ProviderRegistry.list_for_market (us)")
    
    print("Providers: 全部通过")


def test_engine_init():
    """测试 Engine 初始化和配置加载"""
    print("\n=== Engine Init ===")
    
    from stocks.engine import StocksEngine
    
    engine = StocksEngine()
    health = engine.health_check()
    
    assert health["status"] == "ok"
    assert len(health["providers"]) == 3
    assert health["assets_loaded"] == 4
    assert health["watchlist_loaded"] == 4
    print("✓ StocksEngine init + health_check")
    
    assets = engine.load_assets()
    assert len(assets) == 4
    print("✓ load_assets")
    
    mapping = engine.analyze_portfolio(assets)
    assert len(mapping.buckets) == 4
    print("✓ analyze_portfolio")
    
    print("Engine Init: 全部通过")


async def test_engine_async():
    """测试 Engine 异步功能"""
    print("\n=== Engine Async ===")
    
    from stocks.engine import StocksEngine
    
    engine = StocksEngine()
    
    # 获取行情
    quotes = await engine.fetch_quotes(market="a")
    assert isinstance(quotes, dict)
    print(f"✓ fetch_quotes: {len(quotes)} markets")
    
    # 构建上下文
    context = await engine.build_context()
    assert isinstance(context, AnalysisContext)
    assert context.asset_count == 4
    assert context.schema_version == 2
    print("✓ build_context")
    
    # 序列化
    ctx_dict = context.to_dict()
    assert "assets" in ctx_dict
    assert "quotes" in ctx_dict
    assert "market_state" in ctx_dict
    print("✓ AnalysisContext.to_dict")
    
    print("Engine Async: 全部通过")


def run_all_tests():
    """运行所有测试"""
    print("=" * 50)
    print("stocks-claw v2 测试套件")
    print("=" * 50)
    
    test_domain_models()
    test_scaffolds()
    test_providers()
    test_engine_init()
    
    # 异步测试
    import asyncio
    asyncio.run(test_engine_async())
    
    print("\n" + "=" * 50)
    print("所有测试通过！")
    print("=" * 50)


if __name__ == "__main__":
    run_all_tests()
