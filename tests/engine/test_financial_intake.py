import json
from copy import deepcopy

import pytest

from scripts.smoke_financial_reconciliation import _files_hash, _isolated_adapter, sample_facts
from stocks.adapters.cli import CLIAdapter
from stocks.engine.financial_intake import account_readiness, intake_template, preview_intake

NOW = "2026-10-10T00:00:00Z"


def bundle_fixture(market="a"):
    opening, closing, fills, movement, _ = sample_facts()
    value = {"schema_version": 1, "account_id": opening["account_id"], "market": "a",
             "opening_statement": opening, "closing_statement": closing,
             "executions": fills, "cash_movements": [movement]}
    if market == "us":
        value = json.loads(json.dumps(value).replace('a:518880', 'us:SPY').replace('CNY', 'USD'))
        value["market"] = "us"
    return value


@pytest.mark.parametrize("market", ["a", "us"])
def test_preview_is_reconcilable_but_never_confirmed_or_action_ready(market):
    bundle = bundle_fixture(market)
    before = deepcopy(bundle)
    result = preview_intake(bundle, as_of=NOW)
    assert result["errors"] == []
    assert result["status"] == "ready_for_user_review", result
    assert result["reconciliation"]["status"] == "matched"
    assert result["confirmation_state"] == "unconfirmed_draft"
    assert result["requires_confirmation"] is True
    assert result["financial_memory_written"] is False
    assert result["advisory_ready"] is False
    assert bundle == before


@pytest.mark.parametrize("case", ["account", "market", "duplicate", "future", "missing_cash", "unknown_fee",
                                   "partial", "same_id", "outside_interval", "unknown_field", "bad_time", "future_settlement"])
def test_invalid_evidence_is_visible_not_silently_dropped(case):
    bundle = bundle_fixture()
    if case == "account":
        bundle["executions"][0]["account_id"] = "another-account"
    elif case == "market":
        bundle["market"] = "us"
    elif case == "duplicate":
        bundle["executions"].append(deepcopy(bundle["executions"][0]))
    elif case == "future":
        bundle["closing_statement"]["as_of"] = "2026-10-11T00:00:00Z"
    elif case == "missing_cash":
        bundle["closing_statement"]["cash"][0]["balance"] = None
    elif case == "unknown_fee":
        bundle["executions"][0]["fee_amount"] = None
    elif case == "partial":
        bundle["closing_statement"]["scope"] = "listed_only"
    elif case == "same_id":
        bundle["closing_statement"]["id"] = bundle["opening_statement"]["id"]
    elif case == "outside_interval":
        bundle["cash_movements"][0]["effective_at"] = "2026-09-01T00:00:00Z"
    elif case == "unknown_field":
        bundle["executions"][0]["available_cash"] = 100000
    elif case == "future_settlement":
        bundle["executions"][0]["settled_at"] = "2026-10-11T00:00:00Z"
    else:
        bundle["opening_statement"]["as_of"] = "2026-10-01"
    result = preview_intake(bundle, as_of=NOW)
    assert result["status"] == "needs_correction", result
    assert result["errors"] or result["reconciliation"]["status"] != "matched"
    assert result["financial_memory_written"] is False


def test_difference_is_reported_without_repairing_closing_statement():
    bundle = bundle_fixture()
    bundle["closing_statement"]["cash"][0]["balance"] = "9999"
    result = preview_intake(bundle, as_of=NOW)
    assert result["status"] == "needs_correction"
    assert result["reconciliation"]["status"] == "mismatch"
    assert result["proposed_records"]["closing_statement"]["cash"][0]["balance"] == "9999"


@pytest.mark.parametrize("market", ["a", "us"])
def test_template_keeps_unknowns_and_cannot_be_mistaken_for_complete_account(market):
    result = preview_intake(intake_template(market), as_of=NOW)
    assert result["status"] == "needs_correction"
    assert result["errors"]


def readiness_inputs():
    integrity = {"status": "matched", "gaps": [], "accounts": {
        "test-account": {"status": "matched", "statement_id": "closing", "statement_as_of": NOW,
                         "cash_by_currency": {"USD": {"available_balance": "10"}}}},
        "positions": {"position": {"account_id": "test-account", "available_quantity": "2"}}}
    mandate = {"sources": {key: {"kind": "local_confirmed"} for key in ("profile", "constraints")},
               "constraint_snapshot": {"status": "confirmed"},
               "fields": {key: {"status": "confirmed"} for key in ("loss_capacity", "cash_needs")}}
    return integrity, mandate


@pytest.mark.parametrize("time,expected", [(NOW, "current_facts_available"),
    ("2026-10-12T00:00:00Z", "review_required"), ("2026-10-09T00:00:00Z", "review_required")])
def test_historical_match_is_distinct_from_current_readiness(time, expected):
    integrity, mandate = readiness_inputs()
    report = account_readiness(integrity, mandate, as_of=time)
    assert report["status"] == expected
    assert report["advisory_ready"] is False


def test_readiness_preserves_unknown_available_values_and_bad_policy():
    integrity, mandate = readiness_inputs()
    integrity["positions"]["position"]["available_quantity"] = None
    integrity["accounts"]["test-account"]["cash_by_currency"]["USD"]["available_balance"] = None
    report = account_readiness(integrity, mandate, as_of=NOW, max_age_seconds=0)
    assert "invalid_statement_freshness_policy" in report["blockers"]
    assert set(report["accounts"]["test-account"]["blockers"]) == {
        "available_quantity_unknown:position", "available_cash_unknown:USD"}


def test_cli_and_mcp_leave_all_financial_files_unchanged(tmp_path, capsys):
    adapter = _isolated_adapter(tmp_path)
    bundle = bundle_fixture()
    source = tmp_path / "intake.json"
    source.write_text(json.dumps(bundle))
    before = _files_hash(tmp_path)
    for method, params in (("financial_intake_template", {"market": "a"}),
                           ("financial_intake_preview", {"bundle": bundle, "as_of": NOW}),
                           ("financial_readiness", {"as_of": NOW}), ("agent_status", {"as_of": NOW})):
        result = adapter.handle_request({"method": method, "params": params})
        assert result["success"], result
    cli = CLIAdapter(adapter.engine)
    for args in (["--financial-intake-template", "us"],
                 ["--financial-intake-preview", json.dumps(bundle), "--now", NOW],
                 ["--financial-intake-preview-file", str(source), "--now", NOW],
                 ["--financial-readiness", "--now", NOW]):
        cli.run(args)
        assert json.loads(capsys.readouterr().out)["success"]
    assert _files_hash(tmp_path) == before
    assert {"financial_intake_preview", "financial_readiness", "financial_intake_template"} <= {
        tool["name"] for tool in adapter.list_tools()}


def test_preview_file_failure_is_reported_without_traceback_or_writes(tmp_path, capsys):
    adapter = _isolated_adapter(tmp_path)
    before = _files_hash(tmp_path)
    CLIAdapter(adapter.engine).run(["--financial-intake-preview-file", str(tmp_path / "missing.json")])
    assert json.loads(capsys.readouterr().out)["success"] is False
    assert _files_hash(tmp_path) == before


def test_account_readiness_smoke(tmp_path):
    from scripts.smoke_account_readiness import run_smoke
    summary = run_smoke(tmp_path)
    assert summary["financial_memory_unchanged"]
    assert summary["stale_account_blocked"]
    assert summary["real_user_records"] is False
