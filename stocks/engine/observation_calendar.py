"""Small, deterministic market-session calendar for evaluation targets."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Holidays must be supplied by the caller/config; weekends are universally known.
DEFAULT_WINDOWS = {'a': (time(15,0), 'Asia/Shanghai'), 'us': (time(16,0), 'America/New_York'), 'crypto': (None, 'UTC'), 'hk': (time(16,0), 'Asia/Hong_Kong')}

def next_observation_target(generated_at, market, *, holidays=frozenset(), sessions=None):
    """Return target_at as ISO UTC; target is next market close after generation.

    For crypto target is the requested horizon instant (24h/7d handled by caller).
    This stage only supplies the session boundary and never fetches a price.
    """
    if not isinstance(generated_at, datetime) or generated_at.tzinfo is None:
        raise ValueError('timezone required')
    if market not in DEFAULT_WINDOWS:
        raise ValueError('unknown market')
    close, tz_name = DEFAULT_WINDOWS[market]
    if close is None:
        return generated_at.astimezone(timezone.utc)
    tz=ZoneInfo(tz_name); local=generated_at.astimezone(tz)
    d=local.date()
    for _ in range(370):
        if d.weekday()<5 and d.isoformat() not in holidays:
            candidate=datetime.combine(d,close,tzinfo=tz)
            if candidate > local:
                return candidate.astimezone(timezone.utc)
        d += timedelta(days=1)
    raise RuntimeError('calendar search exhausted')

def observation_status(target_at, observed_at, *, max_delay_seconds=86400):
    if not isinstance(target_at,datetime) or not isinstance(observed_at,datetime) or target_at.tzinfo is None or observed_at.tzinfo is None:
        raise ValueError('timezone required')
    delay=(observed_at-target_at).total_seconds()
    if delay < 0: return {'status':'early','delay_seconds':delay}
    if delay > max_delay_seconds: return {'status':'expired','delay_seconds':delay}
    return {'status':'observed','delay_seconds':delay}
