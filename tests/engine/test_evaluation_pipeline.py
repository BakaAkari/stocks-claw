import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from stocks.engine.evaluation_contract import utc
from stocks.engine.evaluation_ledger import EvaluationLedger
from stocks.engine.evaluation_metrics import build_report
from stocks.engine.evaluation_task_sync import schedule_signal
from stocks.engine.observation_pipeline import collect_observation


def read_report(ledger):
    with ledger.connect() as db:
        return build_report(db)


def test_producer_to_frozen_result_repeats_without_new_rows(tmp_path, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    now = utc(key[2]) + timedelta(minutes=5)
    assert collect_observation(ledger, key, now=now, provider=observation_factory) == 'evaluated'
    report = read_report(ledger)
    result = report['results'][0]
    assert result['price_return'] == pytest.approx(.1)
    assert result['signal']['action_semantics'] == 'wait_for_pullback'
    assert result['scoring_status'] == 'observation_only' and result['outcome'] is None
    assert 'win_rate' not in result and 'net_return' not in result
    assert report['tasks'][0]['state'] == 'completed'
    assert collect_observation(ledger, key, now=now, provider=lambda *_a, **_k: pytest.fail('must not fetch again')) == 'not_claimed'
    assert schedule_signal(ledger, evaluation_signal, evaluation_window) == (key, False)
    assert read_report(ledger) == report
    with ledger.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM observation_attempts').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM evaluation_results').fetchone()[0] == 1


def test_worker_never_creates_tasks(tmp_path):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    with pytest.raises(ValueError, match='frozen_task_required'):
        collect_observation(ledger, ('missing', 'policy', '2026-10-01T00:00:00+00:00'))
    with ledger.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0


def test_pending_retry_and_expiry_preserve_failures(tmp_path, evaluation_signal, evaluation_window):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    assert read_report(ledger)['tasks'][0]['status'] == 'pending'
    assert collect_observation(ledger, key, now=utc(key[2]) - timedelta(seconds=1)) == 'not_due'

    def failing(*args, **kwargs):
        raise TimeoutError()

    assert collect_observation(ledger, key, now=utc(key[2]) + timedelta(seconds=1), provider=failing) == 'unavailable_retry'
    report = read_report(ledger)
    assert not report['results']
    assert report['tasks'][0]['status'] == 'retrying'
    assert report['tasks'][0]['last_observation']['reason'] == 'provider_error'
    assert collect_observation(ledger, key, now=utc(key[2]) + timedelta(hours=2), provider=failing) == 'expired'
    report = read_report(ledger)
    assert report['results'][0]['reason'] == 'provider_error'
    assert report['results'][0]['price_return'] is None
    assert report['tasks'][0]['state'] == 'expired'
    assert report['tasks'][0]['attempt_count'] == 2


def test_lease_expiry_then_recovery_has_one_frozen_result(tmp_path, monkeypatch, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    elapsed = [0.0]
    monkeypatch.setattr('stocks.engine.observation_pipeline.time.monotonic', lambda: elapsed[0])

    def slow(symbol, target, **kwargs):
        elapsed[0] = 61.0
        return observation_factory(symbol, target, **kwargs)

    now = utc(key[2]) + timedelta(minutes=5)
    assert collect_observation(ledger, key, now=now, provider=slow) == 'lease_lost'
    assert not read_report(ledger)['results']
    assert collect_observation(ledger, key, now=now + timedelta(seconds=62), provider=observation_factory) == 'evaluated'
    assert len(read_report(ledger)['results']) == 1


def test_result_and_observation_transaction_rolls_back_together(tmp_path, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    with ledger.connect() as db:
        db.execute("CREATE TRIGGER fail_attempt BEFORE INSERT ON observation_attempts BEGIN SELECT RAISE(ABORT,'crash'); END")
    now = utc(key[2]) + timedelta(minutes=5)
    with pytest.raises(sqlite3.IntegrityError, match='crash'):
        collect_observation(ledger, key, now=now, provider=observation_factory)
    assert ledger.get(key) == {'state': 'scheduled', 'result': None}
    assert not read_report(ledger)['results']
    with ledger.connect() as db:
        db.execute('DROP TRIGGER fail_attempt')
    assert collect_observation(ledger, key, now=now, provider=observation_factory) == 'evaluated'


def test_concurrent_workers_finish_once(tmp_path, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    now = utc(key[2]) + timedelta(minutes=5)
    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(lambda _: collect_observation(ledger, key, now=now, provider=observation_factory), range(10)))
    assert statuses.count('evaluated') == 1
    assert statuses.count('not_claimed') == 9
    assert len(read_report(ledger)['results']) == 1


def test_nonfinite_provider_failure_is_audited(tmp_path, evaluation_signal, evaluation_window):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    status = collect_observation(ledger, key, now=utc(key[2]) + timedelta(minutes=5),
                                 provider=lambda *_a, **_k: {'status': 'observed', 'price': float('nan')})
    assert status == 'unavailable_retry'
    observation = read_report(ledger)['tasks'][0]['last_observation']
    assert observation['reason'] == 'provider_error'
    json.dumps(observation, allow_nan=False)


def test_runtime_upgrade_cannot_reinterpret_frozen_evaluator(tmp_path, monkeypatch, evaluation_signal, evaluation_window, observation_factory):
    ledger = EvaluationLedger(tmp_path / 'ledger.db')
    key, _ = schedule_signal(ledger, evaluation_signal, evaluation_window)
    monkeypatch.setattr('stocks.engine.evaluation_contract.EVALUATOR_VERSION', 'future-v2')
    with pytest.raises(ValueError, match='unsupported_frozen_evaluator_version'):
        collect_observation(ledger, key, now=utc(key[2]) + timedelta(minutes=5), provider=observation_factory)
    assert not read_report(ledger)['results']
    assert ledger.get(key) == {'state': 'scheduled', 'result': None}
