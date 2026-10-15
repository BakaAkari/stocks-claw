"""Personal constraints stay explicit and retain their confirmation provenance."""

from copy import deepcopy

import pytest

from stocks.domain.models import AnalysisContext
from stocks.engine.personal_mandate import build_personal_mandate, validate_mandate_update

NOW = "2026-10-01T00:00:00Z"
LOCAL = {"kind": "local_confirmed", "path": "test/profile.json", "updated_at": NOW}


def mandate():
    return {
        "goals": "保留近期生活资金", "horizon": "三年", "operation_frequency": "每月一次",
        "loss_capacity": {"amount": 1000, "currency": "CNY"},
        "cash_needs": [{"account_id": "account-a", "currency": "CNY", "amount": 500,
                        "needed_at": "2026-11-01T00:00:00+08:00"}],
        "style_preferences": ["先核对事实"],
    }


def snapshot(profile, *, profile_source=None, constraints_source=None, captured_at=NOW):
    return build_personal_mandate(
        profile, {"现金": {"min": 0.2, "max": 0.8}}, profile_source=profile_source or LOCAL,
        constraints_source=constraints_source or LOCAL, captured_at=captured_at,
    )


def test_complete_explicit_mandate_preserves_values_and_detaches_source():
    raw = {"mandate": mandate(), "risk_tolerance": "high"}
    before = deepcopy(raw)
    result = snapshot(raw)
    assert result["missing_fields"] == []
    assert result["fields"]["loss_capacity"] == {
        "value": {"amount": 1000, "currency": "CNY"}, "status": "confirmed",
        "source_field": "profile.mandate.loss_capacity",
    }
    assert result["constraint_snapshot"]["status"] == "confirmed"
    result["fields"]["cash_needs"]["value"][0]["amount"] = 999
    result["raw_profile"]["mandate"]["goals"] = "changed"
    assert raw == before


def test_risk_appetite_and_legacy_preferences_never_invent_capacity_or_style():
    result = snapshot({"risk_tolerance": "high", "investment_horizon": "long_term",
                       "preferences": ["左侧"]})
    assert result["fields"]["horizon"]["value"] == "long_term"
    assert result["fields"]["horizon"]["source_field"] == "profile.investment_horizon"
    for key in ("goals", "loss_capacity", "cash_needs", "operation_frequency", "style_preferences"):
        assert result["fields"][key]["value"] is None
        assert result["fields"][key]["status"] == "unknown"
    assert result["raw_profile"]["preferences"] == ["左侧"]


def test_explicit_null_clears_legacy_horizon_and_empty_lists_are_meaningful():
    result = snapshot({"investment_horizon": "long_term", "mandate": {
        "horizon": None, "cash_needs": [], "style_preferences": [], "goals": "  ",
    }})
    assert result["fields"]["horizon"]["value"] is None
    assert result["fields"]["horizon"]["status"] == "unknown"
    assert result["fields"]["goals"]["status"] == "unknown"
    for key in ("cash_needs", "style_preferences"):
        assert result["fields"][key]["value"] == []
        assert result["fields"][key]["status"] == "confirmed"


@pytest.mark.parametrize("kind", ["legacy_unverified", "reference_default", "missing"])
def test_unconfirmed_source_is_never_upgraded_by_presence_of_values(kind):
    result = snapshot({"mandate": mandate()}, profile_source={"kind": kind},
                      constraints_source={"kind": kind})
    assert all(field["status"] == "unknown" for field in result["fields"].values())
    assert result["fields"]["goals"]["value"] == "保留近期生活资金"
    assert result["constraint_snapshot"]["status"] == "unknown"


def test_hash_is_stable_on_repeated_capture_but_binds_values_and_confirmation_source():
    profile = {"mandate": mandate()}
    first = snapshot(profile)
    later = snapshot(profile, captured_at="2026-10-02T00:00:00Z")
    assert first["content_hash"] == later["content_hash"]
    changed = deepcopy(profile)
    changed["mandate"]["loss_capacity"]["amount"] = 2000
    assert snapshot(changed)["content_hash"] != first["content_hash"]
    downgraded = snapshot(profile, profile_source={**LOCAL, "kind": "legacy_unverified"})
    assert downgraded["content_hash"] != first["content_hash"]
    assert downgraded["sources"]["profile"]["content_hash"] == first["sources"]["profile"]["content_hash"]
    reference = snapshot(profile, constraints_source={**LOCAL, "kind": "reference_default"})
    assert reference["content_hash"] != first["content_hash"]


@pytest.mark.parametrize("invalid", [
    {"unexpected": 1}, {"goals": []}, {"horizon": 1}, {"operation_frequency": True},
    {"loss_capacity": {"amount": -1, "currency": "CNY"}},
    {"loss_capacity": {"amount": True, "currency": "CNY"}},
    {"loss_capacity": {"amount": float("inf"), "currency": "CNY"}},
    {"loss_capacity": {"amount": float("nan"), "currency": "CNY"}},
    {"loss_capacity": {"amount": 10}},
    {"loss_capacity": {"amount": 10, "currency": ""}},
    {"cash_needs": [{"account_id": "a", "amount": 10, "currency": "CNY", "needed_at": "2026-11-01"}]},
    {"cash_needs": [{"account_id": "", "amount": 10, "currency": "CNY", "needed_at": NOW}]},
    {"cash_needs": {"amount": 10}}, {"style_preferences": "value"},
    {"style_preferences": [1]},
])
def test_bad_or_ambiguous_mandate_rejected(invalid):
    with pytest.raises(ValueError):
        validate_mandate_update(invalid)


def test_validation_returns_partial_copy_and_keeps_zero_capacity_known():
    raw = {"loss_capacity": {"amount": 0, "currency": "cny"}}
    validated = validate_mandate_update(raw)
    assert validated == {"loss_capacity": {"amount": 0, "currency": "CNY"}}
    assert raw["loss_capacity"]["currency"] == "cny"
    assert set(validated) == {"loss_capacity"}
    assert snapshot({"mandate": validated})["fields"]["loss_capacity"]["status"] == "confirmed"


def test_context_roundtrip_retains_same_constraints_and_integrity_evidence():
    data = {"personal_mandate": snapshot({"mandate": mandate()}),
            "financial_integrity": {"status": "unreconciled", "source_hash": "evidence"}}
    context = AnalysisContext.from_dict(data)
    exported = context.to_dict()
    assert exported["personal_mandate"] == data["personal_mandate"]
    assert exported["financial_integrity"] == data["financial_integrity"]
    exported["financial_integrity"]["status"] = "changed"
    assert context.financial_integrity["status"] == "unreconciled"
    assert AnalysisContext.from_dict({}).personal_mandate == {}
