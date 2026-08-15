"""报告简洁化渲染纪律测试 (2026-08-15)。

锁定: agent_task.output_structure 的 render_discipline 必须存在并包含
"只输出决策结论、禁止罗列技术数据、多空对抗只给结论+证伪/验证、观察候选分组"
的纪律, 防止报告回退成"数据导出"破坏简洁性。
"""
from __future__ import annotations

import json

from stocks.engine.scheduled_analysis import build_agent_task, ScheduledSession


def _session(intent: str = "cn_after_close"):
    return ScheduledSession(
        id="test_session", market="cn", exchange_timezone="Asia/Shanghai",
        user_timezone="Asia/Shanghai", time="14:35", intent=intent,
        push="normal", enabled=True, duplicate_window_minutes=30,
        holidays=frozenset(), primary_market="cn",
    )


def test_output_structure_still_two_sections():
    """仍须恰好两段: 交易指令卡 + 私人投资助理。"""
    os = build_agent_task(_session())["output_structure"]
    names = [s["name"] for s in os["sections"]]
    assert names == ["交易指令卡", "私人投资助理"]


def test_render_discipline_exists_and_covers_noise_ctypes():
    """render_discipline 必须存在, 且覆盖 4 类报告噪音。"""
    os = build_agent_task(_session())["output_structure"]
    rd = os.get("render_discipline")
    assert rd and isinstance(rd, list) and len(rd) >= 3
    text = "\n".join(rd)
    # 1. 禁罗列技术指标/统计
    assert "MA" in text and "RSI" in text and "shadow_account" in text
    assert "不罗列" in text
    # 2. 多空对抗只给结论 + 证伪/验证
    assert "证伪" in text and "结论" in text
    # 3. 同一动作不重复展开
    assert "重复展开" in text
    # 4. 观察候选按语义分组
    assert "同类候选" in text and "分组" in text


def test_final_analysis_is_decision_brief_not_data_dump():
    """final_analysis_instructions 须声明报告是决策简报而非数据导出。"""
    task = build_agent_task(_session())
    assert "决策简报" in task["final_analysis_instructions"]
    assert "不是系统数据导出" in task["final_analysis_instructions"]


def test_instruction_card_renders_all_executable_not_truncated():
    """指令卡 section 指引须要求列出全部可执行动作, 不截断隐藏。"""
    card = [s for s in build_agent_task(_session())["output_structure"]["sections"]
            if s["name"] == "交易指令卡"][0]
    assert "列全部可执行动作" in card["content"]
    assert "actions_overflow" in card["content"]
