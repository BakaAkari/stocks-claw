from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.build_push_payload import (
    build_push_payload,
    render_push_payload,
    validate_payload_text,
    validate_push_truth,
)


def _action(**overrides):
    action = {
        "display_label": "化工ETF（516020）",
        "action_label": "减仓",
        "ratio": 0.25,
        "final_ratio": 0.25,
        "reason_summary": "化工ETF（516020）：减仓 25%",
        "execution_status": "full",
        "executable_quantity": 2100.0,
    }
    action.update(overrides)
    return action


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
    """Artifact with hostile outlook containing an internal ID, injected into the
    top-level ``summary`` field that the renderer actually emits (``near_term`` only
    renders direction/confidence/horizon, so a value there would never reach the
    rendered text and could never be caught by any validator, upstream or downstream).
    """
    base = _full_outlook_artifact()
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]["summary"] = (
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
                        "available_now": {"label": "现在能用", "amount_cny": 345134.15},
                        "confirmed_settling": {"label": "到账途中", "amount_cny": 0},
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
    # E2: conflict counts are no longer rendered as separate lines.
    assert "**可执行动作**" in text
    assert "**禁止与延后**" in text
    assert "**组合与检查点**" in text
    assert "manual_review" not in text
    assert "approved_actions" not in text
    assert "---" in text  # section 之间用 --- 分隔线分割


def test_renderer_reads_canonical_cash_keys_not_legacy_immediate_settling():
    """The cash section must render the actual available_now/confirmed_settling
    amounts; falling back to legacy immediate/settling keys (or missing keys
    entirely) would silently render the "资金待确认" placeholder instead
    (TASK-001D correction item 1).

    M1 additionally collapses zero-valued confirmed_settling from the cash line
    so this test injects a non-zero value.
    """
    artifact = _artifact()
    # Inject non-zero confirmed_settling so M1 collapse rule doesn't hide it
    artifact["portfolio_decision"]["user_view"]["assistant_brief"]["cash"]["confirmed_settling"] = {
        "label": "到账途中", "amount_cny": 12_345.0,
    }
    text = render_push_payload(build_push_payload(artifact, now="2026-07-17T15:27:00+08:00"))
    assert "现在能用 ¥345,134" in text
    assert "到账途中 ¥12,345" in text
    assert "资金待确认" not in text

def test_payload_validator_rejects_unknown_numbers_and_internal_tokens():
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    assert validate_payload_text(payload, render_push_payload(payload)) == []
    errors = validate_payload_text(payload, "manual_review；MA20偏离13.3%；建议减75%")
    assert any("internal token" in error for error in errors)
    assert any("unauthorized number" in error for error in errors)


def test_validate_push_truth_accepts_clean_executable_action():
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    payload["user_view"]["instruction_card"]["actions"] = [_action()]
    payload["user_view"]["instruction_card"]["status"] = "action_required"
    payload["user_view"]["assistant_brief"]["why"] = [
        "化工ETF（516020）：减仓 25%"
    ]
    assert validate_push_truth(payload) == []


def test_validate_push_truth_rejects_text_percentage_disagreeing_with_final_ratio():
    """TASK-001E1 scope item 7 / defect 1: a raw 50% baked into reason_summary
    text must be rejected even though final_ratio itself is 0.25."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    payload["user_view"]["instruction_card"]["actions"] = [
        _action(reason_summary="化工ETF（516020）：减仓 50%")
    ]
    errors = validate_push_truth(payload)
    assert any("action text percentage" in e for e in errors)


def test_validate_push_truth_rejects_zero_or_deferred_action():
    """Defect 2: a deferred_min_unit/zero-ratio action must never reach the
    built payload's instruction_card.actions."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    payload["user_view"]["instruction_card"]["actions"] = [
        _action(final_ratio=0.0, execution_status="deferred_min_unit", executable_quantity=0.0)
    ]
    errors = validate_push_truth(payload)
    assert any("zero or missing final_ratio" in e for e in errors)
    assert any("non-executable action" in e for e in errors)


def test_validate_push_truth_rejects_action_research_identity_overlap():
    """Defect 4: the same instrument must not be both an approved action and
    a research candidate in the built payload."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    payload["user_view"]["instruction_card"]["actions"] = [_action()]
    payload["user_view"]["assistant_brief"]["research"] = [
        {"display_label": "化工ETF（516020）", "action_hint": "仅供观察"}
    ]
    errors = validate_push_truth(payload)
    assert any("appears in both actions and research" in e for e in errors)


def test_validate_push_truth_rejects_successful_outlook_without_source_refs():
    """Defect 5: a successful Outlook narrative with source_refs=[] must be
    rejected at the delivery gate even if it slipped past upstream."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    payload["user_view"]["assistant_brief"]["outlook"] = {
        "status": "ok", "summary": "组合整体研判偏正面", "source_refs": [],
    }
    errors = validate_push_truth(payload)
    assert any("no source_refs" in e for e in errors)


def test_cli_rejects_hostile_action_text_before_writing_output(tmp_path):
    """TASK-001E1 acceptance test: a hostile payload (contradictory action
    percentage text) must be rejected by the build gate, and — unlike the
    existing markdown-only validate_payload_text check — never reach disk."""
    artifact = tmp_path / "artifact.json"
    output = tmp_path / "push.json"
    hostile = _artifact()
    hostile["portfolio_decision"]["user_view"]["instruction_card"]["actions"] = [_action(
        reason_summary="化工ETF（516020）：减仓 50%"
    )]
    hostile["portfolio_decision"]["user_view"]["instruction_card"]["status"] = "action_required"
    artifact.write_text(json.dumps(hostile, ensure_ascii=False), encoding="utf-8")
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
    assert result.returncode == 2
    assert "action text percentage" in result.stderr
    assert not output.exists()


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
    assert "**可执行动作**" in result.stdout
    assert "**组合与检查点**" in result.stdout
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
    """M1 six-section report has 走势研判 and 提前布局 as first-class sections."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "**本窗口变化**" in text
    assert "**走势研判**" in text
    assert "**可执行动作**" in text
    assert "**提前布局**" in text
    assert "**禁止与延后**" in text
    assert "**组合与检查点**" in text
    # Legacy section headings removed
    assert "**中长期研判**" not in text
    assert "**资产类别**" not in text
    assert "**行业观察**" not in text
    assert "**组合影响**" not in text
    assert "**下一检查点**" not in text
    assert text.index("**本窗口变化**") < text.index("**走势研判**")
    assert text.index("**走势研判**") < text.index("**可执行动作**")
    assert text.index("**可执行动作**") < text.index("**提前布局**")
    assert text.index("**提前布局**") < text.index("**禁止与延后**")
    assert text.index("**禁止与延后**") < text.index("**组合与检查点**")


def test_payload_renderer_watch_window_shows_only_outlook_delta():
    """Delta is rendered inside the concise window-change section."""
    payload = build_push_payload(_watch_artifact_with_delta(), now="2026-07-17T10:05:00+08:00")
    text = render_push_payload(payload)
    assert "**本窗口变化**" in text
    assert "综合判断: 中性 → 偏有利" in text
    assert "置信度: 低 → 中" in text
    # Legacy full-outlook subheadings removed
    assert "**中长期研判**" not in text
    assert "**未来1–2周**" not in text


def test_payload_renderer_unavailable_outlook_shows_message_with_trade_card():
    """Unavailable outlook renders inside the 走势研判 section with a friendly downgrade."""
    payload = build_push_payload(_unavailable_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "**可执行动作**" in text
    assert "**走势研判**" in text
    # Never leak internal English disabled/config messages to users
    assert "outlook synthesizer disabled" not in text
    assert "not configured" not in text
    # Friendly downgrade wording
    assert "中长期研判" in text
    assert "**中长期研判**" not in text


def test_validate_payload_text_catches_internal_ids_in_outlook():
    """validate_payload_text catches internal tokens inside outlook fields."""
    payload = build_push_payload(_hostile_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    errors = validate_payload_text(payload, text)
    assert any("internal token" in e for e in errors)



def test_hostile_outlook_number_is_authority_of_upstream_outlook_validator_not_push():
    """Single-semantic-authority boundary: push validation no longer re-scans numbers
    inside the outlook section (``**中长期研判**`` onward) — that financial-semantics
    check belongs to ``outlook_validation.validate_structured_outlook`` upstream, which
    has full evidence context to authorize numbers and push does not. This test proves
    there is no coverage gap: the same hostile value is rejected upstream and,
    separately, confirmed to pass through push's (integrity-only) text validator
    untouched by design.
    """
    from stocks.engine.outlook_validation import validate_structured_outlook

    outlook = {
        "status": "ok",
        "generated_at": "2026-07-17T08:00:00+00:00",
        "summary": "目标99999",
        "confidence": "medium",
        "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "medium", "rationale": "test"},
        "medium_term": {"horizon": "1-3m", "direction": "supportive", "confidence": "medium", "rationale": "test"},
        "scenarios": {
            "base": {"label": "基准", "drivers": [], "portfolio_effect": "无", "validation": [], "invalidation": []},
            "bull": {"label": "乐观", "drivers": [], "portfolio_effect": "无", "validation": [], "invalidation": []},
            "risk": {"label": "风险", "drivers": [], "portfolio_effect": "无", "validation": [], "invalidation": []},
        },
        "source_refs": [{"id": "src-1", "source": "Reuters", "title": "T", "url": "http://x", "published_at": "2026-07-17T00:00:00+00:00"}],
    }
    upstream_errors = validate_structured_outlook(outlook, evidence={})
    assert any(
        "unauthorized number" in e or "numeric claim" in e or "unauthorized source" in e
        for e in upstream_errors
    ), f"expected upstream outlook validator to reject hostile content, got: {upstream_errors}"

    from stocks.engine.outlook_validation import _check_numeric_authority
    direct_errors: list[str] = []
    _check_numeric_authority(outlook, evidence={}, errors=direct_errors)
    assert any("unauthorized number" in e for e in direct_errors), (
        f"expected direct numeric authority check to reject 99999, got: {direct_errors}"
    )

    base = _full_outlook_artifact()
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]["summary"] = "目标99999"
    payload = build_push_payload(base, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    push_errors = validate_payload_text(payload, text)
    assert push_errors == [], (
        "push validation must not re-guess outlook numeric semantics with less "
        f"evidence than the upstream validator; got: {push_errors}"
    )


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
    """Scenario changes render with Chinese labels when inside §1's 3-line cap.

    M1 caps 本窗口变化 at 3 concise delta lines; scenario entries sort last
    in the delta renderer, so this test keeps only scenario changes to prove
    the label rendering itself.
    """
    artifact = _delta_with_scenarios_and_sources()
    brief = artifact["portfolio_decision"]["user_view"]["assistant_brief"]
    changes = brief["outlook_delta"]["changes"]
    brief["outlook_delta"]["changes"] = {"scenarios": changes["scenarios"]}
    payload = build_push_payload(artifact, now="2026-07-17T10:05:00+08:00")
    text = render_push_payload(payload)
    assert "**本窗口变化**" in text
    assert "基准情景" in text
    assert "温和复苏情景" in text
    # Scenario validation/invalidation labels visible
    assert any(k in text for k in ("验证条件", "GDP超预期", "否定条件", "就业恶化"))


def test_delta_renderer_caps_window_changes_at_three_lines():
    """M1: 本窗口变化 renders at most 3 concise delta lines (plus source lines)."""
    payload = build_push_payload(
        _delta_with_scenarios_and_sources(), now="2026-07-17T10:05:00+08:00"
    )
    text = render_push_payload(payload)
    section = text[text.index("**本窗口变化**"):text.index("**走势研判**")]
    delta_bullets = [
        ln for ln in section.splitlines()
        if ln.strip().startswith("- ") and not ln.startswith("- 来源")
    ]
    assert len(delta_bullets) <= 3, f"{len(delta_bullets)} delta lines"


def test_delta_renderer_shows_source_refs_with_added_removed():
    """Concise window-change section renders source_refs with 来源新增/来源移除 labels."""
    payload = build_push_payload(
        _delta_with_scenarios_and_sources(), now="2026-07-17T10:05:00+08:00"
    )
    text = render_push_payload(payload)
    assert "**本窗口变化**" in text
    assert "来源新增" in text
    assert "来源移除" in text
    assert "src-alpha" in text
    assert "src-beta" in text
    assert "src-old" in text


def test_delta_renderer_near_term_uses_chinese_labels():
    """Concise delta uses Chinese labels for direction/confidence/horizon."""
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
    """Concise window-change section includes the outlook summary."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "综合判断" in text
    assert "未来1-2周市场将维持震荡" in text


def test_full_outlook_shows_asset_and_sector_subtitles():
    """Concise window-change section still shows asset and sector direction lines."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "权益: 偏有利" in text
    assert any(line in text for line in ("债券: 中性", "科技行业: 偏有利", "消费行业: 偏不利"))
    assert "**资产类别**" not in text
    assert "**行业观察**" not in text


def test_date_adjacent_hostile_outlook_number_is_upstream_authority_not_push():
    """Hostile number sharing a sentence with an ISO date is upstream's job.

    Single-semantic-authority boundary (see
    test_hostile_outlook_number_is_authority_of_upstream_outlook_validator_not_push):
    outlook numeric semantics belong to
    ``outlook_validation.validate_structured_outlook``, which has the evidence
    context to authorize numbers; push strips the outlook section from number
    scanning entirely. This proves the date-adjacency overlap fix is caught
    upstream, and confirms push does not (and structurally cannot) re-check it.
    """
    from stocks.engine.outlook_validation import validate_structured_outlook

    outlook = {
        "status": "ok",
        "generated_at": "2026-07-17T08:00:00+00:00",
        "summary": "2026-07-17目标价格99999元",
        "confidence": "medium",
        "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "medium"},
        "medium_term": {"horizon": "1-3m", "direction": "supportive", "confidence": "medium"},
        "source_refs": [],
    }
    upstream_errors = validate_structured_outlook(outlook, evidence={})
    assert any(
        "unauthorized number" in e and "99999" in e for e in upstream_errors
    ), f"expected upstream outlook validator to reject date-adjacent 99999, got: {upstream_errors}"

    base = _full_outlook_artifact()
    base["portfolio_decision"]["user_view"]["assistant_brief"]["outlook"]["summary"] = (
        "2026-07-17目标价格99999元"
    )
    payload = build_push_payload(base, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    push_errors = validate_payload_text(payload, text)
    assert push_errors == [], (
        "push validation must not re-guess outlook numeric semantics with less "
        f"evidence than the upstream validator; got: {push_errors}"
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



def test_concise_report_has_six_sections_in_order():
    """M1: trading report has exactly the six required sections in order."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    headings = ["本窗口变化", "走势研判", "可执行动作", "提前布局", "禁止与延后", "组合与检查点"]
    positions = [text.index(f"**{h}**") for h in headings]
    assert positions == sorted(positions)


def test_concise_report_no_legacy_headings():
    """M1: legacy verbose section headings (including old 5-section E2 pair) are removed."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    for banned in (
        "交易指令卡", "私人投资助理", "为什么这样安排", "待人工确认的信号分类",
        "仅供观察", "中长期研判", "资产类别", "行业观察", "基准情景", "乐观情景", "风险情景",
        "组合影响", "下一检查点",
    ):
        assert f"**{banned}**" not in text, f"banned heading {banned!r} found in text"


def test_concise_report_size_limits():
    """M1: rendered trading report is <= 55 non-empty lines and <= 1800 Chinese chars."""
    payload = build_push_payload(_full_outlook_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    non_empty = [ln for ln in text.splitlines() if ln.strip()]
    assert len(non_empty) <= 55, f"{len(non_empty)} non-empty lines"
    import re
    cn_count = len(re.findall(r"[一-鿿]", text))
    assert cn_count <= 1800, f"{cn_count} Chinese characters"


def test_concise_report_action_limit_and_fields():
    """M1: at most 3 actions; each action shows required fields; no checkpoint repeat."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    actions = [
        _action(display_label=f"化工ETF（516020）-{i}", reason_summary=f"化工ETF（516020）-{i}：减仓 25%")
        for i in range(5)
    ]
    payload["user_view"]["instruction_card"]["actions"] = actions
    payload["user_view"]["instruction_card"]["status"] = "action_required"
    payload["user_view"]["assistant_brief"]["why"] = [a["reason_summary"] for a in actions]
    text = render_push_payload(payload)
    action_bullets = [ln for ln in text.splitlines() if ln.strip().startswith("- **减仓｜")]
    assert len(action_bullets) <= 3, f"{len(action_bullets)} action bullet lines"
    assert any("减仓｜" in ln for ln in text.splitlines())
    # Next-checkpoint text lives only in §6 组合与检查点, never inside action lines
    checkpoint_text = "下一交易日盘前复核"
    action_lines = text[:text.index("**组合与检查点**")]
    assert checkpoint_text not in action_lines


def test_concise_report_research_becomes_setup_section():
    """M1: research candidates become their own **提前布局** section (top 2-3), not a count line."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    payload["user_view"]["assistant_brief"]["research"] = [
        {"display_label": "芯片ETF（159995）", "action_hint": "深跌超卖左侧试仓", "composite_score": 0.42},
        {"display_label": "医药ETF（512010）", "action_hint": "回踩短均线附近", "composite_score": 0.31},
    ]
    text = render_push_payload(payload)
    setup = text[text.index("**提前布局**"):text.index("**禁止与延后**")]
    # Both candidates appear with their hint
    assert "芯片ETF（159995）" in setup
    assert "医药ETF（512010）" in setup
    # No legacy "研究候选 N 个" count-line in the blocked section
    blocked = text[text.index("**禁止与延后**"):text.index("**组合与检查点**")]
    assert "研究候选" not in blocked


# ── Adversarial review P0-2 / P0-3 regressions ─────────────────────────


def _executable_action_payload():
    artifact = _artifact()
    card = artifact["portfolio_decision"]["user_view"]["instruction_card"]
    card["status"] = "action_required"
    card["status_label"] = "需要操作"
    card["actions"] = [
        _action(
            estimated_amount_cny=50000.0,
            amount_is_estimate=False,
            cancel_condition="条件不再成立时取消",
            settlement_display="T+1",
            settlement_rule="T+1",
            next_checkpoint="下一交易窗口复核",
            platform="A股证券账户",
            operation_channel="登录证券账户执行",
            decision_reason="通过裁决",
            evidence_summary="signal=reduce",
            original_ratio=0.25,
        )
    ]
    card["actions_overflow"] = 2
    card["no_action_reasons"] = []
    artifact["portfolio_decision"]["user_view"]["assistant_brief"]["why"] = [
        "化工ETF（516020）：减仓 25%"
    ]
    return artifact


def test_number_gate_scans_executable_action_section_after_e2():
    """P0-2: pre-fix, validate_payload_text truncated the text at the first
    本窗口变化 heading, so tampered amounts inside 可执行动作 were never
    scanned. The gate must now catch them."""
    artifact = _executable_action_payload()
    payload = build_push_payload(artifact, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert validate_payload_text(payload, text) == []
    tampered = text.replace("约 ¥50,000", "约 ¥88,888")
    assert tampered != text
    errors = validate_payload_text(payload, tampered)
    assert any("88888" in e for e in errors)


def test_overflow_actions_line_renders_and_passes_number_gate():
    """P0-3: the overflow count is shown on the card and its number is
    authorized (it is a payload value)."""
    artifact = _executable_action_payload()
    payload = build_push_payload(artifact, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "另有 2 个获批动作超出展示上限" in text
    assert validate_payload_text(payload, text) == []


# ── M1 gap-closure regressions (2026-07-31) ─────────────────────────────


def test_manual_review_reference_line_with_quantity_and_amount():
    """M1 truth-gate audit trail: each manual-review conflict carries the
    rule-driven reference values (signal, ratio, quantity, amount) as a
    参考: sub-line, and every number in it is payload-authorized."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    card = payload["user_view"]["instruction_card"]
    card["suppressed_actions_reference"] = [{
        "display_label": "沪深300ETF（510300）",
        "signal_type": "减仓",
        "ratio": 0.2,
        "executable_quantity": 300,
        "estimated_amount_cny": 12000.0,
    }]
    text = render_push_payload(payload)
    section = text[text.index("**可执行动作**"):text.index("**提前布局**")]
    conflict_pos = section.index("沪深300ETF（510300）")
    ref_pos = section.index("参考: 减仓 20%，参考数量 300，参考金额 ¥12,000")
    assert ref_pos > conflict_pos, "参考 line must follow its conflict"
    assert validate_payload_text(payload, text) == []


def test_unavailable_outlook_without_message_uses_m1_fallback():
    """M1: unavailable outlook without a sanitized message renders the M2
    pending fallback line, never internal English."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    brief = payload["user_view"]["assistant_brief"]
    brief["outlook"] = {"status": "unavailable"}
    text = render_push_payload(payload)
    assert "中长期研判暂不可用（研判待复核）" in text
    assert "outlook synthesizer disabled" not in text
    assert "not configured" not in text


def test_missing_outlook_uses_m1_fallback():
    """M1: no outlook key at all also renders the M2 pending fallback line."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    assert "中长期研判暂不可用（研判待复核）" in text


def test_setup_section_orders_by_composite_score():
    """M1: 提前布局 sorts candidates by composite_score (score alias) desc."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    brief = payload["user_view"]["assistant_brief"]
    brief["research"] = [
        {"display_label": "医药ETF（512010）", "action_hint": "回踩短均线附近", "composite_score": 0.31},
        {"display_label": "芯片ETF（159995）", "action_hint": "深跌超卖左侧试仓", "composite_score": 0.42},
    ]
    text = render_push_payload(payload)
    setup = text[text.index("**提前布局**"):text.index("**禁止与延后**")]
    assert setup.index("芯片ETF") < setup.index("医药ETF")


def test_setup_section_skips_reassess_when_same_as_report_checkpoint():
    """M1: reassess_after equal to the report's own next checkpoint is not
    repeated as a 复核 line; a different one is shown."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    brief = payload["user_view"]["assistant_brief"]
    # card next_checkpoint in the fixture is 下一交易日盘前复核
    brief["research"] = [
        {"display_label": "芯片ETF（159995）", "action_hint": "左侧试仓", "score": 0.5,
         "reassess_after": "下一交易日盘前复核"},
        {"display_label": "医药ETF（512010）", "action_hint": "回踩布局", "score": 0.4,
         "reassess_after": "风险解除后再评估"},
    ]
    text = render_push_payload(payload)
    setup = text[text.index("**提前布局**"):text.index("**禁止与延后**")]
    assert "复核: 下一交易日盘前复核" not in setup
    assert "复核: 风险解除后再评估" in setup


def test_strategic_exit_shown_with_pending_review_sell():
    """M1: 卖出后可释放 renders when an approved-but-review-pending sell
    exists (cash.pending_sell), even without an executable sell action."""
    payload = build_push_payload(_artifact(), now="2026-07-17T15:27:00+08:00")
    brief = payload["user_view"]["assistant_brief"]
    text_without = render_push_payload(payload)
    assert "卖出后可释放" not in text_without
    brief["cash"]["pending_sell"] = True
    text_with = render_push_payload(payload)
    assert "卖出后可释放 ¥613,470" in text_with
    assert validate_payload_text(payload, text_with) == []


# ── R5-10 回溯修正: §3/§5 去重边界 ───────────────────────────────────


def test_blocked_section_keeps_reasons_beyond_section3_cap():
    """R5-10 修正: §3(可执行动作)只显示 no_action_reasons[:3],§5(禁止与
    延后)的 already_shown 只应跳过这前 3 条——第 4+ 条必须保留在 §5,
    否则冲突理由 >3 条时用户完全看不到(修正前 already_shown 跳过全部)。

    回归锁: 若有人把 already_shown 改回 set(no_action_reasons)(跳过
    全部),第 4 条会从报告消失,此测试失败。
    """
    artifact = _artifact()
    card = artifact["portfolio_decision"]["user_view"]["instruction_card"]
    reasons = [
        "沪深300ETF（510300）：方向冲突，需人工确认",
        "科创50ETF（588000）：方向冲突，需人工确认",
        "半导体ETF（512480）：方向冲突，需人工确认",
        "消费ETF（159928）：方向冲突，需人工确认",  # 第 4 条,§3 不显示
    ]
    card["no_action_reasons"] = reasons
    brief = artifact["portfolio_decision"]["user_view"]["assistant_brief"]
    brief["why"] = reasons
    brief["do_not_do"] = reasons

    payload = build_push_payload(artifact, now="2026-07-17T15:27:00+08:00")
    text = render_push_payload(payload)
    blocked = text[text.index("**禁止与延后**"):text.index("**组合与检查点**")]
    # §3 只显示前 3 条(不重复断言,但第 4 条必须在 §5 出现)
    assert "消费ETF（159928）：方向冲突，需人工确认" in blocked
    # 前 3 条已被 §3 展示,§5 不应重复(去重生效)
    assert "沪深300ETF（510300）：方向冲突，需人工确认" not in blocked
    assert validate_payload_text(payload, text) == []
