import pytest

from stocks.engine.entry_snapshot import build_entry_snapshot, canonical_identity, select_entry


@pytest.mark.parametrize('market,code', [('a','518880'),('us','NEM'),('crypto','BTCUSDT')])
def test_identity_and_snapshot(market, code):
    q = {'instrument': {'market': market, 'code': code}, 'price': 10,
         'source': 'test-provider', 'as_of': '2026-10-15T10:00:00+08:00', 'stale': False}
    assert canonical_identity(code, market) == f'{market}:{code}'
    assert build_entry_snapshot(f'{market}:{code}', q)['scoring_status'] == 'scorable'

@pytest.mark.parametrize('field,value,reason', [
    ('price', None, 'missing_or_invalid_price'),
    ('price', float('nan'), 'missing_or_invalid_price'),
    ('price', -1, 'missing_or_invalid_price'),
    ('source', None, 'missing_source'),
    ('as_of', None, 'missing_or_invalid_as_of'),
    ('as_of', '2026-10-15T10:00:00', 'missing_or_invalid_as_of'),
    ('stale', True, 'stale_or_unknown_freshness'),
    ('stale', None, 'stale_or_unknown_freshness')])
def test_fail_closed(field, value, reason):
    q = {'instrument': {'market':'us','code':'NEM'}, 'price':10,
         'source':'test', 'as_of':'2026-10-15T00:00:00Z', 'stale':False}
    q[field] = value
    result = build_entry_snapshot('us:NEM', q)
    assert result['scoring_status'] == 'unscorable'
    assert result['reason'] == reason

def test_no_guess_or_repair():
    for symbol in ['NEM', ' us:NEM', 'us:NEM:x', 'xx:NEM']:
        with pytest.raises(ValueError):
            canonical_identity(symbol)
    with pytest.raises(ValueError):
        canonical_identity('us:NEM', 'a')
    q = {'instrument':{'market':'a','code':'NEM'}}
    assert build_entry_snapshot('us:NEM', q)['reason'] == 'quote_identity_mismatch'


def test_bare_code_requires_unique_explicit_instrument():
    q = {'instrument': {'market': 'us', 'code': 'GLD'}, 'price': 10,
         'source': 'fixture', 'as_of': '2026-10-15T00:00:00Z', 'stale': False}
    assert select_entry('GLD', {'ignored_key': q})['instrument_id'] == 'us:GLD'
    assert select_entry('GLD', {'GLD': {'price': 10}})['reason'] == 'unknown_identity'
    other = dict(q, instrument={'market': 'hk', 'code': 'GLD'})
    assert select_entry('GLD', {'us': q, 'hk': other})['reason'] == 'ambiguous_identity'
    assert select_entry('us:GLD', {'us': [q, dict(q, price=20)]})['reason'] == 'ambiguous_quotes'


def test_price_metadata_is_preserved_without_market_inference():
    q = {'instrument': {'market': 'us', 'code': 'GLD'}, 'price': 10,
         'source': 'fixture', 'as_of': '2026-10-15T00:00:00Z', 'stale': False}
    entry = build_entry_snapshot('us:GLD', q)
    assert entry['currency'] is None
    assert entry['adjustment'] is None
    assert entry['price_basis'] is None
    assert entry['price_type'] == 'unknown'
    q.update(currency='USD', adjustment='unadjusted', price_basis='raw_quoted_price', price_type='last_trade')
    entry = build_entry_snapshot('us:GLD', q)
    for field in ('currency', 'adjustment', 'price_basis', 'price_type'):
        assert entry[field] == q[field]


@pytest.mark.parametrize('status,observed_at,replay_as_of', [
    ('unavailable', '2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z'),
    ('observed', '2026-10-15T00:00:00Z', '2026-10-15T00:01:00Z'),
    ('observed', '2026-10-15T00:00:00', '2026-10-15T00:00:00'),
])
def test_replay_adapter_requires_exact_observed_cutoff(status, observed_at, replay_as_of):
    from stocks.engine.entry_snapshot import entry_quote_from_observation

    with pytest.raises(ValueError):
        entry_quote_from_observation({'status': status, 'symbol': 'crypto:BTCUSDT',
                                      'observed_at': observed_at}, replay_as_of=replay_as_of)
