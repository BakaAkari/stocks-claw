"""Fact review: recommendation != user decision != fill != investment return."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime

import pytest

from stocks.domain.models import AdviceRecord, ExecutionRecord, UserDecisionRecord
from stocks.engine.entry_snapshot import entry_quote_from_observation
from stocks.engine.evaluation_contract import digest
from stocks.engine.exchange_targets import target_after
from stocks.engine.outcome_attribution import (
    build_decision_review,
    load_decision_snapshots,
    run_settlement,
    save_decision_snapshot,
    save_portfolio_snapshots,
    settle_decisions,
)
from stocks.engine.portfolio_adjudicator import PortfolioAction, PortfolioDecision
from stocks.engine.target_observation import observe

GENERATED = "2026-10-08T07:00:59.999000+00:00"
AS_OF = "2026-10-10T00:00:00+00:00"
SYMBOL = "crypto:BTCUSDT"


def observation(target, price=100):
    start = int(datetime.fromisoformat(target["target_at"]).timestamp() // 60) * 60000
    payload = [[start, "100", "120", "90", str(price), "1.5", start + 59999]]
    return observe(SYMBOL, target, clock=datetime.fromisoformat(AS_OF), fetch=lambda _: payload)


def proposal():
    action = PortfolioAction(
        position_id="wallet-local-position", signal="add", action_description="Add in stages",
        ratio=0.1, decision_id="d1", reason="synthetic rule fixture", executable_quantity=10,
    )
    return PortfolioDecision(status="approved", decision_id="portfolio1", approved_actions=[action]).to_dict()


@pytest.fixture
def snapshot(tmp_path):
    target = target_after(datetime.fromisoformat("2026-10-08T06:00:00+00:00"), "crypto", 1)
    quote = entry_quote_from_observation(observation(target), replay_as_of=GENERATED)
    paths = save_portfolio_snapshots(
        proposal(), generated_at=GENERATED, snaps_dir=tmp_path / "decisions", run_id="run1",
        position_valuations=[{"position_id": "wallet-local-position", "instrument_key": SYMBOL,
                              "quantity": 20, "account_id": "simulated"}],
        quotes={SYMBOL: quote}, report_view={"text": "Actual generated report structure"},
        context={"provenance": "synthetic_test"},
    )
    return json.loads(paths[0].read_text())


def advice(snapshot):
    return AdviceRecord.create(
        id="a1", created_at="2026-10-08T08:00:00+00:00", source_run_id="run1",
        source_snapshot=snapshot, original_text="Saved by a simulated user",
        instruments=[{"market": "crypto", "code": "BTCUSDT", "name": "BTC"}],
        direction={}, rationale_summary="fixture", based_on=[], boundary=[],
        actions=[{"decision_id": "d1", "target": SYMBOL, "action": "add",
                  "size_hint": "staged", "horizon": "unspecified"}],
    ).to_dict()


def decision(**changes):
    values = dict(id="ud1", advice_id="a1", decision_id="d1", status="accepted",
                  decided_at="2026-10-08T09:00:00+00:00", recorded_at="2026-10-08T09:01:00+00:00")
    values.update(changes)
    return UserDecisionRecord.create(**values).to_dict()


def fill(**changes):
    values = dict(id="e1", schema_version=2, decision_id="d1", status="executed",
                  target=SYMBOL, action="add", side="buy", quantity=2, price=100,
                  currency="USDT", fee_amount=1, fee_currency="USDT", advice_id="a1",
                  user_decision_id="ud1", source_run_id="run1",
                  executed_at="2026-10-08T10:00:00+00:00", recorded_at="2026-10-08T11:00:00+00:00")
    values.update(changes)
    return ExecutionRecord.create(**values).to_dict()


def frozen(snapshot, price=110):
    target = target_after(datetime.fromisoformat(GENERATED), "crypto", 24)
    obs = observation(target, price)
    return {"result_id": "res1", "signal_id": "signal1", "policy_id": "p1",
            "target_at": target["target_at"], "target": target, "exit": obs,
            "status": "observed", "reason": None,
            "signal": {"symbol": SYMBOL, "provenance": {
                "decision_id": "d1", "source_snapshot_hash": snapshot["source_hash"]}}}


def review(snapshot, fills=None, decisions=None, results=None, **kwargs):
    return build_decision_review([snapshot], fills or [], results or [], as_of=AS_OF,
                                 advice_records=[advice(snapshot)], user_decisions=decisions or [], **kwargs)


def test_one_snapshot_per_action_full_evidence_no_execution_claim(snapshot, tmp_path):
    assert snapshot["schema_version"] == 2
    assert snapshot["instrument_id"] == SYMBOL
    assert snapshot["entry_snapshot"]["price"] == 100
    assert snapshot["entry_snapshot"]["entry_observation"]["source_payload"]
    assert snapshot["position"]["quantity"] == 20
    assert snapshot["report_view"]["text"]
    assert snapshot["recommendation_status"] == "approved"
    assert not {"executed", "entry_price", "execution_price", "horizon_days"} & snapshot.keys()
    assert snapshot["source_hash"] == digest({k: v for k, v in snapshot.items() if k != "source_hash"})
    original = proposal()
    original["suppressed_actions"] = [dict(original["approved_actions"][0], decision_id="d2")]
    paths = save_portfolio_snapshots(original, generated_at=GENERATED, snaps_dir=tmp_path / "two")
    assert len(paths) == 2
    assert {json.loads(p.read_text())["recommendation_status"] for p in paths} == {"approved", "suppressed"}


def test_instrument_must_come_from_unique_position_metadata(tmp_path):
    value = proposal()
    value["approved_actions"][0]["position_id"] = SYMBOL
    paths = save_portfolio_snapshots(value, generated_at=GENERATED, snaps_dir=tmp_path)
    stored = json.loads(paths[0].read_text())
    assert stored["instrument_id"] is None
    assert stored["entry_snapshot"]["price"] is None
    assert stored["identity_reason"] == "missing_position"


def test_immutable_id_atomic_retries_and_conflict(snapshot, tmp_path):
    with ThreadPoolExecutor(max_workers=6) as executor:
        paths = list(executor.map(lambda _: save_decision_snapshot(snapshot, snaps_dir=tmp_path), range(12)))
    assert len(set(paths)) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1
    changed = deepcopy(snapshot)
    changed["action"]["ratio"] = 0.3
    changed["source_hash"] = digest({k: v for k, v in changed.items() if k != "source_hash"})
    with pytest.raises(ValueError, match="immutable_recommendation_conflict"):
        save_decision_snapshot(changed, snaps_dir=tmp_path)
    assert json.loads(paths[0].read_text()) == snapshot


def test_tampered_source_rejected(snapshot, tmp_path):
    snapshot["report_view"] = {"text": "changed"}
    with pytest.raises(ValueError, match="source_snapshot_hash_mismatch"):
        save_decision_snapshot(snapshot, snaps_dir=tmp_path)
    row = build_decision_review([snapshot], [], [], as_of=AS_OF)["by_decision"]["d1"]
    assert row["snapshot_status"] == "source_hash_mismatch"


def test_system_approval_never_means_user_accepted_or_executed(snapshot):
    row = build_decision_review([snapshot], [], [], as_of=AS_OF)["by_decision"]["d1"]
    assert row["recommendation_status"] == "approved"
    assert row["latest_user_decision"] is None
    assert row["execution_status"] == "unrecorded"
    assert row["cashflow_by_currency"] == {}
    assert "confirmed_advice_unrecorded" in row["gaps"]


@pytest.mark.parametrize("status", ["accepted", "partial", "rejected", "deferred"])
def test_user_decision_is_independent_of_execution(snapshot, status):
    row = review(snapshot, decisions=[decision(status=status)])["by_decision"]["d1"]
    assert row["latest_user_decision"]["status"] == status
    assert row["execution_status"] == "unrecorded"
    assert row["recorded_fill_count"] == 0


def test_two_partial_fills_survive_later_rejection_cashflows_hand_checked(snapshot):
    decisions = [decision(status="partial"), decision(id="ud2", status="rejected",
                 decided_at="2026-10-09T12:00:00+00:00", recorded_at="2026-10-09T12:01:00+00:00")]
    fills = [fill(), fill(id="e2", quantity=3, price=110, fee_amount=2)]
    original = deepcopy((snapshot, decisions, fills))
    report = review(snapshot, fills, decisions)
    row = report["by_decision"]["d1"]
    assert len(row["user_decisions"]) == len(row["executions"]) == 2
    assert row["latest_user_decision"]["status"] == "rejected"
    assert row["execution_status"] == "partial"
    assert [f["cash_flow"] for f in row["cashflows"]] == [-201, -332]
    assert row["cashflow_by_currency"]["USDT"]["gross_cash_flow"] == -530
    assert row["cashflow_by_currency"]["USDT"]["cash_flow"] == -533
    assert row["realized_pnl"]["status"] == row["portfolio_gain"]["status"] == "unavailable"
    assert (snapshot, decisions, fills) == original
    assert report == review(snapshot, fills, decisions)
    assert report["content_hash"] != review(snapshot, fills[:1], decisions)["content_hash"]


def test_fill_without_user_decision_is_preserved_and_gap_explicit(snapshot):
    row = review(snapshot, [fill(user_decision_id=None)])["by_decision"]["d1"]
    assert row["cashflows"][0]["cash_flow"] == -201
    assert row["latest_user_decision"] is None
    assert "execution_user_decision_unrecorded" in row["gaps"]


@pytest.mark.parametrize("fee,currency,reason", [
    (None, None, "fee_unknown"), (1, "BNB", "fee_currency_differs"),
])
def test_unknown_or_foreign_fee_does_not_become_zero(snapshot, fee, currency, reason):
    row = review(snapshot, [fill(fee_amount=fee, fee_currency=currency)])["by_decision"]["d1"]
    flow = row["cashflows"][0]
    assert flow["gross_cash_flow"] == -200
    assert flow["cash_flow"] is None
    assert flow["reason"] == reason
    assert row["cashflow_by_currency"]["USDT"]["complete"] is False


def test_sell_is_cash_inflow_not_profit_and_prices_not_direction_flipped(snapshot):
    row = review(snapshot, [fill(side="sell", action="reduce")], results=[frozen(snapshot)])["by_decision"]["d1"]
    assert row["cashflows"][0]["cash_flow"] == 199
    assert row["cashflows"][0]["price_observations"][0]["raw_price_change"] == pytest.approx(0.1)
    assert row["realized_pnl"]["status"] == "unavailable"
    assert "win_rate" not in str(row)


def test_same_id_retry_not_double_counted_and_content_conflict_rejected(snapshot):
    row = review(snapshot, [fill(), fill()], [decision(), decision()])["by_decision"]["d1"]
    assert row["recorded_fill_count"] == len(row["user_decisions"]) == 1
    with pytest.raises(ValueError, match="conflicting_execution_id:e1"):
        review(snapshot, [fill(), fill(quantity=3)])


@pytest.mark.parametrize("field,value,reason", [
    ("advice_id", "wrong", "execution_advice_link_unverified"),
    ("source_run_id", "wrong", "execution_source_run_mismatch"),
    ("target", "crypto:ETHUSDT", "execution_instrument_mismatch"),
])
def test_wrong_fill_link_retained_but_not_used_for_cashflow(snapshot, field, value, reason):
    row = review(snapshot, [fill(**{field: value})])["by_decision"]["d1"]
    assert len(row["executions"]) == 1
    assert row["cashflows"][0]["reason"] == reason
    assert row["cashflows"][0]["gross_cash_flow"] is None
    assert row["execution_status"] == "unrecorded"


def test_observations_require_exact_snapshot_and_instrument_link(snapshot):
    right = frozen(snapshot)
    wrong_hash = deepcopy(right)
    wrong_hash["result_id"] = "wrong-hash"
    wrong_hash["signal"]["provenance"]["source_snapshot_hash"] = "other"
    wrong_symbol = deepcopy(right)
    wrong_symbol["result_id"] = "wrong-symbol"
    wrong_symbol["signal"]["symbol"] = "crypto:ETHUSDT"
    row = review(snapshot, [fill()], results=[wrong_symbol, right, wrong_hash])["by_decision"]["d1"]
    assert [r["result_id"] for r in row["price_observations"]] == ["res1"]
    assert "observation_source_snapshot_mismatch" in row["gaps"]
    assert "observation_instrument_mismatch" in row["gaps"]
    assert row["cashflows"][0]["price_observations"][0]["raw_price_change"] == pytest.approx(.1)


def test_observation_evidence_tampering_or_pre_fill_price_excluded(snapshot):
    bad = frozen(snapshot)
    bad["exit"]["price"] = 123
    row = review(snapshot, [fill()], results=[bad])["by_decision"]["d1"]
    assert row["price_observations"] == []
    assert any(g.startswith("invalid_observation_evidence:") for g in row["gaps"])
    after = fill(executed_at="2026-10-09T12:00:00+00:00", recorded_at="2026-10-09T13:00:00+00:00")
    row = review(snapshot, [after], results=[frozen(snapshot)])["by_decision"]["d1"]
    assert row["cashflows"][0]["price_observations"] == []


def test_event_time_order_uses_instants_and_as_of_excludes_future(snapshot):
    early = decision(id="early", status="partial", decided_at="2026-10-09T12:00:00+08:00",
                     recorded_at="2026-10-09T13:00:00+08:00")
    late = decision(id="late", status="rejected", decided_at="2026-10-09T08:00:00+00:00",
                    recorded_at="2026-10-09T09:00:00+00:00")
    future = decision(id="future", decided_at="2026-10-11T00:00:00+00:00",
                      recorded_at="2026-10-11T00:00:00+00:00")
    row = review(snapshot, decisions=[late, early, future])["by_decision"]["d1"]
    assert [d["id"] for d in row["user_decisions"]] == ["early", "late"]
    assert row["latest_user_decision"]["status"] == "rejected"


def test_unlinked_user_decisions_preserved_as_unverified(snapshot):
    row = review(snapshot, decisions=[decision(advice_id="other")])["by_decision"]["d1"]
    assert len(row["unlinked_user_decisions"]) == 1
    assert row["latest_user_decision"] is None


def test_legacy_read_only_and_old_settlement_never_produces_returns(tmp_path):
    legacy = {"decision_id": "old", "executed": True, "entry_price": 0, "horizon_days": 5}
    path = tmp_path / "old.json"
    path.write_text(json.dumps(legacy))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="legacy_snapshot_write_disabled"):
        save_decision_snapshot(legacy, snaps_dir=tmp_path)
    snapshots = load_decision_snapshots(snaps_dir=tmp_path)
    result = settle_decisions(AS_OF, {"a:588000": {"2026-10-09": 10}}, snapshots, [])
    assert result["by_decision"]["old"]["snapshot_status"] == "legacy_unverified"
    assert result["by_decision"]["old"]["execution_status"] == "unrecorded"
    assert "total_settled" not in result
    assert path.read_bytes() == before


def test_run_settlement_explicit_paths_read_only_deterministic(snapshot, tmp_path):
    directory = tmp_path / "custom" / "snapshots"
    save_decision_snapshot(snapshot, snaps_dir=directory)
    database = tmp_path / "evaluation.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE evaluation_results (signal_id TEXT, policy_id TEXT, target_at TEXT, payload TEXT)")
        value = frozen(snapshot)
        db.execute("INSERT INTO evaluation_results VALUES (?,?,?,?)",
                   ("s1", "p1", value["target_at"], json.dumps(value)))
    before = {p: p.read_bytes() for p in [database, *directory.glob("*.json")]}
    kwargs = dict(snapshots_dir=directory, evaluation_database=database, as_of=AS_OF,
                  executions=[fill()], advice_records=[advice(snapshot)], user_decisions=[decision()])
    first = run_settlement(**kwargs)
    assert first == run_settlement(**kwargs)
    assert len(first["by_decision"]["d1"]["price_observations"]) == 1
    assert all(p.read_bytes() == value for p, value in before.items())
    assert not (tmp_path / ".local").exists()


def test_opposite_direction_never_counts_as_completed_recommendation(snapshot):
    row = review(snapshot, [fill(side="sell", action="reduce", quantity=10)])["by_decision"]["d1"]
    assert row["execution_status"] == "recorded_fills"
    assert row["cashflows"][0]["cash_flow"] == 999
    assert "execution_direction_mismatch" in row["gaps"]


@pytest.mark.parametrize("signal,side", [("buy", "buy"), ("sell", "sell")])
def test_original_llm_direction_counts_multiple_fills_without_rewriting(snapshot, signal, side):
    snapshot["action"]["signal"] = signal
    snapshot["source_hash"] = digest({k: v for k, v in snapshot.items() if k != "source_hash"})
    fills = [fill(id="e1", side=side, quantity=5), fill(id="e2", side=side, quantity=5)]
    row = review(snapshot, fills)["by_decision"]["d1"]
    assert row["execution_status"] == "full"
    assert row["snapshot"]["action"]["signal"] == signal
    opposite = review(snapshot, [fill(side="sell" if side == "buy" else "buy", quantity=10)])["by_decision"]["d1"]
    assert opposite["execution_status"] == "recorded_fills"
    assert "execution_direction_mismatch" in opposite["gaps"]


@pytest.mark.parametrize("snapshots", [[], [{"decision_id": "d1", "executed": True}]])
def test_orphan_or_legacy_execution_kept_without_crash_or_cashflow(snapshots):
    result = build_decision_review(snapshots, [fill()], [], as_of=AS_OF,
                                   user_decisions=[decision()])
    row = result["by_decision"]["d1"]
    assert len(row["executions"]) == 1
    assert row["cashflows"][0]["reason"] == "execution_recommendation_unverified"
    assert row["execution_status"] == "unrecorded"
    assert len(row["unlinked_user_decisions"]) == 1


def test_cashflows_never_merge_currencies_or_overflow_json(snapshot):
    row = review(snapshot, [fill(), fill(id="usd", currency="USD", fee_currency="USD")])["by_decision"]["d1"]
    assert set(row["cashflow_by_currency"]) == {"USD", "USDT"}
    row = review(snapshot, [fill(price=1e308, quantity=1, fee_amount=0),
                            fill(id="large2", price=1e308, quantity=1, fee_amount=0)])["by_decision"]["d1"]
    assert row["cashflow_by_currency"]["USDT"]["cash_flow"] is None
    assert row["cashflow_by_currency"]["USDT"]["gaps"] == ["nonfinite_currency_total"]


def test_source_tampering_retains_advice_but_cannot_verify_fill(snapshot):
    changed = advice(snapshot)
    changed["source_snapshot"]["action"]["ratio"] = .5
    report = build_decision_review([snapshot], [fill()], [], as_of=AS_OF, advice_records=[changed])
    row = report["by_decision"]["d1"]
    assert row["advice_records"] == []
    assert row["unlinked_advice_records"] == [changed]
    assert row["cashflows"][0]["cash_flow"] is None


def test_as_of_does_not_expose_later_failed_observation(snapshot):
    result = frozen(snapshot)
    result["status"] = "expired"
    result["exit"] = {"status": "expired", "reason": "provider_error",
                      "retrieved_at": "2026-10-11T00:00:00+00:00"}
    row = review(snapshot, results=[result])["by_decision"]["d1"]
    assert row["price_observations"] == []
