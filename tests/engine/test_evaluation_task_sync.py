import json
from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.evaluation_task_sync import sync_signal_events

def row(sid,status='scorable'):
 return {'signal_id':sid,'generated_at':'2026-10-15T00:00:00Z','scoring_status':status,'entry_snapshot':{'scoring_status':status}}

def test_sync_is_idempotent_and_skips_unscorable(tmp_path):
 f=tmp_path/'signals.jsonl';f.write_text('\n'.join(json.dumps(x) for x in [row('a'),row('a'),row('bad','unscorable'),{'signal_id':'broken'}]))
 l=EvaluationLedger(tmp_path/'eval.db')
 first=sync_signal_events(f,l);second=sync_signal_events(f,l)
 assert first=={'read':4,'scheduled':2,'existing_or_duplicate':2,'skipped_unscorable':1,'skipped_malformed':1}
 assert second=={'read':4,'scheduled':0,'existing_or_duplicate':4,'skipped_unscorable':1,'skipped_malformed':1}
 assert l.get(('a','directional:24h','2026-10-16T00:00:00+00:00'))['state']=='scheduled'
 assert l.get(('a','directional:1w','2026-10-22T00:00:00+00:00'))['state']=='scheduled'

def test_sync_does_not_open_legacy_settlements(tmp_path):
 f=tmp_path/'signals.jsonl';f.write_text(json.dumps(row('new'))+'\n')
 legacy=tmp_path/'settlements.jsonl';legacy.write_text('{"signal_id":"old"}\n')
 l=EvaluationLedger(tmp_path/'eval.db');sync_signal_events(f,l)
 assert l.get(('old','directional:24h','2026-10-16T00:00:00+00:00')) is None
