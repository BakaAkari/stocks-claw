from datetime import datetime
import pytest
from stocks.engine.exchange_targets import target_after

@pytest.mark.parametrize('start,market,expected',[
 ('2026-10-01T00:00:00Z','a','2026-10-08T07:00:00+00:00'),
 ('2026-11-26T00:00:00Z','us','2026-11-27T18:00:00+00:00'),
 ('2026-03-06T22:00:00Z','us','2026-03-09T20:00:00+00:00'),
 ('2026-10-15T21:00:00Z','us','2026-10-16T20:00:00+00:00'),
])
def test_real_calendar(start,market,expected):
 assert target_after(datetime.fromisoformat(start),market,1,basis='sessions')['target_at']==expected

def test_crypto_elapsed():
 assert target_after(datetime.fromisoformat('2026-10-17T00:00:00Z'),'crypto',24)['target_at']=='2026-10-18T00:00:00+00:00'
