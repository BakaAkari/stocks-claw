"""Freeze LLM responsibility and mechanically validate its unchanged proposals.

No orders, user-memory writes, or alternative investment decisions happen here.
Unsupported conditions remain proposals awaiting review. The intentionally small
executable subset is an existing position with exact shares and confirmed facts.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from math import isfinite

from stocks.engine.constraint_model import ConstraintModel
from stocks.engine.entry_snapshot import select_entry
from stocks.engine.evaluation_contract import digest, utc
from stocks.engine.execution_rules import resolve_execution
from stocks.engine.fee_budget import check_purchase

BRIDGE_VERSION = "llm-proposal-gate-v2"
_ACTIONABLE = {"buy", "add", "reduce", "sell"}
_HARD_CONTEXT_FIELDS = ("loss_capacity", "cash_needs")
_PREFERENCE_FIELDS = ("goals", "horizon", "operation_frequency", "style_preferences")


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and isfinite(float(result)) else None


def empty_decision(reason="LLM proposal unavailable"):
    return {
        "status": "review_required", "decision_id": "", "author": "llm",
        "rule_version": BRIDGE_VERSION, "approved_actions": [],
        "suppressed_actions": [], "replacement_chains": [],
        "unresolved_conflicts": [{"type": "advisory_review_required", "reason": reason}],
        "post_trade_projection": {}, "cash_schedule": {},
    }


def _mandate_reasons(mandate):
    fields = mandate.get("fields") or {}
    reasons = [f"personal_mandate_unknown:{key}" for key in _HARD_CONTEXT_FIELDS
               if (fields.get(key) or {}).get("status") != "confirmed"]
    sources = mandate.get("sources") or {}
    if (sources.get("profile") or {}).get("kind") != "local_confirmed":
        reasons.append("personal_profile_not_user_confirmed")
    if ((mandate.get("constraint_snapshot") or {}).get("status") != "confirmed"
            or (sources.get("constraints") or {}).get("kind") != "local_confirmed"):
        reasons.append("portfolio_constraints_not_user_confirmed")
    return reasons


def _validate_proposal(action, snapshot, common_reasons, position_counts):
    meta = snapshot.metadata
    reasons = list(common_reasons)
    pvs = meta.get("position_valuations") or []
    matches = [pv for pv in pvs if pv.get("position_id") == action.target]
    if not matches:
        matches = [pv for pv in pvs if pv.get("instrument_key") == action.target]
    pv = matches[0] if len(matches) == 1 else {}
    pid = str(pv.get("position_id") or action.target)
    instrument = str(pv.get("instrument_key") or "")
    aid = str(pv.get("account_id") or "")
    holding = pv.get("holding") or {}
    quantity = _number(action.size)
    held = _number(holding.get("quantity"))
    ratio = float(quantity / held) if quantity is not None and held and held > 0 else 0.0
    if not isfinite(ratio):
        ratio = 0.0
        reasons.append("unsupported_quantity_scale")
    if len(matches) != 1:
        reasons.append("existing_position_not_unique")
    if position_counts.get(pid, 0) > 1:
        reasons.append("multiple_proposals_for_same_position_require_review")
    if not action.evidence_refs or f"position:{pid}" not in action.evidence_refs:
        reasons.append("position_evidence_reference_required")
    if action.size_type != "shares" or quantity is None or quantity <= 0:
        reasons.append("exact_positive_share_quantity_required")
    if holding.get("unit") not in {"share", "shares"} or held is None or held <= 0:
        reasons.append("confirmed_share_holding_required")
    if action.execute_when != "now":
        reasons.append("conditional_proposal_requires_manual_review")
    if not action.cancel_when or not action.reasoning:
        reasons.append("proposal_reason_and_invalidation_required")
    integrity = meta.get("financial_integrity") or {}
    confirmed = (integrity.get("positions") or {}).get(pid) or {}
    if (confirmed.get("account_id") != aid or confirmed.get("instrument_id") != instrument
            or confirmed.get("unit") not in {"share", "shares"}
            or _number(confirmed.get("quantity")) != held):
        reasons.append("confirmed_position_mismatch")
    account = (integrity.get("accounts") or {}).get(aid) or {}
    if (not aid or account.get("status") != "matched" or not account.get("statement_hash")
            or not account.get("statement_id") or not account.get("statement_as_of")):
        reasons.append("account_reconciliation_evidence_missing")
    max_age = (meta.get("validation_policy") or {}).get("financial_statement_max_age_seconds")
    try:
        age = (utc(snapshot.generated_at) - utc(account.get("statement_as_of"))).total_seconds()
        if (isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0
                or not 0 <= age <= max_age):
            reasons.append("available_statement_outside_freshness_policy")
    except (ValueError, TypeError):
        reasons.append("available_statement_time_unknown")
    entry = select_entry(instrument, meta.get("quotes") or {})
    if entry.get("scoring_status") != "scorable":
        reasons.append("entry_quote_unverified:" + str(entry.get("reason")))
    try:
        if utc(entry.get("as_of")) > utc(snapshot.generated_at):
            reasons.append("entry_quote_after_snapshot")
    except (TypeError, ValueError):
        reasons.append("entry_quote_time_unknown")
    fee_check = None
    if action.action == "buy":
        reasons.append("new_position_buy_requires_explicit_account_route")
    if action.action in {"reduce", "sell"}:
        available = _number(confirmed.get("available_quantity"))
        if available is None or quantity is None or not (0 < quantity <= available <= (held or 0)):
            reasons.append("available_quantity_missing_or_exceeded")
    else:
        cash = (account.get("cash_by_currency") or {}).get(entry.get("currency")) or {}
        available_cash = _number(cash.get("available_balance"))
        price = _number(entry.get("price"))
        if (available_cash is None or quantity is None or price is None
                or quantity * price > available_cash):
            reasons.append("available_cash_missing_or_exceeded")
        fee_check = check_purchase(
            meta.get("fee_budgets"), account_id=aid, instrument_id=instrument,
            currency=entry.get("currency"), quantity=quantity, price=price,
            available_cash=available_cash, as_of=snapshot.generated_at)
        reasons.extend(fee_check["reasons"])
    mandate = meta.get("personal_mandate") or {}
    constraints = (mandate.get("constraint_snapshot") or {}).get("value") or {}
    try:
        model = ConstraintModel.from_config(constraints)
        restriction = model.restriction_for(pid, instrument)
        if restriction.get("no_buyback"):
            reasons.append("irreversible_position_requires_user_review")
        # No complete multi-currency portfolio projection is claimed by this
        # bounded bridge. Explicit allocation bounds cannot be silently ignored.
        if model.buckets or model.bucket_limits or model.hard_caps:
            reasons.append("allocation_constraints_require_portfolio_projection")
    except (TypeError, ValueError):
        reasons.append("invalid_confirmed_constraints")
    execution_evidence = deepcopy(pv)
    # The financial-fact schema uses singular share; legacy execution-rule
    # matching uses shares. This is a unit spelling adapter, not conversion.
    if (execution_evidence.get("holding") or {}).get("unit") == "share":
        execution_evidence["holding"]["unit"] = "shares"
    execution_card = {**pv, "institution_type": pv.get("institution_type") or
                      (pv.get("account") or {}).get("institution_type", "")}
    resolution = resolve_execution(
        evidence=execution_evidence, card=execution_card, side="reduce" if action.action in {"reduce", "sell"} else "add",
        ratio=ratio, config=meta.get("execution_rules"))
    if (resolution.execution_status not in {"full", "adjusted_to_step"}
            or _number(resolution.executable_quantity) != quantity):
        reasons.append("exact_quantity_execution_rule_not_satisfied")
    record = {
        "position_id": pid, "instrument_id": instrument, "account_id": aid,
        "signal": action.action, "action_description": action.reasoning,
        "reason": action.reasoning, "ratio": ratio, "final_ratio": ratio,
        "original_ratio": ratio, "cancel_condition": action.cancel_when,
        "next_checkpoint": "下一交易窗口复核",
        "platform_display": (pv.get("account") or {}).get("display_name", ""),
        "institution_type": execution_card["institution_type"],
        "settlement_rule": resolution.settlement_rule,
        "executable_quantity": float(quantity) if quantity is not None and quantity > 0 else None,
        "execution_status": "review_required" if reasons else "full",
        "execution_resolution": asdict(resolution),
        "estimated_amount_cny": (float(_number(quantity * _number(entry["price"])))
                                  if quantity is not None and entry.get("currency") == "CNY"
                                  and _number(entry.get("price")) is not None
                                  and _number(quantity * _number(entry["price"])) is not None else None),
        "fee_budget_check": fee_check,
        "amount_is_estimate": True, "author": "llm", "original_proposal": asdict(action),
        "validation_reasons": list(dict.fromkeys(reasons)),
        "decision_reason": "；".join(dict.fromkeys(reasons)) if reasons else action.reasoning,
        "evidence_summary": "，".join(action.evidence_refs),
    }
    return record


def build_advisory_lineage(snapshot, advisory, receipt, attempts):
    """Return immutable-by-value evidence plus the final, unchanged-action decision."""
    frozen_snapshot, parsed = asdict(snapshot), asdict(advisory)
    contract_receipt = asdict(receipt)
    common = _mandate_reasons(snapshot.metadata.get("personal_mandate") or {})
    integrity = snapshot.metadata.get("financial_integrity") or {}
    if integrity.get("status") != "matched" or not integrity.get("input_hash") or integrity.get("gaps"):
        common.append("financial_integrity_not_matched")
    if receipt.errors:
        common.append("advisory_contract_invalid")
    if not attempts or attempts[-1].get("status") != "parsed":
        common.append("llm_response_unavailable")
    position_counts = {}
    for action in advisory.actions:
        matched = [pv for pv in snapshot.metadata.get("position_valuations", [])
                   if action.target in {pv.get("position_id"), pv.get("instrument_key")}]
        key = str(matched[0].get("position_id")) if len(matched) == 1 else action.target
        position_counts[key] = position_counts.get(key, 0) + 1
    records = [_validate_proposal(a, snapshot, common, position_counts)
               for a in advisory.actions if a.action in _ACTIONABLE]
    # Per-order reserves cannot be spent twice in one batch. Until full cash
    # projection exists, suppress all co-funded purchases without choosing one.
    purchase_groups = {}
    for record in records:
        if record["signal"] in {"buy", "add"}:
            currency = (record.get("fee_budget_check") or {}).get("currency")
            purchase_groups.setdefault((record["account_id"], currency), []).append(record)
    for group in purchase_groups.values():
        if len(group) > 1:
            for record in group:
                record["validation_reasons"].append("multiple_purchases_require_cash_projection")
                record["execution_status"] = "review_required"
                record["decision_reason"] = "；".join(record["validation_reasons"])
    action_validation = [{"action_id": r["original_proposal"]["action_id"],
                          "status": r["execution_status"], "reasons": r["validation_reasons"]}
                         for r in records]
    final_receipt = {
        "schema_version": 2, "validator_version": BRIDGE_VERSION,
        "status": "review_required" if common or any(r["validation_reasons"] for r in records) else "validated",
        "snapshot_hash": snapshot.snapshot_id, "advisory_content_hash": digest(parsed),
        "validation_policy": deepcopy(snapshot.metadata.get("validation_policy")),
        "prompt_hash": attempts[-1].get("prompt_hash") if attempts else None,
        "contract_receipt": contract_receipt, "common_reasons": common,
        "personal_mandate_scope": {
            "unknown_preferences": [key for key in _PREFERENCE_FIELDS
                if ((snapshot.metadata.get("personal_mandate") or {}).get("fields", {}).get(key) or {}).get("status") != "confirmed"],
            "mechanical_checks": ["explicit_position_restrictions", "allocation_constraint_support",
                                  "account_position_identity", "available_quantity", "available_cash",
                                  "exact_quantity_and_settlement_rules", "confirmed_fee_budget",
                                  "single_purchase_cash_scope"],
            "llm_context_only": ["goals", "horizon", "operation_frequency", "style_preferences",
                                 "loss_capacity", "cash_needs"],
            "not_claimed": ["suitability", "loss_forecast", "realized_profit", "cash_needs_optimization"],
        },
        "actions": action_validation,
    }
    receipt_hash = digest(final_receipt)
    for record in records:
        record.update({"source_advisory_id": advisory.advisory_id,
                       "source_action_id": record["original_proposal"]["action_id"],
                       "input_hash": snapshot.snapshot_id, "validation_receipt_hash": receipt_hash})
        record["decision_id"] = digest({"advisory_id": advisory.advisory_id,
                                        "proposal": record["original_proposal"],
                                        "receipt_hash": receipt_hash})
    approved = [r for r in records if r["execution_status"] == "full"]
    suppressed = [r for r in records if r["execution_status"] != "full"]
    decision = empty_decision()
    decision.update({
        "decision_id": digest({"advisory": advisory.advisory_id, "receipt": receipt_hash}),
        "status": "review_required" if common or suppressed else ("approved" if approved else "suppressed"),
        "approved_actions": approved, "suppressed_actions": suppressed,
        "unresolved_conflicts": ([{"type": "advisory_review_required", "reason": r} for r in common]
                                 + [{"type": "advisory_review_required", "position_id": a["position_id"],
                                     "reason": ";".join(a["validation_reasons"])} for a in suppressed]),
        "cash_schedule": {"scope": "confirmed_account_cash_only", "status": integrity.get("status", "incomplete"),
                          "accounts": deepcopy(integrity.get("accounts") or {}),
                          "input_hash": integrity.get("input_hash"),
                          "not_a_portfolio_cash_plan": True},
        "source_advisory_id": advisory.advisory_id, "input_hash": snapshot.snapshot_id,
        "validation_receipt_hash": receipt_hash,
    })
    lineage = {"schema_version": 1, "author": "llm", "source_advisory_id": advisory.advisory_id,
               "snapshot": frozen_snapshot, "snapshot_hash": snapshot.snapshot_id,
               "attempts": deepcopy(attempts), "advisory": parsed,
               "validation_receipt": final_receipt, "validation_receipt_hash": receipt_hash,
               "portfolio_decision": decision}
    lineage["content_hash"] = digest(lineage)
    return lineage
