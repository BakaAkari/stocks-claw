"""Read frozen results only. Strategy metrics are outside this first slice."""

import json
from collections import Counter


def build_report(db):
    """No external signal/policy join, re-evaluation, or database writes."""
    # Results, task state and attempts must come from one SQLite snapshot even
    # when another worker commits while this report is being read.
    db.execute("SAVEPOINT evaluation_report_snapshot")
    try:
        return _read_report(db)
    finally:
        db.execute("RELEASE SAVEPOINT evaluation_report_snapshot")


def _read_report(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    legacy_count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] if "tasks" in tables else 0
    if "task_definitions" not in tables:
        return {"schema_version": 2, "feedback_enabled": False, "results": [], "tasks": [],
                "summary": {}, "legacy_tasks_excluded": legacy_count,
                "scope": "raw_price_observation_only"}
    results = [json.loads(row[0]) for row in db.execute(
        "SELECT payload FROM evaluation_results ORDER BY signal_id,policy_id,target_at")]
    tasks = []
    for sid, pid, target, state, definition in db.execute("""
        SELECT t.signal_id,t.policy_id,t.target_at,t.state,d.payload
        FROM tasks t JOIN task_definitions d USING(signal_id,policy_id,target_at)
        ORDER BY t.signal_id,t.policy_id,t.target_at
    """):
        attempt = db.execute("""SELECT attempted_at,payload FROM observation_attempts
            WHERE signal_id=? AND policy_id=? AND target_at=? ORDER BY attempt_id DESC LIMIT 1""",
            (sid, pid, target)).fetchone()
        count = db.execute("""SELECT COUNT(*) FROM observation_attempts
            WHERE signal_id=? AND policy_id=? AND target_at=?""", (sid, pid, target)).fetchone()[0]
        frozen = json.loads(definition)
        tasks.append({"task_key": [sid, pid, target], "state": state,
                      "status": "retrying" if state == "scheduled" and attempt else (
                          "pending" if state in ("scheduled", "claimed") else state),
                      "input_hash": frozen["input_hash"], "attempt_count": count,
                      "last_attempt_at": attempt[0] if attempt else None,
                      "last_observation": json.loads(attempt[1]) if attempt else None})
    return {"schema_version": 2, "feedback_enabled": False, "results": results,
            "tasks": tasks, "summary": dict(Counter(row["status"] for row in tasks)),
            "legacy_tasks_excluded": legacy_count - len(tasks),
            "scope": "raw_price_observation_only"}
