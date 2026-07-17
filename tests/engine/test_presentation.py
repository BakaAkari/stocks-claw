from stocks.engine.presentation import (
    anomaly_display,
    build_user_view,
    display_label,
    freshness_is_estimate,
    public_instrument_code,
    risk_label,
    signal_label,
    status_label,
    suppression_reason_display,
)


def test_public_instrument_codes_and_labels_hide_machine_ids():
    assert public_instrument_code("a:516020") == "516020"
    assert public_instrument_code("us:NVDA") == "NVDA"
    assert public_instrument_code("fund:012345", "qdii_fund") == "012345"
    assert public_instrument_code("", "cash") == ""
    assert display_label("化工ETF", "a:516020") == "化工ETF（516020）"
    assert display_label("英伟达", "us:NVDA") == "英伟达（NVDA）"
    assert display_label("广发纳指100联接A", "us:QQQ", "qdii_fund", public_code="270042") == "广发纳指100联接A（270042）"
    assert display_label("", "", fallback="a_516020") == "未命名持仓"


def test_user_facing_enum_labels_are_chinese_and_safe():
    assert signal_label("stop_loss") == "止损"
    assert signal_label("take_profit") == "止盈"
    assert signal_label("reduce") == "减仓"
    assert signal_label("add") == "加仓"
    assert status_label("review_required") == "等待人工确认"
    assert risk_label("hedge") == "防御状态"
    assert signal_label("totally_new_signal") == "待确认动作"


def test_anomaly_codes_have_deterministic_user_messages():
    item = anomaly_display({
        "code": "price_ma20_dislocation",
        "evidence": {"price": 0.82, "ma20": 0.91},
    })
    assert item == {
        "display_message": "价格与20日均线偏差异常，可能存在复权或数据源口径问题",
        "user_impact": "暂停依据该指标执行交易",
        "evidence_summary": "20日均线=0.91，价格=0.82",
    }
    unknown = anomaly_display({"code": "new_internal_code", "evidence": {}})
    assert "new_internal_code" not in str(unknown)
    assert unknown["display_message"] == "数据质量异常，需人工核对"


def test_estimate_flag_covers_non_current_valuation_sources():
    assert freshness_is_estimate({}, "manual_amount") is True
    assert freshness_is_estimate({}, "fund_nav") is True
    for value in ("previous_close", "stale", "old", "unknown", "missing"):
        assert freshness_is_estimate({"price_freshness": value}, "market_quote") is True
    assert freshness_is_estimate({"price_freshness": "fresh"}, "market_quote") is False



def _position(pid, name, key, value=10_000.0, freshness="fresh", method="market_quote", public_code=""):
    return {
        "position_id": pid,
        "display_name": name,
        "instrument_key": key,
        "public_code": public_code,
        "market_value_cny": value,
        "valuation_method": method,
        "classification": {"product_type": "exchange_traded_fund"},
        "evidence": {"price_freshness": freshness},
    }


def test_build_user_view_renders_approved_action_and_estimated_amount():
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.25,
            "reason": "趋势走弱", "action_description": "趋势走弱，减仓25%",
            "cancel_condition": "重新站回20日均线", "settlement_timing": "T+1",
            "next_checkpoint": "A股收盘前",
        }],
        "suppressed_actions": [], "unresolved_conflicts": [],
        "cash_schedule": {"immediate_cash_cny": 40_000, "settling_cash_cny": 2_500,
                          "strategic_exit_value_cny": 60_000, "locked_value_cny": 20_000},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020", 20_000, "previous_close")],
        [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    card = view["instruction_card"]
    assert card["status"] == "action_required"
    assert card["status_label"] == "需要操作"
    assert len(card["actions"]) == 1
    action = card["actions"][0]
    assert action["display_label"] == "化工ETF（516020）"
    assert action["action_label"] == "减仓"
    assert action["estimated_amount_cny"] == 5_000.0
    assert action["amount_is_estimate"] is True
    assert "a_516020" not in str(view)
    assert view["assistant_brief"]["cash"]["immediate"]["label"] == "现在能用"


def test_build_user_view_no_action_card_has_two_human_reasons():
    decision = {
        "status": "review_required", "approved_actions": [],
        "suppressed_actions": [
            {"position_id": "a_516020", "signal": "reduce", "reason": "数据异常阻断: prev_close_mismatch"},
            {"position_id": "ccb_wmp", "signal": "hold", "reason": "资产处于 periodic_open 流动性等级，不可交易"},
            {"position_id": "alipay_gf_nasdaq", "signal": "hold", "reason": "research_only：长期配置仓信号仅供研究，不可执行"},
        ],
        "unresolved_conflicts": [],
        "cash_schedule": {},
    }
    positions = [
        _position("a_516020", "化工ETF", "a:516020"),
        _position("ccb_wmp", "嘉鑫稳利固收30天", ""),
        _position("alipay_gf_nasdaq", "广发纳指100联接A", "fund:270042", method="fund_nav"),
    ]
    reviews = [{"position_id": "a_516020", "evidence": {"data_anomalies": [
        {"code": "prev_close_mismatch", "evidence": {"prev_close": 0.8, "source_prev_close": 0.9}}
    ]}}]
    view = build_user_view(
        decision, positions, reviews, [],
        {"level": "hedge", "transition": "escalated", "suspend_accumulation": True},
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    card = view["instruction_card"]
    assert card["status_label"] == "今日无需操作"
    assert len(card["no_action_reasons"]) == 2
    assert "化工ETF（516020）" in card["no_action_reasons"][0]
    assert "prev_close_mismatch" not in str(view)

    assert "periodic_open" not in str(view)
    assert "research_only" not in str(view)


def test_outlook_params_are_projected_into_assistant_brief():
    """build_user_view projects structured_outlook and outlook_delta to assistant_brief."""
    decision = {"status": "approved", "approved_actions": [], "suppressed_actions": [],
                "unresolved_conflicts": [], "cash_schedule": {}}
    outlook = {"status": "ok", "summary": "测试研判", "generated_at": "2026-07-17T08:00:00Z"}
    delta = {"summary": "方向变化", "from": "中性", "to": "偏有利"}
    view = build_user_view(
        decision, [], [], [], {},
        session_id="cn_after_close", session_intent="after_close_review",
        structured_outlook=outlook, outlook_delta=delta,
    )
    # Projection strips unknown keys; check content not identity
    assert view["assistant_brief"]["outlook"]["summary"] == "测试研判"
    assert view["assistant_brief"]["outlook"]["status"] == "ok"
    # delta without schema_version or changes gets projected to empty
    assert "outlook_delta" in view["assistant_brief"]


def test_outlook_absent_when_not_provided():
    """No outlook fields when neither param is passed."""
    view = build_user_view(
        {"status": "approved", "approved_actions": [], "suppressed_actions": [],
         "unresolved_conflicts": [], "cash_schedule": {}},
        [], [], [], {}, session_id="cn_after_close", session_intent="after_close_review",
    )
    assert "outlook" not in view["assistant_brief"]


def test_outlook_projection_strips_unknown_and_internal_keys():
    """build_user_view strips unknown/internal keys from outlook projection."""
    outlook = {
        "status": "ok",
        "summary": "测试研判",
        "confidence": "medium",
        "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "high", "position_id": "a_123"},
        "asset_views": [{"asset_class": "权益", "direction": "supportive", "rationale": "估值有支撑", "_internal_key": True}],
        "scenarios": {"base": {"label": "基准", "drivers": [], "portfolio_effect": "", "_secret": "leak"}},
        "source_refs": [{"source": "Reuters", "title": "T", "url": "https://x", "published_at": "2026-07-17T00:00:00Z", "position_id": "a_456"}],
        "unknown_extra": "should not appear",
    }
    decision = {"status": "approved", "approved_actions": [], "suppressed_actions": [],
                "unresolved_conflicts": [], "cash_schedule": {}}
    view = build_user_view(
        decision, [], [], [], {},
        session_id="cn_after_close", session_intent="after_close_review",
        structured_outlook=outlook,
    )
    brief = view["assistant_brief"]["outlook"]
    assert "unknown_extra" not in brief
    assert "position_id" not in str(brief.get("near_term", {}))
    assert "_internal_key" not in str(brief.get("asset_views", []))
    assert "_secret" not in str(brief.get("scenarios", {}).get("base", {}))
    assert "position_id" not in str(brief.get("source_refs", []))
    assert brief["summary"] == "测试研判"


def test_build_user_view_research_candidates_use_real_names_and_never_actions():
    decision = {"status": "suppressed", "approved_actions": [], "suppressed_actions": [],
                "unresolved_conflicts": [], "cash_schedule": {}}
    research = [{"symbol": "a:512010", "name": "医药ETF", "signal": "wait_for_pullback",
                 "action_hint": "等回踩确认再进，不追", "priority": "research_only"}]
    view = build_user_view(decision, [], [], research, {"level": "normal"},
                           session_id="cn_after_close", session_intent="after_close_review")
    assert view["instruction_card"]["actions"] == []
    assert view["assistant_brief"]["research"][0]["display_label"] == "医药ETF（512010）"
    assert "wait_for_pullback" not in str(view)
    assert "research_only" not in str(view)



def test_manual_review_prioritizes_humanized_portfolio_conflict_over_research_suppression():
    decision = {
        "status": "review_required", "approved_actions": [],
        "suppressed_actions": [{
            "position_id": "alipay_gf_nasdaq", "signal": "hold",
            "reason": "research_only：长期配置仓信号仅供研究，不可执行",
        }],
        "unresolved_conflicts": [{
            "position_id": "a_510300", "signal": "reduce", "bucket": "权益",
            "bucket_ratio": 0.156, "bucket_value_cny": 246605.21,
            "portfolio_value_cny": 1578889.3,
            "message": "权益占比 15.6% 低于下限，但 a_510300 触发 reduce",
        }],
        "cash_schedule": {},
    }
    positions = [
        _position("a_510300", "沪深300ETF", "a:510300"),
        _position("alipay_gf_nasdaq", "广发纳指100联接A", "us:QQQ", public_code="270042"),
    ]
    view = build_user_view(decision, positions, [], [], {"level": "watch"},
                           session_id="cn_pre_open", session_intent="pre_open_plan")
    card = view["instruction_card"]
    assert card["status_label"] == "等待人工确认"
    assert card["no_action_reasons"][0] == (
        "沪深300ETF（510300）：权益当前占组合15.6%，低于目标下限，但技术信号要求减仓；方向冲突，需人工确认"
    )
    assert "a_510300" not in str(view)
    assert "reduce" not in str(view)



def test_suppression_reason_translates_liquidity_and_manual_fallback_codes():
    assert suppression_reason_display("t2_plus") == "资金或份额需等待结算完成后才能操作"
    assert suppression_reason_display("manual_fallback") == "当前使用人工估值，需更新可靠行情后再决定"


def test_unknown_session_checkpoint_does_not_leak_english_intent():
    view = build_user_view(
        {"status": "approved", "approved_actions": [], "suppressed_actions": [], "cash_schedule": {}},
        [], [], [], {}, session_id="new_future_window", session_intent="future_trade_check",
    )
    checkpoint = view["instruction_card"]["next_checkpoint"]
    assert checkpoint == "下一交易窗口复核"
    assert "future_trade_check" not in checkpoint



def test_research_reassessment_hides_machine_risk_level():
    view = build_user_view(
        {"status": "approved", "approved_actions": [], "suppressed_actions": [], "cash_schedule": {}},
        [], [], [{
            "symbol": "a:512010", "name": "医药ETF", "action_hint": "仅供观察",
            "reassess_after": "风险解除后再评估（当前状态: hedge）",
        }], {"level": "hedge"}, session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    text = str(view["assistant_brief"]["research"])
    assert "风险解除后再评估" in text
    assert "hedge" not in text



def test_user_view_contains_deterministic_conflict_counts_risk_reasons_and_data_notes():
    decision = {
        "status": "review_required", "approved_actions": [], "suppressed_actions": [],
        "unresolved_conflicts": [
            {"position_id": "a_510300", "signal": "reduce", "bucket": "权益", "bucket_ratio": 0.156},
            {"position_id": "a_588000", "signal": "reduce", "bucket": "权益", "bucket_ratio": 0.156},
            {"position_id": "us_ita", "signal": "reduce", "bucket": "权益", "bucket_ratio": 0.156},
            {"position_id": "alipay_info", "signal": "take_profit", "bucket": "权益", "bucket_ratio": 0.156},
        ], "cash_schedule": {},
    }
    positions = [
        _position("a_510300", "沪深300ETF", "a:510300"),
        _position("a_588000", "科创50ETF", "a:588000"),
        _position("us_ita", "ITA", "us:ITA"),
        _position("alipay_info", "易方达信息产业混合C", "fund:001513"),
    ]
    data_boundaries = {"data_quality": {
        "quotes": {"by_market": {"us": {"freshness": "stale", "as_of": "2026-07-16T20:00:00+00:00"},
                                     "a": {"freshness": "fresh", "as_of": "2026-07-17T05:07:30+00:00"}}},
        "macro": {"freshness": "old", "as_of": "2026-06-01T00:00:00+00:00"},
    }}
    view = build_user_view(
        decision, positions, [], [],
        {"level": "hedge", "transition": "unchanged", "suspend_accumulation": True,
         "evidence_keys": ["cluster:geopolitics"]},
        data_boundaries=data_boundaries,
        session_id="cn_after_close", session_intent="after_close_review",
    )
    brief = view["assistant_brief"]
    assert brief["conflict_summary"] == [
        {"action_label": "减仓", "count": 3}, {"action_label": "止盈", "count": 1}
    ]
    assert brief["risk"]["reasons"] == ["地缘政治风险达到临界级别"]
    assert brief["data_notes"] == [
        "美股行情数据已过时（截止 2026-07-16 20:00 UTC）",
        "宏观数据较旧（截止 2026-06-01 00:00 UTC）",
    ]
    assert "reduce" not in str(view)
    assert "take_profit" not in str(view)
    assert "cluster:geopolitics" not in str(view)


def test_user_view_anomaly_explanation_includes_structured_evidence_without_code():
    decision = {
        "status": "suppressed", "approved_actions": [],
        "suppressed_actions": [{"position_id": "a_516020", "reason": "prev_close_mismatch"}],
        "unresolved_conflicts": [], "cash_schedule": {},
    }
    reviews = [{"position_id": "a_516020", "evidence": {"data_anomalies": [{
        "code": "prev_close_mismatch", "evidence": {
            "stated_prev_close": 0.828, "actual_prev_close": 0.922,
            "diff_pct": 10.2, "threshold_pct": 5.0,
        },
    }]}}]
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020")], reviews, [], {},
        data_boundaries={}, session_id="cn_after_close", session_intent="after_close_review",
    )
    text = view["assistant_brief"]["do_not_do"][0]
    assert text == (
        "化工ETF（516020）：前收盘价在不同数据源之间不一致；"
        "上一根实际收盘价=0.922，差异百分比=10.2，数据源前收盘价=0.828，告警阈值=5.0；"
        "暂停执行，等待核对正确收盘价"
    )
    assert "prev_close_mismatch" not in str(view)

def test_outlook_delta_projection_strips_previous_session_and_market():
    """_project_outlook_delta strips previous_session/current_session/market, keeps schema_version + changes."""
    from stocks.engine.presentation import _project_outlook_delta
    delta = {
        "schema_version": 1,
        "previous_session": "cn_pre_open",
        "current_session": "cn_open_watch",
        "previous_generated_at": "2026-07-17T08:00:00Z",
        "current_generated_at": "2026-07-17T09:00:00Z",
        "market": "cn",
        "changes": {"summary": {"from": "中性", "to": "偏有利"}},
    }
    projected = _project_outlook_delta(delta)
    assert projected.get("schema_version") == 1
    assert "changes" in projected
    assert "previous_session" not in projected
    assert "current_session" not in projected
    assert "market" not in projected
    assert "previous_generated_at" not in projected
    assert "current_generated_at" not in projected


def test_outlook_delta_projection_strips_unknown_nested_fields():
    """_project_outlook_delta strips unknown nested fields, position_id, and extra keys from changes."""
    from stocks.engine.presentation import _project_outlook_delta
    delta = {
        "schema_version": 1,
        "changes": {
            "summary": {"from": "中性", "to": "偏有利", "extra_field": "nope", "position_id": "a_123"},
            "scenarios": {
                "base": {
                    "label": {"from": "基准", "to": "温和"},
                    "validation": {"from": "PMI扩张", "to": "GDP增长"},
                    "invalidation": {"from": "通胀", "to": "通缩"},
                    "drivers": {"from": "A", "to": "B"},
                    "position_id": "a_456",
                },
                "bull": {
                    "label": {"from": "乐观", "to": "非常乐观"},
                    "extra": {"foo": "bar"},
                },
                "new_scenario": {
                    "label": {"from": "X", "to": "Y"},
                },
            },
            "sector_views": {
                "科技": {
                    "direction": {"from": "adverse", "to": "supportive"},
                    "position_id": "a_789",
                    "extra": "should not appear",
                },
            },
            "source_refs": {
                "added": ["src-1", "src-2"],
                "removed": ["src-3"],
                "modified": ["src-4"],
            },
            "near_term": {
                "direction": {"from": "adverse", "to": "supportive"},
                "confidence": {"from": "low", "to": "high"},
                "horizon": {"from": "1-2w", "to": "2-3w"},
                "position_id": "a_000",
            },
        },
    }
    projected = _project_outlook_delta(delta)
    changes = projected.get("changes", {})

    # summary: only from/to
    assert changes["summary"] == {"from": "中性", "to": "偏有利"}
    assert "extra_field" not in changes["summary"]
    assert "position_id" not in changes["summary"]

    # scenarios: only base/bull, only label/validation/invalidation
    assert "base" in changes["scenarios"]
    assert "bull" in changes["scenarios"]
    assert "risk" not in changes["scenarios"]
    assert "new_scenario" not in changes["scenarios"]
    assert changes["scenarios"]["base"] == {
        "label": {"from": "基准", "to": "温和"},
        "validation": {"from": "PMI扩张", "to": "GDP增长"},
        "invalidation": {"from": "通胀", "to": "通缩"},
    }
    assert "drivers" not in changes["scenarios"]["base"]
    assert "position_id" not in changes["scenarios"]["base"]

    # sector_views: only direction from/to
    assert changes["sector_views"]["科技"] == {
        "direction": {"from": "adverse", "to": "supportive"},
    }
    assert "position_id" not in changes["sector_views"]["科技"]
    assert "extra" not in changes["sector_views"]["科技"]

    # source_refs: only added/removed
    assert changes["source_refs"] == {"added": ["src-1", "src-2"], "removed": ["src-3"]}
    assert "modified" not in changes["source_refs"]

    # near_term: only direction/confidence/horizon from/to
    assert changes["near_term"] == {
        "direction": {"from": "adverse", "to": "supportive"},
        "confidence": {"from": "low", "to": "high"},
        "horizon": {"from": "1-2w", "to": "2-3w"},
    }
    assert "position_id" not in changes["near_term"]
