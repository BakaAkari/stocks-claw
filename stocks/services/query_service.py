from __future__ import annotations

from stocks.domain.models import Quote
from stocks.logging_utils import log_event
from stocks.services.provider_service import ProviderService
from stocks.services.quote_guard import QuoteGuard
from stocks.services.resolver_service import InstrumentResolver


class QueryService:
    def __init__(
        self,
        resolver: InstrumentResolver | None = None,
        provider_service: ProviderService | None = None,
        quote_guard: QuoteGuard | None = None,
    ):
        self.resolver = resolver or InstrumentResolver()
        self.provider_service = provider_service or ProviderService()
        self.quote_guard = quote_guard or QuoteGuard()

    def query(self, market_key: str, keyword: str) -> Quote:
        log_event('query.start', market=market_key, keyword=keyword)
        instrument = self.resolver.resolve(market_key, keyword)
        quote = self.provider_service.first_success(market_key, lambda provider: provider.get_quote(instrument))
        quote = self.quote_guard.validate_quote(quote)
        log_event('query.done', market=market_key, code=instrument.code, name=instrument.name)
        return quote
