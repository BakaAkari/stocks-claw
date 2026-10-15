from datetime import datetime

import pytest

from stocks.engine.exchange_targets import target_after, validate_target


@pytest.mark.parametrize('start,market,expected', [
    ('2026-10-01T00:00:00Z', 'a', '2026-10-08T07:00:00+00:00'),
    ('2026-11-26T00:00:00Z', 'us', '2026-11-27T18:00:00+00:00'),
    ('2026-03-06T22:00:00Z', 'us', '2026-03-09T20:00:00+00:00'),
    ('2026-10-15T21:00:00Z', 'us', '2026-10-16T20:00:00+00:00'),
])
def test_real_calendar(start, market, expected):
    target = target_after(datetime.fromisoformat(start), market, 1, basis='sessions')
    assert target['target_at'] == expected
    assert validate_target(market, target).isoformat() == expected


def test_crypto_elapsed():
    target = target_after(datetime.fromisoformat('2026-10-17T00:00:00Z'), 'crypto', 24)
    assert target['target_at'] == '2026-10-18T00:00:00+00:00'
    assert validate_target('crypto', target).isoformat() == target['target_at']


@pytest.mark.parametrize('patch,reason', [
    ({'session': '2026-10-08'}, 'target_session_mismatch'),
    ({'session': '2026-10-10'}, 'invalid_target_session'),
    ({'calendar': 'XNYS'}, 'target_calendar_mismatch'),
    ({'exchange_timezone': 'UTC'}, 'target_timezone_mismatch'),
    ({'target_at': '2026-10-09T07:00:00'}, 'target_timezone_required'),
])
def test_descriptor_rejects_inconsistent_session_calendar_and_timezone(patch, reason):
    target = target_after(datetime.fromisoformat('2026-10-08T08:00:00Z'), 'a', 1, basis='sessions')
    with pytest.raises(ValueError, match=reason):
        validate_target('a', dict(target, **patch))


def test_unknown_market_and_calendar_coverage_fail_closed():
    with pytest.raises(ValueError, match='unsupported market'):
        target_after(datetime.fromisoformat('2026-10-17T00:00:00Z'), 'typo', 24)
    with pytest.raises(ValueError, match='calendar coverage insufficient'):
        target_after(datetime.fromisoformat('2100-01-01T00:00:00Z'), 'a', 1, basis='sessions')
