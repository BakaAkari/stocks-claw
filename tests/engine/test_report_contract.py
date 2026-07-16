"""Tests for Task 9: Report Contract Refactoring.

Tests enforce:
1. Artifact contains data_boundaries and research_candidates structures.
2. agent_task.data_reference only references 5 fields.
3. agent_task has 5 fixed output sections.
4. format_run_markdown has 5 sections, no raw agent_task dump.
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
            "cn_pre_close",
            now=datetime.fromisoformat("2026-07-06T14:35:00+08:00"),
        )
    )
    # Read the full artifact from the store
    run = runner.latest("cn_pre_close")
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


def test_agent_task_must_answer_covers_five_sections(minimal_run: dict):
    """agent_task.must_answer must cover the 5 contract sections."""
    agent_task = minimal_run.get("agent_task", {})
    must_answer = agent_task.get("must_answer", [])
    all_text = " ".join(must_answer).lower()

    section_keywords = {
        "变化摘要": ["变化", "delta", "window"],
        "今日动作": ["动作", "action", "approved"],
        "禁止待确认": ["禁止", "suppressed", "pending", "confirm"],
        "资金到账与边界": ["资金", "cash", "到账", "边界", "risk"],
        "研究候选": ["研究", "research", "candidate"],
    }

    for section, keywords in section_keywords.items():
        found = any(kw.lower() in all_text for kw in keywords)
        assert found, (
            f"must_answer missing coverage for section '{section}' (expected keywords: {keywords})"
        )


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


def test_format_run_markdown_has_five_sections(minimal_run: dict):
    """format_run_markdown output must have 5 distinct sections."""
    run = minimal_run
    md = format_run_markdown(run)
    # Must not contain raw agent_task must_answer lines
    assert "## Agent Task" not in md, "Markdown must not contain raw Agent Task section"
    assert "## Action Signals" not in md, "Markdown must not contain raw Action Signals section"

    sections = ["变化摘要", "今日动作", "禁止待确认", "资金到账与边界", "研究候选"]
    for section in sections:
        assert f"## {section}" in md, f"Markdown missing required section: {section}"


# ─── Test 7: research_only signals never in action section ─────────────


def test_research_only_not_in_action_section(minimal_run: dict):
    """research_only candidates render only after the research heading."""
    run = json.loads(json.dumps(minimal_run))
    run["portfolio_decision"]["approved_actions"] = []
    run["research_candidates"] = [
        {
            "symbol": "research:ONLY",
            "signal": "research_only",
            "action_hint": "仅研究，不执行",
        }
    ]
    markdown = format_run_markdown(run)
    action_section = markdown.split("## 今日动作", 1)[1].split("## 禁止待确认", 1)[0]
    research_section = markdown.split("## 研究候选", 1)[1]
    assert "research:ONLY" not in action_section
    assert "research:ONLY" in research_section


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
            "cn_pre_close",
            now=datetime.fromisoformat("2026-07-06T14:35:00+08:00"),
        )
    )
    run = runner.latest("cn_pre_close")["data"]
    md = format_run_markdown(run)

    cash_schedule = run.get("cash_schedule", {})
    if cash_schedule:
        immediate = cash_schedule.get("immediate_cash_cny", 0)
        if immediate > 0:
            assert str(int(immediate)) in md, f"Cash amount ¥{immediate} must appear in markdown"

    portfolio_decision = run.get("portfolio_decision", {})
    for action in portfolio_decision.get("approved_actions", []):
        ratio = action.get("ratio", 0)
        if ratio > 0:
            ratio_pct = f"{int(ratio * 100)}%"
            assert ratio_pct in md, (
                f"Ratio {ratio_pct} from approved action must appear in markdown"
            )


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


def test_renderer_does_not_extract_numbers_from_free_text(minimal_run: dict):
    run = json.loads(json.dumps(minimal_run))
    run["portfolio_decision"]["approved_actions"] = [
        {
            "position_id": "p1",
            "signal": "reduce",
            "ratio": 0.25,
            "action_description": "减仓",
            "reason": "传闻金额 987654321 元，比例 77%",
            "cancel_condition": "条件不成立",
            "settlement_timing": "T+1",
            "next_checkpoint": "下一窗口",
        }
    ]
    markdown = format_run_markdown(run)
    assert "25%" in markdown
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
    _run(runner.run_session("cn_pre_close", now=datetime.fromisoformat("2026-07-06T14:35:00+08:00")))
    run = runner.latest("cn_pre_close")["data"]
    symbols = {c["symbol"] for c in run["research_candidates"]}
    assert "a:512480" not in symbols, "Blocked position should not appear as research candidate"


def test_pre_close_agent_task_requires_numeric_anomaly_evidence():
    session = ScheduledSession(
        id="cn_pre_close", market="cn", exchange_timezone="Asia/Shanghai",
        user_timezone="Asia/Shanghai", time="14:35", intent="pre_close_decision",
        push="normal", enabled=True, duplicate_window_minutes=30,
        holidays=frozenset(), primary_market="cn",
    )
    task = build_agent_task(session)
    forbidden_instruction = next(x for x in task["must_answer"] if "禁止" in x)
    assert "异常数值" in forbidden_instruction
    assert "不得只写异常码" in forbidden_instruction


def test_renderer_uses_adjudicator_equity_ratio_without_overlapping_tag_double_count():
    run = {
        "session": "cn_pre_close", "market_date": "2026-07-15", "run_id": "r1",
        "status": "ok", "session_summary": {}, "window_delta": {},
        "portfolio_decision": {
            "approved_actions": [], "suppressed_actions": [],
            "unresolved_conflicts": [{
                "message": "权益占比 16.0% 低于下限 25%，但 p1 触发 reduce。",
                "bucket_ratio": 0.16, "bucket_value_cny": 160.0,
                "portfolio_value_cny": 1000.0,
            }],
            "cash_schedule": {},
        },
        "risk_state": {}, "data_boundaries": {},
        "context_digest": {"exposure_summary": {
            "total_value_cny": 1000.0,
            "exposures": {
                "a_share": {"value_cny": 100.0},
                "us_equity": {"value_cny": 60.0},
                "tech": {"value_cny": 60.0},
            },
        }},
        "position_reviews": [], "research_candidates": [],
    }
    md = format_run_markdown(run)
    assert "权益 bucket 汇总: 160 CNY" in md
    assert "占比: 16.0%" in md
    assert "220 CNY" not in md


def test_renderer_prints_generic_numeric_evidence_for_all_anomaly_codes():
    run = {
        "session": "cn_pre_close", "market_date": "2026-07-15", "run_id": "r1",
        "status": "ok", "session_summary": {}, "window_delta": {},
        "portfolio_decision": {
            "approved_actions": [],
            "suppressed_actions": [{"position_id": "p1", "reason": "数据异常阻断"}],
            "unresolved_conflicts": [], "cash_schedule": {},
        },
        "risk_state": {}, "data_boundaries": {}, "context_digest": {},
        "position_reviews": [{
            "position_id": "p1",
            "evidence": {"data_anomalies": [
                {"code": "prev_close_mismatch", "evidence": {
                    "quote_prev_close": 1.01, "history_prev_close": 1.50,
                    "difference_pct": 32.67,
                }},
                {"code": "source_regime_change", "evidence": {
                    "old_source": "eastmoney", "new_source": "tencent",
                    "price_gap_pct": 12.5,
                }},
            ]},
        }],
        "research_candidates": [],
    }
    md = format_run_markdown(run)
    assert "prev_close_mismatch" in md
    assert "quote_prev_close=1.01" in md
    assert "history_prev_close=1.5" in md
    assert "source_regime_change" in md
    assert "price_gap_pct=12.5" in md
