"""Regression tests for the 2026-08-06 adversarial round 2-4 defects.

P2-3  currency_conversion failed 必须进入 data_notes(不能只存在 data_quality)
P3-4  估值 7-30 天的手工资产标记 mid-stale,data_notes 呈现时效
P4-1  macro fact 的 as_of 使用数据自身时间戳(不能伪造为当前时间)
P4-1b macro 节点 as_of 与 age_seconds 必须同源
P2-4  风险状态窗口级迁移(window_level_change)与观察级 transition 并存
"""

from __future__ import annotations

from stocks.engine.context_builder import ContextBuilder
from stocks.engine.presentation import _data_notes, _window_level_change_text
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
