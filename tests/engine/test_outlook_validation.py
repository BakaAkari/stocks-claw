"""Tests for structured outlook validation and hostile-output sanitization."""
from __future__ import annotations

import copy

import pytest

from stocks.engine.outlook_validation import (
    sanitize_unavailable_outlook,
    validate_structured_outlook,
)

NOW = "2026-07-17T14:30:00+00:00"


@pytest.fixture
def evidence() -> dict:
    """Minimal evidence package with one event and authorized instruments."""
    return {
        "intelligence_events": [
            {
                "event_id": "cluster-oil",
                "theme": "\u6cb9\u4ef7\u6ce2\u52a8",
                "summary": "\u4e2d\u4e1c\u5c40\u52bf\u63a8\u9ad8\u6cb9\u4ef7",
                "sources": [
                    {
                        "source": "Reuters",
                        "title": "Oil rises as shipping risk increases",
                        "url": "https://example.test/reuters-oil",
                        "published_at": "2026-07-17T07:30:00+00:00",
                    },
                ],
                "urgency": "high",
                "sentiment": "bearish",
            },
        ],
        "authorized_instruments": [
            {
                "display_label": "\u521b\u4e1a\u677fETF\uff08159915\uff09",
                "instrument_key": "a:159915",
                "asset_class": "equity",
                "product_type": "exchange_traded_fund",
                "exposure_tags": ["cn_equity", "tech", "broad_index"],
            },
            {
                "display_label": "\u9ec4\u91d1ETF\uff08518880\uff09",
                "instrument_key": "a:518880",
                "asset_class": "commodity",
                "product_type": "exchange_traded_fund",
                "exposure_tags": ["gold", "commodity"],
            },
        ],
        "confidence_cap": "high",
        "confidence_reasons": ["\u884c\u60c5\u3001\u5b8f\u89c2\u53ca\u60c5\u62a5\u6570\u636e\u5747\u5728\u6709\u6548\u671f\u5185\uff0c\u7f6e\u4fe1\u5ea6\u4e3a\u9ad8"],
    }


@pytest.fixture
def valid_outlook() -> dict:
    """Minimal perfectly valid outlook \u2014 all checks must pass."""
    return {
        "status": "ok",
        "generated_at": NOW,
        "summary": "\u7ec4\u5408\u6574\u4f53\u7814\u5224\u504f\u6b63\u9762\uff0c\u914d\u7f6e\u98ce\u9669\u4e0a\u5347",
        "near_term": {
            "horizon": "1-2w",
            "direction": "supportive",
            "confidence": "high",
        },
        "medium_term": {
            "horizon": "1-3m",
            "direction": "supportive",
            "confidence": "medium",
        },
        "scenarios": {
            "base": {
                "label": "\u57fa\u51c6\u60c5\u666f",
                "drivers": ["\u7ecf\u6d4e\u6570\u636e\u6e29\u548c\u589e\u957f", "\u6d41\u52a8\u6027\u4fdd\u6301\u5145\u88d5"],
                "portfolio_effect": "\u7ec4\u5408\u9884\u8ba1\u5c0f\u5e45\u4e0a\u6da8",
                "validation": ["GDP\u6570\u636e\u7b26\u5408\u9884\u671f"],
                "invalidation": ["\u901a\u80c0\u8d85\u9884\u671f\u4e0a\u884c"],
            },
            "bull": {
                "label": "\u4e50\u89c2\u60c5\u666f",
                "drivers": ["\u653f\u7b56\u523a\u6fc0\u8d85\u9884\u671f", "\u5916\u8d44\u6301\u7eed\u6d41\u5165"],
                "portfolio_effect": "\u7ec4\u5408\u9884\u8ba1\u660e\u663e\u4e0a\u6da8",
                "validation": ["\u793e\u878d\u6570\u636e\u5927\u5e45\u8d85\u9884\u671f"],
                "invalidation": ["\u5730\u7f18\u98ce\u9669\u7a81\u7136\u5347\u7ea7"],
            },
            "risk": {
                "label": "\u98ce\u9669\u60c5\u666f",
                "drivers": ["\u5730\u7f18\u51b2\u7a81\u5347\u7ea7", "\u5168\u7403\u8870\u9000\u98ce\u9669"],
                "portfolio_effect": "\u7ec4\u5408\u9884\u8ba1\u4e0b\u8dcc",
                "validation": ["VIX\u6307\u6570\u6301\u7eed\u9ad8\u4e8e25"],
                "invalidation": ["\u653f\u7b56\u5f3a\u529b\u5e72\u9884"],
            },
        },
        "sector_views": [
            {
                "sector": "\u79d1\u6280",
                "direction": "supportive",
                "rationale": "AI\u4ea7\u4e1a\u94fe\u666f\u6c14\u5ea6\u63d0\u5347\uff0c\u4f46\u4f30\u503c\u504f\u9ad8",
                "confidence": "high",
            },
        ],
        "asset_views": [
            {
                "asset_class": "equity",
                "direction": "supportive",
                "rationale": "\u6743\u76ca\u8d44\u4ea7\u53d7\u76ca\u4e8e\u6d41\u52a8\u6027\u5bbd\u677e",
                "confidence": "high",
            },
            {
                "asset_class": "commodity",
                "direction": "adverse",
                "rationale": "\u9ec4\u91d1\u5bf9\u51b2\u5730\u7f18\u98ce\u9669",
                "confidence": "medium",
            },
        ],
        "source_refs": [
            {
                "id": "src-reuters-oil",
                "source": "Reuters",
                "title": "Oil rises as shipping risk increases",
                "url": "https://example.test/reuters-oil",
                "published_at": "2026-07-17T07:30:00+00:00",
            },
        ],
        "confidence": "high",
        "forecast_candidates": [],
    }

# ===========================================================================
# Valid outlook passes
# ===========================================================================


def test_valid_outlook_passes(valid_outlook, evidence):
    """A perfectly formed outlook must produce zero errors."""
    errors = validate_structured_outlook(valid_outlook, evidence)
    assert errors == []


# ===========================================================================
# Required top-level fields
# ===========================================================================


def test_missing_status_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook.pop("status")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("missing top-level field: status" in e for e in errors)


def test_missing_generated_at_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook.pop("generated_at")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("missing top-level field: generated_at" in e for e in errors)


def test_missing_scenarios_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook.pop("scenarios")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("missing top-level field: scenarios" in e for e in errors)


def test_missing_near_term_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook.pop("near_term")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("missing top-level field: near_term" in e for e in errors)


def test_missing_medium_term_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook.pop("medium_term")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("missing top-level field: medium_term" in e for e in errors)


def test_missing_source_refs_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook.pop("source_refs")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("missing top-level field: source_refs" in e for e in errors)


def test_missing_confidence_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook.pop("confidence")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("missing top-level field: confidence" in e for e in errors)


# ===========================================================================
# Horizon validation
# ===========================================================================


def test_invalid_near_term_horizon_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["near_term"]["horizon"] = "3-6m"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("invalid near_term horizon" in e.lower() for e in errors)


def test_invalid_medium_term_horizon_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["medium_term"]["horizon"] = "3-6m"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("invalid medium_term horizon" in e.lower() for e in errors)


# ===========================================================================
# Scenario completeness
# ===========================================================================


def test_missing_risk_scenario_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"].pop("risk")
    errors = validate_structured_outlook(outlook, evidence)
    assert "missing scenario: risk" in " ".join(errors)


def test_missing_bull_scenario_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"].pop("bull")
    errors = validate_structured_outlook(outlook, evidence)
    assert "missing scenario: bull" in " ".join(errors)


def test_missing_base_scenario_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"].pop("base")
    errors = validate_structured_outlook(outlook, evidence)
    assert "missing scenario: base" in " ".join(errors)


# ===========================================================================
# Scenario sub-fields
# ===========================================================================


def test_scenario_missing_drivers_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"]["risk"].pop("drivers")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("risk: missing" in e and "drivers" in e for e in errors)


def test_scenario_missing_portfolio_effect_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"]["risk"].pop("portfolio_effect")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("risk: missing" in e and "portfolio_effect" in e for e in errors)


def test_scenario_missing_validation_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"]["risk"].pop("validation")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("risk: missing" in e and "validation" in e for e in errors)


def test_scenario_missing_invalidation_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"]["risk"].pop("invalidation")
    errors = validate_structured_outlook(outlook, evidence)
    assert any("risk: missing" in e and "invalidation" in e for e in errors)


# ===========================================================================
# Source authorization
# ===========================================================================


def test_invented_source_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["source_refs"].append({
        "id": "fake",
        "source": "Fake",
        "title": "Invented",
        "url": "https://invented.test",
        "published_at": NOW,
    })
    errors = validate_structured_outlook(outlook, evidence)
    assert any("unauthorized source" in e for e in errors)


def test_source_missing_title_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["source_refs"].append({
        "id": "src-inc",
        "source": "Reuters",
        "title": "",
        "url": "https://example.test/reuters-oil",
        "published_at": "2026-07-17T07:30:00+00:00",
    })
    errors = validate_structured_outlook(outlook, evidence)
    assert any("unauthorized source" in e for e in errors)


def test_source_missing_url_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["source_refs"].append({
        "id": "src-inc",
        "source": "Reuters",
        "title": "Oil rises as shipping risk increases",
        "url": "",
        "published_at": "2026-07-17T07:30:00+00:00",
    })
    errors = validate_structured_outlook(outlook, evidence)
    assert any("unauthorized source" in e for e in errors)


def test_source_missing_published_at_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["source_refs"].append({
        "id": "src-inc",
        "source": "Reuters",
        "title": "Oil rises as shipping risk increases",
        "url": "https://example.test/reuters-oil",
        "published_at": "",
    })
    errors = validate_structured_outlook(outlook, evidence)
    assert any("unauthorized source" in e for e in errors)


# ===========================================================================
# Instrument authorization
# ===========================================================================


def test_instrument_key_not_in_authorized_set_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["sector_views"].append({
        "sector": "\u672a\u6388\u6743",
        "direction": "supportive",
        "rationale": "\u5f15\u7528\u672a\u6388\u6743\u6807\u7684a:999999",
        "confidence": "low",
    })
    errors = validate_structured_outlook(outlook, evidence)
    assert any("unauthorized instrument" in e for e in errors)


# ===========================================================================
# Confidence ordering
# ===========================================================================


def test_confidence_high_when_evidence_cap_is_low_is_rejected(valid_outlook, evidence):
    ev = dict(evidence)
    ev["confidence_cap"] = "low"
    outlook = copy.deepcopy(valid_outlook)
    outlook["confidence"] = "high"
    errors = validate_structured_outlook(outlook, ev)
    assert any("confidence" in e and "exceeds" in e.lower() for e in errors)


def test_confidence_medium_when_evidence_cap_is_low_is_rejected(valid_outlook, evidence):
    ev = dict(evidence)
    ev["confidence_cap"] = "low"
    outlook = copy.deepcopy(valid_outlook)
    outlook["confidence"] = "medium"
    errors = validate_structured_outlook(outlook, ev)
    assert any("confidence" in e and "exceeds" in e.lower() for e in errors)


def test_confidence_high_when_evidence_cap_is_medium_is_rejected(valid_outlook, evidence):
    ev = dict(evidence)
    ev["confidence_cap"] = "medium"
    outlook = copy.deepcopy(valid_outlook)
    outlook["confidence"] = "high"
    errors = validate_structured_outlook(outlook, ev)
    assert any("confidence" in e and "exceeds" in e.lower() for e in errors)


def test_confidence_medium_when_evidence_cap_is_medium_is_accepted(valid_outlook, evidence):
    ev = dict(evidence)
    ev["confidence_cap"] = "medium"
    outlook = copy.deepcopy(valid_outlook)
    outlook["confidence"] = "medium"
    errors = validate_structured_outlook(outlook, ev)
    assert not any("confidence" in e and "exceeds" in e.lower() for e in errors)


# ===========================================================================
# Internal token leakage
# ===========================================================================


def test_position_id_leakage_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["sector_views"][0]["rationale"] = "position_id=a_510300"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("internal token" in e for e in errors)


def test_decision_id_leakage_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["summary"] = "decision_id=dec_20260717"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("internal token" in e for e in errors)


def test_machine_id_uuid_leakage_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["summary"] = "uuid=7c9e6679-7425-40de-944b-e07fc1f90ae7"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("internal token" in e for e in errors)


# ===========================================================================
# Trade instruction regex
# ===========================================================================


def test_trade_instruction_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["sector_views"][0]["rationale"] = "\u5efa\u8bae\u52a0\u4ed325%"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("trade instruction" in e for e in errors)


@pytest.mark.parametrize("bad_phrase", [
    "\u4e70\u5165",
    "\u5356\u51fa",
    "\u51cf\u4ed3",
    "\u52a0\u4ed3",
    "\u6e05\u4ed3",
    "\u6b62\u635f5%",
    "\u6b62\u76c810%",
    "\u4ed3\u4f4d 50%",
    "\u4ed3\u4f4d50%",
    "\u00a5100",
    "\u4eba\u6c11\u5e01100",
])
def test_various_trade_instructions_are_rejected(valid_outlook, evidence, bad_phrase):
    outlook = copy.deepcopy(valid_outlook)
    outlook["sector_views"][0]["rationale"] = f"\u5efa\u8bae{bad_phrase}"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("trade instruction" in e for e in errors)


def test_config_risk_up_is_allowed(valid_outlook, evidence):
    """\u914d\u7f6e\u98ce\u9669\u4e0a\u5347 is descriptive, not a trade instruction."""
    outlook = copy.deepcopy(valid_outlook)
    outlook["sector_views"][0]["rationale"] = "\u914d\u7f6e\u98ce\u9669\u4e0a\u5347"
    errors = validate_structured_outlook(outlook, evidence)
    trade_errors = [e for e in errors if "trade instruction" in e]
    assert trade_errors == [], (
        f"\u914d\u7f6e\u98ce\u9669\u4e0a\u5347 should not trigger trade instruction: {trade_errors}"
    )


# ===========================================================================
# Numeric authority
# ===========================================================================


def test_numeric_claim_not_in_evidence_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["summary"] = "\u7ec4\u5408\u9884\u8ba1\u56de\u62a5\u7387\u7ea68.5%"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("unauthorized number" in e for e in errors)


def test_dates_are_not_false_positive_numeric(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["summary"] = "\u5c55\u671b\u622a\u6b622026-07-17"
    errors = validate_structured_outlook(outlook, evidence)
    numeric_errors = [e for e in errors if "unauthorized number" in e]
    assert numeric_errors == []


def test_horizon_notation_is_not_false_positive_numeric(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["summary"] = "\u5c55\u671b\u671f\u96501-2w\u548c1-3m"
    errors = validate_structured_outlook(outlook, evidence)
    numeric_errors = [e for e in errors if "unauthorized number" in e]
    assert numeric_errors == []


# ===========================================================================
# sanitize_unavailable_outlook
# ===========================================================================


def test_sanitize_unavailable_outlook_shape():
    result = sanitize_unavailable_outlook(
        ["\u5185\u90e8\u9519\u8bef: cluster_id\u6cc4\u6f0f", "\u8bc1\u636e\u4e0d\u8db3", "\u6a21\u578b\u8f93\u51fa\u683c\u5f0f\u9519\u8bef"],
        generated_at=NOW,
    )
    assert result["status"] == "unavailable"
    assert result["generated_at"] == NOW
    assert "data_limitations" in result
    assert "message" in result


def test_sanitize_unavailable_outlook_reasons_capped_at_3():
    long_reasons = [f"\u9519\u8bef{i}" for i in range(10)]
    result = sanitize_unavailable_outlook(long_reasons, generated_at=NOW)
    assert len(result["data_limitations"]) == 3


def test_sanitize_unavailable_outlook_cleans_internal_codes():
    result = sanitize_unavailable_outlook(
        ["\u5185\u90e8\u9519\u8bef: cluster_id=oil\u6cc4\u6f0f", "\u6570\u636e\u8fc7\u671f"],
        generated_at=NOW,
    )
    for reason in result["data_limitations"]:
        assert "cluster_id" not in reason
        assert "=" not in reason


def test_sanitize_unavailable_outlook_message():
    result = sanitize_unavailable_outlook(["\u9519\u8bef"], generated_at=NOW)
    assert result["message"] == "\u672c\u671f\u7814\u5224\u672a\u901a\u8fc7\u6570\u636e\u5b8c\u6574\u6027\u6821\u9a8c\uff0c\u6682\u4e0d\u8f93\u51fa"


# ===========================================================================
# ===========================================================================
# Fix 1 — non-dict source_ref values must report 'invalid source_ref'
# ===========================================================================


@pytest.mark.parametrize("bad_ref", [None, "malformed_string", [1, 2, 3]])
def test_non_dict_source_ref_is_rejected(valid_outlook, evidence, bad_ref):
    outlook = copy.deepcopy(valid_outlook)
    outlook["source_refs"].append(bad_ref)
    errors = validate_structured_outlook(outlook, evidence)
    assert any("invalid source_ref" in e for e in errors)


# ===========================================================================
# Fix 2 — scenarios[*].label and source_refs[*].id included in scanning
# ===========================================================================


def test_label_with_position_id_is_rejected(valid_outlook, evidence):
    """scenarios[*].label containing position_id must trigger internal token."""
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"]["base"]["label"] = "基准情景 position_id=a_1"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("internal token" in e for e in errors)


def test_label_with_trade_instruction_is_rejected(valid_outlook, evidence):
    """scenarios[*].label containing trade phrasing must trigger instruction."""
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"]["base"]["label"] = "建议加仓25%情景"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("trade instruction" in e for e in errors)


def test_source_ref_id_with_decision_id_is_rejected(valid_outlook, evidence):
    """source_refs[*].id containing decision_id must trigger internal token."""
    outlook = copy.deepcopy(valid_outlook)
    outlook["source_refs"][0]["id"] = "decision_id=abc"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("internal token" in e for e in errors)


# ===========================================================================
# Fix 3 — status type/value strictness, generated_at ISO-8601
# ===========================================================================


def test_status_string_not_ok_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook["status"] = "partial"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("invalid status" in e.lower() for e in errors)


def test_status_non_string_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook["status"] = 12345
    errors = validate_structured_outlook(outlook, evidence)
    assert any("invalid status" in e.lower() for e in errors)


def test_status_none_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook["status"] = None
    errors = validate_structured_outlook(outlook, evidence)
    assert any("invalid status" in e.lower() for e in errors)


def test_generated_at_invalid_format_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook["generated_at"] = "not-a-date"
    errors = validate_structured_outlook(outlook, evidence)
    assert any("generated_at" in e and "iso" in e.lower() for e in errors)


def test_generated_at_none_is_rejected(valid_outlook, evidence):
    outlook = dict(valid_outlook)
    outlook["generated_at"] = None
    errors = validate_structured_outlook(outlook, evidence)
    assert any("generated_at" in e for e in errors)


# Combined / regression
# ===========================================================================


def test_multiple_errors_accumulate(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook.pop("status")
    outlook.pop("generated_at")
    outlook["scenarios"].pop("risk")
    errors = validate_structured_outlook(outlook, evidence)
    assert len(errors) >= 3


def test_empty_outlook_is_rejected(evidence):
    errors = validate_structured_outlook({}, evidence)
    assert len(errors) > 0


def test_explicit_scenario_probability_is_rejected(valid_outlook, evidence):
    outlook = copy.deepcopy(valid_outlook)
    outlook["scenarios"]["base"]["probability"] = 0.5
    errors = validate_structured_outlook(outlook, evidence)
    assert any("probability" in error for error in errors)
