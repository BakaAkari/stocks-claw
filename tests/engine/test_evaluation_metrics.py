import copy
from datetime import timedelta

import pytest

from stocks.engine.evaluation_contract import evaluate_observation, utc
from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.evaluation_metrics import build_report
from stocks.engine.evaluation_task_sync import schedule_signal
from stocks.engine.observation_pipeline import collect_observation


def test_report_uses_frozen_inputs_not_mutable_events(tmp_path, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    collect_observation(ledger, key, now=utc(key[2]) + timedelta(minutes=5), provider=observation_factory)
    with ledger.connect() as db:
        before = build_report(db)
    evaluation_signal['entry_snapshot']['price'] = 200
    evaluation_window['horizon'] = 24
    with ledger.connect() as db:
        after = build_report(db)
    assert after == before
    assert after['results'][0]['price_return'] == pytest.approx(.1)
    assert after['results'][0]['entry']['price'] == 100
    assert after['results'][0]['policy']['horizon'] == 1


def test_raw_price_change_is_not_direction_adjusted(tmp_path, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    evaluation_signal['direction'] = 'sell'
    evaluation_signal['action_semantics'] = 'reduce_risk'
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    definition = ledger.definition(*key[:2])
    observation = observation_factory(evaluation_signal['symbol'], definition['target'])
    result = evaluate_observation(definition, observation)
    assert result['price_return'] == pytest.approx(.1)
    assert result['outcome'] is None and result['scoring_status'] == 'observation_only'
    assert result['signal']['action_semantics'] == 'reduce_risk'


def test_currency_mismatch_and_corrupted_observation_cannot_become_return(tmp_path, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    definition = ledger.definition(*key[:2])
    observation = observation_factory(evaluation_signal['symbol'], definition['target'])
    bad = copy.deepcopy(definition)
    bad['signal']['entry_snapshot']['currency'] = 'USD'
    result = evaluate_observation(bad, observation)
    assert result['reason'] == 'currency_mismatch' and result['price_return'] is None
    observation['price'] = 999
    result = evaluate_observation(definition, observation)
    assert result['reason'] == 'source_evidence_mismatch:price'
    assert result['price_return'] is None


def test_pending_and_legacy_are_explicit_not_mislabeled(tmp_path, evaluation_signal, evaluation_window):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    schedule_signal(ledger, evaluation_signal, evaluation_window)
    ledger.schedule('old', 'directional:24h', '2026-10-01T00:00:00Z')
    with ledger.connect() as db:
        report = build_report(db)
    assert report['legacy_tasks_excluded'] == 1
    assert report['summary'] == {'pending': 1}
    assert report['results'] == []
    assert report['tasks'][0]['last_observation'] is None


def test_report_has_one_snapshot_during_concurrent_worker_commit(tmp_path, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    with ledger.connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)

    class CommitBetweenReads:
        def __init__(self, connection):
            self.connection = connection
            self.committed = False

        def execute(self, statement, *args):
            if 'FROM tasks t JOIN' in statement and not self.committed:
                self.committed = True
                assert collect_observation(ledger, key, now=utc(key[2]) + timedelta(minutes=5), provider=observation_factory) == 'evaluated'
            return self.connection.execute(statement, *args)

    with ledger.connect() as db:
        report = build_report(CommitBetweenReads(db))
        assert report['results'] == []
        assert report['summary'] == {'pending': 1}
        assert build_report(db)['summary'] == {'completed': 1}
