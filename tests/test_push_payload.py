from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.build_push_payload import (
    build_push_payload,
    render_push_payload,
    validate_payload_text,
)


def _artifact(session="cn_after_close"):
    return {
        "run_id": "r1",
        "session": session,
        "market_date": "2026-07-17",
        "generated_at": "2026-07-17T07:25:00+00:00",
        "scheduled_for": "2026-07-17T15:20:00+08:00",
        "agent_task": {"task_version": 5},
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {
                    "status": "manual_review",
                    "status_label": "等待人工确认",
                    "actions": [],
                    "no_action_reasons": ["沪深300ETF（510300）：方向冲突，需人工确认"],
                    "next_checkpoint": "下一交易日盘前复核",
                },
                "assistant_brief": {
                    "why": ["沪深300ETF（510300）：方向冲突，需人工确认"],
                    "conflict_summary": [
                        {"action_label": "减仓", "count": 3},
                        {"action_label": "止盈", "count": 1},
                    ],
                    "do_not_do": ["化工ETF（516020）：数据异常，等待核对"],
                    "cash": {
                        "immediate": {"label": "现在能用", "amount_cny": 345134.15},
                        "settling": {"label": "到账途中", "amount_cny": 0},
                        "strategic_exit": {"label": "卖出后才能用", "amount_cny": 613469.73},
                        "locked": {"label": "不能动", "amount_cny": 541303.61},
                    },
                    "risk": {
                        "label": "防御状态",
                        "transition": "状态未变",
                        "suspend_accumulation": True,
                        "reasons": ["地缘政治风险达到临界级别"],
                        "release_condition": "等待风险状态满足解除条件",
                    },
                    "data_notes": ["美股行情数据已过时（截止 2026-07-16 20:00 UTC）"],
                    "research": [],
                },
            }
        },
    }


def test_payload_contains_only_delivery_metadata_and_user_view():
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    assert set(payload) == {
        "payload_version",
        "session_label",
        "market_date",
        "delivery",
        "user_view",
    }
    text = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        "position_id",
        "approved_actions",
        "unresolved_conflicts",
        "risk_state",
        "data_boundaries",
    ):
        assert forbidden not in text


def test_renderer_preserves_signal_classification_and_never_invents_totals():
    text = render_push_payload(build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00"))
    assert "- 减仓: 3 项" in text
    assert "- 止盈: 1 项" in text
    assert "4个减仓信号" not in text
    assert "manual_review" not in text
    assert "approved_actions" not in text
    assert "---" not in text


def test_payload_validator_rejects_unknown_numbers_and_internal_tokens():
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    assert validate_payload_text(payload, render_push_payload(payload)) == []
    errors = validate_payload_text(payload, "manual_review；MA20偏离13.3%；建议减75%")
    assert any("internal token" in error for error in errors)
    assert any("unauthorized number" in error for error in errors)


def test_cli_fail_closed_and_writes_atomic_payload(tmp_path):
    artifact = tmp_path / "artifact.json"
    output = tmp_path / "push.json"
    artifact.write_text(json.dumps(_artifact(), ensure_ascii=False), encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "build_push_payload.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--artifact",
            str(artifact),
            "--session",
            "cn_after_close",
            "--now",
            "2026-07-17T15:27:00+08:00",
            "--output",
            str(output),
            "--format",
            "markdown",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert "**交易指令卡**" in result.stdout
    bad = json.loads(artifact.read_text())
    bad["portfolio_decision"].pop("user_view")
    artifact.write_text(json.dumps(bad), encoding="utf-8")
    failed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--artifact",
            str(artifact),
            "--session",
            "cn_after_close",
            "--now",
            "2026-07-17T15:27:00+08:00",
            "--output",
            str(output),
            "--format",
            "markdown",
        ],
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert failed.stdout == ""



def test_primary_no_action_always_sends_and_watch_no_action_is_silent():
    primary = _artifact("cn_after_close")
    primary["portfolio_decision"]["user_view"]["instruction_card"].update({
        "status": "no_action", "status_label": "今日无需操作", "actions": [],
    })
    assert build_push_payload(primary, now="2026-07-17T15:27:00+08:00")["delivery"] == "send"

    watch = _artifact("cn_open_watch")
    watch["scheduled_for"] = "2026-07-17T10:00:00+08:00"
    watch["generated_at"] = "2026-07-17T02:04:00+00:00"
    watch["portfolio_decision"]["user_view"]["instruction_card"].update({
        "status": "no_action", "status_label": "今日无需操作", "actions": [],
    })
    assert build_push_payload(watch, now="2026-07-17T10:05:00+08:00")["delivery"] == "silent"
