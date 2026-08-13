#!/usr/bin/env python3
"""信号质量报告 —— 让 signal_tracker 积累的结算数据变成可读的胜率统计。

P0-1 (2026-08-12): 反馈闭环之前断在"数据积累但不消费"。本脚本消费
signals.jsonl + settlements.jsonl,按 source/方向/窗口输出胜率,并诚实
标注样本不足的信号类型("数据积累中"而非编造百分比)。

用法:
    .venv/bin/python scripts/signal_quality_report.py [--output PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKER_DIR = REPO_ROOT / ".local" / "signal_tracker"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _rate(items: list[dict]) -> tuple[int, int]:
    ok = sum(1 for s in items if s.get("correct") is True)
    return ok, len(items)


def _pct(ok: int, tot: int) -> str:
    return f"{ok}/{tot} = {ok / tot * 100:.1f}%" if tot else "样本不足"


def build_report() -> str:
    signals = _load_jsonl(TRACKER_DIR / "signals.jsonl")
    settlements = _load_jsonl(TRACKER_DIR / "settlements.jsonl")
    # settlements 记录不带 source, 从 signals join(signal_id 一致)
    sig_source = {s.get("signal_id"): s.get("source", "?") for s in signals}
    for s in settlements:
        s["source"] = s.get("source") or sig_source.get(s.get("signal_id"), "?")

    out: list[str] = []
    out.append("# 信号质量报告")
    out.append("")
    out.append(f"*生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    out.append("")
    out.append(f"追踪信号总数: {len(signals)} ｜ 已结算: {len(settlements)}")
    out.append("")
    out.append("> 说明: 结算窗口 24h = 信号后1个交易日价格方向, 1w = 5个交易日。")
    out.append("> 胜率低于 50% 说明该信号类别方向性差, 高于 55% 才有参考价值。")
    out.append("")

    # 1. 按 source × 方向 × 窗口
    out.append("## 1. 胜率总览(按信号源 × 方向)")
    out.append("")
    out.append("| 信号源 | 方向 | 24h | 1w |")
    out.append("|---|---|---|---|")
    for src in sorted({s.get("source", "?") for s in settlements}):
        for direction in ("buy", "sell"):
            cell_24, cell_1w = "—", "—"
            for w, name in (("24h", "cell_24"), ("1w", "cell_1w")):
                sub = [s for s in settlements if s.get("window") == w and s.get("direction") == direction and s.get("source") == src]
                if sub:
                    ok, tot = _rate(sub)
                    if name == "cell_24":
                        cell_24 = _pct(ok, tot)
                    else:
                        cell_1w = _pct(ok, tot)
            out.append(f"| {src} | {direction} | {cell_24} | {cell_1w} |")
    out.append("")

    # 2. 按标的(样本>=5)
    out.append("## 2. 按标的胜率(样本≥5)")
    out.append("")
    out.append("| 标的 | 窗口 | 胜率 |")
    out.append("|---|---|---|")
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for s in settlements:
        parts = s.get("signal_id", "").split("_")
        sym = parts[-2] if len(parts) >= 2 else "?"
        by_sym[sym].append(s)
    for sym, rows in sorted(by_sym.items(), key=lambda x: -len(x[1])):
        if len(rows) < 5:
            continue
        for w in ("24h", "1w"):
            sub = [s for s in rows if s.get("window") == w]
            if sub:
                ok, tot = _rate(sub)
                out.append(f"| {sym} | {w} | {_pct(ok, tot)} |")
    out.append("")

    # 3. 引擎动作信号
    eng_sig = [s for s in signals if s.get("source") == "engine_action"]
    eng_settled = [s for s in settlements if s.get("source") == "engine_action"]
    out.append("## 3. 引擎动作信号(股票, 新接入)")
    out.append("")
    if not eng_sig:
        out.append("尚无 engine_action 信号记录(追踪从 2026-08-12 开始接入)。")
    else:
        out.append(f"已追踪 {len(eng_sig)} 条, 已结算 {len(eng_settled)} 条 —— 结算需等待后续窗口, 当前样本不足, 不编造胜率。")
        out.append("")
        out.append("最近记录(最多5条):")
        for s in sorted(eng_sig, key=lambda x: x.get("generated_at", ""), reverse=True)[:5]:
            out.append(f"- {s.get('generated_at', '')[:16]} {s.get('symbol')} {s.get('direction')} @ {s.get('generation_price')}")
    out.append("")

    # 4. 周趋势
    out.append("## 4. 周趋势(1w 窗口胜率)")
    out.append("")
    by_week: dict[str, list[dict]] = defaultdict(list)
    for s in settlements:
        if s.get("window") != "1w":
            continue
        by_week[str(s.get("settled_at", ""))[:10]].append(s)
    if by_week:
        out.append("| 结算日 | 胜率 |")
        out.append("|---|---|")
        for wk in sorted(by_week):
            ok, tot = _rate(by_week[wk])
            out.append(f"| {wk} | {_pct(ok, tot)} |")
    else:
        out.append("无 1w 窗口结算数据。")
    out.append("")

    # 5. 结论
    out.append("## 5. 结论与建议")
    out.append("")
    total_ok, total = _rate(settlements)
    out.append(f"- 全部信号总体胜率: {_pct(total_ok, total)}")
    llm = [s for s in settlements if s.get("source") in ("llm", "fallback_rules")]
    ok, tot = _rate(llm)
    out.append(f"- LLM/fallback 信号(主要是 BTCUSDT): {_pct(ok, tot)}")
    out.append("- 引擎动作信号为新增追踪, 预计 2-4 周后开始产生可参考的胜率。")
    out.append("- 若某信号类别持续 <50%, 应暂停该类别信号输出或调整阈值(用数据说话, 不靠感觉)。")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="signal quality report")
    parser.add_argument("--output", help="输出文件路径(默认 stdout)")
    args = parser.parse_args()
    report = build_report()
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"written: {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
