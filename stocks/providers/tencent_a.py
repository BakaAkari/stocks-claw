from __future__ import annotations

import subprocess

from stocks.domain.models import Instrument, Quote
from stocks.errors import ProviderError
from stocks.providers.base import QuoteProvider


class TencentAQuoteProvider(QuoteProvider):
    def _prefix(self, instrument: Instrument) -> str:
        exchange = (instrument.exchange or '').lower()
        code = instrument.code
        if exchange in ('sh', 'sh_stock', 'sh_a', 'sh_index'):
            return 'sh'
        if exchange in ('sz', 'sz_stock', 'sz_a', 'sz_index'):
            return 'sz'
        if code.startswith(('5', '6', '9')):
            return 'sh'
        return 'sz'

    def _fetch_raw(self, symbols: list[str]) -> str:
        url = 'https://qt.gtimg.cn/q=' + ','.join(symbols)
        try:
            result = subprocess.run(
                ['curl', '-L', '--max-time', '20', '-s', url],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise ProviderError(f'tencent 请求失败: rc={e.returncode}') from e
        text = result.stdout.decode('gbk', errors='replace')
        if not text.strip():
            raise ProviderError('tencent 返回空响应')
        return text

    def get_quote(self, instrument: Instrument) -> Quote:
        symbol = f"s_{self._prefix(instrument)}{instrument.code}"
        raw = self._fetch_raw([symbol]).strip()
        quote = self._parse_line(raw, instrument)
        if quote is None:
            raise ProviderError('tencent 返回为空或无法解析')
        return quote

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        if not instruments:
            return []
        symbols = [f"s_{self._prefix(item)}{item.code}" for item in instruments]
        raw = self._fetch_raw(symbols)
        quotes = []
        lines = [line for line in raw.strip().splitlines() if line.strip()]
        instrument_map = {item.code: item for item in instruments}
        for line in lines:
            quote = self._parse_line(line, instrument_map=instrument_map)
            if quote:
                quotes.append(quote)
        if not quotes:
            raise ProviderError('tencent 批量返回为空或无法解析')
        return quotes

    def _parse_line(self, line: str, instrument: Instrument | None = None, instrument_map: dict[str, Instrument] | None = None) -> Quote | None:
        if '="' not in line:
            return None
        _, raw = line.split('="', 1)
        raw = raw.rstrip('";')
        parts = raw.split('~')
        if len(parts) < 10:
            return None

        code = parts[2] or (instrument.code if instrument else '-')
        resolved_instrument = instrument or (instrument_map or {}).get(code)
        if resolved_instrument is None:
            resolved_instrument = Instrument(code=code, name=parts[1] or '-', market='a')

        return Quote(
            instrument=resolved_instrument,
            price=float(parts[3]) if parts[3] else None,
            change=float(parts[4]) if parts[4] else None,
            pct_change=float(parts[5]) if parts[5] else None,
            volume_lot=float(parts[6]) if parts[6] else None,
            amount_10k=float(parts[9]) if len(parts) > 9 and parts[9] else None,
        )
