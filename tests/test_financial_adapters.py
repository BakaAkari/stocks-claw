"""CLI confirmation and read-only reconciliation through real engine services."""

import json

from scripts.smoke_decision_review import _files_hash, _isolated_adapter
from scripts.smoke_financial_reconciliation import fixture_document, sample_facts
from stocks.adapters.cli import CLIAdapter
from stocks.engine.confirmation_provenance import ConfirmationStore


def _cli(tmp_path):
    adapter = _isolated_adapter(tmp_path)
    doc = fixture_document()
    path = tmp_path / ".local/financial_assets.json"
    path.write_text(json.dumps(doc))
    ConfirmationStore(tmp_path / ".local").record(
        "financial_assets", doc, channel="cli", source_path=str(path)
    )
    return CLIAdapter(adapter.engine)


def _commands():
    opening, closing, fills, movement, mandate = sample_facts()
    return [
        ("--profile-update", {"mandate": mandate}),
        ("--constraints-replace", {}),
        ("--statement-save", opening),
        *[("--execution-save", fill) for fill in fills],
        ("--cash-movement-save", movement),
        ("--statement-save", closing),
    ]


def test_cli_financial_writes_require_confirmation_without_side_effects(tmp_path, capsys):
    cli = _cli(tmp_path)
    before = _files_hash(tmp_path)
    for flag, record in _commands():
        cli.run([flag, json.dumps(record)])
        result = json.loads(capsys.readouterr().out)
        assert result == {"success": False, "error": "Memory writes require --confirmed"}
    assert _files_hash(tmp_path) == before


def test_cli_confirmed_facts_reconcile_and_reads_preserve_memory(tmp_path, capsys):
    cli = _cli(tmp_path)
    for flag, record in _commands():
        cli.run([flag, json.dumps(record), "--confirmed"])
        assert json.loads(capsys.readouterr().out)["success"]
    before = _files_hash(tmp_path)
    cli.run(["--financial-reconcile", "opening", "closing"])
    report = json.loads(capsys.readouterr().out)["data"]
    assert report["status"] == "matched"
    assert report["cash_by_currency"]["CNY"]["expected"] == "1398"
    assert report["positions_by_instrument"]["a:518880"]["share"]["expected"] == "70"
    for flag in ("--financial-integrity", "--personal-mandate", "--statement-list", "--cash-movement-list"):
        cli.run([flag])
        assert json.loads(capsys.readouterr().out)["success"]
    assert _files_hash(tmp_path) == before
