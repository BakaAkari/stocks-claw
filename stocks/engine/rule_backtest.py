"""Rule Backtest — 轻量因子回测引擎"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class BacktestResult:
    rule_name: str
    total_signals: int = 0
    correct_direction: int = 0
    false_direction: int = 0
    accuracy: float = 0.0
    avg_gain_pct: float = 0.0
    max_gain_pct: float = 0.0
    max_loss_pct: float = 0.0
    signals_by_symbol: dict | None = None

    def __post_init__(self):
        if self.signals_by_symbol is None:
            self.signals_by_symbol = {}


def _calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(diff if diff > 0 else 0.0)
        losses.append(-diff if diff < 0 else 0.0)
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def backtest_from_history(history_dir, *, lookback_days=60, hold_days=5, ma_days=20):
    results = {}
    raw_signals = defaultdict(list)
    if not history_dir.exists():
        return results

    bars_by_symbol = {}
    for json_file in history_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        records = data.get("records") or data.get("data") or []
        if isinstance(data, list):
            records = data
        symbol = json_file.stem
        for bar in records:
            if not isinstance(bar, dict):
                continue
            if "price" in bar and "close" not in bar:
                bar["close"] = bar["price"]
            if "timestamp" in bar and "date" not in bar:
                bar["date"] = bar["timestamp"]
            bar["symbol"] = symbol
            bars_by_symbol.setdefault(symbol, []).append(bar)

    for symbol, bars in bars_by_symbol.items():
        if len(bars) < ma_days + hold_days:
            continue
        bars.sort(key=lambda b: b.get("date", ""))
        closes = [b.get("close", 0) for b in bars]
        dates = [b.get("date", "") for b in bars]

        ma_values = []
        for i in range(len(closes)):
            if i >= ma_days - 1:
                ma = sum(closes[i - ma_days + 1 : i + 1]) / ma_days
            else:
                ma = None
            ma_values.append(ma)

        for i in range(ma_days, len(bars) - hold_days):
            c = closes[i]

            if ma_values[i] and c < ma_values[i] and closes[i - 1] >= (ma_values[i - 1] or closes[i - 1]):
                fc = closes[i + hold_days]
                raw_signals["trend_break"].append({
                    "symbol": symbol, "date": dates[i],
                    "correct": fc < c, "gain_pct": (fc - c) / c * 100,
                })

            if closes[i - 1] > 0:
                dc = (c - closes[i - 1]) / closes[i - 1] * 100
                if dc < -8:
                    fc = closes[i + hold_days]
                    raw_signals["stop_loss"].append({
                        "symbol": symbol, "date": dates[i],
                        "correct": fc < c, "gain_pct": (fc - c) / c * 100,
                    })

            if i >= 14:
                rsi = _calc_rsi(closes[i - 14 : i + 1])
                if rsi and rsi < 30:
                    fc = closes[i + hold_days]
                    raw_signals["rsi_oversold"].append({
                        "symbol": symbol, "date": dates[i],
                        "correct": fc > c, "gain_pct": (fc - c) / c * 100,
                    })

            if i >= 5:
                rh = max(closes[i - 5 : i])
                if rh > 0 and (rh - c) / rh > 0.10:
                    fc = closes[i + hold_days]
                    raw_signals["profit_pullback"].append({
                        "symbol": symbol, "date": dates[i],
                        "correct": fc < c, "gain_pct": (fc - c) / c * 100,
                    })

    for rule_name, signals in raw_signals.items():
        if not signals:
            continue
        total = len(signals)
        correct = sum(1 for s in signals if s["correct"])
        gains = [s["gain_pct"] for s in signals]
        by_sym = defaultdict(int)
        for s in signals:
            by_sym[s["symbol"]] += 1

        results[rule_name] = BacktestResult(
            rule_name=rule_name,
            total_signals=total,
            correct_direction=correct,
            false_direction=total - correct,
            accuracy=correct / total if total > 0 else 0.0,
            avg_gain_pct=sum(gains) / len(gains) if gains else 0.0,
            max_gain_pct=max(gains) if gains else 0.0,
            max_loss_pct=min(gains) if gains else 0.0,
            signals_by_symbol=dict(by_sym),
        )
    return results


def run_backtest(repo_root=None):
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    history_dir = repo_root / ".local" / "history"
    results = backtest_from_history(history_dir)
    now = datetime.now(timezone.utc).isoformat()[:19]
    lines = ["## 因子回测报告", "", f"时间: {now}", ""]
    labels = {
        "trend_break": "MA20 跌破",
        "stop_loss": "止损 -8%",
        "rsi_oversold": "RSI 超卖 <30",
        "profit_pullback": "利润回撤 >10%",
    }
    for name in ["trend_break", "stop_loss", "rsi_oversold", "profit_pullback"]:
        r = results.get(name)
        if not r:
            continue
        lines.append(f"### {labels.get(name, name)}")
        lines.append(f"- 信号: {r.total_signals} | 正确率: {r.accuracy:.1%} | 均收益: {r.avg_gain_pct:+.2f}%")
        lines.append(f"- 最佳: {r.max_gain_pct:+.2f}% / 最差: {r.max_loss_pct:+.2f}%")
        lines.append("")
    return {
        "results": {
            k: {
                "rule_name": v.rule_name,
                "total_signals": v.total_signals,
                "accuracy": v.accuracy,
                "avg_gain_pct": v.avg_gain_pct,
            }
            for k, v in results.items()
        },
        "output": "\n".join(lines),
    }
