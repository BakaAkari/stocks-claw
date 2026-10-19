"""交易员辅助判断指标 — 由日 K（OHLCV）计算的纯函数，无 IO。

覆盖趋势（均线排列与斜率）、动量（RSI、MACD 柱变化、多周期涨跌）、波动（ATR、
年化波动、布林带宽与收口分位）、量能（量比、OBV 方向）、关键价位（20/60 日高低、
距高点回撤、区间最大回撤）与相对强弱。数据不足的项返回 None，不外推。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from stocks.engine.indicators import TechnicalIndicators


def _r(value, digits: int = 2) -> Optional[float]:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(value) or np.isinf(value) else round(value, digits)


def _ret(close: pd.Series, bars: int) -> Optional[float]:
    if len(close) <= bars or close.iloc[-(bars + 1)] <= 0:
        return None
    return (close.iloc[-1] / close.iloc[-(bars + 1)] - 1) * 100


def _sma_slope(close: pd.Series, window: int, lag: int) -> Optional[float]:
    """均线在 lag 根内的变化百分比（斜率）。"""
    if len(close) < window + lag:
        return None
    now = close.tail(window).mean()
    before = close.iloc[-(window + lag):-lag].mean()
    return (now / before - 1) * 100 if before else None


def _atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if len(df) < period + 1 or not {"high", "low"} <= set(df.columns):
        return None
    high, low, close = df["high"], df["low"], df["price"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean().iloc[-1]
    return float(atr)


def _alignment(price: float, ma20: Optional[float], ma60: Optional[float]) -> Optional[str]:
    if ma20 is None or ma60 is None:
        return None
    if price > ma20 > ma60:
        return "多头排列"
    if price < ma20 < ma60:
        return "空头排列"
    return "均线缠绕"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("price", "high", "low", "volume_lot"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["price"])
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp")
    return df.reset_index(drop=True)


def compute_trader_metrics(df: pd.DataFrame, benchmark: Optional[pd.DataFrame] = None) -> dict:
    if df is None or df.empty or "price" not in df.columns:
        return {"bars": 0}
    df = _clean(df)
    close = df["price"]
    n = len(close)
    if n < 15:
        return {"bars": n}
    price = float(close.iloc[-1])
    ind = TechnicalIndicators.calculate(df)
    ma20, ma60 = ind.get("ma_20"), ind.get("ma_60")
    macd_now = (ind.get("macd") or {}).get("hist")
    macd_prev = None
    if n > 3:
        macd_prev = TechnicalIndicators.macd(df.iloc[:-3]).get("hist")
    boll = ind.get("bollinger") or {}

    # 布林带宽在近 60 根中的分位：越低越“收口”，常见于变盘前
    bw_pct = None
    if n >= 40:
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        bandwidth = (4 * std / mid).dropna().tail(60)
        if len(bandwidth) >= 20:
            bw_pct = (bandwidth < bandwidth.iloc[-1]).mean() * 100

    atr = _atr(df)
    window60 = close.tail(60)
    window20 = close.tail(20)
    high60, low60 = float(window60.max()), float(window60.min())
    running_max = window60.cummax()
    max_dd60 = ((window60 / running_max - 1) * 100).min()

    vol = df["volume_lot"] if "volume_lot" in df.columns else pd.Series(dtype=float)
    vol = vol.dropna()
    vol_trend = None
    if len(vol) >= 20 and vol.tail(20).mean() > 0:
        vol_trend = vol.tail(5).mean() / vol.tail(20).mean()
    obv_dir = None
    if len(vol) >= 21:
        aligned = df.dropna(subset=["volume_lot"]).tail(21)
        sign = np.sign(aligned["price"].diff().fillna(0))
        obv = (sign * aligned["volume_lot"]).cumsum()
        change = obv.iloc[-1] - obv.iloc[0]
        obv_dir = "上升" if change > 0 else "下降" if change < 0 else "持平"

    rel20 = None
    if benchmark is not None and not benchmark.empty:
        bench_close = _clean(benchmark)["price"]
        own, ref = _ret(close, 20), _ret(bench_close, 20)
        if own is not None and ref is not None:
            rel20 = own - ref

    return {
        "bars": n,
        "trend": {
            "alignment": _alignment(price, ma20, ma60),
            "vs_ma20_pct": _r((price / ma20 - 1) * 100) if ma20 else None,
            "vs_ma60_pct": _r((price / ma60 - 1) * 100) if ma60 else None,
            "ma20_slope_5d_pct": _r(_sma_slope(close, 20, 5)),
            "ma60_slope_10d_pct": _r(_sma_slope(close, 60, 10)),
        },
        "momentum": {
            "rsi_14": _r(ind.get("rsi_14"), 1),
            "macd_hist": _r(macd_now, 4),
            "macd_hist_3d_change": _r(macd_now - macd_prev, 4) if macd_now is not None and macd_prev is not None else None,
            "ret_5d_pct": _r(_ret(close, 5)),
            "ret_20d_pct": _r(_ret(close, 20)),
            "ret_60d_pct": _r(_ret(close, 60)),
            "rel_strength_20d_vs_benchmark_pct": _r(rel20),
        },
        "volatility": {
            "atr_14_pct": _r(atr / price * 100) if atr and price else None,
            "vol_20d_annual_pct": _r((ind.get("volatility_20") or 0) * 100) if ind.get("volatility_20") else None,
            "bollinger_pct_b": _r(ind.get("price_position"), 1),
            "bollinger_bandwidth_pct": _r((boll.get("bandwidth") or 0) * 100) if boll.get("bandwidth") else None,
            "bandwidth_percentile_60": _r(bw_pct, 0),
        },
        "volume": {
            "volume_ratio_today": _r(ind.get("volume_ratio")),
            "volume_5d_vs_20d": _r(vol_trend),
            "obv_20d_direction": obv_dir,
        },
        "levels": {
            "high_20d": _r(window20.max(), 4),
            "low_20d": _r(window20.min(), 4),
            "high_60d": _r(high60, 4),
            "low_60d": _r(low60, 4),
            "drawdown_from_60d_high_pct": _r((price / high60 - 1) * 100) if high60 else None,
            "above_60d_low_pct": _r((price / low60 - 1) * 100) if low60 else None,
            "max_drawdown_60d_pct": _r(max_dd60),
            "atr_stop_2x": _r(price - 2 * atr, 4) if atr else None,
        },
    }


def market_regime(context: dict) -> dict:
    """市场环境：宏观数值、A 股/美股广度（站上 MA20 比例）、板块强弱前后各 5。"""
    rotation = context.get("rotation") or {}
    items = rotation.get("items") or []
    breadth = {}
    for market, label in (("a", "A股"), ("us", "美股")):
        pool = [i for i in items if str(i.get("symbol", "")).startswith(f"{market}:") and i.get("above_ma20") is not None]
        if pool:
            breadth[label] = {
                "above_ma20_pct": round(sum(1 for i in pool if i["above_ma20"]) / len(pool) * 100, 0),
                "sample": len(pool),
            }
    categories = sorted(
        ((k, v.get("r20")) for k, v in (rotation.get("category_momentum") or {}).items()
         if isinstance(v.get("r20"), (int, float))),
        key=lambda kv: kv[1], reverse=True,
    )
    macro = context.get("macro_snapshot") or {}
    return {
        "macro": {k: v for k, v in macro.items() if isinstance(v, (int, float)) and not isinstance(v, bool)},
        "breadth": breadth,
        "sector_strongest_20d": [{"sector": k, "r20_pct": round(v, 2)} for k, v in categories[:5]],
        "sector_weakest_20d": [{"sector": k, "r20_pct": round(v, 2)} for k, v in categories[-5:][::-1]],
    }
