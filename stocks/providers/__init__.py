"""Providers 包 — 行情数据 Provider"""

from stocks.providers.base import QuoteProvider
from stocks.providers.binance_quote import BinanceQuoteProvider
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider
from stocks.providers.finnhub_quote import FinnhubQuoteProvider
from stocks.providers.registry import ProviderRegistry
from stocks.providers.tencent_a import TencentAQuoteProvider

__all__ = [
    "QuoteProvider",
    "BinanceQuoteProvider",
    "ProviderRegistry",
    "TencentAQuoteProvider",
    "EastmoneyAQuoteProvider",
    "FinnhubQuoteProvider",
]
