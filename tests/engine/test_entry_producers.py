import json

import pytest

from stocks.engine.entry_snapshot import select_entry
from stocks.engine.scheduled_analysis import _track_engine_action_signals


@pytest.mark.parametrize('market,code',[('a','518880'),('us','NEM'),('crypto','BTCUSDT')])
def test_engine_producer(tmp_path, market, code):
    quote={'instrument':{'market':market,'code':code},'price':10,'source':'fixture','as_of':'2026-10-15T00:00:00Z','stale':False}
    _track_engine_action_signals([{'symbol':f'{market}:{code}','signal':'left_bottom_candidate','price':999}], '2026-10-15T00:00:00Z',repo_root=tmp_path,quotes={market:[quote]})
    row=json.loads((tmp_path/'.local/signal_tracker/signals.jsonl').read_text())
    assert row['generation_price']==10
    assert row['entry_snapshot']['source']=='fixture'
    assert row['scoring_status']=='observation_only'
    assert row['entry_snapshot']['scoring_status']=='scorable'
    assert row['action_semantics']=='left_bottom_candidate'
    assert row['signal_type']=='research_candidate'
    assert select_entry(f'{market}:{code}',{code:quote})==row['entry_snapshot']

def test_missing_quote_is_recorded(tmp_path):
    _track_engine_action_signals([{'symbol':'us:NEM','signal':'left_bottom_candidate','price':999}], '2026-10-15T00:00:00Z',repo_root=tmp_path)
    row=json.loads((tmp_path/'.local/signal_tracker/signals.jsonl').read_text())
    assert row['generation_price'] is None
    assert row['scoring_status']=='unscorable'
    assert row['scoring_reason']=='missing_quote'


def test_entry_recovers_without_direction_flip(tmp_path):
    candidates = [{'symbol': 'us:NEM', 'signal': 'wait_for_pullback'}]
    _track_engine_action_signals(candidates, '2026-10-14T00:00:00Z', repo_root=tmp_path)
    quote = {'instrument': {'market': 'us', 'code': 'NEM'}, 'price': 10,
             'source': 'fixture', 'as_of': '2026-10-15T00:00:00Z', 'stale': False}
    for _ in range(2):
        _track_engine_action_signals(candidates, '2026-10-15T00:00:00Z',
                                     repo_root=tmp_path, quotes={'us': [quote]})
    rows = [json.loads(line) for line in (tmp_path / '.local/signal_tracker/signals.jsonl').read_text().splitlines()]
    assert [row['scoring_status'] for row in rows] == ['unscorable', 'observation_only']
    assert all(row['action_semantics'] == 'wait_for_pullback' for row in rows)
    assert all(row['direction'] == 'unknown' for row in rows)


@pytest.mark.parametrize('action', ['wait_for_pullback', 'reduce_risk', 'avoid_catching_falling_knife', 'new_unknown_action'])
def test_original_actions_survive_without_directional_reinterpretation(tmp_path, action):
    quote = {'instrument': {'market': 'us', 'code': 'NEM'}, 'price': 10,
             'source': 'fixture', 'as_of': '2026-10-15T00:00:00Z', 'stale': False}
    _track_engine_action_signals([{'symbol': 'us:NEM', 'signal': action}],
                                 '2026-10-15T00:00:00Z', repo_root=tmp_path, quotes={'us': [quote]})
    row = json.loads((tmp_path / '.local/signal_tracker/signals.jsonl').read_text())
    assert row['action_semantics'] == action
    assert row['direction'] == 'unknown'
    assert row['strategy_version'] == 'unknown'
    assert row['scoring_status'] == 'observation_only'


def _historical_entry():
    from datetime import datetime, timezone

    from stocks.engine.exchange_targets import target_after
    from stocks.engine.target_observation import observe

    target = target_after(datetime(2026, 10, 14, tzinfo=timezone.utc), 'crypto', 24)
    start = int(datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()) * 1000
    observation = observe('crypto:BTCUSDT', target,
                          clock=datetime(2026, 10, 16, tzinfo=timezone.utc),
                          fetch=lambda _: [[start, '59000', '61000', '58000', '60000', '1', start + 59999]])
    assert observation['status'] == 'observed'
    return observation


def test_explicit_historical_replay_retains_provider_evidence(tmp_path):
    from stocks.engine.entry_snapshot import entry_quote_from_observation

    observation = _historical_entry()
    timestamp = observation['observed_at']
    quote = entry_quote_from_observation(observation, replay_as_of=timestamp)
    provenance = {'origin': 'historical_replay', 'decision_as_of': timestamp,
                  'replay_created_at': '2026-10-16T00:00:00Z'}
    _track_engine_action_signals([{'symbol': 'crypto:BTCUSDT', 'signal': 'wait_for_pullback'}],
                                 timestamp, repo_root=tmp_path, quotes={'crypto': [quote]},
                                 provenance=provenance)
    row = json.loads((tmp_path / '.local/signal_tracker/signals.jsonl').read_text())
    assert row['provenance'] == provenance
    assert row['entry_snapshot']['entry_observation'] == observation
    assert row['entry_snapshot']['freshness_context'] == 'historical_replay'
    assert row['entry_snapshot']['source_payload_sha256'] == observation['source_payload_sha256']
    assert row['scoring_status'] == 'observation_only'


def test_replay_quote_cannot_be_recorded_as_live(tmp_path):
    from stocks.engine.entry_snapshot import entry_quote_from_observation

    observation = _historical_entry()
    timestamp = observation['observed_at']
    quote = entry_quote_from_observation(observation, replay_as_of=timestamp)
    _track_engine_action_signals([{'symbol': 'crypto:BTCUSDT', 'signal': 'wait_for_pullback'}],
                                 timestamp, repo_root=tmp_path, quotes={'crypto': [quote]})
    assert not (tmp_path / '.local/signal_tracker/signals.jsonl').exists()


def test_replay_entry_rejects_price_that_disagrees_with_source_payload():
    from stocks.engine.entry_snapshot import entry_quote_from_observation

    observation = _historical_entry()
    observation['price'] = 1
    with pytest.raises(ValueError, match='source_evidence_mismatch:price'):
        entry_quote_from_observation(observation, replay_as_of=observation['observed_at'])
