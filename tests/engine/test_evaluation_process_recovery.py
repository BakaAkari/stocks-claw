"""Real OS process crash and contention, no production ledger access."""
import os
import multiprocessing as mp
from stocks.engine.evaluation_ledger import EvaluationLedger


def crash_after_claim(path, key):
    ledger = EvaluationLedger(path)
    assert ledger.claim(key, 100, 10)
    os._exit(23)


def contend(path, key, output):
    ledger = EvaluationLedger(path)
    token = ledger.claim(key, 200, 60)
    output.put(bool(token and ledger.finish(key, token, {'winner': os.getpid()}, 201)))


def crash_uncommitted(path, key):
    import sqlite3
    db = sqlite3.connect(path)
    db.execute("UPDATE tasks SET state='settled', result='{}' WHERE signal_id=? AND policy_id=? AND target_at=?",key)
    os._exit(24)


def test_actual_crash_and_concurrent_recovery(tmp_path):
    ctx = mp.get_context('spawn')
    path = str(tmp_path/'ledger.db')
    ledger = EvaluationLedger(path)
    key = ledger.schedule('s','p','2026-10-16T00:00:00Z')
    worker = ctx.Process(target=crash_after_claim,args=(path,key))
    worker.start();worker.join(15)
    assert worker.exitcode == 23
    assert ledger.claim(key,109) is None
    queue = ctx.Queue()
    workers = [ctx.Process(target=contend,args=(path,key,queue)) for _ in range(6)]
    for worker in workers: worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert sum(queue.get(timeout=5) for _ in workers) == 1
    assert ledger.get(key)['state'] == 'settled'


def test_killed_transaction_rolls_back(tmp_path):
    ctx=mp.get_context('spawn')
    path=str(tmp_path/'ledger.db')
    ledger=EvaluationLedger(path)
    key=ledger.schedule('s','p','2026-10-16T00:00:00Z')
    worker=ctx.Process(target=crash_uncommitted,args=(path,key))
    worker.start();worker.join(15)
    assert worker.exitcode == 24
    assert EvaluationLedger(path).get(key)=={'state':'scheduled','result':None}
