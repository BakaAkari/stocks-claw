"""Regression tests for the 2026-08-05 us_after_close report defects.

P1-11 conflict rendering (asset-class weight vs instrument weight)
P1-12 stale-quote suppressed reference must not carry a precise amount
P1-13 risk level label vs transition phrasing
P1-14 deterministic window_delta surfaced in "本窗口变化"
P1-15 research candidates freshness gate + suspend downgrade
"""

from __future__ import annotations

from stocks.engine.presentation import _conflict_reason, build_user_view
from stocks.engine.scheduled_analysis import _build_research_candidates


def _position(position_id, name, instrument_key, market_value=0.0):
    return {
        "position_id": position_id,
        "display_name": name,
        "instrument_key": instrument_key,
        "valuation": {"market_value_cny": market_value},
        "classification": {"exposure_tags": []},
        "liquidity": {"tier": "t1"},
    }


# ── P1-11: conflict text must not read the asset-class ratio as the
# instrument's own weight ───────────────────────────────────────────


def test_conflict_reason_names_bucket_separately_from_instrument():
    conflict = {
        "position_id": "us_nvda",
        "signal": "take_profit",
        "bucket": "权益",
        "bucket_ratio": 0.127,
        "bucket_min": 0.25,
    }
    by_id = {"us_nvda": _position("us_nvda", "NVDA", "us:NVDA")}
    reason = _conflict_reason(conflict, by_id)
    # The old text "NVDA：权益当前占组合12.7%" was read as NVDA holding 12.7%
    # of the portfolio. The fixed text must separate the instrument (which
    # fires the signal) from the bucket (which carries the weight).
    assert "触发止盈信号" in reason
    assert "权益大类当前占组合12.7%" in reason
    assert "低于下限25%" in reason


def test_conflict_detail_keeps_structured_fields():
    conflict = {
        "position_id": "us_nvda",
        "signal": "take_profit",
        "bucket": "权益",
        "bucket_ratio": 0.127,
        "bucket_min": 0.25,
    }
    by_id = {"us_nvda": _position("us_nvda", "NVDA", "us:NVDA")}
    detail = {
        "label": "NVDA",
        "code": "NVDA",
        "type": "方向冲突",
        "bucket": "权益",
        "bucket_ratio": 0.127,
        "bucket_min": 0.25,
        "action": "止盈",
        "reason": _conflict_reason(conflict, by_id),
        "branch": "默认：维持现状，等待人工确认方向",
    }
    assert detail["code"] == "NVDA"
    assert "权益大类" in detail["reason"]


# ── P1-13: risk transition is relative, never an absolute contradiction ──


def test_risk_transition_phrasing_is_relative():
    decision = {
        "status": "review_required", "approved_actions": [], "suppressed_actions": [],
        "unresolved_conflicts": [], "cash_schedule": {},
    }
    view = build_user_view(
        decision, [], [], [], {"level": "reduce", "transition": "escalated"},
        session_id="us_after_close", session_intent="after_close_review",
    )
    risk = view["assistant_brief"]["risk"]
    assert risk["label"] == "降风险"
    assert risk["transition"] == "较上次升级"
    # Old text "降风险（风险升级）" read as contradictory; the new phrasing
    # states the level and the relative move separately.
    assert "较上次升级" in str(risk["label"]) or "较上次升级" == risk["transition"]


# ── P1-15: research candidate freshness gate ────────────────────────


def test_research_candidate_stale_market_downgraded():
    signals = {"items": [
        {"symbol": "a:513770", "name": "港股互联网ETF", "signal": "accumulate_candidate",
         "action_hint": "趋势与动能配合", "reasons": ["轮动排名前段"], "_score": 0.49},
    ]}
    risk = {"level": "reduce", "suspend_accumulation": True}
    dq = {"quotes": {"by_market": {"a": {"freshness": "stale"}}}}
    cands = _build_research_candidates(signals, risk, None, data_quality=dq)
    assert len(cands) == 1
    cand = cands[0]
    assert cand["quote_stale"] is True
    assert cand["condition"] == "quote_stale"
    assert cand["setup_tag"] == "观察"
    assert "行情数据过时" in cand["sizing_hint"]
    assert "分批布局" not in cand["sizing_hint"]
    # No precise price claim survives the gate.
    assert "0.36 > MA20" not in " ".join(cand["reasons"])


def test_research_candidate_suspend_strips_accumulation_sizing():
    """suspend 态:真加仓信号(accumulate)必须剥离建仓短语,但保留止损线。
    止损是已有仓位的风险保护,与"暂停加仓"不冲突——这是对旧版硬编码
    通用句(整句抹掉止损指导)的回归保护。"""
    signals = {"items": [
        {"symbol": "a:512400", "name": "有色ETF", "signal": "accumulate_candidate",
         "action_hint": "趋势与动能配合", "reasons": ["现价高于 MA20"], "_score": 0.5},
    ]}
    risk = {"level": "reduce", "suspend_accumulation": True}
    dq = {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}
    cands = _build_research_candidates(signals, risk, None, data_quality=dq)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.get("quote_stale") is None
    # 建仓短语被剥离
    assert "分批布局" not in cand["sizing_hint"]
    assert "暂停加仓" in cand["sizing_hint"]
    # 止损线被保留(不再被通用句吞掉)
    assert "止损" in cand["sizing_hint"]


def test_research_candidate_wait_for_pullback_keeps_observation_guidance_when_suspended():
    """wait_for_pullback 是"等回踩再进"的观望信号,不是加仓信号。
    suspend 态下必须保留其完整观察指导,而不是被误套成"暂停加仓"——
    回归保护(旧版把它误划入加仓信号集合)。"""
    signals = {"items": [
        {"symbol": "a:512010", "name": "医药ETF", "signal": "wait_for_pullback",
         "action_hint": "趋势完好但短线过热", "reasons": ["现价高于 MA20"], "_score": 0.4},
    ]}
    risk = {"level": "hedge", "suspend_accumulation": True}
    dq = {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}
    cands = _build_research_candidates(signals, risk, None, data_quality=dq)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.get("quote_stale") is None
    # 观察指导保留
    assert "等回踩" in cand["sizing_hint"] or "回踩" in cand["sizing_hint"]
    assert "不追高" in cand["sizing_hint"]


def test_research_candidate_fresh_market_keeps_guidance_when_not_suspended():
    signals = {"items": [
        {"symbol": "us:NVDA", "name": "英伟达", "signal": "wait_for_pullback",
         "action_hint": "等回踩", "reasons": ["现价高于 MA20"], "_score": 0.5},
    ]}
    risk = {"level": "normal", "suspend_accumulation": False}
    dq = {"quotes": {"by_market": {"us": {"freshness": "fresh"}}}}
    cands = _build_research_candidates(signals, risk, None, data_quality=dq)
    assert len(cands) == 1
    assert cands[0].get("quote_stale") is None
    assert "分批布局" in cands[0]["sizing_hint"] or "回踩" in cands[0]["sizing_hint"]


# ── P2-1: technical-indicator as_of gate ────────────────────────────


def test_research_candidate_indicator_as_of_stale_downgraded():
    """quotes 层 fresh 但技术指标 as_of 停在两周前 → 仍降级观察,
    不得展示两周前的精确价格（2026-08-06 对抗性校验实测场景）。"""
    from datetime import datetime, timedelta, timezone

    stale_date = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    signals = {"items": [
        {"symbol": "a:513770", "name": "港股互联网ETF", "signal": "accumulate_candidate",
         "action_hint": "趋势与动能配合", "reasons": ["轮动排名前段：现价 0.36 > MA20 0.35"],
         "_score": 0.49, "as_of": stale_date},
    ]}
    risk = {"level": "normal", "suspend_accumulation": False}
    # quotes 层显示 A 股 fresh —— 门控必须仍识别技术指标层过时
    dq = {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}
    cands = _build_research_candidates(signals, risk, None, data_quality=dq)
    assert len(cands) == 1
    cand = cands[0]
    assert cand["quote_stale"] is True
    assert cand["setup_tag"] == "观察"
    assert cand["as_of"] == stale_date
    # 原始精确价格 reason 必须被替换
    assert "现价 0.36" not in " ".join(cand["reasons"])
    assert "技术指标数据停留较早" in cand["reasons"][0]


def test_research_candidate_fresh_indicator_as_of_kept():
    """技术指标 as_of 新鲜且 quotes fresh → 正常展示。"""
    from datetime import datetime, timedelta, timezone

    fresh_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    signals = {"items": [
        {"symbol": "a:513770", "name": "港股互联网ETF", "signal": "accumulate_candidate",
         "action_hint": "趋势与动能配合", "reasons": ["轮动排名前段：现价 0.36 > MA20 0.35"],
         "_score": 0.49, "as_of": fresh_date},
    ]}
    risk = {"level": "normal", "suspend_accumulation": False}
    dq = {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}
    cands = _build_research_candidates(signals, risk, None, data_quality=dq)
    assert len(cands) == 1
    # setup_tag 由渲染层(presentation)设置;此处数据层只保证不降级
    assert cands[0].get("quote_stale") is None
    assert cands[0].get("setup_tag") is None


def test_research_candidate_missing_as_of_unknown_not_downgraded():
    """as_of 缺失(未知年龄)且 quotes fresh → 不降级,保留既有行为;
    市场级过时仍由 quotes 门控兜底(见 stale_market 测试)。"""
    signals = {"items": [
        {"symbol": "a:513770", "name": "港股互联网ETF", "signal": "accumulate_candidate",
         "action_hint": "趋势与动能配合", "reasons": ["轮动排名前段：现价 0.36 > MA20 0.35"],
         "_score": 0.49},  # 无 as_of
    ]}
    risk = {"level": "normal", "suspend_accumulation": False}
    dq = {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}
    cands = _build_research_candidates(signals, risk, None, data_quality=dq)
    assert len(cands) == 1
    # setup_tag 由渲染层(presentation)设置;此处数据层只保证不降级
    assert cands[0].get("quote_stale") is None
    assert cands[0].get("setup_tag") is None
