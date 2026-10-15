import json
from datetime import datetime, timezone

from stocks.engine.entry_snapshot import build_entry_snapshot
from stocks.engine.signal_tracker import SignalTracker, TrackedSignal


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


def test_event_identity_is_idempotent_but_conflicting_reuse_is_rejected(tmp_path):
    from dataclasses import replace

    import pytest

    tracker = SignalTracker(tmp_path)
    signal = TrackedSignal('event', datetime(2026, 10, 15, tzinfo=timezone.utc),
                           'us:NEM', 'unknown', 'original', 10, .5, 'engine_action', 'low',
                           action_semantics='wait_for_pullback', scoring_status='observation_only')
    assert tracker.record(signal) is True
    assert tracker.record(signal) is False
    with pytest.raises(ValueError, match='conflicting signal_id'):
        tracker.record(replace(signal, generation_price=20))
    assert len(tracker.signals_file.read_text().splitlines()) == 1
    with pytest.raises(ValueError, match='evaluation ledger'):
        tracker.settle(signal, '24h', 12)


def test_old_same_direction_does_not_hide_new_event_or_rewrite_legacy(tmp_path):
    tracker = SignalTracker(tmp_path)
    legacy = '{"signal_id":"legacy","generated_at":"2026-01-01T00:00:00Z","symbol":"us:NEM","direction":"buy"}\n'
    tracker.signals_file.write_text(legacy)
    old = tracker._from_dict(json.loads(legacy))
    assert old.schema_version == 0
    signal = TrackedSignal('new', datetime(2026, 10, 15, tzinfo=timezone.utc),
                           'us:NEM', 'buy', 'new judgment', 10, .5, 'llm', 'low')
    tracker.record(signal)
    assert tracker.signals_file.read_text().startswith(legacy)
    assert len(tracker.signals_file.read_text().splitlines()) == 2


def test_parallel_retries_append_only_one_event(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    signal = TrackedSignal('same', datetime(2026, 10, 15, tzinfo=timezone.utc),
                           'us:NEM', 'unknown', 'event', 10, .5, 'test', 'low')
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: SignalTracker(tmp_path).record(signal), range(12)))
    assert sum(results) == 1
    assert len((tmp_path / 'signals.jsonl').read_text().splitlines()) == 1
