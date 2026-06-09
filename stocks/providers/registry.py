from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from stocks.providers.base import QuoteProvider


# 默认 markets.json 路径
DEFAULT_MARKETS_CONFIG = Path(__file__).resolve().parents[1] / "config" / "markets.json"


class ProviderRegistry:
    """Provider 注册表 — 运行时动态注册/发现

    支持从 markets.json 读取市场配置，包括默认 Provider 设置。
    """

    def __init__(self, markets_config_path: Optional[Path] = None):
        self._providers: dict[str, QuoteProvider] = {}
        self._markets_config: dict[str, dict] = {}
        self._load_markets_config(markets_config_path or DEFAULT_MARKETS_CONFIG)

    def _load_markets_config(self, path: Path) -> None:
        """加载 markets.json 配置。"""
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._markets_config = data
        except (json.JSONDecodeError, OSError):
            pass

    def register(self, provider: QuoteProvider) -> None:
        """注册 Provider"""
        self._providers[provider.name] = provider

    def get(self, name: str) -> Optional[QuoteProvider]:
        """按名称获取 Provider"""
        return self._providers.get(name)

    def list_for_market(self, market: str) -> list[QuoteProvider]:
        """获取支持指定市场的所有 Provider"""
        return [p for p in self._providers.values() if market in p.supported_markets]

    def get_default_for_market(self, market: str) -> Optional[QuoteProvider]:
        """获取指定市场的默认 Provider（从 markets.json 配置读取）。"""
        market_config = self._markets_config.get(market, {})
        default_name = market_config.get("default_provider")
        if default_name:
            provider = self._providers.get(default_name)
            if provider and market in provider.supported_markets:
                return provider
        # 回退到第一个可用 Provider
        candidates = self.list_for_market(market)
        return candidates[0] if candidates else None

    def all(self) -> list[QuoteProvider]:
        """获取所有 Provider"""
        return list(self._providers.values())
