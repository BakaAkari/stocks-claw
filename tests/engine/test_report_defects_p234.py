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


def _fresh_by_market() -> dict:
    return {"a": {"status": "ok", "freshness": "fresh"},
            "us": {"status": "ok", "freshness": "fresh"}}


def test_tomorrow_plan_traces_approved_actions():
    plan = _tomorrow_plan(
        {"approved_actions": [{
            "position_id": "a_510300", "signal": "reduce",
            "action_description": "减仓 525 股", "ratio": 0.2,
            "execution_status": "full", "final_ratio": 0.2,
            "executable_quantity": 525,
        }]},
        [], {
            "a_510300": {
                "position_id": "a_510300",
                "display_name": "沪深300ETF",
                "instrument_key": "a:510300",
                "classification": {"exposure_tags": ["沪深300"]},
            }
        },
        by_market=_fresh_by_market(),
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
        by_market=_fresh_by_market(),
        data_notes=[], risk_state={}, structured_outlook=None,
    )
    assert any("维持现状" in p["action"] and p["source"] == "conflict_tilt" for p in plan)


def test_tomorrow_plan_emits_observation_when_empty():
    plan = _tomorrow_plan(
        {}, [], {}, by_market=_fresh_by_market(),
        data_notes=[], risk_state={}, structured_outlook=None,
    )
    assert plan == [{"action": "观察：明日无新增动作，维持当前仓位",
                     "position": "", "priority": "low", "source": "no_action"}]


def test_tomorrow_plan_includes_risk_and_low_confidence_notes():
    plan = _tomorrow_plan(
        {}, [], {},
        by_market=_fresh_by_market(),
        data_notes=["¥36,850 的卖出资金结算方式待确认"],
        risk_state={"level": "hedge", "transition": "unchanged"},
        structured_outlook={"near_term": {"confidence": "low"}},
    )
    sources = [p["source"] for p in plan]
    assert "data_note" in sources
    assert "risk_state" in sources
    assert "outlook_confidence" in sources


def test_tomorrow_plan_gate_alignment_does_not_conflict_with_card():
    # P5-1: 行情过时导致 _is_executable=False 的动作,明日计划不得列为
    # high 执行(与指令卡"暂缓执行"矛盾)。必须降级为 medium 复核。
    stale_market = {"a": {"status": "ok", "freshness": "old"}}
    plan = _tomorrow_plan(
        {"approved_actions": [{
            "position_id": "cn_broker_512480", "signal": "stop_loss",
            "action_description": "止损清仓", "ratio": 1.0,
            "execution_status": "full", "final_ratio": 1.0,
            "executable_quantity": 5000,
        }]},
        [], {
            "cn_broker_512480": {
                "position_id": "cn_broker_512480",
                "display_name": "半导体ETF",
                "instrument_key": "a:512480",
                "classification": {"exposure_tags": ["半导体"]},
            }
        },
        by_market=stale_market,
        data_notes=[], risk_state={}, structured_outlook=None,
    )
    assert plan[0]["priority"] == "medium"
    assert plan[0]["source"] == "approved_action_review"
    assert "暂缓执行" in plan[0]["action"] or "数据恢复" in plan[0]["action"]


def test_tomorrow_plan_executable_stays_high_when_quotes_fresh():
    # P5-1: 行情新鲜时动作保持 high 执行
    plan = _tomorrow_plan(
        {"approved_actions": [{
            "position_id": "cn_broker_512480", "signal": "stop_loss",
            "action_description": "止损清仓", "ratio": 1.0,
            "execution_status": "full", "final_ratio": 1.0,
            "executable_quantity": 5000,
        }]},
        [], {
            "cn_broker_512480": {
                "position_id": "cn_broker_512480",
                "display_name": "半导体ETF",
                "instrument_key": "a:512480",
                "classification": {"exposure_tags": ["半导体"]},
            }
        },
        by_market=_fresh_by_market(),
        data_notes=[], risk_state={}, structured_outlook=None,
    )
    assert plan[0]["priority"] == "high"
    assert plan[0]["source"] == "approved_action"


# ── P5-3: risk reasons 英文枚举翻译 ─────────────────────────────────


def test_risk_reasons_translate_english_enums():
    from stocks.engine.presentation import _risk_reasons

    reasons = _risk_reasons({
        "level": "hedge",
        "triggers": [
            {"condition": "Critical cluster", "value": "1 critical"},
            {"condition": "Geopolitical crisis", "value": "geopolitics critical"},
            {"condition": "VIX > 35", "value": "VIX=36.2"},
        ],
    })
    assert any("关键集群事件" in r and "1 个" in r for r in reasons), reasons
    assert any("地缘政治危机" in r and "地缘局势关键" in r for r in reasons), reasons
    # VIX 数字保持原样
    assert any("VIX=36.2" in r for r in reasons), reasons
    # 无英文枚举残留
    for r in reasons:
        assert "Critical cluster" not in r
        assert "geopolitics critical" not in r


def test_risk_reasons_unknown_trigger_falls_back_raw():
    from stocks.engine.presentation import _risk_reasons

    reasons = _risk_reasons({
        "level": "reduce",
        "triggers": [{"condition": "Mystery condition", "value": "x"}],
    })
    # 未知枚举保留原文(不伪造翻译)
    assert any("Mystery condition" in r for r in reasons)


# ── P5-2: 估值过期聚合行 + P5-5: 候选名单 ──────────────────────────


def test_blocked_section_aggregates_valuation_stale_notes():
    from scripts.build_push_payload import _section_blocked_and_deferred

    assistant = {
        "data_notes": [
            "广发纳指100联接A 手工估值超过 30 天，精确调仓需先更新金额",
            "大成纳指100联接A 手工估值超过 30 天，精确调仓需先更新金额",
            "华安黄金ETF联接C 手工估值超过 30 天，精确调仓需先更新金额",
            "A股行情数据已过时（截止 2026-08-06 08:14 UTC）",
        ],
        "do_not_do": [],
        "why": [],
        "risk": {"suspend_accumulation": False},
    }
    lines = _section_blocked_and_deferred({"status": "no_action", "actions": [], "no_action_reasons": []}, assistant)
    joined = "\n".join(lines)
    assert "3 项持仓为手工估值" in joined, joined
    # 行情过时仍在
    assert "A股行情数据已过时" in joined


def test_setup_section_lists_overflow_candidate_names():
    from scripts.build_push_payload import _section_setup_candidates

    research = [
        {"display_label": f"候选{i}ETF", "score": 1.0 - i * 0.1,
         "setup_tag": "观察", "reasons": ["趋势"]}
        for i in range(5)
    ]
    lines = _section_setup_candidates({"research": research})
    joined = "\n".join(lines)
    # top3 显示 + 其余 2 个列名
    assert "候选0ETF" in joined
    assert "另有 2 个候选" in joined
    assert "候选3ETF" in joined
    assert "候选4ETF" in joined



def test_tomorrow_plan_uses_final_ratio_not_stale_action_description():
    """TASK-011(2026-08-15): 明日计划不得出现'减仓 30%(按 28% 比例)'自相矛盾。
    action_description 内嵌执行前旧百分比(30%), final_ratio=28%(adjusted_to_step),
    修复后明日计划百分比单一来源 = final_ratio。"""
    # action_description 带旧百分比 30%, final_ratio=0.28125 (adjusted_to_step)
    plan = _tomorrow_plan(
        {"approved_actions": [{
            "position_id": "us_xle", "signal": "take_profit",
            "action_description": "止盈触发 — 建议减仓 30%",
            "ratio": 0.3, "final_ratio": 0.28125, "original_ratio": 0.3,
            "execution_status": "adjusted_to_step", "executable_quantity": 9,
        }]},
        [], {
            "us_xle": {
                "position_id": "us_xle", "display_name": "XLE",
                "instrument_key": "us:XLE",
                "classification": {"exposure_tags": ["能源"]},
            }
        },
        by_market=_fresh_by_market(),
        data_notes=[], risk_state={}, structured_outlook=None,
    )
    action_text = plan[0]["action"]
    # 不再同时出现"减仓 30%"与"按 28% 比例"
    assert "减仓 30%" not in action_text, action_text
    assert "30%（按" not in action_text, action_text
    # 百分比单一来源 final_ratio 28%
    assert "减仓 28%" in action_text, action_text
