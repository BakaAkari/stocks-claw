from __future__ import annotations

from stocks.errors import ProviderExhaustedError
from stocks.logging_utils import log_event
from stocks.providers.registry import ProviderRegistry


class ProviderService:
    def __init__(self, registry: ProviderRegistry | None = None, retries: int = 2):
        self.registry = registry or ProviderRegistry()
        self.retries = max(1, retries)

    def first_success(self, market_key: str, call):
        provider_names = self.registry.get_market_provider_names(market_key)
        if not provider_names:
            log_event('provider.no_config', market=market_key)
            raise ProviderExhaustedError(f'市场 {market_key} 没配置 provider')

        errors = []
        for name in provider_names:
            provider = self.registry.get(market_key, name)
            for attempt in range(1, self.retries + 1):
                try:
                    result = call(provider)
                    log_event('provider.success', market=market_key, provider=name, attempt=attempt)
                    return result
                except Exception as e:
                    errors.append(f'{name}#{attempt}: {e}')
                    log_event('provider.failure', market=market_key, provider=name, attempt=attempt, error=str(e))
            log_event('provider.fallback_next', market=market_key, provider=name)

        log_event('provider.exhausted', market=market_key, errors=errors)
        raise ProviderExhaustedError(' ; '.join(errors))
