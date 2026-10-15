"""Frozen-event contract for raw quoted-price changes, never strategy scores."""

import hashlib
import json
import math
from datetime import datetime, timezone

EVALUATOR_VERSION = "raw-price-observation-v1"


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def utc(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timezone_required")
    return dt.astimezone(timezone.utc)


def positive_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def normalize_policy(market, window):
    """A policy ID names an immutable definition, including retry semantics."""
    if market not in ("a", "us", "hk", "crypto"):
        raise ValueError("unsupported_market")
    if not isinstance(window, dict) or not isinstance(window.get("id"), str):
        raise ValueError("window_id_required")
    if not window["id"].strip() or window["id"] != window["id"].strip():
        raise ValueError("invalid_window_id")
    for field in ("horizon", "retry_seconds"):
        value = window.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < (1 if field == "horizon" else 0):
            raise ValueError(f"invalid_{field}")
    if window.get("basis") not in ("elapsed", "sessions"):
        raise ValueError("explicit_window_basis_required")
    if window.get("metric", "raw_price_return") != "raw_price_return":
        raise ValueError("unsupported_metric")
    unknown = set(window) - {"id", "horizon", "retry_seconds", "basis", "metric"}
    if unknown:
        raise ValueError(f"unknown_policy_fields:{','.join(sorted(unknown))}")
    return {
        "policy_id": f"{EVALUATOR_VERSION}:{market}:{window['id']}",
        "schema_version": 1, "market": market, "window_id": window["id"],
        "basis": window["basis"], "horizon": window["horizon"],
        "retry_seconds": window["retry_seconds"], "metric": "raw_price_return",
        "price_basis": "raw_quoted_price", "evaluator_version": EVALUATOR_VERSION,
    }


def validate_signal(signal):
    """Return an eligibility reason; never infer missing legacy semantics."""
    if not isinstance(signal, dict) or signal.get("schema_version") != 1:
        return "legacy_or_unknown_signal_schema"
    for field in ("signal_id", "symbol", "signal_type", "action_semantics", "strategy_id"):
        if not isinstance(signal.get(field), str) or not signal[field].strip():
            return f"missing_{field}"
    from stocks.engine.entry_snapshot import canonical_identity
    try:
        canonical_identity(signal["symbol"])
        generated = utc(signal["generated_at"])
        canonical_json(signal)
    except (KeyError, TypeError, ValueError):
        return "invalid_signal_identity_or_time"
    entry = signal.get("entry_snapshot")
    if (signal.get("scoring_status") not in ("scorable", "observation_only")
            or not isinstance(entry, dict) or entry.get("scoring_status") != "scorable"):
        return "unscorable_entry"
    if entry.get("instrument_id") != signal["symbol"]:
        return "entry_symbol_mismatch"
    if not positive_number(entry.get("price")) or not entry.get("source"):
        return "invalid_entry_price_or_source"
    try:
        if utc(entry["as_of"]) > generated:
            return "entry_after_signal"
    except (KeyError, TypeError, ValueError):
        return "invalid_entry_time"
    if entry.get("stale") is not False:
        return "stale_or_unknown_entry"
    if entry.get("price_basis") != "raw_quoted_price":
        return "entry_price_basis_unknown"
    if not isinstance(entry.get("currency"), str) or not entry["currency"]:
        return "entry_currency_unknown"
    return None


def evaluate_observation(definition, observation):
    """Build a result exclusively from the task's immutable inputs."""
    signal, policy, target = (definition[k] for k in ("signal", "policy", "target"))
    if policy.get("evaluator_version") != EVALUATOR_VERSION:
        raise ValueError("unsupported_frozen_evaluator_version")
    entry = signal["entry_snapshot"]
    status = observation.get("status", "unavailable")
    reason = observation.get("reason")
    price_return = None
    if status == "observed":
        from stocks.engine.target_observation import validate_observation
        reason = validate_observation(signal["symbol"], target, observation)
        if reason is None and entry.get("currency") != observation.get("currency"):
            reason = "currency_mismatch"
        if reason is None and observation.get("price_basis") != entry.get("price_basis"):
            reason = "price_basis_mismatch"
        if reason is None and utc(observation["observed_at"]) <= utc(entry["as_of"]):
            reason = "invalid_price_times"
        if reason is None:
            price_return = observation["price"] / entry["price"] - 1
            if not math.isfinite(price_return):
                price_return, reason = None, "nonfinite_return"
        status = "evaluated" if reason is None else "unavailable"
    elif status != "expired":
        status = "unavailable"
    result = {
        "schema_version": 1, "evaluator_version": EVALUATOR_VERSION,
        "signal_id": signal["signal_id"], "policy_id": policy["policy_id"],
        "target_at": target["target_at"], "status": status, "reason": reason,
        "price_return": price_return, "currency": entry.get("currency"),
        "return_semantics": "raw_quoted_price_change_not_trade_pnl_or_total_return",
        "scoring_status": "observation_only", "outcome": None,
        "input_hash": definition["input_hash"], "observation_hash": digest(observation),
        "signal": signal, "policy": policy, "target": target,
        "entry": entry, "exit": observation,
    }
    result["result_id"] = digest(result)
    return result
