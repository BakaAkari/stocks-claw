from __future__ import annotations

from typing import Optional

from stocks.providers.base import QuoteProvider


class ProviderRegistry:
    """Provider 注册表 — 运行时动态注册/发现"""

    def __init__(self):
        self._providers: dict[str, QuoteProvider] = {}

    def register(self, provider: QuoteProvider) -> None:
        """注册 Provider"""
        self._providers[provider.name] = provider

    def get(self, name: str) -> Optional[QuoteProvider]:
        """按名称获取 Provider"""
        return self._providers.get(name)

    def list_for_market(self, market: str) -> list[QuoteProvider]:
        """获取支持指定市场的所有 Provider"""
        return [p for p in self._providers.values() if market in p.supported_markets]

    def all(self) -> list[QuoteProvider]:
        """获取所有 Provider"""
        return list(self._providers.values())
