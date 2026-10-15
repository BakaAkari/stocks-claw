import copy
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from stocks.engine.exchange_targets import target_after
from stocks.engine.target_observation import observe, validate_observation

T = target_after(datetime.fromisoformat('2026-10-08T08:00:00+00:00'), 'a', 1, basis='sessions')
US_TARGET = target_after(datetime.fromisoformat('2026-10-08T21:00:00+00:00'), 'us', 1, basis='sessions')
CRYPTO_TARGET = target_after(datetime.fromisoformat('2026-10-08T07:00:00+00:00'), 'crypto', 24)
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
START = int(datetime(2026, 10, 9, 7, tzinfo=timezone.utc).timestamp()) * 1000


def crypto_bar(start=START, price='100.25'):
    return [[start, '100', '101', '99', price, '1.5', start + 59999]]


def yahoo_bar(currency='USD'):
    return {'chart': {'result': [{
        'meta': {'symbol': 'NEM', 'currency': currency, 'exchangeTimezoneName': 'America/New_York'},
        'timestamp': [int(datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc).timestamp())],
        'indicators': {'quote': [{'close': [100.0]}]},
    }]}}


def test_raw_not_adjusted_and_no_current_fallback():
    result = observe('a:518880', T, clock=NOW, fetch=lambda u: {
        'data': {'sh518880': {'qfqday': [['2026-10-09', '1', '2']]}}})
    assert result['status'] == 'unavailable'
    assert result['reason'] == 'target_bar_missing'
    assert result['price'] is None


def test_target_date_not_latest_and_no_invented_source_timestamp():
    data = {'data': {'sh518880': {'day': [['2026-10-08', '1', '2'], ['2026-10-09', '1', '3']]}}}
    result = observe('a:518880', T, clock=NOW, fetch=lambda u: data)
    assert result['price'] == 3
    assert result['time_origin'] == 'exchange_calendar'
    assert result['source_as_of'] is None
    assert result['source_session'] == '2026-10-09'
    assert result['observed_at'] == T['target_at']
    assert result['currency'] == 'CNY'
    assert validate_observation('a:518880', T, result) is None


def test_target_session_mismatch_rejected_before_fetch():
    wrong = dict(T, session='2026-10-08')
    result = observe('a:518880', wrong, clock=NOW,
                     fetch=lambda _: pytest.fail('must reject before fetching'))
    assert result['reason'] == 'target_session_mismatch'
    assert result['price'] is None


def test_wrong_market_calendar_rejected():
    assert observe('us:NEM', T, clock=NOW)['reason'] == 'target_calendar_mismatch'


def test_provider_failure_and_future():
    def fail(url):
        raise TimeoutError()

    assert observe('us:NEM', US_TARGET, clock=NOW, fetch=fail)['reason'] == 'provider_error'
    result = observe('us:NEM', US_TARGET, clock=datetime(2026, 10, 8, tzinfo=timezone.utc), fetch=fail)
    assert result['status'] == 'not_due'


def test_same_day_equity_bar_is_not_assumed_final():
    now = datetime(2026, 10, 9, 20, 1, tzinfo=timezone.utc)
    result = observe('us:NEM', US_TARGET, clock=now,
                     fetch=lambda _: pytest.fail('same-day bar is not verifiable'))
    assert result['reason'] == 'equity_daily_bar_finality_unverified'


def test_yahoo_currency_comes_from_metadata_and_adjustment_is_not_invented():
    result = observe('us:NEM', US_TARGET, clock=NOW, fetch=lambda _: yahoo_bar())
    assert result['status'] == 'observed'
    assert result['currency'] == 'USD'
    assert result['currency_origin'] == 'provider_metadata'
    assert result['adjustment'] == 'provider_adjustment_unspecified'
    assert result['source_as_of'] is None
    assert result['provider_bar_timestamp'] == '2026-10-09T13:30:00+00:00'
    assert observe('us:NEM', US_TARGET, clock=NOW,
                   fetch=lambda _: yahoo_bar(None))['reason'] == 'currency_unverified'


def test_crypto_retains_verifiable_bar_times_currency_and_original_evidence():
    payload = crypto_bar()
    result = observe('crypto:BTCUSDT', CRYPTO_TARGET, clock=NOW, fetch=lambda _: payload)
    assert result['status'] == 'observed'
    assert result['currency'] == 'USDT'
    assert result['price_basis'] == 'raw_quoted_price'
    assert result['source_as_of'] == result['bar_close_at'] == result['observed_at']
    assert result['delay_seconds'] == 59.999
    assert result['retrieved_at'] == NOW.isoformat()
    assert result['source_payload'] == payload
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    assert result['source_payload_sha256'] == hashlib.sha256(canonical.encode()).hexdigest()
    query = parse_qs(urlparse(result['request_url']).query)
    assert query['startTime'] == [str(START)]
    assert query['endTime'] == [str(START + 59999)]
    assert validate_observation('crypto:BTCUSDT', CRYPTO_TARGET, result) is None


def test_crypto_last_microseconds_select_next_close_not_a_price_before_target():
    target = dict(CRYPTO_TARGET, target_at='2026-10-09T07:00:59.999500+00:00')
    result = observe('crypto:BTCUSDT', target, clock=NOW,
                     fetch=lambda _: crypto_bar(START + 60000))
    assert result['status'] == 'observed'
    assert result['bar_open_at'] == '2026-10-09T07:01:00+00:00'
    assert result['delay_seconds'] == 59.9995


def test_crypto_requires_actual_selected_minute_close_and_completed_bar():
    wrong = crypto_bar()
    wrong[0][6] += 1000
    assert observe('crypto:BTCUSDT', CRYPTO_TARGET, clock=NOW,
                   fetch=lambda _: wrong)['reason'] == 'target_bar_close_mismatch'
    result = observe('crypto:BTCUSDT', CRYPTO_TARGET,
                     clock=datetime(2026, 10, 9, 7, 0, 30, tzinfo=timezone.utc),
                     fetch=lambda _: crypto_bar())
    assert result['reason'] == 'bar_not_closed'
    assert result['price'] is None


def test_unknown_crypto_quote_asset_is_not_guessed():
    result = observe('crypto:BTCXYZ', CRYPTO_TARGET, clock=NOW,
                     fetch=lambda _: pytest.fail('unsupported quote asset'))
    assert result['reason'] == 'unsupported_quote_currency'


@pytest.mark.parametrize('field,value,reason', [
    ('price', 999, 'source_evidence_mismatch:price'),
    ('source_as_of', NOW.isoformat(), 'source_evidence_mismatch:source_as_of'),
    ('request_url', 'https://example.org/', 'source_evidence_mismatch:request_url'),
    ('source_payload_sha256', '0' * 64, 'source_payload_hash_mismatch'),
    ('delay_seconds', 0, 'observation_delay_mismatch'),
])
def test_stored_observation_must_agree_with_original_payload(field, value, reason):
    result = observe('crypto:BTCUSDT', CRYPTO_TARGET, clock=NOW, fetch=lambda _: crypto_bar())
    changed = copy.deepcopy(result)
    changed[field] = value
    assert validate_observation('crypto:BTCUSDT', CRYPTO_TARGET, changed) == reason


def test_invalid_source_price_is_unavailable():
    result = observe('crypto:BTCUSDT', CRYPTO_TARGET, clock=NOW,
                     fetch=lambda _: crypto_bar(price='NaN'))
    assert result['reason'] == 'invalid_price'
    assert result['price'] is None
