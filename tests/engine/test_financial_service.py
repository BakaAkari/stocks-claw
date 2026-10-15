"""Public confirmed finance APIs using only explicit simulated temporary data."""

import builtins
import io
import json
import socket
import subprocess
import urllib.request
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.smoke_financial_reconciliation import (
    _files_hash,
    _isolated_adapter,
    fixture_document,
    sample_facts,
)

RECORDED = "2026-10-10T00:00:00Z"
AS_OF = "2026-10-14T00:00:00Z"


def call(adapter, method, params=None):
    result = adapter.handle_request({"method": method, "params": params or {}})
    assert result.get("success"), result
    return result["data"]


@pytest.fixture
def finance(tmp_path):
    adapter = _isolated_adapter(tmp_path)
    path = tmp_path / ".local" / "financial_assets.json"
    path.write_text(json.dumps(fixture_document()), encoding="utf-8")
    adapter.engine._assets = adapter.engine._load_assets_from_file()
    return adapter, tmp_path


def requests():
    opening, closing, fills, movement, mandate = deepcopy(sample_facts())
    facts = [("statement_save", "statement", opening),
             *[("execution_save", "execution", fill) for fill in fills],
             ("cash_movement_save", "movement", movement),
             ("statement_save", "statement", closing)]
    return [(method, {key: {**row, "recorded_at": RECORDED}}) for method, key, row in facts], mandate


def seed(adapter):
    facts, mandate = requests()
    for method, params in facts:
        call(adapter, method, {**params, "confirmed": True})
    call(adapter, "profile_update", {"profile": {"mandate": mandate}, "confirmed": True})
    return facts


@pytest.mark.parametrize("confirmation", [None, False, "true", 1])
def test_public_confirmation_gate_writes_nothing(finance, confirmation):
    adapter, output = finance
    facts, mandate = requests()
    all_requests = facts + [("profile_update", {"profile": {"mandate": mandate}}),
                            ("constraints_replace", {"constraints": {}})]
    before = _files_hash(output / ".local")
    for method, params in all_requests:
        if confirmation is not None:
            params = {**params, "confirmed": confirmation}
        result = adapter.handle_request({"method": method, "params": params})
        assert result["success"] is False
        assert "confirm" in result["error"].lower()
    assert _files_hash(output / ".local") == before


def test_public_manual_fill_and_reconciliation_preserve_independent_facts(finance):
    adapter, output = finance
    seed(adapter)
    fills = call(adapter, "execution_list")
    assert len(fills) == 2
    for fill in fills:
        assert fill["schema_version"] == 3 and fill["record_origin"] == "manual"
        assert fill["decision_id"] is None and fill["advice_id"] is None
        assert "source_run_id" not in fill and "user_decision_id" not in fill
    before = _files_hash(output / ".local")
    report = call(adapter, "financial_reconcile", {"opening_id": "opening", "closing_id": "closing", "as_of": AS_OF})
    integrity = call(adapter, "financial_integrity", {"as_of": AS_OF})
    assert report["status"] == integrity["status"] == "matched"
    assert report["cash_by_currency"]["CNY"]["expected"] == "1398"
    assert integrity["positions"]["sim-stock"]["quantity"] == "70"
    assert call(adapter, "financial_reconcile", {
        "opening_id": "opening", "closing_id": "closing", "as_of": AS_OF,
    }) == report
    assert _files_hash(output / ".local") == before


def test_public_financial_events_retry_immutably_and_conflicts_preserve_originals(finance):
    adapter, output = finance
    facts = seed(adapter)
    before = _files_hash(output / ".local")
    for method, params in facts:
        call(adapter, method, {**params, "confirmed": True})
        # Retrying without recorded_at also resolves the original recording time.
        key, row = next(iter(params.items()))
        omitted_time = {k: v for k, v in row.items() if k != "recorded_at"}
        call(adapter, method, {key: omitted_time, "confirmed": True})
    assert _files_hash(output / ".local") == before
    for method, params in facts:
        changed = deepcopy(params)
        key = next(iter(changed))
        if method == "execution_save":
            changed[key]["quantity"] += 1
        elif method == "statement_save":
            changed[key]["cash"][0]["balance"] = "9999"
        else:
            changed[key]["amount"] = "51"
        result = adapter.handle_request({"method": method, "params": {**changed, "confirmed": True}})
        assert result["success"] is False
        assert _files_hash(output / ".local") == before


@pytest.mark.parametrize("directory", ["executions", "cash_movements", "account_statements"])
def test_corrupt_ledger_cannot_produce_matched_reconciliation(finance, directory):
    adapter, output = finance
    seed(adapter)
    (output / ".local" / directory / "broken.json").write_text("{broken", encoding="utf-8")
    before = _files_hash(output / ".local")
    report = call(adapter, "financial_reconcile", {"opening_id": "opening", "closing_id": "closing", "as_of": AS_OF})
    integrity = call(adapter, "financial_integrity", {"as_of": AS_OF})
    assert report["status"] != "matched" and integrity["status"] != "matched"
    assert report["record_read_errors"]
    assert "financial_record_read_errors" in integrity["gaps"]
    assert _files_hash(output / ".local") == before


@pytest.mark.parametrize("kind,reason", [
    ("quantity", "asset_memory_quantity_mismatch"), ("cash", "asset_memory_cash_mismatch"),
])
def test_current_asset_facts_must_match_independent_closing_statement(finance, kind, reason):
    adapter, output = finance
    seed(adapter)
    doc = fixture_document()
    if kind == "quantity":
        doc["positions"][0]["holding"]["quantity"] = 71
    else:
        doc["positions"][1]["valuation_input"]["manual_amount"] = 1400
    path = output / ".local" / "financial_assets.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    before = path.read_bytes()
    integrity = call(adapter, "financial_integrity", {"as_of": AS_OF})
    assert integrity["status"] != "matched"
    assert any(reason in gap for gap in integrity["gaps"])
    assert path.read_bytes() == before
    # Historical statement arithmetic still matches; the difference is current memory.
    historical = call(adapter, "financial_reconcile", {"opening_id": "opening", "closing_id": "closing", "as_of": AS_OF})
    assert historical["status"] == "matched"


def test_later_recorded_fill_expires_current_reconciliation(finance):
    adapter, _ = finance
    seed(adapter)
    later = {**sample_facts()[2][0], "id": "fill-after-closing", "quantity": 1,
             "executed_at": "2026-10-11T00:00:00Z", "settled_at": "2026-10-12T00:00:00Z",
             "recorded_at": "2026-10-13T00:00:00Z"}
    call(adapter, "execution_save", {"execution": later, "confirmed": True})
    integrity = call(adapter, "financial_integrity", {"as_of": AS_OF})
    assert integrity["status"] != "matched"
    assert any("events_after_latest_statement" in gap for gap in integrity["gaps"])


@pytest.mark.parametrize("kind", ["statement", "execution", "movement"])
def test_unknown_account_cannot_be_confirmed_into_ledger(finance, kind):
    adapter, output = finance
    opening, _, fills, movement, _ = sample_facts()
    method, payload = {"statement": ("statement_save", opening),
                       "execution": ("execution_save", fills[0]),
                       "movement": ("cash_movement_save", movement)}[kind]
    before = _files_hash(output / ".local")
    result = adapter.handle_request({"method": method, "params": {
        kind: {**payload, "account_id": "unknown-account", "recorded_at": RECORDED}, "confirmed": True,
    }})
    assert result["success"] is False and "account" in result["error"]
    assert _files_hash(output / ".local") == before


@pytest.mark.parametrize("kind", ["execution", "movement", "statement", "position"])
def test_unknown_account_already_in_inputs_is_a_visible_integrity_gap(finance, kind):
    adapter, output = finance
    seed(adapter)
    if kind == "position":
        doc = fixture_document()
        extra = deepcopy(doc["positions"][0])
        extra.update(position_id="unknown-position", account_id="unknown-account")
        doc["positions"].append(extra)
        (output / ".local" / "financial_assets.json").write_text(json.dumps(doc), encoding="utf-8")
    else:
        if kind == "execution":
            row = call(adapter, "execution_list")[0]
            directory = "executions"
        elif kind == "movement":
            row = call(adapter, "cash_movement_list")["records"][0]
            directory = "cash_movements"
        else:
            row = call(adapter, "statement_list")["records"][0]
            directory = "account_statements"
        row = {**row, "id": "unknown-account-record", "account_id": "unknown-account"}
        (output / ".local" / directory / "unknown-account.json").write_text(json.dumps(row), encoding="utf-8")
    integrity = call(adapter, "financial_integrity", {"as_of": AS_OF})
    assert integrity["status"] != "matched", "orphan account facts must never be silently excluded"
    assert any("account" in gap for gap in integrity["gaps"])


def test_partial_mandate_update_keeps_previous_confirmed_goals_and_supports_clearing(finance):
    adapter, _ = finance
    _, _, _, _, mandate = sample_facts()
    call(adapter, "profile_update", {"profile": {"mandate": mandate}, "confirmed": True})
    call(adapter, "profile_update", {"profile": {"mandate": {"operation_frequency": "每月一次"}}, "confirmed": True})
    result = call(adapter, "personal_mandate")
    assert result["fields"]["goals"]["value"] == mandate["goals"]
    assert result["fields"]["cash_needs"]["value"] == mandate["cash_needs"]
    assert result["fields"]["operation_frequency"]["value"] == "每月一次"
    call(adapter, "profile_update", {"profile": {"mandate": {"horizon": None}}, "confirmed": True})
    result = call(adapter, "personal_mandate")
    assert result["fields"]["horizon"]["value"] is None
    assert result["fields"]["horizon"]["status"] == "unknown"
    assert result["fields"]["goals"]["status"] == "confirmed"


@pytest.mark.parametrize("constraints", [None, [], {"现金": {"min": 0.9, "max": 0.2}},
                                         {"现金": {"min": 0, "max": float("nan")}}])
def test_invalid_confirmed_constraints_leave_memory_unchanged(finance, constraints):
    adapter, output = finance
    call(adapter, "constraints_replace", {"constraints": {"现金": {"min": 0.1, "max": 0.3}}, "confirmed": True})
    before = _files_hash(output / ".local")
    result = adapter.handle_request({"method": "constraints_replace", "params": {
        "constraints": constraints, "confirmed": True,
    }})
    assert result["success"] is False
    assert _files_hash(output / ".local") == before


def test_confirmed_constraint_replacement_is_reflected_in_mandate_snapshot(finance):
    adapter, _ = finance
    constraints = {"现金": {"min": 0.1, "max": 0.3}}
    call(adapter, "constraints_replace", {"constraints": constraints, "confirmed": True})
    mandate = call(adapter, "personal_mandate")
    assert mandate["constraint_snapshot"]["value"] == constraints
    assert mandate["constraint_snapshot"]["status"] == "confirmed"
    assert mandate["sources"]["constraints"]["kind"] == "local_confirmed"
    call(adapter, "constraints_replace", {"constraints": {}, "confirmed": True})
    cleared = call(adapter, "personal_mandate")
    assert cleared["constraint_snapshot"]["status"] == "confirmed"
    assert cleared["constraint_snapshot"]["value"] == {}
    assert cleared["content_hash"] != mandate["content_hash"]


def test_duplicate_position_id_cannot_overwrite_a_trusted_position_mapping(finance):
    adapter, output = finance
    doc = fixture_document()
    stock = doc["positions"][0]
    stock["position_id"] = "duplicate-position"
    stock["holding"]["quantity"] = 10
    second = deepcopy(stock)
    second["instrument"]["instrument_key"] = "a:510300"
    doc["positions"].append(second)
    doc["positions"][1]["valuation_input"]["manual_amount"] = 1000
    (output / ".local" / "financial_assets.json").write_text(json.dumps(doc), encoding="utf-8")
    opening, closing, *_ = sample_facts()
    for statement in (opening, closing):
        statement["cash"] = [{"currency": "CNY", "balance": "1000", "available_balance": "1000"}]
        statement["positions"] = [{"instrument_id": symbol, "unit": "share", "quantity": "10",
                                   "available_quantity": "10"} for symbol in ("a:518880", "a:510300")]
        call(adapter, "statement_save", {"statement": {**statement, "recorded_at": RECORDED}, "confirmed": True})
    historical = call(adapter, "financial_reconcile", {"opening_id": "opening", "closing_id": "closing", "as_of": AS_OF})
    assert historical["status"] == "matched"
    integrity = call(adapter, "financial_integrity", {"as_of": AS_OF})
    assert integrity["status"] == "incomplete"
    assert any("position" in gap and "duplicate" in gap for gap in integrity["gaps"])
    assert "duplicate-position" not in integrity["positions"]


@pytest.mark.parametrize("relative", [".local/financial_assets.json", ".secret/openai-key.md",
                                     "stocks/data/financial_assets.json", "stocks/config/investor_profile.json"])
def test_test_environment_rejects_actual_private_paths(relative):
    repo = Path(__file__).resolve().parents[2]
    with pytest.raises(AssertionError, match="real private data"):
        (repo / relative).read_text()
    with pytest.raises(AssertionError, match="real private data"):
        (repo / relative).write_text("must never reach the file system")


def test_test_environment_blocks_external_transports_and_allows_loopback():
    with pytest.raises(AssertionError, match="external urlopen"):
        urllib.request.urlopen("https://example.invalid/test")
    with pytest.raises(AssertionError, match="external curl"):
        subprocess.run(["curl", "https://example.invalid/test"], check=True)
    with socket.socket() as client:
        with pytest.raises(AssertionError, match="external socket"):
            client.connect(("192.0.2.1", 443))
    with socket.socket() as listener, socket.socket() as client:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client.connect(listener.getsockname())
        connection, _ = listener.accept()
        connection.close()


@pytest.mark.asyncio
async def test_build_context_uses_configured_directory_and_real_personal_facts(finance, monkeypatch):
    adapter, output = finance
    seed(adapter)
    repo = Path(__file__).resolve().parents[2]
    protected = (repo / ".local", repo / ".secret")
    original_builtin, original_io = builtins.open, io.open

    def guarded(original):
        def open_guard(file, *args, **kwargs):
            if isinstance(file, (str, bytes, Path)):
                path = Path(file).resolve()
                assert not any(path == base or base in path.parents for base in protected), path
            return original(file, *args, **kwargs)
        return open_guard

    monkeypatch.setattr(builtins, "open", guarded(original_builtin))
    monkeypatch.setattr(io, "open", guarded(original_io))
    before = _files_hash(output / ".local")
    context = await adapter.engine.build_context(include_news=False, include_quotes=False, include_history=False)
    assert context.personal_mandate["fields"]["goals"]["status"] == "confirmed"
    assert context.financial_integrity["status"] == "matched"
    stock = next(row for row in context.position_valuations if row["position_id"] == "sim-stock")
    assert stock["quantity"] == stock["holding"]["quantity"] == 70
    assert stock["holding"]["unit"] == "share"
    persisted = json.loads((output / ".local" / "scheduled_runs" / "latest" / "context.json").read_text())
    assert persisted["personal_mandate"] == context.personal_mandate
    assert persisted["financial_integrity"] == context.financial_integrity
    after = _files_hash(output / ".local")
    for path, value in before.items():
        assert after[path] == value, f"context construction changed confirmed fact {path}"
