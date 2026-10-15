"""Confirmed public adapters preserve a linked, append-only decision history."""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from scripts.smoke_decision_review import _files_hash, _isolated_adapter
from stocks.adapters.cli import CLIAdapter
from stocks.engine.advice_review import attach_execution_review
from stocks.engine.context_builder import ContextBuilder
from stocks.engine.outcome_attribution import save_portfolio_snapshots


@pytest.fixture
def lifecycle(tmp_path):
    adapter = _isolated_adapter(tmp_path)
    decision = {"rule_version": "test-rules-v1", "decision_id": "portfolio-id",
                "approved_actions": [{"decision_id": "opaque-action-hash", "position_id": "p1",
                                      "signal": "reduce", "ratio": 0.3, "executable_quantity": 30,
                                      "action_description": "减仓三成，余仓继续观察", "reason": "测试规则"}]}
    quote = {"instrument": {"market": "a", "code": "518880"}, "price": 10,
             "source": "fixture", "as_of": "2026-09-01T07:00:00Z", "stale": False,
             "currency": "CNY", "price_type": "close", "price_basis": "raw_quoted_price",
             "adjustment": "unadjusted"}
    save_portfolio_snapshots(
        decision, generated_at="2026-09-01T07:00:00Z", run_id="actual-run", repo_root=tmp_path,
        position_valuations=[{"position_id": "p1", "instrument_key": "a:518880"}],
        quotes={"a:518880": quote}, report_view={"headline": "减仓建议"})
    did = "opaque-action-hash"
    aid = "advice:" + did
    choice = {"id": "choice-1", "advice_id": aid, "decision_id": did, "status": "partial",
              "decided_at": "2026-09-02T07:00:00Z", "note": "先卖一部分"}
    fill = {"schema_version": 2, "id": "fill-1", "advice_id": aid, "decision_id": did,
            "user_decision_id": "choice-1", "status": "executed", "side": "sell",
            "quantity": 10, "price": 11, "currency": "CNY", "fee_amount": 1,
            "fee_currency": "CNY", "executed_at": "2026-09-02T08:00:00Z"}
    return adapter, did, choice, fill


def call(adapter, method, **params):
    return adapter.handle_request({"method": method, "params": params})


def save_advice(adapter, did):
    result = call(adapter, "advice_save", advice={"source_decision_id": did}, confirmed=True)
    assert result["success"], result
    return result["data"]


def test_cli_and_mcp_confirm_gates_and_readonly_review(lifecycle, tmp_path, capsys):
    adapter, did, choice, fill = lifecycle
    before = _files_hash(tmp_path / ".local")
    for method, field, payload in (("advice_save", "advice", {"source_decision_id": did}),
                                   ("decision_save", "decision", choice),
                                   ("execution_save", "execution", fill)):
        result = call(adapter, method, **{field: payload})
        assert not result["success"]
        CLIAdapter(adapter.engine).run(["--" + method.replace("_", "-"), json.dumps(payload)])
        assert not json.loads(capsys.readouterr().out)["success"]
    assert _files_hash(tmp_path / ".local") == before
    CLIAdapter(adapter.engine).run(["--advice-save", json.dumps({"source_decision_id": did}), "--confirmed"])
    assert json.loads(capsys.readouterr().out)["success"]
    CLIAdapter(adapter.engine).run(["--decision-save", json.dumps(choice), "--confirmed"])
    assert json.loads(capsys.readouterr().out)["success"]
    CLIAdapter(adapter.engine).run(["--execution-save", json.dumps(fill), "--confirmed"])
    assert json.loads(capsys.readouterr().out)["success"]
    before = _files_hash(tmp_path / ".local")
    CLIAdapter(adapter.engine).run(["--decision-review", did])
    row = json.loads(capsys.readouterr().out)["data"]["by_decision"][did]
    assert len(row["executions"]) == 1
    assert row["cashflow_by_currency"]["CNY"]["cash_flow"] == 109
    assert _files_hash(tmp_path / ".local") == before


def test_latest_rejection_does_not_erase_partial_fills_or_prompt_history(lifecycle):
    adapter, did, choice, fill = lifecycle
    save_advice(adapter, did)
    assert call(adapter, "decision_save", decision=choice, confirmed=True)["success"]
    for payload in (fill, dict(fill, id="fill-2", quantity=5)):
        assert call(adapter, "execution_save", execution=payload, confirmed=True)["success"]
    rejected = dict(choice, id="choice-2", status="rejected", decided_at="2026-09-03T07:00:00Z",
                    note="不再卖出剩余部分")
    assert call(adapter, "decision_save", decision=rejected, confirmed=True)["success"]
    engine = adapter.engine
    row = engine.decision_review(did)["by_decision"][did]
    assert row["latest_user_decision"]["status"] == "rejected"
    assert row["execution_status"] == "partial"
    assert row["cashflow_by_currency"]["CNY"]["cash_flow"] == 163
    assert len(engine.find_executions_by_run_id("actual-run")) == 2
    assert not engine.find_executions_by_run_id("opaque")
    reviewed = attach_execution_review(engine.list_advice(), engine.list_executions(),
                                       user_decisions=engine.list_user_decisions())
    lines = []
    ContextBuilder._append_review_executions(None, lines, reviewed)
    prompt = "\n".join(lines)
    assert "先卖一部分" in prompt and "不再卖出剩余部分" in prompt
    assert "fill-1" in prompt and "fill-2" in prompt
    now = datetime.now(timezone.utc).isoformat()
    assert engine.decision_attribution_settle(now=now) == engine.decision_review(as_of=now)


@pytest.mark.parametrize("change,error", [
    ({"advice_id": "another"}, "record_reference"),
    ({"decision_id": "another"}, "advice_decision"),
    ({"target": "us:518880"}, "target_mismatch"),
    ({"source_run_id": "wrong"}, "run_mismatch"),
    ({"currency": "USD"}, "currency_mismatch"),
    ({"executed_at": "2026-08-31T07:00:00Z"}, "precedes_recommendation"),
    ({"executed_at": "2026-09-01T08:00:00Z"}, "precedes_linked_user_decision"),
    ({"user_decision_id": "unknown"}, "record_reference"),
    ({"quantity": float("nan")}, "finite"),
    ({"id": ""}, "event_id_required"),
])
def test_invalid_link_or_fact_cannot_change_persistence(lifecycle, tmp_path, change, error):
    adapter, did, choice, fill = lifecycle
    save_advice(adapter, did)
    assert call(adapter, "decision_save", decision=choice, confirmed=True)["success"]
    before = _files_hash(tmp_path / ".local")
    result = call(adapter, "execution_save", execution=dict(fill, **change), confirmed=True)
    assert not result["success"] and error in result["error"], result
    assert _files_hash(tmp_path / ".local") == before


def test_new_advice_cannot_downgrade_to_legacy_execution(lifecycle):
    adapter, did, _, fill = lifecycle
    save_advice(adapter, did)
    legacy = {k: fill[k] for k in ("id", "advice_id", "decision_id", "status", "price")}
    legacy.update(action="reduce", target="a:518880", executed_ratio=0.3)
    result = call(adapter, "execution_save", execution=legacy, confirmed=True)
    assert not result["success"] and "requires_schema_version_2" in result["error"]
    assert not adapter.engine.list_executions()


def test_fill_without_recorded_choice_keeps_gap_and_unknown_fees(lifecycle):
    adapter, did, _, fill = lifecycle
    save_advice(adapter, did)
    payload = {k: v for k, v in fill.items() if k not in {"user_decision_id", "fee_amount", "fee_currency"}}
    assert call(adapter, "execution_save", execution=payload, confirmed=True)["success"]
    row = adapter.engine.decision_review(did)["by_decision"][did]
    assert row["user_decisions"] == []
    assert "execution_user_decision_unrecorded" in row["gaps"]
    assert row["cashflows"][0]["gross_cash_flow"] == 110
    assert row["cashflows"][0]["cash_flow"] is None


def test_ids_are_idempotent_and_conflicting_content_is_rejected(lifecycle, tmp_path):
    adapter, did, choice, fill = lifecycle
    save_advice(adapter, did)
    for method, key, payload in (("decision_save", "decision", choice), ("execution_save", "execution", fill)):
        first = call(adapter, method, **{key: payload, "confirmed": True})
        assert first["success"], first
        before = _files_hash(tmp_path / ".local")
        assert call(adapter, method, **{key: payload, "confirmed": True}) == first
        conflict = call(adapter, method, **{key: dict(payload, note="changed"), "confirmed": True})
        assert not conflict["success"]
        assert _files_hash(tmp_path / ".local") == before


def test_scheduled_producer_and_reader_share_explicit_financial_directory(tmp_path):
    from stocks.engine.scheduled_analysis import ScheduledAnalysisRunner
    from tests.engine.test_advisory_lineage import (
        EXECUTION_RULES,
        NOW,
        InjectedClient,
        context_fixture,
    )
    from tests.engine.test_scheduled_analysis import _engine_with_enough_for_outlook
    engine, config = _engine_with_enough_for_outlook(tmp_path, context_fixture().to_dict())
    config["llm"]["advisory_mainline"]["enabled"] = True
    config["execution_rules"] = EXECUTION_RULES
    async def build_confirmed_context(**kwargs):
        return context_fixture()
    engine.build_context = build_confirmed_context
    engine._local_data_dir = tmp_path / "custom-financial-dir"
    runner = ScheduledAnalysisRunner(engine, config=config, artifact_dir=config["artifact_dir"],
                                     advisory_client=InjectedClient())
    result = asyncio.run(runner.run_session(
        "cn_after_close", now=datetime.fromisoformat(NOW)))
    from pathlib import Path
    artifact = json.loads(Path(result["paths"]["json_path"]).read_text())
    files = list((engine._local_data_dir / "decisions").glob("*.json"))
    assert files
    snapshot = json.loads(files[0].read_text())
    assert snapshot["source_run_id"] == artifact["run_id"]
    assert snapshot["report_view"] == artifact["portfolio_decision"]["user_view"]
    assert not (tmp_path / ".local" / "decisions").exists()


def test_manual_observation_command_reuses_only_frozen_tasks(lifecycle, tmp_path, monkeypatch, capsys):
    import sys

    from scripts import collect_target_observations as command

    adapter, did, _, _ = lifecycle
    # Scheduling is tested separately from the network worker.
    windows = tmp_path / "windows.json"
    windows.write_text(json.dumps({"a": [{"id": "manual-1000h", "basis": "elapsed",
                                           "horizon": 1000, "retry_seconds": 3600}]}))
    database = tmp_path / "review.sqlite3"
    monkeypatch.setattr(sys, "argv", ["collect_target_observations.py", "--database", str(database),
                                    "--recommendations", str(tmp_path / ".local" / "decisions"),
                                    "--decision-id", did, "--windows", str(windows)])
    monkeypatch.setattr(command, "collect_due", lambda ledger: {"claimed": 0})
    command.main()
    assert json.loads(capsys.readouterr().out)["sync"]["created"] == 1
    command.main()
    assert json.loads(capsys.readouterr().out)["sync"]["existing"] == 1
    from stocks.engine.evaluation_ledger import EvaluationLedger
    ledger = EvaluationLedger(database)
    assert len(ledger.keys()) == 1
    definition = ledger.definition(*ledger.keys()[0][:2])
    assert definition["signal"]["provenance"]["decision_id"] == did
    assert definition["signal"]["provenance"]["source_snapshot_hash"] == adapter.engine.decision_attribution()[0]["source_hash"]


@pytest.mark.parametrize("as_of", [123, "", "2026-09-01T07:00:00", "invalid"])
def test_review_rejects_invalid_or_ambiguous_cutoff(lifecycle, as_of):
    adapter, did, _, _ = lifecycle
    result = call(adapter, "decision_review", decision_id=did, as_of=as_of)
    assert result["success"] is False
