from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.domain.models import Instrument, Quote
from stocks.errors import ProviderError
from stocks.services.quote_guard import QuoteGuard


if __name__ == '__main__':
    guard = QuoteGuard()
    instrument = Instrument(code='600519', name='贵州茅台', market='a', exchange='sh')

    ok = Quote(
        instrument=instrument,
        price=123.45,
        change=1.2,
        pct_change=0.98,
        volume_lot=1000,
        amount_10k=5000,
        open_price=122.0,
        high=124.0,
        low=121.5,
        prev_close=122.25,
    )
    assert guard.validate_quote(ok) == ok

    bad_price = Quote(
        instrument=instrument,
        price=0,
        change=1.2,
        pct_change=0.98,
        volume_lot=1000,
        amount_10k=5000,
    )
    try:
        guard.validate_quote(bad_price)
        raise AssertionError('bad price should fail')
    except ProviderError:
        pass

    bad_pct = Quote(
        instrument=instrument,
        price=123.45,
        change=1.2,
        pct_change=88.8,
        volume_lot=1000,
        amount_10k=5000,
    )
    try:
        guard.validate_quote(bad_pct)
        raise AssertionError('bad pct should fail')
    except ProviderError:
        pass

    print('quote guard ok')
