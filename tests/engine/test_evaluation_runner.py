from datetime import datetime
from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.evaluation_runner import run_evaluation

TARGET='2026-10-16T00:00:00Z'
# Derive from the target so the clock can never silently drift out of sync.
DUE=datetime.fromisoformat(TARGET).timestamp()

def test_not_due_and_equivalent_offsets_share_task(tmp_path):
    l=EvaluationLedger(tmp_path/'l.db')
    assert run_evaluation(l,'s','p',TARGET,lambda:{'r':1},clock=lambda:DUE-10**6)=='not_due'
    assert run_evaluation(l,'s','p','2026-10-16T08:00:00+08:00',lambda:{'r':1},clock=lambda:DUE)=='settled'
    assert l.get(('s','p','2026-10-16T00:00:00+00:00'))['result']=={'r':1}
    assert run_evaluation(l,'s','p',TARGET,lambda:{'r':2},clock=lambda:DUE)=='not_claimed'

def test_unavailable_releases_and_caller_error_preserves_no_result(tmp_path):
    l=EvaluationLedger(tmp_path/'l.db')
    assert run_evaluation(l,'s','p',TARGET,lambda:None,clock=lambda:DUE)=='unavailable_retry'
    assert run_evaluation(l,'s','p',TARGET,lambda:{'r':1},clock=lambda:DUE)=='settled'
    assert run_evaluation(l,'s','p',TARGET,lambda:{'r':2},clock=lambda:DUE)=='not_claimed'
    assert l.get(('s','p','2026-10-16T00:00:00+00:00'))['result']=={'r':1}
    try:
        run_evaluation(l,'x','p',TARGET,lambda:(_ for _ in ()).throw(RuntimeError('boom')),clock=lambda:DUE)
    except RuntimeError:
        pass
    assert l.get(('x','p','2026-10-16T00:00:00+00:00'))['state']=='scheduled'

def test_naive_target_rejected(tmp_path):
    try:
        run_evaluation(EvaluationLedger(tmp_path/'l.db'),'s','p','2026-10-16T00:00:00',lambda:{},clock=lambda:DUE)
    except ValueError:
        pass
    else:
        raise AssertionError('naive target accepted')
