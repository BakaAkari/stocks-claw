#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

TRUSTED_FIELDS = {
    "window_delta", "portfolio_decision", "risk_state",
    "data_boundaries", "research_candidates",
}


def parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def fail(message: str) -> int:
    print(f"INVALID: {message}", file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--now", required=True)
    args = parser.parse_args()

    path = Path(args.artifact)
    if not path.is_file():
        return fail(f"artifact missing: {path}")
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"artifact unreadable: {exc}")

    if artifact.get("session") != args.session:
        return fail(f"session mismatch: {artifact.get('session')} != {args.session}")
    task_version = (artifact.get("agent_task") or {}).get("task_version")
    if task_version != 5:
        return fail(f"task_version must be 5, got {task_version}")
    missing = TRUSTED_FIELDS - set(artifact)
    if missing:
        return fail(f"trusted fields missing: {sorted(missing)}")

    try:
        now = parse_dt(args.now)
        scheduled = parse_dt(str(artifact.get("scheduled_for") or ""))
        generated = parse_dt(str(artifact.get("generated_at") or ""))
    except (TypeError, ValueError) as exc:
        return fail(f"invalid timestamp: {exc}")
    expected_date = scheduled.date().isoformat()
    if artifact.get("market_date") != expected_date:
        return fail(
            f"market_date mismatch: artifact={artifact.get('market_date')} "
            f"scheduled={expected_date}"
        )
    if generated < scheduled:
        return fail(
            f"generated_at {generated.isoformat()} precedes scheduled_for "
            f"{scheduled.isoformat()}"
        )
    if generated > now:
        return fail(f"generated_at {generated.isoformat()} is in the future")
    age_minutes = (now.astimezone(generated.tzinfo) - generated).total_seconds() / 60
    if age_minutes > 30:
        return fail(f"artifact age {age_minutes:.1f} minutes exceeds 30-minute limit")

    print(f"VALID {artifact.get('run_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
