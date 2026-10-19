import numpy as np
import pandas as pd

from stocks.engine.trader_metrics import compute_trader_metrics, market_regime


def _frame(prices, volumes=None):
    n = len(prices)
    prices = np.asarray(prices, dtype=float)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC"),
        "price": prices, "high": prices * 1.01, "low": prices * 0.99,
        "volume_lot": volumes if volumes is not None else [1000.0] * n,
    })


def test_uptrend_metrics():
    df = _frame(np.linspace(100, 160, 120), volumes=[1000.0] * 115 + [3000.0] * 5)
    m = compute_trader_metrics(df, benchmark=_frame(np.linspace(100, 110, 120)))
    assert m["bars"] == 120
    assert m["trend"]["alignment"] == "多头排列"
    assert m["trend"]["ma20_slope_5d_pct"] > 0 and m["trend"]["ma60_slope_10d_pct"] > 0
    assert m["momentum"]["rsi_14"] > 70
    assert m["momentum"]["rel_strength_20d_vs_benchmark_pct"] > 0
    assert m["volume"]["volume_5d_vs_20d"] > 1
    assert m["volume"]["obv_20d_direction"] == "上升"
    assert m["levels"]["drawdown_from_60d_high_pct"] == 0
    assert m["levels"]["atr_stop_2x"] < 160
    assert 0 < m["volatility"]["atr_14_pct"] < 5


def test_downtrend_and_short_history():
    m = compute_trader_metrics(_frame(np.linspace(160, 100, 80)))
    assert m["trend"]["alignment"] == "空头排列"
    assert m["levels"]["max_drawdown_60d_pct"] < -20
    assert m["momentum"]["ret_60d_pct"] < 0
    short = compute_trader_metrics(_frame(np.linspace(100, 101, 30)))
    assert short["trend"]["vs_ma60_pct"] is None and short["momentum"]["ret_60d_pct"] is None
    assert compute_trader_metrics(_frame([1.0] * 5)) == {"bars": 5}
    assert compute_trader_metrics(pd.DataFrame()) == {"bars": 0}


def test_market_regime_breadth_and_sectors():
    context = {
        "rotation": {
            "items": [
                {"symbol": "a:1", "above_ma20": True}, {"symbol": "a:2", "above_ma20": False},
                {"symbol": "us:X", "above_ma20": True}, {"symbol": "a:3", "above_ma20": None},
            ],
            "category_momentum": {"能源": {"r20": 9.2}, "科技": {"r20": -5.3}, "银行": {"r20": 3.9}},
        },
        "macro_snapshot": {"vix": 18.5, "source": "yahoo", "flag": True},
    }
    regime = market_regime(context)
    assert regime["breadth"] == {"A股": {"above_ma20_pct": 50.0, "sample": 2}, "美股": {"above_ma20_pct": 100.0, "sample": 1}}
    assert regime["sector_strongest_20d"][0] == {"sector": "能源", "r20_pct": 9.2}
    assert regime["sector_weakest_20d"][0] == {"sector": "科技", "r20_pct": -5.3}
    assert regime["macro"] == {"vix": 18.5}
