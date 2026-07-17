from stocks.engine.presentation import (
    anomaly_display,
    build_user_view,
    display_label,
    freshness_is_estimate,
    public_instrument_code,
    risk_label,
    signal_label,
    status_label,
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
