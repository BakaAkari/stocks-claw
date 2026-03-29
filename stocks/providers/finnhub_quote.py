from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from stocks.domain.models import Instrument, Quote
from stocks.providers.base import QuoteProvider

ROOT = Path(__file__).resolve().parents[2]
FINNHUB_KEY_PATH = ROOT / '.secret' / 'finnhub-key.md'


class FinnhubQuoteProvider(QuoteProvider):
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or FINNHUB_KEY_PATH.read_text(encoding='utf-8').strip()

    def get_quote(self, instrument: Instrument) -> Quote:
        symbol = instrument.code.strip().upper()
        params = urllib.parse.urlencode({'symbol': symbol, 'token': self.api_key})
        url = f'https://finnhub.io/api/v1/quote?{params}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))

        price = data.get('c')
        prev_close = data.get('pc')
        change = data.get('d')
        pct_change = data.get('dp')
        return Quote(
            instrument=instrument,
            price=price,
            change=change,
            pct_change=pct_change,
            volume_lot=None,
            amount_10k=None,
            open_price=data.get('o'),
            high=data.get('h'),
            low=data.get('l'),
            prev_close=prev_close,
        )
