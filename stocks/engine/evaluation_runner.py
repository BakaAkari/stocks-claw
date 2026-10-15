"""Claim, evaluate and atomically persist. Caller supplies target-price evaluator."""
import time
from datetime import datetime


def run_evaluation(ledger, signal_id, policy_id, target_at, evaluate, *, clock=time.time, lease_seconds=60):
    target = datetime.fromisoformat(target_at.replace('Z', '+00:00'))
    if target.tzinfo is None:
        raise ValueError('timezone required')
    # Canonical target prevents duplicate tasks caused by equivalent offsets.
    from datetime import timezone
    target_at = target.astimezone(timezone.utc).isoformat()
    key = ledger.schedule(signal_id, policy_id, target_at)
    now = clock()
    if now < target.timestamp():
        return 'not_due'
    token = ledger.claim(key, now, lease_seconds)
    if token is None:
        return 'not_claimed'
    try:
        result = evaluate()
        if result is None:
            ledger.release(key, token)
            return 'unavailable_retry'
        return 'settled' if ledger.finish(key, token, result, clock()) else 'lease_lost'
    except Exception:
        ledger.release(key, token)
        raise
