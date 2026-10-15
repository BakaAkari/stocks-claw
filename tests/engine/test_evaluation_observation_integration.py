from datetime import datetime
from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.evaluation_runner import run_evaluation
from stocks.engine.observation_calendar import next_observation_target, observation_status

def test_task_target_uses_explicit_observation_and_result_keeps_times(tmp_path):
    l=EvaluationLedger(tmp_path/'x.db'); generated=datetime.fromisoformat('2026-10-16T20:00:00+00:00')
    target=next_observation_target(generated,'a',holidays={'2026-10-19'})
    key=('s','a-close','2026-10-20T07:00:00+00:00'); assert l.schedule(*key)==key
    result={'target_at':target.isoformat(),'observed_at':'2026-10-20T07:05:00+00:00','price':10,'status':observation_status(target,datetime.fromisoformat('2026-10-20T07:05:00+00:00'))['status']}
    token=l.claim(key,target.timestamp()+1);assert token
    assert l.finish(key,token,result,target.timestamp()+2)
    assert l.get(key)['result']==result
