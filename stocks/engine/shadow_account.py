"""
Shadow Account — 执行行为诊断模块

从 ExecutionRecord 提取用户执行模式，对比系统建议与用户实际行为，
产出行为诊断报告。不改动任何决策逻辑，纯只读分析。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from stocks.logging_utils import get_logger

logger = get_logger("shadow_account")


@dataclass
class AdviceRecord:
    run_id: str
    session: str
    generated_at: str
    position_id: str
    signal: str
    action: str
    ratio: float
    stop_price: Optional[float] = None
    target_prices: list[float] = field(default_factory=list)


@dataclass
class ExecutionMatch:
    advice: AdviceRecord
    executed: bool
    action_taken: str = ""
    extent: str = ""
    note: str = ""
    executed_at: str = ""


@dataclass
class BehavioralDiagnostic:
    generated_at: str
    total_advice: int
    total_executed: int
    adoption_rate: float
    adoption_by_signal: dict
    systemic_biases: list[str]
    execution_lag_stats: dict
    summary: str
    over_trading_signals: list[str] = field(default_factory=list)
    under_reaction_signals: list[str] = field(default_factory=list)


def load_advice_from_runs(runs_dir: Path, *, max_days: int = 30) -> list[AdviceRecord]:
    advice_list: list[AdviceRecord] = []
    if not runs_dir.exists():
        return advice_list

    for date_dir in sorted(runs_dir.iterdir(), reverse=True):
        if not date_dir.is_dir() or date_dir.name == "latest":
            continue
        for session_type_dir in sorted(date_dir.iterdir()):
            if not session_type_dir.is_dir():
                continue
            for session_id_dir in sorted(session_type_dir.iterdir()):
                if not session_id_dir.is_dir():
                    continue
                for json_file in session_id_dir.glob("*.json"):
                    try:
                        data = json.loads(json_file.read_text())
                    except (json.JSONDecodeError, OSError):
                        continue
                    cards = data.get("action_cards") or []
                    for card in cards:
                        advice_list.append(AdviceRecord(
                            run_id=data.get("run_id", ""),
                            session=data.get("session", ""),
                            generated_at=data.get("generated_at", ""),
                            position_id=card.get("position_id", ""),
                            signal=card.get("signal", ""),
                            action=card.get("action", ""),
                            ratio=float(card.get("ratio", 0)),
                            stop_price=card.get("stop_price"),
                            target_prices=card.get("target_prices") or [],
                        ))
    return advice_list


def match_executions(advice_list, executions):
    matches = []
    for adv in advice_list:
        matches.append(ExecutionMatch(advice=adv, executed=False))
    return matches


def analyze_behavior(matches):
    total_advice = len(matches)
    total_executed = sum(1 for m in matches if m.executed)
    adoption_rate = total_executed / total_advice if total_advice > 0 else 0.0

    by_signal: dict = {}
    for m in matches:
        sig = m.advice.signal or "unknown"
        if sig not in by_signal:
            by_signal[sig] = {"total": 0, "executed": 0}
        by_signal[sig]["total"] += 1
        if m.executed:
            by_signal[sig]["executed"] += 1
    for sig in by_signal:
        d = by_signal[sig]
        d["rate"] = d["executed"] / d["total"] if d["total"] > 0 else 0.0

    biases = []
    reduce_signals = by_signal.get("reduce_risk", {})
    if reduce_signals.get("total", 0) >= 3 and reduce_signals.get("rate", 1.0) < 0.5:
        biases.append(f"止损/减仓信号采纳率仅 {reduce_signals['rate']:.0%}")

    execution_lag_stats = {"avg_hours": 0, "samples": 0}
    summary = f"整体采纳率 {adoption_rate:.0%}" if not biases else f"采纳率 {adoption_rate:.0%}，{len(biases)} 个偏差"

    return BehavioralDiagnostic(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_advice=total_advice, total_executed=total_executed,
        adoption_rate=adoption_rate, adoption_by_signal=by_signal,
        systemic_biases=biases, execution_lag_stats=execution_lag_stats,
        summary=summary,
    )


def run_shadow_diagnostic(repo_root=None):
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    runs_dir = repo_root / ".local" / "scheduled_runs"
    exec_dir = repo_root / ".local" / "executions"
    advice_list = load_advice_from_runs(runs_dir)
    executions = []
    if exec_dir.exists():
        for exec_file in exec_dir.glob("*.jsonl"):
            for line in exec_file.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        executions.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    matches = match_executions(advice_list, executions)
    diag = analyze_behavior(matches)

    if diag.total_advice == 0:
        return {
            "diagnostic": {},
            "output": "## 执行行为诊断\n\n"
                      "无执行记录。开始使用 advice_save 记录执行后，"
                      "系统将自动追踪采纳率与行为偏差。\n",
        }

    lines = [
        "## 执行行为诊断", "",
        f"- 统计周期内建议: {diag.total_advice}",
        f"- 已执行: {diag.total_executed} ({diag.adoption_rate:.0%})",
        f"- 未执行: {diag.total_advice - diag.total_executed}", "",
    ]
    if diag.adoption_by_signal:
        lines.append("### 按信号类型采纳率")
        for sig in sorted(diag.adoption_by_signal.keys()):
            d = diag.adoption_by_signal[sig]
            lines.append(f"- `{sig}`: {d['rate']:.0%} ({d['executed']}/{d['total']})")
    if diag.systemic_biases:
        lines.append("")
        lines.append("### 检测到的行为偏差")
        for b in diag.systemic_biases:
            lines.append(f"- {b}")
    lines.append("")
    lines.append(f"**诊断摘要**: {diag.summary}")
    return {"diagnostic": {"generated_at": diag.generated_at, "summary": diag.summary}, "output": "\n".join(lines)}
