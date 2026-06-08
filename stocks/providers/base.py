from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from stocks.domain.models import Instrument, Quote


class QuoteProvider(ABC):
    """行情数据 Provider 抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称"""
        pass

    @property
    @abstractmethod
    def supported_markets(self) -> list[str]:
        """支持的市场列表，如 ["a", "us"]"""
        pass

    @abstractmethod
    async def fetch(self, instrument: Instrument) -> Optional[Quote]:
        """获取单只标的行情"""
        pass

    @abstractmethod
    async def fetch_batch(self, instruments: list[Instrument]) -> list[Quote]:
        """批量获取行情（默认实现可逐个 fetch）"""
        pass
