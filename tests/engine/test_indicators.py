"""TechnicalIndicators 测试 — 覆盖所有指标计算、边界条件、数据不足场景

测试策略：
- 构造已知序列的 DataFrame，验证指标值是否符合预期
- 边界测试：数据不足时返回 None，空 DataFrame 返回空字典
- 纯函数测试：无 IO、无状态、无外部依赖
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from stocks.engine.indicators import TechnicalIndicators


def make_df(prices: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """辅助函数：构造标准格式 DataFrame"""
    base = len(prices)
    volumes = volumes or [1_000_000] * base
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=base, freq="D"),
        "code": ["000001"] * base,
        "name": ["平安银行"] * base,
        "market": ["a"] * base,
        "price": prices,
        "open_price": prices,
        "high": prices,
        "low": prices,
        "prev_close": [prices[0]] + prices[:-1],
        "volume_lot": volumes,
    })


# ------------------------------------------------------------------
# 批量计算入口
# ------------------------------------------------------------------

class TestCalculate:
    def test_calculate_all(self):
        """正常数据应返回所有指标"""
        prices = [10.0 + i * 0.1 for i in range(80)]
        df = make_df(prices)
        result = TechnicalIndicators.calculate(df)

        assert result["ma_5"] is not None
        assert result["ma_20"] is not None
        assert result["ma_60"] is not None
        assert result["rsi_14"] is not None
        assert result["macd"] is not None
        assert result["macd"]["macd"] is not None
        assert result["bollinger"] is not None
        assert result["bollinger"]["upper"] is not None
        assert result["volume_ratio"] is not None
        assert result["price_position"] is not None
        assert result["volatility_20"] is not None
        assert result["data_points"] == 80

    def test_calculate_empty(self):
        """空 DataFrame 返回空字典"""
        df = pd.DataFrame()
        result = TechnicalIndicators.calculate(df)
        assert result == {}

    def test_calculate_no_price_column(self):
        """缺少 price 列返回空字典"""
        df = pd.DataFrame({"volume_lot": [1, 2, 3]})
        result = TechnicalIndicators.calculate(df)
        assert result == {}

    def test_calculate_insufficient_data(self):
        """数据不足时对应指标为 None"""
        prices = [10.0, 10.1, 10.2]
        df = make_df(prices)
        result = TechnicalIndicators.calculate(df)

        assert result["ma_5"] is None
        assert result["rsi_14"] is None
        assert result["macd"] is not None
        assert result["macd"]["macd"] is None
        assert result["bollinger"] is not None
        assert result["bollinger"]["upper"] is None
        assert result["volatility_20"] is None


# ------------------------------------------------------------------
# MA
# ------------------------------------------------------------------

class TestMA:
    def test_ma_basic(self):
        df = make_df([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
        ma5 = TechnicalIndicators.ma(df, 5)
        assert ma5 == 13.0  # (11+12+13+14+15)/5 = 13.0

    def test_ma_insufficient(self):
        df = make_df([10.0, 11.0])
        assert TechnicalIndicators.ma(df, 5) is None


# ------------------------------------------------------------------
# RSI
# ------------------------------------------------------------------

class TestRSI:
    def test_rsi_basic(self):
        """持续上涨后 RSI 应接近 100"""
        prices = [10.0 + i * 0.5 for i in range(30)]
        df = make_df(prices)
        rsi = TechnicalIndicators.rsi(df, 14)
        assert rsi is not None
        assert 50 <= rsi <= 100  # 持续上涨，RSI 可能为 100

    def test_rsi_falling(self):
        """持续下跌后 RSI 应接近 0"""
        prices = [20.0 - i * 0.5 for i in range(30)]
        df = make_df(prices)
        rsi = TechnicalIndicators.rsi(df, 14)
        assert rsi is not None
        assert 0 <= rsi <= 50  # 持续下跌，RSI 可能为 0

    def test_rsi_insufficient(self):
        df = make_df([10.0] * 14)
        assert TechnicalIndicators.rsi(df, 14) is None

    def test_rsi_flat(self):
        """价格不变时 RSI 应接近 50"""
        prices = [10.0] * 30
        df = make_df(prices)
        rsi = TechnicalIndicators.rsi(df, 14)
        assert rsi is not None
        assert 40 < rsi < 60  # 无动量，RSI 接近 50


# ------------------------------------------------------------------
# MACD
# ------------------------------------------------------------------

class TestMACD:
    def test_macd_basic(self):
        prices = [10.0 + i * 0.2 for i in range(50)]
        df = make_df(prices)
        macd = TechnicalIndicators.macd(df)

        assert macd["macd"] is not None
        assert macd["signal"] is not None
        assert macd["hist"] is not None
        # 上涨趋势中 MACD 通常为正
        assert macd["macd"] > 0

    def test_macd_insufficient(self):
        df = make_df([10.0] * 30)
        macd = TechnicalIndicators.macd(df)
        assert macd["macd"] is None
        assert macd["signal"] is None
        assert macd["hist"] is None


# ------------------------------------------------------------------
# Bollinger
# ------------------------------------------------------------------

class TestBollinger:
    def test_bollinger_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        df = make_df(prices)
        boll = TechnicalIndicators.bollinger(df, 20)

        assert boll["upper"] is not None
        assert boll["middle"] is not None
        assert boll["lower"] is not None
        assert boll["bandwidth"] is not None
        # 上涨趋势中，价格应接近上轨
        assert boll["upper"] > boll["middle"] > boll["lower"]

    def test_bollinger_insufficient(self):
        df = make_df([10.0] * 10)
        boll = TechnicalIndicators.bollinger(df, 20)
        assert boll["upper"] is None

    def test_bollinger_flat(self):
        """价格不变时，带宽应接近 0"""
        prices = [10.0] * 30
        df = make_df(prices)
        boll = TechnicalIndicators.bollinger(df, 20)
        assert boll["bandwidth"] is not None
        assert boll["bandwidth"] < 0.01  # 接近 0


# ------------------------------------------------------------------
# Volume Ratio
# ------------------------------------------------------------------

class TestVolumeRatio:
    def test_volume_ratio_basic(self):
        """量比 = 今日成交量 / 前 N 日均量"""
        prices = [10.0] * 10
        # 前 9 日 1M，最后 1 日 2M
        volumes = [1_000_000] * 9 + [2_000_000]
        df = make_df(prices, volumes)
        ratio = TechnicalIndicators.volume_ratio(df, 5)
        assert ratio is not None
        # 前 5 日均量 1M（索引 4-8 全为 1M），今日 2M
        assert ratio == 2.0

    def test_volume_ratio_no_volume(self):
        df = make_df([10.0] * 10)
        df = df.drop(columns=["volume_lot"])
        assert TechnicalIndicators.volume_ratio(df, 5) is None

    def test_volume_ratio_insufficient(self):
        df = make_df([10.0] * 3)
        assert TechnicalIndicators.volume_ratio(df, 5) is None


# ------------------------------------------------------------------
# Price Position
# ------------------------------------------------------------------

class TestPricePosition:
    def test_price_position_basic(self):
        prices = [10.0 + i * 0.1 for i in range(30)]
        df = make_df(prices)
        pos = TechnicalIndicators.price_position(df, 20)
        assert pos is not None
        assert 0 <= pos <= 100

    def test_price_position_at_upper(self):
        """价格等于最高时 position = 100"""
        prices = [10.0] * 19 + [20.0]
        df = make_df(prices)
        pos = TechnicalIndicators.price_position(df, 20)
        assert pos is not None
        assert pos > 95  # 接近 100

    def test_price_position_at_lower(self):
        """价格等于最低时 position = 0"""
        prices = [20.0] * 19 + [10.0]
        df = make_df(prices)
        pos = TechnicalIndicators.price_position(df, 20)
        assert pos is not None
        assert pos < 5  # 接近 0


# ------------------------------------------------------------------
# Volatility
# ------------------------------------------------------------------

class TestVolatility:
    def test_volatility_basic(self):
        prices = [10.0 + np.sin(i) * 2 for i in range(30)]
        df = make_df(prices)
        vol = TechnicalIndicators.volatility(df, 20)
        expected = df["price"].pct_change().dropna().tail(20).std() * np.sqrt(252)
        assert vol is not None
        assert np.isclose(vol, expected)

    def test_volatility_flat(self):
        """价格不变时年化波动率应为 0。"""
        prices = [10.0] * 30
        df = make_df(prices)
        vol = TechnicalIndicators.volatility(df, 20)
        assert vol == 0.0

    def test_volatility_near_zero_mean_is_finite(self):
        """平均收益接近零时不应因除以均值而爆炸。"""
        prices = [100.0]
        for daily_return in [0.01, -0.01] * 10:
            prices.append(prices[-1] * (1 + daily_return))

        vol = TechnicalIndicators.volatility(make_df(prices), 20)

        assert vol is not None
        assert np.isfinite(vol)
        assert 0.1 < vol < 0.3

    def test_volatility_insufficient(self):
        df = make_df([10.0] * 5)
        assert TechnicalIndicators.volatility(df, 20) is None
