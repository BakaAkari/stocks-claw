"""Confirmed-write services linking recommendations, choices and actual fills.

Adapters own the human confirmation gate, as with the existing memory APIs.
This service checks identities against frozen sources instead of guessing links.
"""

from datetime import datetime, timezone

from stocks.domain.models import AdviceRecord, ExecutionRecord, UserDecisionRecord
from stocks.engine.entry_snapshot import canonical_identity
from stocks.engine.evaluation_contract import digest, utc

_ACTION = {
    "add": "add", "increase": "increase", "buy": "add", "accumulate": "add",
    "reduce": "reduce", "sell": "reduce", "exit": "exit",
    "stop_loss": "reduce", "take_profit": "reduce", "hold": "hold", "watch": "watch",
}


def _one(records, identity):
    matches = [r for r in records if r.get("id") == identity]
    if len(matches) != 1:
        raise ValueError("record_reference_missing_or_ambiguous")
    return matches[0]


def recommendation(engine, decision_id):
    matches = [s for s in engine.decision_attribution()
               if s.get("schema_version") == 2 and s.get("decision_id") == decision_id]
    if len(matches) != 1:
        raise ValueError("frozen_recommendation_missing_or_ambiguous")
    source = matches[0]
    if digest({k: v for k, v in source.items() if k != "source_hash"}) != source.get("source_hash"):
        raise ValueError("recommendation_hash_mismatch")
    return source


def save_source_advice(engine, payload):
    if set(payload) != {"source_decision_id"}:
        raise ValueError("source advice accepts only source_decision_id")
    source = recommendation(engine, payload["source_decision_id"])
    if source.get("recommendation_status") != "approved":
        raise ValueError("recommendation_is_not_an_approved_action")
    identity = canonical_identity(source.get("instrument_id"))
    market, code = identity.split(":", 1)
    action = source["action"]
    normalized_action = _ACTION.get(action.get("signal"))
    if normalized_action is None:
        raise ValueError("unsupported_recommendation_action")
    advice_id = "advice:" + source["decision_id"]
    existing = [r for r in engine.list_advice() if r.get("id") == advice_id]
    if existing:
        if len(existing) != 1 or existing[0].get("source_snapshot") != source:
            raise ValueError("immutable_advice_source_conflict")
        return existing[0]
    original = str(action.get("action_description") or action.get("reason") or "")
    quantity = action.get("executable_quantity")
    ratio = action.get("final_ratio", action.get("ratio"))
    size = f"建议数量 {quantity}" if quantity is not None else f"原建议比例 {ratio}"
    record = AdviceRecord.create(
        schema_version=2, id=advice_id, source_run_id=source.get("source_run_id", ""),
        source_snapshot=source, original_text=original,
        instruments=[{"market": market, "code": code,
                      "name": source.get("position", {}).get("display_name") or code}],
        direction={}, rationale_summary=str(action.get("reason") or original)[:500],
        based_on=["portfolio"], boundary=[],
        actions=[{"decision_id": source["decision_id"], "target": identity,
                  "action": normalized_action, "size_hint": size, "horizon": "unspecified",
                  "trigger": original, "invalidation": action.get("cancel_condition", "")}],
    )
    engine.persistence.save_advice(record)
    return record.to_dict()


def linked_advice(engine, advice_id, decision_id):
    advice = _one(engine.list_advice(), advice_id)
    matches = [a for a in advice.get("actions", []) if a.get("decision_id") == decision_id]
    if advice.get("schema_version") != 2 or len(matches) != 1:
        raise ValueError("advice_decision_link_mismatch")
    source = recommendation(engine, decision_id)
    if advice.get("source_snapshot") != source:
        raise ValueError("advice_recommendation_source_mismatch")
    return advice, matches[0], source


def _stable_record_time(payload, existing):
    if not payload.get("id"):
        raise ValueError("explicit_event_id_required")
    old = next((r for r in existing if r.get("id") == payload["id"]), None)
    return payload.get("recorded_at") or (old or {}).get("recorded_at") or datetime.now(timezone.utc).isoformat()


def save_user_decision(engine, payload):
    if not isinstance(payload, dict):
        raise ValueError("decision payload must be an object")
    allowed = {"id", "advice_id", "decision_id", "status", "decided_at", "recorded_at", "note"}
    if set(payload) - allowed:
        raise ValueError("unsupported_decision_fields")
    _, _, source = linked_advice(engine, payload.get("advice_id"), payload.get("decision_id"))
    if utc(payload.get("decided_at")) < utc(source["generated_at"]):
        raise ValueError("decision_precedes_recommendation")
    record = UserDecisionRecord.create(**dict(
        payload, recorded_at=_stable_record_time(payload, engine.list_user_decisions())))
    engine.persistence.save_decision(record)
    return record.to_dict()


def save_linked_execution(engine, payload):
    allowed = {"schema_version", "id", "advice_id", "decision_id", "user_decision_id",
               "status", "target", "action", "side", "extent", "note", "executed_at",
               "recorded_at", "price", "executed_ratio", "quantity", "currency",
               "fee_amount", "fee_currency", "account_id", "source_run_id"}
    if payload.get("schema_version") == 3:
        allowed |= {"record_origin", "source", "unit", "settled_at", "fee_booking"}
    if set(payload) - allowed:
        raise ValueError("unsupported_fill_fields")
    if payload.get("status") != "executed":
        raise ValueError("use_decision_save_for_user_choices")
    _, _, source = linked_advice(engine, payload.get("advice_id"), payload.get("decision_id"))
    if payload.get("schema_version") == 3:
        account = (source.get("position") or {}).get("account_id")
        if not account or payload.get("account_id") != account:
            raise ValueError("execution_recommendation_account_mismatch")
        unit = ((source.get("position") or {}).get("holding") or {}).get("unit")
        if not unit or payload.get("unit") != unit:
            raise ValueError("execution_recommendation_unit_mismatch")
    target = canonical_identity(source.get("instrument_id"))
    if payload.get("target", target) != target:
        raise ValueError("execution_target_mismatch")
    if payload.get("source_run_id", source["source_run_id"]) != source["source_run_id"]:
        raise ValueError("execution_run_mismatch")
    actual_at = utc(payload.get("executed_at"))
    if actual_at < utc(source["generated_at"]):
        raise ValueError("execution_precedes_recommendation")
    currency = source.get("entry_snapshot", {}).get("currency")
    if currency and payload.get("currency") != currency:
        raise ValueError("execution_currency_mismatch")
    response_id = payload.get("user_decision_id")
    if response_id:
        response = _one(engine.list_user_decisions(), response_id)
        if (response.get("advice_id") != payload.get("advice_id")
                or response.get("decision_id") != payload.get("decision_id")):
            raise ValueError("execution_user_decision_mismatch")
        if utc(response["decided_at"]) > actual_at:
            raise ValueError("execution_precedes_linked_user_decision")
    record_payload = dict(payload)
    record_payload.update(target=target, source_run_id=source["source_run_id"],
                          recorded_at=_stable_record_time(payload, engine.list_executions()))
    # Side is an actual user-confirmed fact, allowed to differ from the advice.
    # The legacy action field is derived from that fact, never from a recommendation.
    fill_action = {"buy": "add", "sell": "reduce"}.get(payload.get("side"))
    if payload.get("action", fill_action) != fill_action:
        raise ValueError("execution_action_side_mismatch")
    record_payload["action"] = fill_action or ""
    record = ExecutionRecord.create(**record_payload)
    engine.persistence.save_execution(record)
    return record.to_dict()
