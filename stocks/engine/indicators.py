"""技术指标计算引擎 — 纯函数计算，无状态无 IO

输入 HistoryCache 返回的 DataFrame，输出标准化技术指标字典。
所有方法为纯函数，便于并行计算和单元测试。

支持指标：
- MA(5/20/60): 简单移动平均线
- RSI(14): 相对强弱指数
- MACD(12,26,9): 异同移动平均线
- Bollinger(20,2): 布林带
- Volume Ratio: 量比（成交量 / 5 日均量）
- Price Position: 当前价格在布林带中的位置百分比
- Volatility: 20 日收益率年化历史波动率

使用方式：
    from stocks.engine.indicators import TechnicalIndicators
    df = await cache.get_history(inst, lookback_bars=60)
    indicators = TechnicalIndicators.calculate(df)
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd


class TechnicalIndicators:
    """技术指标计算引擎"""

    # ------------------------------------------------------------------
    # 批量计算入口
    # ------------------------------------------------------------------

    @staticmethod
    def calculate(df: pd.DataFrame) -> dict[str, Any]:
        """计算所有技术指标，返回标准化字典

        Args:
            df: HistoryCache 返回的 DataFrame，必须包含 "price" 和 "volume_lot" 列

        Returns:
            指标字典，数据不足时对应值为 None
        """
        if df.empty or "price" not in df.columns:
            return {}

        return {
            "ma_5": TechnicalIndicators.ma(df, 5),
            "ma_20": TechnicalIndicators.ma(df, 20),
            "ma_60": TechnicalIndicators.ma(df, 60),
            "rsi_14": TechnicalIndicators.rsi(df, 14),
            "macd": TechnicalIndicators.macd(df),
            "bollinger": TechnicalIndicators.bollinger(df),
            "volume_ratio": TechnicalIndicators.volume_ratio(df),
            "price_position": TechnicalIndicators.price_position(df),
            "volatility_20": TechnicalIndicators.volatility(df, 20),
            "data_points": len(df),
        }

    # ------------------------------------------------------------------
    # 单指标计算
    # ------------------------------------------------------------------

    @staticmethod
    def ma(df: pd.DataFrame, period: int = 5) -> Optional[float]:
        """简单移动平均线 (SMA)

        Returns:
            最近 period 日的平均收盘价，数据不足返回 None
        """
        if len(df) < period:
            return None
        return float(df["price"].tail(period).mean())

    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """相对强弱指数 (RSI)

        标准公式：RSI = 100 - (100 / (1 + RS))
        RS = 平均涨幅 / 平均跌幅（使用 Wilder's smoothing）

        Returns:
            0-100 的 RSI 值，数据不足返回 None
        """
        if len(df) < period + 1:
            return None

        prices = df["price"].astype(float)
        delta = prices.diff()

        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        # Wilder's smoothing: 首次用 SMA，后续用 EMA (alpha=1/period)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain.iloc[-1] / avg_loss.iloc[-1] if avg_loss.iloc[-1] != 0 else np.inf
        # 边界：avg_gain=0 且 avg_loss=0 时，无动量，RSI=50
        if avg_gain.iloc[-1] == 0 and avg_loss.iloc[-1] == 0:
            return 50.0
        rsi = 100 - (100 / (1 + rs))
        return float(rsi)

    @staticmethod
    def macd(
        df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> dict[str, Optional[float]]:
        """MACD 指标

        Returns:
            {"macd": float, "signal": float, "hist": float}
            数据不足时全部为 None
        """
        if len(df) < slow + signal:
            return {"macd": None, "signal": None, "hist": None}

        prices = df["price"].astype(float)
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()

        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        hist = macd_line - signal_line

        return {
            "macd": float(macd_line.iloc[-1]),
            "signal": float(signal_line.iloc[-1]),
            "hist": float(hist.iloc[-1]),
        }

    @staticmethod
    def bollinger(
        df: pd.DataFrame, period: int = 20, std_dev: float = 2.0
    ) -> dict[str, Optional[float]]:
        """布林带 (Bollinger Bands)

        Returns:
            {"upper": float, "middle": float, "lower": float, "bandwidth": float}
            bandwidth = (upper - lower) / middle
        """
        if len(df) < period:
            return {"upper": None, "middle": None, "lower": None, "bandwidth": None}

        prices = df["price"].astype(float)
        middle = prices.tail(period).mean()
        std = prices.tail(period).std()

        upper = middle + std_dev * std
        lower = middle - std_dev * std
        bandwidth = (upper - lower) / middle if middle != 0 else None

        return {
            "upper": float(upper),
            "middle": float(middle),
            "lower": float(lower),
            "bandwidth": float(bandwidth) if bandwidth is not None else None,
        }

    @staticmethod
    def volume_ratio(df: pd.DataFrame, period: int = 5) -> Optional[float]:
        """量比：当日成交量 / 前 period 日均成交量

        用于判断成交量异常放大或萎缩。
        """
        if len(df) < period + 1 or "volume_lot" not in df.columns:
            return None

        volumes = df["volume_lot"].astype(float)
        today_vol = volumes.iloc[-1]
        avg_vol = volumes.iloc[-(period + 1) : -1].mean()  # 不包含今日

        if avg_vol == 0 or pd.isna(avg_vol) or pd.isna(today_vol):
            return None
        return float(today_vol / avg_vol)

    @staticmethod
    def price_position(df: pd.DataFrame, period: int = 20) -> Optional[float]:
        """价格位置：当前价格在布林带区间中的位置百分比

        0% = 触及下轨，100% = 触及上轨，50% = 中轨
        用于判断超买/超卖位置。
        """
        boll = TechnicalIndicators.bollinger(df, period)
        if boll["upper"] is None or boll["lower"] is None:
            return None

        current_price = float(df["price"].iloc[-1])
        upper = boll["upper"]
        lower = boll["lower"]

        if upper == lower:
            return 50.0

        position = (current_price - lower) / (upper - lower) * 100
        return float(position)

    @staticmethod
    def volatility(df: pd.DataFrame, period: int = 20) -> Optional[float]:
        """年化历史波动率：period 日收益率标准差 × sqrt(252)。

        使用日收益率样本标准差，并按一年 252 个交易日年化。
        """
        if len(df) < period + 1:
            return None

        prices = df["price"].astype(float)
        returns = prices.pct_change().dropna()

        if len(returns) < period:
            return None

        recent_returns = returns.tail(period)
        std = recent_returns.std()

        if pd.isna(std):
            return None

        return float(std * np.sqrt(252))
