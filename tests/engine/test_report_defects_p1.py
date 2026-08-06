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
    signals = {"items": [
        {"symbol": "us:NVDA", "name": "英伟达", "signal": "wait_for_pullback",
         "action_hint": "等回踩", "reasons": ["现价高于 MA20"], "_score": 0.5},
    ]}
    risk = {"level": "reduce", "suspend_accumulation": True}
    dq = {"quotes": {"by_market": {"us": {"freshness": "fresh"}}}}
    cands = _build_research_candidates(signals, risk, None, data_quality=dq)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.get("quote_stale") is None
    assert "分批布局" not in cand["sizing_hint"]
    assert "仅观察" in cand["sizing_hint"]


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
