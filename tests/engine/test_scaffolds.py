"""Engine Scaffold 测试 — 覆盖边界、异常、降级场景

PortfolioScaffold: 组合映射、分桶、偏离检查
MarketScaffold: 市场状态判断
"""

from __future__ import annotations

import pytest

from stocks.domain.models import FinancialAsset, Instrument, Quote
from stocks.engine.scaffolds import MarketScaffold, PortfolioScaffold

# ------------------------------------------------------------------
# PortfolioScaffold 测试
# ------------------------------------------------------------------


class TestPortfolioScaffoldBuild:
    """组合映射构建 — 正常与边界场景"""

    @pytest.fixture
    def scaffold(self):
        return PortfolioScaffold()

    def test_empty_assets(self, scaffold):
        """空资产列表 — 返回空 PortfolioMapping"""
        mapping = scaffold.build([], {})
        assert mapping.buckets == {}
        assert mapping.ratios == {}
        assert mapping.dominant_layers == []
        assert mapping.growth_exposure == "none"
        assert mapping.buffer_strength == "none"
        assert mapping.liquidity_status == "thin"

    def test_single_asset(self, scaffold):
        """单一资产 — 100% 占比"""
        assets = [
            FinancialAsset(name="现金", platform="银行", amount=100000, asset_type="现金管理"),
        ]
        mapping = scaffold.build(assets, {})

        assert "现金" in mapping.buckets
        assert mapping.ratios["现金"] == 1.0
        assert mapping.dominant_layers == ["现金"]
        assert mapping.growth_exposure == "none"
        assert mapping.buffer_strength == "strong"
        assert mapping.liquidity_status == "ample"

    def test_asset_type_normalization(self, scaffold):
        """资产类型大小写/别名映射 — 应归入正确 bucket"""
        assets = [
            FinancialAsset(name="A", platform="x", amount=10000, asset_type="股票ETF"),
            FinancialAsset(name="B", platform="x", amount=10000, asset_type="股票etf"),
            FinancialAsset(name="C", platform="x", amount=10000, asset_type="ETF"),
            FinancialAsset(name="D", platform="x", amount=10000, asset_type="etf"),
        ]
        mapping = scaffold.build(assets, {})

        # 所有类型都应归入 "权益" bucket
        assert "权益" in mapping.buckets
        assert len(mapping.buckets["权益"]) == 4
        assert mapping.ratios["权益"] == 1.0

    def test_unknown_asset_type(self, scaffold):
        """未知资产类型 — 归入 '其他' bucket"""
        assets = [
            FinancialAsset(name="奇怪资产", platform="x", amount=10000, asset_type="神秘类型"),
        ]
        mapping = scaffold.build(assets, {})

        assert "其他" in mapping.buckets
        assert mapping.ratios["其他"] == 1.0

    def test_zero_amount(self, scaffold):
        """金额为 0 的资产 — 应参与计算但占比 0"""
        assets = [
            FinancialAsset(name="现金", platform="x", amount=0, asset_type="现金管理"),
            FinancialAsset(name="股票", platform="x", amount=100000, asset_type="股票ETF"),
        ]
        mapping = scaffold.build(assets, {})

        assert mapping.ratios["现金"] == 0.0
        assert mapping.ratios["权益"] == 1.0

    def test_growth_exposure_thresholds(self, scaffold):
        """成长暴露阈值判断"""
        # > 60% → high
        assets_high = [FinancialAsset(name=f"ETF{i}", platform="x", amount=100000, asset_type="股票ETF") for i in range(7)]
        assets_high += [FinancialAsset(name="现金", platform="x", amount=100000, asset_type="现金管理")]
        mapping = scaffold.build(assets_high, {})
        assert mapping.growth_exposure == "high"

        # 35-60% → moderate
        assets_mod = [
            FinancialAsset(name="ETF", platform="x", amount=50000, asset_type="股票ETF"),
            FinancialAsset(name="现金", platform="x", amount=50000, asset_type="现金管理"),
        ]
        mapping = scaffold.build(assets_mod, {})
        assert mapping.growth_exposure == "moderate"

        # 10-35% → light
        assets_light = [
            FinancialAsset(name="ETF", platform="x", amount=20000, asset_type="股票ETF"),
            FinancialAsset(name="现金", platform="x", amount=80000, asset_type="现金管理"),
        ]
        mapping = scaffold.build(assets_light, {})
        assert mapping.growth_exposure == "light"

        # < 10% → none
        assets_none = [
            FinancialAsset(name="ETF", platform="x", amount=5000, asset_type="股票ETF"),
            FinancialAsset(name="现金", platform="x", amount=95000, asset_type="现金管理"),
        ]
        mapping = scaffold.build(assets_none, {})
        assert mapping.growth_exposure == "none"

    def test_buffer_strength_thresholds(self, scaffold):
        """缓冲强度阈值判断"""
        # > 50% → strong
        assets = [
            FinancialAsset(name="理财", platform="x", amount=60000, asset_type="理财"),
            FinancialAsset(name="现金", platform="x", amount=10000, asset_type="现金管理"),
            FinancialAsset(name="股票", platform="x", amount=30000, asset_type="股票ETF"),
        ]
        mapping = scaffold.build(assets, {})
        assert mapping.buffer_strength == "strong"

        # 25-50% → moderate
        assets = [
            FinancialAsset(name="理财", platform="x", amount=40000, asset_type="理财"),
            FinancialAsset(name="股票", platform="x", amount=60000, asset_type="股票ETF"),
        ]
        mapping = scaffold.build(assets, {})
        assert mapping.buffer_strength == "moderate"

        # 5-25% → light
        assets = [
            FinancialAsset(name="理财", platform="x", amount=15000, asset_type="理财"),
            FinancialAsset(name="股票", platform="x", amount=85000, asset_type="股票ETF"),
        ]
        mapping = scaffold.build(assets, {})
        assert mapping.buffer_strength == "light"

        # < 5% → none
        assets = [
            FinancialAsset(name="股票", platform="x", amount=98000, asset_type="股票ETF"),
            FinancialAsset(name="理财", platform="x", amount=2000, asset_type="理财"),
        ]
        mapping = scaffold.build(assets, {})
        assert mapping.buffer_strength == "none"

    def test_liquidity_status_thresholds(self, scaffold):
        """流动性状态阈值判断"""
        # > 20% → ample
        assets = [
            FinancialAsset(name="现金", platform="x", amount=30000, asset_type="现金管理"),
            FinancialAsset(name="股票", platform="x", amount=70000, asset_type="股票ETF"),
        ]
        mapping = scaffold.build(assets, {})
        assert mapping.liquidity_status == "ample"

        # 8-20% → adequate
        assets = [
            FinancialAsset(name="现金", platform="x", amount=15000, asset_type="现金管理"),
            FinancialAsset(name="股票", platform="x", amount=85000, asset_type="股票ETF"),
        ]
        mapping = scaffold.build(assets, {})
        assert mapping.liquidity_status == "adequate"

        # < 8% → thin
        assets = [
            FinancialAsset(name="现金", platform="x", amount=5000, asset_type="现金管理"),
            FinancialAsset(name="股票", platform="x", amount=95000, asset_type="股票ETF"),
        ]
        mapping = scaffold.build(assets, {})
        assert mapping.liquidity_status == "thin"

    def test_dominant_layers(self, scaffold):
        """主导层识别 — 占比 > 30% 的 bucket"""
        assets = [
            FinancialAsset(name="理财", platform="x", amount=40000, asset_type="理财"),  # 40%
            FinancialAsset(name="股票", platform="x", amount=40000, asset_type="股票ETF"),  # 40%
            FinancialAsset(name="现金", platform="x", amount=20000, asset_type="现金管理"),  # 20%
        ]
        mapping = scaffold.build(assets, {})

        assert len(mapping.dominant_layers) == 2
        assert "固收" in mapping.dominant_layers
        assert "权益" in mapping.dominant_layers
        assert "现金" not in mapping.dominant_layers

    def test_locked_assets(self, scaffold):
        """锁定资产标记"""
        assets = [
            FinancialAsset(name="锁定资产", platform="x", amount=50000, asset_type="locked"),
            FinancialAsset(name="股票", platform="x", amount=50000, asset_type="股票ETF"),
        ]
        mapping = scaffold.build(assets, {})
        assert mapping.locked_assets_present is True


class TestPortfolioScaffoldDrift:
    """偏离检查测试"""

    @pytest.fixture
    def scaffold(self):
        return PortfolioScaffold()

    @pytest.fixture
    def sample_mapping(self, scaffold, sample_assets):
        return scaffold.build(sample_assets, {})

    def test_no_constraints(self, scaffold, sample_mapping):
        """无约束配置 — 返回空列表"""
        drift = scaffold.check_drift(sample_mapping, {})
        assert drift == []

    def test_within_range(self, scaffold, sample_mapping):
        """在范围内 — 不触发偏离"""
        # 样例资产: 现金 100k / 固收 200k / 权益 150k / 黄金 50k = 500k
        # 现金 20%, 固收 40%, 权益 30%, 黄金 10%
        constraints = {
            "现金": {"min": 0.15, "max": 0.25},  # 20% 在范围内
        }
        drift = scaffold.check_drift(sample_mapping, constraints)
        assert len(drift) == 1
        assert drift[0].status == "within_range"
        assert drift[0].gap == 0.0

    def test_below_min(self, scaffold, sample_mapping):
        """低于下限 — 触发 below_min"""
        constraints = {
            "权益": {"min": 0.40, "max": 0.60},  # 30% < 40%
        }
        drift = scaffold.check_drift(sample_mapping, constraints)
        assert drift[0].status == "below_min"
        assert drift[0].gap == pytest.approx(0.10, abs=0.01)

    def test_above_max(self, scaffold, sample_mapping):
        """高于上限 — 触发 above_max"""
        constraints = {
            "固收": {"min": 0.10, "max": 0.30},  # 40% > 30%
        }
        drift = scaffold.check_drift(sample_mapping, constraints)
        assert drift[0].status == "above_max"
        assert drift[0].gap == pytest.approx(0.10, abs=0.01)

    def test_multiple_drifts(self, scaffold, sample_mapping):
        """多个偏离同时存在"""
        constraints = {
            "权益": {"min": 0.40, "max": 0.60},  # 30% < 40% → below_min
            "固收": {"min": 0.10, "max": 0.30},  # 40% > 30% → above_max
            "现金": {"min": 0.15, "max": 0.25},  # 20% → within_range
        }
        drift = scaffold.check_drift(sample_mapping, constraints)

        statuses = {d.bucket: d.status for d in drift}
        assert statuses["权益"] == "below_min"
        assert statuses["固收"] == "above_max"
        assert statuses["现金"] == "within_range"  # 在范围内也返回

    def test_missing_bucket_is_checked_as_zero(self, scaffold):
        """组合中没有现金时，现金最低占比约束仍必须报警。"""
        mapping = scaffold.build(
            [FinancialAsset(name="股票", platform="x", amount=100000, asset_type="股票ETF")],
            {},
        )

        drift = scaffold.check_drift(mapping, {"现金": {"min": 0.05, "max": 0.30}})

        assert len(drift) == 1
        assert drift[0].bucket == "现金"
        assert drift[0].current_ratio == 0.0
        assert drift[0].status == "below_min"
        assert drift[0].gap == 0.05


# ------------------------------------------------------------------
# MarketScaffold 测试
# ------------------------------------------------------------------


class TestMarketScaffoldBuild:
    """市场状态判断测试"""

    @pytest.fixture
    def scaffold(self):
        return MarketScaffold()

    def test_empty_quotes(self, scaffold):
        """空行情 — 返回未知状态"""
        state = scaffold.build({})
        assert state.risk_appetite == "unknown"
        assert state.tech_state == "unknown"
        assert state.china_state == "unknown"

    def test_risk_on(self, scaffold):
        """权益平均涨 > 1.5% → risk_on"""
        quotes = {
            "a": [
                Quote(Instrument("000300", "沪深300", "a"), price=3600, pct_change=2.0),
                Quote(Instrument("000001", "平安", "a"), price=12, pct_change=1.8),
            ],
        }
        state = scaffold.build(quotes)
        assert state.risk_appetite == "risk_on"

    def test_cooling(self, scaffold):
        """权益平均涨 0.3-1.5% → cooling"""
        quotes = {
            "a": [
                Quote(Instrument("000300", "沪深300", "a"), price=3600, pct_change=0.8),
                Quote(Instrument("000001", "平安", "a"), price=12, pct_change=0.5),
            ],
        }
        state = scaffold.build(quotes)
        assert state.risk_appetite == "cooling"

    def test_risk_off(self, scaffold):
        """权益平均跌 > 1.0% → broad_risk_off"""
        quotes = {
            "a": [
                Quote(Instrument("000300", "沪深300", "a"), price=3500, pct_change=-1.5),
                Quote(Instrument("000001", "平安", "a"), price=11, pct_change=-1.2),
            ],
        }
        state = scaffold.build(quotes)
        assert state.risk_appetite == "broad_risk_off"

    def test_mixed(self, scaffold):
        """涨跌幅在 [-1.0, 0.3] 之间 → mixed"""
        quotes = {
            "a": [
                Quote(Instrument("000300", "沪深300", "a"), price=3540, pct_change=0.1),
                Quote(Instrument("000001", "平安", "a"), price=11.5, pct_change=-0.5),
            ],
        }
        state = scaffold.build(quotes)
        assert state.risk_appetite == "mixed"

    def test_china_state_positive(self, scaffold):
        """A股平均 > 1.0% → stable_positive"""
        quotes = {
            "a": [
                Quote(Instrument("000300", "沪深300", "a"), price=3600, pct_change=1.5),
            ],
        }
        state = scaffold.build(quotes)
        assert state.china_state == "stable_positive"

    def test_china_state_under_pressure(self, scaffold):
        """A股平均 < -1.0% → under_pressure"""
        quotes = {
            "a": [
                Quote(Instrument("000300", "沪深300", "a"), price=3500, pct_change=-1.5),
            ],
        }
        state = scaffold.build(quotes)
        assert state.china_state == "under_pressure"

    def test_tech_state(self, scaffold):
        """科技标的状态判断"""
        # 含 NVDA 的标的被视为科技
        quotes = {
            "us": [
                Quote(Instrument("NVDA", "英伟达", "us"), price=460, pct_change=2.5),
            ],
        }
        state = scaffold.build(quotes)
        assert state.tech_state == "expanding"

    def test_safe_haven_strengthening(self, scaffold):
        """避险资产上涨 → strengthening"""
        quotes = {
            "gold": [
                Quote(Instrument("518880", "黄金ETF", "gold"), price=4.6, pct_change=1.0),
            ],
        }
        state = scaffold.build(quotes)
        assert state.safe_haven_state == "strengthening"

    def test_cross_asset_summary(self, scaffold):
        """跨资产摘要生成"""
        quotes = {
            "a": [
                Quote(Instrument("000300", "沪深300", "a"), price=3500, pct_change=-1.5),
            ],
            "gold": [
                Quote(Instrument("518880", "黄金ETF", "gold"), price=4.6, pct_change=1.0),
            ],
        }
        state = scaffold.build(quotes)
        summary_text = " ".join(state.cross_asset_summary)
        assert "避险" in summary_text or "跷跷板" in summary_text

    def test_no_pct_change_ignored(self, scaffold):
        """pct_change 为 None 的标的应被忽略"""
        quotes = {
            "a": [
                Quote(Instrument("000300", "沪深300", "a"), price=3540, pct_change=None),
                Quote(Instrument("000001", "平安", "a"), price=12, pct_change=2.0),
            ],
        }
        state = scaffold.build(quotes)
        # 只有平安被计入，平均 2.0% → risk_on
        assert state.risk_appetite == "risk_on"
