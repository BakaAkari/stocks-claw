"""回测 CLI 命令 — 因子历史命中率分析。

用法:
  uv run python -m stocks.cli.backtest_cmd
  uv run python -m stocks.cli.backtest_cmd --lookback 60
  uv run python -m stocks.cli.backtest_cmd --output .local/backtests/latest.md
"""

from __future__ import annotations

import sys
from pathlib import Path


def main():
    # 解析简单参数
    lookback = 60
    output_path = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--lookback" and i + 1 < len(args):
            lookback = int(args[i + 1])
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output_path = Path(args[i + 1])
            i += 2
        else:
            i += 1

    repo_root = Path(__file__).resolve().parents[2]
    history_dir = repo_root / ".local" / "history"

    if not history_dir.exists() or not list(history_dir.glob("*.json")):
        print("错误: .local/history/ 中无历史数据，请先运行一次 scheduled run 以填充数据。")
        sys.exit(1)

    from datetime import datetime, timezone

    from stocks.engine.rule_backtest import backtest_from_history

    print(f"正在分析 {lookback} 天历史数据...")
    results = backtest_from_history(history_dir, lookback_days=lookback)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# 因子回测报告 — {now}",
        f"回溯: {lookback} 日 | 标的: {len(list(history_dir.glob('*.json')))} 个",
        "",
    ]

    labels = {
        "trend_break": "📉 MA20 跌破",
        "stop_loss": "🛑 止损 -8%",
        "rsi_oversold": "📊 RSI 超卖 (<30)",
        "profit_pullback": "⚠️ 利润回撤 (>10%)",
    }

    for name in ["trend_break", "stop_loss", "rsi_oversold", "profit_pullback"]:
        r = results.get(name)
        if not r:
            continue
        lines.append(f"## {labels.get(name, name)}")
        lines.append(f"- 信号次数: {r.total_signals}")
        lines.append(f"- 方向正确率: {r.accuracy:.1%} ({r.correct_direction}/{r.total_signals})")
        lines.append(f"- 平均 {lookback} 日收益: {r.avg_gain_pct:+.2f}%")
        lines.append(f"- 最佳: {r.max_gain_pct:+.2f}%  /  最差: {r.max_loss_pct:+.2f}%")
        if r.signals_by_symbol:
            top = sorted(r.signals_by_symbol.items(), key=lambda x: -x[1])[:5]
            lines.append(f"- 最多信号标的: {', '.join(f'{s}({n})' for s, n in top)}")
        lines.append("")

    if not results:
        lines.append("无足够历史数据生成回测结果。")

    output = "\n".join(lines)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output)
        print(f"已保存: {output_path}")
    else:
        # 默认保存到 .local/backtests/
        default = repo_root / ".local" / "backtests" / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.md"
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_text(output)
        print(f"已保存: {default}")

    print()
    print(output)


if __name__ == "__main__":
    main()
