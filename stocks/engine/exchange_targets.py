"""Exchange session targets; calendar dependency is explicit and fail-closed."""
from datetime import datetime, timedelta, timezone

CALENDARS = {'a': 'XSHG', 'us': 'XNYS', 'hk': 'XHKG'}
TIMEZONES = {'a': 'Asia/Shanghai', 'us': 'America/New_York', 'hk': 'Asia/Hong_Kong',
             'crypto': 'UTC'}


def validate_target(market, target):
    """Validate a persisted descriptor against its actual exchange close.

    Return the target instant in UTC. A session label alone is insufficient:
    the calendar, basis and instant must describe the same observation.
    """
    if market not in TIMEZONES:
        raise ValueError('unsupported_market')
    try:
        instant = datetime.fromisoformat(target['target_at'].replace('Z', '+00:00'))
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError('invalid_target_at') from exc
    if instant.tzinfo is None:
        raise ValueError('target_timezone_required')
    instant = instant.astimezone(timezone.utc)
    if target.get('session') is None:
        if target.get('basis') != 'elapsed_hours' or target.get('calendar') != '24/7':
            raise ValueError('invalid_elapsed_target')
    else:
        if (market not in CALENDARS or target.get('calendar') != CALENDARS[market]
                or target.get('basis') != 'future_session_closes'):
            raise ValueError('target_calendar_mismatch')
        import exchange_calendars as xc

        try:
            session = datetime.strptime(target['session'], '%Y-%m-%d').date()
            if session.isoformat() != target['session']:
                raise ValueError('noncanonical session')
            cal = xc.get_calendar(CALENDARS[market])
            if not cal.is_session(target['session']):
                raise ValueError('not a session')
            close = cal.session_close(target['session']).to_pydatetime()
        except (ValueError, TypeError) as exc:
            raise ValueError('invalid_target_session') from exc
        if instant != close:
            raise ValueError('target_session_mismatch')
    if target.get('exchange_timezone', TIMEZONES[market]) != TIMEZONES[market]:
        raise ValueError('target_timezone_mismatch')
    return instant


def target_after(generated_at, market, horizon, *, basis='elapsed'):
    if not isinstance(generated_at, datetime) or generated_at.tzinfo is None:
        raise ValueError('timezone required')
    if market not in TIMEZONES:
        raise ValueError('unsupported market')
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise ValueError('positive integer horizon required')
    generated_at = generated_at.astimezone(timezone.utc)
    if basis == 'elapsed':
        return {'target_at': (generated_at + timedelta(hours=horizon)).isoformat(),
                'basis': 'elapsed_hours', 'calendar': '24/7', 'session': None,
                'exchange_timezone': TIMEZONES[market]}
    if basis != 'sessions' or market not in CALENDARS:
        raise ValueError('unsupported calendar/basis')
    import exchange_calendars as xc
    import pandas as pd
    # Bounded coverage; missing/out-of-range calendar must raise, not guess weekdays.
    cal = xc.get_calendar(CALENDARS[market])
    if generated_at.date() < cal.first_session.date() or generated_at.date() > cal.last_session.date():
        raise ValueError('calendar coverage insufficient')
    closes = cal.schedule['close']
    eligible = closes[closes > pd.Timestamp(generated_at)]
    if len(eligible) < horizon:
        raise ValueError('calendar coverage insufficient')
    close = eligible.iloc[horizon-1]
    return {'target_at': close.isoformat(), 'basis': 'future_session_closes',
            'calendar': CALENDARS[market], 'calendar_version': xc.__version__,
            'exchange_timezone': TIMEZONES[market],
            'session': eligible.index[horizon-1].date().isoformat()}
