"""Replay real rule recommendations and explicitly simulated user facts.

Public historical prices are real; the portfolio, decisions, and fills are
fixtures, never claims about a real user's account or broker executions.
Run with a new/empty --output-dir. No production data is read or changed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.smoke_evaluation_pipeline import _prepare_output, _read_report, _write_json
from stocks.domain.models import Instrument
from stocks.engine.config_loader import load_engine_config
from stocks.engine.data_quality_gate import compute_action_eligible, detect_price_anomalies
from stocks.engine.entry_snapshot import entry_quote_from_observation
from stocks.engine.exchange_targets import target_after
from stocks.engine.history_provider import TencentKLineProvider
from stocks.engine.indicators import TechnicalIndicators
from stocks.engine.portfolio_adjudicator import adjudicate_portfolio
from stocks.engine.scheduled_analysis import _build_action_cards, _build_adjudicator_evidences
from stocks.engine.target_observation import observe


def _fixture_position(prefix, instrument, *, quote=None, quantity=1000):
    """No invented signal or quality flag: derive technical evidence from history."""
    price = float(prefix.iloc[-1]["price"])
    if quote is not None and not math.isclose(price, quote["price"], rel_tol=1e-6):
        raise ValueError("historical technical close differs from the raw entry quote")
    cost = float(prefix.iloc[-60]["price"])
    indicators = TechnicalIndicators.calculate(prefix)
    anomalies = detect_price_anomalies(prefix, current_price=price, ma20=indicators["ma_20"])
    eligible, reasons = compute_action_eligible(anomalies)
    return {
        "position_id": f"simulated_a_{instrument.code}",
        "instrument_key": f"a:{instrument.code}",
        "display_name": f"SIMULATED holding {instrument.code}",
        "account_id": "simulated_brokerage",
        "account": {"account_id": "simulated_brokerage", "type": "broker",
                    "institution_type": "brokerage"},
        "classification": {
            "product_type": instrument.category if instrument.category in {
                "stock", "exchange_traded_fund"} else "stock",
            "exposure_tags": ["a_share"],
        },
        "holding": {"quantity": quantity, "unit": "share"},
        "liquidity": {"tier": "t1", "tradable": True, "rebalance_eligible": True},
        "valuation_method": "quote", "currency": "CNY",
        "price": price, "cost_amount": cost, "market_value_cny": quantity * price,
        "pnl_pct": (price / cost - 1) * 100,
        "one_day_change_pct": (price / float(prefix.iloc[-2]["price"]) - 1) * 100,
        # The fixture models a holding, not a complete account. Unknown portfolio
        # weight also avoids the legacy USD risk conversion for CNY instruments.
        "portfolio_weight": None, "horizon": "long", "indicators": indicators,
        "evidence": {"price_freshness": "current", "data_anomalies": anomalies,
                     "action_eligible": eligible, "action_ineligible_reasons": reasons,
                     "freshness_context": "historical_replay",
                     "entry_quote": deepcopy(quote)},
        "scenario_origin": "simulated_portfolio",
        "cost_basis_origin": "60th_last_historical_close_not_actual_cost",
    }


def _produce_decision(prefix, instrument, cutoff, *, quote=None):
    position = _fixture_position(prefix, instrument, quote=quote)
    cards = _build_action_cards([position])
    run_id = f"simulated-replay-{instrument.code}-{cutoff.isoformat()}"
    decision = adjudicate_portfolio(
        cards, _build_adjudicator_evidences([position]), {}, {},
        {"immediate_cash_cny": position["market_value_cny"] * 10},
        run_id=run_id, execution_rules=load_engine_config()["execution_rules"],
    ).to_dict()
    return decision, position, cards, run_id


def _select_recommendation(frame, instrument, *, now, replay_days, sessions):
    """Select by rule output and completed horizon, never by future returns."""
    ordered = frame.sort_values("timestamp").reset_index(drop=True)
    attempts = []
    for offset in range(min(replay_days, len(ordered) - 59)):
        prefix = ordered.iloc[: len(ordered) - offset].copy()
        day = prefix.iloc[-1]["timestamp"].to_pydatetime().date()
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        entry_target = target_after(start, "a", 1, basis="sessions")
        if entry_target["session"] != day.isoformat():
            continue
        cutoff = datetime.fromisoformat(entry_target["target_at"])
        exit_target = target_after(cutoff, "a", sessions, basis="sessions")
        if (datetime.fromisoformat(exit_target["target_at"]) >= now
                or exit_target["session"] >= now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()):
            continue
        decision, position, cards, run_id = _produce_decision(prefix, instrument, cutoff)
        approved = decision.get("approved_actions", [])
        attempts.append({"cutoff": cutoff.isoformat(), "signals": [c["signal"] for c in cards],
                         "execution_statuses": [a["execution_status"] for a in approved]})
        if len(approved) == 1 and approved[0].get("execution_status") in {"full", "adjusted_to_step"}:
            if (approved[0].get("executable_quantity") or 0) >= 2:
                return prefix, cutoff, entry_target, decision, position, cards, run_id, attempts
    raise RuntimeError("No naturally executable rule recommendation; no action was fabricated. "
                       + json.dumps(attempts, ensure_ascii=False))


async def _replay_inputs(args, output):
    instrument = Instrument(args.symbol, args.symbol, "a", category=args.product_type)
    frame = await TencentKLineProvider().fetch(instrument, lookback_days=args.lookback_days)
    if frame.empty:
        raise RuntimeError("public Tencent history unavailable")
    now = datetime.now(timezone.utc)
    prefix, cutoff, target, _, _, _, _, attempts = _select_recommendation(
        frame, instrument, now=now, replay_days=args.replay_days, sessions=args.sessions)
    entry = observe(f"a:{args.symbol}", target, clock=now)
    quote = entry_quote_from_observation(entry, replay_as_of=cutoff)
    decision, position, cards, run_id = _produce_decision(prefix, instrument, cutoff, quote=quote)
    if len(decision["approved_actions"]) != 1:
        raise RuntimeError("production adjudicator did not preserve one approved recommendation")
    prefix.to_json(output / "history-input.json", orient="records", date_format="iso")
    _write_json(output / "candidate-search.json", attempts)
    _write_json(output / "entry-observation.json", entry)
    _write_json(output / "position-fixture.json", position)
    _write_json(output / "action-cards.json", cards)
    _write_json(output / "portfolio-decision.json", decision)
    return decision, position, quote, cutoff, run_id, now


def _isolated_adapter(output):
    """Use normal production construction with explicit paths and no providers."""
    from stocks.adapters.mcp import MCPAdapter
    from stocks.engine import StocksEngine
    from stocks.engine.config_loader import DEFAULT_ENGINE_CONFIG

    config = deepcopy(DEFAULT_ENGINE_CONFIG)
    for key, value in config["providers"].items():
        if key != "fallback":
            value["enabled"] = False
    for name in ("cache", "macro", "calendar", "filings"):
        config.setdefault(name, {})["enabled"] = False
    config["cache"]["save_to_file"] = True
    config["news"]["watchlist_templates_enabled"] = False
    config["llm"]["analysis_enabled"] = False
    config["llm"]["outlook"]["enabled"] = False
    for name in ("config", "data", ".local", ".secret"):
        (output / name).mkdir(exist_ok=True)
    return MCPAdapter(StocksEngine(
        engine_config=config, config_dir=str(output / "config"), data_dir=str(output / "data"),
        local_data_dir=str(output / ".local"), secret_dir=str(output / ".secret"),
        llm_analysis_enabled=False, openai_api_key="unused-smoke-no-llm",
        openai_base_url="https://invalid.example/smoke-no-llm",
    ))


def _files_hash(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def _call(adapter, method, params):
    result = adapter.handle_request({"method": method, "params": params})
    if not result.get("success"):
        raise RuntimeError(f"{method} failed: {result}")
    return result["data"]


def _manual_cashflow(fills):
    total = Decimal("0")
    for fill in fills:
        amount = Decimal(str(fill["quantity"])) * Decimal(str(fill["price"]))
        total += (amount if fill["side"] == "sell" else -amount) - Decimal(str(fill["fee_amount"]))
    return float(total)


def _run_smoke(args, output, decision, position, quote, cutoff, run_id, now):
    from stocks.engine.decision_observation import schedule_decision_observation
    from stocks.engine.evaluation_ledger import EvaluationLedger
    from stocks.engine.observation_pipeline import collect_due
    from stocks.engine.outcome_attribution import save_portfolio_snapshots

    context = {"origin": "historical_replay", "portfolio_origin": "simulated_portfolio",
               "user_decisions_origin": "simulated", "executions_origin": "simulated",
               "replay_created_at": now.isoformat(), "decision_as_of": cutoff.isoformat()}
    paths = save_portfolio_snapshots(
        decision, generated_at=cutoff.isoformat(), run_id=run_id, repo_root=output,
        position_valuations=[position], quotes={position["instrument_key"]: quote}, context=context)
    if len(paths) != 1:
        raise RuntimeError("expected exactly one frozen recommendation")
    snapshot = json.loads(Path(paths[0]).read_text())
    if snapshot.get("schema_version") != 2:
        raise RuntimeError("legacy snapshot cannot represent a confirmed execution")
    did = snapshot["decision_id"]
    aid = "advice:" + did
    adapter = _isolated_adapter(output)
    action = decision["approved_actions"][0]
    side = "buy" if action["signal"] in {"add", "buy", "accumulate"} else "sell"
    decisions = [
        {"id": f"simulated-deferred:{did}", "advice_id": aid, "decision_id": did,
         "status": "deferred", "decided_at": (cutoff + timedelta(seconds=1)).isoformat(),
         "note": "SIMULATED historical replay user decision; not a real user action"},
        {"id": f"simulated-accepted:{did}", "advice_id": aid, "decision_id": did,
         "status": "accepted", "decided_at": (cutoff + timedelta(seconds=2)).isoformat(),
         "note": "SIMULATED historical replay user decision; not a real user action"},
    ]
    total_quantity = int(action["executable_quantity"])
    quantities = [total_quantity // 2, total_quantity - total_quantity // 2]
    fills = []
    for index, quantity in enumerate(quantities, 1):
        target = target_after(cutoff, "a", index, basis="sessions")
        observation = observe(position["instrument_key"], target, clock=now)
        if observation.get("status") != "observed":
            raise RuntimeError(f"historical simulated fill quote missing: {observation}")
        _write_json(output / f"simulated-fill-{index}-price-evidence.json", observation)
        fills.append({
            "schema_version": 2, "id": f"simulated-fill-{index}:{did}", "advice_id": aid,
            "decision_id": did, "user_decision_id": decisions[-1]["id"], "status": "executed",
            "side": side, "quantity": quantity, "price": observation["price"],
            "currency": observation["currency"], "fee_amount": 1.0,
            "fee_currency": observation["currency"], "executed_at": observation["observed_at"],
            "note": "SIMULATED fill at historical close, fixed fixture fee 1 CNY; not broker execution",
        })
    requests = [("advice_save", {"advice": {"source_decision_id": did}})]
    requests += [("decision_save", {"decision": row}) for row in decisions]
    requests += [("execution_save", {"execution": row}) for row in fills]
    before = _files_hash(output / ".local")
    for method, params in requests:
        denied = adapter.handle_request({"method": method, "params": params})
        if denied.get("success") or "confirm" not in str(denied).lower():
            raise RuntimeError(f"unconfirmed {method} was not rejected: {denied}")
    if before != _files_hash(output / ".local"):
        raise RuntimeError("unconfirmed requests changed persistent data")
    pre_fill_review = None
    for index, (method, params) in enumerate(requests):
        _call(adapter, method, {**params, "confirmed": True})
        if index == 2:
            pre_fill_review = _call(adapter, "decision_review", {"decision_id": did})
            before_fills = pre_fill_review["by_decision"][did]
            if before_fills["executions"] or before_fills["execution_status"] != "unrecorded":
                raise RuntimeError("accepting a recommendation was mistaken for execution")
    database = output / "evaluation.sqlite3"
    ledger = EvaluationLedger(database)
    window = {"id": f"simulated-decision-raw-close-{args.sessions}s-v1", "basis": "sessions",
              "horizon": args.sessions, "retry_seconds": 3600}
    key, created = schedule_decision_observation(ledger, snapshot, window)
    worker = collect_due(ledger, now=now, provider=observe)
    frozen = _read_report(database)
    if len(frozen["results"]) != 1 or frozen["results"][0]["status"] != "evaluated":
        _write_json(output / "evaluation-report.json", frozen)
        raise RuntimeError("real historical price evaluation did not complete: " + str(
            [(r.get("status"), r.get("reason")) for r in frozen["results"]]))
    review_as_of = datetime.now(timezone.utc).isoformat()
    params = {"decision_id": did, "as_of": review_as_of, "evaluation_database": str(database)}
    before = _files_hash(output / ".local")
    review = _call(adapter, "decision_review", params)
    if before != _files_hash(output / ".local"):
        raise RuntimeError("decision_review changed financial facts")
    for method, request_params in requests:
        _call(adapter, method, {**request_params, "confirmed": True})
    second_key, second_created = schedule_decision_observation(ledger, snapshot, window)
    second_worker = collect_due(ledger, now=now, provider=observe)
    second_review = _call(adapter, "decision_review", params)
    if before != _files_hash(output / ".local") or review != second_review:
        raise RuntimeError("repeating confirmed facts changed persistence or review")
    if second_key != key or second_created or _read_report(database) != frozen:
        raise RuntimeError("repeated observation scheduling changed frozen evidence")
    row = review["by_decision"][did]
    if len(row["user_decisions"]) != 2 or len(row["executions"]) != 2:
        raise RuntimeError("review did not preserve both decisions and both fills")
    expected = _manual_cashflow(fills)
    actual = row["cashflow_by_currency"].get("CNY", {}).get("cash_flow")
    if not isinstance(actual, (int, float)) or not math.isclose(actual, expected, abs_tol=1e-8):
        raise RuntimeError(f"cashflow differs from independent arithmetic: {actual} != {expected}")
    _write_json(output / "review.json", review)
    _write_json(output / "review-before-fills.json", pre_fill_review)
    _write_json(output / "evaluation-report.json", frozen)
    summary = {
        "status": "ok", "scope": "historical_rule_replay_with_simulated_user_facts",
        "real_user_actions": False, "real_broker_executions": False,
        "provenance": context, "symbol": position["instrument_key"], "decision_id": did,
        "action": action["signal"], "user_decision_count": 2, "fill_count": 2,
        "unconfirmed_writes": 0, "idempotent": True, "manual_net_cashflow_cny": expected,
        "cashflow_is_profit": False, "portfolio_weight_known": False,
        "fee_origin": "simulated_fixed_1_CNY_per_fill", "first_task_created": created,
        "first_worker": worker, "second_worker": second_worker,
        "evaluation_result_id": frozen["results"][0]["result_id"], "output_dir": str(output),
    }
    _write_json(output / "smoke-result.json", summary)
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--symbol", default="518880")
    parser.add_argument("--product-type", choices=["stock", "exchange_traded_fund"],
                        default="exchange_traded_fund")
    parser.add_argument("--lookback-days", type=int, default=150)
    parser.add_argument("--replay-days", type=int, default=20)
    parser.add_argument("--sessions", type=int, default=3)
    args = parser.parse_args()
    if not args.symbol.isdigit() or len(args.symbol) != 6:
        parser.error("--symbol must be a six-digit A-share ticker")
    if not 80 <= args.lookback_days <= 500 or not 1 <= args.replay_days <= args.lookback_days - 60:
        parser.error("require 80..500 history bars and at least 60 bars before each cutoff")
    if args.sessions < 3:
        parser.error("--sessions must be at least 3, after both simulated daily fills")
    output = _prepare_output(args.output_dir)
    inputs = asyncio.run(_replay_inputs(args, output))
    _run_smoke(args, output, *inputs)


if __name__ == "__main__":
    main()
