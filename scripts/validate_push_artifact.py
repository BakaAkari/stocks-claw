#!/usr/bin/env python3
"""Validate push artifact before delivery. Fail-closed on trust-boundary violations."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse outlook leak-scan helpers (no evidence needed)
from stocks.engine.outlook_validation import (
    _check_internal_tokens,
    _check_trade_instructions,
)
from stocks.engine.presentation import (
    project_outlook_delta_for_display,
    project_outlook_for_display,
)

TRUSTED_FIELDS = {
    "window_delta", "portfolio_decision", "risk_state",
    "data_boundaries", "research_candidates",
}


def fail(message: str) -> int:
    print(f"INVALID: {message}", file=sys.stderr)
    return 1


def parse_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--now", required=True)
    args = parser.parse_args()

    path = Path(args.artifact)
    if not path.exists():
        return fail(f"Artifact not found: {path}")

    raw = path.read_text(encoding="utf-8")
    try:
        artifact = json.loads(raw)
    except json.JSONDecodeError:
        return fail("invalid JSON")

    # ── Schema / version checks ─────────────────────────────────────
    if artifact.get("schema_version") != 1:
        return fail(f"unexpected schema_version: {artifact.get('schema_version')}")

    agent_task = artifact.get("agent_task") or {}
    if agent_task.get("task_version", 0) < 5:
        return fail(f"task_version must be >= 5, got {agent_task.get('task_version')}")

    generated = parse_dt(str(artifact.get("generated_at") or ""))
    if generated is None:
        return fail("invalid timestamp: generated_at")

    scheduled = parse_dt(str(artifact.get("scheduled_for") or ""))
    if scheduled is None:
        return fail("invalid timestamp: scheduled_for")

    now = parse_dt(args.now)
    if now is None:
        return fail(f"invalid --now timestamp: {args.now}")

    if artifact.get("session") != args.session:
        return fail(f"session mismatch: expected {args.session}, got {artifact.get('session')}")

    # ── Market-date sanity (allow US sessions) ──────────────────────
    market = str(artifact.get("market") or "")
    market_date = str(artifact.get("market_date") or "")
    if market == "cn":
        expected = now.strftime("%Y-%m-%d")
        if market_date != expected:
            return fail(f"market_date mismatch for CN session: {market_date} != {expected}")
    else:
        expected = now.strftime("%Y-%m-%d")
        yesterday = (now - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
        if market_date not in (expected, yesterday):
            return fail(f"market_date out of range for {market} session: {market_date}")

    # ── Age check ───────────────────────────────────────────────────
    max_age_seconds = 24 * 3600
    age = (now - generated).total_seconds()
    if age < 0:
        return fail("artifact generated in the future")
    if age > max_age_seconds:
        return fail(f"artifact age is {age / 3600:.1f}h (more than a day old)")

    # ── Data-reference contract ─────────────────────────────────────
    data_ref = agent_task.get("data_reference", {})
    for field in TRUSTED_FIELDS:
        if field not in data_ref:
            return fail(f"data_reference missing required field: {field}")

    for field in data_ref:
        if field not in TRUSTED_FIELDS:
            return fail(f"data_reference has unknown field: {field}")

    # ── portfolio_decision.user_view contract ───────────────────────
    decision = artifact.get("portfolio_decision", {})
    user_view = decision.get("user_view")
    if not isinstance(user_view, dict):
        return fail("portfolio_decision.user_view missing")

    if "instruction_card" not in user_view:
        return fail("portfolio_decision.user_view.instruction_card missing")
    if "assistant_brief" not in user_view:
        return fail("portfolio_decision.user_view.assistant_brief missing")

    if not isinstance(user_view["assistant_brief"], dict):
        return fail("portfolio_decision.user_view.assistant_brief missing")

    # ── Task 7: Validate outlook content via projection equality ────
    outlook = user_view["assistant_brief"].get("outlook")
    structured = artifact.get("structured_outlook")

    if outlook is not None:
        # fail closed: user has outlook but no top-level structured_outlook
        if not isinstance(structured, dict):
            return fail(
                "user_view has outlook but no top-level structured_outlook"
            )
        projected = project_outlook_for_display(structured)
        if projected != outlook:
            return fail(
                "assistant_brief.outlook != project_outlook_for_display(structured_outlook)"
            )
        # extra security: scan for internal tokens / trade instructions
        errors: list[str] = []
        _check_internal_tokens(outlook, errors)
        _check_trade_instructions(outlook, errors)
        if errors:
            return fail("outlook validation: " + "; ".join(errors))

    # ── Task 7: Validate outlook_delta via projection equality ──────
    outlook_delta = user_view["assistant_brief"].get("outlook_delta")
    if outlook_delta is not None:
        if not isinstance(outlook_delta, dict):
            return fail("assistant_brief.outlook_delta must be a dict")
        projected_delta = project_outlook_delta_for_display(outlook_delta)
        if projected_delta != outlook_delta:
            return fail(
                "assistant_brief.outlook_delta != project_outlook_delta_for_display(outlook_delta)"
            )


    print("VALID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
