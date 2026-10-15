"""Exchange session targets; calendar dependency is explicit and fail-closed."""
from datetime import datetime, timedelta, timezone

CALENDARS = {'a': 'XSHG', 'us': 'XNYS', 'hk': 'XHKG'}


def target_after(generated_at, market, horizon, *, basis='elapsed'):
    if generated_at.tzinfo is None:
        raise ValueError('timezone required')
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise ValueError('positive integer horizon required')
    generated_at = generated_at.astimezone(timezone.utc)
    if basis == 'elapsed':
        return {'target_at': (generated_at + timedelta(hours=horizon)).isoformat(),
                'basis': 'elapsed_hours', 'calendar': '24/7', 'session': None}
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
            'session': eligible.index[horizon-1].date().isoformat()}
