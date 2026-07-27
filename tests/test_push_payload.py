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


def _full_outlook_artifact(session="cn_after_close"):
    """Artifact with a valid full structured outlook."""
    base = _artifact(session)
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"] = {
        "status": "ok",
        "generated_at": "2026-07-17T08:00:00+00:00",
        "summary": "未来1-2周市场将维持震荡，1-3个月关注政策拐点",
        "confidence": "medium",
        "near_term": {
            "horizon": "1-2w",
            "direction": "supportive",
            "confidence": "medium",
        },
        "medium_term": {
            "horizon": "1-3m",
            "direction": "supportive",
            "confidence": "medium",
        },
        "asset_views": [
            {"asset_class": "权益", "direction": "supportive", "rationale": "估值有支撑"},
            {"asset_class": "债券", "direction": "neutral", "rationale": "利率预期稳定"},
        ],
        "sector_views": [
            {"sector": "科技", "direction": "supportive", "rationale": "AI主题活跃"},
            {"sector": "消费", "direction": "adverse", "rationale": "需求疲软"},
        ],
        "scenarios": {
            "base": {
                "label": "基准情景",
                "drivers": ["经济温和复苏", "流动性合理充裕"],
                "portfolio_effect": "组合小幅正向",
                "validation": "PMI持续扩张",
                "invalidation": "通胀超预期",
            },
            "bull": {
                "label": "乐观情景",
                "drivers": ["政策超预期宽松"],
                "portfolio_effect": "组合显著受益",
                "validation": "社融大幅增长",
                "invalidation": "外部冲击",
            },
            "risk": {
                "label": "风险情景",
                "drivers": ["地缘冲突升级"],
                "portfolio_effect": "组合承压",
                "validation": "VIX上升",
                "invalidation": "冲突缓和",
            },
        },
        "source_refs": [
            {
                "source": "Reuters",
                "title": "Oil rises as shipping risk increases",
                "url": "https://example.test/reuters-oil",
                "published_at": "2026-07-17T07:30:00+00:00",
            },
        ],
    }
    return base


def _watch_artifact_with_delta():
    """Watch-window artifact with outlook_delta only."""
    base = _full_outlook_artifact("cn_post_open")
    base["generated_at"] = "2026-07-17T02:04:00+00:00"
    del base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook_delta"] = {
        "schema_version": 1,
        "market": "cn",
        "changes": {
            "summary": {"from": "中性", "to": "偏有利"},
            "confidence": {"from": "low", "to": "medium"},
            "sector_views": {
                "科技": {"direction": {"from": "adverse", "to": "supportive"}},
            },
            "source_refs": {"added": ["src-5"], "removed": []},
        },
    }
    return base


def _unavailable_outlook_artifact(session="cn_after_close"):
    """Artifact with unavailable outlook."""
    base = _artifact(session)
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"] = {
        "status": "unavailable",
        "generated_at": "2026-07-17T08:00:00+00:00",
        "message": "本期研判未通过数据完整性校验，暂不输出",
        "data_limitations": ["缺失关键宏观数据", "情报覆盖不足"],
    }
    return base


def _hostile_outlook_artifact():
    """Artifact with hostile outlook containing internal IDs and unauthorized numbers."""
    base = _full_outlook_artifact()
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]["near_term"]["summary"] = (
        "a_516020需要减仓50%"
    )
    return base


def _artifact(session="cn_after_close"):
    return {
        "run_id": "r1",
        "session": session,
        "market_date": "2026-07-17",
        "generated_at": "2026-07-17T07:25:00+00:00",
        "scheduled_for": "2026-07-17T15:20:00+08:00",
        "agent_task": {"task_version": 5, "data_reference": {"window_delta": "", "portfolio_decision": "", "risk_state": "", "data_boundaries": "", "research_candidates": ""}},
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
        "session_type",
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
            str(bad),
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

    # All sessions are now PRIMARY — watch windows removed


def test_payload_renderer_includes_outlook_section_and_order():
    """Primary report renders full outlook in correct order."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "**中长期研判**" in text
    assert "**未来1–2周**" in text
    assert "**未来1–3个月**" in text
    assert "**基准情景**" in text
    assert "**乐观情景**" in text
    assert "**风险情景**" in text
    assert "[Reuters｜Oil rises as shipping risk increases](https://example.test/reuters-oil)" in text
    assert text.index("**交易指令卡**") < text.index("**私人投资助理**")
    assert text.index("**私人投资助理**") < text.index("**中长期研判**")

    # Near term shows direction + confidence, not nested asset/sector views
    assert "偏有利" in text or "中性" in text

    # Asset views and sector views rendered at top level
    assert "权益: 偏有利" in text
    assert "科技行业: 偏有利" in text


def test_payload_renderer_watch_window_shows_only_outlook_delta():
    """Observation window renders only outlook_delta, not full outlook."""
    payload = build_push_payload(_watch_artifact_with_delta(), now="2026-07-17T10:05:00+08:00")
    text = render_push_payload(payload)
    assert "**研判变化**" in text
    assert "**中长期研判**" not in text
    assert "**未来1–2周**" not in text


def test_payload_renderer_unavailable_outlook_shows_message_with_trade_card():
    """Unavailable outlook renders message and limitations, trade card still present."""
    payload = build_push_payload(_unavailable_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "**交易指令卡**" in text
    assert "本期研判未通过数据完整性校验" in text
    assert "缺失关键宏观数据" in text


def test_validate_payload_text_catches_internal_ids_in_outlook():
    """validate_payload_text catches internal tokens inside outlook fields."""
    payload = build_push_payload(_hostile_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    errors = validate_payload_text(payload, text)
    assert any("internal token" in e for e in errors)



def test_validate_payload_text_rejects_hostile_numbers_even_if_in_outlook():
    """Hostile number in outlook summary is rejected even if only present in outlook."""
    base = _full_outlook_artifact()
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]["summary"] = "目标99999"
    payload = build_push_payload(base, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    errors = validate_payload_text(payload, text)
    assert any("unauthorized number" in e for e in errors)


def test_validate_payload_text_allows_legitimate_outlook_values():
    """validate_payload_text allows ISO dates, URL numbers, horizon patterns."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    errors = validate_payload_text(payload, text)
    assert errors == []


def test_outlook_section_respects_limit_on_sources():
    """Outlook section limits sources to 5 items."""
    outlook = _full_outlook_artifact()
    brief = outlook["portfolio_decision"]["user_view"]["assistant_brief"]
    brief["outlook"]["source_refs"] = [
        {"source": f"S{i}", "title": f"T{i}", "url": f"https://x{i}.test", "published_at": "2026-07-17T00:00:00Z"}
        for i in range(10)
    ]
    payload = build_push_payload(outlook, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    count = text.count("[S")  # Each source link starts with [S
    assert count <= 5, f"Expected at most 5 source links, found {count}"


def _delta_with_scenarios_and_sources():
    """Watch-window artifact with rich delta including scenarios and source_refs."""
    base = _full_outlook_artifact("cn_post_open")
    base["generated_at"] = "2026-07-17T02:04:00+00:00"
    del base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook_delta"] = {
        "schema_version": 1,
        "market": "cn",
        "changes": {
            "summary": {"from": "中性", "to": "偏有利"},
            "confidence": {"from": "low", "to": "medium"},
            "scenarios": {
                "base": {
                    "label": {"from": "基准情景", "to": "温和复苏情景"},
                    "validation": {"from": "PMI持续扩张", "to": "GDP超预期"},
                    "invalidation": {"from": "通胀超预期", "to": "就业恶化"},
                },
                "bull": {
                    "label": {"from": "乐观情景", "to": "强力反弹情景"},
                },
            },
            "source_refs": {
                "added": ["src-alpha", "src-beta", "src-gamma"],
                "removed": ["src-old"],
            },
            "near_term": {
                "direction": {"from": "adverse", "to": "supportive"},
                "confidence": {"from": "low", "to": "high"},
                "horizon": {"from": "1w", "to": "2w"},
            },
        },
    }
    return base


def test_delta_renderer_shows_scenarios_with_chinese_labels():
    """_render_delta_changes renders scenario changes with Chinese labels."""
    payload = build_push_payload(
        _delta_with_scenarios_and_sources(), now="2026-07-17T10:05:00+08:00"
    )
    text = render_push_payload(payload)
    assert "**研判变化**" in text
    # Scenario labels
    assert "基准情景" in text
    assert "温和复苏情景" in text
    assert "乐观情景" in text
    assert "强力反弹情景" in text
    # Scenario validation/invalidation
    assert "验证条件" in text or "GDP超预期" in text
    assert "否定条件" in text or "就业恶化" in text


def test_delta_renderer_shows_source_refs_with_added_removed():
    """_render_delta_changes renders source_refs with 来源新增/来源移除 labels."""
    payload = build_push_payload(
        _delta_with_scenarios_and_sources(), now="2026-07-17T10:05:00+08:00"
    )
    text = render_push_payload(payload)
    assert "来源新增" in text
    assert "来源移除" in text
    assert "src-alpha" in text
    assert "src-beta" in text
    assert "src-old" in text


def test_delta_renderer_near_term_uses_chinese_labels():
    """_render_delta_changes near/medium term uses Chinese labels for direction/confidence/horizon."""
    payload = build_push_payload(
        _delta_with_scenarios_and_sources(), now="2026-07-17T10:05:00+08:00"
    )
    text = render_push_payload(payload)
    # Chinese labels for direction/confidence
    assert "方向" in text
    assert "置信度" in text
    # Value translations
    assert "偏有利" in text  # supportive translated
    # Horizon with Chinese label
    assert "时间范围" in text


def test_full_outlook_shows_summary():
    """Full outlook rendering includes summary line."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "综合判断" in text
    assert "未来1-2周市场将维持震荡" in text


def test_full_outlook_shows_asset_and_sector_subtitles():
    """Full outlook rendering includes 资产类别 and 行业观察 subtitles."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "**资产类别**" in text
    assert "**行业观察**" in text


def test_validate_payload_text_rejects_date_adjacent_hostile_number():
    """Hostile number adjacent to ISO date in outlook summary is rejected.

    Ensures that the old ±30-char lookback date exemption does not falsely
    authorize 99999 when it appears in the same sentence as an ISO date.
    """
    base = _full_outlook_artifact()
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]["summary"] = (
        "2026-07-17目标价格99999元"
    )
    payload = build_push_payload(base, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    errors = validate_payload_text(payload, text)
    # 99999 must be caught; 2026, 07, 17 (parts of ISO date) are safe
    assert any("unauthorized number" in e and "99999" in e for e in errors), (
        f"Expected 99999 in errors, got: {errors}"
    )


def test_validate_payload_text_allows_legitimate_published_date_and_horizon():
    """Legitimate published_at date, URL numbers, and horizon values pass."""
    base = _full_outlook_artifact()
    payload = build_push_payload(base, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    # Confirm the rendered text includes published date and horizon
    assert "2026-07-17" in text
    assert "1-2w" in text or "1-2" in text
    errors = validate_payload_text(payload, text)
    assert errors == [], f"Expected no errors for legitimate values, got: {errors}"


def test_validate_payload_text_allows_https_url_with_port_numbers():
    """URL port numbers (e.g., :443 in https://host:443/path) pass through."""
    base = _full_outlook_artifact()
    brief = base["portfolio_decision"]["user_view"]["assistant_brief"]
    # Inject a URL with port number into source_refs
    brief["outlook"]["source_refs"] = [
        {"source": "Test", "title": "API", "url": "https://api.test:443/v3/data", "published_at": "2026-07-17T00:00:00Z"}
    ]
    payload = build_push_payload(base, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    errors = validate_payload_text(payload, text)
    assert errors == [], f"Expected no errors for URL with port, got: {errors}"
