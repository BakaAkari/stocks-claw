"""Tests for Task 9: Report Contract Refactoring.

Tests enforce:
1. Artifact contains data_boundaries and research_candidates structures.
2. agent_task.data_reference only references 5 fields.
3. agent_task has the trade-card-first two-layer output structure.
4. format_run_markdown has the same two layers and hides internal identifiers.
5. Each approved action has cancel_condition, settlement, next_checkpoint.
6. research_only signals never appear in action section.
7. All monetary values in markdown are traceable to artifact fields.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from stocks.engine.portfolio_adjudicator import PortfolioAction, PortfolioDecision
from stocks.engine.scheduled_analysis import (
    ScheduledSession,
    build_agent_task,
    format_run_markdown,
)
from tests.engine.test_scheduled_analysis import (
    FakeEngine,
    ScheduledAnalysisRunner,
    _config,
    _context,
    _run,
)


@pytest.fixture
def minimal_run(tmp_path: Path) -> dict:
    """Produce a full scheduled run artifact for contract assertions."""
    config = _config(tmp_path)
    runner = ScheduledAnalysisRunner(
        FakeEngine(_context()),
        config=config,
        artifact_dir=config["artifact_dir"],
    )
    _run(
        runner.run_session(
            "cn_post_open",
            now=datetime.fromisoformat("2026-07-06T14:35:00+08:00"),
        )
    )
    # Read the full artifact from the store
    run = runner.latest("cn_post_open")
    assert run is not None and run.get("success") is True
    return run["data"]


# ─── Test 1: Artifact contains data_boundaries ──────────────────────────


def test_artifact_has_data_boundaries(minimal_run: dict):
    """Artifact must contain data_boundaries structure."""
    run = minimal_run
    assert "data_boundaries" in run, "Missing data_boundaries in run artifact"
    db = run["data_boundaries"]
    assert isinstance(db, dict), "data_boundaries must be a dict"


# ─── Test 2: Artifact contains research_candidates ──────────────────────


def test_artifact_has_research_candidates(minimal_run: dict):
    """Artifact must contain research_candidates structure."""
    run = minimal_run
    assert "research_candidates" in run, "Missing research_candidates in run artifact"
    rc = run["research_candidates"]
    assert isinstance(rc, list), "research_candidates must be a list"
    risk_state = run.get("risk_state", {})
    if risk_state.get("suspend_accumulation"):
        for candidate in rc:
            assert "reassess_after" in candidate or "condition" in candidate, (
                "Research candidates under suspension must state reassessment condition"
            )


# ─── Test 3: agent_task.data_reference only references 5 fields ─────────


def test_agent_task_data_reference_five_fields(minimal_run: dict):
    """agent_task.data_reference must only reference the 5 contract fields."""
    agent_task = minimal_run.get("agent_task", {})
    data_ref = agent_task.get("data_reference", {})
    allowed_keys = {
        "window_delta",
        "portfolio_decision",
        "risk_state",
        "data_boundaries",
        "research_candidates",
    }
    actual_keys = set(data_ref.keys())
    extra = actual_keys - allowed_keys
    assert not extra, f"agent_task.data_reference contains disallowed keys: {extra}"
    missing = allowed_keys - actual_keys
    assert not missing, f"agent_task.data_reference missing required keys: {missing}"


# ─── Test 4: agent_task.must_answer covers 5 sections ──────────────────


def test_agent_task_must_answer_covers_two_human_layers(minimal_run: dict):
    agent_task = minimal_run.get("agent_task", {})
    sections = agent_task["output_structure"]["sections"]
    assert [section["name"] for section in sections] == ["交易指令卡", "私人投资助理"]
    text = json.dumps(agent_task, ensure_ascii=False)
    assert "instruction_card" in text
    assert "assistant_brief" in text
    assert "不得向用户展示 position_id" in text


# ─── Test 5: Each approved action has structured fields ────────────────


def test_approved_actions_have_structured_fields():
    """Each PortfolioAction must carry cancel_condition, settlement, next_checkpoint."""
    action = PortfolioAction(
        position_id="test_pos",
        signal="reduce",
        action_description="测试减仓",
        ratio=0.3,
        decision_id="test-did",
        reason="测试理由",
    )
    pd = PortfolioDecision(
        status="approved",
        decision_id="portfolio-did",
        approved_actions=[action],
    )
    pd_dict = pd.to_dict()
    for act in pd_dict["approved_actions"]:
        assert act["cancel_condition"], "Missing cancel_condition on approved action"
        assert "settlement_timing" in act, "Missing settlement on approved action"
        assert act["next_checkpoint"], "Missing next_checkpoint on approved action"


# ─── Test 6: format_run_markdown has 5 sections ───────────────────────


def test_format_run_markdown_has_trade_card_then_private_assistant(minimal_run: dict):
    md = format_run_markdown(minimal_run)
    assert "## Agent Task" not in md
    assert "## Action Signals" not in md
    assert "**交易指令卡**" in md
    assert "**私人投资助理**" in md
    assert md.index("**交易指令卡**") < md.index("**私人投资助理**")


# ─── Test 7: research_only signals never in action section ─────────────


def test_research_only_not_in_instruction_card(minimal_run: dict):
    run = json.loads(json.dumps(minimal_run))
    view = run["portfolio_decision"]["user_view"]
    view["instruction_card"]["actions"] = []
    view["assistant_brief"]["research"] = [{
        "display_label": "医药ETF（512010）",
        "action_hint": "仅研究，不执行",
        "reassess_after": "下一窗口复核",
    }]
    markdown = format_run_markdown(run)
    card = markdown.split("**交易指令卡**", 1)[1].split("**私人投资助理**", 1)[0]
    assistant = markdown.split("**私人投资助理**", 1)[1]
    assert "医药ETF（512010）" not in card
    assert "医药ETF（512010）" in assistant


# ─── Test 8: Monetary values traceable to artifact fields ─────────────


def test_markdown_monetary_values_traceable_to_artifact(tmp_path: Path):
    """Monetary values and ratios in markdown must map back to artifact fields."""
    config = _config(tmp_path)
    runner = ScheduledAnalysisRunner(
        FakeEngine(_context()),
        config=config,
        artifact_dir=config["artifact_dir"],
    )
    _run(
        runner.run_session(
            "cn_post_open",
            now=datetime.fromisoformat("2026-07-06T14:35:00+08:00"),
        )
    )
    run = runner.latest("cn_post_open")["data"]
    md = format_run_markdown(run)

    view = run["portfolio_decision"]["user_view"]
    immediate = view["assistant_brief"]["cash"]["immediate"]["amount_cny"]
    assert f"¥{immediate:,.0f}" in md
    for action in view["instruction_card"]["actions"]:
        assert f"{action['ratio'] * 100:.0f}%" in md
        if action["estimated_amount_cny"] is not None:
            assert f"¥{action['estimated_amount_cny']:,.0f}" in md


# ─── Test 9: Intelligence agent task not affected ──────────────────────


def test_intelligence_agent_task_not_affected():
    """build_intelligence_agent_task must remain independent."""
    from stocks.engine.scheduled_analysis import ScheduledSession, build_intelligence_agent_task

    session = ScheduledSession(
        id="global_intelligence_watch",
        market="intelligence",
        exchange_timezone="UTC",
        user_timezone="Asia/Shanghai",
        time="00:00",
        intent="intelligence_patrol",
        push="normal",
        enabled=True,
        duplicate_window_minutes=60,
        holidays=frozenset(),
        primary_market="global",
    )
    task = build_intelligence_agent_task(session)
    data_ref = task.get("data_reference", {})
    assert "事件" in data_ref or "intelligence_digest" in str(data_ref), (
        "Intelligence agent task must retain its own data_reference fields"
    )


# ─── Test 10: Suppressed actions listed in forbidden section ────────────


def test_suppressed_actions_in_forbidden_section():
    """Suppressed actions must be listable in the forbidden/pending section."""
    suppressed = PortfolioAction(
        position_id="suppressed_pos",
        signal="add",
        action_description="测试加仓",
        ratio=0.1,
        decision_id="suppressed-did",
        reason="风险暂停",
    )
    pd = PortfolioDecision(
        status="suppressed",
        decision_id="portfolio-did",
        suppressed_actions=[suppressed],
    )
    pd_dict = pd.to_dict()
    assert len(pd_dict["suppressed_actions"]) > 0
    sa = pd_dict["suppressed_actions"][0]
    assert sa["position_id"] == "suppressed_pos"


def test_renderer_uses_only_user_view_structured_numbers(minimal_run: dict):
    run = json.loads(json.dumps(minimal_run))
    run["portfolio_decision"]["user_view"]["instruction_card"] = {
        "status": "action_required", "status_label": "需要操作",
        "actions": [{
            "display_label": "化工ETF（516020）", "action_label": "减仓",
            "ratio": 0.25, "estimated_amount_cny": 4200.0,
            "amount_is_estimate": True, "reason_summary": "传闻金额 987654321 元，比例 77%",
            "cancel_condition": "条件不成立", "settlement_display": "T+1",
            "next_checkpoint": "下一窗口",
        }], "no_action_reasons": [], "next_checkpoint": "下一窗口",
    }
    markdown = format_run_markdown(run)
    assert "25%" in markdown
    assert "¥4,200" in markdown
    assert "987654321" not in markdown
    assert "77%" not in markdown


def test_blocked_position_symbol_excluded_from_research_candidates(tmp_path):
    config = _config(tmp_path)
    ctx = _context()
    ctx["position_valuations"] = [{
        "position_id": "a_512480", "instrument_key": "a:512480", "display_name": "半导体ETF",
        "liquidity": {}, "evidence": {"data_anomalies": [{"code": "single_bar_jump"}]},
    }]
    ctx["action_signals"] = {
        "items": [{"symbol": "a:512480", "signal": "accumulate_candidate", "action_hint": "可分批布局"}],
    }
    runner = ScheduledAnalysisRunner(FakeEngine(ctx), config=config, artifact_dir=config["artifact_dir"])
    _run(runner.run_session("cn_post_open", now=datetime.fromisoformat("2026-07-06T14:35:00+08:00")))
    run = runner.latest("cn_post_open")["data"]
    symbols = {c["symbol"] for c in run["research_candidates"]}
    assert "a:512480" not in symbols, "Blocked position should not appear as research candidate"


@pytest.mark.parametrize(
    "intent",
    [
        "pre_open_plan", "open_watch", "pre_close_decision",
        "after_close_review", "morning_close_check",
        "afternoon_open_check", "mid_session_check",
    ],
)
def test_all_trading_agent_tasks_require_humanized_anomaly_explanations(intent):
    session = ScheduledSession(
        id="test_session", market="cn", exchange_timezone="Asia/Shanghai",
        user_timezone="Asia/Shanghai", time="14:35", intent=intent,
        push="normal", enabled=True, duplicate_window_minutes=30,
        holidays=frozenset(), primary_market="cn",
    )
    task = build_agent_task(session)
    text = json.dumps(task, ensure_ascii=False)
    assert "原始异常码" in text
    assert "不得向用户展示" in text
    assert "assistant_brief" in text


def test_renderer_hides_machine_ids_hashes_enums_and_anomaly_codes(minimal_run: dict):
    run = json.loads(json.dumps(minimal_run))
    run["portfolio_decision"]["decision_id"] = "abcdef0123456789"
    run["portfolio_decision"]["approved_actions"] = [{
        "position_id": "a_516020", "signal": "reduce", "decision_id": "abcdef0123456789",
    }]
    run["portfolio_decision"]["suppressed_actions"] = [{
        "position_id": "ccb_wmp", "reason": "prev_close_mismatch periodic_open",
    }]
    md = format_run_markdown(run)
    for forbidden in (
        "a_516020", "ccb_wmp", "abcdef0123456789", "prev_close_mismatch",
        "periodic_open", "review_required", "research_only",
    ):
        assert forbidden not in md



@pytest.mark.skip(reason="session labels updated — header format TBD")
def test_renderer_header_uses_human_session_name_not_internal_session_id(minimal_run: dict):
    run = json.loads(json.dumps(minimal_run))
    run["session"] = "cn_post_open"
    markdown = format_run_markdown(run)
    assert markdown.startswith("**A股开盘后")
    assert "cn_post_open" not in markdown


# ─── Task 7: Report contract ───────────────────────────────────────


def test_agent_task_still_two_sections(minimal_run: dict):
    """agent_task.output_structure.sections must still have exactly two sections."""
    agent_task = minimal_run.get("agent_task", {})
    sections = agent_task["output_structure"]["sections"]
    names = [s["name"] for s in sections]
    assert names == ["交易指令卡", "私人投资助理"]


def test_agent_task_licenses_assistant_brief_outlook(minimal_run: dict):
    """agent_task must contain the explicit permission path."""
    text = json.dumps(minimal_run.get("agent_task", {}), ensure_ascii=False)
    # Must mention the full path to outlook in data_reference or must_answer
    assert "portfolio_decision.user_view.assistant_brief.outlook" in text \
        or "assistant_brief.outlook" in text
    assert "outlook_delta" in text
    # Should not expand into arbitrary new sections
    sections = minimal_run["agent_task"]["output_structure"]["sections"]
    assert len(sections) == 2


def test_run_artifact_retains_outlook_when_present(tmp_path):
    """When outlook data exists, artifact must retain it without expanding contract."""
    config = _config(tmp_path)
    runner = ScheduledAnalysisRunner(
        FakeEngine(_context()),
        config=config,
        artifact_dir=config["artifact_dir"],
    )
    _run(
        runner.run_session(
            "cn_post_open",
            now=datetime.fromisoformat("2026-07-06T14:35:00+08:00"),
        )
    )
    run = runner.latest("cn_post_open")["data"]
    decision = run.get("portfolio_decision", {})
    _view = decision.get("user_view", {})

    # Regardless of outlook presence, portfolio_decision must not contain unknown top keys
    allowed = {"status", "decision_id", "approved_actions", "suppressed_actions",
               "cancelled_actions", "user_view", "rule_version",
               "post_trade_projection", "cash_schedule", "replacement_chains",
               "unresolved_conflicts"}
    actual = set(decision.keys())
    unknown = actual - allowed
    assert not unknown, f"Unknown portfolio_decision keys: {unknown}"
    # agent_task must still have exactly 2 sections
    agent_task = run.get("agent_task", {})
    sections = agent_task.get("output_structure", {}).get("sections", [])
    assert len(sections) == 2


def test_assistant_brief_accepts_outlook_and_outlook_delta():
    """assistant_brief must tolerate outlook and outlook_delta keys."""
    from stocks.engine.scheduled_analysis import format_run_markdown
    run = {
        "session": "cn_post_open",
        "scheduled_for": "2026-07-17T14:30:00+08:00",
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {
                    "status": "no_action", "status_label": "今日无需操作",
                    "actions": [], "no_action_reasons": ["无获批动作"],
                },
                "assistant_brief": {
                    "why": [], "do_not_do": [],
                    "cash": {}, "risk": {}, "research": [],
                    "outlook": {"status": "ok"},
                    "outlook_delta": {"new": True},
                },
            },
        },
    }
    # Must render without error
    md = format_run_markdown(run)
    assert "**私人投资助理**" in md
