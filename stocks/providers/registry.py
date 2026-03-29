from __future__ import annotations

from stocks.config_loader import load_market_settings
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider
from stocks.providers.finnhub_quote import FinnhubQuoteProvider
from stocks.providers.tencent_a import TencentAQuoteProvider


class ProviderRegistry:
    def __init__(self):
        self._providers = {
            'a': {
                'tencent': TencentAQuoteProvider(),
                'eastmoney': EastmoneyAQuoteProvider(),
            },
            'us': {
                'finnhub': FinnhubQuoteProvider(),
            },
        }

    def get_market_provider_names(self, market_key: str) -> list[str]:
        settings = load_market_settings(market_key)
        names = settings.get('providers', [])
        if names:
            return names
        default_name = settings.get('default_provider')
        return [default_name] if default_name else []

    def get(self, market_key: str, provider_name: str):
        market_providers = self._providers.get(market_key, {})
        provider = market_providers.get(provider_name)
        if provider is None:
            raise RuntimeError(f'provider 不存在: market={market_key}, name={provider_name}')
        return provider
