"""Decision Trust T1 — 版本化效果归因与 Shadow Trial。

Provides:
- DecisionSnapshot: immutable record of a planned/executed portfolio action
- save_decision_snapshot / load_decision_snapshots: file-based persistence
  under .local/decisions/
- settle_decisions: evaluate expired-horizon snapshots against deterministic
  price history, producing an attribution_summary with per-horizon and
  per-decision breakdowns, a sample-size gate (<10 = counts only,
  >=10 = stats with Wilson confidence interval), and separate executed vs
  shadow (counterfactual) summaries.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DECISIONS_DIR = ".local/decisions"
DEFAULT_COMMISSION_RATE = 0.0003  # 0.03%
_HORIZONS = frozenset({1, 5, 20})


# ── I/O helpers ──────────────────────────────────────────────────────────


def _decisions_dir(repo_root: Path | None = None) -> Path:
    root = repo_root or Path.cwd()
    return root / DEFAULT_DECISIONS_DIR


def save_decision_snapshot(
    snapshot: dict,
    *,
    repo_root: Path | None = None,
    snaps_dir: str | None = None,
) -> Path:
    """Save one decision snapshot as JSON (idempotent by decision_id)."""
    base = Path(snaps_dir) if snaps_dir else _decisions_dir(repo_root)
    base.mkdir(parents=True, exist_ok=True)
    did = snapshot["decision_id"]
    horizon = snapshot.get("horizon_days", "")
    path = base / f"{did}_{horizon}d.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return path


def load_decision_snapshots(
    *,
    repo_root: Path | None = None,
    snaps_dir: str | None = None,
) -> list[dict]:
    """Load all decision snapshots from .local/decisions/."""
    base = Path(snaps_dir) if snaps_dir else _decisions_dir(repo_root)
    if not base.exists():
        return []
    snaps: list[dict] = []
    for path in sorted(base.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        snaps.append(data)
    return snaps


# ── Core settlement logic ────────────────────────────────────────────────


def _lookup_price(
    history: dict[str, dict[str, float]],
    instrument_key: str,
    target_date: date,
) -> Optional[float]:
    """Find the closing price for *instrument_key* on or before *target_date*.

    Searches backward from target_date to find the nearest available trading
    day's price (the last close before or on the target date).
    """
    series = history.get(instrument_key)
    if not series:
        return None
    # Try exact date first, then walk backwards up to 10 days
    cursor = target_date
    for _ in range(14):
        ds = cursor.isoformat()
        if ds in series:
            return series[ds]
        cursor -= timedelta(days=1)
    return None


def _count_trading_days(
    history: dict[str, dict[str, float]],
    instrument_key: str,
    from_date: date,
    horizon_days: int,
) -> Optional[date]:
    """Advance *horizon_days* trading days forward from *from_date*.

    Returns the calendar date of the Nth trading day after (and excluding)
    from_date, or None if insufficient history.
    """
    series = history.get(instrument_key)
    if not series:
        return None
    trading_dates = sorted(series.keys())
    # Find from_date in the series
    from_iso = from_date.isoformat()
    start_idx: Optional[int] = None
    for i, ds in enumerate(trading_dates):
        if ds >= from_iso:
            start_idx = i
            break
    if start_idx is None:
        return None
    target_idx = start_idx + horizon_days
    if target_idx >= len(trading_dates):
        return None
    target_str = trading_dates[target_idx]
    return date.fromisoformat(target_str)


def _settle_one(
    snap: dict,
    history: dict[str, dict[str, float]],
    executions_by_id: dict[str, dict],
    as_of: datetime,
) -> dict:
    """Settle a single snapshot and return the outcome dict.

    Returns the original snapshot with an added 'outcome' sub-dict and
    'settled': True when the horizon has expired.
    """
    # Already settled
    if snap.get("settled"):
        outcome = snap.get("outcome") or {}
        return {
            "_snapshot": snap,
            "decision_id": snap["decision_id"],
            "position_id": snap["position_id"],
            "signal": snap["signal"],
            "horizon_days": snap["horizon_days"],
            "executed": snap["executed"],
            "settled": True,
            "settled_now": False,
            "outcome_return": outcome.get("outcome_return"),
            "execution_price_used": outcome.get("execution_price_used"),
            "commission_paid": outcome.get("commission_paid", 0.0),
            "exit_price": outcome.get("exit_price"),
            "win": outcome.get("win"),
        }

    did = snap["decision_id"]
    horizon = snap["horizon_days"]
    instrument_key = snap["position_id"]
    data_as_of = datetime.fromisoformat(snap["data_as_of"])
    entry_date = data_as_of.date()

    # Find execution record
    exec_rec = executions_by_id.get(did)

    # Determine execution price
    if exec_rec:
        execution_price = exec_rec.get("price") or snap["execution_price"]
    else:
        execution_price = snap["execution_price"]

    if execution_price is None:
        execution_price = _lookup_price(history, instrument_key, entry_date)
    if execution_price is None:
        return {
            "_snapshot": snap,
            "decision_id": did,
            "position_id": instrument_key,
            "signal": snap["signal"],
            "horizon_days": horizon,
            "executed": snap["executed"],
            "settled": False,
            "settled_now": False,
            "error": "missing_execution_price",
        }

    # Handle settlement timing: execution_price reflects the actual deal price.
    # T+N means the execution settles N business days later; the true entry for
    # return calculation is the execution_price.
    actual_entry = execution_price

    # Determine exit date: horizon trading days from entry_date
    exit_date = _count_trading_days(history, instrument_key, entry_date, horizon)
    if exit_date is None:
        return {
            "_snapshot": snap,
            "decision_id": did,
            "position_id": instrument_key,
            "signal": snap["signal"],
            "horizon_days": horizon,
            "executed": snap["executed"],
            "settled": False,
            "settled_now": False,
            "error": "insufficient_history_for_exit",
        }

    # Check if horizon has expired (as_of >= exit_date)
    if as_of.date() < exit_date:
        return {
            "_snapshot": snap,
            "decision_id": did,
            "position_id": instrument_key,
            "signal": snap["signal"],
            "horizon_days": horizon,
            "executed": snap["executed"],
            "settled": False,
            "settled_now": False,
            "pending_until": exit_date.isoformat(),
        }

    # Look up exit price
    exit_price = _lookup_price(history, instrument_key, exit_date)
    if exit_price is None:
        return {
            "_snapshot": snap,
            "decision_id": did,
            "position_id": instrument_key,
            "signal": snap["signal"],
            "horizon_days": horizon,
            "executed": snap["executed"],
            "settled": False,
            "settled_now": False,
            "error": "missing_exit_price",
        }

    # Calculate return
    commission_rate = snap.get("commission_rate", DEFAULT_COMMISSION_RATE)
    # For buy (add/accumulate): return = (exit - entry) / entry - 2*commission
    # For sell (reduce): return = (entry - exit) / entry - 2*commission
    signal = snap["signal"]
    if signal in ("add", "accumulate", "buy"):
        gross_return = (exit_price - actual_entry) / actual_entry
    elif signal in ("reduce", "stop_loss", "take_profit", "sell"):
        gross_return = (actual_entry - exit_price) / actual_entry
    else:
        # hold/wait - no directional bet
        gross_return = 0.0

    commission_paid = 2.0 * commission_rate  # buy + sell
    net_return = gross_return - commission_paid
    win = net_return > 0

    snap["settled"] = True  # mark in-place for idempotency
    outcome = {
        "decision_id": did,
        "position_id": instrument_key,
        "signal": signal,
        "horizon_days": horizon,
        "executed": snap["executed"],
        "settled": True,
        "settled_now": True,
        "entry_price_planned": snap["entry_price"],
        "execution_price_used": actual_entry,
        "exit_price": exit_price,
        "outcome_return": round(net_return, 6),
        "commission_paid": round(commission_paid, 6),
        "win": win,
        "_snapshot": snap,
    }
    return outcome


def _wilson_confidence_interval(
    wins: int, total: int, z: float = 1.96,
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if total == 0:
        return (0.0, 0.0)
    p = wins / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
        / denominator
    )
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def _build_group_summary(outcomes: list[dict]) -> dict:
    """Build a summary dict for a group of settled outcomes.

    Sample-size gate: <10 → counts/raw only; >=10 → stats + CI.
    """
    total = len(outcomes)
    wins = sum(1 for o in outcomes if o.get("win"))
    losses = total - wins
    total_return = sum(o.get("outcome_return", 0.0) for o in outcomes)
    avg_return = round(total_return / total, 6) if total > 0 else 0.0

    summary: dict = {
        "count": total,
        "wins": wins,
        "losses": losses,
        "total_return": round(total_return, 6),
        "avg_return": avg_return,
    }

    if total < 10:
        summary["win_rate"] = None
        summary["confidence_interval"] = None
        summary["statistical_note"] = "样本不足10只，仅输出计数结果"
    else:
        win_rate = round(wins / total, 4)
        ci = _wilson_confidence_interval(wins, total)
        summary["win_rate"] = win_rate
        summary["confidence_interval"] = ci
        summary["statistical_note"] = ""

    return summary


def settle_decisions(
    as_of: datetime,
    price_history: dict[str, dict[str, float]],
    snapshots: list[dict],
    executions: list[dict],
) -> dict:
    """Settle all expired-horizon decision snapshots and produce attribution.

    Parameters
    ----------
    as_of : datetime
        Settlement reference time — snapshots whose horizon exit date is on
        or before *as_of* are settled.
    price_history : dict[str, dict[str, float]]
        Deterministic price series keyed by instrument_key, then date str.
        e.g. {"a:588000": {"2026-07-01": 100.0, "2026-07-02": 101.0, ...}}
    snapshots : list[dict]
        Decision snapshots (from load_decision_snapshots).
    executions : list[dict]
        ExecutionRecord dicts (from persistence.list_executions()).

    Returns
    -------
    dict with keys:
        settled_at, total_settled, total_pending, by_horizon, by_decision,
        summary, _snapshots_updated (the settled snapshots to write back).
    """
    executions_by_id: dict[str, dict] = {}
    for rec in executions:
        did = rec.get("decision_id", "")
        if did:
            executions_by_id[did] = rec

    # Settle each snapshot
    outcomes: list[dict] = []
    pending_count = 0
    by_horizon: dict[str, list[dict]] = {"1d": [], "5d": [], "20d": []}
    by_decision: dict[str, dict] = {}

    for snap in snapshots:
        outcome = _settle_one(snap, price_history, executions_by_id, as_of)
        did = outcome["decision_id"]
        by_decision[did] = outcome

        if outcome.get("settled") and outcome.get("settled_now"):
            outcomes.append(outcome)
            h_key = f'{outcome["horizon_days"]}d'
            if h_key in by_horizon:
                by_horizon[h_key].append(outcome)
        elif not outcome.get("settled"):
            pending_count += 1

    # Build summary groups
    executed_outcomes = [o for o in outcomes if o.get("executed")]
    shadow_outcomes = [o for o in outcomes if not o.get("executed")]

    # Separate executed/shadow by horizon
    executed_by_horizon: dict[str, list[dict]] = {}
    shadow_by_horizon: dict[str, list[dict]] = {}
    for h_key in ("1d", "5d", "20d"):
        executed_by_horizon[h_key] = [o for o in by_horizon[h_key] if o.get("executed")]
        shadow_by_horizon[h_key] = [o for o in by_horizon[h_key] if not o.get("executed")]

    # Build snapshots_updated list for writing back
    snapshots_updated: list[dict] = []
    for outcome in by_decision.values():
        if outcome.get("settled_now"):
            updated = dict(outcome["_snapshot"])
            updated["settled"] = True
            updated["outcome"] = {
                "outcome_return": outcome["outcome_return"],
                "execution_price_used": outcome["execution_price_used"],
                "exit_price": outcome["exit_price"],
                "commission_paid": outcome["commission_paid"],
                "win": outcome["win"],
            }
            updated["settled_at"] = as_of.isoformat()
            snapshots_updated.append(updated)

    return {
        "settled_at": as_of.isoformat(),
        "total_settled": len(outcomes),
        "total_pending": pending_count,
        "by_horizon": {
            h: {
                "executed": _build_group_summary(executed_by_horizon[h]),
                "shadow": _build_group_summary(shadow_by_horizon[h]),
            }
            for h in ("1d", "5d", "20d")
        },
        "by_decision": by_decision,
        "summary": {
            "executed": _build_group_summary(executed_outcomes),
            "shadow": _build_group_summary(shadow_outcomes),
        },
        "_snapshots_updated": snapshots_updated,
    }


# ── Integration helpers ──────────────────────────────────────────────────


def save_portfolio_snapshots(
    portfolio_decision: dict,
    *,
    generated_at: str = "",
    repo_root: Path | None = None,
    snaps_dir: str | None = None,
) -> list[Path]:
    """Save DecisionSnapshots for each approved action in the PortfolioDecision.

    Three snapshots (horizons 1, 5, 20 trading days) are created per action.
    Shadow snapshots are also created for suppressed actions so counterfactual
    evaluation can be performed during settlement.

    Returns the list of saved file paths.
    """
    from hashlib import sha256 as _sha256

    rule_version = portfolio_decision.get("rule_version", "decision-trust-t1-v1")
    params_hash = _sha256(json.dumps(portfolio_decision, sort_keys=True).encode()).hexdigest()[:16]
    data_as_of = generated_at or datetime.now(timezone.utc).isoformat()

    saved: list[Path] = []

    # Approved actions → executed snapshots
    for action in portfolio_decision.get("approved_actions", []):
        for horizon in (1, 5, 20):
            snap = {
                "decision_id": action["decision_id"],
                "rule_version": rule_version,
                "params_hash": params_hash,
                "data_as_of": data_as_of,
                "position_id": action["position_id"],
                "signal": action["signal"],
                "ratio": action["ratio"],
                "horizon_days": horizon,
                "executed": True,
                "entry_price": 0.0,   # placeholder — caller should fill
                "execution_price": 0.0,
                "settlement_timing": action.get("settlement_timing", "T+0"),
                "commission_rate": DEFAULT_COMMISSION_RATE,
                "settled": False,
                "outcome": None,
            }
            saved.append(save_decision_snapshot(snap, repo_root=repo_root, snaps_dir=snaps_dir))

    # Suppressed actions → shadow (unexecuted) snapshots
    for action in portfolio_decision.get("suppressed_actions", []):
        for horizon in (1, 5, 20):
            snap = {
                "decision_id": action["decision_id"],
                "rule_version": rule_version,
                "params_hash": params_hash,
                "data_as_of": data_as_of,
                "position_id": action["position_id"],
                "signal": action["signal"],
                "ratio": action["ratio"],
                "horizon_days": horizon,
                "executed": False,
                "entry_price": 0.0,
                "execution_price": 0.0,
                "settlement_timing": action.get("settlement_timing", "T+0"),
                "commission_rate": DEFAULT_COMMISSION_RATE,
                "settled": False,
                "outcome": None,
            }
            saved.append(save_decision_snapshot(snap, repo_root=repo_root, snaps_dir=snaps_dir))

    return saved


# ── Convenience entry point for scheduled / CLI ──────────────────────────


def run_settlement(
    *,
    as_of: datetime | None = None,
    price_history: dict[str, dict[str, float]] | None = None,
    repo_root: Path | None = None,
) -> dict:
    """Load all snapshots + executions, settle them, persist updated snapshots.

    This is the high-level entry point for cron/CLI usage.
    """
    from stocks.engine import StocksEngine

    as_of = as_of or datetime.now(timezone.utc)

    root = repo_root or Path.cwd()
    snapshots = load_decision_snapshots(repo_root=root)

    if not snapshots:
        return {
            "settled_at": as_of.isoformat(),
            "total_settled": 0,
            "total_pending": 0,
            "message": "没有待结算的决策快照",
        }

    # Load executions from persistence
    engine = StocksEngine()
    executions = engine.list_executions()

    # TODO: in production, price_history should be sourced from the engine's
    # history cache or a provider. For now we require it to be passed in.
    if price_history is None:
        return {
            "settled_at": as_of.isoformat(),
            "total_settled": 0,
            "total_pending": len(snapshots),
            "message": "需要传入 price_history 才能结算",
        }

    result = settle_decisions(as_of, price_history, snapshots, executions)

    # Persist updated snapshots
    for updated in result.get("_snapshots_updated", []):
        save_decision_snapshot(updated, repo_root=root)

    # Strip internal fields from return
    result.pop("_snapshots_updated", None)
    return result
