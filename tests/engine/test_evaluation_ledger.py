from concurrent.futures import ThreadPoolExecutor
from stocks.engine.evaluation_ledger import EvaluationLedger

def test_unique_claim_and_result(tmp_path):
    p=tmp_path/'ledger.db'; l=EvaluationLedger(p)
    key=l.schedule('s','policy','2026-10-16T00:00:00Z')
    for _ in range(10):assert l.schedule(*key)==key
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens=list(pool.map(lambda _:EvaluationLedger(p).claim(key,100),range(20)))
    winners=[t for t in tokens if t]
    assert len(winners)==1
    assert l.finish(key,winners[0],{'return':.1},101)
    assert not l.finish(key,winners[0],{'return':-.2},102)
    assert l.get(key)=={'state':'settled','result':{'return':.1}}
    assert l.claim(key,1000) is None

def test_abandoned_worker_and_fencing(tmp_path):
    p=tmp_path/'ledger.db';l=EvaluationLedger(p)
    key=l.schedule('s','p','target')
    old=l.claim(key,0,10)
    restarted=EvaluationLedger(p)
    assert restarted.claim(key,9) is None
    new=restarted.claim(key,10)
    assert new and new!=old
    assert not l.finish(key,old,{'wrong':True},11)
    assert restarted.finish(key,new,{'ok':True},12)
    assert EvaluationLedger(p).get(key)['result']=={'ok':True}

def test_expired_claim_cannot_finalize(tmp_path):
    l=EvaluationLedger(tmp_path/'ledger.db');key=l.schedule('s','p','target')
    token=l.claim(key,0,1)
    assert not l.finish(key,token,{},1)
