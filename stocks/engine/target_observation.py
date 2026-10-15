"""Historical observations only. Never substitute a current quote."""
import json
import math
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta


def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def observe(symbol, target, *, clock=None, fetch=fetch_json):
    """Session close for equities; completed one-minute bar for crypto.

    target is the exchange_targets descriptor. retrieved_at is acquisition time,
    observed_at is bar close time. Equity time is calendar-derived, not feed time.
    """
    now = clock or datetime.now(timezone.utc)
    instant = datetime.fromisoformat(target['target_at'])
    if instant.tzinfo is None or now.tzinfo is None:
        raise ValueError('timezone required')
    market, code = symbol.split(':', 1)
    out = dict(symbol=symbol, target_at=instant.isoformat(), observed_at=None,
               retrieved_at=now.isoformat(), status='unavailable', reason=None,
               price=None, source=None, adjustment='unadjusted', delay_seconds=None)
    if now < instant:
        return dict(out, status='not_due', reason='target_in_future')
    try:
        if market == 'crypto':
            start = int(instant.timestamp() // 60)*60000
            url = 'https://api.binance.com/api/v3/klines?' + urllib.parse.urlencode(
                dict(symbol=code, interval='1m', startTime=start, endTime=start+59999, limit=1))
            out.update(source='binance_klines', price_type='minute_close', time_origin='provider')
            data = fetch(url)
            if not isinstance(data,list) or len(data)!=1 or int(data[0][0])!=start:
                return dict(out, reason='target_bar_missing')
            observed = datetime.fromtimestamp(int(data[0][6])/1000,timezone.utc)
            price = float(data[0][4])
        elif market in ('a','us','hk'):
            if target.get('session') is None:
                return dict(out, reason='equity_elapsed_window_requires_intraday_provider')
            day = target['session']
            if market == 'a':
                if not code.isdigit() or len(code)!=6:
                    return dict(out,reason='unsupported_symbol')
                ticker = ('sh' if code.startswith(('5','6','9')) else 'sz')+code
                url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+urllib.parse.urlencode(
                    {'param':f'{ticker},day,{day},{day},1,'})
                out.update(source='tencent_unadjusted_day',price_type='session_close',time_origin='exchange_calendar')
                rows=fetch(url).get('data',{}).get(ticker,{}).get('day',[])
                rows=[r for r in rows if r[0]==day]
                if len(rows)!=1: return dict(out,reason='target_bar_missing')
                price=float(rows[0][2])
            else:
                ticker=code if market=='us' else code.zfill(4)+'.HK'
                midnight=datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
                url='https://query1.finance.yahoo.com/v8/finance/chart/'+urllib.parse.quote(ticker,safe='')+'?'+urllib.parse.urlencode(
                    dict(period1=int((midnight-timedelta(days=1)).timestamp()),period2=int((midnight+timedelta(days=2)).timestamp()),interval='1d'))
                out.update(source='yahoo_chart_raw_close',price_type='session_close',time_origin='exchange_calendar')
                result=fetch(url)['chart']['result'][0]
                from zoneinfo import ZoneInfo
                tz=ZoneInfo(result['meta']['exchangeTimezoneName'])
                rows=[v for t,v in zip(result.get('timestamp',[]),result['indicators']['quote'][0]['close'])
                      if datetime.fromtimestamp(t,tz).date().isoformat()==day]
                if len(rows)!=1 or rows[0] is None:return dict(out,reason='target_bar_missing')
                price=float(rows[0])
            observed=instant
        else:
            return dict(out,reason='unsupported_market')
        if observed>now:return dict(out,reason='bar_not_closed')
        if not math.isfinite(price) or price<=0:return dict(out,reason='invalid_price')
        return dict(out, status='observed',price=price,observed_at=observed.isoformat(),
                    delay_seconds=(observed-instant).total_seconds())
    except Exception as exc:
        return dict(out,reason='provider_error',error_type=type(exc).__name__)
