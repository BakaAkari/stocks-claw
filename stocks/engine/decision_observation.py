"""Bridge a frozen production recommendation to the single observation chain."""

from stocks.engine.evaluation_contract import digest
from stocks.engine.evaluation_task_sync import schedule_signal


def schedule_decision_observation(ledger, snapshot, window):
    if snapshot.get("schema_version") != 2:
        raise ValueError("versioned_recommendation_required")
    if digest({k: v for k, v in snapshot.items() if k != "source_hash"}) != snapshot.get("source_hash"):
        raise ValueError("recommendation_hash_mismatch")
    signal = {
        "schema_version": 1,
        "signal_id": "recommendation:" + snapshot["decision_id"],
        "symbol": snapshot.get("instrument_id"),
        "generated_at": snapshot["generated_at"],
        "signal_type": "portfolio_recommendation",
        "action_semantics": snapshot["action"]["signal"],
        "strategy_id": "portfolio_adjudicator",
        "strategy_version": snapshot.get("rule_version", "unknown"),
        "scoring_status": "observation_only",
        "entry_snapshot": snapshot["entry_snapshot"],
        "provenance": {"decision_id": snapshot["decision_id"],
                       "source_run_id": snapshot["source_run_id"],
                       "source_snapshot_hash": snapshot["source_hash"]},
    }
    return schedule_signal(ledger, signal, window)
