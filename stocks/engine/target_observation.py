"""Historical quoted prices with provider evidence, never current-price fallbacks."""

import hashlib
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from stocks.engine.exchange_targets import TIMEZONES, validate_target

PRICE_BASIS = 'raw_quoted_price'
# These are quote assets, not inferred fiat equivalents (USDT is not USD).
CRYPTO_QUOTE_ASSETS = ('USDT', 'USDC', 'BTC', 'ETH', 'BNB')


class ObservationError(ValueError):
    """A stable, auditable rejection reason."""


def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def _instant(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('timezone required')
    return value.astimezone(timezone.utc)


def _payload_hash(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _minute_bounds(instant):
    start = int(instant.timestamp() // 60) * 60000
    close = datetime.fromtimestamp((start + 59999) / 1000, timezone.utc)
    # Binance's millisecond close can precede a target in the last microseconds.
    if close < instant:
        start += 60000
        close = datetime.fromtimestamp((start + 59999) / 1000, timezone.utc)
    return start, close


def _request(symbol, target, instant):
    market, code = symbol.split(':', 1)
    if market == 'crypto':
        if not re.fullmatch(r'[A-Z0-9]+', code):
            raise ObservationError('unsupported_symbol')
        currency = next((q for q in CRYPTO_QUOTE_ASSETS
                         if code.endswith(q) and len(code) > len(q)), None)
        if currency is None:
            raise ObservationError('unsupported_quote_currency')
        start, _ = _minute_bounds(instant)
        url = 'https://api.binance.com/api/v3/klines?' + urllib.parse.urlencode(
            dict(symbol=code, interval='1m', startTime=start, endTime=start + 59999, limit=1))
        return dict(request_url=url, source='binance_klines', price_type='minute_close',
                    currency=currency, currency_origin='supported_instrument_quote_asset',
                    adjustment='not_applicable', time_origin='provider')
    if target['session'] is None:
        raise ObservationError('equity_elapsed_window_requires_intraday_provider')
    day = target['session']
    if market == 'a':
        # A shares and CNY funds only. B shares / Beijing listings need other metadata.
        if not re.fullmatch(r'[0156][0-9]{5}|3[0-9]{5}', code):
            raise ObservationError('unsupported_symbol')
        ticker = ('sh' if code.startswith(('5', '6')) else 'sz') + code
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?' + urllib.parse.urlencode(
            {'param': f'{ticker},day,{day},{day},1,'})
        return dict(request_url=url, source='tencent_unadjusted_day',
                    price_type='session_close', currency='CNY',
                    currency_origin='supported_a_share_or_fund_identity',
                    adjustment='unadjusted', time_origin='exchange_calendar')
    if market == 'hk' and not re.fullmatch(r'[0-9]{1,5}', code):
        raise ObservationError('unsupported_symbol')
    ticker = code if market == 'us' else code.zfill(4) + '.HK'
    midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/'
           + urllib.parse.quote(ticker, safe='') + '?' + urllib.parse.urlencode(
               dict(period1=int((midnight - timedelta(days=1)).timestamp()),
                    period2=int((midnight + timedelta(days=2)).timestamp()), interval='1d')))
    return dict(request_url=url, source='yahoo_chart_raw_close', price_type='session_close',
                currency_origin='provider_metadata', adjustment='provider_adjustment_unspecified',
                time_origin='exchange_calendar')


def _parse(symbol, target, instant, payload):
    """Extract only the requested bar. Used again when validating stored evidence."""
    market, code = symbol.split(':', 1)
    if market == 'crypto':
        start, observed = _minute_bounds(instant)
        if (not isinstance(payload, list) or len(payload) != 1
                or len(payload[0]) < 7 or payload[0][0] != start):
            raise ObservationError('target_bar_missing')
        if payload[0][6] != start + 59999:
            raise ObservationError('target_bar_close_mismatch')
        return dict(price=float(payload[0][4]), observed_at=observed.isoformat(),
                    source_as_of=observed.isoformat(), source_time_precision='millisecond',
                    source_as_of_origin='provider_bar_close',
                    bar_open_at=datetime.fromtimestamp(start / 1000, timezone.utc).isoformat(),
                    bar_close_at=observed.isoformat(),
                    bar_selection='first_minute_close_at_or_after_target')
    day = target['session']
    fields = dict(observed_at=instant.isoformat(), source_as_of=None, source_session=day,
                  source_time_precision='session', source_as_of_origin='unavailable_for_daily_bar')
    if market == 'a':
        ticker = ('sh' if code.startswith(('5', '6')) else 'sz') + code
        rows = payload.get('data', {}).get(ticker, {}).get('day', [])
        rows = [r for r in rows if r[0] == day]
        if len(rows) != 1:
            raise ObservationError('target_bar_missing')
        return dict(fields, price=float(rows[0][2]))
    result = payload['chart']['result'][0]
    ticker = code if market == 'us' else code.zfill(4) + '.HK'
    if result['meta'].get('symbol') != ticker:
        raise ObservationError('provider_symbol_mismatch')
    currency = result['meta'].get('currency')
    if not isinstance(currency, str) or not re.fullmatch('[A-Z]{3,8}', currency):
        raise ObservationError('currency_unverified')
    tz = ZoneInfo(result['meta']['exchangeTimezoneName'])
    expected_tz = ZoneInfo(TIMEZONES[market])
    if instant.astimezone(tz).utcoffset() != instant.astimezone(expected_tz).utcoffset():
        raise ObservationError('provider_timezone_mismatch')
    timestamps = result.get('timestamp', [])
    prices = result['indicators']['quote'][0]['close']
    if len(timestamps) != len(prices):
        raise ObservationError('invalid_provider_payload')
    rows = [(t, v) for t, v in zip(timestamps, prices)
            if datetime.fromtimestamp(t, tz).date().isoformat() == day]
    if len(rows) != 1 or rows[0][1] is None:
        raise ObservationError('target_bar_missing')
    return dict(fields, price=float(rows[0][1]), currency=currency,
                provider_bar_timestamp=datetime.fromtimestamp(rows[0][0], timezone.utc).isoformat())


def validate_observation(symbol, target, obs):
    """Return None when evidence agrees, otherwise a stable rejection reason.

    Daily feeds do not supply a per-bar freshness timestamp. Their source_as_of
    stays null; observed_at is explicitly the calendar-derived session close.
    """
    try:
        market = symbol.split(':', 1)[0]
        instant = validate_target(market, target)
        if obs.get('status') != 'observed':
            return 'observation_not_observed'
        if obs.get('symbol') != symbol:
            return 'symbol_mismatch'
        if _instant(obs['target_at']) != instant:
            return 'target_mismatch'
        if obs.get('target_definition') != target:
            return 'target_definition_mismatch'
        if obs.get('price_basis') != PRICE_BASIS:
            return 'price_basis_unverified'
        if obs.get('exchange_timezone') != TIMEZONES[market]:
            return 'observation_timezone_mismatch'
        price = obs['price']
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            return 'invalid_price'
        if _payload_hash(obs['source_payload']) != obs.get('source_payload_sha256'):
            return 'source_payload_hash_mismatch'
        expected = _request(symbol, target, instant)
        expected.update(_parse(symbol, target, instant, obs['source_payload']))
        for field, value in expected.items():
            if obs.get(field) != value:
                return 'source_evidence_mismatch:' + field
        observed = _instant(obs['observed_at'])
        retrieved = _instant(obs['retrieved_at'])
        if observed > retrieved:
            return 'bar_not_closed'
        if market != 'crypto' and target['session'] >= retrieved.astimezone(ZoneInfo(TIMEZONES[market])).date().isoformat():
            return 'equity_daily_bar_finality_unverified'
        if obs.get('delay_seconds') != (observed - instant).total_seconds():
            return 'observation_delay_mismatch'
        return None
    except ObservationError as exc:
        return str(exc)
    except ValueError as exc:
        # validate_target emits stable reason codes; other parse failures do not.
        reason = str(exc)
        return reason if re.fullmatch('[a-z_]+', reason) else 'invalid_observation_evidence'
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError):
        return 'invalid_observation_evidence'


def observe(symbol, target, *, clock=None, fetch=fetch_json):
    """Fetch one historical bar and retain its original payload and request.

    Crypto selects the first minute close at/after target, with its exact delay.
    Equity daily bars are accepted only after the session's local calendar day.
    These are raw quoted prices, not total-return or split-adjusted performance.
    """
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('timezone required')
    out = dict(symbol=symbol, target_at=target.get('target_at'), target_definition=target,
               observed_at=None, source_as_of=None, retrieved_at=now.isoformat(),
               status='unavailable', reason=None, price=None, source=None,
               currency=None, price_basis=PRICE_BASIS, delay_seconds=None)
    try:
        market = symbol.split(':', 1)[0]
        instant = validate_target(market, target)
        out.update(target_at=instant.isoformat(), exchange_timezone=TIMEZONES[market])
        if now < instant:
            return dict(out, status='not_due', reason='target_in_future')
        out.update(_request(symbol, target, instant))
        if (market != 'crypto' and target['session']
                >= now.astimezone(ZoneInfo(TIMEZONES[market])).date().isoformat()):
            return dict(out, reason='equity_daily_bar_finality_unverified')
        try:
            payload = fetch(out['request_url'])
        except Exception as exc:
            return dict(out, reason='provider_error', error_type=type(exc).__name__)
        out.update(retrieved_at=(clock or datetime.now(timezone.utc)).isoformat(),
                   source_payload=payload, source_payload_sha256=_payload_hash(payload))
        out.update(_parse(symbol, target, instant, payload))
        observed = _instant(out['observed_at'])
        out.update(status='observed', delay_seconds=(observed - instant).total_seconds())
        reason = validate_observation(symbol, target, out)
        return dict(out, status='unavailable', reason=reason, price=None) if reason else out
    except ObservationError as exc:
        return dict(out, reason=str(exc), price=None)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError) as exc:
        reason = str(exc)
        return dict(out, reason=reason if re.fullmatch('[a-z_]+', reason)
                    else 'invalid_provider_payload', price=None)
