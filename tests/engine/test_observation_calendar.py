from datetime import datetime,timezone
from stocks.engine.observation_calendar import next_observation_target,observation_status

def dt(s):return datetime.fromisoformat(s)
def test_a_weekend_and_holiday():
 assert next_observation_target(dt('2026-10-16T20:00:00+00:00'),'a',holidays={'2026-10-19'}).isoformat()=='2026-10-20T07:00:00+00:00'
def test_us_timezone_boundary():
 assert next_observation_target(dt('2026-10-15T19:00:00+00:00'),'us').isoformat()=='2026-10-15T20:00:00+00:00'
def test_crypto_keeps_instant():
 x=dt('2026-10-15T06:00:00+00:00');assert next_observation_target(x,'crypto')==x
def test_observation_states():
 t=dt('2026-10-15T00:00:00+00:00')
 assert observation_status(t,t)['status']=='observed'
 assert observation_status(t,dt('2026-10-14T23:59:00+00:00'))['status']=='early'
 assert observation_status(t,dt('2026-10-16T01:00:01+00:00'))['status']=='expired'
