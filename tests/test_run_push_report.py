from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_push_payload import _action, _artifact, _full_outlook_artifact

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_push_report.py"


def test_entrypoint_renders_and_persists_sanitized_payload(tmp_path):
    root = tmp_path / "latest"
    root.mkdir()
    (root / "cn_after_close.json").write_text(json.dumps(_artifact(), ensure_ascii=False))
    payload = tmp_path / "payload"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--session",
            "cn_after_close",
            "--artifact-root",
            str(root),
            "--payload-root",
            str(payload),
            "--now",
            "2026-07-17T15:27:00+08:00",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "**本窗口变化**" in r.stdout
    assert "**可执行动作**" in r.stdout
    assert "**禁止与延后**" in r.stdout
    assert "**组合与检查点**" in r.stdout
    # M1: 下一检查点 是 §6 内嵌行，不再是独立 heading
    assert "下一检查点:" in r.stdout
    assert "**下一检查点**" not in r.stdout
    assert "4个减仓信号" not in r.stdout
    data = json.loads((payload / "cn_after_close.json").read_text())
    assert set(data) == {
        "payload_version", "session_label", "market_date", "delivery", "session_type", "user_view",
        # P1-14: deterministic window_delta travels with the payload so the
        # "本窗口变化" section can surface risk/action/conflict changes even
        # when the LLM outlook_delta is empty.
        "window_delta",
        # R5-5: 报告生成时间(渲染标题标注,用户判断报告时效)。
        "generated_at",
        # 缺口3: 引擎已算出的持仓信号参考(止损位/建议比例),渲染层据此把
        # 被压制冲突的决策价位/比例带给用户。
        "signal_reference_by_position",
        # P0: 产业情报(整理后的 cluster,渲染"产业情报"板块)。
        "intelligence_brief",
    }


def test_entrypoint_fails_loudly_on_invalid_or_stale_artifact(tmp_path):
    root = tmp_path / "latest"
    root.mkdir()
    bad = _artifact()
    bad["generated_at"] = "2026-07-16T00:00:00+00:00"
    (root / "cn_after_close.json").write_text(json.dumps(bad))
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--session",
            "cn_after_close",
            "--artifact-root",
            str(root),
            "--payload-root",
            str(tmp_path / "payload"),
            "--now",
            "2026-07-17T15:27:00+08:00",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert r.stdout == ""
    assert "INVALID:" in r.stderr
    assert not (tmp_path / "payload" / "cn_after_close.json").exists()


def test_entrypoint_fails_loudly_when_push_truth_violated(tmp_path):
    """The cron entrypoint must run validate_push_truth, not just
    validate_payload_text -- a defence-in-depth check on presentation's
    invariants (e.g. action text percentage vs final_ratio) must block
    delivery even though it never touches build_push_payload's own checks."""
    root = tmp_path / "latest"
    root.mkdir()
    bad = _artifact()
    bad["portfolio_decision"]["user_view"]["instruction_card"]["actions"] = [
        _action(final_ratio=0.25, reason_summary="化工ETF（516020）：减仓 50%")
    ]
    (root / "cn_after_close.json").write_text(json.dumps(bad, ensure_ascii=False))
    payload = tmp_path / "payload"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--session",
            "cn_after_close",
            "--artifact-root",
            str(root),
            "--payload-root",
            str(payload),
            "--now",
            "2026-07-17T15:27:00+08:00",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert r.stdout == ""
    assert "INVALID:" in r.stderr
    assert "disagrees with final_ratio" in r.stderr
    assert not (payload / "cn_after_close.json").exists()


def _outlook_test_artifact():
    """Helper for outlook entrypoint tests."""
    from tests.test_push_payload import _full_outlook_artifact
    return _full_outlook_artifact()


def test_entrypoint_renders_outlook_section(tmp_path):
    """Full pipeline renders the outlook section."""
    root = tmp_path / "latest"
    root.mkdir()
    (root / "cn_after_close.json").write_text(
        json.dumps(_full_outlook_artifact(), ensure_ascii=False)
    )
    payload_dir = tmp_path / "payload"
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--session", "cn_after_close",
            "--artifact-root", str(root),
            "--payload-root", str(payload_dir),
            "--now", "2026-07-17T15:27:00+08:00",
        ],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "**本窗口变化**" in r.stdout
    assert "综合判断" in r.stdout
    assert "**中长期研判**" not in r.stdout
    assert "**未来1–2周**" not in r.stdout
