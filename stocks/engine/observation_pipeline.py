"""Consume frozen tasks and atomically finish their price observations."""

import time
from datetime import datetime, timezone

from stocks.engine.evaluation_contract import canonical_json, evaluate_observation, utc
from stocks.engine.target_observation import observe, validate_observation


def collect_observation(ledger, key, *, now=None, provider=observe, lease_seconds=60):
    """The worker cannot schedule tasks or accept new signal/window inputs."""
    definition = ledger.definition(key[0], key[1])
    if definition is None or definition["target"]["target_at"] != key[2]:
        raise ValueError("frozen_task_required")
    started = time.monotonic()
    instant = now or datetime.now(timezone.utc)

    def clock():
        return instant.timestamp() + time.monotonic() - started

    if instant < utc(key[2]):
        return "not_due"
    token = ledger.claim(key, clock(), lease_seconds)
    if token is None:
        return "not_claimed"
    signal, target, policy = (definition[k] for k in ("signal", "target", "policy"))
    try:
        try:
            # An explicit replay clock is fixed; live fetches retain their real
            # response time instead of reusing the worker's start timestamp.
            obs = provider(signal["symbol"], target, clock=now)
            if not isinstance(obs, dict):
                raise ValueError("invalid_provider_response")
            # Bad provider values must become an auditable failure, not a
            # serialization error that silently loses the attempt.
            canonical_json(obs)
            if obs.get("status") == "observed":
                reason = validate_observation(signal["symbol"], target, obs)
                if reason:
                    obs = dict(obs, status="unavailable", reason=reason)
        except Exception as exc:
            obs = {"status": "unavailable", "reason": "provider_error",
                   "error_type": type(exc).__name__, "target_at": key[2],
                   "symbol": signal["symbol"],
                   "retrieved_at": datetime.fromtimestamp(clock(), timezone.utc).isoformat()}
        deadline = utc(key[2]).timestamp() + policy["retry_seconds"]
        if obs.get("status") != "observed" and clock() >= deadline:
            obs = dict(obs, status="expired", expiry_reason="observation_retry_deadline")
        terminal = obs.get("status") in ("observed", "expired")
        result = evaluate_observation(definition, obs) if terminal else None
        if not ledger.record_attempt(key, token, obs, clock(), result=result):
            return "lease_lost"
        return result["status"] if result else "unavailable_retry"
    except Exception:
        ledger.release(key, token)
        raise


def collect_due(ledger, *, now=None, provider=observe):
    counts = {}
    for key in ledger.keys():
        status = collect_observation(ledger, key, now=now, provider=provider)
        counts[status] = counts.get(status, 0) + 1
    return counts
