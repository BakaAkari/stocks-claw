from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WATCHLIST_CONFIG_PATH = ROOT / 'config' / 'watchlist.json'
MARKETS_CONFIG_PATH = ROOT / 'config' / 'markets.json'

MARKET_ALIASES = {
    'a股': 'a',
    'a': 'a',
    'ashare': 'a',
    '沪深': 'a',
    '美股': 'us',
    'us': 'us',
}

MARKET_LABELS = {
    'a': 'A股',
    'us': '美股',
}


def normalize_market(value: str) -> str:
    key = (value or '').strip().lower()
    return MARKET_ALIASES.get(key, key)


def load_watchlist_config() -> dict:
    with open(WATCHLIST_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_markets_config() -> dict:
    with open(MARKETS_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_market_watchlist(market_key: str) -> list[dict]:
    data = load_watchlist_config()
    markets = data.get('markets', {})
    market = markets.get(market_key, {})
    return market.get('watchlist', [])


def load_market_settings(market_key: str) -> dict:
    data = load_markets_config()
    return data.get(market_key, {})


def market_label(market_key: str) -> str:
    settings = load_market_settings(market_key)
    return settings.get('label') or MARKET_LABELS.get(market_key, market_key)
