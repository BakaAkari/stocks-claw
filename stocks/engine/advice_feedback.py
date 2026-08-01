"""M3 advice feedback: marks, weekly rollup, snapshot projection.

Feedback is user-marked outcome evidence (`accepted | partial | rejected |
deferred`) recorded on the advice ledger.  The rollup is a pure function
over ledger records — it informs future Outlook runs as evidence and never
tunes rules or parameters.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

FEEDBACK_STATUSES = frozenset({"accepted", "partial", "rejected", "deferred"})

# Statuses that count as "the user acted against / partly against the advice"
# when surfacing rejection notes.
_NEGATIVE_STATUSES = ("rejected", "partial")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_feedback(status: str, note: str = "") -> dict:
    """Validate and stamp a feedback mark."""
    if status not in FEEDBACK_STATUSES:
        raise ValueError(
            f"feedback status must be one of {sorted(FEEDBACK_STATUSES)}, got '{status}'"
        )
    if not isinstance(note, str):
        raise ValueError("feedback note must be a string")
    return {"status": status, "note": note.strip(), "marked_at": _iso_now()}


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def compute_feedback_rollup(
    records: list[dict],
    *,
    window_days: int = 7,
    now: Optional[datetime] = None,
) -> dict:
    """Summarize feedback marks over a trailing window.

    Pure function.  Returns an honest zero-state rollup when the ledger or
    the window is empty.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    window_start = current - timedelta(days=window_days)

    in_window: list[dict] = []
    for record in records:
        created = _parse_dt(str(record.get("created_at") or ""))
        if created is not None and window_start <= created <= current:
            in_window.append(record)

    by_status = {status: 0 for status in sorted(FEEDBACK_STATUSES)}
    unmarked = 0
    rejection_notes: list[tuple[str, str]] = []  # (created_at, note) newest first
    oldest_unmarked: Optional[str] = None

    for record in sorted(in_window, key=lambda r: str(r.get("created_at") or ""), reverse=True):
        feedback = record.get("feedback")
        if isinstance(feedback, dict) and feedback.get("status") in FEEDBACK_STATUSES:
            by_status[feedback["status"]] += 1
            note = str(feedback.get("note") or "").strip()
            if feedback["status"] in _NEGATIVE_STATUSES and note:
                rejection_notes.append((str(record.get("created_at") or ""), note))
        else:
            unmarked += 1
            created = str(record.get("created_at") or "")
            if oldest_unmarked is None or created < oldest_unmarked:
                oldest_unmarked = created

    marked_total = sum(by_status.values())
    if marked_total:
        acceptance_rate = round(
            (by_status["accepted"] + 0.5 * by_status["partial"]) / marked_total, 3,
        )
    else:
        acceptance_rate = None

    return {
        "window_days": window_days,
        "generated_at": current.isoformat(),
        "total_in_window": len(in_window),
        "by_status": by_status,
        "unmarked": unmarked,
        "marked_total": marked_total,
        "acceptance_rate": acceptance_rate,
        "recent_rejection_notes": [
            {"created_at": created, "note": note}
            for created, note in rejection_notes[:3]
        ],
        "oldest_unmarked": oldest_unmarked,
    }


def summarize_record_for_snapshot(record: dict) -> dict:
    """Compact projection of one ledger record for the advisory snapshot."""
    feedback = record.get("feedback")
    status = "unmarked"
    note = ""
    if isinstance(feedback, dict) and feedback.get("status") in FEEDBACK_STATUSES:
        status = feedback["status"]
        note = str(feedback.get("note") or "")
    instruments = [
        f"{item.get('market')}:{item.get('code')}"
        for item in record.get("instruments") or []
        if isinstance(item, dict) and item.get("market") and item.get("code")
    ]
    summary: dict[str, Any] = {
        "created_at": str(record.get("created_at") or ""),
        "instruments": instruments,
        "direction": dict(record.get("direction") or {}),
        "feedback_status": status,
    }
    if note:
        summary["feedback_note"] = note
    performance = record.get("performance")
    if isinstance(performance, list) and performance:
        summary["performance"] = performance
    execution = record.get("execution_review")
    if isinstance(execution, list) and execution:
        summary["execution_review"] = execution
    return summary
