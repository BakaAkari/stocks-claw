from __future__ import annotations

from stocks.domain.models import Quote
from stocks.errors import ProviderError


class QuoteGuard:
    """Validate provider output before it reaches renderers."""

    def validate_quote(self, quote: Quote) -> Quote:
        if quote.price is None or quote.price <= 0:
            raise ProviderError(f'quote price 非法: code={quote.instrument.code}, price={quote.price}')

        if quote.pct_change is not None and not (-30 <= quote.pct_change <= 30):
            raise ProviderError(f'quote pct_change 异常: code={quote.instrument.code}, pct_change={quote.pct_change}')

        for field_name in ('open_price', 'high', 'low', 'prev_close'):
            value = getattr(quote, field_name)
            if value is not None and value <= 0:
                raise ProviderError(f'quote {field_name} 非法: code={quote.instrument.code}, value={value}')

        return quote

    def validate_quotes(self, quotes: list[Quote]) -> list[Quote]:
        validated = []
        for quote in quotes:
            validated.append(self.validate_quote(quote))
        return validated
