from __future__ import annotations

from stocks.config_loader import load_market_settings, load_market_watchlist
from stocks.errors import ConfigError


REQUIRED_WATCHLIST_KEYS = ('code', 'name', 'market')


def validate_market_settings(market_key: str) -> None:
    settings = load_market_settings(market_key)
    if not settings:
        raise ConfigError(f'市场配置不存在: {market_key}')

    providers = settings.get('providers', [])
    if not isinstance(providers, list):
        raise ConfigError(f'market.providers 必须是 list: {market_key}')

    default_provider = settings.get('default_provider')
    if default_provider and default_provider not in providers:
        raise ConfigError(f'default_provider 不在 providers 中: market={market_key}, provider={default_provider}')


def validate_watchlist(market_key: str) -> None:
    items = load_market_watchlist(market_key)
    if not isinstance(items, list):
        raise ConfigError(f'watchlist 必须是 list: {market_key}')

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f'watchlist item 必须是 object: market={market_key}, index={idx}')
        for key in REQUIRED_WATCHLIST_KEYS:
            if key not in item or item.get(key) in (None, ''):
                raise ConfigError(f'watchlist item 缺字段 {key}: market={market_key}, index={idx}')


def validate_all(market_keys: list[str] | None = None) -> None:
    keys = market_keys or ['a', 'us']
    for market_key in keys:
        validate_market_settings(market_key)
        validate_watchlist(market_key)
