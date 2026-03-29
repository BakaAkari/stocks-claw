from __future__ import annotations

from abc import ABC, abstractmethod

from stocks.domain.models import Instrument, Quote


class QuoteProvider(ABC):
    @abstractmethod
    def get_quote(self, instrument: Instrument) -> Quote:
        raise NotImplementedError

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        return [self.get_quote(item) for item in instruments]
