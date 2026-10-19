import json

from stocks.engine.investment_brief import build_brief_input, judge_brief, render_brief

SECRET_AMOUNTS = ("123456.78", "98765.43", "55555")


def _context():
    return {
        "generated_at": "2026-10-19T08:00:00+00:00",
        "position_valuations": [
            {"display_name": "半导体ETF", "instrument_key": "a:512480", "valuation_method": "market_quote",
             "market_value_cny": 123456.78, "quantity": 55555, "cost_amount": 98765.43,
             "price": 0.82, "one_day_change_pct": -1.68, "pnl_pct": -38.0,
             "indicators": {"ma_20": 0.95, "ma_60": 1.01, "rsi_14": 23.7},
             "evidence": {"price_freshness": "fresh"}},
            {"display_name": "Newmont", "instrument_key": "us:NEM", "valuation_method": "market_quote",
             "market_value_cny": 98765.43, "price": 118.6, "indicators": {},
             "evidence": {"price_freshness": "stale"}},
            {"display_name": "银行理财", "valuation_method": "manual", "market_value_cny": 55555.0},
        ],
        "news": [
            {"title": "半导体ETF公告", "url": "u1", "scope": "holding", "source_type": "filing",
             "published_at": "2026-10-18", "tags": ["512480"]},
            {"title": "美联储观点", "url": "u2", "scope": "general", "source_type": "rss"},
        ],
        "upcoming_events": [
            {"date": "2026-10-22", "name": "Newmont 财报", "event_type": "earnings", "days_until": 3,
             "affected_symbols": ["us:NEM", "us:XLE"]},
            {"date": "2026-11-02", "name": "AAPL 财报", "event_type": "earnings", "days_until": 14,
             "affected_symbols": ["us:AAPL", "us:NEM"]},
            {"date": "2026-10-28", "name": "FOMC", "event_type": "central_bank", "days_until": 9,
             "affected_symbols": ["a:512480"]},
            {"date": "2026-12-20", "name": "远期事件", "event_type": "macro_release", "days_until": 62},
        ],
        "macro_snapshot": {"vix": 18.5, "source": "yahoo"},
    }


class _Client:
    def __init__(self, reply=None, exc=None, model="m"):
        self.reply, self.exc, self.model, self.prompts = reply, exc, model, []

    def complete(self, prompt):
        self.prompts.append(prompt)
        if self.exc:
            raise self.exc
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)


GOOD = {
    "news": [{"url": "u1", "impact": "半导体ETF公告，利空"}, {"url": "evil", "impact": "编造"}],
    "news_judgment": "新闻面偏弱", "calendar_judgment": "关注 FOMC 与 NEM 财报",
    "holdings": [
        {"key": "a:512480", "action": "观察", "how": "等待站回 MA20 再分批", "levels": "支撑 0.79，止损 0.76",
         "reason": "RSI 超卖但趋势向下"},
        {"key": "a:000001", "action": "加仓", "how": "x", "levels": "x", "reason": "y"},   # 非持仓
        {"key": "us:NEM", "action": "梭哈", "how": "x", "levels": "x", "reason": "y"},      # 非法动作
    ],
    "overall": "以观察为主",
}


def test_input_has_no_amounts_or_non_market_assets():
    data = build_brief_input(_context())
    dumped = json.dumps(data, ensure_ascii=False)
    for secret in SECRET_AMOUNTS:
        assert secret not in dumped
    assert "银行理财" not in dumped
    assert [h["key"] for h in data["holdings"]] == ["a:512480", "us:NEM"]
    assert data["holdings"][0]["position_size"] == "较重仓位"  # 44.4% → 档位
    assert data["holdings"][0]["pnl_band"] == "深度浮亏"
    assert "pnl_pct" not in dumped and "weight" not in dumped
    assert [e["name"] for e in data["calendar"]] == ["Newmont 财报", "FOMC"]  # 不含非持仓财报和远期事件
    assert data["calendar"][1]["related_holdings"] == ["半导体ETF"]
    assert data["market"]["macro"] == {"vix": 18.5}
    assert data["holdings"][0]["technical"]["rsi_14"] == 23.7          # 无交易指标时退回基础指标
    assert "估值（PE/PB/分位）" in data["unavailable_data"]


def test_trader_metrics_are_used_when_provided():
    metrics = {"a:512480": {"bars": 120, "trend": {"alignment": "空头排列"}}}
    data = build_brief_input(_context(), metrics)
    assert data["holdings"][0]["technical"] == metrics["a:512480"]


def test_llm_output_is_validated_and_rendered_without_amounts():
    data = build_brief_input(_context())
    client = _Client(GOOD)
    judgment = judge_brief(data, [client])
    for secret in SECRET_AMOUNTS:
        assert secret not in client.prompts[0]
    assert judgment["status"] == "ok"
    assert [n["url"] for n in judgment["news"]] == ["u1"]                 # 非输入链接丢弃
    assert [h["key"] for h in judgment["holdings"]] == ["a:512480"]
    assert judgment["missing_holdings"] == ["us:NEM"]

    text = render_brief(data, judgment)
    for secret in SECRET_AMOUNTS + ("权重", "总资产", "¥"):
        assert secret not in text
    assert "**半导体ETF：观察**" in text
    assert "怎么做：等待站回 MA20 再分批" in text
    assert "关键价位：支撑 0.79，止损 0.76" in text
    assert "Newmont：模型未给出有效判断" in text
    assert "**判断**：关注 FOMC 与 NEM 财报" in text
    assert "行情非最新：Newmont" in text


def test_falls_back_across_models_and_keeps_news_and_calendar_when_unavailable():
    data = build_brief_input(_context())
    ok = judge_brief(data, [_Client(exc=TimeoutError("slow"), model="a"), _Client(GOOD, model="b")])
    assert ok["status"] == "ok" and ok["model"] == "b"

    bad = judge_brief(data, [_Client({"holdings": []})])
    assert bad["status"] == "unavailable"
    text = render_brief(data, bad)
    assert "LLM 判断不可用" in text
    assert "半导体ETF：[半导体ETF公告](u1)" in text
    assert "2026-10-28 FOMC（半导体ETF）" in text
    assert "无 LLM 判断" in text
    assert judge_brief(data, [])["error"] == "llm_not_configured"


def test_stream_reader_joins_content_and_skips_reasoning():
    import pytest

    from stocks.providers.openai_client import _read_stream_content

    lines = [
        b": keep-alive\n",
        b'data: {"choices": [{"delta": {"reasoning_content": "thinking"}}]}\n',
        b'data: {"choices": [{"delta": {"content": "{\\"a\\""}}]}\n',
        b'data: {"choices": [{"delta": {"content": ": 1}"}}]}\n',
        b"data: [DONE]\n",
        b'data: {"choices": [{"delta": {"content": "ignored"}}]}\n',
    ]
    assert _read_stream_content(lines) == '{"a": 1}'
    with pytest.raises(RuntimeError):
        _read_stream_content([b'data: {"error": {"message": "boom"}}\n'])
