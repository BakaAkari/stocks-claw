import copy
import sqlite3
import json
import pytest
from stocks.engine.evaluation_metrics import evaluate,build_report,summarize
from stocks.engine.evaluation_ledger import EvaluationLedger


def sample():
 s={'signal_id':'s','symbol':'a:1','signal_type':'short_term_directional','direction':'buy','scoring_status':'scorable','corporate_actions_checked':True,
 'entry_snapshot':{'scoring_status':'scorable','instrument_id':'a:1','price':100,'currency':'CNY','adjustment':'unadjusted','source':'fixture','as_of':'2026-01-01T00:00:00Z'}}
 o={'status':'observed','symbol':'a:1','price':110,'currency':'CNY','adjustment':'unadjusted','source':'fixture','observed_at':'2026-01-02T00:00:00Z','target_at':'2026-01-02T00:00:00Z'}
 return s,o,{'neutral_band':.001,'round_trip_cost_rate':.002}

def test_returns_and_costs():
 s,o,p=sample();r=evaluate(s,o,p)
 assert r['gross_return']==pytest.approx(.1) and r['net_return']==pytest.approx(.098)
 s['direction']='sell'; assert evaluate(s,o,p)['outcome']=='loss'
 p['round_trip_cost_rate']=None;r=evaluate(s,o,p);assert r['net_return'] is None and r['outcome_basis']=='gross'

@pytest.mark.parametrize('field,value,reason', [('signal_type','risk_reduction','unsupported_signal_semantics'),('corporate_actions_checked',False,'corporate_actions_unverified')])
def test_semantic_guards(field,value,reason):
 s,o,p=sample();s[field]=value;assert evaluate(s,o,p)['reason']==reason

@pytest.mark.parametrize('field,value,reason',[('currency',None,'currency_unverified'),('adjustment',None,'price_basis_unverified'),('price',float('nan'),'invalid_price'),('source',None,'missing_source')])
def test_entry_guards(field,value,reason):
 s,o,p=sample();s['entry_snapshot'][field]=value;assert evaluate(s,o,p)['reason']==reason

def test_neutral_small_sample():
 s,o,p=sample();o['price']=100.2;r=evaluate(s,o,p);assert r['outcome']=='neutral'
 r['policy_id']='p';g=summarize([r],20)[0]
 assert g['neutral']==1 and g['win_rate'] is None and g['expectancy'] is None and g['max_drawdown'] is None

def test_unique_read_and_conflict(tmp_path):
 s,o,p=sample();l=EvaluationLedger(tmp_path/'x.db');key=l.schedule('s','observation-v1:p',o['target_at']);token=l.claim(key,0);l.finish(key,token,o,1)
 with sqlite3.connect(l.path) as db:
  a=build_report(db,[s,s],p,2);b=build_report(db,[s,s],p,2);assert a==b and len(a['results'])==1
  bad=copy.deepcopy(s);bad['direction']='sell'
  assert build_report(db,[s,bad],p,2)['results'][0]['reason']=='conflicting_signal_id'
