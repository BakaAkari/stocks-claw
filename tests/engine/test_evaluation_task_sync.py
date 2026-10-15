import copy
import json

import pytest

from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.evaluation_task_sync import schedule_signal, sync_signal_events


def test_real_producer_event_sync_is_unique(tmp_path, evaluation_signal, evaluation_window):
    signals = tmp_path / 'signals.jsonl'
    invalid = dict(evaluation_signal, signal_id='invalid', entry_snapshot=None)
    signals.write_text('\n'.join(json.dumps(x) for x in [evaluation_signal, evaluation_signal, invalid, {'signal_id': 'legacy'}]))
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    windows = {'crypto': [evaluation_window]}
    first = sync_signal_events(signals, ledger, windows=windows)
    second = sync_signal_events(signals, ledger, windows=windows)
    assert first['scheduled'] == 1 and first['existing_or_duplicate'] == 1
    assert first['skip_reasons'] == {'unscorable_entry': 1, 'legacy_or_unknown_signal_schema': 1}
    assert second['scheduled'] == 0 and second['existing_or_duplicate'] == 2
    assert len(ledger.keys()) == 1
    definition = ledger.definition(*ledger.keys()[0][:2])
    assert definition['signal'] == evaluation_signal
    assert definition['signal']['action_semantics'] == 'wait_for_pullback'


def test_changed_signal_and_policy_are_conflicts_not_new_tasks(tmp_path, evaluation_signal, evaluation_window):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    before = ledger.definition(*key[:2])
    changed = copy.deepcopy(evaluation_signal)
    changed['entry_snapshot']['price'] = 200
    with pytest.raises(ValueError, match='immutable_signal_id_conflict'):
        schedule_signal(ledger, changed, evaluation_window)
    with pytest.raises(ValueError, match='immutable_policy_id_conflict'):
        schedule_signal(ledger, evaluation_signal, dict(evaluation_window, horizon=24))
    assert ledger.keys() == [key]
    assert ledger.definition(*key[:2]) == before


def test_policy_conflict_does_not_partially_save_new_signal(tmp_path, evaluation_signal, evaluation_window):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    schedule_signal(ledger, evaluation_signal, evaluation_window)
    other = dict(evaluation_signal, signal_id='other')
    with pytest.raises(ValueError, match='immutable_policy_id_conflict'):
        schedule_signal(ledger, other, dict(evaluation_window, retry_seconds=20))
    with ledger.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM frozen_signals').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1


def test_sync_never_imports_old_settlement_or_unconfigured_windows(tmp_path, evaluation_signal):
    signals = tmp_path / 'signals.jsonl'
    signals.write_text(json.dumps(evaluation_signal))
    legacy = tmp_path / 'settlements.jsonl'
    legacy.write_text('old evidence is not even JSON')
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    report = sync_signal_events(signals, ledger, windows={})
    assert report['skip_reasons'] == {'no_configured_window': 1}
    assert ledger.keys() == []
    assert legacy.read_text() == 'old evidence is not even JSON'
