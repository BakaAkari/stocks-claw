"""测试规则回测引擎。"""
import json, tempfile
from pathlib import Path

from stocks.engine.rule_backtest import backtest_from_history, run_backtest


def _make_history(dir_path: Path, symbol: str, prices: list[float]):
    """生成合成历史数据 (60 天)。"""
    dir_path.mkdir(parents=True, exist_ok=True)
    records = [
        {"date": f"2026-01-{i+1:02d}", "close": p, "open": p, "high": p, "low": p, "volume": 1000}
        for i, p in enumerate(prices)
    ]
    (dir_path / f"{symbol}.json").write_text(json.dumps({"records": records}))


def test_backtest_uptrend_no_crash():
    """上升趋势：回测不崩溃。"""
    with tempfile.TemporaryDirectory() as td:
        hd = Path(td) / "history"
        prices = [100 * (1.01 ** i) for i in range(60)]
        _make_history(hd, "TEST_UP", prices)
        results = backtest_from_history(hd, lookback_days=60)
        assert isinstance(results, dict)


def test_backtest_downtrend():
    """下降趋势：应有信号。"""
    with tempfile.TemporaryDirectory() as td:
        hd = Path(td) / "history"
        prices = [100] * 30 + [90] * 15 + [80] * 15
        _make_history(hd, "TEST_DN", prices)
        results = backtest_from_history(hd, lookback_days=60)
        assert isinstance(results, dict)
        trend = results.get("trend_break")
        assert trend is not None


def test_run_backtest_empty():
    """空目录不崩溃。"""
    with tempfile.TemporaryDirectory() as td:
        result = run_backtest(repo_root=Path(td))
        assert isinstance(result, dict)


def test_run_backtest_with_data():
    """有数据时产出 output。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        hd = repo / ".local" / "history"
        _make_history(hd, "SYM", [100 + i for i in range(60)])
        result = run_backtest(repo_root=repo)
        assert "output" in result
