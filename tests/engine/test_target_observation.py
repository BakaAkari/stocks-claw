from datetime import datetime, timezone
from stocks.engine.target_observation import observe
from stocks.engine.observation_pipeline import collect_observation
from stocks.engine.evaluation_ledger import EvaluationLedger

T={'target_at':'2026-10-09T07:00:00+00:00','session':'2026-10-09'}
NOW=datetime(2026,10,15,tzinfo=timezone.utc)

def test_raw_not_adjusted_and_no_current_fallback():
 result=observe('a:518880',T,clock=NOW,fetch=lambda u:{'data':{'sh518880':{'qfqday':[['2026-10-09','1','2']]}}})
 assert result['status']=='unavailable' and result['price'] is None

def test_target_date_not_latest():
 data={'data':{'sh518880':{'day':[['2026-10-08','1','2'],['2026-10-09','1','3']]}}}
 result=observe('a:518880',T,clock=NOW,fetch=lambda u:data)
 assert result['price']==3 and result['time_origin']=='exchange_calendar'

def test_provider_failure_and_future():
 def fail(u): raise TimeoutError()
 assert observe('us:NEM',T,clock=NOW,fetch=fail)['reason']=='provider_error'
 assert observe('us:NEM',T,clock=datetime(2026,10,8,tzinfo=timezone.utc),fetch=fail)['status']=='not_due'

def test_pipeline_retry_expiry_and_idempotence(tmp_path):
 l=EvaluationLedger(tmp_path/'obs.db')
 sig={'signal_id':'s','symbol':'crypto:BTCUSDT','generated_at':'2026-10-08T07:00:00+00:00','scoring_status':'scorable','entry_snapshot':{'scoring_status':'scorable'}}
 win={'id':'crypto-24h','basis':'elapsed','horizon':24,'retry_seconds':86400}
 missing=lambda *a,**k:{'status':'unavailable','reason':'provider_error','price':None}
 assert collect_observation(l,sig,win,now=datetime(2026,10,9,8,tzinfo=timezone.utc),provider=missing)=='unavailable_retry'
 assert collect_observation(l,sig,win,now=NOW,provider=missing)=='settled'
 assert l.get(('s','observation-v1:crypto-24h',T['target_at']))['result']['status']=='expired'
 assert collect_observation(l,sig,win,now=NOW,provider=missing)=='not_claimed'
