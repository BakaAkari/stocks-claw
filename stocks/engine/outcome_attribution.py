"""Immutable recommendations and a read-only review of confirmed user facts.

Approval, user decisions, fills, cash movements and later quoted prices remain
separate. No strategy score, realized profit or portfolio gain is inferred.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from stocks.engine.entry_snapshot import canonical_identity, select_entry
from stocks.engine.evaluation_contract import canonical_json, digest, positive_number, utc

DEFAULT_DECISIONS_DIR = ".local/decisions"


def _decisions_dir(repo_root=None):
    return Path(repo_root or Path.cwd()) / DEFAULT_DECISIONS_DIR


def _snapshot_status(snapshot):
    if snapshot.get("schema_version") != 2:
        return "legacy_unverified"
    expected = digest({k: v for k, v in snapshot.items() if k != "source_hash"})
    return "verified" if snapshot.get("source_hash") == expected else "source_hash_mismatch"


def save_decision_snapshot(snapshot, *, repo_root=None, snaps_dir=None):
    """Atomically create one immutable recommendation; identical retries are no-ops."""
    if snapshot.get("schema_version") != 2:
        raise ValueError("legacy_snapshot_write_disabled")
    did = snapshot.get("decision_id")
    if not isinstance(did, str) or not did.strip():
        raise ValueError("decision_id_required")
    if _snapshot_status(snapshot) != "verified":
        raise ValueError("source_snapshot_hash_mismatch")
    base = Path(snaps_dir) if snaps_dir else _decisions_dir(repo_root)
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"recommendation-{digest(did)}.json"
    encoded = canonical_json(snapshot)
    fd, temporary = tempfile.mkstemp(prefix=".recommendation-", suffix=".tmp", dir=base)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)  # Publish complete bytes without replacing an existing ID.
        except FileExistsError:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if canonical_json(existing) != encoded:
                raise ValueError(f"immutable_recommendation_conflict:{did}") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def load_decision_snapshots(*, repo_root=None, snaps_dir=None):
    """Read both generations without upgrading or rewriting legacy claims."""
    base = Path(snaps_dir) if snaps_dir else _decisions_dir(repo_root)
    if not base.exists():
        return []
    snapshots = []
    for path in sorted(base.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(item, dict):
            snapshots.append(item)
    return snapshots


def save_portfolio_snapshots(
    portfolio_decision, *, generated_at="", repo_root=None, snaps_dir=None,
    run_id="", position_valuations=None, quotes=None, report_view=None, context=None,
):
    """Capture each actual proposed action once, without claiming a user execution."""
    generated = utc(generated_at).isoformat()
    positions = defaultdict(list)
    for position in position_valuations or []:
        if isinstance(position, dict):
            positions[position.get("position_id")].append(position)
    saved = []
    for status, field in (("approved", "approved_actions"), ("suppressed", "suppressed_actions")):
        for action in portfolio_decision.get(field, []):
            matches = positions.get(action.get("position_id"), [])
            position = matches[0] if len(matches) == 1 else None
            reason = None if position else ("ambiguous_position" if matches else "missing_position")
            instrument_id = None
            if position is not None:
                try:
                    instrument_id = canonical_identity(position.get("instrument_key"))
                except ValueError:
                    reason = "missing_or_invalid_position_instrument"
            entry = select_entry(instrument_id, quotes or {})
            snapshot = {
                "schema_version": 2, "decision_id": action["decision_id"],
                "source_run_id": run_id, "generated_at": generated,
                "recommendation_status": status, "action": deepcopy(action),
                "position": deepcopy(position), "position_id": action.get("position_id"),
                "instrument_id": instrument_id, "identity_reason": reason,
                "entry_snapshot": entry, "report_view": deepcopy(report_view),
                "context": deepcopy(context or {}),
                "rule_version": portfolio_decision.get("rule_version"),
                "portfolio_decision_id": portfolio_decision.get("decision_id"),
            }
            snapshot["source_hash"] = digest(snapshot)
            saved.append(save_decision_snapshot(snapshot, repo_root=repo_root, snaps_dir=snaps_dir))
    return saved


def _unique(records, kind):
    """Do not choose an arbitrary winner when two confirmed IDs disagree."""
    indexed = {}
    missing = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        identity = record.get("id")
        if not isinstance(identity, str) or not identity:
            missing.append(deepcopy(record))
            continue
        if identity in indexed and canonical_json(indexed[identity]) != canonical_json(record):
            raise ValueError(f"conflicting_{kind}_id:{identity}")
        indexed[identity] = record
    return [deepcopy(indexed[k]) for k in sorted(indexed)] + missing


def _visible(record, as_of, *fields):
    for field in fields:
        value = record.get(field)
        if value:
            try:
                return utc(value) <= as_of
            except (ValueError, TypeError):
                return False
    return False


def _advice_matches(advice, snapshot):
    source = advice.get("source_snapshot")
    return (snapshot is not None and advice.get("schema_version") == 2 and isinstance(source, dict)
            and _snapshot_status(source) == "verified"
            and source.get("source_hash") == snapshot.get("source_hash")
            and source.get("decision_id") == snapshot.get("decision_id")
            and advice.get("source_run_id") == snapshot.get("source_run_id")
            and any(a.get("decision_id") == snapshot.get("decision_id")
                    for a in advice.get("actions", []) if isinstance(a, dict)))


def _observations(snapshot, frozen_results, as_of, gaps):
    observations = []
    if snapshot is None:
        return observations
    from stocks.engine.target_observation import validate_observation

    for result in sorted(frozen_results or [], key=lambda r: str(r.get("result_id", ""))):
        signal = result.get("signal") or {}
        provenance = signal.get("provenance") or {}
        if provenance.get("decision_id") != snapshot["decision_id"]:
            continue
        if provenance.get("source_snapshot_hash") != snapshot.get("source_hash"):
            gaps.append("observation_source_snapshot_mismatch")
            continue
        if signal.get("symbol") != snapshot.get("instrument_id"):
            gaps.append("observation_instrument_mismatch")
            continue
        obs = result.get("exit") or {}
        if not _visible(obs, as_of, "retrieved_at"):
            continue
        if obs.get("status") == "observed":
            reason = validate_observation(signal["symbol"], result.get("target"), obs)
            if reason:
                gaps.append("invalid_observation_evidence:" + reason)
                continue
        observations.append({
            "result_id": result.get("result_id"), "signal_id": result.get("signal_id"),
            "policy_id": result.get("policy_id"), "target_at": result.get("target_at"),
            "status": result.get("status"), "reason": result.get("reason"),
            "source_snapshot_hash": snapshot["source_hash"], "observation": deepcopy(obs),
        })
    return observations


def _cashflow(execution, observations):
    flow = {"execution_id": execution.get("id"), "status": "unavailable", "reason": None,
            "side": execution.get("side"), "quantity": execution.get("quantity"),
            "price": execution.get("price"), "currency": execution.get("currency"),
            "fee_amount": execution.get("fee_amount"), "fee_currency": execution.get("fee_currency"),
            "gross_cash_flow": None, "cash_flow": None, "price_observations": []}
    if execution.get("schema_version") not in {2, 3}:
        return dict(flow, reason="legacy_execution_unverified")
    if (execution.get("status") != "executed" or execution.get("side") not in ("buy", "sell")
            or not positive_number(execution.get("quantity"))
            or not positive_number(execution.get("price"))
            or not isinstance(execution.get("currency"), str) or not execution["currency"]):
        return dict(flow, reason="incomplete_execution_facts")
    notional = execution["quantity"] * execution["price"]
    if not positive_number(notional):
        return dict(flow, reason="nonfinite_execution_notional")
    gross = notional * (-1 if execution["side"] == "buy" else 1)
    flow.update(gross_cash_flow=gross)
    fee = execution.get("fee_amount")
    fee_currency = execution.get("fee_currency")
    if fee is None:
        flow["reason"] = "fee_unknown"
    elif isinstance(fee, bool) or not (fee == 0 or positive_number(fee)):
        flow["reason"] = "invalid_fee"
    elif not fee_currency:
        flow["reason"] = "fee_currency_unknown"
    elif fee_currency != execution["currency"]:
        flow["reason"] = "fee_currency_differs"
    else:
        cash = gross - fee
        if math.isfinite(cash):
            flow.update(status="recorded", cash_flow=cash)
        else:
            flow["reason"] = "nonfinite_execution_cashflow"
    for item in observations:
        obs = item["observation"]
        if (obs.get("status") != "observed" or not positive_number(obs.get("price"))
                or obs.get("currency") != execution["currency"]):
            continue
        try:
            if utc(obs["observed_at"]) <= utc(execution["executed_at"]):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        change = obs["price"] / execution["price"] - 1
        if not (-float("inf") < change < float("inf")):
            continue
        flow["price_observations"].append({
            "result_id": item["result_id"], "target_at": item["target_at"],
            "observed_at": obs["observed_at"], "price": obs["price"],
            "raw_price_change": change,
            "semantics": "quoted_price_change_after_fill_not_profit",
        })
    return flow


def _currency_totals(flows):
    grouped = defaultdict(list)
    for flow in flows:
        if isinstance(flow.get("currency"), str) and flow["currency"]:
            grouped[flow["currency"]].append(flow)
    totals = {}
    for currency, rows in sorted(grouped.items()):
        gross = sum(f["gross_cash_flow"] for f in rows) if all(f["gross_cash_flow"] is not None for f in rows) else None
        cash = sum(f["cash_flow"] for f in rows) if all(f["cash_flow"] is not None for f in rows) else None
        gaps = {f["reason"] for f in rows if f["reason"]}
        if any(value is not None and not math.isfinite(value) for value in (gross, cash)):
            gross = cash = None
            gaps.add("nonfinite_currency_total")
        totals[currency] = {
            "scope": "recorded_fills_only",
            "gross_cash_flow": gross,
            "cash_flow": cash,
            "complete": cash is not None,
            "execution_ids": [f["execution_id"] for f in rows],
            "gaps": sorted(gaps),
        }
    return totals


def _event_order(record, event_field):
    """Order instants, including records carrying different UTC offsets."""
    earliest = datetime.min.replace(tzinfo=timezone.utc)

    def instant(field):
        try:
            return utc(record.get(field))
        except (ValueError, TypeError):
            return earliest

    return instant(event_field), instant("recorded_at"), str(record.get("id", ""))


def build_decision_review(
    snapshots, executions, frozen_results, *, as_of,
    advice_records=None, user_decisions=None, decision_id=None,
):
    """Pure projection: user records never mutate recommendations or frozen results."""
    current = utc(as_of.isoformat() if isinstance(as_of, datetime) else as_of)
    advice = _unique(advice_records, "advice")
    decisions = _unique(user_decisions, "user_decision")
    fills = _unique(executions, "execution")
    by_snapshot = defaultdict(list)
    for snapshot in snapshots or []:
        if isinstance(snapshot, dict) and snapshot.get("decision_id"):
            by_snapshot[snapshot["decision_id"]].append(snapshot)
    ids = set(by_snapshot)
    ids.update(r["decision_id"] for r in decisions + fills if r.get("decision_id"))
    for record in advice:
        ids.update(a["decision_id"] for a in record.get("actions", []) if isinstance(a, dict) and a.get("decision_id"))
    if decision_id is not None:
        ids = {decision_id}
    reviewed = {}
    for did in sorted(ids):
        raw_snapshots = by_snapshot.get(did, [])
        valid = [s for s in raw_snapshots if _snapshot_status(s) == "verified"]
        if len({s["source_hash"] for s in valid}) > 1:
            raise ValueError(f"conflicting_recommendation_id:{did}")
        snapshot = deepcopy(valid[0]) if valid else None
        gaps = []
        snapshot_status = "verified" if snapshot else "missing"
        if snapshot is None:
            if raw_snapshots:
                snapshot_status = "source_hash_mismatch" if any(s.get("schema_version") == 2 for s in raw_snapshots) else "legacy_unverified"
            gaps.append(snapshot_status if raw_snapshots else "missing_recommendation_snapshot")
        elif not _visible(snapshot, current, "generated_at"):
            continue
        if snapshot and not snapshot.get("instrument_id"):
            gaps.append(snapshot.get("identity_reason") or "missing_instrument")
        related_advice = [a for a in advice if _visible(a, current, "created_at")
                          and any(item.get("decision_id") == did for item in a.get("actions", [])
                                  if isinstance(item, dict))]
        linked_advice = [a for a in related_advice if _advice_matches(a, snapshot)]
        unlinked_advice = [a for a in related_advice if not _advice_matches(a, snapshot)]
        if unlinked_advice:
            gaps.append("advice_source_snapshot_unverified")
        advice_ids = {a["id"] for a in linked_advice if a.get("id")}
        if not advice_ids:
            gaps.append("confirmed_advice_unrecorded")
        related_decisions = [d for d in decisions if d.get("decision_id") == did
                            and _visible(d, current, "recorded_at")
                            and _visible(d, current, "decided_at")]
        related_decisions.sort(key=lambda d: _event_order(d, "decided_at"))
        linked_decisions = [d for d in related_decisions if d.get("advice_id") in advice_ids]
        unlinked_decisions = [d for d in related_decisions if d.get("advice_id") not in advice_ids]
        if unlinked_decisions:
            gaps.append("user_decision_advice_link_unverified")
        if not linked_decisions:
            gaps.append("user_decision_unrecorded")
        latest = deepcopy(linked_decisions[-1]) if linked_decisions else None
        decision_ids = {d.get("id") for d in linked_decisions}
        related_executions = [r for r in fills if r.get("decision_id") == did
                              and _visible(r, current, "recorded_at")]
        related_executions.sort(key=lambda r: _event_order(r, "executed_at"))
        observations = _observations(snapshot, frozen_results, current, gaps)
        if not observations:
            gaps.append("frozen_observation_unavailable")
        cashflows = []
        valid_fills = []
        for execution in related_executions:
            link_reason = None
            if snapshot is None:
                link_reason = "execution_recommendation_unverified"
            elif execution.get("advice_id") not in advice_ids:
                link_reason = "execution_advice_link_unverified"
            elif execution.get("source_run_id") != snapshot.get("source_run_id"):
                link_reason = "execution_source_run_mismatch"
            elif execution.get("target") != snapshot.get("instrument_id"):
                link_reason = "execution_instrument_mismatch"
            elif not _visible(execution, current, "executed_at"):
                link_reason = "execution_time_unverified"
            if link_reason:
                gaps.append(link_reason)
                cashflows.append({"execution_id": execution.get("id"), "status": "unavailable",
                                  "reason": link_reason, "currency": execution.get("currency"),
                                  "gross_cash_flow": None, "cash_flow": None, "price_observations": []})
                continue
            if execution.get("user_decision_id") not in decision_ids:
                gaps.append("execution_user_decision_unrecorded")
            flow = _cashflow(execution, observations)
            cashflows.append(flow)
            if flow["gross_cash_flow"] is not None:
                valid_fills.append(execution)
            if flow["reason"]:
                gaps.append(flow["reason"])
        execution_status = "recorded_fills" if valid_fills else "unrecorded"
        action = (snapshot or {}).get("action", {})
        proposed = action.get("executable_quantity")
        expected_side = {"buy": "buy", "sell": "sell", "add": "buy", "reduce": "sell", "stop_loss": "sell", "take_profit": "sell"}.get(action.get("signal"))
        same_direction = expected_side is not None and all(f["side"] == expected_side for f in valid_fills)
        if valid_fills and expected_side and not same_direction:
            gaps.append("execution_direction_mismatch")
        if valid_fills and positive_number(proposed) and same_direction:
            quantity = sum(f["quantity"] for f in valid_fills)
            execution_status = "partial" if quantity < proposed else ("full" if quantity == proposed else "exceeds_recommendation")
        reviewed[did] = {
            "decision_id": did, "snapshot": snapshot,
            "snapshot_status": snapshot_status,
            "legacy_snapshots": deepcopy([s for s in raw_snapshots if s.get("schema_version") != 2]),
            "unverified_snapshots": deepcopy([s for s in raw_snapshots if s.get("schema_version") == 2 and _snapshot_status(s) != "verified"]),
            "recommendation_status": (snapshot or {}).get("recommendation_status", "unknown"),
            "advice_records": linked_advice, "user_decisions": linked_decisions,
            "unlinked_advice_records": unlinked_advice,
            "unlinked_user_decisions": unlinked_decisions,
            "latest_user_decision": latest, "executions": related_executions,
            "execution_status": execution_status, "recorded_fill_count": len(valid_fills),
            "cashflows": cashflows, "cashflow_by_currency": _currency_totals(cashflows),
            "price_observations": observations, "gaps": sorted(set(gaps)),
            "realized_pnl": {"status": "unavailable", "reason": "cost_lots_and_inventory_path_not_available"},
            "portfolio_gain": {"status": "unavailable", "reason": "no_comparable_portfolio_baseline"},
        }
    report = {"schema_version": 2, "scope": "decision_fact_review", "as_of": current.isoformat(),
              "by_decision": reviewed,
              "summary": {"decisions": len(reviewed),
                          "execution_statuses": dict(sorted(Counter(r["execution_status"] for r in reviewed.values()).items()))}}
    report["content_hash"] = digest(report)
    return report


def run_settlement(
    *, as_of=None, executions=None, repo_root=None, evaluation_database=None,
    advice_records=None, user_decisions=None, decision_id=None, snapshots_dir=None,
):
    """Compatibility entry point: read existing evidence without saving or fetching."""
    snapshots = load_decision_snapshots(repo_root=repo_root, snaps_dir=snapshots_dir)
    results = []
    if evaluation_database is not None and Path(evaluation_database).exists():
        uri = Path(evaluation_database).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "evaluation_results" in tables:
                results = [json.loads(r[0]) for r in db.execute("SELECT payload FROM evaluation_results ORDER BY signal_id,policy_id,target_at")]
    return build_decision_review(
        snapshots, executions or [], results, as_of=as_of or datetime.now(timezone.utc),
        advice_records=advice_records, user_decisions=user_decisions, decision_id=decision_id,
    )


def settle_decisions(as_of, price_history, snapshots, executions):
    """Legacy API is retained only to expose unverified history, never old scores."""
    report = build_decision_review(snapshots, executions, [], as_of=as_of)
    report["legacy_price_history_ignored"] = True
    report["content_hash"] = digest({k: v for k, v in report.items() if k != "content_hash"})
    return report
