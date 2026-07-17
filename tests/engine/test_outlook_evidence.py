"""Tests for whitelisted outlook evidence package and deterministic confidence cap."""
from __future__ import annotations

import json

import pytest

from stocks.engine.outlook_evidence import (
    PRIMARY_OUTLOOK_SESSIONS,
    build_outlook_evidence,
    compute_confidence_cap,
    evidence_hash,
)

NOW = "2026-07-17T14:30:00+00:00"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_context() -> dict:
    return {
        "position_valuations": [
            {
                "position_id": "p1",
                "display_name": "\u521b\u4e1a\u677fETF",
                "instrument_key": "a:159915",
                "public_code": "159915",
                "market_value_cny": 500_000.0,
                "portfolio_weight": 0.25,
                "classification": {
                    "asset_class": "equity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["cn_equity", "tech", "broad_index"],
                },
                "evidence": {"price_freshness": "fresh"},
                "valuation_method": "market_quote",
            },
            {
                "position_id": "p2",
                "display_name": "\u9ec4\u91d1ETF",
                "instrument_key": "a:518880",
                "public_code": "518880",
                "market_value_cny": 300_000.0,
                "portfolio_weight": 0.15,
                "classification": {
                    "asset_class": "commodity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["gold", "commodity"],
                },
                "evidence": {"price_freshness": "fresh"},
                "valuation_method": "market_quote",
            },
            {
                "position_id": "p3",
                "display_name": "\u6caa\u6df1300ETF",
                "instrument_key": "a:510300",
                "public_code": "510300",
                "market_value_cny": 200_000.0,
                "portfolio_weight": 0.10,
                "classification": {
                    "asset_class": "equity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["cn_equity", "broad_index"],
                },
                "evidence": {"price_freshness": "fresh"},
                "valuation_method": "market_quote",
            },
            {
                "position_id": "p4",
                "display_name": "\u7eb3\u65af\u8fbe\u514bETF",
                "instrument_key": "us:QQQ",
                "public_code": "513100",
                "market_value_cny": 800_000.0,
                "portfolio_weight": 0.40,
                "classification": {
                    "asset_class": "equity",
                    "product_type": "qdii_fund",
                    "exposure_tags": ["us_equity", "nasdaq100", "tech"],
                },
                "evidence": {"price_freshness": "fresh"},
                "valuation_method": "market_quote",
            },
            {
                "position_id": "p5",
                "display_name": "\u8d8a\u5357ETF",
                "instrument_key": "us:VNM",
                "public_code": "VNM",
                "market_value_cny": 100_000.0,
                "portfolio_weight": 0.05,
                "classification": {
                    "asset_class": "equity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["vn_equity"],
                },
                "evidence": {"price_freshness": "fresh"},
                "valuation_method": "market_quote",
            },
            {
                "position_id": "p6",
                "display_name": "\u5370\u5ea6ETF",
                "instrument_key": "us:INDA",
                "public_code": "INDA",
                "market_value_cny": 50_000.0,
                "portfolio_weight": 0.025,
                "classification": {
                    "asset_class": "equity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["in_equity"],
                },
                "evidence": {"price_freshness": "fresh"},
                "valuation_method": "market_quote",
            },
            {
                "position_id": "p7",
                "display_name": "\u95ee\u9898ETF",
                "instrument_key": "a:999999",
                "public_code": "999999",
                "market_value_cny": 30_000.0,
                "portfolio_weight": 0.015,
                "classification": {
                    "asset_class": "equity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["cn_equity"],
                },
                "evidence": {
                    "price_freshness": "fresh",
                    "data_anomalies": [
                        {"code": "single_bar_jump", "evidence": {"price": 1.2, "ma20": 1.0}}
                    ],
                },
                "valuation_method": "market_quote",
            },
            {
                "position_id": "p8",
                "display_name": "\u80fd\u6e90ETF",
                "instrument_key": "a:159930",
                "public_code": "159930",
                "market_value_cny": 20_000.0,
                "portfolio_weight": 0.01,
                "classification": {
                    "asset_class": "equity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["energy", "cn_equity"],
                },
                "evidence": {"price_freshness": "fresh"},
                "valuation_method": "market_quote",
            },
            {
                "position_id": "p9",
                "display_name": "\u79d1\u521b50ETF",
                "instrument_key": "a:588000",
                "public_code": "588000",
                "market_value_cny": 100_000.0,
                "portfolio_weight": 0.05,
                "classification": {
                    "asset_class": "equity",
                    "product_type": "exchange_traded_fund",
                    "exposure_tags": ["cn_equity", "tech", "broad_index"],
                },
                "evidence": {"price_freshness": "fresh"},
                "valuation_method": "market_quote",
            },
        ],
        "portfolio_mapping": {
            "buckets": [
                {"name": "\u6743\u76ca", "target_min": 0.5, "target_max": 0.8},
                {"name": "\u5546\u54c1", "target_min": 0.0, "target_max": 0.15},
            ],
        },
        "exposure_summary": {
            "cn_equity": 730_000.0,
            "us_equity": 800_000.0,
            "gold": 300_000.0,
            "tech": 1_300_000.0,
            "energy": 20_000.0,
            "in_equity": 50_000.0,
            "vn_equity": 100_000.0,
        },
        "liquidity_summary": {"deployable_cny": 200_000.0, "locked_cny": 50_000.0},
        "action_signals": {
            "items": [
                {
                    "symbol": "a:159915",
                    "action": "hold",
                    "direction": 1,
                    "urgency": "medium",
                },
                {
                    "symbol": "a:518880",
                    "action": "hold",
                    "direction": 0,
                    "urgency": "low",
                },
                {
                    "symbol": "us:QQQ",
                    "action": "reduce",
                    "direction": -1,
                    "urgency": "high",
                },
            ],
        },
        "rotation": {
            "items": [
                {"symbol": "a:159915", "rank": 1, "momentum": 2.5},
                {"symbol": "us:QQQ", "rank": 2, "momentum": 1.8},
                {"symbol": "a:518880", "rank": 5, "momentum": -0.5},
                {"symbol": "a:510300", "rank": 3, "momentum": 1.2},
                {"symbol": "us:VNM", "rank": 7, "momentum": -1.0},
                {"symbol": "a:588000", "rank": 4, "momentum": 0.3},
            ],
        },
        "market_state": {
            "status": "open",
            "aggregate": "mixed",
            "details": {
                "a": {"status": "closed", "freshness": "current"},
                "us": {"status": "open", "freshness": "current"},
            },
        },
        "intelligence_digest": {
            "status": "ok",
            "intelligence_health": {
                "status": "ok", "age_minutes": 30, "risk_eligible": True,
            },
            "intelligence_coverage": {"field": 80, "directional": 60, "padding": 0},
            "top_clusters": [
                {
                    "cluster_id": "cluster-oil",
                    "theme": "\u6cb9\u4ef7\u6ce2\u52a8",
                    "event_type": "geopolitical",
                    "summary": "\u4e2d\u4e1c\u5c40\u52bf\u63a8\u9ad8\u6cb9\u4ef7",
                    "articles": [
                        {
                            "source": "Reuters",
                            "title": "Oil rises as shipping risk increases",
                            "url": "https://example.test/reuters-oil",
                            "published_at": "2026-07-17T07:30:00+00:00",
                        },
                        {
                            "source": "Bloomberg",
                            "title": "Traders hedge oil exposure",
                            "url": "https://example.test/bloomberg-hedge",
                            "published_at": "2026-07-17T07:45:00+00:00",
                        },
                    ],
                    "affected_markets": ["crude_oil"],
                    "affected_symbols": ["a:159930"],
                    "sentiment": "bearish",
                    "urgency": "high",
                    "confidence": 0.85,
                    "formed_at": "2026-07-17T08:00:00+00:00",
                },
                {
                    "cluster_id": "cluster-source-less",
                    "theme": "\u65e0\u6765\u6e90",
                    "event_type": "rumor",
                    "summary": "\u65e0\u6cd5\u9a8c\u8bc1\u7684\u4e8b\u4ef6",
                    "articles": [],
                    "affected_markets": ["crypto"],
                    "affected_symbols": [],
                    "sentiment": "negative",
                    "urgency": "high",
                    "confidence": 0.3,
                    "formed_at": "2026-07-17T09:00:00+00:00",
                },
            ],
            "top_signals": [
                {"symbol": "a:159915", "direction": "hold", "urgency": "medium"},
            ],
        },
        "upcoming_events": [
            {"name": "FOMC Meeting", "scheduled_at": "2026-07-29T18:00:00+00:00", "source": "calendar"},
        ],
        "data_quality": {
            "quotes": {
                "freshness": "current",
                "by_market": {
                    "a": {"freshness": "current", "as_of": "2026-07-17T14:00:00+00:00"},
                    "us": {"freshness": "current", "as_of": "2026-07-17T14:00:00+00:00"},
                },
            },
            "macro": {"freshness": "current", "as_of": "2026-07-17T12:00:00+00:00"},
        },
        "risk_state": {
            "level": "watch",
            "transition": "stable",
            "evidence_keys": ["cluster:geopolitics"],
        },
        "cash_schedule": {
            "immediate_cash_cny": 100_000.0,
            "settling_cash_cny": 50_000.0,
            "strategic_exit_value_cny": 30_000.0,
            "locked_value_cny": 20_000.0,
        },
    }


@pytest.fixture
def sample_run() -> dict:
    return {
        "session": "cn_after_close",
        "market": "a",
        "generated_at": NOW,
        "run_id": "run-test-001",
        "risk_state": {
            "level": "watch",
            "transition": "stable",
            "evidence_keys": ["cluster:geopolitics"],
        },
        "cash_schedule": {
            "immediate_cash_cny": 100_000.0,
            "settling_cash_cny": 50_000.0,
            "strategic_exit_value_cny": 30_000.0,
            "locked_value_cny": 20_000.0,
        },
    }


# ---------------------------------------------------------------------------
# Evidence structure and field presence
# ---------------------------------------------------------------------------


def test_outlook_evidence_has_complete_structure(sample_context, sample_run):
    evidence = build_outlook_evidence(
        sample_context, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    assert set(evidence) == {
        "version", "generated_at", "session", "market",
        "portfolio_snapshot", "asset_class_snapshot", "sector_snapshot",
        "technical_evidence", "rotation_evidence", "intelligence_events",
        "directional_intelligence", "macro_evidence", "upcoming_events",
        "risk_context", "data_boundaries", "authorized_instruments",
        "confidence_cap", "confidence_reasons",
    }
    assert isinstance(evidence["version"], int)
    assert evidence["generated_at"] == NOW
    assert evidence["session"] == "cn_after_close"
    assert evidence["market"] == "a"


# ---------------------------------------------------------------------------
# Intelligence event source preservation
# ---------------------------------------------------------------------------


def test_intelligence_events_retain_sources(sample_context, sample_run):
    evidence = build_outlook_evidence(
        sample_context, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    events = evidence["intelligence_events"]
    assert len(events) >= 1
    assert events[0]["sources"][0]["source"] == "Reuters"
    assert all(event["sources"] for event in events)


# ---------------------------------------------------------------------------
# No position_id anywhere in output
# ---------------------------------------------------------------------------


def test_evidence_contains_no_position_ids(sample_context, sample_run):
    evidence = build_outlook_evidence(
        sample_context, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    dumped = json.dumps(evidence)
    assert "position_id" not in dumped
    assert "decision_id" not in dumped
    assert "p1" not in dumped
    assert "p2" not in dumped


# ---------------------------------------------------------------------------
# Source-less clusters are dropped
# ---------------------------------------------------------------------------


def test_sourceless_clusters_are_dropped(sample_context, sample_run):
    evidence = build_outlook_evidence(
        sample_context, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    themes = [e["theme"] for e in evidence["intelligence_events"]]
    assert "\u65e0\u6765\u6e90" not in themes
    assert evidence["data_boundaries"]["omitted_event_count"] >= 1


# ---------------------------------------------------------------------------
# Focus positions membership
# ---------------------------------------------------------------------------


def test_focus_positions_only_top5_conflicts_or_event_match(sample_context, sample_run):
    evidence = build_outlook_evidence(
        sample_context, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    focus = evidence["portfolio_snapshot"]["focus_positions"]
    labels = {p["display_label"] for p in focus}
    assert "\u5370\u5ea6ETF" not in labels
    assert "\u80fd\u6e90ETF\uff08159930\uff09" in labels
    assert all(p.get("display_label") for p in focus)
    assert all("position_id" not in p for p in focus)


# ---------------------------------------------------------------------------
# directional=0 \u2192 confidence cap low
# ---------------------------------------------------------------------------


def test_zero_directional_yields_low_confidence(sample_context, sample_run):
    evidence = build_outlook_evidence(
        sample_context, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    cap, reasons = evidence["confidence_cap"], evidence["confidence_reasons"]
    assert isinstance(cap, str)
    assert isinstance(reasons, list)


def test_directional_zero_coverage_yields_low(sample_context, sample_run):
    ctx = dict(sample_context)
    ctx["intelligence_digest"] = dict(ctx["intelligence_digest"])
    ctx["intelligence_digest"]["intelligence_coverage"] = {
        "field": 0, "directional": 0, "padding": 0,
    }
    ctx["intelligence_digest"]["top_signals"] = []
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    cap, reasons = evidence["confidence_cap"], evidence["confidence_reasons"]
    assert cap == "low"
    assert any("\u65b9\u5411\u6027" in r or "directional" in r.lower() for r in reasons)


# ---------------------------------------------------------------------------
# Single-source event \u2192 at most medium
# ---------------------------------------------------------------------------


def test_single_source_event_yields_medium_at_most(sample_context, sample_run):
    ctx = dict(sample_context)
    ctx["intelligence_digest"] = dict(ctx["intelligence_digest"])
    clusters = list(ctx["intelligence_digest"]["top_clusters"])
    clusters[0] = dict(clusters[0])
    clusters[0]["articles"] = [clusters[0]["articles"][0]]
    ctx["intelligence_digest"]["top_clusters"] = clusters
    ctx["intelligence_digest"]["intelligence_coverage"] = {
        "field": 40, "directional": 30, "padding": 0,
    }
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    cap = evidence["confidence_cap"]
    if cap == "high":
        pytest.fail("Single-source event should cap at medium")
    assert cap in ("medium", "low")


# ---------------------------------------------------------------------------
# Data anomaly in top-5 position \u2192 low
# ---------------------------------------------------------------------------


def test_top5_data_anomaly_yields_low(sample_context, sample_run):
    ctx = dict(sample_context)
    pv = list(ctx["position_valuations"])
    pv[0] = dict(pv[0])
    pv[0]["evidence"] = dict(pv[0].get("evidence", {}))
    pv[0]["evidence"]["data_anomalies"] = [
        {"code": "single_bar_jump", "evidence": {"price": 1.2, "ma20": 1.0}},
    ]
    ctx["position_valuations"] = pv
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    cap = evidence["confidence_cap"]
    assert cap == "low"
    assert any("\u5f02\u5e38" in r or "anomal" in r.lower() for r in evidence["confidence_reasons"])


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_outlook_session_constants():
    assert isinstance(PRIMARY_OUTLOOK_SESSIONS, set)
    assert len(PRIMARY_OUTLOOK_SESSIONS) >= 4
    assert "cn_after_close" in PRIMARY_OUTLOOK_SESSIONS
    assert "cn_pre_open" in PRIMARY_OUTLOOK_SESSIONS
    assert "us_after_close" in PRIMARY_OUTLOOK_SESSIONS
    assert "us_pre_open" in PRIMARY_OUTLOOK_SESSIONS


# ---------------------------------------------------------------------------
# compute_confidence_cap function
# ---------------------------------------------------------------------------


def test_compute_confidence_cap_returns_tuple():
    evidence = {"data_boundaries": {"omitted_event_count": 0}}
    cap, reasons = compute_confidence_cap(evidence)
    assert isinstance(cap, str)
    assert cap in ("high", "medium", "low")
    assert isinstance(reasons, list)

# ===========================================================================
# NEW TESTS — Task 2 Important Issue Fixes
# ===========================================================================


# ---------------------------------------------------------------------------
# Issue 1: _top5_anomaly must NOT appear in evidence;
#           top5_position_anomaly (public name) SHOULD appear
# ---------------------------------------------------------------------------


def test_top5_anomaly_no_underscore_key_in_data_boundaries(sample_context, sample_run):
    """_top5_anomaly / _anomaly_top5 must NOT leak; top5_position_anomaly should exist."""
    ctx = dict(sample_context)
    pv = list(ctx["position_valuations"])
    pv[0] = dict(pv[0])
    pv[0]["evidence"] = dict(pv[0].get("evidence", {}))
    pv[0]["evidence"]["data_anomalies"] = [{"code": "single_bar_jump"}]
    ctx["position_valuations"] = pv
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    dumped = json.dumps(evidence)
    assert "_top5_anomaly" not in dumped, "Underscore-prefixed key must not leak"
    assert "_anomaly_top5" not in dumped, "Underscore-prefixed key must not leak"
    assert evidence["data_boundaries"].get("top5_position_anomaly") is True


# ---------------------------------------------------------------------------
# Issue 2: data_quality must be whitelisted (only quotes & macro with safe fields)
# ---------------------------------------------------------------------------


def test_data_quality_debug_info_not_leaked(sample_context, sample_run):
    """data_quality must not pass through debug_info / internal_query_log."""
    ctx = dict(sample_context)
    dq = dict(ctx["data_quality"])
    dq["debug_info"] = {"secret": "must_not_leak"}
    dq["internal_query_log"] = ["query1", "query2"]
    dq["providers"] = {"main": "provider_a"}
    ctx["data_quality"] = dq
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    dumped = json.dumps(evidence)
    assert "debug_info" not in dumped
    assert "internal_query_log" not in dumped
    dq_out = evidence["data_boundaries"]["data_quality"]
    assert "quotes" in dq_out
    assert "macro" in dq_out


# ---------------------------------------------------------------------------
# Issue 3: conflicts must be read from run.portfolio_decision.unresolved_conflicts
# ---------------------------------------------------------------------------


def test_conflicts_read_from_run_portfolio_decision(sample_context, sample_run):
    """Conflict source must read run.portfolio_decision, not context.portfolio_decision."""
    run = dict(sample_run)
    run["portfolio_decision"] = {
        "unresolved_conflicts": [
            {"position_id": "p6", "instrument_key": "us:INDA", "reason": "duplicate entry"},
        ],
    }
    evidence = build_outlook_evidence(
        sample_context, run,
        session_id="cn_after_close", generated_at=NOW,
    )
    focus = evidence["portfolio_snapshot"]["focus_positions"]
    labels = {p["display_label"] for p in focus}
    # p6 (印度ETF) has weight 0.025, below top-5 threshold, no event tag match
    # but is in run.portfolio_decision.unresolved_conflicts → must appear
    assert "印度ETF（INDA）" in labels, (
        "印度ETF should be in focus_positions via run.portfolio_decision conflict"
    )


# ---------------------------------------------------------------------------
# Issue 4: directional coverage rule — coverage < 20% OR signal_count == 0 → low
# ---------------------------------------------------------------------------


def test_directional_zero_coverage_with_signals_yields_low(sample_context, sample_run):
    """directional=0 but signals non-empty must still yield low."""
    ctx = dict(sample_context)
    ctx["intelligence_digest"] = dict(ctx["intelligence_digest"])
    ctx["intelligence_digest"]["intelligence_coverage"] = {
        "field": 10, "directional": 0, "padding": 0,
    }
    # Keep signals non-empty
    ctx["intelligence_digest"]["top_signals"] = [
        {"symbol": "a:159915", "direction": "hold", "urgency": "medium"},
    ]
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    assert evidence["confidence_cap"] == "low"
    assert any("方向性" in r or "coverage" in r.lower() for r in evidence["confidence_reasons"])


def test_directional_low_coverage_ratio_yields_low(sample_context, sample_run):
    """directional=0 (coverage_ratio=0/1=0 < 20%) must yield low."""
    ctx = dict(sample_context)
    ctx["intelligence_digest"] = dict(ctx["intelligence_digest"])
    ctx["intelligence_digest"]["intelligence_coverage"] = {
        "field": 1, "directional": 0, "padding": 0,
    }
    ctx["intelligence_digest"]["top_signals"] = [
        {"symbol": "a:159915", "direction": "hold", "urgency": "medium"},
    ]
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    # coverage_ratio = 0 / max(1, 1) = 0.0 < 0.2 → low
    assert evidence["confidence_cap"] == "low"


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Issue 4b: directional=3, field=20 → ratio=0.15 → low, reason must mention 15%
# ---------------------------------------------------------------------------


def test_directional_coverage_ratio_15_percent_yields_low(sample_context, sample_run):
    """directional=3, field=20 → ratio=0.15 → low, reason must contain 15% or 覆盖率不足."""
    ctx = dict(sample_context)
    ctx["intelligence_digest"] = dict(ctx["intelligence_digest"])
    ctx["intelligence_digest"]["intelligence_coverage"] = {
        "field": 20, "directional": 3, "padding": 0,
    }
    ctx["intelligence_digest"]["top_signals"] = [
        {"symbol": "a:159915", "direction": "hold", "urgency": "medium"},
    ]
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    assert evidence["confidence_cap"] == "low"
    reasons_text = " ".join(evidence["confidence_reasons"])
    assert "15%" in reasons_text or "覆盖率不足" in reasons_text
    assert "覆盖率为0" not in reasons_text  # must not claim zero coverage when ratio=0.15


# ---------------------------------------------------------------------------
# Issue 4c: signal_count=0 → reason must say 无方向性信号
# ---------------------------------------------------------------------------


def test_signal_count_zero_yields_low_with_no_signal_reason(sample_context, sample_run):
    """signal_count=0 → cap low, reason must say 无方向性信号."""
    ctx = dict(sample_context)
    ctx["intelligence_digest"] = dict(ctx["intelligence_digest"])
    ctx["intelligence_digest"]["intelligence_coverage"] = {
        "field": 80, "directional": 60, "padding": 0,
    }
    ctx["intelligence_digest"]["top_signals"] = []  # empty signals
    evidence = build_outlook_evidence(
        ctx, sample_run,
        session_id="cn_after_close", generated_at=NOW,
    )
    assert evidence["confidence_cap"] == "low"
    reasons_text = " ".join(evidence["confidence_reasons"])
    assert "无方向性信号" in reasons_text


# Issue 6: evidence_hash direct tests
# ---------------------------------------------------------------------------


def test_evidence_hash_is_stable_under_generated_at_change():
    """Only generated_at changes → same hash."""
    e1 = {"version": 1, "generated_at": NOW, "session": "cn_after_close"}
    e2 = {"version": 1, "generated_at": "2099-01-01T00:00:00+00:00", "session": "cn_after_close"}
    assert evidence_hash(e1) == evidence_hash(e2)


def test_evidence_hash_changes_when_content_changes():
    """Content change (version) → different hash."""
    e1 = {"version": 1, "generated_at": NOW, "session": "cn_after_close"}
    e2 = {"version": 2, "generated_at": NOW, "session": "cn_after_close"}
    assert evidence_hash(e1) != evidence_hash(e2)
