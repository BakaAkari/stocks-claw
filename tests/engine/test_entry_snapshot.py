import pytest
from stocks.engine.entry_snapshot import canonical_identity, build_entry_snapshot

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
