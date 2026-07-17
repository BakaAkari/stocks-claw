from stocks.engine.presentation import (
    anomaly_display,
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
