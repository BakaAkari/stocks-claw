from __future__ import annotations

import json
import subprocess
import urllib.parse

from stocks.domain.models import Instrument, Quote
from stocks.errors import ProviderError
from stocks.providers.base import QuoteProvider


class EastmoneyAQuoteProvider(QuoteProvider):
    def _secid(self, instrument: Instrument) -> str:
        exchange = (instrument.exchange or '').lower()
        code = instrument.code
        if exchange in ('sh', 'sh_stock', 'sh_a', 'sh_index'):
            return f'1.{code}'
        if exchange in ('sz', 'sz_stock', 'sz_a', 'sz_index'):
            return f'0.{code}'
        if code.startswith(('5', '6', '9')):
            return f'1.{code}'
        return f'0.{code}'

    def _fetch(self, secids: list[str]) -> dict:
        params = {
            'fltt': '2',
            'invt': '2',
            'fields': 'f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18',
            'secids': ','.join(secids),
        }
        url = 'https://push2.eastmoney.com/api/qt/ulist.np/get?' + urllib.parse.urlencode(params)
        try:
            result = subprocess.run(
                ['curl', '-L', '--max-time', '20', '-A', 'Mozilla/5.0', '-s', url],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise ProviderError(f'eastmoney 请求失败: rc={e.returncode}') from e
        if not result.stdout.strip():
            raise ProviderError('eastmoney 返回空响应')
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise ProviderError('eastmoney 返回不是合法 JSON') from e

    def get_quote(self, instrument: Instrument) -> Quote:
        payload = self._fetch([self._secid(instrument)])
        rows = payload.get('data', {}).get('diff', [])
        if not rows:
            raise ProviderError('eastmoney 返回为空')
        return self._row_to_quote(rows[0], instrument)

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        if not instruments:
            return []
        instrument_map = {item.code: item for item in instruments}
        payload = self._fetch([self._secid(item) for item in instruments])
        rows = payload.get('data', {}).get('diff', [])
        quotes = []
        for row in rows:
            code = str(row.get('f12'))
            instrument = instrument_map.get(code)
            if instrument is None:
                continue
            quotes.append(self._row_to_quote(row, instrument))
        return quotes

    def _row_to_quote(self, row: dict, instrument: Instrument) -> Quote:
        return Quote(
            instrument=instrument,
            price=float(row['f2']) if row.get('f2') not in (None, '') else None,
            change=float(row['f4']) if row.get('f4') not in (None, '') else None,
            pct_change=float(row['f3']) if row.get('f3') not in (None, '') else None,
            volume_lot=float(row['f5']) if row.get('f5') not in (None, '') else None,
            amount_10k=float(row['f6']) if row.get('f6') not in (None, '') else None,
            high=float(row['f15']) if row.get('f15') not in (None, '') else None,
            low=float(row['f16']) if row.get('f16') not in (None, '') else None,
            open_price=float(row['f17']) if row.get('f17') not in (None, '') else None,
            prev_close=float(row['f18']) if row.get('f18') not in (None, '') else None,
        )
