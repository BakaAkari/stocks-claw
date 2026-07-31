from stocks.engine.presentation import (
    anomaly_display,
    build_user_view,
    display_label,
    public_instrument_code,
    risk_label,
    signal_label,
    status_label,
    suppression_reason_display,
)
from stocks.engine.valuation_freshness import freshness_is_estimate


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
    assert risk_label("hedge") == "对冲/高风险"
    assert risk_label("reduce") == "降风险"
    assert risk_label("watch") == "观察"
    assert risk_label("normal") == "常态"
    assert risk_label("totally_unknown_level") == "风险状态待确认"
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
    # market_value_cny=20_000 * ratio=0.25 would naively be 5_000.0; the
    # supplied estimated_amount_cny is deliberately different (4_200.0) to
    # prove presentation projects the adjudicator's authoritative amount
    # verbatim instead of recomputing valuation x ratio (TASK-001D item 2/3).
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.25,
            "reason": "趋势走弱", "action_description": "趋势走弱，减仓25%",
            "cancel_condition": "重新站回20日均线", "settlement_timing": "T+1",
            "next_checkpoint": "A股收盘前",
            "final_ratio": 0.21, "original_ratio": 0.25,
            "decision_reason": "趋势走弱；执行规则调整至可交易份数",
            "evidence_summary": "signal=reduce, requested_ratio=0.25, product_type=exchange_traded_fund, liquidity_tier=t0, execution_rule=etf_t0",
            "settlement_rule": "T+1", "executable_quantity": 2100.0,
            "execution_status": "adjusted_to_step",
            "estimated_amount_cny": 4_200.0, "amount_is_estimate": False,
        }],
        "suppressed_actions": [], "unresolved_conflicts": [],
        "cash_schedule": {
            "available_now": 40_000, "confirmed_settling": 2_500,
            "planned_release": 3_000, "strategic_exit": 60_000, "locked": 20_000,
        },
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020", 20_000, "previous_close")],
        [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}},
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    card = view["instruction_card"]
    assert card["status"] == "action_required"
    assert card["status_label"] == "需要操作"
    assert len(card["actions"]) == 1
    action = card["actions"][0]
    assert action["display_label"] == "化工ETF（516020）"
    assert action["action_label"] == "减仓"
    assert action["estimated_amount_cny"] == 4_200.0
    assert action["amount_is_estimate"] is False
    assert action["ratio"] == 0.21
    assert action["final_ratio"] == 0.21
    assert action["original_ratio"] == 0.25
    assert action["decision_reason"] == "趋势走弱；执行规则调整至可交易份数"
    assert action["evidence_summary"] == (
        "signal=reduce, requested_ratio=0.25, product_type=exchange_traded_fund, "
        "liquidity_tier=t0, execution_rule=etf_t0"
    )
    assert action["settlement_rule"] == "T+1"
    assert action["executable_quantity"] == 2100.0
    assert action["execution_status"] == "adjusted_to_step"
    assert "a_516020" not in str(view)
    cash = view["assistant_brief"]["cash"]
    assert cash["available_now"] == {"label": "现在能用", "amount_cny": 40_000.0}
    assert cash["confirmed_settling"] == {"label": "到账途中", "amount_cny": 2_500.0}
    assert cash["planned_release"] == {"label": "计划内到期释放", "amount_cny": 3_000.0}
    assert cash["strategic_exit"] == {"label": "卖出后才能用", "amount_cny": 60_000.0}
    assert cash["locked"] == {"label": "不能动", "amount_cny": 20_000.0}
    assert "immediate" not in cash
    assert "settling" not in cash


def test_build_user_view_never_synthesizes_missing_final_action_fields():
    """When the adjudicator omits final-action fields, presentation must project
    None/absence verbatim rather than falling back to ratio, reason, or a
    default estimate flag (TASK-001D correction item 2). final_ratio and
    execution_status must be supplied here (as "full") purely to clear
    TASK-001E1's executable gate so the action still reaches
    instruction_card.actions; every other final-action field stays absent
    and must not be synthesized."""
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.25,
            "reason": "趋势走弱", "action_description": "趋势走弱，减仓25%",
            "cancel_condition": "重新站回20日均线", "settlement_timing": "T+1",
            "next_checkpoint": "A股收盘前",
            "final_ratio": 0.25, "execution_status": "full",
            # original_ratio, decision_reason, evidence_summary,
            # settlement_rule, executable_quantity, estimated_amount_cny,
            # amount_is_estimate are all deliberately absent from the raw
            # adjudicator record here.
        }],
        "suppressed_actions": [], "unresolved_conflicts": [],
        "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020", 20_000, "previous_close")],
        [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}},
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    action = view["instruction_card"]["actions"][0]
    assert action["final_ratio"] == 0.25
    assert action["original_ratio"] is None
    assert action["decision_reason"] is None
    assert action["evidence_summary"] is None
    assert action["settlement_rule"] is None
    assert action["executable_quantity"] is None
    assert action["execution_status"] == "full"
    assert action["estimated_amount_cny"] is None
    assert action["amount_is_estimate"] is None


_FRESH_A_MARKET = {"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}}


def test_action_sentence_always_agrees_with_final_ratio_not_raw_percentage():
    """TASK-001E1 defect 1: a raw 50% requested ratio that execution_rules
    revises down to a finalized 25% must render 25% everywhere user-visible
    (reason_summary and assistant_brief.why), never the raw 50% text baked
    into the pre-adjudication action_description/reason."""
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.25,
            "reason": "趋势走弱，减仓50%", "action_description": "趋势走弱，减仓50%",
            "final_ratio": 0.25, "original_ratio": 0.5,
            "execution_status": "full", "executable_quantity": 2100.0,
        }],
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020")],
        [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries=_FRESH_A_MARKET,
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    action = view["instruction_card"]["actions"][0]
    assert "25%" in action["reason_summary"]
    assert "50%" not in action["reason_summary"]
    assert any("25%" in why for why in view["assistant_brief"]["why"])
    assert not any("50%" in why for why in view["assistant_brief"]["why"])


def test_action_displayed_ratio_field_matches_final_ratio_not_raw_ratio():
    """The projected action["ratio"] must be sourced from final_ratio, never
    the raw pre-adjudication ratio, so ratio/final_ratio cannot diverge."""
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.5,
            "final_ratio": 0.25,
            "execution_status": "full", "executable_quantity": 2100.0,
        }],
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020")],
        [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries=_FRESH_A_MARKET,
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    action = view["instruction_card"]["actions"][0]
    assert action["ratio"] == 0.25
    assert action["ratio"] == action["final_ratio"]


def test_deferred_min_unit_action_excluded_from_executable_actions():
    """Defect 2: execution_status=deferred_min_unit / final_ratio=0 /
    executable_quantity=0 must never appear in instruction_card.actions,
    and the card must not become action_required solely because of it."""
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.02,
            "reason": "小幅减仓", "final_ratio": 0.0, "executable_quantity": 0.0,
            "execution_status": "deferred_min_unit",
        }],
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020")],
        [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries=_FRESH_A_MARKET,
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    card = view["instruction_card"]
    assert card["actions"] == []
    assert card["status"] != "action_required"
    assert any("最小交易单位" in reason for reason in card["no_action_reasons"])


def test_stale_market_quote_blocks_action_even_for_cross_market_position():
    """Defect 3: an action targeting a market whose quote freshness is
    stale/missing must not be executable, including when the position
    belongs to a market other than the session's own (cross-market)."""
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "us_ita", "signal": "reduce", "ratio": 0.2,
            "reason": "美股信号", "final_ratio": 0.2, "executable_quantity": 10.0,
            "execution_status": "full",
        }],
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    data_boundaries = {"data_quality": {"quotes": {"by_market": {
        "us": {"freshness": "stale"}, "a": {"freshness": "fresh"},
    }}}}
    view = build_user_view(
        decision, [_position("us_ita", "ITA", "us:ITA")],
        [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries=data_boundaries,
        session_id="cn_after_close", session_intent="after_close_review",
    )
    card = view["instruction_card"]
    assert card["actions"] == []
    assert card["status"] != "action_required"
    assert any("行情数据过时或缺失" in reason for reason in card["no_action_reasons"])

    # Missing market entry (no_data) must fail closed the same way.
    view2 = build_user_view(
        decision, [_position("us_ita", "ITA", "us:ITA")],
        [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}},
        session_id="cn_after_close", session_intent="after_close_review",
    )
    assert view2["instruction_card"]["actions"] == []


def test_research_candidate_deduplicated_against_finalized_action_identity():
    """Defect 4: an instrument already present as a finalized approved or
    deferred action must never also appear as a research candidate."""
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.25,
            "reason": "趋势走弱", "final_ratio": 0.25, "executable_quantity": 2100.0,
            "execution_status": "full",
        }],
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020", 20_000, "previous_close")],
        [], [
            {"symbol": "a:516020", "name": "化工ETF", "action_hint": "仅供观察"},
            {"symbol": "a:512480", "name": "半导体ETF", "action_hint": "仅供观察"},
        ],
        {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries=_FRESH_A_MARKET,
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    research_labels = [item["display_label"] for item in view["assistant_brief"]["research"]]
    assert not any("516020" in label for label in research_labels)
    assert any("512480" in label for label in research_labels)


def test_unresolved_settlement_excluded_from_cash_and_surfaced_as_data_note():
    """Unresolved sale-settlement proceeds must never count as available_now
    or confirmed_settling cash, and must not become a sixth cash bucket
    (TASK-001D item 1)."""
    decision = {
        "status": "approved", "approved_actions": [], "suppressed_actions": [],
        "unresolved_conflicts": [],
        "cash_schedule": {
            "available_now": 10_000, "confirmed_settling": 2_000,
            "planned_release": 0, "strategic_exit": 5_000, "locked": 1_000,
            "unresolved_settlement": 7_500,
        },
    }
    view = build_user_view(
        decision, [], [], [], {"level": "normal", "transition": "stable", "suspend_accumulation": False},
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    cash = view["assistant_brief"]["cash"]
    # M1: six canonical spendable/blocked buckets + safety_buffer + unresolved_settlement
    # (unresolved_settlement is not a spendable bucket — it's a render-layer
    # amount field so the "资金缺口" line in §6 can quote the figure).
    assert set(cash) == {
        "available_now", "confirmed_settling", "planned_release",
        "strategic_exit", "locked", "safety_buffer", "unresolved_settlement",
    }
    assert cash["available_now"]["amount_cny"] == 10_000.0
    assert cash["confirmed_settling"]["amount_cny"] == 2_000.0
    # unresolved_settlement is exposed as an amount field, not a spendable bucket
    assert "unresolved_settlement" in cash
    assert cash["unresolved_settlement"]["amount_cny"] == 7_500
    # It still surfaces as a data_note for user attention
    notes = view["assistant_brief"]["data_notes"]
    assert any("¥7,500" in note for note in notes)


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


def test_hedge_reduce_without_triggers_fails_closed_to_review_message():
    """Defect 6: hedge/reduce risk must never render with an empty reasons list.
    When the risk_state carries no derivable trigger evidence, the level is
    itself invalid and must fail closed to a readable review message."""
    decision = {"status": "approved", "approved_actions": [], "suppressed_actions": [],
                "unresolved_conflicts": [], "cash_schedule": {}}
    view = build_user_view(
        decision, [], [], [],
        {"level": "hedge", "transition": "escalated", "suspend_accumulation": True, "triggers": []},
        session_id="cn_pre_open", session_intent="pre_open_plan",
    )
    reasons = view["assistant_brief"]["risk"]["reasons"]
    assert len(reasons) == 1
    assert "缺少可读证据" in reasons[0]


def test_stale_a_market_action_blocked_in_us_session():
    """Defect 3 symmetric: an A-share action in a US session must be blocked if
    A-market quotes are stale, just as a US action in a CN session is blocked."""
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_510300", "signal": "reduce", "ratio": 0.1,
            "reason": "A股信号", "final_ratio": 0.1, "executable_quantity": 1000.0,
            "execution_status": "full",
        }],
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    data_boundaries = {"data_quality": {"quotes": {"by_market": {
        "a": {"freshness": "stale"}, "us": {"freshness": "fresh"},
    }}}}
    view = build_user_view(
        decision, [
            {"position_id": "a_510300", "display_name": "沪深300ETF", "instrument_key": "a:510300",
             "market_value_cny": 100_000, "valuation_method": "market_quote",
             "classification": {"product_type": "exchange_traded_fund"},
             "evidence": {"price_freshness": "stale"}},
        ], [], [], {"level": "watch", "transition": "stable", "suspend_accumulation": False},
        data_boundaries=data_boundaries,
        session_id="us_after_close", session_intent="after_close_review",
    )
    card = view["instruction_card"]
    assert card["actions"] == []
    assert card["status"] != "action_required"
    assert any("行情数据过时或缺失" in reason for reason in card["no_action_reasons"])


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
         "triggers": [{"condition": "Geopolitical crisis", "value": "geopolitics critical", "severity": "hedge"}]},
        data_boundaries=data_boundaries,
        session_id="cn_after_close", session_intent="after_close_review",
    )
    brief = view["assistant_brief"]
    assert brief["conflict_summary"] == [
        {"action_label": "减仓", "count": 3}, {"action_label": "止盈", "count": 1}
    ]
    assert brief["risk"]["reasons"] == ["Geopolitical crisis：geopolitics critical"]
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


def test_executable_actions_beyond_display_cap_are_counted_not_dropped():
    """P0-3: executable approved actions past the 3-card cap must surface as
    an actions_overflow count, never vanish silently."""
    approved = []
    positions = []
    for i in range(5):
        pid = f"a_51602{i}"
        approved.append({
            "position_id": pid, "signal": "reduce", "ratio": 0.2,
            "reason": "趋势走弱", "action_description": "减仓",
            "cancel_condition": "", "settlement_timing": "T+1",
            "next_checkpoint": "",
            "final_ratio": 0.2, "original_ratio": 0.2,
            "decision_reason": "趋势走弱", "evidence_summary": "signal=reduce",
            "settlement_rule": "T+1", "executable_quantity": 1000.0,
            "execution_status": "full",
            "estimated_amount_cny": 2_000.0, "amount_is_estimate": False,
        })
        positions.append(_position(pid, f"标的{i}", f"a:51602{i}", 10_000, "fresh"))
    decision = {
        "status": "approved", "approved_actions": approved,
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    view = build_user_view(
        decision, positions, [], [],
        {"level": "normal", "transition": "stable", "suspend_accumulation": False},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}},
        session_id="cn_post_open", session_intent="post_open_decision",
    )
    card = view["instruction_card"]
    assert len(card["actions"]) == 3
    assert card["actions_overflow"] == 2


def test_no_overflow_when_all_executable_actions_fit():
    decision = {
        "status": "approved",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.25,
            "reason": "趋势走弱", "action_description": "减仓",
            "cancel_condition": "", "settlement_timing": "T+1", "next_checkpoint": "",
            "final_ratio": 0.25, "original_ratio": 0.25,
            "decision_reason": "趋势走弱", "evidence_summary": "signal=reduce",
            "settlement_rule": "T+1", "executable_quantity": 2100.0,
            "execution_status": "full",
            "estimated_amount_cny": 4_200.0, "amount_is_estimate": False,
        }],
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020", 20_000, "fresh")],
        [], [], {"level": "normal", "transition": "stable", "suspend_accumulation": False},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}},
        session_id="cn_post_open", session_intent="post_open_decision",
    )
    assert view["instruction_card"]["actions_overflow"] == 0


def test_replacement_buy_leg_deferred_text_is_chain_aware():
    """P1-4: a replacement-chain buy leg (review_required by design) must say
    it waits for sale proceeds, not the generic 'constraints unmet' text."""
    decision = {
        "status": "review_required",
        "approved_actions": [{
            "position_id": "us_qqq", "signal": "add", "ratio": 0.05,
            "reason": "卖出资金到账后转买替代标的，维持权益敞口",
            "action_description": "替代链买入",
            "cancel_condition": "", "settlement_timing": None,
            "next_checkpoint": "",
            "final_ratio": 0.05, "original_ratio": 0.05,
            "decision_reason": "卖出资金到账后转买替代标的，维持权益敞口；quantity basis portfolio is not modeled",
            "evidence_summary": "signal=add",
            "settlement_rule": "T+1", "executable_quantity": None,
            "execution_status": "review_required",
            "estimated_amount_cny": 50_000.0, "amount_is_estimate": False,
            "alternative_position_id": "a_588000",
        }],
        "suppressed_actions": [], "unresolved_conflicts": [], "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("us_qqq", "纳指ETF", "us:QQQ", 50_000, "fresh")],
        [], [], {"level": "normal", "transition": "stable", "suspend_accumulation": False},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"us": {"freshness": "fresh"}}}}},
        session_id="us_post_open", session_intent="post_open_decision",
    )
    card = view["instruction_card"]
    assert card["actions"] == []
    assert card["no_action_reasons"] == [
        "纳指ETF（QQQ）：换仓买入腿——等待卖出资金到账后执行，维持权益敞口"
    ]


# ── M1 gap-closure regressions (2026-07-31) ─────────────────────────────


def test_build_user_view_emits_reference_for_gate_rejected_action():
    """M1 truth-gate audit trail: an adjudicator-approved action that fails
    the executable gate keeps its rule-driven proposal (ratio / quantity /
    amount) as structured reference data on the instruction card."""
    decision = {
        "status": "review_required",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce", "ratio": 0.25,
            "reason": "趋势走弱",
            "final_ratio": 0.21, "execution_status": "review_required",
            "decision_reason": "权益低配与技术信号方向冲突",
            "executable_quantity": 2100.0,
            "estimated_amount_cny": 4_200.0,
        }],
        "suppressed_actions": [], "unresolved_conflicts": [],
        "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020", 20_000)],
        [], [], {"level": "normal", "transition": "stable"},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}},
        session_id="cn_after_close", session_intent="after_close_review",
    )
    card = view["instruction_card"]
    assert card["status"] == "manual_review"
    assert card["actions"] == []
    refs = card["suppressed_actions_reference"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["display_label"] == "化工ETF（516020）"
    assert ref["signal_type"] == "减仓"
    assert ref["ratio"] == 0.21
    assert ref["executable_quantity"] == 2100.0
    assert ref["estimated_amount_cny"] == 4_200.0


def test_build_user_view_no_reference_without_positive_ratio():
    """A gate-rejected action without a real proposal ratio emits no
    reference entry (nothing useful for the user to decide against)."""
    decision = {
        "status": "review_required",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "reduce",
            "reason": "趋势走弱", "execution_status": "review_required",
        }],
        "suppressed_actions": [], "unresolved_conflicts": [],
        "cash_schedule": {},
    }
    view = build_user_view(
        decision, [_position("a_516020", "化工ETF", "a:516020", 20_000)],
        [], [], {"level": "normal", "transition": "stable"},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}},
        session_id="cn_after_close", session_intent="after_close_review",
    )
    assert "suppressed_actions_reference" not in view["instruction_card"]


def test_build_user_view_pending_sell_flag_only_for_sell_signals():
    """M1: cash.pending_sell is set iff suppressed_actions contains an
    approved-but-review-pending sell (stop_loss / take_profit / reduce)."""
    base = {
        "status": "review_required",
        "approved_actions": [],
        "unresolved_conflicts": [],
        "cash_schedule": {"strategic_exit": 60_000},
    }
    sell = dict(base, suppressed_actions=[
        {"position_id": "a_516020", "signal": "stop_loss", "reason": "硬止损"},
    ])
    view = build_user_view(
        sell, [_position("a_516020", "化工ETF", "a:516020")], [], [],
        {"level": "normal", "transition": "stable"},
        session_id="cn_after_close", session_intent="after_close_review",
    )
    assert view["assistant_brief"]["cash"].get("pending_sell") is True

    # A gate-rejected approved sell also marks pending_sell.
    gate_rejected = {
        "status": "review_required",
        "approved_actions": [{
            "position_id": "a_516020", "signal": "stop_loss", "ratio": 1.0,
            "reason": "硬止损", "final_ratio": 1.0,
            "execution_status": "review_required",
        }],
        "suppressed_actions": [], "unresolved_conflicts": [],
        "cash_schedule": {"strategic_exit": 60_000},
    }
    view = build_user_view(
        gate_rejected, [_position("a_516020", "化工ETF", "a:516020")], [], [],
        {"level": "normal", "transition": "stable"},
        data_boundaries={"data_quality": {"quotes": {"by_market": {"a": {"freshness": "fresh"}}}}},
        session_id="cn_after_close", session_intent="after_close_review",
    )
    assert view["assistant_brief"]["cash"].get("pending_sell") is True

    buy = dict(base, suppressed_actions=[
        {"position_id": "a_516020", "signal": "add", "reason": "低于最小加仓金额"},
    ])
    view = build_user_view(
        buy, [_position("a_516020", "化工ETF", "a:516020")], [], [],
        {"level": "normal", "transition": "stable"},
        session_id="cn_after_close", session_intent="after_close_review",
    )
    assert "pending_sell" not in view["assistant_brief"]["cash"]
