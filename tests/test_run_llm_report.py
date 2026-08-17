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
