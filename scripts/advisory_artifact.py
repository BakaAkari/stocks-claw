"""Standard-library-only integrity checks at the report delivery boundary.

These content hashes detect drift/corruption, not a malicious writer who can
replace an entire artifact. They never authorize deployment or validate returns.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

PRIMARY_SESSIONS = frozenset({"cn_post_open", "cn_after_close", "us_post_open", "us_after_close"})
AUTHORITY = "llm_proposal_with_deterministic_validation"


def _digest(value):
    # Same canonical encoding as evaluation_contract.digest, without importing
    # stocks (whose package initializer loads the engine and pandas).
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError("advisory integrity: " + message)


def validate_advisory_artifact(artifact, *, require_primary=False):
    """Fail closed for declared Advisory artifacts and current delivery sessions.

    Legacy artifacts remain available to offline rendering tests/tools only.
    Every delivery entrypoint must pass require_primary=True.
    """
    _require(isinstance(artifact, dict), "artifact must be an object")
    required = require_primary and artifact.get("session") in PRIMARY_SESSIONS
    declared = "advisory_lineage" in artifact or "decision_authority" in artifact
    if not (required or declared):
        return
    try:
        _validate(artifact)
    except (TypeError, KeyError, AttributeError, IndexError, ArithmeticError) as exc:
        raise ValueError("advisory integrity: malformed lineage") from exc


def _validate(artifact):
    _require(artifact.get("decision_authority") == AUTHORITY, "missing decision authority")
    lineage = artifact.get("advisory_lineage")
    _require(isinstance(lineage, dict), "missing lineage")
    _require(lineage.get("schema_version") == 1 and lineage.get("author") == "llm", "unsupported lineage")
    _require(lineage.get("content_hash") == _digest({k: v for k, v in lineage.items() if k != "content_hash"}),
             "lineage content hash mismatch")
    decision = artifact.get("portfolio_decision")
    _require(isinstance(decision, dict), "missing decision")
    frozen_decision = lineage.get("portfolio_decision")
    _require({k: v for k, v in decision.items() if k != "user_view"} == frozen_decision,
             "decision differs from frozen proposal")
    view = decision.get("user_view")
    display = lineage.get("deterministic_display") or {}
    _require(isinstance(view, dict) and display.get("user_view") == view
             and display.get("content_hash") == _digest(view), "display hash mismatch")
    _require(display.get("source_advisory_id") == lineage.get("source_advisory_id")
             and display.get("portfolio_decision_id") == decision.get("decision_id"), "display source mismatch")
    approved = decision.get("approved_actions")
    suppressed = decision.get("suppressed_actions")
    _require(isinstance(approved, list) and isinstance(suppressed, list), "invalid decision actions")
    _require(artifact.get("action_cards", []) == approved, "action cards differ from approved proposals")
    actions = (view.get("instruction_card") or {}).get("actions")
    _require(isinstance(actions, list), "invalid display actions")
    approved_by_id = {a["decision_id"]: a for a in approved}
    _require(len(approved_by_id) == len(approved), "duplicate approved action")
    _require(len({a.get("decision_id") for a in actions}) == len(actions), "duplicate display action")
    for action in actions:
        source = approved_by_id.get(action.get("decision_id"))
        _require(source is not None, "display action was not approved")
        fee = source.get("fee_budget_check") or {}
        expected_cost = ({key: fee.get(key) for key in ("currency", "fee_upper_bound", "total_cash_required")}
                         if fee.get("status") == "covered" else None)
        _require(action.get("purchase_cost") == expected_cost, "display purchase cost mismatch")
        for key in ("source_advisory_id", "source_action_id", "validation_receipt_hash", "input_hash",
                    "signal", "executable_quantity", "final_ratio", "execution_status"):
            _require(action.get(key) == source.get(key), "display action mismatch: " + key)
    # A renderer may not silently drop an approved action either.
    _require({a.get("decision_id") for a in actions} == set(approved_by_id), "approved action missing from display")

    snapshot = lineage.get("snapshot")
    if snapshot is not None:
        snapshot_hash = _digest({k: v for k, v in snapshot.items() if k != "snapshot_id"})
        _require(snapshot.get("snapshot_id") == snapshot_hash == lineage.get("snapshot_hash"),
                 "snapshot hash mismatch")
        _require(snapshot.get("session") == artifact.get("session"), "snapshot session mismatch")

    advisory = lineage.get("advisory")
    if advisory is None:
        _require(lineage.get("status") == "unavailable" and not approved and not suppressed
                 and decision.get("status") == "review_required", "unavailable proposal has actions")
        return

    _require(isinstance(snapshot, dict), "proposal snapshot missing")
    receipt = lineage.get("validation_receipt")
    receipt_hash = _digest(receipt)
    _require(isinstance(receipt, dict) and lineage.get("validation_receipt_hash") == receipt_hash,
             "validation receipt hash mismatch")
    _require(receipt.get("snapshot_hash") == lineage.get("snapshot_hash")
             and receipt.get("advisory_content_hash") == _digest(advisory), "receipt input mismatch")
    _require(advisory.get("advisory_id") == lineage.get("source_advisory_id")
             == decision.get("source_advisory_id"), "advisory identity mismatch")
    _require(decision.get("input_hash") == lineage.get("snapshot_hash")
             and decision.get("validation_receipt_hash") == receipt_hash, "decision receipt mismatch")
    _require(decision.get("decision_id") == _digest({"advisory": lineage["source_advisory_id"],
                                                    "receipt": receipt_hash}), "decision identity mismatch")
    attempts = lineage.get("attempts") or []
    for attempt in attempts:
        if "prompt" in attempt:
            _require(attempt.get("prompt_hash") == _digest(attempt["prompt"]), "prompt hash mismatch")
    _require(receipt.get("prompt_hash") == (attempts[-1].get("prompt_hash") if attempts else None),
             "receipt prompt mismatch")
    originals = {a["action_id"]: a for a in advisory.get("actions", [])}
    validations = {a["action_id"]: a for a in receipt.get("actions", [])}
    expected = {key for key, value in originals.items() if value.get("action") in {"buy", "add", "sell", "reduce"}}
    records = approved + suppressed
    _require(len({a.get("source_action_id") for a in records}) == len(records)
             and {a.get("source_action_id") for a in records} == expected == set(validations),
             "proposal validation coverage mismatch")
    for action in records:
        original = originals.get(action.get("source_action_id"))
        _require(original is not None and action.get("original_proposal") == original, "original proposal mismatch")
        _require(action.get("signal") == original.get("action"), "proposal direction changed")
        _require(action.get("decision_id") == _digest({"advisory_id": lineage["source_advisory_id"],
                                                       "proposal": original, "receipt_hash": receipt_hash}),
                 "action identity mismatch")
        _require(action.get("input_hash") == lineage["snapshot_hash"]
                 and action.get("validation_receipt_hash") == receipt_hash
                 and action.get("source_advisory_id") == lineage["source_advisory_id"], "action source mismatch")
        validation = validations.get(action["source_action_id"], {})
        _require(validation.get("status") == action.get("execution_status")
                 and validation.get("reasons") == action.get("validation_reasons"), "action validation mismatch")
        if action in approved:
            _require(action.get("execution_status") == "full" and not action.get("validation_reasons")
                     and not receipt.get("common_reasons"), "unvalidated approved action")
            _require(original.get("size_type") == "shares"
                     and Decimal(str(original.get("size"))) == Decimal(str(action.get("executable_quantity"))),
                     "approved quantity differs from proposal")
        else:
            _require(action.get("execution_status") != "full", "approved action incorrectly suppressed")
