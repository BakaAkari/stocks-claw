"""The sole task-definition entry point; no default directional horizons."""

import json
from collections import Counter
from pathlib import Path

from stocks.engine.evaluation_contract import normalize_policy, utc, validate_signal
from stocks.engine.exchange_targets import target_after


def schedule_signal(ledger, signal, window):
    reason = validate_signal(signal)
    if reason:
        raise ValueError(reason)
    market = signal["symbol"].split(":", 1)[0]
    policy = normalize_policy(market, window)
    old = ledger.definition(signal["signal_id"], policy["policy_id"])
    # Calendar-library updates must not move previously frozen targets.
    target = old["target"] if old else target_after(
        utc(signal["generated_at"]), market, policy["horizon"], basis=policy["basis"])
    return ledger.freeze_task(signal, policy, target)


def sync_signal_events(signals_file, ledger, *, windows):
    """Schedule explicit windows from new events; legacy data is only counted."""
    for market, definitions in windows.items():
        for window in definitions:
            normalize_policy(market, window)
    summary = {"read": 0, "scheduled": 0, "existing_or_duplicate": 0,
               "skipped": 0, "skip_reasons": Counter()}
    path = Path(signals_file)
    if not path.exists():
        raise FileNotFoundError(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        summary["read"] += 1
        try:
            signal = json.loads(line)
        except json.JSONDecodeError:
            signal = None
        reason = validate_signal(signal)
        if reason:
            summary["skipped"] += 1
            summary["skip_reasons"][reason] += 1
            continue
        definitions = windows.get(signal["symbol"].split(":", 1)[0], [])
        if not definitions:
            summary["skipped"] += 1
            summary["skip_reasons"]["no_configured_window"] += 1
        for window in definitions:
            _, created = schedule_signal(ledger, signal, window)
            summary["scheduled" if created else "existing_or_duplicate"] += 1
    summary["skip_reasons"] = dict(summary["skip_reasons"])
    return summary
