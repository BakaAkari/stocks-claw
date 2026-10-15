"""Transactional evaluation tasks. No legacy import and no price fetching."""
import json
import sqlite3
import uuid
from contextlib import contextmanager

class EvaluationLedger:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tasks (
                signal_id TEXT NOT NULL, policy_id TEXT NOT NULL, target_at TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'scheduled', token TEXT, lease_until REAL,
                result TEXT, PRIMARY KEY(signal_id, policy_id, target_at))''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def schedule(self, signal_id, policy_id, target_at):
        key = (signal_id, policy_id, target_at)
        if not all(isinstance(x,str) and x for x in key):
            raise ValueError('nonempty task identity required')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO tasks(signal_id,policy_id,target_at) VALUES (?,?,?)',key)
        return key

    def claim(self, key, now, lease_seconds=60):
        if lease_seconds <= 0:
            raise ValueError('positive lease required')
        token = uuid.uuid4().hex
        with self.connect() as db:
            cur=db.execute('''UPDATE tasks SET state='claimed',token=?,lease_until=?
                WHERE signal_id=? AND policy_id=? AND target_at=?
                AND (state='scheduled' OR (state='claimed' AND lease_until<=?))''',
                (token,now+lease_seconds,*key,now))
            return token if cur.rowcount==1 else None

    def finish(self,key,token,result,now):
        payload=json.dumps(result,sort_keys=True,allow_nan=False)
        with self.connect() as db:
            cur=db.execute('''UPDATE tasks SET state='settled',result=?,token=NULL,lease_until=NULL
                WHERE signal_id=? AND policy_id=? AND target_at=?
                AND state='claimed' AND token=? AND lease_until>?''',
                (payload,*key,token,now))
            return cur.rowcount==1

    def release(self, key, token):
        """Retry transient failures; stale workers cannot release another lease."""
        with self.connect() as db:
            cur = db.execute('''UPDATE tasks SET state='scheduled',token=NULL,lease_until=NULL
                WHERE signal_id=? AND policy_id=? AND target_at=? AND state='claimed' AND token=?''',
                (*key, token))
            return cur.rowcount == 1

    def get(self,key):
        with self.connect() as db:
            row=db.execute('SELECT state,result FROM tasks WHERE signal_id=? AND policy_id=? AND target_at=?',key).fetchone()
        return None if row is None else {'state':row[0],'result':json.loads(row[1]) if row[1] else None}
