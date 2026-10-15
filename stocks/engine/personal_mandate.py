"""Personal goals and constraints from explicit facts, without inferred preferences.

This module is pure: callers provide the documents and their provenance. A
reference default or legacy document never becomes a confirmed user instruction.
"""

from copy import deepcopy
from datetime import datetime
from math import isfinite

from stocks.engine.evaluation_contract import digest

_FIELDS = (
    "goals", "horizon", "loss_capacity", "cash_needs", "operation_frequency", "style_preferences",
)
_SOURCE_KINDS = {"local_confirmed", "legacy_unverified", "reference_default", "missing"}


def _aware_time(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be an aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{where} must be an aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{where} must be an aware ISO timestamp")


def _nonempty_string(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty string")


def _nonnegative_amount(value, where):
    try:
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and isfinite(value) and value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{where} must be a finite non-negative number")


def validate_mandate_update(mandate: dict) -> dict:
    """Validate a partial mandate update and return a detached copy.

    Absent fields are not supplied. Explicit null clears a previously known
    value; empty cash-needs/preferences lists are explicit user statements.
    """
    if not isinstance(mandate, dict):
        raise ValueError("mandate must be an object")
    unknown = set(mandate) - set(_FIELDS)
    if unknown:
        raise ValueError(f"unsupported mandate fields: {sorted(unknown)}")
    result = deepcopy(mandate)
    for key in ("goals", "horizon", "operation_frequency"):
        if key in result and result[key] is not None and not isinstance(result[key], str):
            raise ValueError(f"mandate.{key} must be a string or null")
    loss = result.get("loss_capacity")
    if loss is not None:
        if not isinstance(loss, dict) or set(loss) != {"amount", "currency"}:
            raise ValueError("mandate.loss_capacity must contain amount and currency")
        _nonnegative_amount(loss["amount"], "mandate.loss_capacity.amount")
        _nonempty_string(loss["currency"], "mandate.loss_capacity.currency")
        loss["currency"] = loss["currency"].strip().upper()
    cash_needs = result.get("cash_needs")
    if cash_needs is not None:
        if not isinstance(cash_needs, list):
            raise ValueError("mandate.cash_needs must be an array or null")
        for index, need in enumerate(cash_needs):
            where = f"mandate.cash_needs[{index}]"
            if not isinstance(need, dict) or set(need) != {"account_id", "currency", "amount", "needed_at"}:
                raise ValueError(f"{where} must contain account_id, currency, amount and needed_at")
            _nonempty_string(need["account_id"], where + ".account_id")
            _nonempty_string(need["currency"], where + ".currency")
            need["currency"] = need["currency"].strip().upper()
            _nonnegative_amount(need["amount"], where + ".amount")
            _aware_time(need["needed_at"], where + ".needed_at")
    preferences = result.get("style_preferences")
    if preferences is not None and (
        not isinstance(preferences, list) or not all(isinstance(item, str) for item in preferences)
    ):
        raise ValueError("mandate.style_preferences must be a string array or null")
    return result


def _source(source, document):
    if not isinstance(source, dict) or source.get("kind") not in _SOURCE_KINDS:
        raise ValueError("source must declare kind: local_confirmed, legacy_unverified, reference_default or missing")
    for key in ("path", "updated_at"):
        if source.get(key) is not None and not isinstance(source[key], str):
            raise ValueError(f"source.{key} must be a string or null")
    return {
        "kind": source["kind"],
        "path": source.get("path"),
        "updated_at": source.get("updated_at"),
        "content_hash": digest(document),
    }


def build_personal_mandate(
    profile: dict, constraints: dict, *, profile_source: dict,
    constraints_source: dict, captured_at: str,
) -> dict:
    """Freeze explicit values and trust status; reading never confirms a value."""
    if not isinstance(profile, dict) or not isinstance(constraints, dict):
        raise ValueError("profile and constraints must be objects")
    _aware_time(captured_at, "captured_at")
    raw_mandate = profile.get("mandate")
    mandate = validate_mandate_update(raw_mandate if raw_mandate is not None else {})
    sources = {"profile": _source(profile_source, profile),
               "constraints": _source(constraints_source, constraints)}
    fields = {}
    for key in _FIELDS:
        value = mandate.get(key)
        source_field = f"profile.mandate.{key}" if key in mandate else None
        # Preserve an explicitly entered legacy horizon verbatim. Never infer
        # loss capacity from risk appetite or style from past operations.
        if key == "horizon" and key not in mandate and isinstance(profile.get("investment_horizon"), str):
            value = profile["investment_horizon"]
            source_field = "profile.investment_horizon"
        has_value = value is not None and (not isinstance(value, str) or bool(value.strip()))
        fields[key] = {
            "value": deepcopy(value),
            "status": "confirmed" if has_value and sources["profile"]["kind"] == "local_confirmed" else "unknown",
            "source_field": source_field,
        }
    snapshot = {
        "schema_version": 1,
        "sources": sources,
        "fields": fields,
        "missing_fields": [key for key in _FIELDS if fields[key]["status"] == "unknown"],
        "constraint_snapshot": {
            "value": deepcopy(constraints),
            "status": "confirmed" if sources["constraints"]["kind"] == "local_confirmed" else "unknown",
            "source_field": "portfolio_constraints",
        },
        "raw_profile": deepcopy(profile),
    }
    snapshot["content_hash"] = digest(snapshot)
    snapshot["captured_at"] = captured_at
    return snapshot
