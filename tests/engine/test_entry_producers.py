import json
import pytest
from stocks.engine.scheduled_analysis import _track_engine_action_signals
from stocks.engine.entry_snapshot import select_entry

@pytest.mark.parametrize('market,code',[('a','518880'),('us','NEM'),('crypto','BTCUSDT')])
def test_engine_producer(tmp_path, market, code):
    quote={'instrument':{'market':market,'code':code},'price':10,'source':'fixture','as_of':'2026-10-15T00:00:00Z','stale':False}
    _track_engine_action_signals([{'symbol':f'{market}:{code}','signal':'left_bottom_candidate','price':999}], '2026-10-15T00:00:00Z',repo_root=tmp_path,quotes={market:[quote]})
    row=json.loads((tmp_path/'.local/signal_tracker/signals.jsonl').read_text())
    assert row['generation_price']==10
    assert row['entry_snapshot']['source']=='fixture'
    assert row['scoring_status']=='scorable'
    assert select_entry(f'{market}:{code}',{code:quote})==row['entry_snapshot']

def test_missing_quote_is_recorded(tmp_path):
    _track_engine_action_signals([{'symbol':'us:NEM','signal':'left_bottom_candidate','price':999}], '2026-10-15T00:00:00Z',repo_root=tmp_path)
    row=json.loads((tmp_path/'.local/signal_tracker/signals.jsonl').read_text())
    assert row['generation_price'] is None
    assert row['scoring_status']=='unscorable'
    assert row['scoring_reason']=='missing_quote'
