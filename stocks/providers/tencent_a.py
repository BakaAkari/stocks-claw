from __future__ import annotations

import asyncio
import urllib.request
from typing import Optional

from stocks.domain.models import Instrument, Quote
from stocks.providers.base import QuoteProvider


class TencentAQuoteProvider(QuoteProvider):
    """腾讯股票接口 A 股行情 Provider

    使用腾讯股票接口 https://qt.gtimg.cn/q={codes}
    支持 A 股（sh/sz），返回 Quote 对象。
    网络异常或解析失败时返回 None / 空列表。
    """

    @property
    def name(self) -> str:
        return "tencent_a"

    @property
    def supported_markets(self) -> list[str]:
        return ["a"]

    def _prefix(self, instrument: Instrument) -> str:
        """根据交易所或代码前缀判断市场前缀。"""
        exchange = (instrument.exchange or "").lower()
        code = instrument.code
        if exchange in ("sh", "sh_stock", "sh_a", "sh_index"):
            return "sh"
        if exchange in ("sz", "sz_stock", "sz_a", "sz_index"):
            return "sz"
        if code.startswith(("5", "6", "9")):
            return "sh"
        return "sz"

    def _build_symbol(self, instrument: Instrument) -> str:
        return f"s_{self._prefix(instrument)}{instrument.code}"

    def _fetch_raw_sync(self, symbols: list[str]) -> Optional[str]:
        """同步请求腾讯接口，返回原始文本。"""
        url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            text = data.decode("gbk", errors="replace")
            return text.strip() or None
        except Exception:
            return None

    def _parse_line(
        self,
        line: str,
        instrument: Optional[Instrument] = None,
        instrument_map: Optional[dict[str, Instrument]] = None,
    ) -> Optional[Quote]:
        """解析单行行情数据。"""
        if '="' not in line:
            return None
        _, raw = line.split('="', 1)
        raw = raw.rstrip('";')
        parts = raw.split("~")
        if len(parts) < 10:
            return None

        code = parts[2] or (instrument.code if instrument else "-")
        resolved = instrument or (instrument_map or {}).get(code)
        if resolved is None:
            resolved = Instrument(code=code, name=parts[1] or "-", market="a")

        return Quote(
            instrument=resolved,
            price=float(parts[3]) if parts[3] else None,
            change=float(parts[4]) if parts[4] else None,
            pct_change=float(parts[5]) if parts[5] else None,
            volume_lot=float(parts[6]) if parts[6] else None,
            amount_10k=float(parts[9]) if len(parts) > 9 and parts[9] else None,
        )

    async def fetch(self, instrument: Instrument) -> Optional[Quote]:
        """获取单只标的行情。"""
        symbol = self._build_symbol(instrument)
        raw = await asyncio.to_thread(self._fetch_raw_sync, [symbol])
        if raw is None:
            return None
        return self._parse_line(raw, instrument=instrument)

    async def fetch_batch(self, instruments: list[Instrument]) -> list[Quote]:
        """批量获取行情。"""
        if not instruments:
            return []
        symbols = [self._build_symbol(item) for item in instruments]
        raw = await asyncio.to_thread(self._fetch_raw_sync, symbols)
        if raw is None:
            return []

        instrument_map = {item.code: item for item in instruments}
        quotes: list[Quote] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            quote = self._parse_line(line, instrument_map=instrument_map)
            if quote:
                quotes.append(quote)
        return quotes
