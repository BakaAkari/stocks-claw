"""
Shadow Account — 执行行为诊断模块

保存每期建议快照，加载历史快照，产出行为诊断。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stocks.logging_utils import get_logger

logger = get_logger("shadow_account")

SNAPSHOT_DIR = ".local/advice_snapshots"


@dataclass
class AdviceSnapshot:
    """单次 run 的建议快照。"""
    run_id: str
    session: str
    generated_at: str
    action_cards: list[dict]    # position_id, signal, action, ratio
    market_date: str = ""


def save_snapshot(
    action_cards: list[dict],
    *,
    run_id: str,
    session: str,
    generated_at: str,
    market_date: str = "",
    repo_root: Path | None = None,
) -> Path:
    """保存当前 run 的建议快照到 .local/advice_snapshots/。"""
    root = repo_root or Path(__file__).resolve().parents[2]
    snap_dir = root / SNAPSHOT_DIR
    snap_dir.mkdir(parents=True, exist_ok=True)

    snapshot = AdviceSnapshot(
        run_id=run_id,
        session=session,
        generated_at=generated_at,
        market_date=market_date,
        action_cards=action_cards,
    )

    filename = f"{run_id}.json"
    filepath = snap_dir / filename
    filepath.write_text(json.dumps({
        "run_id": snapshot.run_id,
        "session": snapshot.session,
        "generated_at": snapshot.generated_at,
        "market_date": snapshot.market_date,
        "action_cards": snapshot.action_cards,
    }, ensure_ascii=False, indent=2))
    return filepath


def load_all_snapshots(repo_root: Path | None = None) -> list[AdviceSnapshot]:
    """加载所有历史建议快照。"""
    root = repo_root or Path(__file__).resolve().parents[2]
    snap_dir = root / SNAPSHOT_DIR
    if not snap_dir.exists():
        return []

    snapshots = []
    for json_file in sorted(snap_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        snapshots.append(AdviceSnapshot(
            run_id=data.get("run_id", ""),
            session=data.get("session", ""),
            generated_at=data.get("generated_at", ""),
            market_date=data.get("market_date", ""),
            action_cards=data.get("action_cards") or [],
        ))
    return snapshots


def analyze_snapshots(snapshots: list[AdviceSnapshot]) -> dict:
    """分析所有快照的聚合统计。"""
    by_signal: dict[str, int] = defaultdict(int)
    by_session: dict[str, int] = defaultdict(int)
    total_cards = 0

    for snap in snapshots:
        for card in snap.action_cards:
            sig = card.get("signal", "unknown")
            by_signal[sig] += 1
            by_session[snap.session] += 1
            total_cards += 1

    top_signals = sorted(by_signal.items(), key=lambda x: -x[1])[:5]
    total_runs = len(snapshots)

    return {
        "total_runs": total_runs,
        "total_advice": total_cards,
        "by_signal": dict(by_signal),
        "by_session": dict(by_session),
        "top_signals": top_signals,
        "latest_run_at": snapshots[-1].generated_at if snapshots else "",
    }


def build_shadow_block(repo_root: Path | None = None) -> str:
    """生成 Shadow Account 诊断段（markdown）。"""
    snapshots = load_all_snapshots(repo_root)
    stats = analyze_snapshots(snapshots)

    if stats["total_advice"] == 0:
        return ""

    lines = [
        "**执行行为追踪**",
        f"- 累计分析: {stats['total_runs']} 次会话, {stats['total_advice']} 条建议",
    ]
    for sig, count in stats["top_signals"]:
        pct = count / stats["total_advice"] * 100
        lines.append(f"- `{sig}`: {count} 次 ({pct:.0f}%)")

    # 执行记录追踪: 检查 .local/executions/
    root = repo_root or Path(__file__).resolve().parents[2]
    exec_dir = root / ".local" / "executions"
    if exec_dir.exists():
        exec_count = sum(1 for _ in exec_dir.glob("*.jsonl"))
        lines.append(f"- 已记录执行: {exec_count} 次" if exec_count > 0 else "- 尚未记录任何执行")
    else:
        lines.append("- 尚未记录任何执行")

    # 最近一次分析
    if stats["latest_run_at"]:
        try:
            dt = datetime.fromisoformat(stats["latest_run_at"])
            lines.append(f"- 最近分析: {dt.strftime('%m-%d %H:%M')}")
        except (ValueError, TypeError):
            pass

    # 信号分布健康度检查
    reduce_count = stats["by_signal"].get("reduce_risk", 0) + stats["by_signal"].get("reduce", 0)
    add_count = stats["by_signal"].get("add", 0) + stats["by_signal"].get("accumulate", 0)
    if add_count > reduce_count * 3 and reduce_count > 0:
        lines.append("- ⚠️ 加仓信号远多于减仓信号，检查是否存在过度乐观偏差")

    return "\n".join(lines)
