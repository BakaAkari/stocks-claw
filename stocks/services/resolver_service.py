from __future__ import annotations

from stocks.config_loader import load_market_watchlist
from stocks.domain.models import Instrument
from stocks.errors import ResolverError


class InstrumentResolver:
    def resolve(self, market_key: str, keyword: str) -> Instrument:
        if market_key == 'a':
            return self._resolve_a_share(keyword)
        if market_key == 'us':
            return self._resolve_us(keyword)
        raise ResolverError(f'市场 {market_key} 还没接解析器')

    def load_watchlist_instruments(self, market_key: str) -> list[Instrument]:
        raw_items = load_market_watchlist(market_key)
        instruments = []
        for item in raw_items:
            instruments.append(
                Instrument(
                    code=str(item['code']),
                    name=item.get('name', str(item['code'])),
                    market=market_key,
                    exchange=item.get('market'),
                )
            )
        return instruments

    def _resolve_a_share(self, keyword: str) -> Instrument:
        keyword = keyword.strip()
        instruments = self.load_watchlist_instruments('a')

        for item in instruments:
            if item.code == keyword:
                return item

        for item in instruments:
            if item.name == keyword:
                return item

        for item in instruments:
            if keyword in item.name:
                return item

        if keyword.isdigit() and len(keyword) == 6:
            exchange = 'sh' if keyword.startswith(('5', '6', '9')) else 'sz'
            return Instrument(code=keyword, name=keyword, market='a', exchange=exchange)

        raise ResolverError('A股标的没匹配上，先把它加进 watchlist，或者直接给 6 位代码')

    def _resolve_us(self, keyword: str) -> Instrument:
        keyword = keyword.strip().upper()
        instruments = self.load_watchlist_instruments('us')

        for item in instruments:
            if item.code.upper() == keyword:
                return item

        for item in instruments:
            if item.name.upper() == keyword:
                return item

        for item in instruments:
            if keyword in item.name.upper():
                return item

        if keyword.isalpha() and 1 <= len(keyword) <= 8:
            return Instrument(code=keyword, name=keyword, market='us', exchange='us')

        raise ResolverError('美股标的没匹配上，先把它加进 watchlist，或者直接给代码')
