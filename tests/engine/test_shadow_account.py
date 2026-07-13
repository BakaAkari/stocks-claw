
"""测试 Shadow Account 快照保存和诊断生成。"""
import json, tempfile
from pathlib import Path

from stocks.engine.shadow_account import (
    save_snapshot, load_all_snapshots,
    analyze_snapshots, build_shadow_block,
)

def test_save_and_load():
    """保存快照后能正确加载。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        # 创建 .local/advice_snapshots 的父级
        save_snapshot(
            [{"signal": "hold", "action": "hold"}],
            run_id="r1", session="cn", generated_at="2026-01-01T10:00:00Z",
            market_date="2026-01-01", repo_root=repo,
        )
        snaps = load_all_snapshots(repo_root=repo)
        assert len(snaps) == 1
        assert snaps[0].run_id == "r1"
        assert snaps[0].action_cards == [{"signal": "hold", "action": "hold"}]

def test_analyze_stats():
    """多快照的信号统计。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        for i in range(3):
            save_snapshot(
                [{"signal": "hold"}, {"signal": "add"}],
                run_id=f"r{i}", session="cn",
                generated_at=f"2026-01-{i+1:02d}T10:00:00Z",
                market_date=f"2026-01-{i+1:02d}", repo_root=repo,
            )
        stats = analyze_snapshots(load_all_snapshots(repo_root=repo))
        assert stats["total_runs"] == 3
        assert stats["total_advice"] == 6
        assert stats["by_signal"]["hold"] == 3
        assert stats["by_signal"]["add"] == 3

def test_build_shadow_block_empty():
    """无快照时返回空字符串。"""
    with tempfile.TemporaryDirectory() as td:
        block = build_shadow_block(repo_root=Path(td))
        assert block == ""

def test_build_shadow_block_content():
    """生成诊断段包含关键信息。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        save_snapshot(
            [{"signal": "add"}, {"signal": "reduce"}],
            run_id="r1", session="cn", generated_at="2026-01-01T10:00:00Z",
            market_date="2026-01-01", repo_root=repo,
        )
        block = build_shadow_block(repo_root=repo)
        assert "执行行为追踪" in block
        assert "add" in block
        assert "reduce" in block
