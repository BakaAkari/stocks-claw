"""Regression tests for the 2026-08-06 adversarial round 2-4 defects.

P2-3  currency_conversion failed 必须进入 data_notes(不能只存在 data_quality)
P3-4  估值 7-30 天的手工资产标记 mid-stale,data_notes 呈现时效
P4-1  macro fact 的 as_of 使用数据自身时间戳(不能伪造为当前时间)
P4-1b macro 节点 as_of 与 age_seconds 必须同源
P2-4  风险状态窗口级迁移(window_level_change)与观察级 transition 并存
"""

from __future__ import annotations

from stocks.engine.advisory_mainline import (
    _apply_freshness_downgrade,
    _downgrade_confidence,
)
from stocks.engine.context_builder import ContextBuilder
from stocks.engine.presentation import (
    _conflict_detail,
    _conflict_tilt,
    _data_notes,
    _tomorrow_plan,
    _window_level_change_text,
)
from stocks.engine.unified_snapshot import _build_macro_facts


def _macro_quality_dict(
    as_of: str = "2026-06-01T00:00:00+00:00",
    market_as_of: str = "2026-07-31T00:00:00+00:00",
) -> dict:
    """构造与生产 _macro_quality 输出同构的节点(age 与 as_of 已同源)。"""
    return {
        "status": "ok",
        "source": "composite",
        "as_of": as_of,
        "freshness": "old",
        "age_seconds": 5437152,
        "filled_fields": 9,
        "missing_fields": [],
        "sources": ["fred:CPIAUCSL"],
        "field_sources": {
            "vix": {"source": "fred:VIXCLS", "as_of": market_as_of},
            "official_stats.cpi_yoy": {"source": "fred:CPIAUCSL", "as_of": as_of},
        },
        "market": {"as_of": market_as_of, "freshness": "stale"},
        "official": {"as_of": as_of, "freshness": "old"},
    }


def _quality_with_completeness(issues: list[dict]) -> dict:
    q = _macro_quality_dict()
    q["asset_completeness"] = {
        "status": "degraded" if issues else "ok",
        "issue_count": len(issues),
        "issues": issues,
    }
    return q


# ── P2-3: currency_conversion failed 必须进入 data_notes ─────────────


def test_data_notes_surfaces_blocked_currency_issue():
    issues = [{
        "position_id": "us_hkd_cash",
        "severity": "blocked",
        "capability": "cny_valuation",
        "message": "IBKR HKD 现金 币种 HKD 暂不支持自动换算",
    }]
    quality = _quality_with_completeness(issues)
    notes = _data_notes({"data_quality": quality})
    assert any("HKD" in n and "不支持" in n for n in notes), notes


# ── P3-4: 7-30 天估值标记 mid-stale 并呈现 ──────────────────────────


def test_data_notes_surfaces_mid_stale_valuation():
    issues = [{
        "position_id": "alipay_gold",
        "severity": "degraded",
        "capability": "valuation_age",
        "message": "alipay_gold 估值为 6 天前（截止 2026-07-31），与当日行情混算，金额为近似值",
    }]
    quality = _quality_with_completeness(issues)
    notes = _data_notes({"data_quality": quality})
    assert any("估值为 6 天前" in n for n in notes), notes


def test_data_notes_ignores_unrelated_degraded_issues():
    # 与资金/估值无关的 degraded(如缺成本价)不应进入 data_notes 噪音。
    issues = [{
        "position_id": "us_nvda",
        "severity": "degraded",
        "capability": "pnl",
        "message": "NVDA 缺成本价，无法计算未实现盈亏",
    }]
    quality = _quality_with_completeness(issues)
    notes = _data_notes({"data_quality": quality})
    assert all("缺成本价" not in n for n in notes), notes


# ── P4-1b: macro as_of 与 age_seconds 同源 ───────────────────────────


def test_macro_quality_as_of_matches_age_source():
    # _freshness_from_datetime(as_of=6/1, generated_at=8/6) 应给出
    # age_seconds ≈ 66 天,与 as_of 一致,而非市场层(7/31)的 6 天。
    q = _macro_quality_dict(as_of="2026-06-01T00:00:00+00:00",
                            market_as_of="2026-07-31T00:00:00+00:00")
    # 直接验证 _freshness 语义:as_of 老则 age 大
    cb = ContextBuilder.__new__(ContextBuilder)
    parsed = cb._parse_iso_datetime(q["as_of"])
    fresh = cb._freshness_from_datetime(parsed, "2026-08-06T07:00:00+00:00")
    assert fresh["freshness"] == "old"
    assert fresh["age_seconds"] > 50 * 24 * 3600  # > 50 天


# ── P4-1: macro fact as_of 用数据自身时间戳 ──────────────────────────


def test_macro_facts_use_field_source_as_of_not_generated_at():
    class FakeContext:
        generated_at = "2026-08-06T07:00:00+00:00"
        macro_snapshot = {
            "vix": 16.5,
            "usd_cny": 7.25,
            "field_sources": {
                "vix": {"source": "fred:VIXCLS", "as_of": "2026-07-31"},
                "usd_cny": {"source": "yahoo_finance", "as_of": "2026-08-05"},
            },
        }

    facts = _build_macro_facts(FakeContext(), [])
    by_key = {f.fact_id: f for f in facts}
    vix = by_key.get("macro:vix")
    assert vix is not None
    # P4-1: vix 的 as_of 必须是数据自身时间戳(7/31),不是 generated_at(8/6)
    assert vix.as_of == "2026-07-31"
    usd = by_key.get("macro:usd_cny")
    assert usd.as_of == "2026-08-05"


# ── P2-4: 窗口级迁移文本 ─────────────────────────────────────────────


def test_window_level_change_text_renders_migration():
    wd = {
        "changes": [
            {"field": "risk_state.level", "old": "reduce", "new": "hedge"},
        ]
    }
    text = _window_level_change_text(wd)
    assert "降风险" in text and "对冲/高风险" in text


def test_window_level_change_text_empty_without_level_change():
    wd = {
        "changes": [
            {"field": "portfolio_decision.status", "old": "ok", "new": "review_required"},
        ]
    }
    assert _window_level_change_text(wd) == ""
    assert _window_level_change_text(None) == ""


# ── C1-WP1: 冲突确定性倾向 ──────────────────────────────────────────


def test_conflict_tilt_stop_loss_leans_action():
    tilt, reason = _conflict_tilt(
        {"position_id": "us_nvda", "signal": "stop_loss",
         "bucket_ratio": 0.127, "bucket_min": 0.25}, {}
    )
    assert tilt == "action"
    assert "硬止损" in reason


def test_conflict_tilt_reduce_underweight_leans_constraint():
    # 科创50 减仓 vs 权益低配 12.7% < 25%(8/6 实测场景)
    tilt, reason = _conflict_tilt(
        {"position_id": "a_588000", "signal": "reduce",
         "bucket_ratio": 0.127, "bucket_min": 0.25}, {}
    )
    assert tilt == "constraint"
    assert "低于下限" in reason


def test_conflict_tilt_manual_fallback():
    tilt, _ = _conflict_tilt(
        {"position_id": "a_512480", "signal": "wait",
         "bucket_ratio": 0.2, "bucket_min": 0.1}, {}
    )
    assert tilt == "manual"


def test_conflict_detail_exposes_tilt_fields():
    by_id = {
        "a_588000": {
            "position_id": "a_588000",
            "display_name": "科创50ETF",
            "instrument_key": "a:588000",
            "classification": {"exposure_tags": [], "product_type": "exchange_traded_fund"},
            "liquidity": {"tier": "t1"},
        }
    }
    detail = _conflict_detail(
        {"position_id": "a_588000", "signal": "reduce",
         "bucket": "权益", "bucket_ratio": 0.127, "bucket_min": 0.25}, by_id
    )
    assert detail["tilt"] == "constraint"
    assert "低于下限" in detail["tilt_reason"]


# ── C1-WP2: 研判置信度降级 ──────────────────────────────────────────


def _outlook_with_conf(conf: str) -> dict:
    return {
        "status": "ok",
        "near_term": {"horizon": "3-7天", "direction": "uncertain", "confidence": conf},
        "medium_term": {"horizon": "1-3个月", "direction": "mixed", "confidence": conf},
        "data_limitations": [],
    }


class _Ctx:
    def __init__(self, data_quality: dict):
        self.data_quality = data_quality


def _stale_dq() -> dict:
    return {
        "macro": {
            "official": {"freshness": "old", "as_of": "2026-06-01T00:00:00+00:00"},
        },
        "quotes": {"by_market": {"a": {"freshness": "fresh"}, "us": {"freshness": "old"}}},
    }


def test_downgrade_confidence_order():
    assert _downgrade_confidence("high") == "medium"
    assert _downgrade_confidence("medium") == "low"
    assert _downgrade_confidence("low") == "low"  # 不再降
    assert _downgrade_confidence("unknown") == "unknown"


def test_freshness_downgrade_reduces_confidence_and_appends_limitation():
    out = _outlook_with_conf("medium")
    res = _apply_freshness_downgrade(out, _Ctx(_stale_dq()))
    assert res["near_term"]["confidence"] == "low"
    assert res["medium_term"]["confidence"] == "low"
    assert any("降级" in note for note in res["data_limitations"]), res["data_limitations"]


def test_freshness_downgrade_keeps_fresh_data_unchanged():
    fresh_dq = {
        "macro": {"official": {"freshness": "fresh", "as_of": "2026-08-06"}},
        "quotes": {"by_market": {"a": {"freshness": "fresh"}, "us": {"freshness": "fresh"}}},
    }
    out = _outlook_with_conf("medium")
    res = _apply_freshness_downgrade(out, _Ctx(fresh_dq))
    assert res["near_term"]["confidence"] == "medium"
    assert res["data_limitations"] == []


def test_freshness_downgrade_does_not_touch_rationale():
    out = _outlook_with_conf("high")
    out["near_term"]["rationale"] = "美国 7 月非农及 CPI 数据即将公布"
    res = _apply_freshness_downgrade(out, _Ctx(_stale_dq()))
    assert res["near_term"]["confidence"] == "medium"
    # 诚实保留 LLM 原话,只调可信度
    assert res["near_term"]["rationale"] == "美国 7 月非农及 CPI 数据即将公布"


# ── C1-WP3: 明日计划 ────────────────────────────────────────────────


def test_tomorrow_plan_traces_approved_actions():
    plan = _tomorrow_plan(
        {"approved_actions": [{
            "position_id": "a_510300", "signal": "reduce",
            "action_description": "减仓 525 股", "ratio": 0.2,
        }]},
        [], {},
        data_notes=[], risk_state={}, structured_outlook=None,
    )
    assert plan[0]["source"] == "approved_action"
    assert plan[0]["priority"] == "high"
    assert "510300" in plan[0]["action"] or "减仓" in plan[0]["action"]


def test_tomorrow_plan_uses_conflict_tilt():
    plan = _tomorrow_plan(
        {}, [
            {"label": "科创50ETF", "code": "588000", "tilt": "constraint",
             "tilt_reason": "权益低配 12.7% 低于下限 25%"},
        ], {},
        data_notes=[], risk_state={}, structured_outlook=None,
    )
    assert any("维持现状" in p["action"] and p["source"] == "conflict_tilt" for p in plan)


def test_tomorrow_plan_emits_observation_when_empty():
    plan = _tomorrow_plan(
        {}, [], {}, data_notes=[], risk_state={}, structured_outlook=None,
    )
    assert plan == [{"action": "观察：明日无新增动作，维持当前仓位",
                     "position": "", "priority": "low", "source": "no_action"}]


def test_tomorrow_plan_includes_risk_and_low_confidence_notes():
    plan = _tomorrow_plan(
        {}, [], {},
        data_notes=["¥36,850 的卖出资金结算方式待确认"],
        risk_state={"level": "hedge", "transition": "unchanged"},
        structured_outlook={"near_term": {"confidence": "low"}},
    )
    sources = [p["source"] for p in plan]
    assert "data_note" in sources
    assert "risk_state" in sources
    assert "outlook_confidence" in sources
