"""Transactional task, observation and immutable evaluation-result storage."""

import json
import sqlite3
import uuid
from contextlib import contextmanager

from stocks.engine.evaluation_contract import canonical_json, digest


class EvaluationLedger:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    signal_id TEXT NOT NULL, policy_id TEXT NOT NULL, target_at TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'scheduled', token TEXT, lease_until REAL,
                    result TEXT, PRIMARY KEY(signal_id, policy_id, target_at));
                CREATE TABLE IF NOT EXISTS frozen_signals (
                    signal_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS frozen_policies (
                    policy_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS task_definitions (
                    signal_id TEXT NOT NULL, policy_id TEXT NOT NULL, target_at TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(signal_id, policy_id));
                CREATE TABLE IF NOT EXISTS observation_attempts (
                    attempt_id INTEGER PRIMARY KEY, signal_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL, target_at TEXT NOT NULL,
                    attempted_at REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluation_results (
                    signal_id TEXT NOT NULL, policy_id TEXT NOT NULL, target_at TEXT NOT NULL,
                    result_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, frozen_at REAL NOT NULL,
                    PRIMARY KEY(signal_id, policy_id, target_at));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def schedule(self, signal_id, policy_id, target_at):
        """Legacy low-level API. New workers require a frozen task definition."""
        key = (signal_id, policy_id, target_at)
        if not all(isinstance(x, str) and x for x in key):
            raise ValueError("nonempty task identity required")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO tasks(signal_id,policy_id,target_at) VALUES (?,?,?)", key)
        return key

    def freeze_task(self, signal, policy, target):
        key = (signal["signal_id"], policy["policy_id"], target["target_at"])
        bundle = {"signal": signal, "policy": policy, "target": target}
        bundle["input_hash"] = digest(bundle)
        payload = canonical_json(bundle)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for table, field, identity, value in (
                ("frozen_signals", "signal_id", key[0], signal),
                ("frozen_policies", "policy_id", key[1], policy),
            ):
                encoded = canonical_json(value)
                row = db.execute(f"SELECT payload FROM {table} WHERE {field}=?", (identity,)).fetchone()
                if row is not None and row[0] != encoded:
                    raise ValueError(f"immutable_{field}_conflict:{identity}")
                db.execute(f"INSERT OR IGNORE INTO {table} VALUES (?,?)", (identity, encoded))
            old = db.execute("SELECT payload FROM task_definitions WHERE signal_id=? AND policy_id=?", key[:2]).fetchone()
            if old is not None:
                if old[0] != payload:
                    raise ValueError("immutable_task_definition_conflict")
                return key, False
            if db.execute("SELECT 1 FROM tasks WHERE signal_id=? AND policy_id=?", key[:2]).fetchone():
                raise ValueError("legacy_task_requires_explicit_migration")
            db.execute("INSERT INTO tasks(signal_id,policy_id,target_at) VALUES (?,?,?)", key)
            db.execute("INSERT INTO task_definitions VALUES (?,?,?,?)", (*key, payload))
        return key, True

    def definition(self, signal_id, policy_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM task_definitions WHERE signal_id=? AND policy_id=?", (signal_id, policy_id)).fetchone()
        return json.loads(row[0]) if row else None

    def keys(self):
        with self.connect() as db:
            return db.execute("SELECT signal_id,policy_id,target_at FROM task_definitions ORDER BY target_at,signal_id,policy_id").fetchall()

    def claim(self, key, now, lease_seconds=60):
        if lease_seconds <= 0:
            raise ValueError("positive lease required")
        token = uuid.uuid4().hex
        with self.connect() as db:
            cur = db.execute("""UPDATE tasks SET state='claimed',token=?,lease_until=?
                WHERE signal_id=? AND policy_id=? AND target_at=?
                AND (state='scheduled' OR (state='claimed' AND lease_until<=?))""",
                (token, now + lease_seconds, *key, now))
            return token if cur.rowcount == 1 else None

    def finish(self, key, token, result, now):
        """Legacy generic tasks cannot bypass the new result-freezing boundary."""
        payload = canonical_json(result)
        with self.connect() as db:
            cur = db.execute("""UPDATE tasks SET state='settled',result=?,token=NULL,lease_until=NULL
                WHERE signal_id=? AND policy_id=? AND target_at=?
                AND state='claimed' AND token=? AND lease_until>?
                AND NOT EXISTS (SELECT 1 FROM task_definitions d
                    WHERE d.signal_id=tasks.signal_id AND d.policy_id=tasks.policy_id)""",
                (payload, *key, token, now))
            return cur.rowcount == 1

    def record_attempt(self, key, token, observation, now, *, result=None):
        """Save attempt and release, or atomically freeze observation + result."""
        encoded = canonical_json(observation)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owned = db.execute("""SELECT 1 FROM tasks WHERE signal_id=? AND policy_id=? AND target_at=?
                AND state='claimed' AND token=? AND lease_until>?""", (*key, token, now)).fetchone()
            if owned is None:
                return False
            if result is not None:
                row = db.execute("SELECT payload FROM task_definitions WHERE signal_id=? AND policy_id=? AND target_at=?", key).fetchone()
                if row is None:
                    raise ValueError("frozen_task_required")
                from stocks.engine.evaluation_contract import evaluate_observation
                expected = evaluate_observation(json.loads(row[0]), observation)
                if canonical_json(result) != canonical_json(expected):
                    raise ValueError("result_does_not_match_frozen_inputs")
                db.execute("INSERT INTO evaluation_results VALUES (?,?,?,?,?,?)", (*key, result["result_id"], canonical_json(result), now))
                state = {"evaluated": "completed", "expired": "expired"}.get(result["status"], "unavailable")
            else:
                state = "scheduled"
            db.execute("INSERT INTO observation_attempts(signal_id,policy_id,target_at,attempted_at,payload) VALUES (?,?,?,?,?)", (*key, now, encoded))
            db.execute("UPDATE tasks SET state=?,result=?,token=NULL,lease_until=NULL WHERE signal_id=? AND policy_id=? AND target_at=?", (state, encoded if result is not None else None, *key))
        return True

    def release(self, key, token):
        with self.connect() as db:
            cur = db.execute("""UPDATE tasks SET state='scheduled',token=NULL,lease_until=NULL
                WHERE signal_id=? AND policy_id=? AND target_at=? AND state='claimed' AND token=?""", (*key, token))
            return cur.rowcount == 1

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT state,result FROM tasks WHERE signal_id=? AND policy_id=? AND target_at=?", key).fetchone()
        return None if row is None else {"state": row[0], "result": json.loads(row[1]) if row[1] else None}
