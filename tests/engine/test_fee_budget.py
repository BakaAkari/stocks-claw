import json
from copy import deepcopy

import pytest

from scripts.smoke_financial_reconciliation import _files_hash, _isolated_adapter, fixture_document
from stocks.adapters.cli import CLIAdapter
from stocks.engine.confirmation_provenance import ConfirmationStore
from stocks.engine.fee_budget import check_purchase, preview, validate_document

NOW = "2026-10-15T07:00:00Z"


def document(market="a", account="simulation-broker"):
    return {"schema_version": 1, "budgets": [{
        "id": "simulated-budget", "account_id": account, "instrument_id": "a:518880" if market == "a" else "us:SPY",
        "market": market, "currency": "CNY" if market == "a" else "USD",
        "valid_from": "2026-10-01T00:00:00Z", "valid_until": "2026-11-01T00:00:00Z",
        "max_quantity": "100", "max_notional": "1000", "fee_upper_bound": "5",
        "source": "SIMULATED all-in per-order upper bound, not broker rates"}]}


def state(tmp_path, doc=None):
    doc = validate_document(doc or document())
    event = ConfirmationStore(tmp_path).record("fee_budgets", doc, channel="cli", confirmed_at=NOW)
    return {"status": "confirmed", "document": doc, "content_hash": event["content_hash"], "confirmation": event}


def check(s, **changes):
    args = dict(account_id="simulation-broker", instrument_id="a:518880", currency="CNY",
                quantity="100", price="8.5", available_cash="855", as_of=NOW)
    args.update(changes)
    return check_purchase(s, **args)


@pytest.mark.parametrize("market", ["a", "us"])
def test_exact_all_in_boundary_and_no_cross_currency(tmp_path, market):
    s = state(tmp_path, document(market))
    row = s["document"]["budgets"][0]
    result = check(s, instrument_id=row["instrument_id"], currency=row["currency"])
    assert result["status"] == "covered"
    assert result["total_cash_required"] == "855.0"
    assert check(s, instrument_id=row["instrument_id"], currency=row["currency"], available_cash="854.99999999")["status"] == "review_required"
    assert check(s, currency="HKD")["status"] == "review_required"


@pytest.mark.parametrize("change", [
    {"account_id": "other"}, {"instrument_id": "a:other"}, {"quantity": "101"},
    {"price": "11"}, {"price": "NaN"}, {"quantity": True}, {"available_cash": None},
    {"as_of": "2026-11-01T00:00:00Z"}, {"as_of": "2026-09-30T00:00:00Z"},
])
def test_missing_or_out_of_scope_never_assumed(tmp_path, change):
    assert check(state(tmp_path), **change)["status"] == "review_required"


@pytest.mark.parametrize("field,value", [("fee_upper_bound", 0), ("fee_upper_bound", -1),
    ("max_notional", "Infinity"), ("max_quantity", "1e99"), ("currency", "USD"),
    ("valid_until", "2026-09-01T00:00:00Z"), ("valid_from", "2026-10-01"), ("source", "")])
def test_invalid_contract_rejected(field, value):
    doc = document()
    doc["budgets"][0][field] = value
    with pytest.raises(ValueError):
        preview(doc)


def test_duplicate_scope_and_unknown_fields_rejected():
    doc = document()
    doc["budgets"].append(deepcopy(doc["budgets"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_document(doc)
    doc = document()
    doc["budgets"][0]["assumed_rate"] = 0
    with pytest.raises(ValueError):
        validate_document(doc)


def test_content_and_confirmation_tampering_blocks(tmp_path):
    s = state(tmp_path)
    s["document"]["budgets"][0]["fee_upper_bound"] = "1"
    assert check(s)["reasons"] == ["fee_budget_confirmation_mismatch"]
    s = state(tmp_path)
    s["confirmation"]["actor"] = "llm"
    assert check(s)["status"] == "review_required"
    assert check({})["reasons"] == ["confirmed_fee_budget_unavailable"]


def adapter(tmp_path):
    a = _isolated_adapter(tmp_path)
    (tmp_path / ".local/financial_assets.json").write_text(json.dumps(fixture_document()))
    return a


def test_adapter_confirmation_readonly_revocation_and_tamper(tmp_path):
    a = adapter(tmp_path)
    before = _files_hash(tmp_path)
    assert a.handle_request({"method": "fee_budget_preview", "params": {"document": document()}})["success"]
    assert not a.handle_request({"method": "fee_budget_replace", "params": {"document": document()}})["success"]
    assert before == _files_hash(tmp_path)
    with pytest.raises(ValueError, match="confirmation"):
        a.engine.replace_fee_budgets(document(), confirmation_channel="mcp")
    result = a.handle_request({"method": "fee_budget_replace", "params": {"document": document(), "confirmed": True}})
    assert result["success"], result
    assert a.engine.fee_budget_state()["status"] == "confirmed"
    before = _files_hash(tmp_path)
    a.engine.fee_budget_state()
    assert before == _files_hash(tmp_path)
    changed = document()
    changed["budgets"][0]["fee_upper_bound"] = "1"
    (tmp_path / ".local/fee_budgets.json").write_text(json.dumps(changed))
    assert a.engine.fee_budget_state()["status"] == "unverified"
    a.engine.replace_fee_budgets({"schema_version": 1, "budgets": []}, confirmed=True, confirmation_channel="mcp")
    assert a.engine.fee_budget_state()["document"]["budgets"] == []
    (tmp_path / ".local/fee_budgets.json").write_text("broken")
    assert a.engine.fee_budget_state()["status"] == "unavailable"


def test_cli_confirmed_replace_and_tool_registration(tmp_path, capsys):
    a = adapter(tmp_path)
    cli = CLIAdapter(a.engine)
    cli.run(["--fee-budget-replace", json.dumps(document())])
    assert not json.loads(capsys.readouterr().out)["success"]
    cli.run(["--fee-budget-replace", json.dumps(document()), "--confirmed"])
    assert json.loads(capsys.readouterr().out)["success"]
    for flag, args in [("--fee-budget-preview", [json.dumps(document())]), ("--fee-budget-state", [])]:
        cli.run([flag, *args])
        assert json.loads(capsys.readouterr().out)["success"]


def test_actual_isolated_scheduled_smoke(tmp_path):
    from scripts.smoke_fee_budget import run_smoke
    result = run_smoke(tmp_path)
    assert result["outcomes"] == {"covered": "approved", "insufficient_cash": "review_required", "revoked": "review_required"}
    assert result["financial_memory_unchanged_by_analysis"]
