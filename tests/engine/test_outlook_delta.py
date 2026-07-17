"""Tests for deterministic outlook delta computation and state dedup."""
from __future__ import annotations

from pathlib import Path

from stocks.engine.outlook_delta import OutlookDeltaState, compute_outlook_delta

NOW = "2026-07-17T14:30:00+00:00"


def _make_outlook(session: str, scenarios: dict, sector_views: list[dict] | None = None,
                  confidence: str = "high") -> dict:
    return {
        "session": session,
        "market": "cn",
        "generated_at": NOW,
        "structured_outlook": {
            "status": "ok",
            "generated_at": NOW,
            "summary": "组合整体研判偏正面",
            "scenarios": scenarios,
            "sector_views": sector_views or [],
            "asset_views": [],
            "confidence": confidence,
            "source_refs": [
                {"id": "src-1", "source": "Reuters", "title": "Test", "url": "https://test", "published_at": NOW},
            ],
            "near_term": {"horizon": "1-2w", "direction": "supportive", "confidence": "high"},
            "medium_term": {"horizon": "1-3m", "direction": "supportive", "confidence": "medium"},
            "forecast_candidates": [],
        },
    }


# ── compute_outlook_delta tests ──────────────────────────────────────────


def test_delta_both_none_returns_empty():
    assert compute_outlook_delta(None, None) == {}


def test_delta_prev_none_returns_empty():
    cur = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    assert compute_outlook_delta(None, cur) == {}


def test_delta_curr_none_returns_empty():
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    assert compute_outlook_delta(prev, None) == {}


def test_delta_identical_outlooks_returns_empty():
    outlook = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    assert compute_outlook_delta(outlook, outlook) == {}


def test_delta_changed_scenario_label_produces_delta():
    prev = _make_outlook("cn_after_close", {"base": {"label": "基准情景"}})
    curr = _make_outlook("cn_after_close", {"base": {"label": "谨慎乐观情景"}})
    delta = compute_outlook_delta(prev, curr)
    assert delta
    assert "changes" in delta
    assert delta.get("market") == "cn"
    assert delta["previous_session"] == "cn_after_close"
    assert delta["current_session"] == "cn_after_close"


def test_delta_changed_sector_direction_produces_delta():
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}},
                         sector_views=[{"sector": "科技", "direction": "bullish", "rationale": "A"}])
    curr = _make_outlook("cn_after_close", {"base": {"label": "B"}},
                         sector_views=[{"sector": "科技", "direction": "bearish", "rationale": "A"}])
    delta = compute_outlook_delta(prev, curr)
    assert delta
    assert "changes" in delta


def test_delta_changed_confidence_produces_delta():
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}}, confidence="high")
    curr = _make_outlook("cn_after_close", {"base": {"label": "B"}}, confidence="medium")
    delta = compute_outlook_delta(prev, curr)
    assert delta
    assert delta["changes"].get("confidence") == {"from": "high", "to": "medium"}


def test_delta_one_side_status_not_ok_returns_empty():
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    curr = dict(prev)
    curr["structured_outlook"] = {"status": "unavailable", "message": "fail"}
    assert compute_outlook_delta(prev, curr) == {}


# ── OutlookDeltaState tests ──────────────────────────────────────────────


def test_delta_state_first_emit_returns_true(tmp_path: Path):
    state = OutlookDeltaState(tmp_path / "delta_state.json")
    delta = {"changes": {"summary": {"from": "A", "to": "B"}}, "market": "cn"}
    assert state.should_emit("cn", delta) is True


def test_delta_state_identical_emit_returns_false(tmp_path: Path):
    state = OutlookDeltaState(tmp_path / "delta_state.json")
    delta = {"changes": {"summary": {"from": "A", "to": "B"}}, "market": "cn"}
    assert state.should_emit("cn", delta) is True
    assert state.should_emit("cn", delta) is False


def test_delta_state_different_emit_returns_true(tmp_path: Path):
    state = OutlookDeltaState(tmp_path / "delta_state.json")
    d1 = {"changes": {"summary": {"from": "A", "to": "B"}}, "market": "cn"}
    d2 = {"changes": {"summary": {"from": "B", "to": "C"}}, "market": "cn"}
    assert state.should_emit("cn", d1) is True
    assert state.should_emit("cn", d2) is True


def test_delta_state_empty_delta_returns_false(tmp_path: Path):
    state = OutlookDeltaState(tmp_path / "delta_state.json")
    assert state.should_emit("cn", {}) is False


def test_delta_state_persists_across_reload(tmp_path: Path):
    path = tmp_path / "delta_state.json"
    state1 = OutlookDeltaState(path)
    delta = {"changes": {"summary": {"from": "A", "to": "B"}}, "market": "cn"}
    assert state1.should_emit("cn", delta) is True

    state2 = OutlookDeltaState(path)
    assert state2.should_emit("cn", delta) is False


def test_delta_state_different_market_independent(tmp_path: Path):
    state = OutlookDeltaState(tmp_path / "delta_state.json")
    d_cn = {"changes": {"s": {"from": "A", "to": "B"}}, "market": "cn"}
    d_us = {"changes": {"s": {"from": "C", "to": "D"}}, "market": "us"}
    assert state.should_emit("cn", d_cn) is True
    assert state.should_emit("cn", d_cn) is False
    assert state.should_emit("us", d_us) is True
    assert state.should_emit("us", d_us) is False


# ── Improved delta output tests ───────────────────────────────────────────


def test_delta_changed_scenario_validation_invalidation():
    """Scenario validation/invalidation changes produce concrete from/to."""
    prev = _make_outlook("cn_after_close", {
        "base": {"label": "基准", "validation": ["GDP>=5%"], "invalidation": ["CPI>3%"]},
    })
    curr = _make_outlook("cn_after_close", {
        "base": {"label": "基准", "validation": ["GDP>=4.5%"], "invalidation": ["CPI>3%"]},
    })
    delta = compute_outlook_delta(prev, curr)
    assert delta
    sc = delta["changes"].get("scenarios", {})
    assert "base" in sc
    assert sc["base"]["validation"] == {"from": ["GDP>=5%"], "to": ["GDP>=4.5%"]}
    assert "invalidation" not in sc["base"]  # unchanged


def test_delta_changed_scenario_label_concrete_from_to():
    """Scenario label changes produce concrete from/to values."""
    prev = _make_outlook("cn_after_close", {
        "base": {"label": "基准情景"},
    })
    curr = _make_outlook("cn_after_close", {
        "base": {"label": "谨慎乐观情景"},
    })
    delta = compute_outlook_delta(prev, curr)
    assert delta
    sc = delta["changes"].get("scenarios", {})
    assert sc["base"]["label"] == {"from": "基准情景", "to": "谨慎乐观情景"}


def test_delta_changed_sector_direction_concrete():
    """Sector direction change shows identifying key + from/to direction."""
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}},
                         sector_views=[{"sector": "科技", "direction": "bullish"}])
    curr = _make_outlook("cn_after_close", {"base": {"label": "B"}},
                         sector_views=[{"sector": "科技", "direction": "bearish"}])
    delta = compute_outlook_delta(prev, curr)
    assert delta
    sv = delta["changes"].get("sector_views", {})
    assert "科技" in sv
    assert sv["科技"]["direction"] == {"from": "bullish", "to": "bearish"}


def test_delta_changed_horizon_direction_confidence():
    """Horizon block changes show concrete direction/confidence."""
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    curr = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    # Patch the structured outlook near_term
    prev["structured_outlook"]["near_term"] = {"horizon": "1-2w", "direction": "supportive", "confidence": "high"}
    curr["structured_outlook"]["near_term"] = {"horizon": "1-2w", "direction": "cautious", "confidence": "medium"}
    delta = compute_outlook_delta(prev, curr)
    assert delta
    nt = delta["changes"].get("near_term", {})
    assert nt["direction"] == {"from": "supportive", "to": "cautious"}
    assert nt["confidence"] == {"from": "high", "to": "medium"}
    assert "horizon" not in nt  # unchanged


def test_delta_changed_source_ids_concrete():
    """Source ID changes show added/removed IDs, not counts."""
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    curr = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    # Replace source_refs
    prev["structured_outlook"]["source_refs"] = [
        {"id": "src-a", "source": "R1"},
        {"id": "src-b", "source": "R2"},
    ]
    curr["structured_outlook"]["source_refs"] = [
        {"id": "src-a", "source": "R1"},
        {"id": "src-c", "source": "R3"},
    ]
    delta = compute_outlook_delta(prev, curr)
    assert delta
    sr = delta["changes"].get("source_refs", {})
    assert sr.get("added") == ["src-c"]
    assert sr.get("removed") == ["src-b"]


def test_delta_unknown_extra_keys_ignored():
    """Keys not in the whitelist are silently ignored."""
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    curr = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    # Inject model-hallucinated keys
    curr["structured_outlook"]["buy_signal"] = "强烈买入"
    curr["structured_outlook"]["target_price"] = 999
    delta = compute_outlook_delta(prev, curr)
    # Should be empty — whitelist ignores unknown keys
    assert delta == {}


def test_delta_fingerprint_ignores_metadata():
    """Fingerprint-based dedup treats identical changes (different metadata) as duplicate."""
    from stocks.engine.outlook_delta import _stable_fingerprint
    d1 = {
        "market": "cn",
        "changes": {"summary": {"from": "A", "to": "B"}},
        "previous_session": "sess1",
        "current_session": "sess2",
        "previous_generated_at": "2026-07-06T00:00:00Z",
        "current_generated_at": "2026-07-07T00:00:00Z",
    }
    d2 = {
        "market": "cn",
        "changes": {"summary": {"from": "A", "to": "B"}},
        "previous_session": "sess3",
        "current_session": "sess4",
        "previous_generated_at": "2026-07-08T00:00:00Z",
        "current_generated_at": "2026-07-09T00:00:00Z",
    }
    assert _stable_fingerprint(d1) == _stable_fingerprint(d2)


def test_source_refs_filters_invalid_ids():
    """None, empty, and non-string source ref IDs are excluded from delta."""
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    curr = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    prev["structured_outlook"]["source_refs"] = [
        {"id": "valid-1", "source": "R1"},
        {"id": None, "source": "R2"},
        {"id": "", "source": "R3"},
        {"id": "   ", "source": "R4"},
        {},  # no id key at all
    ]
    curr["structured_outlook"]["source_refs"] = [
        {"id": "valid-1", "source": "R1"},
        {"id": "valid-2", "source": "R5"},
    ]
    delta = compute_outlook_delta(prev, curr)
    assert delta
    sr = delta["changes"].get("source_refs", {})
    assert sr.get("added") == ["valid-2"]
    # None / empty / missing / whitespace-only ids must never appear in the delta
    assert None not in sr.get("added", [])
    assert "" not in sr.get("added", [])
    assert None not in sr.get("removed", [])
    assert "" not in sr.get("removed", [])


def test_source_refs_all_invalid_ids_no_delta_in_source_refs():
    """When only invalid IDs are present, source_refs delta is empty."""
    prev = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    curr = _make_outlook("cn_after_close", {"base": {"label": "B"}})
    prev["structured_outlook"]["source_refs"] = [
        {"id": None, "source": "R1"},
    ]
    curr["structured_outlook"]["source_refs"] = [
        {"id": "", "source": "R2"},
    ]
    delta = compute_outlook_delta(prev, curr)
    if delta:
        assert "source_refs" not in delta.get("changes", {})
