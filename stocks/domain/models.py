from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Instrument:
    code: str
    name: str
    market: str
    exchange: Optional[str] = None


@dataclass(frozen=True)
class Quote:
    instrument: Instrument
    price: Optional[float]
    change: Optional[float]
    pct_change: Optional[float]
    volume_lot: Optional[float]
    amount_10k: Optional[float]
    open_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    prev_close: Optional[float] = None
