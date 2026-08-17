"""Tests for run_llm_report.py — the LLM-first report renderer.

TASK-013 (2026-08-17): 切回 LLM 渲染, 让 agent_task 的 render_discipline 生效,
失败降级确定性渲染。这里锁定的是 LLM 输出的专属文本门禁(_validate_llm_text),
不依赖真实 LLM 调用。
"""
import sys
from pathlib import Path

try:
    import pytest
except ImportError:  # pragma: no cover
    raise SystemExit("pytest required")

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

from run_llm_report import _validate_llm_text  # noqa: E402


def _payload(actions=None):
    if actions is None:
        actions = [{
            "display_label": "科创50ETF（588000）", "final_ratio": 0.6,
            "execution_status": "full", "executable_quantity": 1200,
        }]
    return {
        "session_type": "trading",
        "user_view": {"instruction_card": {"actions": actions}},
    }


def test_validate_llm_blocks_internal_tokens():
    """LLM 输出绝不能泄漏 position_id/decision_id/内部前缀。"""
    payload = _payload()
    bad = "科创50ETF 建议止盈 60%（position_id a_588000 决策 decision_id 转码）"
    errors = _validate_llm_text(payload, bad)
    assert any("internal token" in e for e in errors), errors


def test_validate_llm_accepts_clean_report():
    """合法 LLM 报告(含全部动作比例, 无内部 token)通过。"""
    payload = _payload()
    good = (
        "**交易指令卡**\n- 科创50ETF（588000）：止盈 60%\n\n"
        "**私人投资助理**\n多空结论震荡，验证沪深300站稳，证伪跌破。"
    )
    assert _validate_llm_text(payload, good) == []


def test_validate_llm_rejects_missing_action_ratio():
    """LLM 遗漏某个已获批动作的最终比例 -> 拦截(防漏动作)。"""
    payload = _payload()
    missing = "**私人投资助理**\n行情震荡，观望为主。"
    errors = _validate_llm_text(payload, missing)
    assert any("omits action final ratio 60%" in e for e in errors), errors



def test_validate_llm_accepts_intel_without_actions():
    """情报 session 无 instruction_card.actions 时不应误拦。"""
    payload = {
        "session_type": "intelligence",
        "user_view": {"instruction_card": {"actions": []}},
    }
    text = "地缘局势紧张，油价走高，详见情报正文。"
    assert _validate_llm_text(payload, text) == []


# ---------------------------------------------------------------------------
# TASK-014 (2026-08-17, B 方案): LLM 失败不再静默降级推送数据。
# 失败自动重试(_LLM_MAX_ATTEMPTS 次), 全失败 -> 标 fail(rc=3) + stdout 空
# + 失败日志落盘; 保留 --no-llm / --force-llm 手动路径。
# ---------------------------------------------------------------------------

import json as _json
import run_llm_report as _rlr
from tests.test_push_payload import _artifact


def _run_main(monkeypatch, tmp_path, args, *, now="2026-07-17T15:27:00+08:00"):
    """把合法 artifact 写进 tmp 后以 monkeypatch 的 sys.argv 跑 main()。"""
    root = tmp_path / "latest"
    root.mkdir(parents=True, exist_ok=True)
    (root / "cn_after_close.json").write_text(
        _json.dumps(_artifact(), ensure_ascii=False), encoding="utf-8"
    )
    argv = [
        "run_llm_report.py", "--session", "cn_after_close",
        "--artifact-root", str(root),
        "--payload-root", str(tmp_path / "payload"),
    ] + args
    if now:
        argv += ["--now", now]
    monkeypatch.setattr(_rlr.sys, "argv", argv)
    # 失败日志落到 tmp, 不污染 repo .local
    monkeypatch.setattr(_rlr, "_FAILURE_LOG_DIR", tmp_path / "errlog")
    return _rlr.main()


def test_main_fails_loud_after_3_llm_failures(monkeypatch, tmp_path, capsys):
    """LLM 3 次全失败 -> rc=3, stdout 不推送数据报告, 失败日志落盘 3 次。"""
    calls = {"n": 0}

    def boom(artifact):
        calls["n"] += 1
        raise RuntimeError("vllm down")

    monkeypatch.setattr(_rlr, "_render_llm", boom)
    rc = _run_main(monkeypatch, tmp_path, [])
    assert rc == 3
    assert calls["n"] == 3, f"expected 3 attempts, got {calls['n']}"
    out = capsys.readouterr()
    # 核心: 不推送数据报告
    assert out.out.strip() == ""
    # stderr 明示 fail + 手动重发提示
    assert "FAILED after 3 attempts" in out.err
    assert "--force-llm" in out.err
    # 失败日志落盘 3 次
    logf = tmp_path / "errlog" / "cn_after_close.log"
    assert logf.exists()
    content = logf.read_text(encoding="utf-8")
    assert content.count("attempt=") == 3


def test_main_retries_then_succeeds_on_second_attempt(monkeypatch, tmp_path, capsys):
    """LLM 第 1 次失败、第 2 次成功 -> rc=0 并输出 LLM 报告(重试生效)。"""
    calls = {"n": 0}

    def flaky(artifact):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("slow vllm")
        return "**交易指令卡**\n- 科创50ETF（588000）：止盈 60%\n\n**私人投资助理**\n多空震荡。"

    monkeypatch.setattr(_rlr, "_render_llm", flaky)
    rc = _run_main(monkeypatch, tmp_path, [])
    assert rc == 0
    assert calls["n"] == 2
    out = capsys.readouterr()
    assert "交易指令卡" in out.out


def test_force_llm_skips_age_gate_but_regular_gate_holds(monkeypatch, tmp_path, capsys):
    """--force-llm 跳过 age 上限(重发旧 artifact); 常规路径仍被 age 门禁拦。"""
    # 把 artifact 的 generated_at 改到 3 天前, 让 age 远超 45min 门禁。
    artifact = _artifact()
    artifact["generated_at"] = "2026-07-14T07:25:00+00:00"
    root = tmp_path / "latest"
    root.mkdir(parents=True, exist_ok=True)
    (root / "cn_after_close.json").write_text(
        _json.dumps(artifact, ensure_ascii=False), encoding="utf-8"
    )
    argv = ["run_llm_report.py", "--session", "cn_after_close",
            "--artifact-root", str(root),
            "--payload-root", str(tmp_path / "payload"),
            "--now", "2026-07-17T15:27:00+08:00"]
    # 1) 常规路径(无 force-llm): age 门禁拦截, rc=2
    monkeypatch.setattr(_rlr.sys, "argv", list(argv))
    monkeypatch.setattr(_rlr, "_FAILURE_LOG_DIR", tmp_path / "e1")
    rc = _rlr.main()
    assert rc == 2
    assert "outside allowed range" in capsys.readouterr().err
    # 2) --force-llm: 跳过 age 门禁放行。LLM mock 成成功 -> rc=0 输出报告。
    monkeypatch.setattr(
        _rlr, "_render_llm",
        lambda a: "**交易指令卡**\n- 科创50ETF（588000）：止盈 60%\n\n**私人投资助理**\n多空震荡。",
    )
    monkeypatch.setattr(_rlr.sys, "argv", list(argv) + ["--force-llm"])
    monkeypatch.setattr(_rlr, "_FAILURE_LOG_DIR", tmp_path / "e2")
    rc = _rlr.main()
    assert rc == 0, "force-llm 应跳过 age 门禁并成功渲染"
    assert "交易指令卡" in capsys.readouterr().out


def test_main_no_llm_renders_deterministic(monkeypatch, tmp_path, capsys):
    """--no-llm 显式手动兜底: 渲染确定性报告 rc=0 并落盘 payload。"""
    rc = _run_main(monkeypatch, tmp_path, ["--no-llm"])
    assert rc == 0
    out = capsys.readouterr()
    assert "本窗口变化" in out.out
    payload = tmp_path / "payload" / "cn_after_close.json"
    assert payload.exists()
