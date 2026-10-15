"""Create normalized evaluation tasks from immutable signal events.

This module schedules work only; it never fetches prices or writes legacy JSONL.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

WINDOWS = {"24h": timedelta(hours=24), "1w": timedelta(days=7)}


def _utc(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("generated_at timezone required")
    return dt.astimezone(timezone.utc)


def sync_signal_events(signals_file, ledger, *, policy_prefix="directional"):
    """Schedule each valid signal/window once; return an auditable summary."""
    path = Path(signals_file)
    summary = {"read": 0, "scheduled": 0, "existing_or_duplicate": 0,
               "skipped_unscorable": 0, "skipped_malformed": 0}
    if not path.exists():
        return summary
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        summary["read"] += 1
        try:
            row = json.loads(line)
            signal_id = row["signal_id"]
            generated = _utc(row["generated_at"])
            if row.get("scoring_status") != "scorable":
                summary["skipped_unscorable"] += 1
                continue
            if not row.get("entry_snapshot", {}).get("scoring_status") == "scorable":
                summary["skipped_unscorable"] += 1
                continue
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            summary["skipped_malformed"] += 1
            continue
        for window, delta in WINDOWS.items():
            policy = f"{policy_prefix}:{window}"
            target = (generated + delta).isoformat()
            before = ledger.get((signal_id, policy, target))
            ledger.schedule(signal_id, policy, target)
            if before is None:
                summary["scheduled"] += 1
            else:
                summary["existing_or_duplicate"] += 1
    return summary
