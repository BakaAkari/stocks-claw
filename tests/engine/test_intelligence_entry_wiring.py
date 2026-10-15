"""Execute the actual intelligence recording block with controlled upstream data."""
import ast
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
import json
import pytest
from stocks.engine.signal_tracker import SignalTracker, TrackedSignal
import stocks.engine.scheduled_analysis as scheduled

@pytest.mark.parametrize('market,code,missing', [('a','518880',False),('us','NEM',False),('crypto','BTCUSDT',False),('us','NEM',True)])
def test_actual_intelligence_recording_block(tmp_path, market, code, missing):
    tree=ast.parse(Path(scheduled.__file__).read_text())
    blocks=[node for node in ast.walk(tree) if isinstance(node,ast.If)
            and ast.unparse(node.test)=='analysis_result.signals']
    assert len(blocks)==1
    quote={'instrument':{'market':market,'code':code},'price':10,'source':'fixture','as_of':'2026-10-15T00:00:00Z','stale':False}
    sig=SimpleNamespace(symbol=f'{market}:{code}',direction='buy',rationale='fixture',confidence=.5,urgency='low')
    scope=dict(repo_root=tmp_path,SignalTracker=SignalTracker,TrackedSignal=TrackedSignal,
               now=datetime(2026,10,15,tzinfo=timezone.utc),
               analysis_result=SimpleNamespace(signals=[sig],metadata={'analysis_mode':'llm'}),
               harvest_result=SimpleNamespace(macro={},quotes={} if missing else {code:quote}))
    exec(compile(ast.Module(body=blocks,type_ignores=[]),scheduled.__file__,'exec'),scope)
    row=json.loads((tmp_path/'.local/signal_tracker/signals.jsonl').read_text())
    assert row['source']=='llm'
    assert row['symbol']==sig.symbol
    assert row['scoring_status']==('unscorable' if missing else 'scorable')
    assert row['generation_price']==(None if missing else 10)
    if not missing:
        assert row['entry_snapshot']['as_of']==quote['as_of']
        assert row['entry_snapshot']['source']==quote['source']
    else:
        assert row['scoring_reason']=='missing_quote'
