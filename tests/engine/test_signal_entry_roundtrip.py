from datetime import datetime, timezone
import json
from stocks.engine.signal_tracker import SignalTracker, TrackedSignal
from stocks.engine.entry_snapshot import build_entry_snapshot


def test_legacy_read_without_inventing_lineage(tmp_path):
    row = dict(signal_id='old', generated_at='2026-10-15T00:00:00Z', symbol='us:NEM', generation_price=10)
    tracker = SignalTracker(tmp_path)
    tracker.signals_file.write_text(json.dumps(row)+'\n')
    old = tracker._from_dict(row)
    assert old.entry_snapshot is None
    assert old.scoring_reason == 'legacy_missing_snapshot'
    assert tracker.unsettled() == []


def test_roundtrip_and_pending_filter(tmp_path):
    tracker = SignalTracker(tmp_path)
    q = dict(instrument={'market':'us','code':'NEM'},price=10,source='test',as_of='2026-10-15T00:00:00Z',stale=False)
    snapshot = build_entry_snapshot('us:NEM',q)
    signal = TrackedSignal('new',datetime.now(timezone.utc),'us:NEM','buy','test',10,0.5,'test','low',entry_snapshot=snapshot,scoring_status=snapshot['scoring_status'],scoring_reason=snapshot['reason'])
    tracker.record(signal)
    pending = tracker.unsettled()
    assert len(pending) == 1
    assert pending[0].entry_snapshot == snapshot
    assert pending[0].to_dict() == signal.to_dict()
    bad = TrackedSignal('bad',datetime.now(timezone.utc),'us:OTHER','buy','test',None,0.5,'test','low')
    tracker.record(bad)
    assert len(tracker.unsettled()) == 1
    assert len(tracker.signals_file.read_text().splitlines()) == 2
