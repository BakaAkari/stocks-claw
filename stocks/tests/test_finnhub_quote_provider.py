from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.domain.models import Instrument
from stocks.providers.finnhub_quote import FinnhubQuoteProvider


if __name__ == '__main__':
    provider = FinnhubQuoteProvider()
    quote = provider.get_quote(Instrument(code='AAPL', name='AAPL', market='us', exchange='us'))
    assert quote.instrument.code == 'AAPL'
    assert quote.price is not None
    print('finnhub quote provider ok')
